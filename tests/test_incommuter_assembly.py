"""Test the in-commuter assembly builder (all frames + PT boarding)."""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.incommuters import (  # noqa: E402
    _agent_times, build_incommuter_frames)
from braunschweig.data.cordon.gate_entry import gate_entry_time_s  # noqa: E402
from braunschweig.data.cordon.plans import extract_commute_times  # noqa: E402


def _inputs():
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


def test_build_incommuter_frames_schema_and_counts():
    gates, assignment, flows, zgb_work, hp, ht = _inputs()
    rng = np.random.default_rng(7)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.01,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 0.9, "pt": 0.1}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0)
    # 1000 inbound * 0.01 = 10 agents -> 10 persons, 20 trips, 30 activities/locations.
    assert len(frames["persons"]) == 10
    assert len(frames["trips"]) == 20
    assert len(frames["activities"]) == 30
    assert len(frames["locations"]) == 30
    assert frames["persons"]["person_id"].min() >= 100
    assert frames["households"]["household_id"].min() >= 40
    # home stays "home" pre-cut (the cutter converts the gate home to "outside"); work
    # is "work". 2 home + 1 work per agent.
    assert (frames["activities"]["purpose"] == "home").sum() == 20
    assert (frames["activities"]["purpose"] == "work").sum() == 10
    # work activities carry a unique in-commuter work facility id
    work_locs = frames["locations"][frames["locations"]["activity_index"] == 1]
    assert work_locs["location_id"].str.startswith("ic_work_").all()
    # per-agent validation record: one row per in-commuter, with gate + direction + mode
    val = frames["validation"]
    assert len(val) == 10
    assert list(val.columns) == ["ars5", "direction", "mode", "gate_id", "gate_x", "gate_y"]
    assert (val["direction"] == "ein").all()
    assert set(val["mode"]) <= {"car", "pt"}
    persons = frames["persons"]
    for col in ["person_id", "household_id", "age", "sex", "employed",
                "car_availability", "bicycle_availability", "has_license",
                "has_pt_subscription", "household_income", "high_income",
                "is_urban_resident", "pt_subscription_type", "hts_id",
                "hts_household_id", "census_person_id", "census_household_id",
                "subpopulation"]:
        assert col in persons.columns, col
    assert (persons["is_urban_resident"] == False).all()
    assert (persons["subpopulation"] == "incommuter").all()


def test_walk_and_bike_are_never_assigned_to_incommuters():
    # Even a walk/bike-dominated reference (e.g. a short gate->work distance) must yield
    # only car/pt for cross-cordon commuters -- walk/bike over the cordon are unrealistic.
    gates, assignment, flows, zgb_work, hp, ht = _inputs()
    rng = np.random.default_rng(3)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.05,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"walk": 0.7, "bike": 0.2, "car": 0.08, "pt": 0.02}},
        band_edges=(10,), hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0)
    assert set(frames["trips"]["mode"]) <= {"car", "pt"}
    assert set(frames["validation"]["mode"]) <= {"car", "pt"}


def test_pt_agents_board_at_pt_entry_stop():
    gates, assignment, flows, zgb_work, hp, ht = _inputs()
    pt_stops = pd.DataFrame([("03241", "stopA", 604000.0, 5841000.0)],
                            columns=["source_ars5", "stop_id", "x", "y"])
    rng = np.random.default_rng(1)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise={"03101"}, sampling_rate=0.005,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"pt": 1.0}}, band_edges=(10,),
        hts_persons=hp, hts_trips=ht, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
        pt_entry_stops=pt_stops)
    assert (frames["trips"]["mode"] == "pt").all()
    # home (outside) location is the PT stop, not the road gate (605000, 5840000)
    home_loc = frames["locations"][frames["locations"]["activity_index"] == 0].iloc[0]
    assert abs(home_loc.geometry.x - 604000.0) < 1e-6
    assert abs(home_loc.geometry.y - 5841000.0) < 1e-6
    # car-only commuters own no vehicle here
    assert len(frames["vehicles"]) == 0


def _agent_times_reference(donors, hts_trips, person_col, dist_km, speed_kmh, detour_factor):
    """Original per-agent implementation, kept as the equivalence oracle."""
    by_person = {pid: sub for pid, sub in hts_trips.groupby(person_col)}
    arrive_work, depart_work, arrive_home = [], [], []
    for _, donor in donors.iterrows():
        _dh, aw, dw, ah = extract_commute_times(by_person[donor[person_col]])
        arrive_work.append(aw); depart_work.append(dw); arrive_home.append(ah)
    arrive_work = np.array(arrive_work, dtype=float)
    depart_work = np.array(depart_work, dtype=float)
    arrive_home = np.array(arrive_home, dtype=float)
    depart_home = np.array([gate_entry_time_s(aw, dkm, speed_kmh, detour_factor)
                            for aw, dkm in zip(arrive_work, dist_km)], dtype=float)
    return arrive_work, depart_work, depart_home, arrive_home


def test_agent_times_memoised_matches_per_agent_recomputation():
    # Three distinct donors with different Home->Work->Home timings; the agent table
    # samples them WITH duplicates (donor 2 used three times, donor 1 twice) so the
    # memoised path must reproduce each agent's times from its donor exactly.
    hts_trips = pd.DataFrame([
        (1, "home", "work", 7.0 * 3600, 7.5 * 3600),
        (1, "work", "home", 16.0 * 3600, 16.5 * 3600),
        (2, "home", "work", 6.0 * 3600, 7.0 * 3600),
        (2, "work", "home", 17.0 * 3600, 18.0 * 3600),
        (3, "home", "work", 8.0 * 3600, 8.25 * 3600),
        (3, "work", "home", 15.0 * 3600, 15.25 * 3600),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])
    # Sampled-with-replacement donor order (duplicates on purpose).
    donor_ids = [2, 1, 2, 3, 1, 2]
    donors = pd.DataFrame({"person_id": donor_ids})
    # One distance per agent (drives the vectorised gate_entry_time_s); include 0.0
    # to exercise the max(0, ...) clamp boundary identically in both paths.
    dist_km = np.array([5.0, 12.3, 0.0, 40.0, 2.5, 100.0])
    speed_kmh, detour_factor = 30.0, 1.3

    # home_to_gate = 0 (the car case: home == gate) -> identical to the original
    # gate->work-only reference, so the memoisation equivalence still holds.
    out = _agent_times(donors, hts_trips, "person_id", dist_km,
                       np.zeros_like(dist_km), speed_kmh, detour_factor)
    ref = _agent_times_reference(donors, hts_trips, "person_id", dist_km, speed_kmh, detour_factor)
    for got, expected in zip(out, ref):
        np.testing.assert_array_equal(got, expected)


def test_agent_times_outside_access_shifts_departure_earlier():
    # A non-zero home->gate access (PT boarding stop away from the gate) must move
    # the home departure earlier by exactly the access travel time, while the work
    # arrival/departure are unchanged. With home_to_gate = 0 it equals the car case.
    hts_trips = pd.DataFrame([
        (1, "home", "work", 6.0 * 3600, 8.0 * 3600),
        (1, "work", "home", 17.0 * 3600, 18.0 * 3600),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])
    donors = pd.DataFrame({"person_id": [1, 1]})
    dist_km = np.array([5.0, 5.0])           # inside gate->work
    home_to_gate = np.array([0.0, 4.0])      # agent 0: car (home==gate); agent 1: PT access
    speed_kmh, detour_factor = 30.0, 1.3

    aw, dw, dh, ah = _agent_times(donors, hts_trips, "person_id", dist_km,
                                  home_to_gate, speed_kmh, detour_factor)
    # Work arrival unchanged by the access change.
    assert np.allclose(aw, 8.0 * 3600)
    # The 4 km access (agent 1) shifts departure earlier by 4*1.3/30*3600 s vs agent 0.
    access_s = 4.0 * detour_factor / speed_kmh * 3600.0
    assert np.isclose(dh[0] - dh[1], access_s)


def test_agent_times_raises_on_non_positive_speed():
    hts_trips = pd.DataFrame([
        (1, "home", "work", 7.0 * 3600, 8.0 * 3600),
        (1, "work", "home", 17.0 * 3600, 18.0 * 3600),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])
    donors = pd.DataFrame({"person_id": [1, 1]})
    try:
        _agent_times(donors, hts_trips, "person_id", np.array([1.0, 2.0]),
                     np.array([0.0, 0.0]), 0.0, 1.3)
        assert False, "expected ValueError for speed_kmh <= 0"
    except ValueError:
        pass
