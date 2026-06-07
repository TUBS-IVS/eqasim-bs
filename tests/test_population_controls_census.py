"""Tests for the census + distribution controls added in Task 3b.

These exercise the standalone target loaders (which read the same census /
fleet source data the synthesis stages read, but WITHOUT a synpp context) and
the new realized-extractor builders (banded person, categorical household,
categorical vehicle). The MiD reference CSVs used by the existing controls and
the census source tables both live under ``eqasim-data/data`` on this machine.
"""
from __future__ import annotations

import logging

import pandas as pd
from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation.population_source import PopulationFrames

DATA = "eqasim-data/data"


def _frames():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "household_id": [10, 10, 20, 20],
        "age": [40, 41, 30, 5],
        "sex": ["male", "female", "female", "male"],
        "economic_status": ["medium", "medium", "low", "low"],
    })
    households = pd.DataFrame({
        "household_id": [10, 20],
        "household_size": [2, 2],
        "housing_tenure": ["own", "rent"],
        "number_of_cars": [1, 0],
    })
    return PopulationFrames(persons, households, None, None, "run_output", "x", "p_")


def _geo():
    return pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"],
                         "commune_id": ["031010000000", "031010000000"]})


# --- registry membership -----------------------------------------------------

def test_registry_includes_census_and_distribution_controls():
    names = {c.name for c in C.build_registry(DATA)}
    assert {"household_size", "age_group", "economic_status",
            "cars_per_hh", "driving_license_type"} <= names


# --- target loader schemas ---------------------------------------------------

def test_household_size_target_schema():
    t = C.household_size_target(DATA)
    assert set(t.columns) == {"geo_id", "category", "target_share"}
    s = t.groupby("geo_id")["target_share"].sum()
    assert (abs(s - 1.0) < 1e-6).all()


def test_age_group_target_schema_and_bounds_match():
    t = C.age_group_target(DATA)
    assert set(t.columns) == {"geo_id", "category", "target_share"}
    s = t.groupby("geo_id")["target_share"].sum()
    assert (abs(s - 1.0) < 1e-6).all()
    # The target categories must be exactly the bands produced by the registered
    # banded_person_control bounds (no extra / missing band).
    registered = {c.name: c for c in C.build_registry(DATA)}
    assert set(t["category"].unique()) == set(registered["age_group"].categories)


def test_sex_target_schema():
    t = C.sex_target(DATA)
    assert set(t.columns) == {"geo_id", "category", "target_share"}
    assert set(t["category"].unique()) == {"male", "female"}
    s = t.groupby("geo_id")["target_share"].sum()
    assert (abs(s - 1.0) < 1e-6).all()


# --- realized-extractor builders --------------------------------------------

def test_banded_person_control_bins_age():
    frames, geo = _frames(), _geo()
    ctrl = C.banded_person_control(
        "age_group", "census", "kreis", "age",
        bounds=(15, 30, 45, 60, 75), target=None,
    )
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # ages 40, 41 -> 30-44 ; 30 -> 30-44 ; 5 -> 0-14
    assert got["30-44"] == 3
    assert got["0-14"] == 1
    assert ctrl.categories == ("0-14", "15-29", "30-44", "45-59", "60-74", "75+")


def test_banded_person_control_absent_column_warns_and_empty(caplog):
    frames, geo = _frames(), _geo()
    ctrl = C.banded_person_control(
        "missing_band", "census", "kreis", "not_a_column",
        bounds=(15,), target=None,
    )
    with caplog.at_level(logging.WARNING):
        out = ctrl.realized(frames, geo)
    assert list(out.columns) == ["geo_id", "category", "synthetic_count"]
    assert out.empty
    assert any("missing_band" in r.message for r in caplog.records)


def test_categorical_household_control_counts():
    frames, geo = _frames(), _geo()
    ctrl = C.categorical_household_control(
        "housing_tenure", "census", "kreis", "housing_tenure",
        categories=("rent", "own", "other"), target=None,
    )
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    assert got == {"own": 1, "rent": 1}


def test_categorical_vehicle_control_absent_frame_skips(caplog):
    frames, geo = _frames(), _geo()  # vehicles is None
    ctrl = C.categorical_vehicle_control(
        "bev_share", "distribution", "kreis", "powertrain",
        categories=("bev", "not_bev"), target=None,
    )
    with caplog.at_level(logging.INFO):
        out = ctrl.realized(frames, geo)
    assert list(out.columns) == ["geo_id", "category", "synthetic_count"]
    assert out.empty
    assert any("bev_share" in r.message for r in caplog.records)


def test_categorical_vehicle_control_counts_when_present():
    persons = pd.DataFrame({
        "person_id": [1, 2], "household_id": [10, 20],
    })
    households = pd.DataFrame({"household_id": [10, 20]})
    # The real vehicles CSV carries owner_id (= a person_id) but no household_id;
    # the geography is recovered via the vehicles -> persons -> geo join.
    vehicles = pd.DataFrame({
        "owner_id": [1, 2],
        "powertrain": ["bev", "petrol"],
    })
    frames = PopulationFrames(persons, households, None, vehicles,
                              "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"],
                        "commune_id": ["031010000000", "031010000000"]})
    ctrl = C.categorical_vehicle_control(
        "powertrain", "distribution", "kreis", "powertrain",
        categories=("bev", "petrol"), target=None,
    )
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    assert got == {"bev": 1, "petrol": 1}


def test_descriptive_only_controls_have_no_target():
    reg = {c.name: c for c in C.build_registry(DATA)}
    # economic_status is Bayes-modelled from hhtype x region -> no hard geo target.
    assert reg["economic_status"].target is None
