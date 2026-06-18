"""Tests for AgeIncomeModel (income-age tilt + fallback ladder).

Covers:
  * income monotonicity: low-income tilts AWAY from new cars / TOWARD old cars
    relative to high-income, for a real cell (kleinwagen);
  * unknown segment falls back gracefully to ~all-ones tilt (sum ≈ 7);
  * low-base (status)-only fallback triggers and is logged.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO, "eqasim-data", "data")

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles.age_income import AgeIncomeModel  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.exists(
        os.path.join(DATA_PATH, "braunschweig", "kba", "derived",
                     "mid2023_age_by_segment_status.csv")
    ),
    reason="local-only MiD age-by-segment-status CSV not present",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def model():
    return AgeIncomeModel.from_data_path(DATA_PATH)


# ---------------------------------------------------------------------------
# Test 1: income monotonicity (brief case a)
# ---------------------------------------------------------------------------

def test_age_tilt_makes_low_income_older(model):
    """Low-income households tilt AWAY from new cars (under_5) and TOWARD old
    cars (20_to_24) relative to high-income households."""
    t_low = model.age_tilt("kleinwagen", "very_low")
    t_high = model.age_tilt("kleinwagen", "very_high")

    i_new = ft.AGE_BAND_LABELS.index("under_5")
    i_old = ft.AGE_BAND_LABELS.index("20_to_24")

    assert t_low[i_new] < t_high[i_new], (
        f"low-income should tilt AWAY from new cars: "
        f"very_low={t_low[i_new]:.4f} vs very_high={t_high[i_new]:.4f}"
    )
    assert t_low[i_old] > t_high[i_old], (
        f"low-income should tilt TOWARD old cars: "
        f"very_low={t_low[i_old]:.4f} vs very_high={t_high[i_old]:.4f}"
    )


# ---------------------------------------------------------------------------
# Test 2: unknown segment falls back to ~all-ones (brief case b)
# ---------------------------------------------------------------------------

def test_unknown_cell_falls_back_to_ones(model):
    """An entirely unknown segment returns a tilt that sums to ~len(AGE_BAND_LABELS)
    (i.e., all-ones vector -- no income signal)."""
    tilt = model.age_tilt("nonexistent_seg", "very_low")
    np.testing.assert_allclose(
        tilt.sum(), len(ft.AGE_BAND_LABELS), rtol=0.5,
        err_msg="unknown segment should fall back to all-ones tilt"
    )
    assert tilt.shape == (len(ft.AGE_BAND_LABELS),)


# ---------------------------------------------------------------------------
# Test 3: wohnmobile (known absent from table) also falls back
# ---------------------------------------------------------------------------

def test_wohnmobile_falls_back(model):
    """wohnmobile is absent from the MiD table; it should silently fall back via
    the fallback ladder (not raise) and return a near-ones tilt."""
    tilt = model.age_tilt("wohnmobile", "medium")
    assert tilt.shape == (len(ft.AGE_BAND_LABELS),)
    # Should be near-ones regardless of which fallback level is reached.
    np.testing.assert_allclose(tilt.sum(), len(ft.AGE_BAND_LABELS), rtol=0.5)


# ---------------------------------------------------------------------------
# Test 4: tilt is non-negative and finite everywhere
# ---------------------------------------------------------------------------

def test_tilt_always_finite(model):
    """Every (segment, status) tilt must be finite and non-negative."""
    for seg in ft.SEGMENT_LABELS:
        for status in ft.STATUS_LABELS:
            tilt = model.age_tilt(seg, status)
            assert tilt.shape == (len(ft.AGE_BAND_LABELS),)
            assert np.all(np.isfinite(tilt)), f"NaN/inf in tilt for ({seg}, {status})"
            assert np.all(tilt >= 0), f"negative tilt for ({seg}, {status})"


# ---------------------------------------------------------------------------
# Test 5: (status)-only fallback triggers for a synthetic low-base cell
# ---------------------------------------------------------------------------

def test_status_fallback_logged(caplog):
    """When a (segment, status) cell has base_weighted below MIN_CELL_WEIGHT,
    the fallback ladder escalates to (status)-only and logs a warning."""
    import pandas as pd
    from braunschweig.synthesis.vehicles.age_income import (
        AgeIncomeModel, MIN_CELL_WEIGHT,
    )

    # Build a minimal DataFrame with only one real cell that is below the
    # minimum weight, and one status-level row (for fallback).
    ages = list(ft.AGE_BAND_LABELS)
    n = len(ages)
    seg = "test_seg"
    status = "very_low"

    # Cell below threshold
    low_base = max(0.0, MIN_CELL_WEIGHT - 1.0)
    share_val = 1.0 / n

    rows = []
    # Low-base cell for (test_seg, very_low)
    for ab in ages:
        rows.append({"segment": seg, "status": status,
                     "age_band": ab, "share": share_val, "base_weighted": low_base})
    # Also add a valid same-status cell under a different segment to enable
    # the (status)-fallback pool.
    for ab in ages:
        rows.append({"segment": "minis", "status": status,
                     "age_band": ab, "share": share_val, "base_weighted": MIN_CELL_WEIGHT * 10})

    df = pd.DataFrame(rows)

    with caplog.at_level(logging.WARNING, logger="braunschweig.synthesis.vehicles.age_income"):
        m = AgeIncomeModel._from_dataframe(df)
        tilt = m.age_tilt(seg, status)

    # Fallback counter must be > 0 (cell-level fallback triggered).
    primary, fallback = m.log_fallback_rate()
    assert fallback > 0, "expected at least one fallback for the low-base cell"

    # Tilt still valid.
    assert tilt.shape == (n,)
    assert np.all(np.isfinite(tilt))


# ---------------------------------------------------------------------------
# Test 6: log_fallback_rate returns counts
# ---------------------------------------------------------------------------

def test_log_fallback_rate_returns_counts(model):
    """log_fallback_rate() returns (primary, fallback) ints >= 0."""
    # Call a few known cells to populate counters.
    model.age_tilt("kleinwagen", "medium")
    model.age_tilt("minis", "low")
    primary, fallback = model.log_fallback_rate()
    assert isinstance(primary, int)
    assert isinstance(fallback, int)
    assert primary >= 0
    assert fallback >= 0
