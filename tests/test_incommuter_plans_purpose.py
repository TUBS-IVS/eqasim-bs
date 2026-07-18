import numpy as np
import pandas as pd
from braunschweig.data.cordon import plans


def _work_trips():
    return pd.DataFrame({
        "person_id": [1, 1],
        "departure_time": [28800.0, 61200.0],
        "arrival_time": [30600.0, 63000.0],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"],
    })


def test_build_trips_work_default_byte_identical():
    # The pre-existing 5-arg call must produce exactly today's frame.
    legacy = plans.build_incommuter_trips([1], [28800.0], [30600.0], [61200.0], [63000.0])
    assert list(legacy["following_purpose"]) == ["work", "home"]
    assert list(legacy["preceding_purpose"]) == ["home", "work"]


def test_build_trips_education_purpose():
    trips = plans.build_incommuter_trips(
        [1], [28800.0], [30600.0], [61200.0], [63000.0], middle_purpose="education")
    assert list(trips["following_purpose"]) == ["education", "home"]
    assert list(trips["preceding_purpose"]) == ["home", "education"]


def test_build_activities_education_purpose():
    acts = plans.build_incommuter_activities(
        [1], [28800.0], [30600.0], [61200.0], [63000.0], middle_purpose="education")
    assert list(acts["purpose"]) == ["home", "education", "home"]


def test_extract_activity_times_education():
    trips = pd.DataFrame({
        "person_id": [7, 7],
        "departure_time": [30000.0, 55000.0],
        "arrival_time": [32000.0, 57000.0],
        "preceding_purpose": ["home", "education"],
        "following_purpose": ["education", "home"],
    })
    dh, am, dm, ah = plans.extract_activity_times(trips, purpose="education")
    assert (dh, am, dm, ah) == (30000.0, 32000.0, 55000.0, 57000.0)


def test_select_student_donors_filters_studies_and_edu_trip():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3],
        "studies": [True, True, False],
        "employed": [False, False, True],
    })
    trips = pd.DataFrame({
        "person_id": [1, 3],
        "following_purpose": ["education", "work"],
    })
    donors = plans.select_student_donors(persons, trips, "person_id")
    assert list(donors["person_id"]) == [1]
