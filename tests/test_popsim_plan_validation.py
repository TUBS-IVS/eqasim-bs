from __future__ import annotations
import pandas as pd
from braunschweig.popsim import plan_validation as pv


def _good_trips():
    # one person, two trips: home(8:00)->work(8:30..17:00)->home(17:20)
    return pd.DataFrame({
        "person_id": ["p", "p"],
        "departure_time": [8*3600, 17*3600],
        "arrival_time": [8*3600+1800, 17*3600+1200],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"],
        "is_first_trip": [True, False],
        "is_last_trip": [False, True],
    })


def test_validator_passes_clean_plan():
    report = pv.PlanValidator().validate_trips(_good_trips())
    assert report.n_invalid == 0
    assert report.is_valid


def test_validator_flags_departure_after_arrival():
    df = _good_trips()
    df.loc[0, "arrival_time"] = df.loc[0, "departure_time"] - 60  # arrival before departure
    report = pv.PlanValidator().validate_trips(df)
    assert not report.is_valid
    assert any(i.code == "departure_after_arrival" for i in report.issues)
    assert "p" in {i.person_id for i in report.issues}


def test_validator_flags_overlap_with_next_trip():
    df = _good_trips()
    # make trip 0 arrive AFTER trip 1 departs -> overlap
    df.loc[0, "arrival_time"] = 18 * 3600
    report = pv.PlanValidator().validate_trips(df)
    assert any(i.code == "trip_overlap" for i in report.issues)


def test_validator_flags_missing_home_closure():
    df = _good_trips()
    df.loc[0, "preceding_purpose"] = "work"   # day does NOT start at home
    report = pv.PlanValidator(require_home_closure=True).validate_trips(df)
    assert any(i.code == "no_home_start" for i in report.issues)


def test_validator_flags_no_home_end():
    df = _good_trips()
    df.loc[1, "following_purpose"] = "leisure"  # day does NOT end at home
    report = pv.PlanValidator(require_home_closure=True).validate_trips(df)
    assert any(i.code == "no_home_end" for i in report.issues)


def test_home_closure_check_can_be_disabled():
    df = _good_trips()
    df.loc[0, "preceding_purpose"] = "work"
    report = pv.PlanValidator(require_home_closure=False).validate_trips(df)
    assert not any(i.code in {"no_home_start", "no_home_end"} for i in report.issues)


# ---------------------------------------------------------------------------
# Task 5: repair_trips tests
# ---------------------------------------------------------------------------

def test_repair_fixes_overlaps_and_classifies():
    df = _good_trips()
    df.loc[0, "arrival_time"] = 18 * 3600   # overlap (arrives after next departs)
    validator = pv.PlanValidator(require_home_closure=False)
    fixed, report = validator.repair_trips(df)
    after = validator.validate_trips(fixed)
    assert not any(i.code in {"trip_overlap", "departure_after_arrival",
                              "negative_activity_duration"} for i in after.issues)
    assert report.n_repaired >= 1
    assert "p" in report.repaired_persons


def test_home_closure_repair_appends_return_home():
    df = pd.DataFrame({
        "person_id": ["p"], "departure_time": [8*3600], "arrival_time": [8*3600+1800],
        "preceding_purpose": ["home"], "following_purpose": ["work"],
        "is_first_trip": [True], "is_last_trip": [True], "mode": ["car"],
    })
    validator = pv.PlanValidator(require_home_closure=True)
    fixed, report = validator.repair_trips(df)
    person = fixed[fixed["person_id"] == "p"].sort_values("departure_time")
    assert person.iloc[-1]["following_purpose"] == "home"   # day now ends at home
    assert len(person) == 2                                  # a return-home trip was appended


def test_repair_report_all_valid_on_clean_home_closed_plan():
    # _good_trips() already starts and ends at home; repair should leave it unchanged.
    df = _good_trips()
    validator = pv.PlanValidator(require_home_closure=True)
    fixed, report = validator.repair_trips(df)
    assert report.n_valid == 1
    assert report.n_repaired == 0
    assert report.n_unfixable == 0
    # no extra trip should be appended
    person = fixed[fixed["person_id"] == "p"]
    assert len(person) == 2
