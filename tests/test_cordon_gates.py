"""Tests for cordon-gate derivation (network links crossing the cordon boundary)
and external-Kreis -> nearest-gate mapping. Pure geometry, EPSG:25832.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.gates import (  # noqa: E402
    assign_kreise_to_gates,
    derive_road_gates,
)

CORDON = box(0, 0, 1000, 1000)  # simple square cordon


def _links():
    return gpd.GeoDataFrame(
        {
            "link_id": ["crosser", "inside", "outside"],
            "capacity": [2000.0, 500.0, 999.0],
            "geometry": [
                LineString([(500, 500), (500, 1500)]),    # crosses top boundary
                LineString([(200, 200), (300, 300)]),      # fully inside
                LineString([(2000, 2000), (2100, 2100)]),  # fully outside
            ],
        },
        crs="EPSG:25832",
    )


def test_only_crossing_links_become_gates():
    gates = derive_road_gates(_links(), CORDON)
    assert list(gates["link_id"]) == ["crosser"]
    g = gates.geometry.iloc[0]
    assert abs(g.y - 1000.0) < 1e-6 and abs(g.x - 500.0) < 1e-6


def test_gates_carry_capacity_for_ranking():
    gates = derive_road_gates(_links(), CORDON)
    assert gates.iloc[0]["capacity"] == 2000.0


def test_no_crossing_links_returns_empty():
    only_inside = _links().iloc[[1]].reset_index(drop=True)
    gates = derive_road_gates(only_inside, CORDON)
    assert len(gates) == 0


def test_each_kreis_maps_to_nearest_gate():
    gates = gpd.GeoDataFrame(
        {"gate_id": ["g_north", "g_east"]},
        geometry=[Point(500, 1000), Point(1000, 500)], crs="EPSG:25832",
    )
    kreise = gpd.GeoDataFrame(
        {"ars5": ["K_up", "K_right"]},
        geometry=[Point(500, 5000), Point(5000, 500)], crs="EPSG:25832",
    )
    out = assign_kreise_to_gates(kreise, gates)
    m = dict(zip(out["ars5"], out["gate_id"]))
    assert m["K_up"] == "g_north"
    assert m["K_right"] == "g_east"


def test_assign_requires_gate_id():
    gates = gpd.GeoDataFrame({"x": [1]}, geometry=[Point(0, 0)], crs="EPSG:25832")
    kreise = gpd.GeoDataFrame({"ars5": ["A"]}, geometry=[Point(1, 1)], crs="EPSG:25832")
    with pytest.raises(ValueError):
        assign_kreise_to_gates(kreise, gates)
