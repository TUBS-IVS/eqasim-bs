"""Build 100m grid-cell geometry from the Zensus ZENSUS100m id.

The id encodes the cell's south-west corner in EPSG:3035 (ETRS89-LAEA):
``CRS3035RES100mN<north>E<east>`` (optionally prefixed ``ZENSUS100m_``). The cell
is the 100m square [east, east+100) x [north, north+100). Geometry is built native
in EPSG:3035 (exact squares) and reprojected to the project CRS (default 25832).
"""
from __future__ import annotations

import re
from typing import Iterable

import geopandas as gpd
from shapely.geometry import box

_CELL_RE = re.compile(r"CRS3035RES100mN(\d+)E(\d+)")
_CELL_M = 100


def parse_cell_origin(zensus100m: str) -> tuple[int, int]:
    """Return the cell's (east, north) SW corner in metres (EPSG:3035)."""
    match = _CELL_RE.search(str(zensus100m))
    if match is None:
        raise ValueError(f"unrecognised ZENSUS100m id: {zensus100m!r}")
    north, east = int(match.group(1)), int(match.group(2))
    return east, north


def cells_geodataframe(zensus100m_ids: Iterable[str], target_epsg: int = 25832) -> gpd.GeoDataFrame:
    """One 100m square polygon per id, built in EPSG:3035 then reprojected."""
    ids = list(zensus100m_ids)
    geoms = []
    for cid in ids:
        east, north = parse_cell_origin(cid)
        geoms.append(box(east, north, east + _CELL_M, north + _CELL_M))
    gdf = gpd.GeoDataFrame({"zensus100m": ids}, geometry=geoms, crs="EPSG:3035")
    if target_epsg != 3035:
        gdf = gdf.to_crs(epsg=target_epsg)
    return gdf
