"""Tests for the cross-cordon config group (generic keys, default OFF)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.config import CordonConfig  # noqa: E402


def test_defaults_are_off_and_sane():
    c = CordonConfig.from_mapping({})
    assert c.enabled is False
    assert c.network_buffer_fraction == 0.10
    # Default major-road allowlist for car gates (Autobahn/Bundesstrasse).
    assert "motorway" in c.gate_road_classes
    assert c.gate_top_n is None


def test_reads_overrides():
    c = CordonConfig.from_mapping({
        "cordon_enabled": True,
        "cordon_network_buffer_fraction": 0.05,
        "cordon_gate_road_classes": ["motorway", "trunk"],
        "cordon_gate_top_n": 12,
        "cordon_gate_min_capacity": 1500.0,
    })
    assert c.enabled is True
    assert c.network_buffer_fraction == 0.05
    assert c.gate_road_classes == ("motorway", "trunk")
    assert c.gate_top_n == 12
    assert c.gate_min_capacity == 1500.0


def test_rejects_negative_buffer_fraction():
    import pytest
    with pytest.raises(ValueError):
        CordonConfig.from_mapping({"cordon_network_buffer_fraction": -0.1})
