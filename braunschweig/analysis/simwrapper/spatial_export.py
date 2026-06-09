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
# Pure helper functions (testable without disk I/O)
# ---------------------------------------------------------------------------

# Ordered mapping of economic_status category labels to ordinal codes 1..5.
# These are the exact values written to the population CSVs by the synthesis
# pipeline (braunschweig.synthesis.population.enriched).
ECONOMIC_STATUS_ORDER = ("very_low", "low", "medium", "high", "very_high")
_ECONOMIC_STATUS_CODE: dict[str, int] = {
    cat: i + 1 for i, cat in enumerate(ECONOMIC_STATUS_ORDER)
}


def _socio_by_kreis(homes_df: "pd.DataFrame") -> pd.DataFrame:
    """Aggregate socio-economic metrics per Kreis (5-digit ars5 code).

    Computes per-Kreis summary statistics from a flat DataFrame of homes
    that already has ``ars5`` attached (from
    :func:`braunschweig.analysis.spatial.assign_geographies`).

    Columns used (all optional -- missing ones are silently skipped):

    - ``household_income_eur``: float, averaged as ``mean_income_eur``.
    - ``high_income``: bool/int (1=True), averaged as ``high_income_share_pct``
      (0-100).
    - ``number_of_cars``: int, averaged as ``mean_cars``.
    - ``economic_status_ord``: ordinal 1..5, averaged as
      ``mean_economic_status`` (only over rows where the value is non-null;
      carless households have no status and are excluded -- this is logged
      in the calling code, not here).

    Args:
        homes_df: Flat DataFrame with ``ars5`` plus the optional attribute
            columns listed above.  One row per household.

    Returns:
        DataFrame with ``ars5`` and whatever metric columns could be computed
        (those whose source column was present and had at least one non-null
        value per Kreis).  Empty DataFrame when ``ars5`` is absent.
    """
    if "ars5" not in homes_df.columns:
        return pd.DataFrame()

    agg_spec: dict[str, tuple[str, Any]] = {}
    if "household_income_eur" in homes_df.columns:
        agg_spec["mean_income_eur"] = ("household_income_eur", "mean")
    if "high_income" in homes_df.columns:
        agg_spec["high_income_share_pct"] = (
            "high_income",
            lambda s: round(100.0 * pd.to_numeric(s, errors="coerce").mean(), 2),
        )
    if "number_of_cars" in homes_df.columns:
        agg_spec["mean_cars"] = ("number_of_cars", "mean")
    if "economic_status_ord" in homes_df.columns:
        agg_spec["mean_economic_status"] = ("economic_status_ord", "mean")

    if not agg_spec:
        return pd.DataFrame()

    result = (
        homes_df.groupby("ars5", sort=True)
        .agg(**agg_spec)
        .reset_index()
    )
    return result


def _trips_xy(df: pd.DataFrame) -> pd.DataFrame:
    """Return a slim DataFrame of origin/destination x/y coordinates.

    Filters out:
    - Rows where ``mode == "outside"`` (cordon marker trips with no internal geometry).
    - Rows where ``origin_x`` is null (no valid origin coordinate).

    Retains rows where ``destination_x`` is null (origin still valid; null
    destination is written as NaN in the output CSV).

    Args:
        df: Full eqasim_trips DataFrame (sep=";").  Must contain columns
            ``origin_x``, ``origin_y``, ``destination_x``, ``destination_y``,
            ``mode``.

    Returns:
        DataFrame with exactly four columns:
        ``origin_x``, ``origin_y``, ``destination_x``, ``destination_y``.
    """
    mask = df["mode"].ne("outside") & df["origin_x"].notna()
    subset = df.loc[mask, ["origin_x", "origin_y", "destination_x", "destination_y"]]
    return subset.reset_index(drop=True)


def _purpose_to_mode(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate trip counts by (following_purpose, mode) for the sankey.

    Drops rows where ``mode == "outside"`` so external cordon trips do not
    appear in the flow diagram.

    Args:
        df: Full eqasim_trips DataFrame (sep=";").  Must contain columns
            ``following_purpose`` and ``mode``.

    Returns:
        DataFrame with columns ``from``, ``to``, ``value`` (trip count),
        one row per (following_purpose, mode) pair present in the data.
        Sorted descending by ``value`` for stable output.
    """
    filtered = df[df["mode"].ne("outside")].copy()
    counts = (
        filtered.groupby(["following_purpose", "mode"], sort=True)
        .size()
        .reset_index(name="value")
        .rename(columns={"following_purpose": "from", "mode": "to"})
        .sort_values("value", ascending=False)
        .reset_index(drop=True)
    )
    return counts


def _economic_status_ordinal(series: pd.Series) -> pd.Series:
    """Map economic_status category strings to ordinal codes 1..5.

    Mapping (per CLAUDE.md BMDV classes):
        very_low -> 1, low -> 2, medium -> 3, high -> 4, very_high -> 5

    Unknown or null values map to NaN.

    Args:
        series: String series of economic_status values.

    Returns:
        Float series of ordinal codes (NaN for unknowns).
    """
    return series.map(_ECONOMIC_STATUS_CODE).astype(float)


# ---------------------------------------------------------------------------
# Tab 1: Spatial demand (hexagons)
# ---------------------------------------------------------------------------

def emit_spatial_demand(
    sim_output_dir: Path | None,
    folder: Path,
) -> "dict[str, Any] | None":
    """Write ``trips_xy.csv`` and return the Spatial demand dashboard tab.

    Reads ``eqasim_trips.csv`` from ``sim_output_dir`` (the resolved
    ``simulation_output/`` directory, e.g. as returned by
    ``build_dashboard._find_sim_output``).  Keeps origin/destination x/y for
    all trips except cordon ``outside`` trips.  Returns ``None`` with a
    WARNING when the file is absent.

    File written: ``<folder>/trips_xy.csv``
    (columns: origin_x, origin_y, destination_x, destination_y).

    Args:
        sim_output_dir: Resolved path to the MATSim ``simulation_output/``
            directory, or ``None`` when the sim output could not be located.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict or ``None``.
    """
    trips_path: Path | None = None
    if sim_output_dir is not None:
        trips_path = Path(sim_output_dir) / "eqasim_trips.csv"
        if not trips_path.exists():
            trips_path = None

    if trips_path is None:
        LOGGER.warning(
            "[spatial_demand] eqasim_trips.csv not found in %s -- "
            "spatial demand tab skipped",
            sim_output_dir,
        )
        return None

    df = pd.read_csv(trips_path, sep=";")
    xy = _trips_xy(df)
    LOGGER.info("[spatial_demand] %d trips after filtering (mode!=outside, origin_x notna)", len(xy))

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    xy.to_csv(folder / "trips_xy.csv", index=False, encoding="utf-8")
    LOGGER.info("[spatial_demand] wrote trips_xy.csv (%d rows)", len(xy))

    aggregations = {
        "Trips": [
            {"title": "Origins", "x": "origin_x", "y": "origin_y"},
            {"title": "Destinations", "x": "destination_x", "y": "destination_y"},
        ]
    }
    return w.dashboard(
        "Spatial demand",
        "Trip origins & destinations (hexagon density)",
        {
            "hex": [
                w.card_hexagons(
                    "Trip origins and destinations",
                    "trips_xy.csv",
                    aggregations=aggregations,
                    radius=300,
                    description=(
                        "Each hexagon colour encodes trip count. "
                        "Select 'Origins' or 'Destinations' in the panel."
                    ),
                )
            ]
        },
    )


# ---------------------------------------------------------------------------
# Tab 2: Socio home points (xytime)
# ---------------------------------------------------------------------------

def emit_socio(run_output_dir: str, folder: Path) -> "dict[str, Any] | None":
    """Write xytime CSVs and per-Kreis choropleth for household socio-economic attributes.

    Loads the synthetic population from ``run_output_dir`` via
    :func:`braunschweig.analysis.population_validation.population_source.load_population`.

    For each available numeric attribute one xytime CSV is written:

    - ``homes_income.xyt.csv`` (value = ``household_income_eur``).
    - ``homes_economic_status.xyt.csv`` (value = ordinal code 1..5 for
      ``economic_status``; sourced from vehicles.csv; only written for the
      ~84% of households that own a car -- carless households have no vehicle
      row and therefore no status).
    - ``homes_high_income.xyt.csv`` (value = ``high_income`` as 0/1).
    - ``homes_number_of_cars.xyt.csv`` (value = ``number_of_cars``).

    Also writes a per-Kreis choropleth when VG250 is available:

    - ``kreis_socio.geojson`` -- Kreis polygons (EPSG:4326) with aggregated
      metrics.
    - ``kreis_socio.csv`` -- per-Kreis metrics joined by ``ars5``.

    Coverage note: ``economic_status`` is sourced from ``vehicles.csv``
    (one row per vehicle, ``mode == car``; drop_duplicates on household_id).
    Carless households have no vehicle row and therefore no status -- they
    are excluded from the status point map and the ``mean_economic_status``
    Kreis aggregate.  The coverage is always logged explicitly.

    Returns ``None`` when no attribute can be produced (always logged).

    Primary/fallback rates are logged per CLAUDE.md requirement.

    Args:
        run_output_dir: eqasim run output directory.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict with xytime point maps + Kreis choropleth rows, or ``None``.
    """
    import geopandas as gpd
    from braunschweig.analysis.population_validation.population_source import (
        load_population,
    )
    from braunschweig.analysis import spatial

    try:
        pop = load_population(run_output_dir=run_output_dir)
    except Exception as exc:
        LOGGER.warning("[socio] could not load population from %s: %s -- tab skipped",
                       run_output_dir, exc)
        return None

    homes = pop.homes[["household_id", "geometry"]].copy()
    if homes.crs is None or homes.crs.to_epsg() != 25832:
        LOGGER.warning(
            "[socio] homes CRS is %s, expected EPSG:25832 -- tab skipped", homes.crs
        )
        return None

    households = pop.households.copy()
    households["household_id"] = households["household_id"].astype(str)
    homes["household_id"] = homes["household_id"].astype(str)

    # Build one wide household-attributes frame by joining all available
    # household-level attributes onto the homes geometry.
    hh_attrs = households[["household_id"]].copy()

    for col in ("household_income_eur", "high_income", "number_of_cars"):
        if col in households.columns:
            hh_attrs[col] = households[col].values
        else:
            LOGGER.info(
                "[socio] '%s' absent in households.csv -- attribute skipped", col
            )

    # --- Source economic_status from vehicles.csv (car-owning HHs only) ---
    # economic_status is written per vehicle row; carless HHs have no row.
    # We take the first car-mode row per household_id (all rows for the same
    # household share the same status value from the synthesis pipeline).
    vehicles = pop.vehicles
    status_col_available = False
    if vehicles is not None and "economic_status" in vehicles.columns and "household_id" in vehicles.columns:
        car_vehicles = vehicles[vehicles["mode"] == "car"].copy() if "mode" in vehicles.columns else vehicles.copy()
        car_vehicles["household_id"] = (
            pd.to_numeric(car_vehicles["household_id"], errors="coerce")
            .astype("Int64")
            .apply(lambda v: str(int(v)) if pd.notna(v) else None)
        )
        status_per_hh = (
            car_vehicles[["household_id", "economic_status"]]
            .dropna(subset=["household_id"])
            .drop_duplicates("household_id", keep="first")
            .copy()
        )
        status_per_hh["economic_status_ord"] = _economic_status_ordinal(
            status_per_hh["economic_status"]
        )
        n_with_status = int(status_per_hh["economic_status_ord"].notna().sum())
        n_total_hh = len(homes)
        LOGGER.info(
            "[socio] economic_status: sourced from vehicles.csv (mode==car), "
            "%d / %d households (%.1f%%); carless HHs have no vehicle row "
            "and are excluded from the status map.",
            n_with_status, n_total_hh,
            100.0 * n_with_status / max(n_total_hh, 1),
        )
        LOGGER.info(
            "[socio] economic_status ordinal mapping: %s",
            ", ".join(f"{k}={v}" for k, v in _ECONOMIC_STATUS_CODE.items()),
        )
        hh_attrs = hh_attrs.merge(
            status_per_hh[["household_id", "economic_status_ord"]],
            on="household_id", how="left",
        )
        status_col_available = True
    elif vehicles is not None and "economic_status" not in vehicles.columns:
        LOGGER.warning(
            "[socio] vehicles.csv present but lacks 'economic_status' column "
            "(lean-run schema) -- economic status xyt skipped"
        )
    elif vehicles is None:
        LOGGER.warning(
            "[socio] vehicles.csv absent in %s -- economic status xyt skipped",
            run_output_dir,
        )
    else:
        LOGGER.warning(
            "[socio] vehicles.csv present but lacks 'household_id' column "
            "-- economic status xyt skipped (lean-run schema)"
        )

    # Build GeoDataFrame: homes + all attribute columns.
    gdf_full = gpd.GeoDataFrame(
        homes.merge(hh_attrs, on="household_id", how="left"),
        geometry="geometry", crs=pop.homes.crs,
    )

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written_files: list[str] = []

    # --- Write individual xytime point CSVs ---
    def _write_attr_xyt(col: str, filename: str, label: str) -> bool:
        """Write one xytime CSV for ``col``; return True if written."""
        if col not in gdf_full.columns:
            return False
        n_notna = int(gdf_full[col].notna().sum())
        n_total = len(gdf_full)
        LOGGER.info(
            "[socio] %s: %d / %d rows non-null (%.1f%%)",
            col, n_notna, n_total, 100.0 * n_notna / max(n_total, 1),
        )
        if n_notna == 0:
            LOGGER.warning("[socio] %s: all null -- xyt skipped", col)
            return False
        # Cast high_income bool -> int so the xytime CSV is numeric.
        if gdf_full[col].dtype == bool or str(gdf_full[col].dtype) == "object":
            gdf_full[col] = pd.to_numeric(gdf_full[col], errors="coerce")
        write_xyt_csv(gdf_full, folder, filename, col)
        return True

    if _write_attr_xyt("household_income_eur", "homes_income.xyt.csv", "EUR"):
        written_files.append("homes_income.xyt.csv")
    if status_col_available and _write_attr_xyt(
        "economic_status_ord", "homes_economic_status.xyt.csv", "status (1-5)"
    ):
        written_files.append("homes_economic_status.xyt.csv")
    if _write_attr_xyt("high_income", "homes_high_income.xyt.csv", "high income (0/1)"):
        written_files.append("homes_high_income.xyt.csv")
    if _write_attr_xyt("number_of_cars", "homes_number_of_cars.xyt.csv", "cars"):
        written_files.append("homes_number_of_cars.xyt.csv")

    if not written_files:
        LOGGER.warning(
            "[socio] no attribute produced any output -- socio tab skipped"
        )
        return None

    # --- Per-Kreis choropleth (requires VG250; skipped gracefully if absent) ---
    choropleth_available = False
    try:
        kreise = spatial.load_kreise(pop.homes.crs)
        homes_geo = spatial.assign_geographies(
            pop.homes[["household_id", "geometry"]].copy().assign(
                household_id=pop.homes["household_id"].astype(str)
            ),
            kreise=kreise,
        )
        # Attach ars5 to the wide attribute frame.
        ars5_map = homes_geo[["household_id", "ars5"]].copy()
        ars5_map["household_id"] = ars5_map["household_id"].astype(str)
        homes_with_ars5 = gdf_full.drop(columns=["geometry"], errors="ignore").merge(
            ars5_map, on="household_id", how="left"
        )
        # Include economic_status_ord only if it was produced (for the Kreis mean).
        agg_df = _socio_by_kreis(homes_with_ars5)
        if not agg_df.empty:
            n_kreise_with_status = int(agg_df["mean_economic_status"].notna().sum()) if "mean_economic_status" in agg_df.columns else 0
            LOGGER.info(
                "[socio] per-Kreis aggregation: %d Kreise; mean_economic_status "
                "available for %d Kreise (excludes carless HHs per column).",
                len(agg_df), n_kreise_with_status,
            )
            # Write CSV with ars5 key for SimWrapper.
            w.write_csv(folder, "kreis_socio.csv", agg_df)
            # Write GeoJSON (EPSG:4326).
            kreise_4326 = kreise[["ars5", "geometry"]].to_crs(epsg=4326).copy()
            merged_geo = kreise_4326.merge(agg_df, on="ars5", how="left")
            merged_geo.to_file(folder / "kreis_socio.geojson", driver="GeoJSON")
            LOGGER.info(
                "[socio] wrote kreis_socio.geojson and kreis_socio.csv (%d Kreise)",
                len(merged_geo),
            )
            choropleth_available = True
        else:
            LOGGER.warning("[socio] per-Kreis aggregation produced no rows -- choropleth skipped")
    except Exception as exc:
        LOGGER.warning(
            "[socio] per-Kreis choropleth skipped: %s (VG250 may be absent)", exc
        )

    # --- Assemble dashboard rows ---
    rows: dict[str, list[dict[str, Any]]] = {}

    # Row 1: xytime point maps (only include cards for written files).
    point_cards: list[dict[str, Any]] = []
    if "homes_income.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "Household income by home location (EUR)",
            "homes_income.xyt.csv",
            value_label="EUR",
            radius=6,
            description=(
                "Each point = one household at its home location. "
                "Value = household_income_eur (synthesised, descriptive)."
            ),
        ))
    if "homes_economic_status.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "Economic status by home location (1=very low .. 5=very high)",
            "homes_economic_status.xyt.csv",
            value_label="economic status (1=very low .. 5=very high)",
            radius=6,
            description=(
                "Each point = one car-owning household (~84% of HHs). "
                "Ordinal: 1=very_low, 2=low, 3=medium, 4=high, 5=very_high. "
                "Carless households have no status and are excluded."
            ),
        ))
    if "homes_high_income.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "High-income households by home location",
            "homes_high_income.xyt.csv",
            value_label="high income (1=yes, 0=no)",
            radius=6,
            description=(
                "Each point = one household. Value = 1 if high_income, 0 otherwise "
                "(synthesised, descriptive)."
            ),
        ))
    if "homes_number_of_cars.xyt.csv" in written_files:
        point_cards.append(w.card_xytime(
            "Number of cars by home location",
            "homes_number_of_cars.xyt.csv",
            value_label="number of cars",
            radius=6,
            description=(
                "Each point = one household. Value = number_of_cars (0, 1, 2, ...) "
                "(synthesised, descriptive)."
            ),
        ))
    if point_cards:
        rows["point_maps"] = point_cards

    # Row 2: Kreis choropleths.
    if choropleth_available:
        choropleth_cards: list[dict[str, Any]] = []
        if "mean_income_eur" in agg_df.columns:
            choropleth_cards.append(w.card_choropleth(
                "Mean household income by Kreis (EUR)",
                "kreis_socio.geojson",
                "kreis_socio.csv",
                value_col="mean_income_eur",
                join="ars5",
                color_ramp="Viridis",
                description="Mean synthesised household_income_eur per Kreis (descriptive).",
            ))
        if "high_income_share_pct" in agg_df.columns:
            choropleth_cards.append(w.card_choropleth(
                "High-income share by Kreis (%)",
                "kreis_socio.geojson",
                "kreis_socio.csv",
                value_col="high_income_share_pct",
                join="ars5",
                color_ramp="Plasma",
                description="Share of high-income households per Kreis (descriptive).",
            ))
        if "mean_economic_status" in agg_df.columns:
            choropleth_cards.append(w.card_choropleth(
                "Mean economic status by Kreis (1=very low .. 5=very high)",
                "kreis_socio.geojson",
                "kreis_socio.csv",
                value_col="mean_economic_status",
                join="ars5",
                color_ramp="RdYlGn",
                description=(
                    "Mean ordinal economic status per Kreis (1=very_low .. 5=very_high). "
                    "Excludes carless households (no status assigned)."
                ),
            ))
        if choropleth_cards:
            rows["choropleths"] = choropleth_cards
        rows["kreis_table"] = [
            w.card_table(
                "Per-Kreis socio-economic summary",
                "kreis_socio.csv",
                width=2,
                description=(
                    "mean_income_eur, high_income_share_pct, mean_cars, "
                    "mean_economic_status per Kreis (descriptive). "
                    "mean_economic_status excludes carless HHs."
                ),
            )
        ]

    if not rows:
        LOGGER.warning("[socio] no rows assembled -- socio tab skipped")
        return None

    return w.dashboard(
        "Socio",
        "Household socio-economic structure (descriptive)",
        rows,
        description=(
            "Spatial distribution of synthesised socio-economic attributes at home locations "
            "(EPSG:25832). Descriptive output only -- no committed reference target."
        ),
    )


# ---------------------------------------------------------------------------
# Tab 3: Behaviour (sankey + scatter)
# ---------------------------------------------------------------------------

def emit_behaviour(
    sim_output_dir: Path | None,
    record: "dict[str, Any] | None",
    folder: Path,
) -> "dict[str, Any] | None":
    """Write purpose-to-mode sankey and per-Kreis car-share scatter.

    Sankey:
        Reads ``eqasim_trips.csv`` from ``sim_output_dir``; aggregates
        trip counts by (following_purpose, mode); writes
        ``purpose_to_mode.csv`` (SEMICOLON-delimited).  Skipped when
        trips file is absent.

    Scatter:
        Reads ``record["matsim"]["per_kreis_sim"]`` (sim car-share %) and
        ``record["mid_reference"]["p12_per_kreis"]`` (MiD P12 car-share %
        as ``auto`` column); joins on ``ars5``; writes
        ``kreis_car_sim_vs_mid.csv``.  Skipped when either side absent.

    Returns ``None`` if neither part produced output.

    Args:
        sim_output_dir: Resolved path to the MATSim ``simulation_output/``
            directory, or ``None``.
        record: Run record dict from ``assemble_run_record``.
        folder: SimWrapper dashboard output folder.

    Returns:
        Dashboard dict or ``None``.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict[str, Any]]] = {}

    # --- Sankey: purpose -> mode ---
    trips_path: Path | None = None
    if sim_output_dir is not None:
        trips_path = Path(sim_output_dir) / "eqasim_trips.csv"
        if not trips_path.exists():
            trips_path = None

    if trips_path is not None:
        df = pd.read_csv(trips_path, sep=";")
        ptm = _purpose_to_mode(df)
        LOGGER.info(
            "[behaviour] sankey: %d (purpose, mode) pairs from %d trips",
            len(ptm), len(df),
        )
        # SimWrapper sankey expects SEMICOLON-delimited CSV.
        ptm.to_csv(folder / "purpose_to_mode.csv", sep=";", index=False, encoding="utf-8")
        LOGGER.info("[behaviour] wrote purpose_to_mode.csv")
        rows["sankey"] = [
            w.card_sankey(
                "Trip purpose to mode transitions",
                "purpose_to_mode.csv",
                sort=True,
                width=2,
                description=(
                    "Flow: following_purpose (from) -> mode (to). "
                    "Width = trip count. Cordon 'outside' trips excluded."
                ),
            )
        ]
    else:
        LOGGER.info(
            "[behaviour] eqasim_trips.csv not found in %s -- sankey skipped",
            sim_output_dir,
        )

    # --- Scatter: sim car-share vs MiD P12 car-share per Kreis ---
    per_kreis_sim = (record or {}).get("matsim", {}).get("per_kreis_sim") or {}
    p12_per_kreis = (record or {}).get("mid_reference", {}).get("p12_per_kreis") or []

    if per_kreis_sim and p12_per_kreis:
        sim_rows = []
        for ars5, d in per_kreis_sim.items():
            ms = d.get("mode_share_pct", {})
            sim_rows.append({"ars5": ars5, "sim_car_pct": ms.get("car", 0.0)})
        sim_df = pd.DataFrame(sim_rows)

        mid_rows = [
            {"ars5": str(entry["ars5"]), "mid_car_pct": float(entry["auto"])}
            for entry in p12_per_kreis
            if "ars5" in entry and "auto" in entry
        ]
        mid_df = pd.DataFrame(mid_rows)

        scatter_df = sim_df.merge(mid_df, on="ars5", how="inner")
        LOGGER.info(
            "[behaviour] scatter: %d Kreise matched (sim per_kreis_sim=%d, MiD p12=%d)",
            len(scatter_df), len(sim_df), len(mid_df),
        )
        if not scatter_df.empty:
            w.write_csv(folder, "kreis_car_sim_vs_mid.csv", scatter_df)
            LOGGER.info("[behaviour] wrote kreis_car_sim_vs_mid.csv (%d rows)", len(scatter_df))
            rows["scatter"] = [
                w.card_scatter(
                    "Car share per Kreis: Sim vs MiD P12.1",
                    "kreis_car_sim_vs_mid.csv",
                    x="mid_car_pct",
                    y="sim_car_pct",
                    x_axis_name="MiD 2023 P12.1 car share (%)",
                    y_axis_name="Simulation car share (%)",
                    width=2,
                    description=(
                        "Scatter of commute car mode share per Kreis: "
                        "x = MiD 2023 reference (auto %), y = simulation. "
                        "Points on the diagonal indicate a good fit."
                    ),
                )
            ]
        else:
            LOGGER.warning("[behaviour] scatter: no Kreise matched between sim and MiD -- scatter skipped")
    else:
        missing = []
        if not per_kreis_sim:
            missing.append("matsim.per_kreis_sim")
        if not p12_per_kreis:
            missing.append("mid_reference.p12_per_kreis")
        LOGGER.info("[behaviour] scatter skipped: missing %s", ", ".join(missing))

    if not rows:
        LOGGER.warning("[behaviour] neither sankey nor scatter produced output -- behaviour tab skipped")
        return None

    return w.dashboard(
        "Behaviour",
        "Mode transitions & per-Kreis fit",
        rows,
        description=(
            "Left: sankey of trip purpose -> mode (all trips, excl. cordon). "
            "Right: per-Kreis car share scatter (sim vs MiD P12.1 reference)."
        ),
    )


# ---------------------------------------------------------------------------
# export_spatial -- registry-based driver, wired into export.main()
# ---------------------------------------------------------------------------

def export_spatial(
    target_dir: str | Path,
    run_output_dir: str | None = None,
    sim_cache: str | None = None,
    record: "dict[str, Any] | None" = None,
    start_index: int = 9,
) -> list[Path]:
    """Write spatial dashboard tabs (fleet, spatial-demand, socio, behaviour).

    Iterates over a registry of ``(name, emit_fn)`` pairs, writes each
    returned dashboard dict as ``dashboard-{idx}-{name}.yaml``, and skips
    boards that return ``None`` (always logging the skip so there are no
    silent fallbacks).

    Tab order (sequential indices starting at ``start_index``):
    1. fleet -- vehicle fleet map (all-features runs only).
    2. spatial-demand -- trip origin/destination hexagons.
    3. socio -- home points coloured by income / economic status.
    4. behaviour -- purpose->mode sankey + per-Kreis car-share scatter.

    Args:
        target_dir: SimWrapper dashboard output folder (created if absent).
        run_output_dir: eqasim run output directory.
        sim_cache: synpp cache directory.
        record: Run record dict from ``assemble_run_record`` (needed for the
            behaviour scatter; skipped when ``None``).
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

    # Resolve the fleet / socio source directory (run_output preferred).
    source_dir = run_output_dir if run_output_dir is not None else sim_cache

    # Resolve the MATSim simulation_output/ for trips-based tabs.
    sim_output_dir: Path | None = None
    if sim_cache is not None:
        from braunschweig.analysis.dashboard.build_dashboard import _find_sim_output
        sim_output_dir = _find_sim_output(Path(sim_cache))
        if sim_output_dir is None:
            LOGGER.info(
                "[spatial_export] no matsim.simulation.run__*.cache found in %s "
                "-- trips-based tabs (spatial-demand, behaviour-sankey) will be skipped",
                sim_cache,
            )

    _SPATIAL_REGISTRY: list[tuple[str, Any]] = [
        # Tab: fleet
        ("fleet", lambda f: emit_fleet(source_dir, f)),
        # Tab: spatial-demand (hexagons from eqasim_trips.csv)
        ("spatial-demand", lambda f: emit_spatial_demand(sim_output_dir, f)),
        # Tab: socio (home xytime from population)
        ("socio", lambda f: emit_socio(source_dir, f) if source_dir else None),
        # Tab: behaviour (sankey + scatter)
        ("behaviour", lambda f: emit_behaviour(sim_output_dir, record, f)),
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
