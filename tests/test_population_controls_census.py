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
    # The registry now holds only REAL-target controls (descriptive-only ones
    # were removed -- their attributes are exported spatially via geo_export, not
    # validated against an invented target).
    assert names == {
        "household_size", "age_group", "sex", "cars_per_hh",
        "driving_license_type", "pt_ticket_type", "bicycles_per_hh",
        "employment", "bev_share",
    }
    # economic_status / housing_tenure / income_class are exported spatially
    # (geo_export), not validated -> they must NOT be registered as controls.
    assert "economic_status" not in names
    assert "housing_tenure" not in names
    assert "income_class" not in names


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


# --- employment (P9) control ------------------------------------------------

def test_employment_target_schema():
    t = C.employment_target(DATA)
    assert set(t.columns) == {"geo_id", "category", "target_share"}
    # 8 ZGB Kreise (the ZGB aggregate row 03ZGB is excluded).
    assert len(t["geo_id"].unique()) == 8
    assert all(len(g) == 5 for g in t["geo_id"].unique())
    assert set(t["category"].unique()) == {"employed", "not_employed"}
    s = t.groupby("geo_id")["target_share"].sum()
    assert (abs(s - 1.0) < 1e-9).all()


def test_employment_control_respects_age_base():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "household_id": [10, 20, 30],
        "age": [10, 40, 80],
        "employed": [True, True, True],
    })
    households = pd.DataFrame({"household_id": [10, 20, 30]})
    frames = PopulationFrames(persons, households, None, None,
                              "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20, 30],
                        "ars5": ["03101", "03101", "03101"],
                        "commune_id": ["031010000000"] * 3})
    reg = {c.name: c for c in C.build_registry(DATA)}
    long = reg["employment"].realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # Only the age-40 person is within the 15-74 base -> exactly one "employed".
    assert got.get("employed", 0) == 1
    assert got.get("not_employed", 0) == 0


# --- robust driving-licence control -----------------------------------------

def test_license_control_derives_from_boolean():
    # Case 1: no license_type column, only the boolean has_driving_license.
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "household_id": [10, 20, 30],
        "has_driving_license": [True, True, False],
    })
    households = pd.DataFrame({"household_id": [10, 20, 30]})
    frames = PopulationFrames(persons, households, None, None,
                              "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20, 30],
                        "ars5": ["03101", "03101", "03101"],
                        "commune_id": ["031010000000"] * 3})
    ctrl = C.license_control("driving_license_type", "mid_person", "kreis",
                             target=None)
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    assert got == {"ja": 2, "nein": 1}

    # Case 2: license_type present -> used verbatim (boolean ignored).
    persons2 = pd.DataFrame({
        "person_id": [1, 2, 3],
        "household_id": [10, 20, 30],
        "license_type": ["ja", "nein", "keine_angabe"],
        "has_driving_license": [True, True, True],
    })
    frames2 = PopulationFrames(persons2, households, None, None,
                               "run_output", "x", "p_")
    long2 = ctrl.realized(frames2, geo)
    got2 = dict(zip(long2["category"], long2["synthetic_count"]))
    assert got2 == {"ja": 1, "nein": 1, "keine_angabe": 1}


def test_license_control_respects_age_base():
    """license_control with age_min=14 must count only persons age >= 14.

    Both persons have has_driving_license=True (no license_type column), but
    the age-10 child is below the 14+ MiD P17.1 base, so only the age-40
    person contributes a "ja" count (total = 1, not 2).
    """
    persons = pd.DataFrame({
        "person_id": [1, 2],
        "household_id": [10, 20],
        "age": [10, 40],
        "has_driving_license": [True, True],
    })
    households = pd.DataFrame({"household_id": [10, 20]})
    frames = PopulationFrames(persons, households, None, None,
                              "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20],
                        "ars5": ["03101", "03101"],
                        "commune_id": ["031010000000", "031010000000"]})
    ctrl = C.license_control("driving_license_type", "mid_person", "kreis",
                             target=None, age_min=14)
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # Only the age-40 person is within the 14+ base -> exactly one "ja".
    assert got.get("ja", 0) == 1, f"Expected ja=1 (age-10 excluded), got {got}"
    assert got.get("nein", 0) == 0


def test_pt_control_age_base_excludes_children():
    """pt_ticket_type categorical control with age_min=14 must exclude children.

    The age-8 child has pt_subscription_type="fahre_nie"; the age-40 adult
    has "deutschlandticket".  With age_min=14 the child is excluded, so only
    the adult's category is counted (deutschlandticket=1); fahre_nie=0.
    """
    persons = pd.DataFrame({
        "person_id": [1, 2],
        "household_id": [10, 20],
        "age": [8, 40],
        "pt_subscription_type": ["fahre_nie", "deutschlandticket"],
    })
    households = pd.DataFrame({"household_id": [10, 20]})
    frames = PopulationFrames(persons, households, None, None,
                              "run_output", "x", "p_")
    geo = pd.DataFrame({"household_id": [10, 20],
                        "ars5": ["03101", "03101"],
                        "commune_id": ["031010000000", "031010000000"]})
    import braunschweig.data.mid.reference_tables as RT
    ctrl = C.categorical_person_control(
        "pt_ticket_type", "mid_person", "kreis", "pt_subscription_type",
        RT.PT_TICKET_CATEGORIES, target=None, age_min=14,
    )
    long = ctrl.realized(frames, geo)
    got = dict(zip(long["category"], long["synthetic_count"]))
    # Only the age-40 adult is within the 14+ base -> deutschlandticket=1.
    assert got.get("deutschlandticket", 0) == 1, (
        f"Expected deutschlandticket=1 (age-8 child excluded), got {got}"
    )
    assert got.get("fahre_nie", 0) == 0, (
        f"Expected fahre_nie=0 (age-8 child excluded from base), got {got}"
    )
