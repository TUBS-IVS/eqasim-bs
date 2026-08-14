"""Generic geo-referenced SimWrapper output writers (issue #267 split).

Unlike :mod:`braunschweig.analysis.simwrapper.writers` (pure card/dashboard-dict
builders plus thin generic CSV/YAML serialisation -- no geometry knowledge),
this module contains the two writers that actually consume geometry-bearing
GeoDataFrames and produce the geo-referenced data files those cards point at:

* :func:`write_xyt_csv` -- down-samples and writes a SimWrapper xytime
  point-cloud CSV (one row per point, EPSG:25832 coordinates read straight off
  the geometry column).
* :func:`write_kreis_choropleth_geojson` -- reprojects Kreis polygons to
  EPSG:4326, merges aggregated per-Kreis metrics onto them, and writes the
  choropleth GeoJSON consumed by the SimWrapper shapefiles plugin.

Both writers are shared across tabs: the ``fleet`` tab
(:mod:`braunschweig.analysis.simwrapper.fleet`) and the ``socio`` tab
(``emit_socio`` in :mod:`braunschweig.analysis.simwrapper.socio`) both
call :func:`write_xyt_csv`; :func:`write_kreis_choropleth_geojson` is currently
only called by the fleet tab. Neither tab owns them, so they were relocated
here from ``fleet.py`` -- where Task 1 of this split had to place them
temporarily, because no dedicated "generic layer writers" sibling existed yet
and leaving them in the ``spatial_export`` facade would have forced a
forbidden facade-import cycle (see ``fleet.py``'s prior module docstring
history in git blame) -- now that this dedicated sibling exists to own them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import geopandas as gpd

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper.spatial")

# Maximum number of points written into an xytime point-cloud CSV. A 100% run
# has millions of vehicles/homes; writing and rendering all of them is slow, so
# the raw point cloud is down-sampled to this cap (deterministically, logged).
# Aggregate maps (choropleths, hexagon density) always use the full data.
MAX_XYT_POINTS = 150_000
_XYT_SAMPLE_SEED = 42


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

    # Performance / browser cap: a raw point cloud of a 100% run is millions of
    # rows, which is slow to write and to render. Down-sample to MAX_XYT_POINTS
    # with a FIXED seed (deterministic, reproducible) and LOG the reduction --
    # this is an explicit, observable cap, NOT a silent truncation. Aggregate
    # maps (choropleths, hexagon density) use the full data and are unaffected.
    n_full = len(rows)
    if n_full > MAX_XYT_POINTS:
        rows = rows.sample(n=MAX_XYT_POINTS, random_state=_XYT_SAMPLE_SEED) \
                   .sort_index().reset_index(drop=True)
        LOGGER.info(
            "[xytime] %s: down-sampled point cloud %d -> %d (cap MAX_XYT_POINTS=%d, "
            "seed %d); aggregate maps use the full data",
            name, n_full, len(rows), MAX_XYT_POINTS, _XYT_SAMPLE_SEED,
        )

    out_path = folder / name
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("# EPSG:25832\n")
        rows.to_csv(fh, index=False)

    LOGGER.info("[xytime] wrote xytime CSV %s (%d points%s)", name, len(rows),
                f" of {n_full}" if n_full != len(rows) else "")
    return name


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
