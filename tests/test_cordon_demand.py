"""Tests for cross-cordon in-commuter demand expansion (ported from the branch)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
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


def _expand_to_agents_reference(flows, sampling_rate):
    """Original elementwise implementation, kept here as the equivalence oracle."""
    rows = []
    for _, r in flows.iterrows():
        n = int(round(float(r["flow"]) * sampling_rate))
        rows.extend([(r["orig_ars"], r["dest_ars"])] * n)
    return pd.DataFrame(rows, columns=["orig_ars", "dest_ars"])


def test_expand_to_agents_matches_reference_including_rounding():
    # Flows chosen to exercise: a whole count, a fractional flow that rounds DOWN,
    # a fractional flow that rounds UP, and the two exact half cases (banker's
    # rounding: 2.5 -> 2, 3.5 -> 4) that round() and np.round() must treat alike.
    flows = pd.DataFrame({
        "orig_ars": ["A", "B", "C", "D", "E", "F"],
        "dest_ars": ["Z", "Z", "Z", "Z", "Z", "Z"],
        # *0.5 -> 1000.0, 0.2 (down to 0, dropped), 0.7 (up to 1), 2.5 -> 2, 3.5 -> 4, 5 -> 5
        "flow": [2000, 1, 1, 5, 7, 10],
    })
    sampling_rate = 0.5
    out = expand_to_agents(flows, sampling_rate)
    ref = _expand_to_agents_reference(flows, sampling_rate)
    pd.testing.assert_frame_equal(out, ref, check_dtype=False)
    # Spell out the expected per-flow counts so the banker's-rounding contract is explicit.
    counts = out["orig_ars"].value_counts().to_dict()
    assert counts.get("A") == 1000      # 2000 * 0.5 = 1000.0
    assert "B" not in counts            # 1 * 0.5 = 0.5 -> banker's -> 0 -> dropped
    assert "C" not in counts            # 1 * 0.5 = 0.5 -> 0 -> dropped
    assert counts.get("D") == 2         # 5 * 0.5 = 2.5 -> banker's -> 2
    assert counts.get("E") == 4         # 7 * 0.5 = 3.5 -> banker's -> 4
    assert counts.get("F") == 5         # 10 * 0.5 = 5.0


def test_expand_to_agents_preserves_row_order():
    # The vectorised np.repeat must keep the per-flow row order of the input frame.
    flows = pd.DataFrame({"orig_ars": ["X", "Y", "X"], "dest_ars": ["Z", "Z", "W"],
                          "flow": [4, 2, 2]})
    out = expand_to_agents(flows, sampling_rate=0.5)
    ref = _expand_to_agents_reference(flows, sampling_rate=0.5)
    pd.testing.assert_frame_equal(out, ref, check_dtype=False)


def test_make_incommuter_ids_offsets():
    ids = make_incommuter_ids(3, n_residents=100, n_resident_households=80)
    assert list(ids["person_id"]) == [100, 101, 102]
    assert list(ids["household_id"]) == [80, 81, 82]


def test_empty_frame_like_preserves_schema():
    frame = pd.DataFrame({"a": [1], "b": ["x"]})
    out = empty_frame_like(frame)
    assert len(out) == 0
    assert list(out.columns) == ["a", "b"]
