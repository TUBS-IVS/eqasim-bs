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


def _match(persons_home, donors, rng, **kwargs):
    """Call the matcher, defaulting the ruling-R7 education columns a fixture does not set.

    ``match_home_office_donors`` REQUIRES ``has_education_location`` on the person side and
    ``has_education_leg`` on the donor side: an absent column must never silently disable a hard
    criterion (CLAUDE.md "Fallback transparency"). The fixtures below predate ruling R7 and are
    about other criteria entirely, so this helper gives them the NEUTRAL values -- no donor
    carries an education leg, every person has an education location -- under which that
    criterion excludes nobody. The criterion's own behaviour is covered by the dedicated tests
    at the end of this module, which set both columns explicitly.
    """
    if matching.PERSON_EDUCATION_LOCATION_COLUMN not in persons_home.columns:
        persons_home = persons_home.assign(
            **{matching.PERSON_EDUCATION_LOCATION_COLUMN: True})
    if matching.DONOR_EDUCATION_LEG_COLUMN not in donors.columns:
        donors = donors.assign(**{matching.DONOR_EDUCATION_LEG_COLUMN: False})
    return matching.match_home_office_donors(persons_home, donors, rng, **kwargs)


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
    matches, diagnostics = _match(persons_home, donors, rng)

    assert "p3" not in set(matches["person_id"])
    assert diagnostics["n_not_replaceable"] == 1
    assert diagnostics["share_not_replaceable"] == pytest.approx(1.0 / 3.0)


def test_exact_match_is_recorded_at_coarsening_level_zero():
    persons_home = _persons_home_fixture()
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = _match(persons_home, donors, rng)

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1"
    assert row["coarsening_level"] == 0
    assert diagnostics["matched_by_level"][0] == 1


def test_coarsening_reaches_level_four_when_only_sex_differs():
    persons_home = _persons_home_fixture()
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = _match(persons_home, donors, rng)

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
    matches, diagnostics = _match(persons_home, donors, rng)

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
    matches, diagnostics = _match(persons_home, donors, rng)

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
    matches, diagnostics = _match(persons_home, donors, rng)

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1"
    assert row["coarsening_level"] == 5


def test_missing_has_license_column_is_skipped_not_invented():
    persons_home = _persons_home_fixture().drop(columns=["has_license"])
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = _match(persons_home, donors, rng)

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
    matches_a, _ = _match(
        persons_home, donors, np.random.RandomState(7))
    matches_b, _ = _match(
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
    matches, diagnostics = _match(
        persons_home, donors, rng, minimum_cell=2)
    assert len(matches) == 0
    assert diagnostics["n_not_replaceable"] == 1


# ---------------------------------------------------------------------------
# Fix round 1 (issue #244 review)
# ---------------------------------------------------------------------------

def test_invalid_assigned_distance_class_raises_value_error_naming_it():
    # No donor satisfies the (irrelevant) hard criteria at all, so the coarsening loop reaches
    # level 5 for this person regardless -- where the invalid "bogus" class must be rejected
    # loudly (a module-style ValueError naming it) rather than raising a bare KeyError.
    persons_home = pd.DataFrame({
        "person_id":            ["p1"],
        "assigned_distance_class": ["bogus"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [True],
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    donors = pd.DataFrame({
        "donor_id":             ["d1"],
        "distance_class":       ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],  # hard mismatch -- never a candidate at any level.
        "has_children_u14":     [False],
        "has_car":              [True],
    })
    rng = np.random.RandomState(0)
    with pytest.raises(ValueError, match="bogus"):
        _match(persons_home, donors, rng)


def test_person_with_nan_hard_criterion_is_counted_and_not_replaceable():
    persons_home = _persons_home_fixture()
    persons_home.loc[persons_home["person_id"] == "p1", "has_car"] = np.nan
    donors = _donors_fixture()
    rng = np.random.RandomState(0)
    matches, diagnostics = _match(persons_home, donors, rng)

    assert "p1" not in set(matches["person_id"])
    assert diagnostics["n_persons_hard_criteria_missing"] == 1


def test_donor_row_order_does_not_change_the_draw():
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
    donors_shuffled = donors.iloc[::-1].reset_index(drop=True)

    matches_a, _ = _match(
        persons_home, donors, np.random.RandomState(3))
    matches_b, _ = _match(
        persons_home, donors_shuffled, np.random.RandomState(3))
    assert matches_a.equals(matches_b)


# ---------------------------------------------------------------------------
# Ruling R7 (fix wave A after the 100 % proof run): the education anchor
# ---------------------------------------------------------------------------

def _education_persons(has_education_location):
    return pd.DataFrame({
        "person_id":            ["p1"],
        "assigned_distance_class": ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
        "has_education_location": [has_education_location],
    })


def _education_donors(has_education_leg):
    return pd.DataFrame({
        "donor_id":             ["d1"],
        "distance_class":       ["lt10"],
        "sex":                  ["male"],
        "age_class":            [2],
        "household_size":       [2],
        "has_active_escort":    [False],
        "has_children_u14":     [False],
        "has_car":              [True],
        "has_education_leg":    [has_education_leg],
    })


def test_person_without_education_location_never_gets_an_education_leg_donor():
    """The blocker of the 2026-09-05 proof run: an education activity nobody can anchor.

    The donor is an otherwise EXACT level-0 match and the ONLY donor in the pool, so every
    coarsening level would pick it up if the criterion were soft -- the person must instead stay
    unmatched (the state stage then downgrades them to at_workplace).
    """
    matches, diagnostics = matching.match_home_office_donors(
        _education_persons(False), _education_donors(True), np.random.RandomState(0))

    assert len(matches) == 0
    assert diagnostics["n_not_replaceable"] == 1
    assert diagnostics["n_donors_with_education_leg"] == 1
    assert diagnostics["n_persons_without_education_location"] == 1
    assert diagnostics["n_persons_education_restricted"] == 1


def test_person_with_education_location_may_receive_an_education_leg_donor():
    matches, diagnostics = matching.match_home_office_donors(
        _education_persons(True), _education_donors(True), np.random.RandomState(0))

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1" and row["coarsening_level"] == 0
    assert diagnostics["n_persons_without_education_location"] == 0
    assert diagnostics["n_persons_education_restricted"] == 0


def test_donor_without_an_education_leg_matches_anybody():
    """The criterion is an implication, not an equality: it restricts one direction only."""
    matches, diagnostics = matching.match_home_office_donors(
        _education_persons(False), _education_donors(False), np.random.RandomState(0))

    row = matches.set_index("person_id").loc["p1"]
    assert row["donor_id"] == "d1" and row["coarsening_level"] == 0
    assert diagnostics["n_donors_with_education_leg"] == 0
    # The rule was evaluated for this person but removed no donor, so it is NOT counted: the
    # diagnostic reports the rule's EFFECT, not how often it was checked.
    assert diagnostics["n_persons_education_restricted"] == 0


def test_education_columns_are_required_on_both_frames():
    """An absent column must fail loudly, never silently skip a HARD criterion."""
    with pytest.raises(ValueError, match="has_education_leg"):
        matching.match_home_office_donors(
            _education_persons(True), _education_donors(True).drop(columns=["has_education_leg"]),
            np.random.RandomState(0))
    with pytest.raises(ValueError, match="has_education_location"):
        matching.match_home_office_donors(
            _education_persons(True).drop(columns=["has_education_location"]),
            _education_donors(True), np.random.RandomState(0))


def test_unresolved_education_flags_take_the_restrictive_reading():
    """NaN on either side must not be able to reproduce the crash the criterion prevents."""
    persons = _education_persons(True)
    persons.loc[0, "has_education_location"] = np.nan
    donors = _education_donors(False)
    donors.loc[0, "has_education_leg"] = np.nan

    matches, diagnostics = matching.match_home_office_donors(
        persons, donors, np.random.RandomState(0))

    assert len(matches) == 0
    assert diagnostics["n_donors_with_education_leg"] == 1        # NaN donor read as "has one"
    assert diagnostics["n_persons_without_education_location"] == 1  # NaN person read as "none"
