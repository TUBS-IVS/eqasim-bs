"""Gate points inside the cut extent, rail exit stations, the external point draw and the detour rule."""
import numpy as np
import pandas as pd
import pytest
import geopandas as gpd
from shapely.geometry import LineString, Point, box

from braunschweig.data.transit.schedule import RouteInfo, Schedule
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


def _exit_schedule():
    stops = {"a": (2000.0, 5000.0), "b": (8000.0, 5000.0), "c": (14000.0, 5000.0)}

    def route(route_id, mode, stop_ids):
        return RouteInfo("L_" + route_id, route_id, mode, tuple(stop_ids), (0.0,) * len(stop_ids), (0.0,))

    return Schedule(stops, [route("r1", "rail", ["a", "b", "c"]), route("r2", "bus", ["b", "c"]),
                            route("r3", "bus", ["c", "b"])])


def test_exit_stops_lists_one_row_per_inside_stop_and_route_with_an_outside_next_stop():
    exits = g.exit_stops(_exit_schedule(), SQUARE)
    assert list(exits.columns) == ["stop_id", "line_id", "route_id", "mode", "outside_x", "outside_y", "geometry"]
    # r3 enters the extent at b (an ENTRY, not an exit), so only r1 and r2 qualify.
    assert sorted(exits["route_id"]) == ["r1", "r2"]
    assert exits["stop_id"].tolist() == ["b", "b"]
    assert (exits["outside_x"] == 14000.0).all() and (exits["outside_y"] == 5000.0).all()
    assert exits.crs.to_epsg() == 25832
    assert exits.geometry.iloc[0].coords[0] == (8000.0, 5000.0)


def test_exit_stops_modes_filter_keeps_only_the_given_lower_cased_modes():
    exits = g.exit_stops(_exit_schedule(), SQUARE, modes=frozenset({"rail"}))
    assert exits["route_id"].tolist() == ["r1"]
    assert g.ALL_MODES == "all"


def test_exit_stops_loop_route_lists_the_stop_before_the_outside_stop_only():
    stops = {"a": (2000.0, 5000.0), "c": (14000.0, 5000.0), "b": (8000.0, 5000.0)}
    loop = RouteInfo("L", "loop", "rail", ("a", "c", "b"), (0.0, 0.0, 0.0), (0.0,))
    exits = g.exit_stops(Schedule(stops, [loop]), SQUARE)
    assert exits["stop_id"].tolist() == ["a"]


def test_exit_stop_points_has_one_row_per_distinct_stop():
    points = g.exit_stop_points(g.exit_stops(_exit_schedule(), SQUARE))
    assert points["stop_id"].tolist() == ["b"]
    assert points.geometry.iloc[0].coords[0] == (8000.0, 5000.0)


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


# ---- C5: direction, served window and router connection ----

from braunschweig.data.transit import connections  # noqa: E402

INSIDE_STOPS = {"hbf", "soph", "bus1", "x:y:1"}


def _gate_schedule(departures):
    """Stops hbf/soph/bus1/x:y:1 inside, 'ext' east and 'extw' west outside; departures = {route_id: times}."""
    stops = {"hbf": (5000.0, 5000.0), "soph": (3000.0, 5000.0), "bus1": (4000.0, 5000.0),
             "x:y:1": (5000.0, 6000.0), "ext": (20000.0, 5000.0), "extw": (-9000.0, 5000.0)}

    def route(route_id, mode, stop_ids):
        return RouteInfo("L_" + route_id, route_id, mode, tuple(stop_ids), (0.0,) * len(stop_ids),
                         tuple(departures.get(route_id, ())))

    return Schedule(stops, [route("r_hbf", "rail", ["hbf", "ext"]), route("r_soph", "rail", ["soph", "ext"]),
                            route("r_bus", "bus", ["bus1", "extw"]), route("r_colon", "bus", ["x:y:1", "ext"])])


def _fake_router(connected, log):
    """Router returning real-shape trips/legs; a task connects exactly when its identifier is in ``connected``.

    A non-connecting task gets a leg whose access stop lies outside the extent, so ``connection_mask``
    itself (not the fake) rejects it.
    """
    def router(tasks):
        log.append(tasks.copy())
        ids = tasks["identifier"].tolist()
        trips = pd.DataFrame({"identifier": ids, "is_only_walk": 0, "access_distance_km": 0.3,
                              "egress_distance_km": 0.3, "transfer_distance_km": 0.0,
                              "total_walk_distance_km": 0.6})
        legs = pd.DataFrame({"identifier": ids, "leg_index": 0,
                             "access_stop_id": ["hbf" if i in connected else "far_outside" for i in ids],
                             "egress_stop_id": "soph"})
        return trips, legs
    return router


def _choose(departures, connected, *, point=(50000.0, 5000.0), has_return=True, n_stays=1, modes="all",
            n_candidates=5, log=None):
    log = [] if log is None else log
    schedule = _gate_schedule(departures)
    exits = g.exit_stops(schedule, SQUARE, modes=modes)
    n = n_stays
    frame = g.choose_pt_gates(
        np.tile([5000.0, 5200.0], (n, 1)), np.tile(point, (n, 1)),
        np.full(n, 36000.0), np.full(n, 37800.0), np.full(n, 50000.0), np.full(n, 51800.0),
        np.full(n, has_return), exits, schedule, _fake_router(connected, log),
        n_candidates=n_candidates, service_window_s=3600.0, inside_stop_ids=INSIDE_STOPS, walk_max_m=5000.0)
    return frame, log


def test_choose_pt_gates_skips_the_unserved_nearest_gate_and_takes_the_next_served_one():
    # East point: hbf (5000) has the smallest detour but no departure in the window; soph is served.
    # The gate arrival is about 36000 + share * 1800, so the window is roughly [36000, 39600].
    frame, log = _choose({"r_hbf": [20000.0], "r_soph": [36500.0, 52000.0]},
                         {"0:soph:out", "0:soph:ret", "0:hbf:out", "0:hbf:ret"},
                         modes="rail")
    row = frame.iloc[0]
    assert list(frame.columns) == g.PT_GATE_COLUMNS
    assert (row["gate_id"], row["reason"]) == ("soph", g.REASON_GATE_FOUND)
    assert row["n_served"] == 1 and row["n_connected"] == 1
    assert (row["x"], row["y"]) == (3000.0, 5000.0)
    # the unserved gate never reached the router
    assert all("hbf" not in i for task in log for i in task["identifier"])


def test_choose_pt_gates_a_bus_exit_wins_when_it_is_the_only_served_one():
    # Point in the west: only the bus route r_bus heads west, so no rail gate is in direction.
    frame, _ = _choose({"r_bus": [36200.0, 52000.0]}, {"0:bus1:out", "0:bus1:ret"}, point=(-50000.0, 5000.0))
    assert frame.iloc[0]["gate_id"] == "bus1" and frame.iloc[0]["n_in_direction"] == 1


def test_choose_pt_gates_reports_no_gate_in_direction():
    # No exit route heads north.
    frame, log = _choose({"r_hbf": [36500.0]}, set(), point=(5000.0, 90000.0))
    row = frame.iloc[0]
    assert row["gate_id"] is None and row["reason"] == g.REASON_NO_GATE_IN_DIRECTION
    assert row["n_in_direction"] == 0 and log == []


def test_choose_pt_gates_reports_no_gate_served_when_every_candidate_lacks_a_departure():
    frame, log = _choose({"r_hbf": [1000.0], "r_soph": [90000.0]}, set(), modes="rail")
    row = frame.iloc[0]
    assert row["reason"] == g.REASON_NO_GATE_SERVED and row["n_served"] == 0 and log == []


def test_choose_pt_gates_reports_no_connection_when_the_router_rejects_every_served_gate():
    frame, _ = _choose({"r_hbf": [36500.0, 52000.0], "r_soph": [36500.0, 52000.0]}, set(), modes="rail")
    row = frame.iloc[0]
    assert row["gate_id"] is None and row["reason"] == g.REASON_NO_CONNECTION
    assert row["n_served"] == 2 and row["n_connected"] == 0


def test_choose_pt_gates_without_a_return_leg_sends_no_return_task_and_needs_no_return_service():
    # r_hbf runs only in the outbound window: with a return leg hbf is not served at the re-entry.
    departures = {"r_hbf": [36500.0]}
    with_return, _ = _choose(departures, {"0:hbf:out", "0:hbf:ret"}, has_return=True,
                             modes="rail", n_candidates=1)
    assert with_return.iloc[0]["reason"] == g.REASON_NO_GATE_SERVED
    frame, log = _choose(departures, {"0:hbf:out"}, has_return=False, modes="rail", n_candidates=1)
    assert frame.iloc[0]["gate_id"] == "hbf"
    assert len(log) == 1 and not any(i.endswith(":ret") for task in log for i in task["identifier"])


def test_choose_pt_gates_return_connection_is_required_when_there_is_a_return():
    frame, _ = _choose({"r_hbf": [36500.0, 52000.0]}, {"0:hbf:out"}, modes="rail", n_candidates=1)
    assert frame.iloc[0]["reason"] == g.REASON_NO_CONNECTION


def test_choose_pt_gates_makes_one_router_call_per_direction_with_stays_times_candidates_tasks():
    departures = {"r_hbf": [36500.0, 52000.0], "r_soph": [36500.0, 52000.0]}
    connected = {f"{i}:{s}:{d}" for i in range(3) for s in ("hbf", "soph") for d in ("out", "ret")}
    frame, log = _choose(departures, connected, n_stays=3, n_candidates=2, modes="rail")
    assert [len(task) for task in log] == [6, 6]
    assert list(log[0].columns) == connections.TASK_COLUMNS
    assert frame["reason"].tolist() == [g.REASON_GATE_FOUND] * 3
    # outbound: origin -> gate at the planned departure; return: gate -> origin at the re-entry time
    out = log[0].set_index("identifier").loc["0:hbf:out"]
    assert (out["origin_x"], out["origin_y"], out["destination_x"], out["destination_y"],
            out["departure_time"]) == (5000.0, 5200.0, 5000.0, 5000.0, 36000.0)
    back = log[1].set_index("identifier").loc["0:hbf:ret"]
    assert (back["origin_x"], back["destination_y"]) == (5000.0, 5200.0)
    # return leg 50000..51800: the gate is 200 m from the origin and ~45 km from the point, so the re-entry
    # (return_departure + d(gate, point) / (d(gate, point) + d(gate, origin)) * 1800 s) lies just before arrival
    assert 51700.0 < back["departure_time"] < 51800.0


def test_choose_pt_gates_parses_stop_ids_that_contain_colons():
    frame, _ = _choose({"r_colon": [36500.0, 52000.0]}, {"0:x:y:1:out", "0:x:y:1:ret"}, modes="bus",
                       n_candidates=1)
    assert frame.iloc[0]["gate_id"] == "x:y:1"


def test_choose_pt_gates_keeps_only_the_n_smallest_detour_candidates():
    frame, log = _choose({"r_hbf": [36500.0], "r_soph": [36500.0]}, {"0:soph:out"}, has_return=False,
                         modes="rail", n_candidates=1)
    assert frame.iloc[0]["n_in_direction"] == 1 and frame.iloc[0]["reason"] == g.REASON_NO_CONNECTION
    assert log[0]["identifier"].tolist() == ["0:hbf:out"]


def test_choose_pt_gates_logs_the_reason_rates(caplog):
    with caplog.at_level("INFO", logger=g.logger.name):
        _choose({"r_hbf": [36500.0, 52000.0]}, {"0:hbf:out", "0:hbf:ret"}, modes="rail")
    assert any("gate_found 1/1" in r.getMessage() for r in caplog.records)


def test_choose_gates_takes_pt_stays_from_the_pt_gates_and_road_stays_by_detour():
    road = gpd.GeoDataFrame({"gate_id": ["gate_e", "gate_s"], "link_id": ["L1", "L2"],
                             "road_class": ["motorway", "primary"], "capacity": [6000.0, 1500.0]},
                            geometry=[Point(9000.0, 5000.0), Point(5000.0, 1000.0)], crs="EPSG:25832")
    pt_gates = pd.DataFrame({"gate_id": ["hbf"], "x": [5000.0], "y": [5000.0], "reason": [g.REASON_GATE_FOUND],
                             "n_in_direction": [1], "n_served": [1], "n_connected": [1]})
    origin = np.array([[5000.0, 5000.0]] * 3)
    point = np.array([[100000.0, 5000.0], [5000.0, -100000.0], [100000.0, 5000.0]])
    chosen = g.choose_gates(origin, point, np.array(["car", "car_passenger", "pt"]), road, pt_gates)
    assert list(chosen.columns) == g.GATE_COLUMNS
    assert chosen["gate_id"].tolist() == ["gate_e", "gate_s", "hbf"]
    assert chosen["kind"].tolist() == ["road", "road", "pt"]
    assert chosen[["x", "y"]].iloc[2].tolist() == [5000.0, 5000.0]


def test_choose_gates_raises_for_a_pt_stay_without_a_resolved_gate():
    road = gpd.GeoDataFrame({"gate_id": ["gate_e"], "link_id": ["L1"], "road_class": ["motorway"],
                             "capacity": [6000.0]}, geometry=[Point(9000.0, 5000.0)], crs="EPSG:25832")
    pt_gates = pd.DataFrame({"gate_id": [None], "x": [np.nan], "y": [np.nan], "reason": [g.REASON_NO_CONNECTION],
                             "n_in_direction": [1], "n_served": [1], "n_connected": [0]})
    with pytest.raises(ValueError, match="no_connection"):
        g.choose_gates(np.zeros((1, 2)), np.array([[100000.0, 0.0]]), np.array(["pt"]), road, pt_gates)


def test_choose_gates_rejects_non_finite_coordinates():
    road = gpd.GeoDataFrame({"gate_id": ["gate_e"], "link_id": ["L1"], "road_class": ["motorway"],
                             "capacity": [6000.0]}, geometry=[Point(9000.0, 5000.0)], crs="EPSG:25832")
    with pytest.raises(ValueError, match="origin"):
        g.choose_gates(np.array([[np.nan, 0.0]]), np.array([[1.0, 1.0]]), np.array(["car"]), road, None)
    with pytest.raises(ValueError, match="external point"):
        g.choose_gates(np.array([[0.0, 0.0]]), np.array([[np.inf, 1.0]]), np.array(["car"]), road, None)



# ---- review fixes: both-endpoints-inside, aggregated errors, validation, weighting ----

def _both_inside_gate():
    links = gpd.GeoDataFrame({"link_id": ["LB"], "capacity": [900.0], "road_class": ["secondary"]},
                             geometry=[LineString([(5000.0, 100.0), (5000.0, 3000.0)])], crs="EPSG:25832")
    gates = gpd.GeoDataFrame({"link_id": ["LB"], "capacity": [900.0], "road_class": ["secondary"],
                              "gate_id": ["gate_b"]}, geometry=[Point(5000.0, 0.0)], crs="EPSG:25832")
    return gates, links


def test_gate_inside_points_both_endpoints_inside_picks_the_deeper_one_and_logs_the_count(caplog):
    gates, links = _both_inside_gate()
    with caplog.at_level("INFO", logger=g.logger.name):
        inside = g.gate_inside_points(gates, links, SQUARE)
    assert inside.geometry.iloc[0].coords[0] == (5000.0, 3000.0)
    assert any("both link endpoints inside: 1/1" in record.getMessage() for record in caplog.records)


def test_gate_inside_points_both_endpoints_inside_works_for_a_multipolygon_cordon():
    from shapely.geometry import MultiPolygon
    gates, links = _both_inside_gate()
    cordon = MultiPolygon([SQUARE, box(20000.0, 0.0, 30000.0, 10000.0)])
    inside = g.gate_inside_points(gates, links, cordon)
    assert inside.geometry.iloc[0].coords[0] == (5000.0, 3000.0)


def test_gate_inside_points_lists_all_offending_gates_in_one_error():
    gates, links = _road_gates()
    links.loc[links["link_id"] == "L1", "geometry"] = LineString([(11000.0, 5000.0), (12000.0, 5000.0)])
    links = links[links["link_id"] != "L2"]  # gate_s now refers to a link that is missing
    with pytest.raises(ValueError) as error:
        g.gate_inside_points(gates, links, SQUARE)
    assert "gate_e" in str(error.value) and "gate_s" in str(error.value)


def test_rail_exit_stations_use_the_given_crs():
    stops = {"a": (2000.0, 5000.0), "b": (8000.0, 5000.0), "c": (14000.0, 5000.0)}
    stations = g.rail_exit_stations(stops, [("rail", ["a", "b", "c"])], SQUARE, crs="EPSG:3035")
    assert stations.crs.to_epsg() == 3035
    assert g.rail_exit_stations(stops, [("rail", ["a", "b", "c"])], SQUARE).crs.to_epsg() == 25832


def test_rail_exit_stations_raise_for_a_route_stop_missing_from_the_stops(caplog):
    stops = {"a": (2000.0, 5000.0)}
    with pytest.raises(ValueError, match="'ghost'"):
        g.rail_exit_stations(stops, [("rail", ["a", "ghost"])], SQUARE)


def test_rail_exit_stations_log_the_routes_scanned_and_the_stations_found(caplog):
    stops = {"a": (2000.0, 5000.0), "b": (8000.0, 5000.0), "c": (14000.0, 5000.0)}
    with caplog.at_level("INFO", logger=g.logger.name):
        g.rail_exit_stations(stops, [("rail", ["a", "b", "c"]), ("bus", ["b", "c"])], SQUARE)
    assert any("1 rail routes scanned, 1 exit stations" in record.getMessage() for record in caplog.records)


def _external(ewz, xs=None):
    xs = xs or [100000.0 + 1000.0 * k for k in range(len(ewz))]
    return gpd.GeoDataFrame({"commune_id": [f"EXT{k}" for k in range(len(ewz))], "ewz": ewz},
                            geometry=[Point(x, 0.0) for x in xs], crs="EPSG:25832")


@pytest.mark.parametrize("reported", [[np.nan], [np.inf], [0.0], [-5.0]])
def test_draw_external_points_rejects_non_positive_or_non_finite_reported_distances(reported):
    with pytest.raises(ValueError, match="reported distance"):
        g.draw_external_points(np.array(reported), np.zeros((1, 2)), _external([1.0]), 0.2,
                               np.random.default_rng(0))


def test_draw_external_points_names_the_first_invalid_stay():
    with pytest.raises(ValueError, match="first at stay index 1"):
        g.draw_external_points(np.array([1000.0, -1.0, np.nan]), np.zeros((3, 2)), _external([1.0]), 0.2,
                               np.random.default_rng(0))


@pytest.mark.parametrize("tolerance", [0.0, 1.0, -0.1, 1.5, np.nan])
def test_draw_external_points_rejects_a_tolerance_outside_the_open_unit_interval(tolerance):
    with pytest.raises(ValueError, match="tolerance"):
        g.draw_external_points(np.array([100000.0]), np.zeros((1, 2)), _external([1.0]), tolerance,
                               np.random.default_rng(0))


def test_draw_external_points_rejects_non_finite_origins():
    with pytest.raises(ValueError, match="origin"):
        g.draw_external_points(np.array([100000.0]), np.array([[np.nan, 0.0]]), _external([1.0]), 0.2,
                               np.random.default_rng(0))


def test_draw_external_points_rejects_a_band_with_zero_population_weight():
    with pytest.raises(ValueError, match="population weight"):
        g.draw_external_points(np.array([100000.0]), np.zeros((1, 2)), _external([0.0, 0.0]), 0.2,
                               np.random.default_rng(0))


def test_draw_external_points_population_weighting_is_proven_by_many_draws():
    external = _external([1.0, 1e9])
    n = 2000
    _, commune, band_miss = g.draw_external_points(
        np.full(n, 100500.0), np.zeros((n, 2)), external, 0.2, np.random.default_rng(42))
    assert not band_miss.any()
    assert (commune == "EXT1").mean() > 0.99


def test_draw_external_points_band_miss_flags_are_per_stay_for_a_mixed_vector():
    external = _external([1.0], xs=[100000.0])
    # stay 0 and 2 hit the band around 100 km, stay 1 asks for 10 km and misses.
    _, commune, band_miss = g.draw_external_points(
        np.array([100000.0, 10000.0, 95000.0]), np.zeros((3, 2)), external, 0.2, np.random.default_rng(0))
    assert band_miss.tolist() == [False, True, False]
    assert commune.tolist() == ["EXT0", "EXT0", "EXT0"]


def test_exit_stops_accepts_a_single_mode_string_and_all_case_insensitively():
    assert g.exit_stops(_exit_schedule(), SQUARE, modes="rail")["route_id"].tolist() == ["r1"]
    assert len(g.exit_stops(_exit_schedule(), SQUARE, modes="All")) == 2


def test_exit_stops_rejects_modes_that_are_not_strings():
    with pytest.raises(ValueError, match="modes"):
        g.exit_stops(_exit_schedule(), SQUARE, modes=5)
    with pytest.raises(ValueError, match="modes"):
        g.exit_stops(_exit_schedule(), SQUARE, modes=[1, 2])


def test_exit_stops_raises_for_a_route_stop_missing_from_the_stops():
    schedule = Schedule({"a": (2000.0, 5000.0)}, [RouteInfo("L", "r", "rail", ("a", "ghost"), (0.0, 0.0), (0.0,))])
    with pytest.raises(ValueError, match="'ghost'"):
        g.exit_stops(schedule, SQUARE)


def test_exit_stops_empty_result_keeps_columns_and_crs_and_warns(caplog):
    with caplog.at_level("WARNING", logger=g.logger.name):
        exits = g.exit_stops(_exit_schedule(), SQUARE, modes=frozenset({"tram"}), crs="EPSG:3035")
    assert len(exits) == 0
    assert list(exits.columns) == ["stop_id", "line_id", "route_id", "mode", "outside_x", "outside_y", "geometry"]
    assert exits.crs.to_epsg() == 3035
    assert any(r.levelname == "WARNING" and "tram" in r.getMessage() for r in caplog.records)
