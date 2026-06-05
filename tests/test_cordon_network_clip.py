"""Tests for the cross-cordon OSM-clip geometry (enlarged network extent).

The simulation network is clipped (osmosis --bounding-polygon) to the dissolved
in-scope municipalities. For the cross-cordon feature the network must extend
beyond the cordon so motorways cross it and the eqasim cutter can create real
boundary links. This enlarges only the OSM clip polygon (network extent), not the
population scope. Region-neutral, metric CRS (EPSG:25832).
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box, Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.network_clip import osm_clip_geometry  # noqa: E402


def _municipalities():
    # Two adjacent communes in EPSG:25832 (metres); union spans x 600000..620000.
    return gpd.GeoDataFrame(
        {"commune_id": ["a", "b"]},
        geometry=[box(600000, 5790000, 610000, 5800000),
                  box(610000, 5790000, 620000, 5800000)],
        crs="EPSG:25832",
    )


def test_off_returns_dissolved_union():
    muni = _municipalities()
    out = osm_clip_geometry(muni, cordon_enabled=False, source_buffer_m=30000)
    assert len(out) == 1
    assert out.crs == muni.crs
    assert abs(out.geometry.iloc[0].area - muni.geometry.union_all().area) < 1.0


def test_on_enlarges_by_buffer():
    muni = _municipalities()
    out = osm_clip_geometry(muni, cordon_enabled=True, source_buffer_m=20000)
    geom = out.geometry.iloc[0]
    assert geom.area > muni.geometry.union_all().area
    # 15 km east of the union edge is within the 20 km source buffer; 25 km is not.
    assert geom.contains(Point(620000 + 15000, 5795000))
    assert not geom.contains(Point(620000 + 25000, 5795000))


def test_on_zero_buffer_equals_union():
    muni = _municipalities()
    out = osm_clip_geometry(muni, cordon_enabled=True, source_buffer_m=0)
    assert abs(out.geometry.iloc[0].area - muni.geometry.union_all().area) < 1.0
