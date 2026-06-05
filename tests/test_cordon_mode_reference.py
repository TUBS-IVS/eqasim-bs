"""Tests for the ported cordon mode/distance reference helpers (pure)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.mode_reference import (  # noqa: E402
    MID_DISTANCE_EDGES,
    commute_distance_band,
    mode_reference_from_table,
    rake_to_margins,
    route_distance_band,
)


def test_mode_reference_renormalises_per_band():
    tidy = pd.DataFrame({
        "distance_band": ["a", "a", "b", "b"],
        "mode": ["car", "pt", "car", "pt"],
        "probability": [80.0, 20.0, 50.0, 50.0],  # percentages
    })
    ref = mode_reference_from_table(tidy)
    assert abs(ref["a"]["car"] - 0.8) < 1e-9
    assert abs(sum(ref["a"].values()) - 1.0) < 1e-9
    assert abs(ref["b"]["pt"] - 0.5) < 1e-9


def test_mode_reference_rejects_negative_and_zero_band():
    with pytest.raises(ValueError):
        mode_reference_from_table(pd.DataFrame(
            {"distance_band": ["a"], "mode": ["car"], "probability": [-1.0]}))
    with pytest.raises(ValueError):
        mode_reference_from_table(pd.DataFrame(
            {"distance_band": ["a"], "mode": ["car"], "probability": [0.0]}))


def test_rake_to_margins_fits_row_and_col_margins():
    seed = np.ones((2, 2))
    fitted = rake_to_margins(seed, row_margin=[0.7, 0.3], col_margin=[0.6, 0.4])
    assert np.allclose(fitted.sum(axis=1), [0.7, 0.3], atol=1e-6)
    assert np.allclose(fitted.sum(axis=0), [0.6, 0.4], atol=1e-6)


def test_commute_distance_band_exclusive_upper():
    assert commute_distance_band(0.4, edges=(0.5, 1, 2)) == "<0.5"
    assert commute_distance_band(1.5, edges=(0.5, 1, 2)) == "1-2"
    assert commute_distance_band(2.0, edges=(0.5, 1, 2)) == ">=2"


def test_route_distance_band_applies_detour():
    # 8 km straight-line * 1.3 = 10.4 km -> band uses routed distance
    band = route_distance_band(8.0, detour_factor=1.3, edges=MID_DISTANCE_EDGES)
    assert band == "10-20"
