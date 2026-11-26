"""Tests of the model-side student harmonisation and the comparison with SrV (#140, Phase 1).

Every expected value is computed by hand on a few synthetic persons; the SrV side is a small
synthetic table in ``student_parameters.PARAMETER_COLUMNS``, never the committed file.
"""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import student_measurement as M
from braunschweig.calibration import student_parameters as SP
from braunschweig.calibration.srv_student_reference import (SHARE_COLUMNS,
                                                            YOUNG_ADULT_STATUS_COLUMNS,
                                                            YOUNG_ADULT_STATUSES)
from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES, PT_TICKET_FLATRATE
from braunschweig.popsim.attributes import map_pt_ticket_group
from braunschweig.synthesis.day_absence.absence import STATE_ABSENT_INDIVIDUAL, STATE_PRESENT

HOUR_S = 3600.0
BOUND = 0.05

# person_id, household_id, age, sex, P_TAET, has_license, number_of_cars, number_of_bicycles,
# pt_subscription_type, has_pt_subscription. Students (P_TAET 10) 1, 2, 5, 6 live in 03101,
# student 7 in Wolfsburg (03103); person 8 is an adult pupil (P_TAET 9) in 03101.
_PERSONS = [
    (1, 10, 22, "female", 10, True, 1, 2, "job_or_semester_ticket", True),
    (2, 20, 31, "male", 10, False, 0, 0, "deutschlandticket", True),
    (3, 20, 55, "female", 1, True, 0, 0, "single_ticket", False),
    (4, 20, 12, "male", 9, False, 0, 0, "never_pt", False),
    (5, 30, 19, "female", 10, True, 2, 1, "never_pt", False),
    (6, 30, 25, "male", 10, False, 2, 1, "single_ticket", False),
    (7, 40, 23, "male", 10, True, 1, 1, "deutschlandticket", True),
    (8, 50, 18, "female", 9, False, 0, 1, "multi_ride_ticket", False),
]
# person_id, trip_index, departure hour, arrival hour, preceding purpose, following purpose
_TRIPS = [
    (1, 0, 8.0, 8.5, "home", "education"), (1, 1, 16.0, 16.5, "education", "home"),
    (2, 0, 9.0, 9.5, "home", "education"), (2, 1, 13.0, 13.5, "education", "home"),
    (3, 0, 7.0, 7.5, "home", "work"), (3, 1, 17.0, 17.5, "work", "home"),
    (5, 0, 18.0, 18.25, "home", "leisure"), (5, 1, 20.0, 20.25, "leisure", "home"),
    (6, 0, 7.0, 7.5, "home", "education"), (6, 1, 15.0, 15.5, "education", "home"),
    (7, 0, 8.0, 8.5, "home", "education"), (7, 1, 12.0, 12.5, "education", "home"),
    (8, 0, 7.5, 8.0, "home", "education"), (8, 1, 13.0, 13.5, "education", "home"),
]
_ABSENT = {6}
_HOMES = {10: "03101", 20: "03101", 30: "03101", 40: "03103", 50: "03101"}


def _build(drop_persons=()):
    persons = pd.DataFrame(_PERSONS, columns=[
        "person_id", "household_id", "age", "sex", "P_TAET", "has_license", "number_of_cars",
        "number_of_bicycles", "pt_subscription_type", "has_pt_subscription"])
    persons["employed"] = persons["P_TAET"] == 1
    persons["employment_status"] = np.where(persons["employed"], "vollzeit",
                                            "nicht_erwerbstaetig")
    trips = pd.DataFrame(_TRIPS, columns=["person_id", "trip_index", "dep_h", "arr_h",
                                          "preceding_purpose", "following_purpose"])
    trips["departure_time"] = trips.pop("dep_h") * HOUR_S
    trips["arrival_time"] = trips.pop("arr_h") * HOUR_S
    absence = persons[["person_id", "household_id"]].copy()
    absence["day_absence_state"] = np.where(absence["person_id"].isin(_ABSENT),
                                            STATE_ABSENT_INDIVIDUAL, STATE_PRESENT)
    homes = pd.DataFrame({"household_id": list(_HOMES), "ars5": list(_HOMES.values())})
    keep = ~persons["person_id"].isin(drop_persons)
    persons = persons[keep].reset_index(drop=True)
    trips = trips[trips["person_id"].isin(persons["person_id"])].reset_index(drop=True)
    absence = absence[absence["person_id"].isin(persons["person_id"])].reset_index(drop=True)
    return persons, trips, homes, absence


def _srv_table(overrides=None, drop=()):
    """A full SrV table: every (parameter, base, label) cell, share 1/len(labels), se 0.05."""
    rows = []
    for parameter, spec in SP.PARAMETERS.items():
        for base in (spec.base,) + SP.EXTRA_BASES.get(parameter, ()):
            for label in spec.labels:
                rows.append({"group": spec.group, "parameter": parameter, "base": base,
                             "label": label, "n_unweighted": 40, "weight_sum": 40.0,
                             "share": 1.0 / len(spec.labels), "n_effective": 35.0,
                             "se_share": 0.05})
    table = pd.DataFrame(rows, columns=SP.PARAMETER_COLUMNS)
    for (parameter, label), values in (overrides or {}).items():
        mask = (table["parameter"] == parameter) & (table["label"] == label)
        for column, value in values.items():
            table.loc[mask, column] = value
    for parameter, label in drop:
        table = table[~((table["parameter"] == parameter) & (table["label"] == label))]
    return table.reset_index(drop=True)


def _student_frame(frames, bound=BOUND):
    persons, trips, homes, absence = frames
    return M.model_student_frame(persons, trips, homes, absence, max_unmatched_home_share=bound)


def _row(out, parameter, label, base=None):
    mask = (out["parameter"] == parameter) & (out["label"] == label)
    if base is not None:
        mask &= out["base"] == base
    selected = out[mask]
    assert len(selected) == 1
    return selected.iloc[0]


@pytest.fixture
def frames():
    return _build()


def test_day_and_attribute_shares_use_present_students_only(frames):
    # 4 students in 03101: 3 present (2 with an education trip), 1 absent with an education trip
    students, _ = _student_frame(frames)
    out = M.compare_parameters(students, _srv_table())
    assert list(out.columns) == M.COMPARISON_COLUMNS
    edu = _row(out, "education_leg", "yes")
    assert (edu["model_n_base"], edu["model_share"]) == (3, pytest.approx(2 / 3))
    # education base = present students with an education leg (1: 450 min, 2: 210 min); the
    # absent student 6 (450 min) would make 6h_more 2/3
    long_day = _row(out, "time_at_education", "6h_more")
    assert (long_day["model_n_base"], long_day["model_share"]) == (2, pytest.approx(1 / 2))
    assert _row(out, "first_departure_hour", "7_8")["model_n_base"] == 3
    away = _row(out, "away_all_day", "yes", SP.BASE_STUDENTS_ALL)
    assert (away["model_n_base"], away["model_share"]) == (4, pytest.approx(1 / 4))
    other = _row(out, "away_all_day", "yes", SP.BASE_OTHER_ADULTS_ALL)
    assert pd.isna(other["model_share"]) and pd.isna(other["model_n_base"])
    assert other["srv_share"] == pytest.approx(0.5)


def test_student_labels_by_hand(frames):
    students, diagnostics = _student_frame(frames)
    assert list(students.columns) == M.MODEL_STUDENT_COLUMNS
    labels = students.set_index("person_id")
    assert sorted(labels.index) == [1, 2, 5, 6, 7]   # the adult pupil 8 is no student
    expected = {
        # age_group, sex, household_type, older, licence, cars, car_access, bicycle, season, group4
        1: ("20_24", "female", "alone", "no", "yes", "1", "yes", "yes", "flatrate",
            "other_flatrate"),
        2: ("30plus", "male", "with_children", "yes", "no", "0", "no", "no", "flatrate",
            "deutschlandticket"),
        5: ("18_19", "female", "several_adults", "no", "yes", "2plus", "yes", "yes",
            "no_flatrate", "never_pt"),
        6: ("25_29", "male", "several_adults", "no", "no", "2plus", "no", "yes", "no_flatrate",
            "occasional_ticket"),
    }
    for person_id, values in expected.items():
        assert tuple(labels.loc[person_id, list(M.ATTRIBUTE_PARAMETERS)]) == values
    assert labels["present"].to_dict() == {1: True, 2: True, 5: True, 6: False, 7: True}
    assert labels["kreis"].to_dict() == {1: "03101", 2: "03101", 5: "03101", 6: "03101",
                                         7: "03103"}
    assert (diagnostics["n_adult_students"], diagnostics["n_adult_students_absent"]) == (5, 1)
    assert diagnostics["n_adult_students_by_kreis"] == {"03101": 4, "03103": 1}


def test_flatrate_groups_are_the_groups_of_the_flatrate_subscription_types():
    # each subscription type through the owner's mapping on its own: the flat-rate types land
    # exactly on the derived groups, every other type outside them
    groups = {category: map_pt_ticket_group(pd.DataFrame(
        {"pt_subscription_type": [category]}))["pt_ticket_group4"].iloc[0]
        for category in PT_TICKET_CATEGORIES}
    derived = set(M.flatrate_ticket_groups4())
    assert derived == {groups[category] for category in PT_TICKET_FLATRATE}
    assert all(groups[category] not in derived
               for category in set(PT_TICKET_CATEGORIES) - PT_TICKET_FLATRATE)


def _set(column, person_id, value):
    def mutate(persons, trips, homes, absence):
        persons = persons.copy()
        persons[column] = persons[column].astype(object)
        persons.loc[persons["person_id"] == person_id, column] = value
        return persons, trips, homes, absence
    return mutate


def _home_kreis(household_id, kreis):
    def mutate(persons, trips, homes, absence):
        homes = homes.copy()
        homes.loc[homes["household_id"] == household_id, "ars5"] = kreis
        return persons, trips, homes, absence
    return mutate


def _absence_state(person_id, state):
    def mutate(persons, trips, homes, absence):
        absence = absence.copy()
        absence.loc[absence["person_id"] == person_id, "day_absence_state"] = state
        return persons, trips, homes, absence
    return mutate


@pytest.mark.parametrize("mutate, match", [
    (_set("sex", 1, "diverse"), "sex"),
    (_set("pt_subscription_type", 1, "bahncard"), "pt_subscription_type"),
    (_set("has_pt_subscription", 1, False), "has_pt_subscription"),
    (_set("P_TAET", 3, 55), "P_TAET"),
    (_set("number_of_cars", 1, -1), "number_of_cars"),
    (_set("has_license", 1, np.nan), "has_license"),
    (_home_kreis(10, "03241"), "ZGB_KREISE"),                    # Region Hannover
    (_absence_state(1, "on_holiday"), "day_absence_state"),
    (_absence_state(1, np.nan), "day_absence_state"),
])
def test_codes_outside_the_documented_set_raise(frames, mutate, match):
    with pytest.raises(ValueError, match=match):
        _student_frame(mutate(*frames))


def test_unknown_ages_are_excluded_and_counted(frames):
    persons, trips, homes, absence = frames
    persons = persons.assign(age=persons["age"].astype(float))
    persons.loc[persons["person_id"] == 6, "age"] = np.nan   # student 6: no age, no adult
    students, diagnostics = M.model_student_frame(persons, trips, homes, absence,
                                                  max_unmatched_home_share=BOUND)
    labels = students.set_index("person_id")
    assert sorted(labels.index) == [1, 2, 5, 7]
    assert (diagnostics["n_students_missing_age"], diagnostics["n_adult_students"]) == (1, 4)
    # student 5 lives with 6 (age unknown, no known minor): neither label is determined
    assert labels.loc[5, ["household_type", "lives_with_person_18_years_older"]].isna().all()
    assert (diagnostics["n_excluded_household_type"],
            diagnostics["n_excluded_lives_with_person_18_years_older"]) == (1, 1)
    assert labels.loc[2, "household_type"] == "with_children"


def test_mostly_unreadable_times_raise(frames):
    persons, trips, homes, absence = frames
    # the first departure of 4 of the 5 mobile adult students is unreadable: 80 % excluded
    unreadable = trips["person_id"].isin([1, 2, 5, 6]) & (trips["trip_index"] == 0)
    trips = trips.assign(departure_time=trips["departure_time"].where(~unreadable))
    with pytest.raises(ValueError, match="first_departure_hour"):
        M.model_student_frame(persons, trips, homes, absence, max_unmatched_home_share=BOUND)


def _orphan_trip(persons, trips, homes, absence):
    extra = trips.iloc[[0]].assign(person_id=99)
    return persons, pd.concat([trips, extra], ignore_index=True), homes, absence


def _duplicate_person(persons, trips, homes, absence):
    return pd.concat([persons, persons.iloc[[0]]], ignore_index=True), trips, homes, absence


def _orphan_absence(persons, trips, homes, absence):
    extra = absence.iloc[[0]].assign(person_id=99)
    return persons, trips, homes, pd.concat([absence, extra], ignore_index=True)


def _duplicate_absence(persons, trips, homes, absence):
    return persons, trips, homes, pd.concat([absence, absence.iloc[[0]]], ignore_index=True)


def _student_without_absence_row(persons, trips, homes, absence):
    return persons, trips, homes, absence[absence["person_id"] != 2].reset_index(drop=True)


@pytest.mark.parametrize("mutate", [_orphan_trip, _duplicate_person, _orphan_absence,
                                    _duplicate_absence, _student_without_absence_row])
def test_orphan_or_duplicate_person_ids_raise(frames, mutate):
    with pytest.raises(ValueError, match=r"1 .*person_id"):
        _student_frame(mutate(*frames))


def test_labels_outside_the_vocabulary_raise_and_missing_cells_are_emitted(frames):
    students, _ = _student_frame(frames)
    out = M.compare_parameters(students, _srv_table(drop=[("age_group", "30plus")]))
    row = _row(out, "age_group", "30plus")
    assert pd.isna(row["srv_share"]) and pd.isna(row["srv_n_unweighted"])
    assert row["model_share"] == pytest.approx(1 / 3)          # student 2 of the 3 present
    assert len(out) == len(_srv_table())                       # the full vocabulary grid
    assert M.count_missing_srv_cells(out) == 1
    empty_cell = _srv_table({("age_group", "30plus"): {"n_unweighted": 0, "share": 0.0}})
    assert M.count_missing_srv_cells(M.compare_parameters(students, empty_cell)) == 0
    unknown = _srv_table()
    unknown.loc[unknown["label"] == "30plus", "label"] = "40plus"
    with pytest.raises(ValueError, match="vocabulary"):
        M.compare_parameters(students, unknown)
    odd_model = students.copy()
    odd_model.loc[0, "licence"] = "maybe"
    with pytest.raises(ValueError, match="vocabulary"):
        M.compare_parameters(odd_model, _srv_table())


def _uniform_students(n_students):
    frame = pd.DataFrame({"person_id": range(n_students), "kreis": "03101", "present": True})
    for parameter in M.ATTRIBUTE_PARAMETERS + M.DAY_PARAMETERS:
        frame[parameter] = SP.PARAMETERS[parameter].labels[0]
    return frame


def test_difference_in_standard_errors_and_flags_by_hand():
    students = _uniform_students(20)
    students.loc[:10, "licence"] = "yes"          # 11 of 20 -> 0.55
    students.loc[11:, "licence"] = "no"           # 9 of 20 -> 0.45
    students.loc[:10, "sex"] = "female"
    students.loc[11:, "sex"] = "male"             # every student is in 18_19: 30plus is 0
    srv = _srv_table({("licence", "yes"): {"share": 0.40, "se_share": 0.05, "n_unweighted": 31},
                      ("licence", "no"): {"share": 0.60, "se_share": 0.05, "n_unweighted": 29},
                      ("sex", "female"): {"share": 0.50, "se_share": 0.05},
                      ("age_group", "30plus"): {"share": 0.0, "se_share": 0.0}})
    out = M.compare_parameters(students, srv)
    yes, no = _row(out, "licence", "yes"), _row(out, "licence", "no")
    assert yes["difference_pp"] == pytest.approx(15.0)
    assert yes["difference_in_se"] == pytest.approx(3.0)
    assert bool(yes["beyond_two_se"]) and bool(yes["srv_cell_ok"])
    assert no["difference_in_se"] == pytest.approx(-3.0)
    assert bool(no["beyond_two_se"]) and not bool(no["srv_cell_ok"])
    female = _row(out, "sex", "female")
    assert female["difference_in_se"] == pytest.approx(1.0) and not bool(female["beyond_two_se"])
    # a zero SrV standard error leaves the difference in standard errors undefined, not infinite
    older = _row(out, "age_group", "30plus")
    assert older["difference_pp"] == pytest.approx(0.0)
    assert pd.isna(older["difference_in_se"]) and pd.isna(older["beyond_two_se"])


def test_wolfsburg_and_non_students_are_outside_the_comparison(frames):
    students, _ = _student_frame(frames)
    assert 7 in set(students["person_id"])        # the Wolfsburg student reaches the frame
    reduced, _ = _student_frame(_build(drop_persons=(7, 8)))
    srv = _srv_table()
    pd.testing.assert_frame_equal(M.compare_parameters(students, srv),
                                  M.compare_parameters(reduced, srv))


def test_unmatched_home_share_above_the_bound_raises(frames):
    persons, trips, homes, absence = frames
    homes = homes[homes["household_id"] != 10].reset_index(drop=True)   # student 1: 1 of 5
    with pytest.raises(ValueError, match="home Kreis"):
        M.model_student_frame(persons, trips, homes, absence, max_unmatched_home_share=0.1)
    students, diagnostics = M.model_student_frame(persons, trips, homes, absence,
                                                  max_unmatched_home_share=0.3)
    assert diagnostics["n_adult_students_no_kreis"] == 1
    edu = _row(M.compare_parameters(students, _srv_table()), "education_leg", "yes")
    assert (edu["model_n_base"], edu["model_share"]) == (2, pytest.approx(1 / 2))


# --------------------------------------------------------------------------- Task 7 tables
SAMPLING_RATE = 0.25
# person_id, age, P_TAET, employed, employment_status, kreis, present. 03101: three students
# (22 away all day, 19, 35), an employed 25-year-old, a 27-year-old with no job, an employed
# 50-year-old, a pupil of 12 and a 40-year-old without a readable P_TAET (99); 03102: a student
# of 24 and an employed apprentice of 29; Wolfsburg (03103): a student of 21; one student of 23
# without a home Kreis.
_LEVEL_PERSONS = [
    (1, 22, 10, False, "nicht_erwerbstaetig", "03101", False),
    (2, 19, 10, False, "geringfuegig", "03101", True),
    (3, 35, 10, True, "teilzeit", "03101", True),
    (4, 25, 1, True, "vollzeit", "03101", True),
    (5, 27, 12, False, "nicht_erwerbstaetig", "03101", True),
    (6, 50, 1, True, "vollzeit", "03101", True),
    (7, 12, 9, False, "nicht_erwerbstaetig", "03101", True),
    (8, 40, 99, False, "nicht_erwerbstaetig", "03101", True),
    (9, 24, 10, False, "nicht_erwerbstaetig", "03102", True),
    (10, 29, 8, True, "in_ausbildung", "03102", True),
    (11, 21, 10, False, "nicht_erwerbstaetig", "03103", True),
    (12, 23, 10, False, "nicht_erwerbstaetig", None, True),
]


def _level_persons():
    return pd.DataFrame(_LEVEL_PERSONS, columns=["person_id", "age", "P_TAET", "employed",
                                                 "employment_status", "kreis", "present"])


def _srv_shares():
    rows = [{"code": code, "level": "total" if code == M.REGION_CODE else "kreis",
             "student_share_adults": 0.3, "se_student_share_adults": 0.1,
             "student_share_20_29": 0.3, "se_student_share_20_29": 0.1,
             "students_weighted_persons": 100.0}
            for code in list(M.ZGB_KREISE) + [M.REGION_CODE]]
    table = pd.DataFrame(rows).reindex(columns=SHARE_COLUMNS).set_index("code")
    shares = ["student_share_adults", "se_student_share_adults", "student_share_20_29",
              "se_student_share_20_29", "students_weighted_persons"]
    table.loc["03101", shares] = [0.4, 0.05, 0.5, 0.1, 100.0]
    table.loc[M.REGION_CODE, shares] = [0.45, 0.025, 0.3, 0.05, 900.0]
    # the committed file carries Wolfsburg as a zero row with NaN shares
    table.loc["03103", shares] = [np.nan, np.nan, np.nan, np.nan, 0.0]
    return table.reset_index()


def _srv_status():
    rows = [{"code": code, "level": "total" if code == M.REGION_CODE else "kreis",
             "status": status, "n_unweighted": 10, "weight_sum": 10.0, "share": 0.3,
             "n_effective": 9.0, "se_share": 0.1}
            for code in list(M.ZGB_KREISE) + [M.REGION_CODE] for status in YOUNG_ADULT_STATUSES]
    table = pd.DataFrame(rows, columns=YOUNG_ADULT_STATUS_COLUMNS)
    mask = (table["code"] == "03101") & (table["status"] == "employed")
    table.loc[mask, ["share", "se_share"]] = [0.5, 0.1]
    return table


def _zensus(drop_kreise=()):
    """Employed persons by Kreis, age class and sex in the shape ``read_kreis_employed_by_age``
    returns; only the 20-29 rows count, the 15-19, 30-39 and Germany rows must be ignored."""
    employed_20_29 = {"03101": (100, 120), "03102": (50, 40), "03103": (30, 20)}
    rows = [("00", "20-29", "M", 10 ** 6), ("00", "20-29", "F", 10 ** 6)]
    for code in M.ZGB_KREISE:
        male, female = employed_20_29.get(code, (10, 10))
        rows += [(code, "20-29", "M", male), (code, "20-29", "F", female),
                 (code, "15-19", "M", 1000), (code, "30-39", "F", 1000)]
    frame = pd.DataFrame(rows, columns=["region", "age_class", "sex", "employed_persons"])
    frame.insert(1, "region_name", "x")
    return frame[~frame["region"].isin(drop_kreise)].reset_index(drop=True)


def test_level_table_by_hand():
    table, diag = M.student_level_table(_level_persons(), _srv_shares(), SAMPLING_RATE)
    assert list(table.columns) == M.LEVEL_COLUMNS
    assert list(table["code"]) == list(M.ZGB_KREISE) + [M.REGION_CODE]
    rows = table.set_index("code")
    # 03101 over all persons: adults 1-6 (8 has no readable P_TAET, 7 is 12); students 22, 19 |
    # 35; 20-29: persons 1, 4, 5, of them student 1
    bs = rows.loc["03101"]
    assert (bs["model_adults"], bs["model_students_18_29"], bs["model_students_30plus"],
            bs["model_persons_20_29"], bs["model_students_20_29"]) == (6, 2, 1, 3, 1)
    assert bs["model_students_expanded_persons"] == pytest.approx(12.0)
    # shares over the PRESENT persons only: student 1 is away all day, so 2 of 5 adults and 0 of
    # the 2 present 20-29 year olds (over all persons they would be 3/6 and 1/3)
    assert (bs["model_present_adults"], bs["model_present_students_adults"],
            bs["model_present_persons_20_29"], bs["model_present_students_20_29"]) == (5, 2, 2, 0)
    assert bs["model_student_share_adults"] == pytest.approx(0.4)
    assert bs["model_student_share_20_29"] == pytest.approx(0.0)
    assert bs["difference_in_se_adults"] == pytest.approx(0.0)
    assert bs["difference_in_se_20_29"] == pytest.approx((0.0 - 0.5) / 0.1)
    # region = the seven surveyed Kreise: 03101 + 03102; Wolfsburg and no-Kreis student 12 out
    region = rows.loc[M.REGION_CODE]
    assert region["level"] == "total"
    assert (region["model_adults"], region["model_students_18_29"],
            region["model_persons_20_29"], region["model_students_20_29"]) == (8, 3, 5, 2)
    assert region["model_students_expanded_persons"] == pytest.approx(16.0)
    assert region["difference_in_se_adults"] == pytest.approx((3 / 7 - 0.45) / 0.025)
    assert region["difference_in_se_20_29"] == pytest.approx((1 / 4 - 0.3) / 0.05)
    assert region["srv_students_weighted_persons"] == pytest.approx(900.0)
    # Wolfsburg: model only, every SrV column NaN (the file's 0 weight sum included)
    wolfsburg = rows.loc["03103"]
    assert (wolfsburg["model_adults"], wolfsburg["model_student_share_adults"]) == (1, 1.0)
    srv_columns = [column for column in M.LEVEL_COLUMNS if column.startswith(("srv_", "diff"))]
    assert wolfsburg[srv_columns].isna().all()
    # a surveyed Kreis without a model person: zero counts, NaN shares
    empty = rows.loc["03158"]
    assert empty["model_adults"] == 0 and pd.isna(empty["model_student_share_adults"])
    # exclusions reach the diagnostics: P_TAET over the 11 adults, home Kreis over the 10 left
    assert (diag["n_adults_with_age"], diag["n_adults_unreadable_p_taet"],
            diag["n_adults_no_home_kreis"], diag["n_missing_srv_rows"]) == (11, 1, 1, 0)
    # a surveyed Kreis missing from the SrV table: NaN SrV columns there, counted
    srv = _srv_shares()
    reduced, diag = M.student_level_table(_level_persons(), srv[srv["code"] != "03102"],
                                          SAMPLING_RATE)
    assert diag["missing_srv_codes"] == ["03102"] and diag["n_missing_srv_rows"] == 1
    assert pd.isna(reduced.set_index("code").loc["03102", "srv_student_share_adults"])


def test_young_adult_status_and_work_class_by_hand():
    table, diag = M.young_adult_status_table(_level_persons(), _srv_status(), _zensus(),
                                             SAMPLING_RATE)
    assert list(table.columns) == M.YOUNG_ADULT_COLUMNS
    rows = table.set_index(["code", "status"])
    # 03101, ages 20-29, all persons: student 1 (away all day), employed 4, other 5
    for status in ("student", "employed", "other"):
        assert rows.loc[("03101", status), "model_n"] == 1
        assert rows.loc[("03101", status), "model_expanded_persons"] == pytest.approx(4.0)
    # shares over the present ones: employed 4 and other 5
    assert rows.loc[("03101", "student"), ["model_present_n", "model_share"]].tolist() == [0, 0.0]
    assert rows.loc[("03101", "employed"), "model_share"] == pytest.approx(1 / 2)
    assert rows.loc[("03101", "employed"), "difference_in_se"] == pytest.approx(
        (1 / 2 - 0.5) / 0.1)
    # 03102: student 9, employed apprentice 10; region present: student 9, employed 4 and 10,
    # other 5 (student 1 away)
    assert rows.loc[("03102", "other"), "model_n"] == 0
    assert rows.loc[(M.REGION_CODE, "student"), "model_n"] == 2
    assert rows.loc[(M.REGION_CODE, "student"), "model_share"] == pytest.approx(1 / 4)
    assert rows.loc[(M.REGION_CODE, "employed"), "model_share"] == pytest.approx(1 / 2)
    # Zensus employed 20-29, both sexes summed, on the employed rows only; region = the seven
    # surveyed Kreise (220 + 90 + 5 * 20), Wolfsburg keeps its own count
    zensus = rows["zensus_employed_persons"]
    assert zensus[("03101", "employed")] == pytest.approx(220.0)
    assert zensus[("03103", "employed")] == pytest.approx(50.0)
    assert zensus[(M.REGION_CODE, "employed")] == pytest.approx(410.0)
    assert zensus[rows.index.get_level_values("status") != "employed"].isna().all()
    assert rows.loc[("03103", "student"), ["srv_share", "srv_se_share",
                                          "difference_in_se"]].isna().all()
    assert (diag["n_young_adults"], diag["n_zensus_missing_kreise"]) == (6, 0)
    # a Kreis missing from the Zensus frame: NaN there and on the region row, counted
    missing, diag = M.young_adult_status_table(_level_persons(), _srv_status(),
                                               _zensus(["03158"]), SAMPLING_RATE)
    missing = missing.set_index(["code", "status"])
    assert pd.isna(missing.loc[("03158", "employed"), "zensus_employed_persons"])
    assert pd.isna(missing.loc[(M.REGION_CODE, "employed"), "zensus_employed_persons"])
    assert missing.loc[("03101", "employed"), "zensus_employed_persons"] == pytest.approx(220.0)
    assert diag["zensus_missing_kreise"] == ["03158"]

    work, diag = M.student_work_class_table(_level_persons())
    assert list(work.columns) == M.WORK_CLASS_COLUMNS
    # adult students 1, 2, 3, 9, 11, 12: student 3 is employed (reported, not dropped), the
    # non-employed student 2 sits in an employed class of the work control (reported too)
    cells = work.set_index(["employed", "employment_status"])
    assert cells.loc[(False, "nicht_erwerbstaetig"), "model_n"] == 4
    assert cells.loc[(False, "geringfuegig"), "model_n"] == 1
    assert cells.loc[(True, "teilzeit"), "share"] == pytest.approx(1 / 6)
    assert work["model_n"].sum() == 6
    assert (diag["n_adult_students_employed"],
            diag["n_adult_students_not_employed_in_employed_class"]) == (1, 1)


def _set_value(frame_name, select, column, value):
    """Set ``column`` of the rows ``select(frame)`` picks in the named input frame."""
    def mutate(inputs):
        frame = inputs[frame_name].astype({column: object})
        frame.loc[select(frame), column] = value
        return {**inputs, frame_name: frame}
    return mutate


def _change(frame_name, change):
    def mutate(inputs):
        return {**inputs, frame_name: change(inputs[frame_name])}
    return mutate


def _sampling_rate(rate):
    def mutate(inputs):
        return {**inputs, "rate": rate}
    return mutate


def _one_20_29_row(zensus, kreis, sex):
    return (zensus["region"] == kreis) & (zensus["age_class"] == "20-29") & (zensus["sex"] == sex)


def _person(person_id):
    return lambda frame: frame["person_id"] == person_id


def _code(code):
    return lambda frame: frame["code"] == code


_LEVEL = "student_level_table"
_STATUS = "young_adult_status_table"
_BAD_INPUTS = [
    # (target, mutate, match): the persons and the rate reach both tables, the SrV frames and
    # the Zensus frame their own table only
    *[(target, mutate, match) for target in (_LEVEL, _STATUS) for mutate, match in [
        (_set_value("persons", _person(2), "kreis", "03241"), "ZGB_KREISE"),  # Region Hannover
        (_set_value("persons", _person(2), "kreis", 3101), "non-string"),
        (_sampling_rate(0.0), "sampling_rate"),
        (_sampling_rate(1.5), "sampling_rate"),
    ]],
    (_LEVEL, _set_value("srv_shares", _code("03102"), "code", 3102), "non-string"),
    (_LEVEL, _set_value("srv_shares", _code("03102"), "code", "03241"), "outside"),
    (_STATUS, _set_value("srv_status", _code("03102"), "code", 3102), "non-string"),
    (_STATUS, _set_value("srv_status", _code("03102"), "code", "03241"), "outside"),
    (_STATUS, _set_value("srv_status", lambda f: f["status"] == "other", "status", "retired"),
     "outside"),
    # more or fewer than one Zensus row per sex
    (_STATUS, _change("zensus", lambda z: z[~_one_20_29_row(z, "03101", "F")]), "sexes"),
    (_STATUS, _change("zensus", lambda z: pd.concat([z, z[_one_20_29_row(z, "03101", "M")]])),
     "sexes"),
    # region read as numbers: no Kreis would be found
    (_STATUS, _change("zensus", lambda z: z.assign(region=z["region"].astype(int))),
     "non-string"),
    # five of the eight ZGB Kreise missing (62.5 %) is above the 50 % limit
    (_STATUS, _change("zensus", lambda z: z[~z["region"].isin(M.ZGB_KREISE[:5])]), "Zensus"),
]


@pytest.mark.parametrize("target, mutate, match", _BAD_INPUTS)
def test_invalid_level_and_status_inputs_raise(target, mutate, match):
    inputs = mutate({"persons": _level_persons(), "srv_shares": _srv_shares(),
                     "srv_status": _srv_status(), "zensus": _zensus(), "rate": SAMPLING_RATE})
    with pytest.raises(ValueError, match=match):
        if target == _LEVEL:
            M.student_level_table(inputs["persons"], inputs["srv_shares"], inputs["rate"])
        else:
            M.young_adult_status_table(inputs["persons"], inputs["srv_status"],
                                       inputs["zensus"], inputs["rate"])


# Seed key (H_ID, P_ID), plan source and attribute source of each student. Student 1 keeps its
# own diary; student 5 is a PopulationSim replicate of the same seed person; student 2 is
# re-drawn by the weekend match from a student donor; student 3 is re-drawn by the diary match
# from an employed donor; student 4 is re-drawn from a donor missing from the donor persons and
# reaches no trace row that names a path.
_PLAN_PERSONS = [
    # person_id, H_ID, P_ID, source_H_ID, source_P_ID, attribute_source_H_ID, attribute_source_P_ID
    (1, 100, 1, 100, 1, 100, 1),
    (5, 100, 1, 100, 1, 100, 1),
    (2, 200, 1, 300, 1, 200, 1),
    (3, 400, 1, 500, 2, 400, 1),
    (4, 600, 1, 700, 1, 600, 1),
]
DIARY_REASON = "nodiary_mobile"


def _plan_frames(unknown_donors=()):
    persons = pd.DataFrame(_PLAN_PERSONS, columns=[
        "person_id", "H_ID", "P_ID", "source_H_ID", "source_P_ID", "attribute_source_H_ID",
        "attribute_source_P_ID"])
    students = pd.DataFrame({"person_id": [1, 5, 2, 3, 4],
                             "present": [True, False, True, True, True],
                             "education_leg": ["yes", "no", "yes", "no", "no"],
                             "work_leg": ["no", "no", "no", "yes", "no"]})
    # donor persons and traces carry H_ID as float64, as the production frames do
    donors = pd.DataFrame({"H_ID": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
                           "P_ID": [1, 1, 1, 1, 2, 1], "P_TAET": [10, 10, 10, 10, 1, 10]})
    donors = donors[~donors["H_ID"].isin(unknown_donors)].reset_index(drop=True)
    weekend = pd.DataFrame({"H_ID": [100.0, 200.0, 400.0], "P_ID": [1, 1, 1],
                            "resolution": ["own_plan", "hh_match", "own_plan"],
                            "match_level": [np.nan, 1.0, np.nan],
                            "plan_source_H_ID": [100.0, 300.0, 400.0],
                            "plan_source_P_ID": [1, 1, 1]})
    diary = pd.DataFrame({"H_ID": [100.0, 200.0, 400.0, 600.0], "P_ID": [1, 1, 1, 1],
                          "reason": ["realisable", "nodiary_immobile_keep", DIARY_REASON,
                                     "realisable"],
                          "match_level": [np.nan, np.nan, 2.0, np.nan],
                          "plan_source_H_ID": [100.0, 200.0, 500.0, 700.0],
                          "plan_source_P_ID": [1, 1, 2, 1]})
    return students, persons, donors, weekend, diary


def test_plan_source_by_hand():
    table, diag = M.plan_source_table(*_plan_frames())
    assert list(table.columns) == M.PLAN_SOURCE_COLUMNS
    row = table.set_index(["plan_origin", "match_path", "donor_status"])
    assert row.loc[("redrawn", "weekend:hh_match", "student"), "model_n"] == 1
    assert row.loc[("redrawn", "diary:" + DIARY_REASON, "employed"), "model_n"] == 1
    assert row.loc[("redrawn", "diary:" + DIARY_REASON, "employed"),
                   "share_with_work_leg"] == pytest.approx(1.0)
    # the own-diary row holds the seed person and its replicate; leg shares over present ones
    own = row.loc[("own_diary", "weekend:own_plan", "student")]
    assert own["model_n"] == 2 and own["share_of_students"] == pytest.approx(2 / 5)
    assert own["share_with_education_leg"] == pytest.approx(1.0)
    # student 4: no weekend row, a diary row it keeps -> unresolved; its donor is unknown
    assert row.loc[("redrawn", "unresolved", "unknown"), "model_n"] == 1
    assert diag["n_redrawn_donor_unknown"] == 1
    assert (diag["n_redrawn"], diag["n_redrawn_unresolved"]) == (3, 1)
    assert table["model_n"].sum() == 5


def test_donor_without_readable_p_taet_is_unknown():
    students, persons, donors, weekend, diary = _plan_frames()
    # student 4's donor is found this time (a student); the weekend donor of student 2 answers
    # P_TAET 99: 1 of 3 re-drawn plans has an unknown donor, below the limit
    donors = pd.concat([donors, pd.DataFrame({"H_ID": [700.0], "P_ID": [1], "P_TAET": [10]})],
                       ignore_index=True)
    donors.loc[donors["H_ID"] == 300.0, "P_TAET"] = 99
    table, diag = M.plan_source_table(students, persons, donors, weekend, diary)
    row = table.set_index(["plan_origin", "match_path", "donor_status"])
    assert row.loc[("redrawn", "weekend:hh_match", "unknown"), "model_n"] == 1
    assert row.loc[("redrawn", "unresolved", "student"), "model_n"] == 1
    assert (diag["n_redrawn_donor_p_taet_unreadable"], diag["n_redrawn_donor_not_found"],
            diag["n_redrawn_donor_unknown"]) == (1, 0, 1)


def test_plan_source_without_seed_key_is_unresolved():
    students, persons, donors, weekend, diary = _plan_frames()
    table, diag = M.plan_source_table(students, persons.drop(columns=["H_ID", "P_ID"]), donors,
                                      weekend, diary)
    assert set(table["match_path"]) == {"unresolved"}
    assert diag["n_redrawn_unresolved"] == 3 and "H_ID" in diag["unresolved_reason"]


def _trace_change(position, change):
    def mutate(frames):
        frames = list(frames)
        frames[position] = change(frames[position].copy())
        return frames
    return mutate


def _set_first(column, value):
    def change(frame):
        frame.loc[frame.index[0], column] = value
        return frame
    return change


@pytest.mark.parametrize("mutate, match", [
    # donors of students 3 and 4 missing: 2 of 3 re-drawn plans (67 %) above the 50 % limit
    (lambda frames: _plan_frames(unknown_donors=(500.0,)), "donor"),
    (_trace_change(3, lambda weekend: pd.concat([weekend, weekend.iloc[[0]]])), "duplicated"),
    (_trace_change(4, lambda diary: pd.concat([diary, diary.iloc[[1]]])), "duplicated"),
    (_trace_change(4, _set_first("reason", "lost_diary")), "reason"),
    (_trace_change(3, _set_first("resolution", "best_guess")), "resolution"),
    (_trace_change(2, _set_first("H_ID", 100.5)), "integer key"),
])
def test_invalid_plan_source_inputs_raise(mutate, match):
    with pytest.raises(ValueError, match=match):
        M.plan_source_table(*mutate(_plan_frames()))


def _resolution_assignments(source: str) -> tuple[set, list]:
    """Values assigned to ``resolution`` in ``source``: ``(string literals, other right-hand
    sides)``. Targets: ``resolution``, ``resolution[...]``, ``resolution.loc/.at/.iloc/.iat[...]``;
    a call such as ``pd.Series("x", index=...)`` contributes its first argument."""
    literals, others = set(), []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(_is_resolution_target(target) for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            literals.add(value.value)
        else:
            others.append(ast.unparse(node))
    return literals, others


def _is_resolution_target(target) -> bool:
    if isinstance(target, ast.Subscript):
        target = target.value
    if isinstance(target, ast.Attribute) and target.attr in ("loc", "at", "iloc", "iat"):
        target = target.value
    return isinstance(target, ast.Name) and target.id == "resolution"


@pytest.mark.parametrize("snippet, literals, others", [
    ('resolution = pd.Series("a", index=persons.index)', {"a"}, 0),
    ('resolution[persons["x"].to_numpy()] = "b"', {"b"}, 0),
    ('resolution.loc[mask] = "c"', {"c"}, 0),
    ('resolution.at[3] = "d"', {"d"}, 0),
    ('resolution.iloc[ridx] = "e"', {"e"}, 0),
    ('resolution[ridx] = RESOLUTION_HH_MATCH', set(), 1),
    ('resolution = np.where(mask, "f", "g")', set(), 1),
    ('other[ridx] = "h"', set(), 0),
])
def test_resolution_scan_catches_every_assignment_idiom(snippet, literals, others):
    # positive controls of the scan below: each idiom is caught, a non-literal is surfaced
    found, rest = _resolution_assignments(snippet)
    assert (found, len(rest)) == (literals, others)


def test_weekend_resolutions_are_the_literals_the_weekend_match_writes():
    # weekend_plan_match keeps its resolutions as inline literals; this pins the measurement's
    # vocabulary to every value assigned to `resolution` there. A non-literal right-hand side
    # (e.g. a module constant) fails: then import the constants in student_measurement instead.
    import braunschweig.popsim.weekend_plan_match as W
    literals, others = _resolution_assignments(Path(W.__file__).read_text(encoding="utf-8"))
    assert others == [], "non-literal resolution assignment(s): import the constants instead"
    assert literals == set(M.WEEKEND_RESOLUTIONS)
