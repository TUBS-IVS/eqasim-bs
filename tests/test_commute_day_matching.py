"""Unit tests for braunschweig.synthesis.commute_day.matching (Phase B Task 3, issue #244).

Synthetic frames only. Covers hard-criteria enforcement (never coarsened), the soft-criteria
coarsening ladder (has_license -> household_size_class -> age_class -> sex -> distance widened ->
any distance), determinism under a seeded RNG, and the has_car-unknown hard-exclusion diagnostic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.commute_day import matching


def _persons_home_fixture():
    # p1: wants a female, age_class 1, household_size 2, distance "10_25" donor -- d1 matches
    #     it exactly (level 0).
    # p2: no escort, no children, has_car True; d3 would be an exact match except its has_car is
    #     NaN (hard-excluded), so p2 is only reachable via d2, which differs only in sex --
    #     coarsening level 4.
    # p3: escort duty, but the donor pool has NO escort-duty donor at all -> never replaceable,
    #     regardless of coarsening (hard criterion, never coarsened).
    return pd.DataFrame({
        "person_id":            ["p1", "p2", "p3"],
        "assigned_distance_class": ["10_25", "25_50", "lt10"],
        "sex":                  ["female", "male", "male"],
        "age_class":            [1, 2, 3],
        "household_size":       [2, 3, 1],
        "has_license":          [True, True, True],
        "has_active_escort":    [False, False, True],
        "has_children_u14":     [False, False, False],
        "has_car":              [True, True, True],
    })


def _donors_fixture():
    # d1: matches p1 exactly on every soft criterion (level 0).
    # d2: matches p2's hard criteria and every soft criterion EXCEPT sex (female vs p2's male);
    #     only reachable once sex is dropped, i.e. coarsening level 4.
    # d3: would be an EXACT match for p2 at level 0 (same sex/age_class/household_size/distance),
    #     but has_car is NaN (household unmatched) -- must never satisfy the has_car hard
    #     criterion, so p2 falls through to d2 at level 4 instead.
    # No donor anywhere has has_active_escort=True, so p3 (escort duty) can never be matched.
    return pd.DataFrame({
        "donor_id":             ["d1", "d2", "d3"],
        "distance_class":       ["10_25", "25_50", "25_50"],
        "sex":                  ["female", "female", "male"],
        "age_class":            [1, 2, 2],
        "household_size":       [2, 3, 3],
        "has_license":          [True, True, True],
        "has_active_escort":    [False, False, False],
        "has_children_u14":     [False, False, False],
        "has_car":              [True, True, np.nan],
    })


def test_hard_criterion_escort_is_never_coarsened():
    persons_home = _persons_home_fixture()
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    assert "p3" not in set(matches["person_id"])
    assert diagnostics["n_not_replaceable"] == 1
    assert diagnostics["share_not_replaceable"] == pytest.approx(1.0 / 3.0)


def test_exact_match_is_recorded_at_coarsening_level_zero():
    persons_home = _persons_home_fixture()
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1"
    assert row["coarsening_level"] == 0
    assert diagnostics["matched_by_level"][0] == 1


def test_coarsening_reaches_level_four_when_only_sex_differs():
    persons_home = _persons_home_fixture()
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    row = matches.set_index("person_id").loc["p2"]
    assert row["donor_id"] == "d2"
    assert row["coarsening_level"] == 4
    assert diagnostics["matched_by_level"][4] == 1


def test_donor_with_unknown_has_car_never_hard_matches():
    # d3 has has_car=NaN; even though it otherwise matches p2 except for sex (same as d2), it
    # must never be selected because the has_car hard criterion can never be satisfied by NaN.
    persons_home = _persons_home_fixture()
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    assert "d3" not in set(matches["donor_id"])
    assert diagnostics["n_donors_hard_excluded_has_car_unknown"] == 1


def test_distance_widening_at_level_five_accepts_adjacent_rank():
    # A single donor pool with only an adjacent-rank donor: exact distance match (levels 0-4)
    # never succeeds, but the level-5 one-rank widening picks it up.
    persons_home = pd.DataFrame({
        "person_id":            ["p1"],
        "assigned_distance_class": ["25_50"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    donors = pd.DataFrame({
        "donor_id":             ["d1"],
        "distance_class":       ["10_25"],  # adjacent rank below "25_50", not exact.
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1"
    assert row["coarsening_level"] == 5


def test_unknown_distance_donor_qualifies_only_from_level_five_on():
    persons_home = pd.DataFrame({
        "person_id":            ["p1"],
        "assigned_distance_class": ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    donors = pd.DataFrame({
        "donor_id":             ["d1"],
        "distance_class":       ["unknown"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1"
    assert row["coarsening_level"] == 5


def test_missing_has_license_column_is_skipped_not_invented():
    persons_home = _persons_home_fixture().drop(columns=["has_license"])
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(persons_home, donors, rng)

    assert "has_license" not in diagnostics["soft_criteria_used"]
    # p1 must still match d1 exactly at level 0 -- has_license absence changes nothing else.
    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1"
    assert row["coarsening_level"] == 0


def test_determinism_with_seeded_rng():
    # A cell with two equally-eligible donors: the choice must be reproducible across repeated
    # calls with an identically-seeded RNG.
    persons_home = pd.DataFrame({
        "person_id":            ["p1"],
        "assigned_distance_class": ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    donors = pd.DataFrame({
        "donor_id":             ["d1", "d2"],
        "distance_class":       ["lt10", "lt10"],
        "sex":                  ["male", "male"],
        "age_class":            [2, 2],
        "household_size":       [2, 2],
        "has_active_escort":    [False, False],
        "has_children_u14":     [False, False],
        "has_car":              [True, True],
    })
    matches_a, _ = matching.match_home_office_donors(
        persons_home, donors, np.random.RandomState(7))
    matches_b, _ = matching.match_home_office_donors(
        persons_home, donors, np.random.RandomState(7))
    assert matches_a.equals(matches_b)


def test_minimum_cell_requires_more_donors_than_available():
    persons_home = pd.DataFrame({
        "person_id":            ["p1"],
        "assigned_distance_class": ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    donors = pd.DataFrame({
        "donor_id":             ["d1"],
        "distance_class":       ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    rng = np.random.RandomState(0)
    matches, diagnostics = matching.match_home_office_donors(
        persons_home, donors, rng, minimum_cell=2)
    assert len(matches) == 0
    assert diagnostics["n_not_replaceable"] == 1
