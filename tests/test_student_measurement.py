"""Tests of the model-side student harmonisation and the comparison with SrV (#140, Phase 1).

Every expected value is computed by hand on a few synthetic persons; the SrV side is a small
synthetic table in ``student_parameters.PARAMETER_COLUMNS``, never the committed file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import student_measurement as M
from braunschweig.calibration import student_parameters as SP
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
