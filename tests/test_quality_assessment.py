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
