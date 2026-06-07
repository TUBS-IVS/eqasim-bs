import numpy as np
import pandas as pd
from braunschweig.analysis.population_validation import quality_assessment as QA


def _long():
    return pd.DataFrame({
        "control": ["perfect", "perfect", "off", "off"],
        "family": ["mid_person"] * 4,
        "category": ["a", "a", "a", "a"],
        "geo_id": ["1", "2", "1", "2"],
        "synthetic_count": [100, 100, 130, 70],
        "target_count": [100, 100, 100, 100],
        "delta_pp": [0.0, 0.0, 6.0, -6.0],
        "pct_diff": [0.0, 0.0, 30.0, -30.0],
    })


def test_srmse_zero_for_perfect_control():
    q = QA.assess(_long())
    perfect = q[q["control"] == "perfect"].iloc[0]
    assert abs(perfect["srmse"]) < 1e-9
    assert perfect["grade"] == "very good"


def test_off_control_flagged_needs_improvement():
    q = QA.assess(_long())
    off = q[q["control"] == "off"].iloc[0]
    assert off["srmse"] > 0
    assert off["grade"] == "needs improvement"
    assert 0.0 <= off["coverage_10pp"] <= 1.0


def test_cause_hint_returned_for_known_control():
    hint = QA.cause_hint("driving_license_type", same_sign_bias=True,
                         small_cell_correlation=False)
    assert "BF17" in hint or "14" in hint


def test_assess_empty_returns_empty():
    out = QA.assess(pd.DataFrame())
    assert out.empty


def test_family_scores_rolls_up_per_family():
    quality = pd.DataFrame({
        "control": ["a", "b", "c"],
        "family": ["mid_person", "mid_person", "census"],
        "srmse": [0.10, 0.30, 0.50],
        "mean_abs_delta_pp": [1.0, 3.0, 5.0],
    })
    fam = QA.family_scores(quality).set_index("family")
    # mid_person: two controls -> mean srmse 0.2, mean |delta| 2.0, n=2.
    assert abs(fam.loc["mid_person", "mean_srmse"] - 0.20) < 1e-9
    assert abs(fam.loc["mid_person", "mean_abs_delta_pp"] - 2.0) < 1e-9
    assert fam.loc["mid_person", "n_controls"] == 2
    # census: single control -> values equal that control's, n=1.
    assert abs(fam.loc["census", "mean_srmse"] - 0.50) < 1e-9
    assert abs(fam.loc["census", "mean_abs_delta_pp"] - 5.0) < 1e-9
    assert fam.loc["census", "n_controls"] == 1


def test_unknown_control_small_cells_no_spurious_hint():
    """A control with <3 cells (so the small-cell correlation is NaN) whose name
    is not in CAUSE_HINTS must produce an empty cause_hint -- the NaN correlation
    must not be coerced into a spurious small-cell hint."""
    long = pd.DataFrame({
        "control": ["unknown_ctrl", "unknown_ctrl"],
        "family": ["mid_person", "mid_person"],
        "category": ["a", "a"],
        "geo_id": ["1", "2"],
        "synthetic_count": [110, 95],
        "target_count": [100, 100],
        "delta_pp": [2.0, -1.0],  # mixed sign -> no same-sign bias either
        "pct_diff": [10.0, -5.0],
    })
    q = QA.assess(long)
    assert q.iloc[0]["cause_hint"] == ""


def test_srmse_is_nan_when_mean_target_zero():
    long = pd.DataFrame({
        "control": ["zero_t", "zero_t"],
        "family": ["mid_person", "mid_person"],
        "category": ["a", "a"],
        "geo_id": ["1", "2"],
        "synthetic_count": [3, 5],
        "target_count": [0, 0],
        "delta_pp": [1.0, 2.0],
        "pct_diff": [np.nan, np.nan],
    })
    q = QA.assess(long)
    assert np.isnan(q.iloc[0]["srmse"])


def test_grade_for_boundaries():
    assert QA.grade_for(0.99) == "very good"
    assert QA.grade_for(1.0) == "good"
    assert QA.grade_for(5.0) == "needs improvement"
