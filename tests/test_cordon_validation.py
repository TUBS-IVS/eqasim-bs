"""Tests for the cross-cordon commuter validation aggregations (pure).

Compare synthesized in/out-commuters against real targets (BA Pendler OD counts,
Mikrozensus modal split) and aggregate flows per gate, so every run shows how well
reality was hit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.validation import (  # noqa: E402
    counts_by_kreis_direction_mode,
    deviation_vs_target,
    gate_flows,
    modal_split_deviation,
)


def _agents():
    rows = (
        [("03241", "ein", "car", "g1", 100.0, 200.0)] * 3
        + [("03241", "ein", "pt", "g2", 50.0, 80.0)] * 1
        + [("03241", "aus", "car", "g1", 100.0, 200.0)] * 2
    )
    return pd.DataFrame(rows, columns=["ars5", "direction", "mode", "gate_id",
                                       "gate_x", "gate_y"])


def test_counts_by_kreis_direction_mode():
    c = counts_by_kreis_direction_mode(_agents())
    m = {(r.ars5, r.direction, r["mode"]): r.n for _, r in c.iterrows()}
    assert m[("03241", "ein", "car")] == 3
    assert m[("03241", "ein", "pt")] == 1
    assert m[("03241", "aus", "car")] == 2


def test_deviation_vs_target_scales_by_sampling_rate():
    c = counts_by_kreis_direction_mode(_agents())
    target = pd.DataFrame([
        ("03241", "ein", "car", 5),  # synth scaled = 3/0.5 = 6 -> +1 (+20%)
    ], columns=["ars5", "direction", "mode", "n_target"])
    dev = deviation_vs_target(c, target, sampling_rate=0.5)
    row = dev[(dev["ars5"] == "03241") & (dev["direction"] == "ein")
              & (dev["mode"] == "car")].iloc[0]
    assert row["n_scaled"] == 6
    assert row["abs_dev"] == 1
    assert abs(row["pct_dev"] - 20.0) < 1e-6


def test_modal_split_deviation_pp():
    c = counts_by_kreis_direction_mode(_agents())
    # ein: car 3/4 = 75%, pt 1/4 = 25%
    target = pd.DataFrame([
        ("ein", "car", 70.0),
        ("ein", "pt", 30.0),
    ], columns=["direction", "mode", "share_pct_target"])
    dev = modal_split_deviation(c, target)
    d = {(r.direction, r["mode"]): r.pp_dev for _, r in dev.iterrows()}
    assert abs(d[("ein", "car")] - 5.0) < 1e-6   # 75 - 70
    assert abs(d[("ein", "pt")] + 5.0) < 1e-6    # 25 - 30


def test_gate_flows():
    g = gate_flows(_agents())
    m = {(r.gate_id, r.direction, r["mode"]): r.n for _, r in g.iterrows()}
    assert m[("g1", "ein", "car")] == 3
    assert m[("g2", "ein", "pt")] == 1
    assert m[("g1", "aus", "car")] == 2
    # gate coordinates preserved for mapping
    g1 = g[g["gate_id"] == "g1"].iloc[0]
    assert g1["gate_x"] == 100.0 and g1["gate_y"] == 200.0
