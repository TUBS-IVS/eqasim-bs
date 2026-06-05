"""Tests for cross-cordon in-commuter demand expansion (ported from the branch)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.demand import (  # noqa: E402
    empty_frame_like,
    expand_to_agents,
    make_incommuter_ids,
    select_inbound_flows,
)


def _flows():
    return pd.DataFrame({
        "orig_ars": ["03241", "03101", "09999", "03241"],
        "dest_ars": ["03101", "03102", "03101", "09999"],
        "flow": [1000, 500, 200, 50],
    })


def test_select_inbound_flows():
    # ZGB = {03101, 03102}; ring includes 03241 + 09999 (and ZGB).
    out = select_inbound_flows(_flows(), zgb_kreise={"03101", "03102"},
                               in_ring_kreise={"03241", "09999", "03101", "03102"})
    pairs = set(zip(out["orig_ars"], out["dest_ars"]))
    assert ("03241", "03101") in pairs       # external -> ZGB: in-commute
    assert ("09999", "03101") in pairs       # external -> ZGB: in-commute
    assert ("03101", "03102") not in pairs   # ZGB -> ZGB: resident, excluded
    assert ("03241", "09999") not in pairs   # dest not ZGB: excluded


def test_expand_to_agents_scales_and_rounds():
    flows = pd.DataFrame({"orig_ars": ["A", "B"], "dest_ars": ["Z", "Z"],
                          "flow": [1000, 1]})
    agents = expand_to_agents(flows, sampling_rate=0.25)
    # A: 1000*0.25 = 250 agents; B: 1*0.25 = 0.25 -> rounds to 0 -> dropped
    assert len(agents) == 250
    assert set(agents["orig_ars"]) == {"A"}


def test_make_incommuter_ids_offsets():
    ids = make_incommuter_ids(3, n_residents=100, n_resident_households=80)
    assert list(ids["person_id"]) == [100, 101, 102]
    assert list(ids["household_id"]) == [80, 81, 82]


def test_empty_frame_like_preserves_schema():
    frame = pd.DataFrame({"a": [1], "b": ["x"]})
    out = empty_frame_like(frame)
    assert len(out) == 0
    assert list(out.columns) == ["a", "b"]
