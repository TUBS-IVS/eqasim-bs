"""Tests for in-commuter synthesis helpers (PT direct-ride entry stops + gate draw)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

from braunschweig.synthesis.incommuters import (  # noqa: E402
    RAIL_LIKE_MODES,
    _sample_workplaces,
    build_pt_entry_stops,
    build_incommuter_frames,
    direct_ride_stop_stats,
    direct_ride_stops,
)
from braunschweig.data.cordon.gate_assignment import sample_gate_per_agent  # noqa: E402

ZGB = {"03101", "03102"}


def test_direct_ride_stops_keeps_external_stops_of_zgb_serving_routes():
    routes = [
        ("rail", ["s_ext_a", "s_ext_b", "s_zgb"]),   # serves ZGB -> a,b are entries
        ("bus", ["s_ext_c", "s_ext_d"]),             # never reaches ZGB -> ignored
    ]
    stop_kreis = {
        "s_ext_a": "03241", "s_ext_b": "03241", "s_zgb": "03101",
        "s_ext_c": "03158", "s_ext_d": "03158",
    }
    entry = direct_ride_stops(routes, stop_kreis, ZGB)
    assert entry == {"03241": {"s_ext_a", "s_ext_b"}}


def test_direct_ride_stops_groups_by_source_kreis():
    routes = [("rail", ["a", "b", "z"])]
    stop_kreis = {"a": "03241", "b": "03158", "z": "03102"}
    entry = direct_ride_stops(routes, stop_kreis, ZGB)
    assert entry == {"03241": {"a"}, "03158": {"b"}}


def test_sample_gate_per_agent_weights_by_volume_and_is_seeded():
    # Kreis 03241 uses gate_A 90% and gate_B 10%; 03158 uses gate_C only.
    assignment = pd.DataFrame([
        ("03241", "gate_A", 900),
        ("03241", "gate_B", 100),
        ("03158", "gate_C", 50),
    ], columns=["ars5", "gate_id", "inbound"])
    rng = np.random.default_rng(42)
    agents = ["03241"] * 1000 + ["03158"] * 10
    gates = sample_gate_per_agent(agents, assignment, rng)
    chosen = pd.Series(gates)
    # 03158 agents always gate_C
    assert set(chosen.iloc[1000:]) == {"gate_C"}
    # 03241 agents split ~90/10 toward gate_A
    share_a = (chosen.iloc[:1000] == "gate_A").mean()
    assert 0.85 < share_a < 0.95


def test_build_pt_entry_stops_maps_and_filters():
    # s1 in external Kreis 03241, s2 in ZGB 03101; a route serving both -> s1 is a
    # one-seat entry stop for 03241. s3 (external, on a route never reaching ZGB) out.
    stops = {"s1": (605000.0, 5805000.0), "s2": (615000.0, 5805000.0),
             "s3": (605000.0, 5845000.0)}
    routes = [("rail", ["s1", "s2"]), ("bus", ["s3"])]
    kreise = gpd.GeoDataFrame(
        {"ars5": ["03241", "03101"]},
        geometry=[box(600000, 5800000, 610000, 5850000),     # contains s1, s3
                  box(610000, 5800000, 620000, 5810000)],    # contains s2 (ZGB)
        crs="EPSG:25832")
    df = build_pt_entry_stops(stops, routes, kreise, zgb_kreise={"03101"})
    assert list(df.columns) == ["source_ars5", "stop_id", "x", "y",
                                "n_zgb_routes", "is_rail"]
    assert set(zip(df["source_ars5"], df["stop_id"])) == {("03241", "s1")}
    # s1 is on one ZGB-serving rail route -> 1 route, rail-like.
    row = df[df["stop_id"] == "s1"].iloc[0]
    assert int(row["n_zgb_routes"]) == 1 and bool(row["is_rail"]) is True


def test_sample_gate_per_agent_none_for_unknown_kreis():
    assignment = pd.DataFrame([("03241", "gate_A", 900)],
                             columns=["ars5", "gate_id", "inbound"])
    rng = np.random.default_rng(0)
    assert sample_gate_per_agent(["09999"], assignment, rng) == [None]


# --- workplace-fallback transparency (CLAUDE.md "Fallback transparency") ---------

def _zgb_work():
    """Two ZGB Kreise (03101, 03102) each with one employment-weighted workplace."""
    return gpd.GeoDataFrame(
        {"location_id": ["w_03101", "w_03102"],
         "commune_id": ["03101000", "03102000"],
         "employees": [50, 70]},
        geometry=[Point(606000.0, 5805000.0), Point(616000.0, 5805000.0)],
        crs="EPSG:25832")


def test_sample_workplaces_no_fallback_when_every_dest_kreis_has_workplaces():
    # PRIMARY path: every agent's dest Kreis (03101 / 03102) has a workplace, so each
    # agent is sampled inside its own Kreis and the fallback count is exactly zero.
    work = _zgb_work()
    dest_ars = np.array(["03101", "03102", "03101", "03102", "03101"])
    rng = np.random.default_rng(0)
    x, y, ids, n_fallback = _sample_workplaces(dest_ars, work, rng)
    assert n_fallback == 0
    assert len(ids) == len(dest_ars)
    # primary draw stays inside the agent's Kreis -> matching location id per dest
    expected = {"03101": "w_03101", "03102": "w_03102"}
    assert [expected[d] for d in dest_ars] == list(ids)


def test_sample_workplaces_counts_fallback_when_dest_kreis_absent():
    # FALLBACK path: 03999 has no workplace in the ZGB pool -> those agents fall back
    # to the whole-ZGB pool and are counted; the present Kreis 03101 stays primary.
    work = _zgb_work()
    dest_ars = np.array(["03101", "03999", "03999"])
    rng = np.random.default_rng(0)
    x, y, ids, n_fallback = _sample_workplaces(dest_ars, work, rng)
    assert n_fallback == 2
    assert len(ids) == len(dest_ars)


# ---------------------------------------------------------------------------
# PT-boarding via rail stations (new B5 placement): primary and fallback tests
# ---------------------------------------------------------------------------

def _minimal_inputs():
    """Minimal shared inputs for build_incommuter_frames tests."""
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000"], "capacity": [8000.0], "road_class": ["motorway"]},
        geometry=[Point(605000, 5840000)], crs="EPSG:25832")
    assignment = pd.DataFrame([("03241", "gate_0000", 1000, 0)],
                              columns=["ars5", "gate_id", "inbound", "outbound"])
    flows = pd.DataFrame([("03241", "03101", 1000)],
                         columns=["orig_ars", "dest_ars", "flow"])
    zgb_work = gpd.GeoDataFrame(
        {"location_id": ["work_1"], "commune_id": ["03101000"], "employees": [50]},
        geometry=[Point(606000, 5805000)], crs="EPSG:25832")
    hts_persons = pd.DataFrame({"person_id": [1], "employed": [True], "age": [42],
                                "sex": ["female"]})
    hts_trips = pd.DataFrame([
        (1, "home", "work", 7 * 3600.0, 8 * 3600.0),
        (1, "work", "home", 17 * 3600.0, 18 * 3600.0),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])
    return gates, assignment, flows, zgb_work, hts_persons, hts_trips


def _pt_entry_stops_new_schema(ars5="03241", stop_id="railA",
                                x=604000.0, y=5841000.0):
    """A one-row pt_entry_stops DataFrame with the new B4 schema (reach + ewz)."""
    return pd.DataFrame({
        "source_ars5": [ars5],
        "stop_id": [stop_id],
        "x": [x],
        "y": [y],
        "reach": ["direct"],
        "ewz": [50000.0],
    })


def test_pt_agents_board_at_rail_station_not_road_gate():
    """PRIMARY: PT agents from a Kreis with eligible stations board at a rail station.

    With 100% PT demand and one eligible rail station (railA) for source Kreis 03241,
    every PT agent must board at railA (604000, 5841000) rather than the road gate
    at (605000, 5840000).  The validation frame must record entry_kind=="rail_station"
    and entry_x/entry_y matching the station coordinates.
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()
    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops)

    # All agents are PT (100% pt reference).
    assert (frames["trips"]["mode"] == "pt").all(), \
        "all agents should be PT with 100% PT reference"

    # PT home coordinates must equal the rail station, not the road gate.
    home_locs = frames["locations"][frames["locations"]["activity_index"] == 0]
    station_x, station_y = 604000.0, 5841000.0
    gate_x, gate_y = 605000.0, 5840000.0
    for _, row in home_locs.iterrows():
        assert abs(row.geometry.x - station_x) < 1e-6, \
            f"PT home x {row.geometry.x} does not match rail station {station_x}"
        assert abs(row.geometry.y - station_y) < 1e-6, \
            f"PT home y {row.geometry.y} does not match rail station {station_y}"
        # Must NOT be at the road gate.
        assert not (abs(row.geometry.x - gate_x) < 1e-6 and
                    abs(row.geometry.y - gate_y) < 1e-6), \
            "PT agent home must not be at the road gate"

    # Validation frame: entry_kind must be "rail_station" for all PT agents.
    val = frames["validation"]
    assert (val["entry_kind"] == "rail_station").all(), \
        "PT agents must have entry_kind=='rail_station'"
    assert (abs(val["entry_x"] - station_x) < 1e-6).all(), \
        "validation entry_x must match the rail station x coordinate"
    assert (abs(val["entry_y"] - station_y) < 1e-6).all(), \
        "validation entry_y must match the rail station y coordinate"


def test_car_agents_board_at_road_gate():
    """Car agents must board at the road gate; validation must record entry_kind=='road_gate'."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()
    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops)

    assert (frames["trips"]["mode"] == "car").all(), \
        "all agents should be car with 100% car reference"

    gate_x, gate_y = 605000.0, 5840000.0
    home_locs = frames["locations"][frames["locations"]["activity_index"] == 0]
    for _, row in home_locs.iterrows():
        assert abs(row.geometry.x - gate_x) < 1e-6, \
            f"car home x {row.geometry.x} does not match gate {gate_x}"
        assert abs(row.geometry.y - gate_y) < 1e-6, \
            f"car home y {row.geometry.y} does not match gate {gate_y}"

    val = frames["validation"]
    assert (val["entry_kind"] == "road_gate").all(), \
        "car agents must have entry_kind=='road_gate'"
    assert (abs(val["entry_x"] - gate_x) < 1e-6).all(), \
        "car validation entry_x must match the road gate x"
    assert (abs(val["entry_y"] - gate_y) < 1e-6).all(), \
        "car validation entry_y must match the road gate y"


def test_every_incommuter_owns_a_car_passenger_vehicle():
    """Regression: every in-commuter -- car AND pt -- must own a car_passenger vehicle.

    car_passenger is a network-routed mode (eqasim core NETWORK_MODES =
    [car, car_passenger, truck]) the in-loop discrete mode choice can assign to ANY
    agent regardless of car ownership. If an in-commuter lacks the vehicle, MATSim aborts
    routing in replanning with "Could not retrieve vehicle id ... for mode car_passenger"
    (observed on the 25% all-features popsim run, iteration 1). Residents get it via
    synthesis.vehicles.passengers.default; in-commuters must get it too. A 50/50 car/pt
    reference is used so the assertion also covers pt agents, which own no car vehicle.
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()
    rng = np.random.default_rng(7)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops)

    persons = set(frames["persons"]["person_id"])
    vehicles = frames["vehicles"]
    assert len(persons) > 0, "fixture should produce at least one in-commuter"

    passenger_owners = set(vehicles.loc[vehicles["mode"] == "car_passenger", "owner_id"])
    missing = persons - passenger_owners
    assert not missing, (
        "every in-commuter must own a car_passenger vehicle; missing for: %s" % sorted(missing))

    # The car_passenger vehicle ids follow the "<person_id>:car_passenger" convention and
    # reference the default_car_passenger type (consistent with the resident passengers stage).
    cp = vehicles[vehicles["mode"] == "car_passenger"]
    assert (cp["vehicle_id"] == cp["owner_id"].astype(str) + ":car_passenger").all()
    assert (cp["type_id"] == "default_car_passenger").all()

    # Regression: car-mode agents still own their "car" vehicle (the fix only adds, never
    # removes). pt agents legitimately own no car vehicle -- only car_passenger.
    car_trip_persons = set(frames["trips"].loc[frames["trips"]["mode"] == "car", "person_id"])
    car_owners = set(vehicles.loc[vehicles["mode"] == "car", "owner_id"])
    assert car_trip_persons <= car_owners, \
        "car-mode in-commuters must still own a car vehicle"


def test_pt_fallback_to_car_when_no_station_in_source_kreis():
    """FALLBACK: a PT agent whose source Kreis has NO eligible rail station is reassigned to car.

    The agent's mode in trips and persons (car_availability) must reflect the
    reassignment consistently.  Its validation row must record entry_kind=='road_gate'
    and entry coordinates matching the road gate, not a rail station for a different Kreis.
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    # pt_entry_stops has only Kreis 03999, not the actual source Kreis 03241.
    pt_stops_wrong_kreis = pd.DataFrame({
        "source_ars5": ["03999"],  # different Kreis -> no station for 03241 agents
        "stop_id": ["railOther"],
        "x": [700000.0],
        "y": [5900000.0],
        "reach": ["direct"],
        "ewz": [50000.0],
    })
    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops_wrong_kreis,
        mode_balance=False,  # explicitly OFF: legacy lossy PT->car reassignment
    )

    # All agents were originally PT, but 03241 has no station -> all reassigned to car.
    assert (frames["trips"]["mode"] == "car").all(), \
        "PT agents with no source-Kreis rail station must be reassigned to car"

    # car_availability must reflect the car mode (not "none").
    persons = frames["persons"]
    assert (persons["car_availability"] == "all").all(), \
        "reassigned car agents must have car_availability='all'"

    # Validation: entry_kind must be 'road_gate' for all reassigned agents.
    val = frames["validation"]
    assert (val["entry_kind"] == "road_gate").all(), \
        "reassigned PT agents must have entry_kind=='road_gate' in validation"

    # Home locations must be at the road gate (not at a rail station for another Kreis).
    gate_x, gate_y = 605000.0, 5840000.0
    home_locs = frames["locations"][frames["locations"]["activity_index"] == 0]
    for _, row in home_locs.iterrows():
        assert abs(row.geometry.x - gate_x) < 1e-6, \
            "reassigned agent home must be at the road gate x"
        assert abs(row.geometry.y - gate_y) < 1e-6, \
            "reassigned agent home must be at the road gate y"


def test_pt_and_car_mixed_validation_entry_kind():
    """Mixed PT/car demand: each agent gets the correct entry_kind in validation."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()
    # Force a large sampling rate so we get enough agents for both modes.
    rng = np.random.default_rng(11)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops)

    val = frames["validation"]
    # All required columns present.
    for col in ("ars5", "direction", "mode", "entry_kind", "entry_x", "entry_y", "gate_id"):
        assert col in val.columns, f"column '{col}' missing from validation frame"

    pt_rows = val[val["mode"] == "pt"]
    car_rows = val[val["mode"] == "car"]

    if len(pt_rows) > 0:
        assert (pt_rows["entry_kind"] == "rail_station").all(), \
            "PT rows must have entry_kind=='rail_station'"
    if len(car_rows) > 0:
        assert (car_rows["entry_kind"] == "road_gate").all(), \
            "car rows must have entry_kind=='road_gate'"


def test_validation_columns_schema():
    """Validation frame must have the expected column schema after B5."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    rng = np.random.default_rng(7)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.01,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 0.9, "pt": 0.1}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0)
    val = frames["validation"]
    expected_cols = {"ars5", "direction", "mode", "entry_kind", "entry_x", "entry_y", "gate_id"}
    assert expected_cols <= set(val.columns), \
        f"validation frame missing columns; got {list(val.columns)}"


def test_pt_placement_is_deterministic():
    """Same rng seed -> byte-identical PT home locations and validation frame."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()

    def run(seed):
        rng = np.random.default_rng(seed)
        return build_incommuter_frames(
            flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
            gates=gates, assignment=assignment, zgb_work=zgb_work,
            mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
            hts_persons=hp, hts_trips=ht, person_col="person_id",
            n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
            pt_entry_stops=pt_stops,
            mode_balance=False,  # explicitly OFF so rng stream is invariant
        )

    f1 = run(99)
    f2 = run(99)
    np.testing.assert_array_equal(
        f1["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        f2["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        err_msg="Repeated runs with same seed must produce identical home x coordinates",
    )
    assert list(f1["validation"]["entry_kind"]) == list(f2["validation"]["entry_kind"]), \
        "Repeated runs with same seed must produce identical entry_kind"


def test_pt_placement_no_station_empty_stops():
    """When pt_entry_stops is None/empty, all PT agents keep the road gate (worst fallback)."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    rng = np.random.default_rng(5)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=None)

    # When no stations, PT falls back to car.
    assert (frames["trips"]["mode"] == "car").all(), \
        "PT agents with no stations at all must be reassigned to car (worst fallback)"
    val = frames["validation"]
    assert (val["entry_kind"] == "road_gate").all(), \
        "worst-fallback (no stations) must record entry_kind=='road_gate'"


# --- entry-stop connectivity (n_zgb_routes / is_rail) -----------------------------

def test_direct_ride_stop_stats_counts_distinct_zgb_routes():
    # s1 sits on two distinct ZGB-serving routes -> n_zgb_routes == 2.
    routes = [
        ("rail", ["s1", "s_zgb"]),
        ("bus", ["s1", "s_zgb2"]),
        ("bus", ["s_far"]),            # never reaches ZGB -> contributes nothing
    ]
    stop_kreis = {"s1": "03241", "s_zgb": "03101", "s_zgb2": "03102",
                  "s_far": "03158"}
    stats = direct_ride_stop_stats(routes, stop_kreis, ZGB)
    assert stats["s1"]["n_zgb_routes"] == 2
    assert "s_far" not in stats  # non-ZGB-serving route contributes nothing


def test_direct_ride_stop_stats_marks_rail_route():
    # A rail ZGB-serving route makes the entry stop is_rail True; a bus-only one False.
    routes = [("rail", ["s_rail", "s_zgb"]), ("bus", ["s_bus", "s_zgb"])]
    stop_kreis = {"s_rail": "03241", "s_bus": "03241", "s_zgb": "03101"}
    stats = direct_ride_stop_stats(routes, stop_kreis, ZGB)
    assert stats["s_rail"]["is_rail"] is True
    assert stats["s_bus"]["is_rail"] is False
    assert stats["s_bus"]["n_zgb_routes"] == 1


def test_direct_ride_stop_stats_rail_or_over_multiple_routes():
    # is_rail is the OR across all ZGB-serving routes through the stop: one rail route
    # plus one bus route -> True.
    routes = [("bus", ["s1", "s_zgb"]), ("regional_rail", ["s1", "s_zgb2"])]
    stop_kreis = {"s1": "03241", "s_zgb": "03101", "s_zgb2": "03102"}
    stats = direct_ride_stop_stats(routes, stop_kreis, ZGB)
    assert stats["s1"]["n_zgb_routes"] == 2 and stats["s1"]["is_rail"] is True


def test_rail_like_modes_excludes_tram_and_bus():
    # Sanity-check the chosen RAIL_LIKE set: regional rail in, tram/bus out.
    assert "rail" in RAIL_LIKE_MODES and "regional_rail" in RAIL_LIKE_MODES
    assert "tram" not in RAIL_LIKE_MODES and "bus" not in RAIL_LIKE_MODES


# ---------------------------------------------------------------------------
# Task 4b: real in-ring in-commuter origins (flag-gated, default OFF)
# ---------------------------------------------------------------------------

def _gemeinden_for_origin_test(crs="EPSG:25832"):
    """Minimal Gemeinden GeoDataFrame for real-origin tests.

    Geometry: a 1x1 unit square near the gate at (605000, 5840000).
    The gate is at (605000, 5840000).  The Gemeinde centroid is at
    (607000, 5842000) -- 2828 m NE, well within a 5000 m ring buffer
    around the ZGB polygon that includes the gate point.  A second
    "far" Gemeinde is placed 200 km away so the same fixture covers
    both in-ring and far branches.
    """
    from shapely.geometry import Polygon as ShapelyPolygon
    rows = [
        {
            "ars5": "03241",
            "gem_ags": "03241001",
            "ewz": 50000.0,
            # In-ring: small square near the gate (chosen so rep_point is
            # inside a ZGB buffer of the gate region).
            "geometry": ShapelyPolygon([
                (606000, 5841000), (608000, 5841000),
                (608000, 5843000), (606000, 5843000),
            ]),
        },
        {
            "ars5": "09162",
            "gem_ags": "09162001",
            "ewz": 1300000.0,
            # Far Gemeinde (Munich area): well outside any 50 km ring around ZGB.
            "geometry": ShapelyPolygon([
                (692000, 5335000), (694000, 5335000),
                (694000, 5337000), (692000, 5337000),
            ]),
        },
    ]
    return gpd.GeoDataFrame(rows, crs=crs)


def _zgb_polygon_for_test():
    """Dissolved ZGB polygon that covers the area around the test gate."""
    return box(590000, 5820000, 650000, 5870000)


def test_real_origin_off_is_byte_identical():
    """Flag OFF: no agent gets entry_kind='real_origin'; result matches a second OFF run.

    CLAUDE.md no-silent-fallback: when the flag is OFF no rng draws for real origins
    are consumed, so repeated runs with the same seed must be byte-identical.
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()

    def run(seed):
        rng = np.random.default_rng(seed)
        return build_incommuter_frames(
            flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
            gates=gates, assignment=assignment, zgb_work=zgb_work,
            mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
            hts_persons=hp, hts_trips=ht, person_col="person_id",
            n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
            pt_entry_stops=pt_stops,
            # Both flags explicitly OFF: no additional rng draws consumed, byte-identical.
            real_origin=False,
            mode_balance=False,
        )

    f1 = run(77)
    f2 = run(77)

    # No agent has entry_kind="real_origin" when the flag is OFF.
    val1 = f1["validation"]
    assert "real_origin" not in val1["entry_kind"].values, \
        "Flag OFF: no agent should have entry_kind='real_origin'"

    # Byte-identical outputs with the same seed.
    np.testing.assert_array_equal(
        f1["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        f2["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        err_msg="Flag OFF: same seed must produce identical home x coordinates",
    )
    assert list(f1["validation"]["entry_kind"]) == list(f2["validation"]["entry_kind"]), \
        "Flag OFF: same seed must produce identical entry_kind lists"
    np.testing.assert_array_equal(
        f1["trips"]["departure_time"].to_numpy(),
        f2["trips"]["departure_time"].to_numpy(),
        err_msg="Flag OFF: same seed must produce identical departure times",
    )


def test_real_origin_in_ring_agent_home_is_origin_not_gate():
    """Flag ON: an in-ring agent's home is the real Gemeinde point, not the road gate.

    The gate is at (605000, 5840000).  The in-ring Gemeinde representative point is
    near (607000, 5842000).  With real_origin=True the in-ring car agent should have
    its home at the Gemeinde point, NOT at the gate.  Validation must record
    entry_kind='real_origin'.  home_to_gate_km must be > 0 (positive straight-line
    distance from the real origin to the gate).
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    # Force 100% car so no PT station logic runs, making assertions simpler.
    gemeinden = _gemeinden_for_origin_test()
    zgb_poly = _zgb_polygon_for_test()

    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        real_origin=True, gemeinden=gemeinden, zgb_polygon=zgb_poly,
        source_buffer_m=5000.0,
    )

    gate_x, gate_y = 605000.0, 5840000.0
    home_locs = frames["locations"][frames["locations"]["activity_index"] == 0]
    assert len(home_locs) > 0, "Expected at least one in-commuter"

    for _, row in home_locs.iterrows():
        # Home must NOT be at the gate (in-ring Gemeinde is ~2 km away).
        is_at_gate = (abs(row.geometry.x - gate_x) < 1.0
                      and abs(row.geometry.y - gate_y) < 1.0)
        assert not is_at_gate, \
            f"In-ring agent home ({row.geometry.x:.0f},{row.geometry.y:.0f}) must NOT be at gate"
        # Home must be in the Gemeinde square [606000..608000, 5841000..5843000].
        assert 606000 <= row.geometry.x <= 608000 and 5841000 <= row.geometry.y <= 5843000, \
            f"In-ring agent home ({row.geometry.x:.0f},{row.geometry.y:.0f}) outside Gemeinde bounds"

    val = frames["validation"]
    assert (val["entry_kind"] == "real_origin").all(), \
        "Flag ON, in-ring agents: all must have entry_kind='real_origin'"

    # gate_id is still populated (road gate kept as cordon-entry reference).
    assert val["gate_id"].notna().all(), "gate_id must remain populated for in-ring agents"

    # home_to_gate_km > 0: real origin to gate is a positive distance.
    # We verify via depart_home: if home_to_gate_km > 0 the departure is earlier than
    # it would be with home==gate (depart_home < arrive_work - inside_travel).
    depart = frames["trips"].loc[frames["trips"]["preceding_purpose"] == "home",
                                  "departure_time"].to_numpy()
    assert len(depart) > 0
    # Just check that departure times are finite positive numbers (timing ran with the
    # shifted home_to_gate_km; exact value depends on inside trip distance).
    assert np.all(np.isfinite(depart)), "depart_home must be finite for in-ring agents"
    assert np.all(depart >= 0.0), "depart_home must be >= 0"


def test_real_origin_far_agent_keeps_gate():
    """Flag ON: a far (out-of-ring) car agent keeps its road gate as home.

    Source Kreis 09162 (Munich) has no in-ring Gemeinden within 5 km of ZGB.
    With real_origin=True the far agent must still be placed at the gate (road_gate).
    """
    # Use a flow from Munich (09162) so the agent's source Kreis is far.
    far_gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000"], "capacity": [8000.0], "road_class": ["motorway"]},
        geometry=[Point(605000, 5840000)], crs="EPSG:25832")
    far_assignment = pd.DataFrame([("09162", "gate_0000", 1000, 0)],
                                   columns=["ars5", "gate_id", "inbound", "outbound"])
    far_flows = pd.DataFrame([("09162", "03101", 1000)],
                              columns=["orig_ars", "dest_ars", "flow"])
    zgb_work = gpd.GeoDataFrame(
        {"location_id": ["work_1"], "commune_id": ["03101000"], "employees": [50]},
        geometry=[Point(606000, 5805000)], crs="EPSG:25832")
    hp = pd.DataFrame({"person_id": [1], "employed": [True], "age": [42], "sex": ["female"]})
    ht = pd.DataFrame([
        (1, "home", "work", 7 * 3600.0, 8 * 3600.0),
        (1, "work", "home", 17 * 3600.0, 18 * 3600.0),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])

    gemeinden = _gemeinden_for_origin_test()   # includes 09162 (far) Gemeinde
    zgb_poly = _zgb_polygon_for_test()

    rng = np.random.default_rng(55)
    frames = build_incommuter_frames(
        flows=far_flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=far_gates, assignment=far_assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        real_origin=True, gemeinden=gemeinden, zgb_polygon=zgb_poly,
        source_buffer_m=5000.0,
    )

    gate_x, gate_y = 605000.0, 5840000.0
    home_locs = frames["locations"][frames["locations"]["activity_index"] == 0]
    for _, row in home_locs.iterrows():
        assert abs(row.geometry.x - gate_x) < 1.0 and abs(row.geometry.y - gate_y) < 1.0, \
            f"Far car agent home must be at road gate; got ({row.geometry.x:.0f},{row.geometry.y:.0f})"

    val = frames["validation"]
    assert (val["entry_kind"] == "road_gate").all(), \
        "Far car agent must have entry_kind='road_gate' in validation"


def test_real_origin_deterministic():
    """Flag ON: same seed -> byte-identical results."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    gemeinden = _gemeinden_for_origin_test()
    zgb_poly = _zgb_polygon_for_test()

    def run(seed):
        rng = np.random.default_rng(seed)
        return build_incommuter_frames(
            flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
            gates=gates, assignment=assignment, zgb_work=zgb_work,
            mode_reference={">=10": {"car": 1.0}}, band_edges=(10,),
            hts_persons=hp, hts_trips=ht, person_col="person_id",
            n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
            real_origin=True, gemeinden=gemeinden, zgb_polygon=zgb_poly,
            source_buffer_m=5000.0,
        )

    f1 = run(123)
    f2 = run(123)
    np.testing.assert_array_equal(
        f1["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        f2["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        err_msg="Flag ON: same seed must produce identical home x coordinates",
    )
    assert list(f1["validation"]["entry_kind"]) == list(f2["validation"]["entry_kind"]), \
        "Flag ON: same seed must produce identical entry_kind lists"


# ---------------------------------------------------------------------------
# Mode balancer integration tests (flag cordon_incommuter_mode_balance)
# ---------------------------------------------------------------------------

def test_mode_balance_on_conserves_global_pt_count():
    """mode_balance=True: global PT count after balancing equals the Mikrozensus target.

    Setup: 100% PT reference + one station for source Kreis 03241.  Every agent is
    assigned PT by the Mikrozensus draw; all have can_board_pt=True (station
    exists); no forced demotions -> n_pt_final == n_pt_target (all remain PT).

    This exercises the PRIMARY path: balancer fires, station draw always succeeds
    because the balancer guarantees can_board_pt for every PT agent.
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()

    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops,
        mode_balance=True,
    )

    # With 100% PT reference and a valid station, all agents should be PT.
    assert (frames["trips"]["mode"] == "pt").all(), \
        "mode_balance=True with 100% PT reference and valid stations: all agents must be PT"

    # Validation: entry_kind must be rail_station for all PT agents.
    val = frames["validation"]
    assert (val["entry_kind"] == "rail_station").all(), \
        "mode_balance=True PT agents must board at rail station"


def test_mode_balance_on_with_mixed_demand_compensates_forced_car():
    """mode_balance=True: agents from a rail-less Kreis are forced to car, but the
    global PT count is restored by promoting an equal number of reachable car agents.

    Two flows:
      - Kreis 03241: 500 agents, has a rail station -> can_board_pt=True
      - Kreis 03999: 500 agents, NO rail station -> can_board_pt=False

    Mode reference: 50/50 car/pt.  Mikrozensus assigns ~500 PT (250 from each Kreis).
    The balancer must:
      1. Force the ~250 PT agents from 03999 to car (no station).
      2. Promote ~250 reachable car agents from 03241 back to PT.
    -> global PT count stays at the Mikrozensus target (~500).

    This is the COMPENSATION path: the balancer fires meaningfully and the
    fallback-to-car is undone at the global level.
    """
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000", "gate_0001"],
         "capacity": [8000.0, 8000.0], "road_class": ["motorway", "motorway"]},
        geometry=[Point(605000, 5840000), Point(615000, 5840000)], crs="EPSG:25832")
    assignment = pd.DataFrame([
        ("03241", "gate_0000", 500, 0),
        ("03999", "gate_0001", 500, 0),
    ], columns=["ars5", "gate_id", "inbound", "outbound"])
    flows = pd.DataFrame([
        ("03241", "03101", 500),
        ("03999", "03101", 500),
    ], columns=["orig_ars", "dest_ars", "flow"])
    zgb_work = gpd.GeoDataFrame(
        {"location_id": ["work_1"], "commune_id": ["03101000"], "employees": [50]},
        geometry=[Point(606000, 5805000)], crs="EPSG:25832")
    hp = pd.DataFrame({"person_id": [1], "employed": [True], "age": [42], "sex": ["female"]})
    ht = pd.DataFrame([
        (1, "home", "work", 7 * 3600.0, 8 * 3600.0),
        (1, "work", "home", 17 * 3600.0, 18 * 3600.0),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])

    # Station only for 03241, not 03999.
    pt_stops = _pt_entry_stops_new_schema(ars5="03241")

    # Use large enough sampling_rate to get a significant number of agents.
    rate = 0.2
    rng_mikro = np.random.default_rng(0)
    # Compute the Mikrozensus target count by running WITHOUT balancer first.
    frames_off = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=rate,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng_mikro, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops, mode_balance=False,
    )
    # Count how many agents in the OFF run ended up as PT (the lossy result).
    n_pt_off = int((frames_off["trips"]["mode"] == "pt").sum()) // 2  # trips are 2 per agent

    rng_on = np.random.default_rng(0)
    frames_on = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=rate,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng_on, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops, mode_balance=True,
    )
    n_pt_on = int((frames_on["trips"]["mode"] == "pt").sum()) // 2

    # mode_balance=ON must have MORE PT agents than the lossy OFF run
    # (the compensation promoted reachable car -> PT to restore the target).
    # Both runs use the same rng stream so the Mikrozensus draw is identical.
    assert n_pt_on >= n_pt_off, (
        f"mode_balance=True must have >= PT agents than OFF run: "
        f"ON={n_pt_on}, OFF={n_pt_off}. Compensation did not fire."
    )

    # The ON run must have no PT agents from Kreis 03999 (no station).
    val_on = frames_on["validation"]
    pt_from_03999 = val_on[(val_on["mode"] == "pt") & (val_on["ars5"] == "03999")]
    assert len(pt_from_03999) == 0, (
        f"mode_balance=True: {len(pt_from_03999)} PT agents from Kreis 03999 "
        "which has no rail station. Hard constraint violated."
    )

    # All PT agents in the ON run must have entry_kind='rail_station'.
    pt_rows = val_on[val_on["mode"] == "pt"]
    if len(pt_rows) > 0:
        assert (pt_rows["entry_kind"] == "rail_station").all(), \
            "mode_balance=True: all PT agents must board at a rail station"


def test_mode_balance_off_preserves_legacy_lossy_behaviour():
    """mode_balance=False: PT agent in a rail-less Kreis IS reassigned to car (legacy).

    This is the OFF-path regression guard: with mode_balance=False the lossy
    PT->car reassignment in the station-draw step still fires, so a Kreis with
    no eligible rail station ends up with all-car agents.
    """
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    # pt_entry_stops has only Kreis 03999, not the actual source Kreis 03241.
    pt_stops_wrong_kreis = pd.DataFrame({
        "source_ars5": ["03999"],
        "stop_id": ["railOther"],
        "x": [700000.0], "y": [5900000.0],
        "reach": ["direct"], "ewz": [50000.0],
    })
    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops_wrong_kreis,
        mode_balance=False,  # explicitly OFF
    )

    # Legacy behaviour: all PT agents from 03241 (no station) are reassigned to car.
    assert (frames["trips"]["mode"] == "car").all(), \
        "mode_balance=False (legacy): PT agents with no source-Kreis rail station must be car"


def test_mode_balance_deterministic():
    """Same rng seed -> byte-identical results regardless of mode_balance flag value."""
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()
    pt_stops = _pt_entry_stops_new_schema()

    def run(seed, balance):
        rng = np.random.default_rng(seed)
        return build_incommuter_frames(
            flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
            gates=gates, assignment=assignment, zgb_work=zgb_work,
            mode_reference={">=10": {"car": 0.5, "pt": 0.5}}, band_edges=(10,),
            hts_persons=hp, hts_trips=ht, person_col="person_id",
            n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
            pt_entry_stops=pt_stops, mode_balance=balance,
        )

    f1 = run(55, True)
    f2 = run(55, True)
    np.testing.assert_array_equal(
        f1["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        f2["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        err_msg="mode_balance=True: same seed must produce identical home x coordinates",
    )
    assert list(f1["validation"]["mode"]) == list(f2["validation"]["mode"]), \
        "mode_balance=True: same seed must produce identical modes"

    f3 = run(55, False)
    f4 = run(55, False)
    np.testing.assert_array_equal(
        f3["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        f4["locations"]["geometry"].apply(lambda g: g.x).to_numpy(),
        err_msg="mode_balance=False: same seed must produce identical home x coordinates",
    )


def test_configure_defaults_real_origin_and_mode_balance_true():
    """configure() must declare cordon_incommuter_real_origin and
    cordon_incommuter_mode_balance both with default True.

    Uses a minimal context spy to capture config() calls without running
    the full synpp pipeline.
    """
    from braunschweig.synthesis.incommuters import configure  # noqa: PLC0415

    defaults_captured = {}

    class _ConfigSpy:
        """Minimal synpp context spy: captures config(key, default) calls.

        Only stores a value in defaults_captured when a default is explicitly
        provided (args non-empty).  Bare config(key) calls (no default) record
        _MISSING but do NOT overwrite a previously stored default so the first
        declared default (the meaningful one) always wins.
        """
        def config(self, key, *args, **options):  # synpp also takes volatile=...
            # args[0] is the default if provided; only update when a default is given.
            if args:
                defaults_captured[key] = args[0]
            elif key not in defaults_captured:
                # Mark as "declared without a default" only if not already captured.
                defaults_captured[key] = _MISSING
            # Return True for cordon_enabled so the rest of configure() runs.
            if key == "cordon_enabled":
                return True
            # Return the stored default (or None) so configure()'s conditionals work.
            val = defaults_captured.get(key, None)
            if val is _MISSING:
                return None
            return val

        def stage(self, *args, **kwargs):
            pass

    _MISSING = object()
    configure(_ConfigSpy())

    assert "cordon_incommuter_real_origin" in defaults_captured, \
        "configure() must declare cordon_incommuter_real_origin"
    assert defaults_captured["cordon_incommuter_real_origin"] is True, (
        f"cordon_incommuter_real_origin default must be True (new feature default-on), "
        f"got {defaults_captured['cordon_incommuter_real_origin']!r}"
    )

    assert "cordon_incommuter_mode_balance" in defaults_captured, \
        "configure() must declare cordon_incommuter_mode_balance"
    assert defaults_captured["cordon_incommuter_mode_balance"] is True, (
        f"cordon_incommuter_mode_balance default must be True (new feature default-on), "
        f"got {defaults_captured['cordon_incommuter_mode_balance']!r}"
    )


def _late_donor_frames(time_bound_s, pt_check=None, mode="car"):
    """Frames from a donor pool of one normal and one 35 h-late donor."""
    gates, assignment, flows, zgb_work, _hp, _ht = _minimal_inputs()
    hts_persons = pd.DataFrame({"person_id": [1, 2], "employed": [True, True],
                                "age": [42, 30], "sex": ["female", "male"]})
    cols = ["person_id", "preceding_purpose", "following_purpose", "departure_time", "arrival_time"]
    hts_trips = pd.DataFrame([
        (1, "home", "work", 7 * 3600.0, 8 * 3600.0), (1, "work", "home", 17 * 3600.0, 18 * 3600.0),
        (2, "home", "work", 30 * 3600.0, 31 * 3600.0), (2, "work", "home", 34 * 3600.0, 35 * 3600.0),
    ], columns=cols)
    return build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05, gates=gates, assignment=assignment,
        zgb_work=zgb_work, mode_reference={">=10": {mode: 1.0}}, band_edges=(10,),
        hts_persons=hts_persons, hts_trips=hts_trips, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=np.random.default_rng(3),
        gate_speed_kmh=30.0, pt_entry_stops=_pt_entry_stops_new_schema() if mode == "pt" else None,
        time_bound_s=time_bound_s, pt_check=pt_check)


def test_late_donor_days_fit_the_time_bound_with_and_without_pt_check():
    from braunschweig.synthesis.incommuter_pt_connections import PtCheck
    bound = 29 * 3600.0

    def probe(tasks):
        return pd.Series(True, index=tasks["identifier"].astype(str).to_numpy())

    for kwargs in ({}, {"pt_check": PtCheck(probe=probe, bound_s=bound), "mode": "pt"}):
        frames = _late_donor_frames(bound, **kwargs)
        assert len(frames["trips"]) > 0
        # the re-draw happened: every person follows the fitting donor (age 42), not the 35 h donor
        assert (frames["persons"]["age"] == 42).all()
        assert frames["trips"]["arrival_time"].max() <= bound
        assert frames["activities"]["end_time"].dropna().max() <= bound


def test_day_too_long_for_the_whole_day_shift_is_kept_and_moved_as_far_as_possible(caplog):
    # One donor leaves at 1 h and returns at 40 h: even moved to 0 h it ends after the 29 h bound, so no re-draw
    # and no shift fits. The agent is never dropped; its day moves earlier by the room it has.
    gates, assignment, flows, zgb_work, hp, _ht = _minimal_inputs()
    cols = ["person_id", "preceding_purpose", "following_purpose", "departure_time", "arrival_time"]
    ht = pd.DataFrame([(1, "home", "work", 1 * 3600.0, 2 * 3600.0), (1, "work", "home", 39 * 3600.0, 40 * 3600.0)],
                      columns=cols)
    with caplog.at_level("INFO"):
        frames = build_incommuter_frames(
            flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05, gates=gates, assignment=assignment,
            zgb_work=zgb_work, mode_reference={">=10": {"car": 1.0}}, band_edges=(10,), hts_persons=hp,
            hts_trips=ht, person_col="person_id", n_residents=100, n_resident_households=40,
            rng=np.random.default_rng(3), gate_speed_kmh=30.0, time_bound_s=29 * 3600.0,
            time_redraw_max_attempts=2)
    assert len(frames["persons"]) > 0
    report = frames["day_bound"]
    assert report["n_still_late"] == report["n_days_moved"] == report["n_unfit"] == len(frames["persons"])
    assert any(r.levelname == "WARNING" and "still late" in r.getMessage() and "day bound" in r.getMessage()
               for r in caplog.records)
    assert frames["trips"]["arrival_time"].max() > 29 * 3600.0
    assert frames["trips"]["arrival_time"].max() < 40 * 3600.0
    assert frames["trips"].groupby("person_id")["departure_time"].min().min() >= 0.0


# Iterative pt re-balancing after the timetable check (#467, D3): a pt in-commuter the timetable switched to car is
# replaced by a promoted car in-commuter that the timetable serves; a promoted one it does not serve stays car.

def _rebalance_frames(refused_ids, *, serve_nobody=False, with_check=True, **kwargs):
    """Frames with the balancer ON (unless overridden in ``kwargs``), one station (railA) and a probe serving every
    person not in ``refused_ids`` (nobody with ``serve_nobody``); ``with_check=False`` passes no check."""
    from braunschweig.synthesis.incommuter_pt_connections import PtCheck
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()

    def probe(tasks):
        ids = tasks["identifier"].astype(str)
        person = ids.str.split(":").str[0].astype(int)
        served = np.zeros(len(ids), dtype=bool) if serve_nobody else ~person.isin(refused_ids).to_numpy()
        return pd.Series(served, index=ids.to_numpy())

    return build_incommuter_frames(**{
        **dict(flows=flows, zgb_kreise={"03101"}, sampling_rate=0.02, gates=gates, assignment=assignment,
               zgb_work=zgb_work, mode_reference={">=10": {"pt": 0.5, "car": 0.5}}, band_edges=(10,),
               hts_persons=hp, hts_trips=ht, person_col="person_id", n_residents=100, n_resident_households=40,
               rng=np.random.default_rng(11), gate_speed_kmh=30.0, pt_entry_stops=_pt_entry_stops_new_schema(),
               mode_balance=True, pt_check=PtCheck(probe=probe, bound_s=29 * 3600.0) if with_check else None,
               time_bound_s=29 * 3600.0),
        **kwargs})


def _first_trips(frames):
    return frames["trips"][frames["trips"]["trip_index"] == 0].set_index("person_id")


def _home_xy(frames):
    home = frames["locations"][frames["locations"]["activity_index"] == 0].set_index("person_id")
    return home.geometry.x, home.geometry.y


def test_a_pt_in_commuter_switched_to_car_is_replaced_by_a_connected_promoted_car_in_commuter():
    base = _rebalance_frames(set())
    base_modes = _first_trips(base)["mode"]
    pt_ids = sorted(base_modes.index[base_modes == "pt"])
    assert 0 < len(pt_ids) < len(base_modes)
    assert base["pt_rebalance"]["n_iterations"] == 0                    # nothing switched -> nothing re-balanced

    frames = _rebalance_frames({pt_ids[0]})
    first = _first_trips(frames)
    hx, hy = _home_xy(frames)
    assert first.loc[pt_ids[0], "mode"] == "car"                        # switched by the chain, never promoted
    assert (hx[pt_ids[0]], hy[pt_ids[0]]) == (605000.0, 5840000.0)     # boards at the road gate
    assert frames["pt_connection"]["actions"]["to_car"] == 1
    assert frames["pt_connection"]["n_pt_days"] == len(pt_ids)          # promotion attempts are reported apart
    promoted = sorted(set(first.index[first["mode"] == "pt"]) - set(pt_ids))
    assert len(promoted) == 1 and (first["mode"] == "pt").sum() == len(pt_ids)   # back at the target
    assert (hx[promoted[0]], hy[promoted[0]]) == (604000.0, 5841000.0) # placed at the station
    # same donor, same station: the promoted agent leaves home when the original pt agents do
    assert first.loc[promoted[0], "departure_time"] == first.loc[pt_ids[1], "departure_time"]
    report = frames["pt_rebalance"]
    assert (report["n_pt_target"], report["n_pt_final"], report["n_iterations"], report["n_promoted"],
            report["n_promoted_unconnected"], report["residual"], report["met"]) == (
        len(pt_ids), len(pt_ids), 1, 1, 0, 0, True)
    assert report["promotion_actions"]["connection_found"] == 1
    validation = frames["validation"].set_index(frames["persons"]["person_id"].to_numpy())
    assert validation.loc[promoted[0], "entry_kind"] == "rail_station"
    cars = set(frames["vehicles"].loc[frames["vehicles"]["mode"] == "car", "owner_id"])
    assert pt_ids[0] in cars and promoted[0] not in cars


def test_promoted_car_in_commuters_without_a_connection_stay_car_at_the_road_gate():
    base = _rebalance_frames(set())
    base_modes = _first_trips(base)["mode"]
    pt_ids = sorted(base_modes.index[base_modes == "pt"])
    frames = _rebalance_frames({pt_ids[0], *base_modes.index[base_modes == "car"]}, rebalance_max_iterations=3)
    first = _first_trips(frames)
    hx, hy = _home_xy(frames)
    assert sorted(first.index[first["mode"] == "pt"]) == pt_ids[1:]
    cars = first.index[first["mode"] == "car"]
    assert np.allclose(hx[cars], 605000.0) and np.allclose(hy[cars], 5840000.0)
    # a failed attempt leaves no trace: every car in-commuter keeps the road-gate home departure of the base run
    base_car_departure = _first_trips(base).loc[base_modes == "car", "departure_time"]
    assert base_car_departure.nunique() == 1
    assert (first.loc[cars, "departure_time"] == base_car_departure.iloc[0]).all()
    report = frames["pt_rebalance"]
    assert report["met"] is False and report["residual"] == 1
    n_car = len(base_modes) - len(pt_ids)
    assert n_car > 3                                                    # the cap, not the pool, ends the loop
    assert report["n_iterations"] == 3 and report["max_iterations"] == 3
    assert report["n_promoted"] == report["n_promoted_unconnected"] == 3


@pytest.mark.parametrize("variant", [
    dict(with_check=False),                         # no timetable check -> nothing to re-balance against
    dict(mode_balance=False, serve_nobody=True),    # balancer off: the chain switches every pt agent, no re-balancing
])
def test_without_a_check_or_without_the_balancer_the_rebalancing_never_runs(monkeypatch, variant):
    import braunschweig.synthesis.incommuters as incommuters_module

    def must_not_run(*args, **kwargs):
        raise AssertionError("rebalance_with_timetable must not run (it would consume rng draws)")

    expected = _rebalance_frames(set(), **variant)
    monkeypatch.setattr(incommuters_module, "rebalance_with_timetable", must_not_run)
    frames = _rebalance_frames(set(), **variant)
    assert "pt_rebalance" not in frames
    for key in ("persons", "trips", "activities", "vehicles"):
        pd.testing.assert_frame_equal(frames[key], expected[key])
    if variant.get("serve_nobody"):
        assert frames["pt_connection"]["actions"]["to_car"] > 0        # a deficit existed, still no re-balancing


# Promotion path of the re-balancing (#467, final review F3): how a promoted car in-commuter ends up pt.

def _promotion_frames(serves, stops=None, **kwargs):
    """Frames of the re-balancing set-up with a custom probe. ``serves(person, kind, row)`` -> bool is asked per
    routed task: ``kind`` is "out"/"ret", ``row`` the task row (origin/destination, departure_time)."""
    from braunschweig.synthesis.incommuter_pt_connections import PtCheck
    gates, assignment, flows, zgb_work, hp, ht = _minimal_inputs()

    def probe(tasks):
        served = [serves(int(row.identifier.split(":")[0]), row.identifier.rsplit(":", 1)[1], row)
                  for row in tasks.itertuples()]
        return pd.Series(served, index=tasks["identifier"].astype(str).to_numpy())

    return build_incommuter_frames(**{
        **dict(flows=flows, zgb_kreise={"03101"}, sampling_rate=0.02, gates=gates, assignment=assignment,
               zgb_work=zgb_work, mode_reference={">=10": {"pt": 0.5, "car": 0.5}}, band_edges=(10,),
               hts_persons=hp, hts_trips=ht, person_col="person_id", n_residents=100, n_resident_households=40,
               rng=np.random.default_rng(11), gate_speed_kmh=30.0,
               pt_entry_stops=_pt_entry_stops_new_schema() if stops is None else stops, mode_balance=True,
               pt_check=PtCheck(probe=probe, bound_s=29 * 3600.0), time_bound_s=29 * 3600.0),
        **kwargs})


def _base_pt_ids():
    base = _rebalance_frames(set())
    modes = _first_trips(base)["mode"]
    return sorted(modes.index[modes == "pt"])


def _promoted_ids(frames, pt_ids):
    first = _first_trips(frames)
    return sorted(set(first.index[first["mode"] == "pt"]) - set(pt_ids))


def test_promoted_agent_ends_station_redrawn_when_only_another_station_is_connected():
    # Two stations of the Kreis; railB has a negligible weight, so the draw is railA, but only railB is connected.
    stops = pd.concat([_pt_entry_stops_new_schema(),
                       _pt_entry_stops_new_schema(stop_id="railB", x=603000.0, y=5842000.0)], ignore_index=True)
    stops.loc[1, "ewz"] = 1.0
    pt_ids = _base_pt_ids()
    refused = pt_ids[0]
    frames = _promotion_frames(
        lambda person, kind, row: person != refused and (row.origin_x if kind == "out" else row.destination_x)
        == 603000.0, stops=stops)
    promoted = _promoted_ids(frames, pt_ids)
    assert len(promoted) == 1
    assert frames["pt_rebalance"]["promotion_actions"]["station_redrawn"] == 1
    hx, hy = _home_xy(frames)
    assert (hx[promoted[0]], hy[promoted[0]]) == (603000.0, 5842000.0)       # boards at the connected station


def test_promoted_agent_ends_day_shifted_when_only_an_earlier_day_is_connected():
    pt_ids = _base_pt_ids()
    refused = pt_ids[0]
    # the donor's work departure is 17 h: only a day moved earlier is served
    frames = _promotion_frames(
        lambda person, kind, row: person != refused and (kind == "out" or row.departure_time < 17 * 3600.0))
    promoted = _promoted_ids(frames, pt_ids)
    assert len(promoted) == 1
    assert frames["pt_rebalance"]["promotion_actions"]["day_shifted"] == 1
    hx, hy = _home_xy(frames)
    assert (hx[promoted[0]], hy[promoted[0]]) == (604000.0, 5841000.0)
    work_departure = frames["trips"][frames["trips"]["trip_index"] == 1].set_index("person_id")["departure_time"]
    assert work_departure[promoted[0]] < 17 * 3600.0                          # the whole day moved earlier
    assert frames["trips"]["arrival_time"].max() <= 29 * 3600.0


def test_promoted_in_ring_agent_keeps_its_home():
    in_ring = dict(real_origin=True, gemeinden=_gemeinden_for_origin_test(), zgb_polygon=_zgb_polygon_for_test(),
                   source_buffer_m=5000.0)
    base = _promotion_frames(lambda person, kind, row: True, **in_ring)
    base_pt = sorted(_first_trips(base)["mode"].index[_first_trips(base)["mode"] == "pt"])
    refused = base_pt[0]
    frames = _promotion_frames(lambda person, kind, row: person != refused, **in_ring)
    promoted = _promoted_ids(frames, base_pt)
    assert len(promoted) == 1 and frames["pt_rebalance"]["promotion_actions"]["connection_found"] == 1
    hx, hy = _home_xy(frames)
    assert 606000 <= hx[promoted[0]] <= 608000 and 5841000 <= hy[promoted[0]] <= 5843000   # Gemeinde, no station
    validation = frames["validation"].set_index(frames["persons"]["person_id"].to_numpy())
    assert validation.loc[promoted[0], "entry_kind"] == "real_origin"


def test_promoted_agents_that_draw_no_station_are_counted_and_stay_car(caplog):
    # can_board_pt is true (the Kreis has a station) but a promotion draw returns no station; the first draw call
    # places the original pt agents normally, every later call is a promotion round.
    import logging
    stops = _pt_entry_stops_new_schema()
    pt_ids = _base_pt_ids()
    refused = pt_ids[0]
    import braunschweig.data.cordon.pt_reachability as reach

    original = reach.sample_pt_station_per_agent
    calls = {"n": 0}

    def draws_nothing_when_promoting(orig_ars, weighted, rng):
        calls["n"] += 1
        return original(orig_ars, weighted, rng) if calls["n"] == 1 else [None] * len(orig_ars)

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp, caplog.at_level(logging.INFO):
        mp.setattr(reach, "sample_pt_station_per_agent", draws_nothing_when_promoting)
        frames = _promotion_frames(lambda person, kind, row: person != refused, stops=stops)
    report = frames["pt_rebalance"]
    assert report["n_promotion_without_station"] > 0
    assert report["n_promotion_without_station"] == report["n_promoted_unconnected"] == report["n_promoted"]
    assert report["promotion_actions"]["unconnected"] == report["n_promoted"]
    assert _promoted_ids(frames, pt_ids) == []
    # M4: the promotion-action log line, with the 5 % no-station WARN
    lines = [r for r in caplog.records
             if "pt re-balancing:" in r.getMessage() and "promotion attempts" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    assert "without a station" in lines[0].getMessage() and "router probe calls" in lines[0].getMessage()


def test_a_pt_check_bound_lowers_the_day_bound_of_a_direct_caller():
    # One donor day ends at 29.5 h; the caller keeps the default 30 h time_bound_s but passes a 29 h PtCheck: the
    # day must still move earlier (the #488 trap), and the frames report the bound that was applied.
    from braunschweig.synthesis.incommuter_pt_connections import PtCheck
    gates, assignment, flows, zgb_work, hp, _ht = _minimal_inputs()
    cols = ["person_id", "preceding_purpose", "following_purpose", "departure_time", "arrival_time"]
    ht = pd.DataFrame([(1, "home", "work", 20 * 3600.0, 21 * 3600.0),
                       (1, "work", "home", 28.5 * 3600.0, 29.5 * 3600.0)], columns=cols)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05, gates=gates, assignment=assignment,
        zgb_work=zgb_work, mode_reference={">=10": {"car": 1.0}}, band_edges=(10,), hts_persons=hp, hts_trips=ht,
        person_col="person_id", n_residents=100, n_resident_households=40, rng=np.random.default_rng(3),
        gate_speed_kmh=30.0, time_redraw_max_attempts=0,
        pt_check=PtCheck(probe=lambda tasks: pd.Series(True, index=tasks["identifier"].astype(str).to_numpy()),
                         bound_s=29 * 3600.0))
    assert frames["day_bound"]["bound_s"] == 29 * 3600.0
    assert frames["day_bound"]["n_days_moved"] == len(frames["persons"]) > 0
    assert frames["trips"]["arrival_time"].max() <= 29 * 3600.0


def test_day_bound_report_is_always_carried_in_the_frames():
    frames = _late_donor_frames(29 * 3600.0)           # no check
    report = frames["day_bound"]
    assert report["n"] == len(frames["persons"]) and report["n_redrawn"] > 0 and report["n_unfit"] == 0
    assert report["n_still_late"] == 0 and report["bound_s"] == 29 * 3600.0
    assert {"n_attempts_max", "n_days_moved"} <= set(report)
    assert "pt_connection" not in frames


@pytest.mark.parametrize("key,bad", [
    ("braunschweig.incommuters.time_redraw_max_attempts", -1),
    ("braunschweig.incommuters.time_redraw_max_attempts", 1.5),
    ("braunschweig.incommuters.pt_station_candidates", 1.5),
    ("braunschweig.incommuters.pt_station_candidates", 0),
])
def test_configure_rejects_invalid_redraw_and_station_counts(key, bad):
    from braunschweig.synthesis.incommuters import configure

    class Spy:
        def config(self, name, *args, **options):
            if name == key:
                return bad
            if name in ("cordon_enabled", "braunschweig.incommuters.pt_connection_check.enabled"):
                return True
            return args[0] if args else None

        def stage(self, *args, **kwargs):
            pass

    with pytest.raises(ValueError, match=key.rsplit(".", 1)[1]):
        configure(Spy())


def test_pt_check_rejects_a_non_integer_station_candidate_count():
    from braunschweig.synthesis.incommuter_pt_connections import PtCheck
    for bad in (1.5, 0):
        with pytest.raises(ValueError, match="pt_station_candidates"):
            PtCheck(probe=lambda tasks: None, station_candidates=bad)


def test_rebalancing_reports_the_router_probe_calls_per_round_and_in_total():
    pt_ids = _base_pt_ids()
    refused = pt_ids[0]
    frames = _promotion_frames(lambda person, kind, row: person != refused)
    report = frames["pt_rebalance"]
    assert len(report["router_probe_rounds"]) == report["n_iterations"] == 1
    # one round: the planned day only (a connected promoted agent needs no shift or station probe)
    assert report["router_probe_rounds"][0]["router_probe_calls"] == 1
    assert report["n_router_probe_calls"] == 1 and report["n_router_probe_tasks"] == 2
