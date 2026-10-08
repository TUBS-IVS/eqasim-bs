"""Gate points inside the cut extent, rail exit stations, the external point draw and the detour rule."""
import numpy as np
import pandas as pd
import pytest
import geopandas as gpd
from shapely.geometry import LineString, Point, box

from braunschweig.synthesis.portal_trips import gates as g

SQUARE = box(0.0, 0.0, 10000.0, 10000.0)  # the cut extent of these tests


def _road_gates():
    links = gpd.GeoDataFrame({"link_id": ["L1", "L2"], "capacity": [6000.0, 1500.0],
                              "road_class": ["motorway", "primary"]},
                             geometry=[LineString([(9000.0, 5000.0), (11000.0, 5000.0)]),
                                       LineString([(5000.0, -1000.0), (5000.0, 1000.0)])], crs="EPSG:25832")
    gates = gpd.GeoDataFrame({"link_id": ["L1", "L2"], "capacity": [6000.0, 1500.0],
                              "road_class": ["motorway", "primary"], "gate_id": ["gate_e", "gate_s"]},
                             geometry=[Point(10000.0, 5000.0), Point(5000.0, 0.0)], crs="EPSG:25832")
    return gates, links


def test_rail_like_modes_copy_matches_the_incommuter_stage_constant():
    # The copy avoids importing a synpp stage module at module level; this test keeps it from drifting.
    from braunschweig.synthesis import incommuters
    assert g.RAIL_LIKE_MODES == incommuters.RAIL_LIKE_MODES


def test_gate_inside_points_takes_the_link_endpoint_inside_the_polygon():
    gates, links = _road_gates()
    inside = g.gate_inside_points(gates, links, SQUARE)
    assert list(inside.columns) == ["gate_id", "link_id", "road_class", "capacity", "geometry"]
    assert inside.set_index("gate_id").geometry["gate_e"].coords[0] == (9000.0, 5000.0)
    assert inside.set_index("gate_id").geometry["gate_s"].coords[0] == (5000.0, 1000.0)
    assert all(SQUARE.contains(point) for point in inside.geometry)


def test_gate_inside_points_raises_when_no_endpoint_is_inside():
    gates, links = _road_gates()
    links.loc[links["link_id"] == "L1", "geometry"] = LineString([(11000.0, 5000.0), (12000.0, 5000.0)])
    with pytest.raises(ValueError, match="gate_e"):
        g.gate_inside_points(gates, links, SQUARE)


def test_rail_exit_stations_are_inside_stops_adjacent_to_an_outside_stop_on_a_rail_route():
    stops = {"a": (2000.0, 5000.0), "b": (8000.0, 5000.0), "c": (14000.0, 5000.0), "d": (3000.0, 3000.0)}
    routes = [("rail", ["a", "b", "c"]), ("bus", ["b", "c"]), ("rail", ["a", "d"])]
    stations = g.rail_exit_stations(stops, routes, SQUARE)
    assert stations["stop_id"].tolist() == ["b"]
    assert stations.geometry.iloc[0].coords[0] == (8000.0, 5000.0)


def test_draw_external_points_prefers_the_reported_distance_band_weighted_by_population():
    external = gpd.GeoDataFrame({"commune_id": ["EXT1", "EXT2", "EXT3"], "ewz": [1.0, 1.0, 1000.0]},
                                geometry=[Point(100000.0, 0.0), Point(0.0, 100000.0), Point(300000.0, 0.0)],
                                crs="EPSG:25832")
    rng = np.random.default_rng(1)
    point_xy, commune, band_miss = g.draw_external_points(
        np.array([100000.0, 100000.0]), np.zeros((2, 2)), external, tolerance=0.2, rng=rng)
    assert set(commune) <= {"EXT1", "EXT2"}       # EXT3 lies outside the 80-120 km band
    assert not band_miss.any()
    assert np.allclose(np.hypot(point_xy[:, 0], point_xy[:, 1]), 100000.0)


def test_draw_external_points_falls_back_to_the_nearest_distance_and_counts_the_miss():
    external = gpd.GeoDataFrame({"commune_id": ["EXT1"], "ewz": [5.0]},
                                geometry=[Point(300000.0, 0.0)], crs="EPSG:25832")
    point_xy, commune, band_miss = g.draw_external_points(
        np.array([100000.0]), np.zeros((1, 2)), external, tolerance=0.2, rng=np.random.default_rng(0))
    assert commune.tolist() == ["EXT1"] and band_miss.tolist() == [True]


def test_choose_gates_minimises_the_detour_and_uses_rail_stations_for_pt():
    road = gpd.GeoDataFrame({"gate_id": ["gate_e", "gate_s"], "link_id": ["L1", "L2"],
                             "road_class": ["motorway", "primary"], "capacity": [6000.0, 1500.0]},
                            geometry=[Point(9000.0, 5000.0), Point(5000.0, 1000.0)], crs="EPSG:25832")
    rail = gpd.GeoDataFrame({"stop_id": ["hbf"]}, geometry=[Point(5000.0, 5000.0)], crs="EPSG:25832")
    origin = np.array([[5000.0, 5000.0], [5000.0, 5000.0], [5000.0, 5000.0]])
    point = np.array([[100000.0, 5000.0], [5000.0, -100000.0], [100000.0, 5000.0]])
    chosen = g.choose_gates(origin, point, np.array(["car", "car_passenger", "pt"]), road, rail)
    assert chosen["gate_id"].tolist() == ["gate_e", "gate_s", "hbf"]
    assert chosen["kind"].tolist() == ["road", "road", "rail"]
    assert chosen[["x", "y"]].iloc[2].tolist() == [5000.0, 5000.0]


def test_choose_gates_raises_when_a_pt_stay_has_no_rail_station():
    road = gpd.GeoDataFrame({"gate_id": ["gate_e"], "link_id": ["L1"], "road_class": ["motorway"],
                             "capacity": [6000.0]}, geometry=[Point(9000.0, 5000.0)], crs="EPSG:25832")
    rail = gpd.GeoDataFrame({"stop_id": []}, geometry=[], crs="EPSG:25832")
    with pytest.raises(ValueError, match="rail exit station"):
        g.choose_gates(np.zeros((1, 2)), np.array([[100000.0, 0.0]]), np.array(["pt"]), road, rail)
