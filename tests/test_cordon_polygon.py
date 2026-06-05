"""Tests for the cordon polygon (dissolved RVB municipalities + buffer).

The cordon polygon bounds the clipped MATSim network; links crossing its boundary
become cordon gates and activities outside it are eqasim ``outside`` activities.
Region-neutral geometry helpers, EPSG:25832 (metres).
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box, Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.spatial.cordon import (  # noqa: E402
    build_cordon_polygon,
    buffer_m_from_fraction,
)


def _municipalities():
    # Two adjacent ZGB-like communes in EPSG:25832 (metres).
    return gpd.GeoDataFrame(
        {"commune_id": ["03101000", "03102000"]},
        geometry=[box(600000, 5790000, 610000, 5800000),
                  box(610000, 5790000, 620000, 5800000)],
        crs="EPSG:25832",
    )


def test_cordon_dissolves_and_buffers():
    muni = _municipalities()
    poly = build_cordon_polygon(muni, buffer_m=1000.0)
    # Dissolved bbox is 600000..620000 x 5790000..5800000; +1000 m buffer means a
    # point 500 m outside the raw edge is inside, 1500 m outside is not.
    assert poly.contains(Point(620500, 5795000))      # within buffer
    assert not poly.contains(Point(621500, 5795000))  # beyond buffer
    assert poly.area >= muni.geometry.union_all().area


def test_zero_buffer_equals_dissolved_union():
    muni = _municipalities()
    poly = build_cordon_polygon(muni, buffer_m=0.0)
    assert abs(poly.area - muni.geometry.union_all().area) < 1.0


def test_build_cordon_polygon_rejects_empty_and_negative():
    import pytest
    empty = gpd.GeoDataFrame({"commune_id": []}, geometry=[], crs="EPSG:25832")
    with pytest.raises(ValueError):
        build_cordon_polygon(empty, buffer_m=100.0)
    with pytest.raises(ValueError):
        build_cordon_polygon(_municipalities(), buffer_m=-1.0)


def test_buffer_m_from_fraction_uses_extent_radius():
    muni = _municipalities()
    # Dissolved bbox 20 km x 10 km -> half-diagonal ~ 11180 m; 10% -> ~1118 m.
    b = buffer_m_from_fraction(muni, fraction=0.10)
    assert 1000.0 < b < 1300.0


def test_buffer_m_from_fraction_zero():
    assert buffer_m_from_fraction(_municipalities(), fraction=0.0) == 0.0
