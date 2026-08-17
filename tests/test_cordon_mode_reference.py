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
    restrict_to_modes,
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


def test_restrict_to_modes_drops_walk_and_renormalises():
    # A short band where the raw Mikrozensus reference is walk-heavy -- exactly the
    # case that produced unrealistic walking in-commuters before the restriction.
    reference = {
        "<0.5": {"walk": 0.6, "bike": 0.2, "car": 0.15, "pt": 0.05},
        "10-20": {"walk": 0.02, "car": 0.78, "pt": 0.20},
    }
    restricted = restrict_to_modes(reference, allowed=("car", "pt"))
    # No walk/bike survive.
    for band, dist in restricted.items():
        assert set(dist) <= {"car", "pt"}
        assert abs(sum(dist.values()) - 1.0) < 1e-9
    # car:pt ratio within the kept modes is preserved (0.15:0.05 -> 0.75:0.25).
    assert abs(restricted["<0.5"]["car"] - 0.75) < 1e-9
    assert abs(restricted["<0.5"]["pt"] - 0.25) < 1e-9


def test_restrict_to_modes_falls_back_when_no_allowed_mode():
    reference = {"x": {"walk": 0.7, "bike": 0.3}}
    restricted = restrict_to_modes(reference, allowed=("car", "pt"))
    assert restricted["x"] == {"car": 1.0}


def test_restrict_to_modes_logs_warning_for_fallback_band(capsys):
    """A walk/bike-only band has zero car/pt mass -> falls back and is logged as a
    WARNING naming the affected band (CLAUDE.md "no silent fallbacks")."""
    reference = {
        "<0.5": {"walk": 0.7, "bike": 0.3},  # zero mass in (car, pt) -> fallback
        "10-20": {"walk": 0.02, "car": 0.78, "pt": 0.20},  # primary
    }
    restricted = restrict_to_modes(reference, allowed=("car", "pt"))
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "<0.5" in out
    assert "fallback 1" in out
    assert restricted["<0.5"] == {"car": 1.0}


def test_restrict_to_modes_normal_reference_stays_silent(capsys):
    """Every band has nonzero mass in the allowed modes -> no fallback, no log."""
    reference = {
        "<0.5": {"walk": 0.1, "car": 0.7, "pt": 0.2},
        "10-20": {"walk": 0.02, "car": 0.78, "pt": 0.20},
    }
    restrict_to_modes(reference, allowed=("car", "pt"))
    out = capsys.readouterr().out
    assert out == ""


def test_route_distance_band_applies_detour():
    # 8 km straight-line * 1.3 = 10.4 km -> band uses routed distance
    band = route_distance_band(8.0, detour_factor=1.3, edges=MID_DISTANCE_EDGES)
    assert band == "10-20"
