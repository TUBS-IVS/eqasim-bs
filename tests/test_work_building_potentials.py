"""Tests for the building-level potential_work weight in braunschweig.locations.work.

Covers:
- OFF path: exactly area*floors (byte-identical to legacy).
- ON path: potential_work from matching building, area*floors fallback where no
  building matches.
"""
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, Polygon
from braunschweig.locations.work import compute_employees_weight


def _work_points():
    return gpd.GeoDataFrame(
        {"area": [100.0, 200.0], "floors": [2, 2]},
        geometry=[Point(5, 5), Point(500, 500)], crs="EPSG:25832",
    )


def _buildings():
    b = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    return gpd.GeoDataFrame({"building_id": [0], "potential_work": [42.0]},
                            geometry=[b], crs="EPSG:25832")


def test_off_is_area_times_floors():
    w = compute_employees_weight(_work_points(), _buildings(), enabled=False)
    assert list(w) == [200.0, 400.0]   # area*floors, byte-identical to legacy


def test_on_uses_potential_with_area_fallback():
    w = compute_employees_weight(_work_points(), _buildings(), enabled=True)
    assert w[0] == 42.0                 # inside building -> potential_work
    assert w[1] == 400.0               # outside -> area*floors fallback


def test_on_with_no_buildings_falls_back_to_area_floors():
    """Empty buildings GeoDataFrame -> full fallback to area*floors."""
    empty = gpd.GeoDataFrame({"potential_work": []},
                             geometry=[], crs="EPSG:25832")
    w = compute_employees_weight(_work_points(), empty, enabled=True)
    assert list(w) == [200.0, 400.0]


def test_on_with_none_buildings_falls_back_to_area_floors():
    """None buildings -> full fallback to area*floors."""
    w = compute_employees_weight(_work_points(), None, enabled=True)
    assert list(w) == [200.0, 400.0]
