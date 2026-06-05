"""Tests for the cordon gate network-entry time of injected in-commuters."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.gate_entry import gate_entry_time_s  # noqa: E402


def test_entry_time_is_work_arrival_minus_in_zgb_travel():
    # work arrives 08:00:00 (28800 s); gate->work 12 km at 30 km/h, detour 1.3
    # travel = 12*1.3/30 h = 0.52 h = 1872 s -> entry 26928 s.
    t = gate_entry_time_s(work_arrival_s=28800, gate_to_work_km=12.0,
                          speed_kmh=30.0, detour_factor=1.3)
    assert abs(t - 26928.0) < 1.0


def test_entry_time_never_negative():
    assert gate_entry_time_s(work_arrival_s=600, gate_to_work_km=100.0,
                             speed_kmh=30.0, detour_factor=1.3) == 0.0


def test_zero_distance_equals_arrival():
    assert gate_entry_time_s(work_arrival_s=28800, gate_to_work_km=0.0,
                             speed_kmh=30.0) == 28800.0


def test_rejects_nonpositive_speed():
    with pytest.raises(ValueError):
        gate_entry_time_s(work_arrival_s=28800, gate_to_work_km=10.0, speed_kmh=0.0)
