import numpy as np
import pandas as pd
import pytest
from braunschweig.analysis.population_validation import validation_chart as VC


def _summary():
    return pd.DataFrame({
        "control": ["cars_per_hh", "cars_per_hh", "driving_license_type"],
        "family": ["mid_household", "mid_household", "mid_person"],
        "category": ["0", "1", "ja"],
        "n_cells": [8, 8, 8],
        "mean_pct_diff": [2.0, -3.0, 1.0],
        "stdev_pct_diff": [4.0, 5.0, 2.0],
        "rmse_pct_diff": [4.5, 6.0, 2.2],
        "mean_delta_pp": [0.5, -1.0, 0.3],
        "max_abs_delta_pp": [3.0, 4.0, 1.0],
    })


def test_dot_and_whisker_writes_png(tmp_path):
    out = tmp_path / "chart.png"
    path = VC.dot_and_whisker(_summary(), out, whisker="stdev")
    assert path.exists() and path.stat().st_size > 0


def test_dot_and_whisker_rmse_variant_writes_png(tmp_path):
    out = tmp_path / "chart_rmse.png"
    path = VC.dot_and_whisker(_summary(), out, whisker="rmse")
    assert path.exists() and path.stat().st_size > 0


def test_dot_and_whisker_empty_summary_writes_placeholder(tmp_path):
    out = tmp_path / "empty.png"
    path = VC.dot_and_whisker(pd.DataFrame(), out, whisker="stdev")
    assert path.exists() and path.stat().st_size > 0


def test_quality_plot_writes_png(tmp_path):
    q = pd.DataFrame({"control": ["a", "b"], "family": ["mid_person", "mid_household"],
                      "mean_abs_delta_pp": [0.5, 6.0], "grade": ["very good", "needs improvement"],
                      "srmse": [0.01, 0.2]})
    out = tmp_path / "quality.png"
    path = VC.quality_plot(q, out)
    assert path.exists() and path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Tests for the _robust_xlim helper
# ---------------------------------------------------------------------------

def test_robust_xlim_normal_rows():
    """With all rows having small whiskers, cap is driven by the data, not a
    spurious floor."""
    means = np.array([2.0, -3.0, 1.0])
    half = np.array([4.0, 5.0, 2.0])
    cap = VC._robust_xlim(means, half)
    # Endpoints: hi = [6, 2, 3], lo = [-2, -8, -1] -> abs = [6,2,3,2,8,1]
    # 90th pct of [1,2,2,3,6,8] ~ 7.2 -> cap = max(7.2, 5) * 1.10 = ~7.9
    assert cap >= 5.0
    assert cap < 20.0  # stays reasonable


def test_robust_xlim_caps_explosive_outlier():
    """One row with stdev=300 must NOT drive the cap to ~300; bulk rows (stdev~3)
    should dominate and the explosive row should be far above the resulting cap.

    With 4 rows, |endpoints| contains 8 values: [3,3,3,3,3,3,300,300].  The
    p75 of those 8 values is 3.0 (the top-25 % are the two 300s), so the
    explosive row does NOT set the cap.
    """
    means = np.array([0.0, 0.0, 0.0, 0.0])
    half = np.array([3.0, 3.0, 3.0, 300.0])
    cap = VC._robust_xlim(means, half)
    # p75([3,3,3,3,3,3,300,300]) = 3.0 -> cap = max(3.0, 5.0)*1.10 = 5.5
    assert cap < 100.0, f"cap={cap} is too large; explosive outlier dominated the scale"
    assert cap >= 5.0, f"cap={cap} dropped below the floor"


def test_robust_xlim_floor_enforced():
    """When all endpoints are tiny, the floor of 5.0 * 1.10 is applied."""
    means = np.array([0.1, -0.1])
    half = np.array([0.2, 0.2])
    cap = VC._robust_xlim(means, half)
    assert cap >= 5.0 * 1.10 - 1e-9


def test_robust_xlim_all_nan():
    """All-NaN/inf input falls back to 10.0 without raising."""
    means = np.array([np.nan, np.inf])
    half = np.array([np.nan, np.inf])
    cap = VC._robust_xlim(means, half)
    assert cap == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Integration test: extreme whisker does not raise and file is written
# ---------------------------------------------------------------------------

def test_dot_and_whisker_caps_extreme_whisker(tmp_path):
    """A summary where one row has stdev=300 and others stdev~3 must produce a
    valid PNG without raising.  The explosive row must not set the axis scale."""
    summary = pd.DataFrame({
        "control": ["household_size", "cars_per_hh", "cars_per_hh", "driving_license_type"],
        "family": ["census", "mid_household", "mid_household", "mid_person"],
        "category": ["1", "0", "1", "ja"],
        "n_cells": [8, 8, 8, 8],
        "mean_pct_diff": [0.0, 2.0, -3.0, 1.0],
        "stdev_pct_diff": [300.0, 3.0, 4.0, 2.0],  # first row is explosive
        "rmse_pct_diff": [310.0, 3.5, 4.5, 2.2],
        "mean_delta_pp": [0.0, 0.5, -1.0, 0.3],
        "max_abs_delta_pp": [50.0, 3.0, 4.0, 1.0],
    })
    out = tmp_path / "chart_extreme.png"
    path = VC.dot_and_whisker(summary, out, whisker="stdev")
    assert path.exists(), "PNG was not written"
    assert path.stat().st_size > 0, "PNG is empty"
