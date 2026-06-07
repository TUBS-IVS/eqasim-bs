import numpy as np
import pandas as pd
from braunschweig.analysis.population_validation import control_validation as CV
from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation.population_source import PopulationFrames


def _frames():
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4],
                            "household_id": [10, 10, 20, 20],
                            "has_driving_license": [True, True, True, False]})
    return PopulationFrames(persons, pd.DataFrame(), None, None, "run_output", "x", "p_")


def _geo():
    return pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"],
                         "commune_id": ["03101000", "03101000"]})


def _target_75_25(data_path):
    return pd.DataFrame({"geo_id": ["03101", "03101"],
                         "category": ["True", "False"],
                         "target_share": [0.75, 0.25]})


def test_evaluate_control_computes_pct_and_delta():
    ctrl = C.categorical_person_control(
        "lic", "mid_person", "kreis", "has_driving_license",
        ("True", "False"), _target_75_25)
    long = CV.evaluate_control(ctrl, _frames(), _geo(), data_path="x")
    true_row = long[long["category"] == "True"].iloc[0]
    assert abs(true_row["synthetic_pct"] - 75.0) < 1e-9
    assert abs(true_row["target_pct"] - 75.0) < 1e-9
    assert abs(true_row["delta_pp"] - 0.0) < 1e-9
    assert abs(true_row["pct_diff"] - 0.0) < 1e-9


def test_summarize_stats_match_hand_values():
    long = pd.DataFrame({
        "control": ["x", "x"], "family": ["f", "f"], "category": ["a", "a"],
        "geo_id": ["1", "2"], "pct_diff": [10.0, -10.0], "delta_pp": [2.0, -2.0],
        "synthetic_count": [5, 5], "target_count": [4, 6],
    })
    summ = CV.summarize(long)
    row = summ.iloc[0]
    assert row["n_cells"] == 2
    assert abs(row["mean_pct_diff"] - 0.0) < 1e-9
    assert abs(row["rmse_pct_diff"] - 10.0) < 1e-9
    assert abs(row["stdev_pct_diff"] - 10.0) < 1e-9
    assert abs(row["max_abs_delta_pp"] - 2.0) < 1e-9


def test_zero_count_target_category_is_evaluated_not_dropped():
    # Synthetic: kreis 03101 has 4 persons, all license True (0 False).
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4],
                            "household_id": [10, 10, 20, 20],
                            "has_driving_license": [True, True, True, True]})
    frames = PopulationFrames(persons, pd.DataFrame(), None, None, "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"],
                        "commune_id": ["03101000", "03101000"]})
    ctrl = C.categorical_person_control("lic", "mid_person", "kreis",
                                        "has_driving_license", ("True", "False"), _target_75_25)
    long = CV.evaluate_control(ctrl, frames, geo, data_path="x")
    # The "False" target category (25%) must appear with synthetic 0 -> delta_pp -25, pct_diff -100.
    false_row = long[long["category"] == "False"]
    assert len(false_row) == 1
    assert false_row["synthetic_count"].iloc[0] == 0
    assert abs(false_row["synthetic_pct"].iloc[0] - 0.0) < 1e-9
    assert abs(false_row["delta_pp"].iloc[0] - (-25.0)) < 1e-9
    assert abs(false_row["pct_diff"].iloc[0] - (-100.0)) < 1e-9


def test_evaluate_control_descriptive_only_returns_empty():
    """A control with target=None (descriptive-only) has realised data but no
    geographic target, so evaluate_control returns an empty frame."""
    ctrl = C.categorical_person_control(
        "descriptive", "census", "kreis", "has_driving_license",
        ("True", "False"), target=None)
    long = CV.evaluate_control(ctrl, _frames(), _geo(), data_path="x")
    assert long.empty


def test_evaluate_all_concatenates_only_targeted_controls():
    """evaluate_all over a registry of one targeted + one descriptive-only
    control concatenates only the targeted control's rows."""
    targeted = C.categorical_person_control(
        "lic", "mid_person", "kreis", "has_driving_license",
        ("True", "False"), _target_75_25)
    descriptive = C.categorical_person_control(
        "descriptive", "census", "kreis", "has_driving_license",
        ("True", "False"), target=None)
    long = CV.evaluate_all([targeted, descriptive], _frames(), _geo(), data_path="x")
    assert not long.empty
    assert set(long["control"].unique()) == {"lic"}
