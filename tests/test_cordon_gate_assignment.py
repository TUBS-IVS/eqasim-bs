"""Tests for external-Kreis -> cordon-gate assignment with directional volume.

Validates that each external Kreis is routed through its nearest gate, the BA-Pendler
inbound (Einfahren) AND outbound (Ausfahren) volumes are carried, and the per-gate
aggregation answers "how often was each gate chosen, in/out, and by which Kreise".
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box, Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.gate_assignment import (  # noqa: E402
    assign_kreise_to_gates_with_volume,
    commuter_volume_by_kreis,
    gate_volume_summary,
    inbound_volume_by_kreis,
)

ZGB = {"03101", "03102"}


def _flows():
    return pd.DataFrame([
        ("03241", "03101", 500),   # external -> ZGB  (inbound from 03241)
        ("03241", "03102", 100),   # external -> ZGB  (inbound from 03241)
        ("03158", "03101", 300),   # external -> ZGB  (inbound from 03158)
        ("03101", "03241", 200),   # ZGB -> external  (outbound to 03241)
        ("03102", "03158", 80),    # ZGB -> external  (outbound to 03158)
        ("03101", "03102", 999),   # intra-ZGB        (excluded)
        ("03241", "03999", 999),   # external -> external (excluded)
    ], columns=["orig_ars", "dest_ars", "flow"])


def _gates():
    return gpd.GeoDataFrame(
        {"gate_id": ["gate_0000", "gate_0001"]},
        geometry=[Point(600000, 5800000), Point(650000, 5800000)],
        crs="EPSG:25832",
    )


def _kreise():
    return gpd.GeoDataFrame(
        {"ars5": ["03241", "03158"]},
        geometry=[box(598000, 5799000, 602000, 5801000),
                  box(648000, 5799000, 652000, 5801000)],
        crs="EPSG:25832",
    )


def test_commuter_volume_both_directions():
    vol = commuter_volume_by_kreis(_flows(), ZGB)
    by = {r.ars5: (r.inbound, r.outbound) for _, r in vol.iterrows()}
    assert by["03241"] == (600, 200)   # in 500+100, out 200
    assert by["03158"] == (300, 80)


def test_inbound_only_convenience():
    vol = inbound_volume_by_kreis(_flows(), ZGB)
    assert dict(zip(vol["ars5"], vol["inbound"])) == {"03241": 600, "03158": 300}


def test_assignment_carries_both_directions_to_nearest_gate():
    assignment = assign_kreise_to_gates_with_volume(
        _kreise(), _gates(), commuter_volume_by_kreis(_flows(), ZGB))
    by = {r.ars5: (r.gate_id, r.inbound, r.outbound) for _, r in assignment.iterrows()}
    assert by["03241"] == ("gate_0000", 600, 200)
    assert by["03158"] == ("gate_0001", 300, 80)


def test_gate_volume_summary_per_direction():
    assignment = assign_kreise_to_gates_with_volume(
        _kreise(), _gates(), commuter_volume_by_kreis(_flows(), ZGB))
    summary = gate_volume_summary(assignment)
    top = summary.iloc[0]
    assert top["gate_id"] == "gate_0000"          # 600+200 = 800 total, the busiest
    assert top["inbound"] == 600 and top["outbound"] == 200
    assert top["n_kreise"] == 1 and top["source_kreise"] == "03241"
