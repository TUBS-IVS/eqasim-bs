"""Spatial fleet map tabs for the SimWrapper dashboard export.

Produces three kinds of outputs when the run uses the all-features fleet
(``braunschweig_*_vehicles.csv`` contains ``powertrain`` and
``engine_power_kw`` columns):

* Point-cloud xytime CSVs (one per vehicle, coloured by power or BEV status)
  for the SimWrapper xytime viewer.
* Per-Kreis aggregated CSV + GeoJSON choropleth for the SimWrapper shapefiles
  plugin (BEV share and mean power by Kreis).
* A powertrain-mix or brand-mix bar chart depending on brand coverage.

When the run uses the lean vehicles schema (no ``powertrain`` column) all
fleet outputs are skipped and a WARNING is logged -- the caller must not
silently ignore this signal.

BEV identification: the real value in ``powertrain`` for battery-electric
vehicles is ``"bev"`` (Step-0 verified on
``eqasim-data/output_bs_25pct_allfeat/``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd

from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")

# Fleet columns that must be present for the rich fleet tab.
_REQUIRED_FLEET_COLS = {"powertrain", "engine_power_kw"}

# The exact ``powertrain`` value for battery-electric vehicles.
# Verified in Step-0 on output_bs_25pct_allfeat (2026-06-09).
BEV_POWERTRAIN_VALUE = "bev"

# Minimum brand coverage (notna/non-empty share) to use brand bars instead of
# powertrain bars.  Below this threshold brand data is too sparse.
_MIN_BRAND_COVERAGE = 0.30


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_fleet(run_output_dir: str) -> "gpd.GeoDataFrame | None":
    """Load fleet data merged with home geometry from a run output directory.

    Uses :func:`braunschweig.analysis.population_validation.population_source.load_population`
    to read vehicles and homes, then merges on ``household_id`` to geolocate
    each vehicle.

    The BEV flag ``is_bev`` is derived from the real ``powertrain`` value
    ``"bev"`` (Step-0 verified; do not guess other labels).

    Logs:
    - A WARNING and returns ``None`` when ``vehicles`` is absent or lacks the
      rich fleet columns (lean-run schema); the caller must not silently ignore
      this.
    - The BEV count and share (primary-method coverage).
    - Geolocation coverage: how many vehicles received a home geometry
      (primary) vs how many did not match (fallback signal per CLAUDE.md).

    Args:
        run_output_dir: Path to the eqasim run output directory.

    Returns:
        A GeoDataFrame (CRS = homes.crs, nominally EPSG:25832) with one row
        per vehicle, or ``None`` when the fleet data is unavailable/lean.
    """
    import geopandas as gpd
    from braunschweig.analysis.population_validation.population_source import (
        load_population,
    )

    pop = load_population(run_output_dir=run_output_dir)
    vehicles = pop.vehicles

    if vehicles is None:
        LOGGER.warning(
            "[fleet] vehicles.csv absent in %s -- fleet tab skipped", run_output_dir
        )
        return None

    missing = _REQUIRED_FLEET_COLS - set(vehicles.columns)
    if missing:
        LOGGER.warning(
            "[fleet] vehicles.csv lacks required columns %s (lean-run schema) "
            "in %s -- fleet tab skipped",
            sorted(missing),
            run_output_dir,
        )
        return None

    homes = pop.homes[["household_id", "geometry"]].copy()
    # Assert CRS is EPSG:25832 (required by write_xyt_csv and downstream).
    assert homes.crs is not None and homes.crs.to_epsg() == 25832, (
        f"Expected homes CRS EPSG:25832, got {homes.crs}"
    )

    vehicles = vehicles.copy()

    # The vehicles CSV contains one row per (household, mode) assignment.
    # Rows where mode == "car_passenger" are ride-sharing passengers -- they
    # do not own a distinct vehicle asset and their powertrain is NaN.
    # The fleet map must show OWNED vehicles only (mode == "car").
    n_raw = len(vehicles)
    if "mode" in vehicles.columns:
        vehicles = vehicles[vehicles["mode"] == "car"].copy()
        n_owned = len(vehicles)
        LOGGER.info(
            "[fleet] filtered to mode=='car': %d owned vehicles / %d total rows "
            "(dropped %d car_passenger rows with no powertrain)",
            n_owned, n_raw, n_raw - n_owned,
        )
    else:
        LOGGER.warning(
            "[fleet] 'mode' column absent -- using all %d rows (may include "
            "car_passenger rows without powertrain/geometry)", n_raw,
        )

    # Cast engine_power_kw to float so describe() and mean() work correctly.

    # Normalise household_id to integer string on both sides before joining.
    # The vehicles CSV may read household_id as float (e.g. "6.0") due to
    # mixed-type inference, while homes.gpkg stores it as int32 -- the join
    # fails silently without this cast.  Using Int64 (nullable) so that any
    # genuinely null household_id values stay null rather than being cast to
    # the string "NaN".
    vehicles["household_id"] = (
        pd.to_numeric(vehicles["household_id"], errors="coerce")
        .astype("Int64")
        .astype("object")
        .where(vehicles["household_id"].notna())
        .apply(lambda v: str(int(v)) if pd.notna(v) else None)
    )
    homes["household_id"] = homes["household_id"].astype(str)
    vehicles["engine_power_kw"] = pd.to_numeric(
        vehicles["engine_power_kw"], errors="coerce"
    )
    vehicles["engine_power_ps"] = pd.to_numeric(
        vehicles.get("engine_power_ps", pd.Series(dtype=float)), errors="coerce"
    )

    # Derive BEV flag from the verified real value.
    vehicles["is_bev"] = (vehicles["powertrain"] == BEV_POWERTRAIN_VALUE).astype(int)
    n_total = len(vehicles)
    n_bev = int(vehicles["is_bev"].sum())
    bev_share = 100.0 * n_bev / max(n_total, 1)
    LOGGER.info(
        "[fleet] BEV: %d / %d vehicles (%.1f%%) using powertrain=='%s'",
        n_bev, n_total, bev_share, BEV_POWERTRAIN_VALUE,
    )

    # Normalise the Kreis key to a zero-padded 5-char string so it matches the
    # VG250 ``ars5`` (e.g. "03101"). The CSV reader infers ``kreis_ags5`` as a
    # float (3101.0), which would break the choropleth merge on the string ars5.
    if "kreis_ags5" in vehicles.columns:
        vehicles["kreis_ags5"] = vehicles["kreis_ags5"].map(
            lambda v: str(int(float(v))).zfill(5) if pd.notna(v) else v
        )

    # Merge with home geometry on household_id.
    gdf = vehicles.merge(homes, on="household_id", how="left")
    n_with_geom = int(gdf["geometry"].notna().sum())
    n_no_geom = n_total - n_with_geom
    LOGGER.info(
        "[fleet] geolocation: primary (geometry matched) %d / %d (%.1f%%), "
        "no-match (fallback signal) %d (%.1f%%)",
        n_with_geom, n_total, 100.0 * n_with_geom / max(n_total, 1),
        n_no_geom, 100.0 * n_no_geom / max(n_total, 1),
    )
    if n_total > 0 and n_no_geom / n_total > 0.05:
        LOGGER.warning(
            "[fleet] %.1f%% of vehicles have no home geometry -- "
            "check household_id join; high rate is a bug signal.",
            100.0 * n_no_geom / n_total,
        )

    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=homes.crs)
    return gdf


# ---------------------------------------------------------------------------
# xytime CSV writer
# ---------------------------------------------------------------------------

def write_xyt_csv(gdf: "gpd.GeoDataFrame", folder: Path,
                  name: str, value_col: str) -> str:
    """Write a SimWrapper xytime CSV for point-cloud visualisation.

    The file format is::

        # EPSG:25832
        time,x,y,value
        0,<x>,<y>,<value>
        ...

    Only rows with non-null geometry AND non-null ``value_col`` are written.
    Coordinates are taken directly from the GeoDataFrame geometry (must be
    EPSG:25832 -- asserted before writing).

    Args:
        gdf: GeoDataFrame with point geometry in EPSG:25832.
        folder: Output directory (created if absent).
        name: Output filename (e.g. ``fleet_power_kw.xyt.csv``).
        value_col: Column name to use as the ``value`` field.

    Returns:
        The ``name`` argument (for chaining / logging).
    """
    assert gdf.crs is not None and gdf.crs.to_epsg() == 25832, (
        f"write_xyt_csv requires EPSG:25832, got {gdf.crs}"
    )
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    mask = gdf["geometry"].notna() & gdf[value_col].notna()
    subset = gdf[mask].copy()

    rows = pd.DataFrame({
        "time": 0,
        "x": subset["geometry"].x,
        "y": subset["geometry"].y,
        "value": subset[value_col],
    })

    out_path = folder / name
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("# EPSG:25832\n")
        rows.to_csv(fh, index=False)

    LOGGER.info("[fleet] wrote xytime CSV %s (%d points)", name, len(rows))
    return name


# ---------------------------------------------------------------------------
# Per-Kreis aggregation
# ---------------------------------------------------------------------------

def fleet_by_kreis(gdf: "gpd.GeoDataFrame | pd.DataFrame") -> pd.DataFrame:
    """Aggregate fleet metrics by Kreis.

    Groups by ``kreis_ags5`` (5-digit Kreis code already present in the
    vehicles data; no spatial join needed) and computes:

    - ``n_vehicles``: count of vehicles in the Kreis.
    - ``bev_share_pct``: BEV share as a percentage (0-100).
    - ``mean_power_kw``: mean ``engine_power_kw`` (NaN where no values).
    - ``mean_power_ps``: mean ``engine_power_ps`` (NaN where no values).

    Args:
        gdf: GeoDataFrame or DataFrame with columns ``kreis_ags5``,
            ``is_bev``, ``engine_power_kw``, ``engine_power_ps``.

    Returns:
        DataFrame with columns ``kreis_ags5``, ``n_vehicles``,
        ``bev_share_pct``, ``mean_power_kw``, ``mean_power_ps``.
    """
    agg = (
        gdf.groupby("kreis_ags5", sort=True)
        .agg(
            n_vehicles=("is_bev", "count"),
            bev_share_pct=("is_bev", lambda s: round(100.0 * s.mean(), 2)),
            mean_power_kw=("engine_power_kw", "mean"),
            mean_power_ps=("engine_power_ps", "mean"),
        )
        .reset_index()
    )
    return agg


# ---------------------------------------------------------------------------
# Kreis choropleth GeoJSON + CSV
# ---------------------------------------------------------------------------

def write_kreis_choropleth_geojson(
    kreise_gdf: "gpd.GeoDataFrame",
    agg_df: pd.DataFrame,
    folder: Path,
    join_left: str = "ars5",
    join_right: str = "kreis_ags5",
) -> str:
    """Write a Kreis choropleth GeoJSON (EPSG:4326) for the SimWrapper shapefiles plugin.

    Reprojects ``kreise_gdf`` to EPSG:4326 (GeoJSON standard), merges
    ``agg_df`` onto it, and writes ``<folder>/kreis_fleet.geojson``.

    The SimWrapper shapefiles plugin joins the GeoJSON ``join_left`` property
    to the CSV ``join_left`` column (both renamed to ``ars5`` for consistency).

    Args:
        kreise_gdf: Kreis polygons as returned by
            :func:`braunschweig.analysis.spatial.load_kreise`.
            Must contain column ``ars5``.
        agg_df: Per-Kreis aggregated metrics from :func:`fleet_by_kreis`;
            must contain ``kreis_ags5`` column.
        folder: Output directory (created if absent).
        join_left: Column in ``kreise_gdf`` to join on (default ``ars5``).
        join_right: Column in ``agg_df`` to join on (default ``kreis_ags5``).

    Returns:
        Filename ``"kreis_fleet.geojson"``.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    kreise_4326 = kreise_gdf[[join_left, "geometry"]].to_crs(epsg=4326).copy()
    # Rename join_right -> join_left so the GeoJSON and CSV share the same key.
    agg_renamed = agg_df.rename(columns={join_right: join_left})

    merged = kreise_4326.merge(agg_renamed, on=join_left, how="left")
    out_path = folder / "kreis_fleet.geojson"
    merged.to_file(out_path, driver="GeoJSON")
    LOGGER.info("[fleet] wrote %s (%d Kreise)", out_path.name, len(merged))
    return "kreis_fleet.geojson"


# ---------------------------------------------------------------------------
# Brand / powertrain mix bar data
# ---------------------------------------------------------------------------

def _brand_mix_by_kreis(gdf: "gpd.GeoDataFrame | pd.DataFrame") -> pd.DataFrame:
    """Compute per-Kreis top-brand share (top 5 + Other).

    Returns a wide DataFrame: rows = Kreise, columns = ``ars5`` + brand names.
    Values are vehicle counts (not shares; SimWrapper bar with stacked=True
    will show proportions visually).
    """
    top_brands = (
        gdf["brand"].value_counts().dropna().head(5).index.tolist()
    )
    gdf = gdf.copy()
    gdf["_brand_grp"] = gdf["brand"].where(
        gdf["brand"].isin(top_brands), other="Other"
    )
    pivot = (
        gdf.groupby(["kreis_ags5", "_brand_grp"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={"kreis_ags5": "ars5"})
    )
    # Ensure consistent column order: top brands first, then Other.
    brand_cols = [b for b in top_brands if b in pivot.columns]
    if "Other" in pivot.columns:
        brand_cols.append("Other")
    return pivot[["ars5"] + brand_cols]


def _powertrain_mix_by_kreis(gdf: "gpd.GeoDataFrame | pd.DataFrame") -> pd.DataFrame:
    """Compute per-Kreis powertrain mix (vehicle counts per powertrain type).

    Returns a wide DataFrame: rows = Kreise, columns = ``ars5`` + powertrain
    values. NaN powertrain is grouped as ``"unknown"``.
    """
    gdf = gdf.copy()
    gdf["_pt"] = gdf["powertrain"].fillna("unknown")
    pivot = (
        gdf.groupby(["kreis_ags5", "_pt"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={"kreis_ags5": "ars5"})
    )
    value_cols = [c for c in pivot.columns if c != "ars5"]
    return pivot[["ars5"] + value_cols]


# ---------------------------------------------------------------------------
# emit_fleet -- main entry point for this tab
# ---------------------------------------------------------------------------

def emit_fleet(run_output_dir: str, folder: Path) -> "dict[str, Any] | None":
    """Build the Fleet dashboard tab: xytime point maps, Kreis choropleths,
    brand/powertrain bar.

    Calls :func:`load_fleet`; returns ``None`` when fleet data is unavailable
    (lean-run schema). The absence is always logged by :func:`load_fleet`.

    Files written to ``folder``:
    - ``fleet_power_kw.xyt.csv`` -- xytime point cloud coloured by kW.
    - ``fleet_bev.xyt.csv`` -- xytime point cloud coloured by BEV flag (0/1).
    - ``kreis_fleet.csv`` -- per-Kreis aggregated stats (joined by ``ars5``).
    - ``kreis_fleet.geojson`` -- Kreis polygons (EPSG:4326) merged with stats.
    - ``fleet_mix_by_kreis.csv`` -- brand or powertrain mix counts per Kreis.

    Args:
        run_output_dir: Path to the eqasim run output directory.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict (from :func:`braunschweig.analysis.simwrapper.writers.dashboard`)
        or ``None`` when fleet data is absent.
    """
    from braunschweig.analysis.spatial import load_kreise

    gdf = load_fleet(run_output_dir)
    if gdf is None:
        return None

    folder = Path(folder)

    # --- xytime point clouds ---
    write_xyt_csv(gdf, folder, "fleet_power_kw.xyt.csv", "engine_power_kw")
    write_xyt_csv(gdf, folder, "fleet_bev.xyt.csv", "is_bev")

    # --- per-Kreis aggregation + choropleth ---
    agg = fleet_by_kreis(gdf)
    # Rename kreis_ags5 -> ars5 for the shared join key used by SimWrapper.
    agg_csv = agg.rename(columns={"kreis_ags5": "ars5"})
    w.write_csv(folder, "kreis_fleet.csv", agg_csv)

    try:
        kreise = load_kreise(gdf.crs)
        write_kreis_choropleth_geojson(kreise, agg, folder)
        choropleth_available = True
    except Exception as exc:
        LOGGER.warning(
            "[fleet] choropleth skipped: could not load Kreis geometry: %s", exc
        )
        choropleth_available = False

    # --- brand vs powertrain mix decision ---
    brand_coverage = 0.0
    if "brand" in gdf.columns:
        nonempty = gdf["brand"].notna() & (gdf["brand"].str.strip() != "")
        brand_coverage = float(nonempty.mean())

    if brand_coverage > _MIN_BRAND_COVERAGE:
        LOGGER.info(
            "[fleet] brand coverage %.1f%% > threshold %.0f%% -- using brand bar",
            100.0 * brand_coverage, 100.0 * _MIN_BRAND_COVERAGE,
        )
        mix_df = _brand_mix_by_kreis(gdf)
        mix_name = w.write_csv(folder, "fleet_mix_by_kreis.csv", mix_df)
        mix_value_cols = [c for c in mix_df.columns if c != "ars5"]
        mix_bar = w.card_bar(
            "Vehicle brand mix by Kreis (top 5 + Other)",
            mix_name,
            x="ars5",
            columns=mix_value_cols,
            legend_titles=mix_value_cols,
            stacked=True,
            x_axis_name="Kreis (AGS5)",
            y_axis_name="Vehicles",
            width=2,
        )
    else:
        LOGGER.info(
            "[fleet] brand coverage %.1f%% <= threshold %.0f%% (too sparse) "
            "-- falling back to powertrain mix bar (logged, not silent)",
            100.0 * brand_coverage, 100.0 * _MIN_BRAND_COVERAGE,
        )
        mix_df = _powertrain_mix_by_kreis(gdf)
        mix_name = w.write_csv(folder, "fleet_mix_by_kreis.csv", mix_df)
        mix_value_cols = [c for c in mix_df.columns if c != "ars5"]
        mix_bar = w.card_bar(
            "Vehicle powertrain mix by Kreis",
            mix_name,
            x="ars5",
            columns=mix_value_cols,
            legend_titles=mix_value_cols,
            stacked=True,
            x_axis_name="Kreis (AGS5)",
            y_axis_name="Vehicles",
            width=2,
        )

    # --- assemble dashboard rows ---
    rows: dict[str, list[dict[str, Any]]] = {}

    rows["point_maps"] = [
        w.card_xytime(
            "Engine power by vehicle home location (kW)",
            "fleet_power_kw.xyt.csv",
            value_label="kW",
            radius=4,
            description=(
                "Each point = one vehicle at its household home. "
                "Colour encodes engine power in kW."
            ),
        ),
        w.card_xytime(
            "Battery-electric vehicles (BEV) by home location",
            "fleet_bev.xyt.csv",
            value_label="BEV (1=yes)",
            radius=4,
            description=(
                "Each point = one vehicle. Value 1 = battery-electric "
                f"(powertrain=='{BEV_POWERTRAIN_VALUE}')."
            ),
        ),
    ]

    if choropleth_available:
        rows["choropleths"] = [
            w.card_choropleth(
                "BEV share by Kreis (%)",
                "kreis_fleet.geojson",
                "kreis_fleet.csv",
                value_col="bev_share_pct",
                join="ars5",
                color_ramp="Viridis",
                description="Share of battery-electric vehicles per Kreis.",
            ),
            w.card_choropleth(
                "Mean engine power by Kreis (kW)",
                "kreis_fleet.geojson",
                "kreis_fleet.csv",
                value_col="mean_power_kw",
                join="ars5",
                color_ramp="Plasma",
                description="Mean engine power of vehicles per Kreis.",
            ),
        ]

    rows["mix_and_table"] = [
        mix_bar,
        w.card_table(
            "Per-Kreis fleet summary",
            "kreis_fleet.csv",
            width=2,
            description="n_vehicles, bev_share_pct, mean_power_kw per Kreis.",
        ),
    ]

    return w.dashboard(
        "Fleet",
        "Vehicle fleet (powertrain, power, brand)",
        rows,
        description=(
            "Spatial fleet composition from the all-features synthesis. "
            f"BEV flag = powertrain=='{BEV_POWERTRAIN_VALUE}' (MiD-derived)."
        ),
    )


# ---------------------------------------------------------------------------
# export_spatial -- registry-based driver, wired into export.main()
# ---------------------------------------------------------------------------

def export_spatial(
    target_dir: str | Path,
    run_output_dir: str | None = None,
    sim_cache: str | None = None,
    start_index: int = 9,
) -> list[Path]:
    """Write spatial dashboard tabs (fleet, ...) to ``target_dir``.

    Follows the same pattern as :func:`braunschweig.analysis.simwrapper.export.export_run`:
    iterates over a registry of ``(name, emit_fn)`` pairs, writes each
    returned dashboard dict as ``dashboard-{idx}-{name}.yaml``, and skips
    boards that return ``None`` (always logging the skip).

    Args:
        target_dir: SimWrapper dashboard output folder (same as used by
            ``export_run``; created if absent).
        run_output_dir: eqasim run output directory (passed to emit functions).
        sim_cache: synpp cache directory (alternative to ``run_output_dir``).
        start_index: Starting dashboard index (default 9 so it follows the 8
            core tabs produced by ``export_run``).

    Returns:
        List of written YAML :class:`pathlib.Path` objects.
    """
    if run_output_dir is None and sim_cache is None:
        LOGGER.warning(
            "[spatial_export] neither run_output_dir nor sim_cache provided -- "
            "all spatial tabs skipped"
        )
        return []

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the source directory for spatial loaders.
    source_dir = run_output_dir if run_output_dir is not None else sim_cache

    _SPATIAL_REGISTRY: list[tuple[str, Any]] = [
        ("fleet", lambda f: emit_fleet(source_dir, f)),
    ]

    written: list[Path] = []
    for idx, (name, fn) in enumerate(_SPATIAL_REGISTRY, start=start_index):
        try:
            board = fn(target_dir)
        except Exception as exc:
            LOGGER.warning(
                "[spatial_export] tab '%s' skipped due to error: %s", name, exc
            )
            board = None
        if board is None:
            LOGGER.info(
                "[spatial_export] tab '%s' has no data, skipped", name
            )
            continue
        path = w.write_yaml(target_dir, f"dashboard-{idx}-{name}.yaml", board)
        written.append(path)
        LOGGER.info("[spatial_export] wrote %s", path.name)

    LOGGER.info(
        "[spatial_export] wrote %d spatial dashboard tab(s) to %s",
        len(written), target_dir,
    )
    return written
