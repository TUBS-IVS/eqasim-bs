"""Tests for the cross-cordon commuter validation aggregations (pure).

Compare synthesized in/out-commuters against real targets (BA Pendler OD counts,
Mikrozensus modal split) and aggregate flows per entry point, so every run shows how
well reality was hit and WHERE the boundary traffic enters (PT at rail stations,
car at road gates).
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
    """Synthetic agents frame with the B5 schema (entry_x/entry_y/entry_kind).

    Mix: 3 car agents at road_gate (100, 200), 1 pt agent at rail_station (50, 80),
    2 outbound car agents at road_gate (100, 200).  All from Kreis 03241.
    gate_id is carried for traceability but the grouping key is the entry point.
    """
    rows = (
        [("03241", "ein", "car", "road_gate", 100.0, 200.0, "g1")] * 3
        + [("03241", "ein", "pt",  "rail_station", 50.0, 80.0, "g2")] * 1
        + [("03241", "aus", "car", "road_gate", 100.0, 200.0, "g1")] * 2
    )
    return pd.DataFrame(rows, columns=["ars5", "direction", "mode", "entry_kind",
                                       "entry_x", "entry_y", "gate_id"])


def test_counts_by_kreis_direction_mode():
    """counts_by_kreis_direction_mode groups on [ars5, direction, mode] — unchanged."""
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


def test_gate_flows_groups_by_entry_point():
    """gate_flows groups by ACTUAL entry point (entry_x/entry_y/entry_kind/direction/mode).

    The PT row must appear at the rail station coords (50, 80), NOT the road gate.
    Car rows appear at their road gate coords (100, 200).
    """
    g = gate_flows(_agents())
    # Required output columns.
    assert {"entry_x", "entry_y", "entry_kind", "direction", "mode", "n"}.issubset(g.columns), \
        f"gate_flows missing expected columns; got {list(g.columns)}"

    # Index by (entry_x, entry_y, entry_kind, direction, mode) for assertions.
    idx = {
        (row["entry_x"], row["entry_y"], row["entry_kind"], row["direction"], row["mode"]): row["n"]
        for _, row in g.iterrows()
    }
    # Car in-commuters: 3 agents at road_gate (100, 200).
    assert idx[(100.0, 200.0, "road_gate", "ein", "car")] == 3
    # PT in-commuter: 1 agent at rail_station (50, 80) — NOT at the road gate.
    assert idx[(50.0, 80.0, "rail_station", "ein", "pt")] == 1
    # Outbound car: 2 agents at road_gate (100, 200).
    assert idx[(100.0, 200.0, "road_gate", "aus", "car")] == 2

    # The PT entry point must NOT appear under road_gate coords.
    pt_at_road_gate = g[(g["mode"] == "pt") & (g["entry_kind"] == "road_gate")]
    assert len(pt_at_road_gate) == 0, "PT agents must not appear at road_gate entry points"


def test_gate_flows_entry_kind_column_present():
    """entry_kind distinguishes road_gate (car) from rail_station (pt)."""
    g = gate_flows(_agents())
    assert "entry_kind" in g.columns
    kinds = set(g["entry_kind"].unique())
    assert kinds == {"road_gate", "rail_station"}, \
        f"unexpected entry_kind values: {kinds}"


def test_gate_flows_gate_id_carried_for_traceability():
    """gate_id is retained in the output for traceability even though it is not the grouping key."""
    g = gate_flows(_agents())
    assert "gate_id" in g.columns
