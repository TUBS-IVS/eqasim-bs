import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon
from braunschweig.data.building_potential_attach import attach_potential


def _buildings():
    b = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    return gpd.GeoDataFrame({"building_id": [0], "potential_work": [42.0]},
                            geometry=[b], crs="EPSG:25832")


def test_inside_point_takes_building_potential():
    cand = gpd.GeoDataFrame(
        {"fallback": [1.0, 2.0]},
        geometry=[Point(5, 5), Point(100, 100)], crs="EPSG:25832",
    )
    vals, primary, fallback = attach_potential(
        cand, _buildings(), "potential_work",
        fallback=cand["fallback"].values, label="test",
    )
    assert primary == 1 and fallback == 1
    assert vals[0] == 42.0          # inside -> building potential
    assert vals[1] == 2.0           # outside -> fallback value, order preserved


def test_order_is_preserved_and_length_matches():
    cand = gpd.GeoDataFrame(
        {"fallback": [9.0]}, geometry=[Point(1, 1)], crs="EPSG:25832")
    vals, _, _ = attach_potential(
        cand, _buildings(), "potential_work",
        fallback=cand["fallback"].values, label="t")
    assert len(vals) == 1 and vals[0] == 42.0
