import numpy as np
import pandas as pd
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


def test_registry_is_nonempty_and_well_formed():
    reg = C.build_registry(data_path="eqasim-data/data")
    assert len(reg) > 0
    for ctrl in reg:
        assert ctrl.family in {"census", "mid_person", "mid_household", "distribution"}
        assert ctrl.geography in {"kreis", "gemeinde"}
        assert callable(ctrl.realized)
