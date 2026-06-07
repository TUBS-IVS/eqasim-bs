import pandas as pd
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
