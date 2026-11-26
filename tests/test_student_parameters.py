"""Tests of the student parameter vocabulary, its noise floor and the person-day labels (#140).

Every expected value is computed by hand on a few synthetic persons; no survey data are read.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import student_parameters as SP
from braunschweig.calibration.srv_plan_structure import (
    HARMONISED_PERSON_COLUMNS, HARMONISED_TRIP_COLUMNS)


def test_parameter_rows_emit_every_label_of_the_vocabulary():
    rows = SP.parameter_rows("age_group", SP.BASE_PRESENT,
                             pd.Series(["20_24", "20_24", "25_29"]), pd.Series([1.0, 1.0, 1.0]))
    assert list(rows.columns) == SP.PARAMETER_COLUMNS
    assert list(rows["label"]) == ["18_19", "20_24", "25_29", "30plus"]
    assert rows.set_index("label").loc["30plus", "n_unweighted"] == 0
    assert rows.set_index("label").loc["30plus", "share"] == 0.0
    with pytest.raises(ValueError, match="40plus"):
        SP.parameter_rows("age_group", SP.BASE_PRESENT, pd.Series(["40plus"]), pd.Series([1.0]))


def test_parameter_rows_by_hand():
    rows = SP.parameter_rows("licence", SP.BASE_PRESENT,
                             pd.Series(["yes", "yes", "no", np.nan]),
                             pd.Series([1.0, 1.0, 2.0, 5.0]))
    yes = rows.set_index("label").loc["yes"]
    assert yes["share"] == pytest.approx(0.5) and yes["n_unweighted"] == 2
    assert yes["n_effective"] == pytest.approx(16 / 6)
    assert yes["se_share"] == pytest.approx((0.25 / (16 / 6)) ** 0.5)


def test_parameter_rows_on_an_empty_base_have_nan_shares_and_zero_counts():
    rows = SP.parameter_rows("licence", SP.BASE_PRESENT, pd.Series([np.nan]), pd.Series([1.0]))
    assert list(rows["label"]) == ["yes", "no"]
    assert (rows["n_unweighted"] == 0).all()
    assert rows["share"].isna().all()


def test_parameter_rows_reject_an_unknown_parameter_or_base():
    with pytest.raises(ValueError, match="no_such_parameter"):
        SP.parameter_rows("no_such_parameter", SP.BASE_PRESENT, pd.Series(["yes"]),
                          pd.Series([1.0]))
    with pytest.raises(ValueError, match=SP.BASE_MOBILE):
        SP.parameter_rows("licence", SP.BASE_MOBILE, pd.Series(["yes"]), pd.Series([1.0]))


def test_vocabulary_matches_the_shared_label_edges():
    """Trip class and arrival band reuse the Phase 0 edges, so the label counts must line up."""
    from braunschweig.calibration.student_behaviour_labels import (
        ARRIVAL_HOUR_EDGES, TRIP_CLASS_EDGES)
    assert len(SP.PARAMETERS["trip_class"].labels) == len(TRIP_CLASS_EDGES) - 1
    assert len(SP.PARAMETERS["arrival_band"].labels) == len(ARRIVAL_HOUR_EDGES) - 1
    assert list(SP.PARAMETERS)[:4] == ["age_group", "sex", "household_type",
                                       "lives_with_person_18_years_older"]
    assert SP.PARAMETERS["away_all_day"].group == SP.GROUP_DAY


def test_noise_floor_is_seeded_and_zero_for_one_person():
    labels = pd.Series(["yes"] * 6 + ["no"] * 4)
    weights = pd.Series(np.linspace(1.0, 2.0, 10))
    first = SP.bootstrap_share_noise_floor(labels, weights, ["yes", "no"], 200, seed=3)
    second = SP.bootstrap_share_noise_floor(labels, weights, ["yes", "no"], 200, seed=3)
    assert first == second and 0.0 < first < 1.0
    assert SP.bootstrap_share_noise_floor(pd.Series(["yes"]), pd.Series([1.0]),
                                          ["yes", "no"], 50, seed=3) == 0.0
    with pytest.raises(ValueError):
        SP.bootstrap_share_noise_floor(labels, weights, ["yes", "no"], 0, seed=3)


def _persons(rows):
    frame = pd.DataFrame(rows, columns=["pid", "n_trips"])
    for column in HARMONISED_PERSON_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[HARMONISED_PERSON_COLUMNS]


def _trips(rows):
    frame = pd.DataFrame(rows, columns=["pid", "seq", "purpose", "dep_min", "arr_min"])
    frame["weight"] = 1.0
    frame["prev_purpose"] = "unknown"
    return frame[HARMONISED_TRIP_COLUMNS]


def test_day_labels_by_hand():
    # a: home->edu (dep 08:00, arr 510), edu->shop (dep 750, arr 800), shop->home (arr 1100)
    # b: no trip; c: home->edu (arr 600) and the day ends at education
    persons = _persons([("a", 3), ("b", 0), ("c", 1)])
    trips = _trips([("a", 1, "education", 480, 510), ("a", 2, "shop", 750, 800),
                    ("a", 3, "home", 1000, 1100), ("c", 1, "education", 540, 600)])
    labels, diag = SP.person_day_labels(persons, trips)
    by_pid = labels.set_index("pid")
    a = by_pid.loc["a"]
    assert (a["education_leg"], a["shop_or_errand_leg"], a["arrival_band"]) == (
        "yes", "yes", "before_9")
    assert (a["time_at_education"], a["returns_home"], a["last_arrival_home_hour"]) == (
        "4_6h", "1", "16_18")
    assert (a["mobile"], a["trip_class"], a["first_departure_hour"]) == ("yes", "3_4", "7_8")
    assert (a["work_leg"], a["leisure_leg"], a["escort_leg"]) == ("no", "no", "no")
    b = by_pid.loc["b"]
    assert (b["mobile"], b["trip_class"], b["education_leg"]) == ("no", "0", "no")
    assert pd.isna(b["first_departure_hour"]) and pd.isna(b["time_at_education"])
    c = by_pid.loc["c"]
    assert pd.isna(c["time_at_education"]) and c["arrival_band"] == "9_11"
    assert pd.isna(c["last_arrival_home_hour"]) and c["returns_home"] == "0"
    assert diag["n_excluded_time_at_education"] == 1
    assert diag["n_excluded_last_arrival_home_hour"] == 1
    assert diag["n_excluded_first_departure_hour"] == 0
    assert diag["n_excluded_arrival_band"] == 0
    assert list(labels.columns) == ["pid"] + [
        name for name, spec in SP.PARAMETERS.items()
        if spec.group == SP.GROUP_DAY and name != "away_all_day"]


@pytest.mark.parametrize("minutes, expected", [
    (0, "before_7"), (419, "before_7"), (420, "7_8"), (539, "7_8"), (540, "9_11"),
    (719, "9_11"), (720, "12_15"), (959, "12_15"), (960, "16_later")])
def test_first_departure_hour_bands_include_their_named_end_hours(minutes, expected):
    persons = _persons([("a", 1)])
    trips = _trips([("a", 1, "leisure", minutes, minutes + 10)])
    labels, _ = SP.person_day_labels(persons, trips)
    assert labels.loc[0, "first_departure_hour"] == expected


@pytest.mark.parametrize("arrival, expected", [
    (959, "before_16"), (960, "16_18"), (1139, "16_18"), (1140, "19_21"), (1319, "19_21"),
    (1320, "22_later"), (1500, "22_later")])
def test_last_arrival_home_bands_include_their_named_end_hours(arrival, expected):
    persons = _persons([("a", 1)])
    trips = _trips([("a", 1, "home", arrival - 10, arrival)])
    labels, _ = SP.person_day_labels(persons, trips)
    assert labels.loc[0, "last_arrival_home_hour"] == expected


@pytest.mark.parametrize("minutes, expected", [
    (119, "under_2h"), (120, "2_4h"), (239, "2_4h"), (240, "4_6h"), (359, "4_6h"),
    (360, "6h_more")])
def test_time_at_education_bands_are_left_inclusive(minutes, expected):
    persons = _persons([("a", 2)])
    trips = _trips([("a", 1, "education", 400, 500),
                    ("a", 2, "home", 500 + minutes, 500 + minutes + 20)])
    labels, _ = SP.person_day_labels(persons, trips)
    assert labels.loc[0, "time_at_education"] == expected


def test_a_missing_time_excludes_only_that_parameter_and_is_counted():
    persons = _persons([("a", 2)])
    trips = _trips([("a", 1, "education", 480, np.nan), ("a", 2, "home", 900, 960)])
    labels, diag = SP.person_day_labels(persons, trips)
    row = labels.iloc[0]
    assert pd.isna(row["arrival_band"]) and pd.isna(row["time_at_education"])
    assert row["first_departure_hour"] == "7_8" and row["last_arrival_home_hour"] == "16_18"
    assert diag["n_excluded_arrival_band"] == 1
    assert diag["n_excluded_time_at_education"] == 1
    assert diag["n_excluded_first_departure_hour"] == 0


def test_trips_of_a_person_missing_from_the_persons_frame_raise():
    with pytest.raises(ValueError, match="ghost"):
        SP.person_day_labels(_persons([("a", 0)]),
                             _trips([("ghost", 1, "home", 100, 120)]))
