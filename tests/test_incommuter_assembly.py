"""Test the in-commuter assembly builder (all frames + PT boarding)."""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.incommuters import build_incommuter_frames  # noqa: E402


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
