"""Tests for external-Kreis -> cordon-gate assignment with inbound volume.

Validates that each external source Kreis is routed through its nearest gate, the
BA-Pendler inbound volume is carried, and the per-gate aggregation answers "how
often was each gate chosen and by which Kreise".
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
    gate_volume_summary,
    inbound_volume_by_kreis,
)

ZGB = {"03101", "03102"}


def _flows():
    # Two external Kreise commute INTO ZGB; one intra-ZGB pair (excluded); one
    # external->external pair (excluded).
    return pd.DataFrame([
        ("03241", "03101", 500),   # external -> ZGB  (inbound)
        ("03241", "03102", 100),   # external -> ZGB  (inbound, same source)
        ("03158", "03101", 300),   # external -> ZGB  (inbound)
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
    # 03241 sits next to gate_0000; 03158 next to gate_0001.
    return gpd.GeoDataFrame(
        {"ars5": ["03241", "03158"]},
        geometry=[box(598000, 5799000, 602000, 5801000),
                  box(648000, 5799000, 652000, 5801000)],
        crs="EPSG:25832",
    )


def test_inbound_volume_by_kreis_filters_and_sums():
    vol = inbound_volume_by_kreis(_flows(), ZGB)
    m = dict(zip(vol["ars5"], vol["inbound"]))
    assert m == {"03241": 600, "03158": 300}   # 500+100 ; 300; others excluded


def test_assignment_routes_each_kreis_to_nearest_gate_with_volume():
    assignment = assign_kreise_to_gates_with_volume(
        _kreise(), _gates(), inbound_volume_by_kreis(_flows(), ZGB))
    by = {r.ars5: (r.gate_id, r.inbound) for _, r in assignment.iterrows()}
    assert by["03241"] == ("gate_0000", 600)
    assert by["03158"] == ("gate_0001", 300)


def test_gate_volume_summary_counts_per_gate():
    assignment = assign_kreise_to_gates_with_volume(
        _kreise(), _gates(), inbound_volume_by_kreis(_flows(), ZGB))
    summary = gate_volume_summary(assignment)
    top = summary.iloc[0]
    assert top["gate_id"] == "gate_0000"
    assert top["n_commuters_inbound"] == 600
    assert top["n_kreise"] == 1
    assert top["source_kreise"] == "03241"


def test_kreis_without_inbound_gets_zero():
    # A Kreis with no inbound flow still assigns to a gate but contributes 0.
    kreise = gpd.GeoDataFrame(
        {"ars5": ["03241", "03999"]},
        geometry=[box(598000, 5799000, 602000, 5801000),
                  box(648000, 5799000, 652000, 5801000)],
        crs="EPSG:25832",
    )
    assignment = assign_kreise_to_gates_with_volume(
        kreise, _gates(), inbound_volume_by_kreis(_flows(), ZGB))
    by = {r.ars5: r.inbound for _, r in assignment.iterrows()}
    assert by["03999"] == 0
    summary = gate_volume_summary(assignment)
    g1 = summary[summary["gate_id"] == "gate_0001"].iloc[0]
    assert g1["n_commuters_inbound"] == 0 and g1["n_kreise"] == 0
