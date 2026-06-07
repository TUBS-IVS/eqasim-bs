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
