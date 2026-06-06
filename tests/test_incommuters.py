"""Tests for in-commuter synthesis helpers (PT direct-ride entry stops + gate draw)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

from braunschweig.synthesis.incommuters import (  # noqa: E402
    _pt_home_coords,
    _sample_workplaces,
    build_pt_entry_stops,
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
    assert list(df.columns) == ["source_ars5", "stop_id", "x", "y"]
    assert set(zip(df["source_ars5"], df["stop_id"])) == {("03241", "s1")}


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


# --- PT-boarding primary/fallback transparency -----------------------------------

def test_pt_home_coords_counts_own_kreis_stop_as_primary():
    # PRIMARY: the PT agent's source Kreis 03241 HAS an entry stop -> it boards there.
    pt_stops = pd.DataFrame([("03241", "sA", 604000.0, 5841000.0)],
                            columns=["source_ars5", "stop_id", "x", "y"])
    orig_ars = np.array(["03241"])
    modes = np.array(["pt"])
    gate_x = np.array([605000.0]); gate_y = np.array([5840000.0])
    hx, hy, counts = _pt_home_coords(orig_ars, modes, gate_x, gate_y, pt_stops)
    assert counts == {"own_kreis_stop": 1, "nearest_anywhere_stop": 0,
                      "road_gate": 0, "pt_agents": 1}
    # the drawn home coord is the own-Kreis stop, not the gate
    assert abs(hx[0] - 604000.0) < 1e-6 and abs(hy[0] - 5841000.0) < 1e-6


def test_pt_home_coords_counts_nearest_anywhere_fallback():
    # FALLBACK: the agent's Kreis 03999 has NO entry stop, but stops exist elsewhere
    # (03241) -> it boards the nearest-anywhere stop and is counted as a fallback.
    pt_stops = pd.DataFrame([("03241", "sA", 604000.0, 5841000.0)],
                            columns=["source_ars5", "stop_id", "x", "y"])
    orig_ars = np.array(["03999"])
    modes = np.array(["pt"])
    gate_x = np.array([605000.0]); gate_y = np.array([5840000.0])
    hx, hy, counts = _pt_home_coords(orig_ars, modes, gate_x, gate_y, pt_stops)
    assert counts == {"own_kreis_stop": 0, "nearest_anywhere_stop": 1,
                      "road_gate": 0, "pt_agents": 1}
    # boards the only existing stop (anywhere), not the road gate
    assert abs(hx[0] - 604000.0) < 1e-6 and abs(hy[0] - 5841000.0) < 1e-6


def test_pt_home_coords_counts_road_gate_worst_fallback_when_no_stops():
    # WORST fallback: no PT entry stops at all -> every PT agent keeps the road gate.
    orig_ars = np.array(["03241"])
    modes = np.array(["pt"])
    gate_x = np.array([605000.0]); gate_y = np.array([5840000.0])
    hx, hy, counts = _pt_home_coords(orig_ars, modes, gate_x, gate_y, None)
    assert counts == {"own_kreis_stop": 0, "nearest_anywhere_stop": 0,
                      "road_gate": 1, "pt_agents": 1}
    # home coord stays the road gate (unchanged)
    assert abs(hx[0] - 605000.0) < 1e-6 and abs(hy[0] - 5840000.0) < 1e-6


def test_pt_home_coords_ignores_car_agents_and_leaves_their_coords_at_gate():
    # Only PT agents are counted/moved; car agents keep the gate coord untouched.
    pt_stops = pd.DataFrame([("03241", "sA", 604000.0, 5841000.0)],
                            columns=["source_ars5", "stop_id", "x", "y"])
    orig_ars = np.array(["03241", "03241"])
    modes = np.array(["car", "pt"])
    gate_x = np.array([605000.0, 605000.0]); gate_y = np.array([5840000.0, 5840000.0])
    hx, hy, counts = _pt_home_coords(orig_ars, modes, gate_x, gate_y, pt_stops)
    assert counts["pt_agents"] == 1 and counts["own_kreis_stop"] == 1
    # car agent (index 0) keeps the gate coord
    assert abs(hx[0] - 605000.0) < 1e-6 and abs(hy[0] - 5840000.0) < 1e-6
    # pt agent (index 1) moved to the stop
    assert abs(hx[1] - 604000.0) < 1e-6 and abs(hy[1] - 5841000.0) < 1e-6
