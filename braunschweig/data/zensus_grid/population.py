"""
Load the Zensus 2022 100 m population grid clipped to the ZGB-8 study
area.

Source data
-----------
``eqasim-data/data/zensus_grid/population_100m.parquet`` (3.1 M rows,
columns ``x, y, value``) — populated cells only.  Coordinates are the
SW corner of each 100 m INSPIRE cell in EPSG:3035 (LAEA Europe), in
metres.

Output
------
A ``geopandas.GeoDataFrame`` indexed by ``grid_id`` with columns:

    - ``grid_id``      INSPIRE-style identifier ``CRS3035RES100mN{y}E{x}``
                       where the integers are SW-corner cell coordinates
                       in 100 m units (i.e. metres / 100).
    - ``geometry``     100 × 100 m square Polygon, EPSG:3035.
    - ``einwohner``    integer inhabitant count (Zensus 2022 stichtag
                       2022-05-15).

The cells are filtered to the bounding box of the dissolved ZGB-8 union
plus a small buffer (default 100 m) so that downstream stages can
spatially join cells against Kreis polygons without losing edge cells.

Configuration keys
------------------
- ``data_path``                       repository data root
- ``zensus_grid.population_path``     parquet path (default
                                      ``zensus_grid/population_100m.parquet``)
- ``zensus_grid.bbox_buffer_m``       extra buffer around ZGB-8 bbox in
                                      metres (default 200.0)
- ``germany.population_path`` /
  ``germany.population_source``       VG250-EW vsizip (re-used)
- ``bavaria.political_prefix``        ZGB-8 ARS list
"""

from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd
from shapely.geometry import box


CELL_SIZE_M = 100
EPSG_GRID = 3035
EPSG_VG250 = 25832


def configure(context):
    context.config("data_path")
    context.config(
        "zensus_grid.population_path",
        "zensus_grid/population_100m.parquet",
    )
    context.config("zensus_grid.bbox_buffer_m", 200.0)
    context.config(
        "germany.population_path",
        "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
    )
    context.config(
        "germany.population_source",
        "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg",
    )
    context.config("bavaria.political_prefix")


def _resolve_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("zensus_grid.population_path"),
    )


def _vg250_vsi_path(context) -> str:
    archive = "{}/{}".format(
        context.config("data_path"),
        context.config("germany.population_path"),
    )
    inner = context.config("germany.population_source")
    return f"/vsizip/{archive}/{inner}"


def _zgb_bbox_3035(context) -> tuple[float, float, float, float]:
    """Return the ZGB-8 union bbox (minx, miny, maxx, maxy) in EPSG:3035."""
    zgb = [str(p) for p in context.config("bavaria.political_prefix")]
    krs = gpd.read_file(
        _vg250_vsi_path(context),
        layer="vg250_krs",
        columns=["ARS", "GF"],
        engine="pyogrio",
    )
    krs["ars5"] = krs["ARS"].astype(str).str[:5]
    krs = krs[krs["ars5"].isin(zgb)]
    if krs.empty:
        raise RuntimeError(
            "[braunschweig.data.zensus_grid.population] "
            f"no VG250 Kreis polygons matched ZGB scope {zgb}"
        )
    if krs.crs is None:
        krs = krs.set_crs(EPSG_VG250)
    krs_3035 = krs.to_crs(EPSG_GRID)
    buf = float(context.config("zensus_grid.bbox_buffer_m"))
    minx, miny, maxx, maxy = krs_3035.total_bounds
    return (minx - buf, miny - buf, maxx + buf, maxy + buf)


def execute(context) -> gpd.GeoDataFrame:
    path = _resolve_path(context)
    df = pd.read_parquet(path, columns=["x", "y", "value"])

    minx, miny, maxx, maxy = _zgb_bbox_3035(context)
    mask = (
        (df["x"] >= minx - CELL_SIZE_M)
        & (df["x"] <= maxx)
        & (df["y"] >= miny - CELL_SIZE_M)
        & (df["y"] <= maxy)
    )
    clipped = df[mask].copy()

    if clipped.empty:
        raise RuntimeError(
            "[braunschweig.data.zensus_grid.population] "
            "no Zensus cells inside the ZGB-8 bbox — "
            "check `bavaria.political_prefix` and the source parquet."
        )

    x = clipped["x"].to_numpy()
    y = clipped["y"].to_numpy()
    clipped["grid_id"] = [
        f"CRS3035RES100mN{int(yy) // CELL_SIZE_M}E{int(xx) // CELL_SIZE_M}"
        for xx, yy in zip(x, y)
    ]
    clipped["einwohner"] = clipped["value"].astype("int32")

    geometries = [
        box(float(xx), float(yy), float(xx) + CELL_SIZE_M, float(yy) + CELL_SIZE_M)
        for xx, yy in zip(x, y)
    ]
    gdf = gpd.GeoDataFrame(
        clipped[["grid_id", "einwohner"]].reset_index(drop=True),
        geometry=geometries,
        crs=f"EPSG:{EPSG_GRID}",
    )

    print(
        "[braunschweig.data.zensus_grid.population] "
        f"{len(gdf):,} cells, {gdf['einwohner'].sum():,} inhabitants "
        f"in ZGB-8 bbox"
    )
    return gdf


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"Zensus 2022 100 m population parquet not found: {path}\n"
            "Run scripts/download_zensus_grid.py to fetch it."
        )
    return os.path.getsize(path)
