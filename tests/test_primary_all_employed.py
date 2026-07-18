"""Tests for the all-employed primary-location demand selection (#203, Approach A).

The production location assignment restricts work/education primary locations to
persons with a work/education trip on the reference day (``has_<purpose>_trip``).
Approach A additionally assigns a primary location to ALL employed / studying
persons (so the VerBindungen commuter-OD universe matches the QZM "all workers"
reference and a workplace/education-place export is complete), while keeping the
trip-haver assignment byte-identical via a separate second pass.

These are pure-function tests for the demand-partitioning helper only: no synpp
context, no matsim import, no real data.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pandas as pd

from synthesis.population.spatial.primary.candidates import select_all_employed_extra


def _persons():
    # person 1: employed, has work trip      -> pass 1 (NOT extra)
    # person 2: employed, no work trip        -> pass 2 extra (work)
    # person 3: not employed, no work trip     -> neither
    # person 4: not employed, has work trip     -> pass 1 (retiree travelling to a job site etc.)
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "employed": [True, True, False, False],
        "studies": [False, False, False, False],
        "has_work_trip": [True, False, False, True],
        "has_education_trip": [False, False, False, False],
    })


def test_extra_work_persons_are_employed_without_work_trip():
    """Pass-2 extra work demand = employed AND NOT has_work_trip."""
    extra = select_all_employed_extra(_persons(), "work", "employed")
    assert list(extra["person_id"]) == [2], (
        "Only the employed person without a work trip should be an extra; got %s"
        % list(extra["person_id"]))


def test_extra_excludes_persons_already_having_the_trip():
    """A person with the trip is handled by pass 1 and is never an extra
    (even if the employment flag is set) -- no double assignment."""
    extra = select_all_employed_extra(_persons(), "work", "employed")
    assert 1 not in set(extra["person_id"])


def test_extra_studies_uses_the_studies_flag_and_education_trip():
    df = _persons().copy()
    df["studies"] = [False, False, True, False]  # person 3 studies, no education trip
    extra = select_all_employed_extra(df, "education", "studies")
    assert list(extra["person_id"]) == [3]


def test_extra_empty_when_no_uncovered_employed():
    """If every employed person already has the trip, there are no extras."""
    df = _persons().copy()
    df["has_work_trip"] = [True, True, False, True]  # both employed now have a trip
    extra = select_all_employed_extra(df, "work", "employed")
    assert len(extra) == 0
