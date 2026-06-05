"""Tests for cordon gate deduplication (one gate per physical crossing).

A road crossing the cordon has two directed links -> two near-identical gate
points; dedupe_gates collapses them to one representative per location.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.gates import dedupe_gates  # noqa: E402


def _gates(rows):
    return gpd.GeoDataFrame(
        {"link_id": [r[0] for r in rows], "capacity": [r[1] for r in rows],
         "road_class": [r[2] for r in rows]},
        geometry=[Point(r[3], r[4]) for r in rows],
        crs="EPSG:25832",
    )


def test_collapses_colocated_links_keeping_max_capacity():
    # Two directed links of the same crossing (3 m apart) + a distinct crossing.
    gates = _gates([
        ("a_fwd", 2000, "motorway", 600000.0, 5800000.0),
        ("a_bwd", 2000, "motorway", 600003.0, 5800002.0),
        ("b", 1500, "primary", 620000.0, 5800000.0),
    ])
    out = dedupe_gates(gates, tolerance_m=100.0)
    assert len(out) == 2
    pts = sorted(round(p.x) for p in out.geometry)
    assert pts == [600000, 620000]


def test_keeps_distinct_crossings():
    gates = _gates([
        ("a", 2000, "motorway", 600000.0, 5800000.0),
        ("b", 2000, "motorway", 600500.0, 5800000.0),  # 500 m away -> distinct
    ])
    assert len(dedupe_gates(gates, tolerance_m=100.0)) == 2


def test_empty_passthrough():
    empty = _gates([]).iloc[0:0]
    assert len(dedupe_gates(empty)) == 0
