"""Tests for in-commuter synthesis helpers (PT direct-ride entry stops + gate draw)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.incommuters import direct_ride_stops  # noqa: E402
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


def test_sample_gate_per_agent_none_for_unknown_kreis():
    assignment = pd.DataFrame([("03241", "gate_A", 900)],
                             columns=["ars5", "gate_id", "inbound"])
    rng = np.random.default_rng(0)
    assert sample_gate_per_agent(["09999"], assignment, rng) == [None]
