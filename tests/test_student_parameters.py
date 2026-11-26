"""Tests of the student parameter vocabulary, its noise floor and the person-day labels (#140).

Every expected value is computed by hand on a few synthetic persons; no survey data are read.
"""
from __future__ import annotations

import warnings

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
    # a different seed gives a different draw sequence: with only 3 draws the quantiles differ
    values = {SP.bootstrap_share_noise_floor(labels, weights, ["yes", "no"], 3, seed=seed)
              for seed in range(8)}
    assert len(values) > 1


def test_noise_floor_by_hand_two_equal_persons_and_nan_labels_are_dropped():
    # Full shares (0.5, 0.5). A draw of two persons is yy or nn (total-variation distance 0.5,
    # probability 1/4 each) or yn / ny (distance 0, probability 1/2): the 0.95 quantile is 0.5.
    two = SP.bootstrap_share_noise_floor(pd.Series(["yes", "no"]), pd.Series([1.0, 1.0]),
                                         ["yes", "no"], 400, seed=1)
    assert two == pytest.approx(0.5)
    # a NaN-labelled person (huge weight) is not in the base, so the result is unchanged
    with_nan = SP.bootstrap_share_noise_floor(pd.Series(["yes", "no", np.nan]),
                                              pd.Series([1.0, 1.0, 1000.0]), ["yes", "no"], 400,
                                              seed=1)
    assert with_nan == pytest.approx(0.5)
    with pytest.raises(ValueError, match="maybe"):
        SP.bootstrap_share_noise_floor(pd.Series(["yes", "maybe"]), pd.Series([1.0, 1.0]),
                                       ["yes", "no"], 10, seed=1)


def test_noise_floor_counts_and_warns_about_zero_weight_draws(caplog):
    # person 0 has zero weight: a draw of two copies of it (probability 1/4) has no defined shares
    with caplog.at_level("WARNING", logger=SP.LOG.name):
        SP.bootstrap_share_noise_floor(pd.Series(["yes", "no"]), pd.Series([0.0, 1.0]),
                                       ["yes", "no"], 100, seed=2)
    assert any(record.levelname == "WARNING" and "zero total weight" in record.getMessage()
               for record in caplog.records)


def test_parameter_rows_reject_invalid_weights_and_lengths():
    for bad in (-1.0, np.nan, np.inf):
        with pytest.raises(ValueError, match="weights"):
            SP.parameter_rows("licence", SP.BASE_PRESENT, pd.Series(["yes", "no"]),
                              pd.Series([1.0, bad]))
    with pytest.raises(ValueError, match="weights"):
        SP.parameter_rows("licence", SP.BASE_PRESENT, pd.Series(["yes", "no"]), pd.Series([1.0]))


def test_parameter_rows_on_a_zero_weight_base_keep_counts_and_have_nan_shares():
    rows = SP.parameter_rows("licence", SP.BASE_PRESENT, pd.Series(["yes", "no"]),
                             pd.Series([0.0, 0.0]))
    assert list(rows["n_unweighted"]) == [1, 1]
    assert rows["share"].isna().all() and rows["se_share"].isna().all()


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


def test_persons_without_a_trip_or_time_raise_no_numeric_warning():
    """Regression: the hour bands floored NaN times of persons without a trip, which on some
    numpy builds (the server's) logs "invalid value encountered in floor" once per band."""
    persons = _persons([("a", 2), ("b", 0)])
    trips = _trips([("a", 1, "education", 480, np.nan), ("a", 2, "home", 900, 960)])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        labels, _ = SP.person_day_labels(persons, trips)
    by_pid = labels.set_index("pid")
    assert by_pid.loc["a", "first_departure_hour"] == "7_8"
    assert by_pid.loc["b", ["first_departure_hour", "last_arrival_home_hour",
                            "arrival_band"]].isna().all()


def test_trips_of_a_person_missing_from_the_persons_frame_raise():
    with pytest.raises(ValueError, match="ghost"):
        SP.person_day_labels(_persons([("a", 0)]),
                             _trips([("ghost", 1, "home", 100, 120)]))


def test_every_leg_type_and_other_counts_as_shop_or_errand():
    persons = _persons([("w", 1), ("l", 1), ("e", 1), ("o", 1), ("s", 1)])
    trips = _trips([("w", 1, "work", 480, 540), ("l", 1, "leisure", 480, 540),
                    ("e", 1, "escort", 480, 540), ("o", 1, "other", 480, 540),
                    ("s", 1, "shop", 480, 540)])
    labels, _ = SP.person_day_labels(persons, trips)
    legs = labels.set_index("pid")[["education_leg", "work_leg", "leisure_leg",
                                    "shop_or_errand_leg", "escort_leg"]]
    expected = {"w": "work_leg", "l": "leisure_leg", "e": "escort_leg", "o": "shop_or_errand_leg",
                "s": "shop_or_errand_leg"}
    for pid, only_yes in expected.items():
        row = legs.loc[pid]
        assert {column for column in legs.columns if row[column] == "yes"} == {only_yes}


def test_multiple_education_trips_unsorted_input_and_home_returns():
    # seq 1-2 education (first arrival 510 -> before_9; durations 600-510 = 90 and 900-620 = 280,
    # sum 370 -> 6h_more), seq 3 home, seq 4 other, seq 5 home: two returns, last arrival 19:10
    persons = _persons([("a", 5)])
    trips = _trips([("a", 4, "other", 1000, 1010), ("a", 2, "education", 600, 620),
                    ("a", 5, "home", 1100, 1150), ("a", 1, "education", 480, 510),
                    ("a", 3, "home", 900, 960)])
    labels, diag = SP.person_day_labels(persons, trips)
    row = labels.iloc[0]
    assert (row["arrival_band"], row["time_at_education"]) == ("before_9", "6h_more")
    assert (row["returns_home"], row["last_arrival_home_hour"]) == ("2", "19_21")
    assert (row["first_departure_hour"], row["shop_or_errand_leg"]) == ("7_8", "yes")
    assert diag["n_excluded_time_at_education"] == 0


def test_three_or_more_home_returns_are_pooled():
    persons = _persons([("a", 3)])
    trips = _trips([("a", 1, "home", 100, 110), ("a", 2, "home", 200, 210),
                    ("a", 3, "home", 300, 310)])
    labels, _ = SP.person_day_labels(persons, trips)
    assert labels.loc[0, "returns_home"] == "3plus"


def test_a_nan_defining_time_excludes_the_person_instead_of_taking_the_next_trip():
    # a: first trip has no departure time (the second trip's 10:00 must not stand in for it);
    # b: the last home trip has no arrival time (the earlier home arrival 07:30 must not stand in);
    # c: the first education trip has no arrival time (the second's 10:00 must not stand in)
    persons = _persons([("a", 2), ("b", 3), ("c", 3)])
    trips = _trips([("a", 1, "leisure", np.nan, 500), ("a", 2, "home", 600, 700),
                    ("b", 1, "home", 400, 450), ("b", 2, "leisure", 500, 550),
                    ("b", 3, "home", 700, np.nan),
                    ("c", 1, "education", 400, np.nan), ("c", 2, "education", 700, 600),
                    ("c", 3, "home", 900, 960)])
    labels, diag = SP.person_day_labels(persons, trips)
    by_pid = labels.set_index("pid")
    assert pd.isna(by_pid.loc["a", "first_departure_hour"])
    assert pd.isna(by_pid.loc["b", "last_arrival_home_hour"])
    assert pd.isna(by_pid.loc["c", "arrival_band"]) and pd.isna(by_pid.loc["c", "time_at_education"])
    assert by_pid.loc["a", "last_arrival_home_hour"] == "before_16"
    assert diag["n_excluded_first_departure_hour"] == 1
    assert diag["n_excluded_last_arrival_home_hour"] == 1
    assert diag["n_excluded_arrival_band"] == 1
    assert diag["n_excluded_time_at_education"] == 1


def test_a_negative_education_duration_is_excluded_and_counted():
    persons = _persons([("a", 2)])
    trips = _trips([("a", 1, "education", 480, 600), ("a", 2, "home", 500, 560)])
    labels, diag = SP.person_day_labels(persons, trips)
    assert pd.isna(labels.loc[0, "time_at_education"]) and labels.loc[0, "arrival_band"] == "9_11"
    assert diag["n_excluded_time_at_education"] == 1


def test_inconsistent_frames_raise():
    ok_trips = _trips([("a", 1, "home", 100, 110)])
    with pytest.raises(ValueError, match="duplicated pid"):
        SP.person_day_labels(_persons([("a", 1), ("a", 1)]), ok_trips)
    with pytest.raises(ValueError, match="duplicated"):
        SP.person_day_labels(_persons([("a", 2)]),
                             _trips([("a", 1, "home", 100, 110), ("a", 1, "leisure", 120, 130)]))
    with pytest.raises(ValueError, match="1 person"):   # n_trips 0 but one trip row
        SP.person_day_labels(_persons([("a", 0)]), ok_trips)
    with pytest.raises(ValueError, match="1 person"):   # n_trips 2 but one trip row
        SP.person_day_labels(_persons([("a", 2)]), ok_trips)
    with pytest.raises(ValueError, match="n_trips"):
        SP.person_day_labels(_persons([("a", 1.5)]), ok_trips)
    with pytest.raises(ValueError, match="n_trips"):
        SP.person_day_labels(_persons([("a", -1)]), ok_trips.iloc[0:0])
    with pytest.raises(ValueError, match="n_trips"):
        SP.person_day_labels(_persons([("a", np.nan)]), ok_trips.iloc[0:0])
