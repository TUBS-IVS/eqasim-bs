"""Tests for the residual split of the passenger access key measurement (ADR-0162, eqasim-bs#465)."""
from __future__ import annotations

import pandas as pd
import pytest

from scripts import measure_donor_match_passenger_access as measurement


def _persons() -> pd.DataFrame:
    """Six "never" persons, one per path/donor-class combination, plus controls that must not be counted."""
    return pd.DataFrame({
        "H_ID":        [1, 2, 3, 4, 5, 6, 7, 8, 9],
        "P_ID":        [1, 1, 1, 1, 1, 1, 1, 1, 1],
        "P_VAUTO":     [3, 3, 3, 3, 3, 3, 3, 1, 3],
        "member_imputed": [False] * 9,
        # 1: hh alignment from a "sometimes" donor; 2: hh alignment from a "never" donor with a lift;
        # 3: diary re-match from a "never" donor; 4: person fallback from a "sometimes" donor;
        # 5: weekend hh alignment, then diary re-match (the diary match is final); 6: own diary with a lift;
        # 7: foreign diary without a lift; 8: not "never"; 9: filler copy of its mirror
        "source_H_ID": [20, 21, 21, 20, 21, 6, 20, 20, 21],
        "source_P_ID": [1, 1, 1, 1, 1, 1, 1, 1, 1],
        "src_has_car_passenger_trip": [True, True, True, True, True, True, False, True, True],
    })


def _donor_class() -> pd.Series:
    """Passenger class of the plan-source respondents: (20, 1) sometimes, (21, 1) never."""
    return pd.Series([False, True], index=pd.MultiIndex.from_tuples([(20, 1), (21, 1)]))


def _weekend_trace() -> pd.DataFrame:
    return pd.DataFrame({"H_ID": range(1, 10), "P_ID": [1] * 9,
                         "resolution": ["hh_match", "hh_match", "own_plan", "person_fallback", "hh_match",
                                        "own_plan", "hh_match", "hh_match", "member_completion_filler"]})


def _diary_trace() -> pd.DataFrame:
    return pd.DataFrame({"H_ID": range(1, 10), "P_ID": [1] * 9,
                         "reason": ["realisable", "realisable", "nodiary_mobile", "realisable", "holiday",
                                    "realisable", "realisable", "realisable", "realisable"]})


def test_residual_is_split_by_path_and_donor_class():
    split = measurement.split_never_foreign_lift(_persons(), _donor_class(), _weekend_trace(), _diary_trace())

    assert split == {
        "weekend_hh_alignment": {"crossed_class": 1, "same_class_donor_lift": 1},
        "weekend_person_match": {"crossed_class": 1, "same_class_donor_lift": 0},
        "diary_rematch": {"crossed_class": 0, "same_class_donor_lift": 2},
        "member_completion_filler": {"crossed_class": 0, "same_class_donor_lift": 1},
        "other": {"crossed_class": 0, "same_class_donor_lift": 0},
        "total": {"crossed_class": 2, "same_class_donor_lift": 4},
    }


def test_residual_split_refuses_a_trace_that_is_not_row_aligned():
    trace = _weekend_trace().iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="not row-aligned"):
        measurement.split_never_foreign_lift(_persons(), _donor_class(), trace, _diary_trace())


def test_residual_split_refuses_an_unresolved_plan_source():
    persons = _persons()
    persons.loc[0, "source_H_ID"] = 99
    with pytest.raises(ValueError, match="do not resolve"):
        measurement.split_never_foreign_lift(persons, _donor_class(), _weekend_trace(), _diary_trace())
