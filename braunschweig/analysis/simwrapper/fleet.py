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

Sibling module note (issue #267 split): this module previously also carried
the generic ``write_xyt_csv`` (xytime point-cloud CSV writer) and
``write_kreis_choropleth_geojson`` (Kreis choropleth GeoJSON writer) helpers,
placed here by Task 1 of the split because ``emit_fleet`` needs them and no
dedicated "generic layer writers" sibling existed yet (leaving them in the
``spatial_export`` facade would have forced a facade-import cycle). Task 2 of
the same split relocated both writers into the dedicated
:mod:`braunschweig.analysis.simwrapper.geo_layers` sibling; this module now
imports them from there. ``write_xyt_csv`` is also called by ``emit_socio``
(still defined in the facade) via the facade's re-export of ``geo_layers``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd

from braunschweig.analysis.simwrapper import writers as w
from braunschweig.analysis.simwrapper.geo_layers import (
    write_kreis_choropleth_geojson,
    write_xyt_csv,
)

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

    # vehicles.csv holds TWO car-mode sets: the household FLEET vehicles
    # (household_id-keyed, with brand/powertrain/engine_power) and eqasim
    # per-person ROUTING vehicles (owner_id-keyed, no fleet attributes = NaN).
    # Both are mode=='car', so a mode-only filter would mix the routing vehicles
    # in (NaN powertrain/power). The fleet map must show fleet vehicles only --
    # the shared fleet filter keeps mode=='car' AND non-null household_id and logs
    # the fleet-vs-routing split.
    from braunschweig.analysis import fleet_filter as _ff
    vehicles = _ff.fleet_vehicles(vehicles, context="simwrapper.fleet").copy()

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
        # Vectorised (no per-row apply): numeric -> Int64 -> zero-padded 5-char
        # string, NA-safe. Matches the VG250 string ``ars5`` (e.g. "03101").
        vehicles["kreis_ags5"] = (
            pd.to_numeric(vehicles["kreis_ags5"], errors="coerce")
            .astype("Int64").astype("string").str.zfill(5)
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
        # Each big map card gets its own full-width row so it renders large.
        rows["choropleth_bev"] = [
            w.card_choropleth(
                "BEV share by Kreis (%)",
                "kreis_fleet.geojson",
                value_col="bev_share_pct",
                join="ars5",
                color_ramp="Viridis",
                height=13,
                description="Share of battery-electric vehicles per Kreis.",
            ),
        ]
        rows["choropleth_power"] = [
            w.card_choropleth(
                "Mean engine power by Kreis (kW)",
                "kreis_fleet.geojson",
                value_col="mean_power_kw",
                join="ars5",
                color_ramp="Plasma",
                height=13,
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
