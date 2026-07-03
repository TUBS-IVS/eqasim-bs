import logging

import numpy as np
import pandas as pd
import pytest
from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation.population_source import PopulationFrames


def _frames():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "household_id": [10, 10, 20, 20],
        "age": [40, 41, 30, 5],
        "sex": ["male", "female", "female", "male"],
        "has_driving_license": [True, True, False, False],
    })
    households = pd.DataFrame({
        "household_id": [10, 20],
        "household_size": [2, 2],
        "number_of_cars": [1, 0],
    })
    return PopulationFrames(persons, households, None, None, "run_output", "x", "p_")


def _geo():
    return pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"],
                         "commune_id": ["03101000", "03101000"]})


def test_categorical_person_extractor_counts_by_category():
    frames, geo = _frames(), _geo()
    ctrl = C.categorical_person_control(
        name="driving_license", family="mid_person", geography="kreis",
        column="has_driving_license",
        categories=("True", "False"), target=None,
    )
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    assert got == {"True": 2, "False": 2}


def test_bucket_household_extractor_clips_top_bucket():
    frames, geo = _frames(), _geo()
    ctrl = C.bucket_household_control(
        name="cars_per_hh", family="mid_household", geography="kreis",
        column="number_of_cars", top=3, target=None,
    )
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    assert got["0"] == 1 and got["1"] == 1


def test_bucket_household_top_label_maps_overflow_to_label():
    """With top=6 and top_label='6+', a value of 7 (>= top) and a value of 6
    (== top) both map to '6+', while a value of 3 (< top) maps to '3'."""
    households = pd.DataFrame({
        "household_id": [10, 20, 30],
        "household_size": [7, 3, 6],
    })
    persons = pd.DataFrame({"person_id": [1], "household_id": [10]})
    frames = PopulationFrames(persons, households, None, None, "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20, 30],
                        "ars5": ["03101", "03101", "03101"],
                        "commune_id": ["03101000", "03101000", "03101000"]})
    ctrl = C.bucket_household_control(
        name="household_size", family="census", geography="kreis",
        column="household_size", top=6, target=None, top_label="6+")
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # 7 and 6 both fall in the "6+" bucket; 3 stays "3".
    assert got["6+"] == 2
    assert got["3"] == 1
    assert "6" not in got  # the bare "6" label is never produced


def test_registry_is_nonempty_and_well_formed():
    reg = C.build_registry(data_path="eqasim-data/data")
    assert len(reg) > 0
    for ctrl in reg:
        assert ctrl.family in {"census", "mid_person", "mid_household", "distribution"}
        assert ctrl.geography in {"kreis", "gemeinde"}
        assert callable(ctrl.realized)


def test_absent_column_categorical_person_logs_warning_and_returns_empty(caplog):
    frames, geo = _frames(), _geo()
    ctrl = C.categorical_person_control(
        "missing_attr", "mid_person", "kreis",
        column="not_a_column", categories=("a",), target=None,
    )
    with caplog.at_level(logging.WARNING):
        out = ctrl.realized(frames, geo)
    assert list(out.columns) == ["geo_id", "category", "synthetic_count"]
    assert out.empty
    assert any("missing_attr" in r.message for r in caplog.records)


def test_absent_column_bucket_household_logs_warning_and_returns_empty(caplog):
    frames, geo = _frames(), _geo()
    ctrl = C.bucket_household_control(
        "missing_hh_attr", "mid_household", "kreis",
        column="not_a_column", top=3, target=None,
    )
    with caplog.at_level(logging.WARNING):
        out = ctrl.realized(frames, geo)
    assert list(out.columns) == ["geo_id", "category", "synthetic_count"]
    assert out.empty
    assert any("missing_hh_attr" in r.message for r in caplog.records)


# --- issue #97: household_size control must be on a PERSON basis ---------------
#
# The census target (Zensus 1000A-2081, loaded by household_size_target) reports
# PERSONS living in a household of each size class, and the IPF balances that same
# person margin. The synthetic side must therefore also be person-weighted; the
# previous household-count basis compared household-shares against person-shares
# (the #97 basis mismatch: ~24pp spurious 1-person deviation).


def test_bucket_household_control_weight_column_person_weights():
    """With ``weight_column`` set, bucket_household_control reports the sum of that
    column per size bin (persons), not the household count. A 7-person household
    contributes 7 persons to the '6+' bin. Default (weight_column=None) still
    counts households, keeping cars_per_hh / bicycles_per_hh byte-identical."""
    households = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "household_size": [1, 1, 3, 7],
    })
    persons = pd.DataFrame({"person_id": [1], "household_id": [1]})
    frames = PopulationFrames(persons, households, None, None, "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [1, 2, 3, 4],
                        "ars5": ["03101"] * 4,
                        "commune_id": ["03101000"] * 4})
    ctrl = C.bucket_household_control(
        name="household_size", family="census", geography="gemeinde",
        column="household_size", top=6, target=None, top_label="6+",
        weight_column="household_size")
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # persons per size class -- NOT household counts (which would be 2/1/1):
    assert got["1"] == 2      # two 1-person households -> 2 persons
    assert got["3"] == 3      # one 3-person household  -> 3 persons
    assert got["6+"] == 7     # one 7-person household  -> 7 persons in the 6+ class
    assert sum(got.values()) == 12   # 1+1+3+7 persons, not 4 households


def test_registry_household_size_control_is_person_weighted():
    """The REGISTERED household_size control must be person-weighted so its
    synthetic distribution shares the person basis of its Zensus 1000A-2081 target
    (persons) and of the IPF person margin."""
    households = pd.DataFrame({
        "household_id": [1, 2, 3],
        "household_size": [1, 2, 4],
    })
    persons = pd.DataFrame({"person_id": [1], "household_id": [1]})
    frames = PopulationFrames(persons, households, None, None, "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [1, 2, 3],
                        "ars5": ["03101"] * 3,
                        "commune_id": ["03101000"] * 3})
    reg = {c.name: c for c in C.build_registry("eqasim-data/data")}
    long = reg["household_size"].realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # person basis: 1 + 2 + 4 = 7 persons (household-count basis would total 3):
    assert got["1"] == 1
    assert got["2"] == 2
    assert got["4"] == 4
    assert sum(got.values()) == 7


def test_household_size_evaluate_control_uses_person_basis():
    """End-to-end: evaluate_control compares the synthetic side on a PERSON basis
    against the person-based Zensus target, so delta_pp is ~0 when the synthetic
    person-shares match. On the old household-count basis this same population
    would report a large spurious delta (66.7% vs 50%)."""
    from braunschweig.analysis.population_validation import control_validation as CV
    # 3 households, sizes 1,1,2 -> persons: 2 in the '1' class, 2 in the '2' class.
    households = pd.DataFrame({
        "household_id": [1, 2, 3],
        "household_size": [1, 1, 2],
    })
    persons = pd.DataFrame({"person_id": [1], "household_id": [1]})
    frames = PopulationFrames(persons, households, None, None, "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [1, 2, 3],
                        "ars5": ["03101"] * 3,
                        "commune_id": ["03101000"] * 3})

    def target(_data_path):
        # Person basis: 50% of persons in 1-person HH, 50% in 2-person HH.
        return pd.DataFrame({
            "geo_id": ["03101000", "03101000"],
            "category": ["1", "2"],
            "target_share": [0.5, 0.5],
        })

    ctrl = C.bucket_household_control(
        name="household_size", family="census", geography="gemeinde",
        column="household_size", top=6, target=target, top_label="6+",
        weight_column="household_size")
    long = CV.evaluate_control(ctrl, frames, geo, data_path="unused")
    row1 = long[long["category"] == "1"].iloc[0]
    row2 = long[long["category"] == "2"].iloc[0]
    # person basis: 2 of 4 persons in each class = 50% -> matches target, delta ~0.
    assert abs(row1["synthetic_pct"] - 50.0) < 1e-9
    assert abs(row1["delta_pp"]) < 1e-9
    assert abs(row2["synthetic_pct"] - 50.0) < 1e-9
    assert abs(row2["delta_pp"]) < 1e-9
