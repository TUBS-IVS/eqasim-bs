"""Unit tests for braunschweig.synthesis.commute_day.plan_replacement (Phase B Task 3, #244).

Synthetic frames only. Covers the reporting-day plan replacement: absent persons get zero rows,
home persons with a match get the donor's chain (renumbered, jittered once), home persons
without a match and at_workplace persons pass through unchanged, and untouched rows stay
byte-identical to the input (ruling R2).
"""
from __future__ import annotations

import dataclasses
import os

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.departure_time_model import OFFSET_COLUMN
from braunschweig.popsim.trips_stage import CONTRACT
from braunschweig.synthesis.commute_day import plan_replacement

RANDOM_SEED = 42


def _trips_fixture():
    # p1 (at_workplace): 2 trips, unchanged.
    # p2 (home, matched to donor "d1"): 2 trips, replaced by d1's 3-trip chain.
    # p3 (absent): 2 trips, dropped entirely.
    # p4 (home, no match): 2 trips, unchanged (counted as n_home_unmatched).
    # p5 (not present in states at all -- a non-worker pass-through): 1 trip, unchanged.
    return pd.DataFrame({
        "person_id":         ["p1", "p1", "p2", "p2", "p3", "p3", "p4", "p4", "p5"],
        "trip_index":        [0, 1, 0, 1, 0, 1, 0, 1, 0],
        # departure_time/arrival_time are float64, matching real production trips: trips_stage.run
        # applies apply_per_person_jitter (np.round of a float offset) once already upstream, so
        # by the time this stage sees the pre-assignment trips table these columns are already
        # float64, never int64.
        "departure_time":    [8 * 3600.0, 17 * 3600.0, 8 * 3600.0, 17 * 3600.0,
                              8 * 3600.0, 17 * 3600.0, 8 * 3600.0, 17 * 3600.0, 8 * 3600.0],
        "arrival_time":      [8 * 3600.0 + 900, 17 * 3600.0 + 900, 8 * 3600.0 + 900, 17 * 3600.0 + 900,
                              8 * 3600.0 + 900, 17 * 3600.0 + 900, 8 * 3600.0 + 900, 17 * 3600.0 + 900,
                              8 * 3600.0 + 900],
        "preceding_purpose": ["home", "work", "home", "work", "home", "work", "home", "work", "home"],
        "following_purpose": ["work", "home", "work", "home", "work", "home", "work", "home", "shop"],
        "is_first_trip":     [True, False, True, False, True, False, True, False, True],
        "is_last_trip":      [False, True, False, True, False, True, False, True, True],
        "trip_duration":     [900, 900, 900, 900, 900, 900, 900, 900, 900],
        "activity_duration": [np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan],
        "mode":              ["car", "car", "car", "car", "car", "car", "car", "car", "car"],
        "euclidean_distance": [5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0],
        "trip_key":          ["p1_0", "p1_1", "p2_0", "p2_1", "p3_0", "p3_1", "p4_0", "p4_1", "p5_0"],
        "raw_mid_extra":     ["x", "x", "x", "x", "x", "x", "x", "x", "x"],
    })


def _states_fixture():
    return pd.DataFrame({
        "person_id":         ["p1", "p2", "p3", "p4"],
        "commute_day_state": ["at_workplace", "home", "absent", "home"],
        "p_keep":            [1.0, 0.2, 0.0, 0.3],
        "redraw_eligible":   [False, True, True, True],
        "reason":            ["not_eligible", "home_redraw", "absent_far", "home_redraw"],
    })


def _matches_fixture():
    # Only p2 has a match; p4 (also "home") stays unmatched.
    return pd.DataFrame({
        "person_id":         ["p2"],
        "donor_id":          ["d1"],
        "coarsening_level":  [0],
    })


def _donor_trips_fixture():
    # Donor d1's 3-trip chain, already in CONTRACT + euclidean_distance + trip_key order.
    return pd.DataFrame({
        "donor_id":          ["d1", "d1", "d1"],
        "trip_index":        [0, 1, 2],
        "departure_time":    [7 * 3600.0, 12 * 3600.0, 18 * 3600.0],
        "arrival_time":      [7 * 3600.0 + 600, 12 * 3600.0 + 600, 18 * 3600.0 + 600],
        "preceding_purpose": ["home", "work", "shop"],
        "following_purpose": ["work", "shop", "home"],
        "is_first_trip":     [True, False, False],
        "is_last_trip":      [False, False, True],
        "trip_duration":     [600, 600, 600],
        "activity_duration": [np.nan, np.nan, np.nan],
        "mode":              ["bike", "bike", "bike"],
        "euclidean_distance": [3000.0, 2000.0, 4000.0],
        "trip_key":          ["d1_1", "d1_2", "d1_3"],
    })


def test_absent_person_has_zero_rows():
    trips = _trips_fixture()
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    assert "p3" not in set(day_trips["person_id"])
    assert diagnostics["n_persons_absent"] == 1


def test_home_person_with_match_gets_donor_chain_renumbered():
    trips = _trips_fixture()
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    p2_rows = day_trips[day_trips["person_id"] == "p2"].sort_values("trip_index").reset_index(drop=True)

    assert len(p2_rows) == 3
    assert list(p2_rows["trip_index"]) == [0, 1, 2]
    assert list(p2_rows["following_purpose"]) == ["work", "shop", "home"]
    assert list(p2_rows["mode"]) == ["bike", "bike", "bike"]
    assert list(p2_rows["is_first_trip"]) == [True, False, False]
    assert list(p2_rows["is_last_trip"]) == [False, False, True]
    # Donor traceability columns copied verbatim from the donor chain.
    assert list(p2_rows["trip_key"]) == ["d1_1", "d1_2", "d1_3"]
    assert list(p2_rows["euclidean_distance"]) == [3000.0, 2000.0, 4000.0]
    # The raw MiD extra the donor pool does not carry is nulled for replaced rows.
    assert p2_rows["raw_mid_extra"].isna().all()

    assert diagnostics["n_persons_replaced"] == 1
    assert diagnostics["n_trips_removed"] == 2 + 2  # p2's original 2 rows + p3's (absent) 2 rows
    assert diagnostics["n_trips_added"] == 3
    assert diagnostics["n_extra_columns_nulled"] == 1


def test_recomputed_offset_column_is_not_nulled_or_counted_as_an_extra():
    """Ruling A-R8 (issue #123 review fix round 1).

    Once the trips frame carries OFFSET_COLUMN (issue #123, Phase 0 Task 1), it must be treated
    as a RECOMPUTED column, not as a genuine "no donor-side value" extra: apply_per_person_jitter
    overwrites it for every replaced row a few lines after ``_replaced_rows`` would otherwise have
    nulled it, so nulling it first only inflated ``n_extra_columns_nulled`` by one for a null
    nobody ever observes in the output. This fixture adds ``OFFSET_COLUMN`` on top of the
    pre-existing trips fixture (which already has ONE genuine extra column, ``raw_mid_extra``) and
    checks: ``n_extra_columns_nulled`` stays at the pre-existing count (still 1, not 2); the
    replaced person's recorded offset is non-null and IDENTICAL for every trip in the chain (one
    draw per person, per the jitter formula); and ``departure_time - OFFSET_COLUMN`` reproduces
    the rounded DONOR departure -- i.e. Task 1's raw-plus-offset decomposition contract still
    holds across a donor splice, not only inside ``apply_per_person_jitter`` itself.
    """
    trips = _trips_fixture()
    # Arbitrary pre-existing values for the untouched/kept rows -- irrelevant to this test, which
    # only checks what happens to the REPLACED person p2's offset.
    trips[OFFSET_COLUMN] = 0.0
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)

    # Unchanged from test_home_person_with_match_gets_donor_chain_renumbered above: OFFSET_COLUMN
    # must NOT add a second nulled extra column on top of raw_mid_extra.
    assert diagnostics["n_extra_columns_nulled"] == 1

    p2_rows = day_trips[day_trips["person_id"] == "p2"].sort_values("trip_index").reset_index(drop=True)
    assert p2_rows[OFFSET_COLUMN].notna().all()
    assert p2_rows[OFFSET_COLUMN].nunique() == 1   # one draw, shared by every trip in the chain

    donor_departure = _donor_trips_fixture().sort_values("trip_index")["departure_time"].to_numpy()
    recovered_departure = (p2_rows["departure_time"] - p2_rows[OFFSET_COLUMN]).to_numpy()
    assert np.allclose(recovered_departure, np.round(donor_departure))


def test_home_person_without_match_is_unchanged_and_counted():
    trips = _trips_fixture()
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    p4_rows = day_trips[day_trips["person_id"] == "p4"].sort_values("trip_index").reset_index(drop=True)
    original_p4 = trips[trips["person_id"] == "p4"].sort_values("trip_index").reset_index(drop=True)

    assert p4_rows[trips.columns].equals(original_p4)
    assert diagnostics["n_home_unmatched"] == 1


def test_untouched_persons_rows_are_byte_identical_to_input():
    trips = _trips_fixture()
    day_trips, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)

    # p1 (at_workplace), p4 (home, unmatched) and p5 (not in states at all) are all untouched.
    untouched_ids = ["p1", "p4", "p5"]
    actual = (day_trips[day_trips["person_id"].isin(untouched_ids)]
              .sort_values(["person_id", "trip_index"]).reset_index(drop=True))
    expected = (trips[trips["person_id"].isin(untouched_ids)]
                .sort_values(["person_id", "trip_index"]).reset_index(drop=True))
    assert actual[trips.columns].equals(expected)


def test_output_column_order_is_contract_first():
    trips = _trips_fixture()
    day_trips, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    assert list(day_trips.columns[:len(CONTRACT)]) == list(CONTRACT)


def test_output_sorted_by_person_id_then_trip_index():
    trips = _trips_fixture()
    day_trips, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    sort_key = day_trips[["person_id", "trip_index"]].reset_index(drop=True)
    expected = sort_key.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
    assert sort_key.equals(expected)


def test_determinism_of_jitter_with_seeded_random_seed():
    trips = _trips_fixture()
    day_trips_a, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    day_trips_b, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED)
    assert day_trips_a.equals(day_trips_b)


def test_jitter_is_applied_to_replaced_rows_only():
    # A different random_seed must change p2's (replaced) departure times but must never touch
    # any untouched person's rows.
    trips = _trips_fixture()
    day_trips_1, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(), random_seed=1)
    day_trips_2, _ = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(), random_seed=2)

    p2_1 = day_trips_1[day_trips_1["person_id"] == "p2"].sort_values("trip_index")
    p2_2 = day_trips_2[day_trips_2["person_id"] == "p2"].sort_values("trip_index")
    assert not p2_1["departure_time"].reset_index(drop=True).equals(
        p2_2["departure_time"].reset_index(drop=True))

    untouched_ids = ["p1", "p4", "p5"]
    u1 = (day_trips_1[day_trips_1["person_id"].isin(untouched_ids)]
          .sort_values(["person_id", "trip_index"]).reset_index(drop=True))
    u2 = (day_trips_2[day_trips_2["person_id"].isin(untouched_ids)]
          .sort_values(["person_id", "trip_index"]).reset_index(drop=True))
    assert u1[trips.columns].equals(u2[trips.columns])


# ---------------------------------------------------------------------------
# Fix round 1 (issue #244 review)
# ---------------------------------------------------------------------------

def test_zero_matches_preserves_input_dtypes_and_equals_input():
    # Nothing to replace or drop at all (every person at_workplace, none absent, no matches):
    # the output must equal the input exactly, dtypes included -- concatenating with an empty
    # placeholder frame would otherwise upcast every CONTRACT column to object.
    trips = _trips_fixture()
    states = pd.DataFrame({
        "person_id":         ["p1", "p2", "p3", "p4"],
        "commute_day_state": ["at_workplace", "at_workplace", "at_workplace", "at_workplace"],
        "p_keep":            [1.0, 1.0, 1.0, 1.0],
        "redraw_eligible":   [False, False, False, False],
        "reason":            ["not_eligible", "not_eligible", "not_eligible", "not_eligible"],
    })
    empty_matches = pd.DataFrame(columns=["person_id", "donor_id", "coarsening_level"])

    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, states, empty_matches, _donor_trips_fixture(), random_seed=RANDOM_SEED)

    expected = trips.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
    actual = day_trips[trips.columns].sort_values(["person_id", "trip_index"]).reset_index(drop=True)
    assert actual.equals(expected)
    for column in trips.columns:
        assert day_trips[column].dtype == trips[column].dtype, column
    assert diagnostics["n_persons_replaced"] == 0
    assert diagnostics["n_trips_added"] == 0
    assert diagnostics["n_trips_removed"] == 0


def test_donor_missing_from_donor_trips_is_counted_and_warned(caplog):
    # p2 is matched to "d_missing", a donor_id that does not appear in donor_trips at all -- the
    # kind of silent-corruption path a donor_id key/dtype mismatch would produce for every
    # replaced person.
    trips = _trips_fixture()
    matches = pd.DataFrame({
        "person_id":        ["p2"],
        "donor_id":         ["d_missing"],
        "coarsening_level": [0],
    })
    with caplog.at_level("WARNING", logger="braunschweig.synthesis.commute_day.plan_replacement"):
        day_trips, diagnostics = plan_replacement.build_day_trips(
            trips, _states_fixture(), matches, _donor_trips_fixture(), random_seed=RANDOM_SEED)

    assert diagnostics["n_donors_without_trips"] == 1
    assert "p2" not in set(day_trips["person_id"])  # matched but zero rows: legitimately empty.
    assert any("donor_id key/dtype mismatch" in message for message in caplog.messages)


def test_duplicate_person_id_in_matches_raises_value_error():
    trips = _trips_fixture()
    duplicated_matches = pd.DataFrame({
        "person_id":        ["p2", "p2"],
        "donor_id":         ["d1", "d1"],
        "coarsening_level": [0, 0],
    })
    with pytest.raises(ValueError, match="duplicate person_id"):
        plan_replacement.build_day_trips(
            trips, _states_fixture(), duplicated_matches, _donor_trips_fixture(),
            random_seed=RANDOM_SEED)


# ---------------------------------------------------------------------------
# Ruling R9: an immobile donor is not a join failure
# ---------------------------------------------------------------------------

def _donor_attributes_fixture(immobile_by_donor):
    """The two donor-pool columns build_day_trips reads: the decider is ``is_immobile``.

    ``n_trips`` is carried alongside because the real attributes frame has it, and to make the
    point that it is NOT the decider: a donor with ``n_trips == 0`` and ``is_immobile == False``
    is a chain the resample dropped, which must keep the warning semantics.
    """
    return pd.DataFrame({
        "donor_id": list(immobile_by_donor),
        "is_immobile": list(immobile_by_donor.values()),
        "n_trips": [0 if immobile else 4 for immobile in immobile_by_donor.values()],
    })


def _matches_to(donor_id):
    return pd.DataFrame({"person_id": ["p2"], "donor_id": [donor_id], "coarsening_level": [0]})


def test_immobile_donor_is_counted_as_immobile_not_as_a_join_failure(caplog):
    """``is_immobile``: a trip-less home-office day is the CORRECT outcome, not a defect."""
    with caplog.at_level("WARNING",
                         logger="braunschweig.synthesis.commute_day.plan_replacement"):
        day_trips, diagnostics = plan_replacement.build_day_trips(
            _trips_fixture(), _states_fixture(), _matches_to("d_immobile"),
            _donor_trips_fixture(), random_seed=RANDOM_SEED,
            donor_attributes=_donor_attributes_fixture({"d1": False, "d_immobile": True}))

    assert diagnostics["n_donors_immobile"] == 1
    assert diagnostics["n_donors_without_trips"] == 0
    assert diagnostics["share_donors_immobile"] == pytest.approx(1.0)
    assert "p2" not in set(day_trips["person_id"])   # trip-less day, as intended
    assert not any("donor_id key/dtype mismatch" in message for message in caplog.messages)


def test_donor_that_did_travel_but_has_no_rows_still_warns(caplog):
    """``is_immobile == False`` and yet no rows: the suspicious symptom keeps its warning."""
    with caplog.at_level("WARNING",
                         logger="braunschweig.synthesis.commute_day.plan_replacement"):
        _day_trips, diagnostics = plan_replacement.build_day_trips(
            _trips_fixture(), _states_fixture(), _matches_to("d_lost"), _donor_trips_fixture(),
            random_seed=RANDOM_SEED,
            donor_attributes=_donor_attributes_fixture({"d1": False, "d_lost": False}))

    assert diagnostics["n_donors_without_trips"] == 1
    assert diagnostics["n_donors_immobile"] == 0
    assert any("donor_id key/dtype mismatch" in message for message in caplog.messages)


def test_chain_dropped_by_the_resample_is_not_excused_as_immobile(caplog):
    """Fix round 1: ``n_trips == 0`` alone must NOT count as immobile.

    A donor whose chain the repair/resample cascade dropped has no trips either, but that is a
    resample gap rather than real behaviour (the donor pool counts it as
    n_chain_dropped_by_resample), so it must keep the warning semantics.
    """
    attributes = pd.DataFrame({"donor_id": ["d1", "d_dropped"],
                               "is_immobile": [False, False],
                               "n_trips": [3, 0]})
    with caplog.at_level("WARNING",
                         logger="braunschweig.synthesis.commute_day.plan_replacement"):
        _day_trips, diagnostics = plan_replacement.build_day_trips(
            _trips_fixture(), _states_fixture(), _matches_to("d_dropped"),
            _donor_trips_fixture(), random_seed=RANDOM_SEED, donor_attributes=attributes)

    assert diagnostics["n_donors_without_trips"] == 1
    assert diagnostics["n_donors_immobile"] == 0
    assert any("donor_id key/dtype mismatch" in message for message in caplog.messages)


def test_donor_absent_from_the_attributes_is_treated_as_suspicious_not_immobile(caplog):
    """An unknown trip count must never be read as the benign case."""
    with caplog.at_level("WARNING",
                         logger="braunschweig.synthesis.commute_day.plan_replacement"):
        _day_trips, diagnostics = plan_replacement.build_day_trips(
            _trips_fixture(), _states_fixture(), _matches_to("d_unknown"),
            _donor_trips_fixture(), random_seed=RANDOM_SEED,
            donor_attributes=_donor_attributes_fixture({"d1": False}))

    assert diagnostics["n_donors_unknown_trip_count"] == 1
    assert diagnostics["n_donors_without_trips"] == 1
    assert diagnostics["n_donors_immobile"] == 0


def test_donor_attributes_require_the_immobility_flag():
    with pytest.raises(ValueError, match="is_immobile"):
        plan_replacement.build_day_trips(
            _trips_fixture(), _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
            random_seed=RANDOM_SEED,
            donor_attributes=pd.DataFrame({"donor_id": ["d1"]}))


# ---------------------------------------------------------------------------
# Ruling R8: the replaced rows must not be assembled column by column
# ---------------------------------------------------------------------------

def _many_persons_fixture(n_persons=300, n_extra_columns=120, n_trips_per_person=2):
    """A population-shaped fixture: many replaced persons AND many extra input columns.

    Both dimensions matter: the fragmentation warning the 2026-09-05 run drowned in was emitted
    once per (replaced person x inserted column), so a fixture with only a handful of either
    would stay silent even under the old column-wise implementation.
    """
    person_ids = [f"p{index}" for index in range(n_persons)]
    rows = []
    for person_id in person_ids:
        for trip_index in range(n_trips_per_person):
            rows.append({
                "person_id": person_id, "trip_index": trip_index,
                "departure_time": 8 * 3600.0 + trip_index * 3600.0,
                "arrival_time": 8 * 3600.0 + trip_index * 3600.0 + 900.0,
                "preceding_purpose": "home", "following_purpose": "work",
                "is_first_trip": trip_index == 0,
                "is_last_trip": trip_index == n_trips_per_person - 1,
                "trip_duration": 900.0, "activity_duration": np.nan, "mode": "car",
                "euclidean_distance": 5000.0, "trip_key": f"{person_id}_{trip_index}",
            })
    trips = pd.DataFrame(rows)
    # Built with ONE concat rather than a column-at-a-time loop, so the fixture itself does not
    # emit the very warning the test is about.
    trips = pd.concat([trips, pd.DataFrame(
        "x", index=trips.index,
        columns=[f"raw_mid_extra_{index}" for index in range(n_extra_columns)])], axis=1)
    states = pd.DataFrame({"person_id": person_ids, "commute_day_state": "home"})
    matches = pd.DataFrame({"person_id": person_ids, "donor_id": "d1",
                            "coarsening_level": 0})
    return trips, states, matches


# ---------------------------------------------------------------------------
# General day absence composition (issue #370, Task 4)
# ---------------------------------------------------------------------------

def test_general_absence_removes_rows_and_is_counted_separately():
    # p1 is at_workplace (untouched by the commute model) and p3 is already commute-absent; both
    # are ALSO marked generally absent here, so this exercises the "general-only" removal (p1) and
    # the overlap counter (p3, already counted by the commute-absent path) in one test.
    trips = _trips_fixture()
    general = pd.DataFrame({
        "person_id":         ["p1", "p3"],
        "day_absence_state": ["absent_household", "absent_individual"],
    })
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED, general_absence=general)

    assert "p1" not in set(day_trips["person_id"])
    assert diagnostics["n_persons_absent_general"] == 2
    assert diagnostics["n_persons_absent_both"] == 1  # p3 is both commute- and generally absent
    assert diagnostics["n_trips_removed_general"] == int((trips["person_id"] == "p1").sum())
    assert (diagnostics["n_persons_absent_total"] == diagnostics["n_persons_absent_commute"]
            + diagnostics["n_persons_absent_general"] - diagnostics["n_persons_absent_both"])


def test_general_absence_none_is_byte_identical_to_the_previous_signature():
    trips = _trips_fixture()
    states, matches, donor_trips = _states_fixture(), _matches_fixture(), _donor_trips_fixture()
    a, diagnostics_a = plan_replacement.build_day_trips(
        trips, states, matches, donor_trips, random_seed=RANDOM_SEED)
    b, diagnostics_b = plan_replacement.build_day_trips(
        trips, states, matches, donor_trips, random_seed=RANDOM_SEED, general_absence=None)
    pd.testing.assert_frame_equal(a, b)
    assert diagnostics_a["n_persons_absent"] == diagnostics_b["n_persons_absent_commute"]
    assert diagnostics_b["n_persons_absent_general"] == 0
    assert diagnostics_b["n_persons_absent_both"] == 0
    assert diagnostics_b["n_trips_removed_general"] == 0
    assert diagnostics_b["n_persons_absent_total"] == diagnostics_b["n_persons_absent_commute"]


def test_general_absence_requires_the_two_columns():
    trips = _trips_fixture()
    with pytest.raises(ValueError, match="general_absence"):
        plan_replacement.build_day_trips(
            trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
            random_seed=RANDOM_SEED, general_absence=pd.DataFrame({"person_id": [1]}))


def test_general_absence_excludes_a_matched_home_person_from_the_splice():
    # p2 is 'home' and matched to donor d1 in the base fixture; marking them ALSO generally absent
    # must suppress the donor splice entirely (they must never receive donor rows and then have
    # them removed again) -- the exclusion happens BEFORE the splice loop runs.
    trips = _trips_fixture()
    general = pd.DataFrame({"person_id": ["p2"], "day_absence_state": ["absent_individual"]})
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED, general_absence=general)

    assert "p2" not in set(day_trips["person_id"])
    assert diagnostics["n_persons_replaced"] == 0     # p2 excluded from the splice, not replaced
    assert diagnostics["n_trips_added"] == 0
    assert diagnostics["n_persons_absent_general"] == 1


def test_general_absence_excludes_an_unmatched_home_person_from_n_home_unmatched(caplog):
    # p4 is 'home' WITHOUT a donor match in the base fixture -- counted in n_home_unmatched and
    # kept UNCHANGED there (see test_home_person_without_match_is_unchanged_and_counted). Marking
    # them ALSO generally absent must remove their rows like any other absent person: they must
    # drop out of n_home_unmatched (not be double-counted as "kept unchanged" while their rows are
    # actually gone) and must not trigger the "keep their ORIGINAL day unchanged" warning.
    trips = _trips_fixture()
    baseline_trips, baseline_diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(), random_seed=RANDOM_SEED)
    assert "p4" in set(baseline_trips["person_id"])
    assert baseline_diagnostics["n_home_unmatched"] == 1

    general = pd.DataFrame({"person_id": ["p4"], "day_absence_state": ["absent_individual"]})
    caplog.clear()  # drop the baseline call's own "1 home person(s) unmatched" warning above.
    with caplog.at_level("WARNING", logger="braunschweig.synthesis.commute_day.plan_replacement"):
        day_trips, diagnostics = plan_replacement.build_day_trips(
            trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
            random_seed=RANDOM_SEED, general_absence=general)

    assert "p4" not in set(day_trips["person_id"])
    assert diagnostics["n_home_unmatched"] == baseline_diagnostics["n_home_unmatched"] - 1
    assert diagnostics["n_persons_absent_general"] == 1
    assert not any("keep their ORIGINAL" in message for message in caplog.messages)


# ---------------------------------------------------------------------------
# Escort-coherence diagnostics (issue #370 final-review fix wave, ruling R10, spec 2.1 point 4)
# ---------------------------------------------------------------------------

def test_n_absent_with_escort_leg_counts_absent_persons_with_an_original_escort_leg():
    # p1 has an escort leg and is generally absent; p2 has none and is present -- only p1 must
    # be counted, and it must be read from the ORIGINAL trips row (p1 receives no rows at all).
    trips = pd.DataFrame({
        "person_id":         ["p1", "p1", "p2", "p2"],
        "trip_index":        [0, 1, 0, 1],
        "departure_time":    [7 * 3600.0, 8 * 3600.0, 8 * 3600.0, 17 * 3600.0],
        "arrival_time":      [7 * 3600.0 + 600, 8 * 3600.0 + 600, 8 * 3600.0 + 900, 17 * 3600.0 + 900],
        "preceding_purpose": ["home", "escort", "home", "work"],
        "following_purpose": ["escort", "work", "work", "home"],
        "is_first_trip":     [True, False, True, False],
        "is_last_trip":      [False, True, False, True],
        "trip_duration":     [600, 600, 900, 900],
        "activity_duration": [np.nan, np.nan, np.nan, np.nan],
        "mode":              ["car", "car", "car", "car"],
    })
    states = pd.DataFrame({"person_id": ["p1", "p2"], "commute_day_state": ["at_workplace", "at_workplace"]})
    matches = pd.DataFrame(columns=["person_id", "donor_id", "coarsening_level"])
    general = pd.DataFrame({"person_id": ["p1", "p2"], "day_absence_state": ["absent_individual", "present"]})

    _day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, states, matches, _donor_trips_fixture(), random_seed=RANDOM_SEED,
        general_absence=general)

    assert diagnostics["n_absent_with_escort_leg"] == 1
    assert diagnostics["n_persons_absent_total"] == 1


def test_n_children_with_absent_escorter_counts_present_children_via_household_proxy():
    trips = pd.DataFrame({
        "person_id":         ["adult1", "adult1"],
        "trip_index":        [0, 1],
        "departure_time":    [7 * 3600.0, 8 * 3600.0],
        "arrival_time":      [7 * 3600.0 + 600, 8 * 3600.0 + 600],
        "preceding_purpose": ["home", "escort"],
        "following_purpose": ["escort", "work"],
        "is_first_trip":     [True, False],
        "is_last_trip":      [False, True],
        "trip_duration":     [600, 600],
        "activity_duration": [np.nan, np.nan],
        "mode":              ["car", "car"],
    })
    states = pd.DataFrame({"person_id": ["adult1"], "commute_day_state": ["at_workplace"]})
    matches = pd.DataFrame(columns=["person_id", "donor_id", "coarsening_level"])
    general = pd.DataFrame({"person_id": ["adult1"], "day_absence_state": ["absent_individual"]})
    # h1: adult1 (absent, escorting) + two present children -- both counted. h2: an unrelated
    # present child in a household with no absent escorter -- must NOT be counted.
    persons = pd.DataFrame({
        "person_id":   ["adult1", "child1", "child2", "unrelated_child"],
        "household_id": ["h1", "h1", "h1", "h2"],
        "age":          [40, 10, 17, 8],
    })

    _day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, states, matches, _donor_trips_fixture(), random_seed=RANDOM_SEED,
        general_absence=general, persons=persons)

    assert diagnostics["n_children_with_absent_escorter"] == 2


def test_n_children_with_absent_escorter_is_none_without_a_persons_frame():
    trips = _trips_fixture()
    _day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(), random_seed=RANDOM_SEED)
    assert diagnostics["n_children_with_absent_escorter"] is None


def test_build_day_trips_emits_no_pandas_performance_warning():
    """Ruling R8: 657,888 PerformanceWarnings in one run made that run's log 254 MB."""
    import warnings

    trips, states, matches = _many_persons_fixture()
    with warnings.catch_warnings():
        warnings.simplefilter("error", pd.errors.PerformanceWarning)
        day_trips, diagnostics = plan_replacement.build_day_trips(
            trips, states, matches, _donor_trips_fixture(), random_seed=RANDOM_SEED)

    assert diagnostics["n_persons_replaced"] == 300
    assert diagnostics["n_trips_added"] == 300 * 3          # d1's chain is 3 trips long
    assert len(day_trips) == 900
    # The renumbering and the nulled extras must survive the batched assembly unchanged.
    first_person = day_trips[day_trips["person_id"] == "p0"]
    assert list(first_person["trip_index"]) == [0, 1, 2]
    assert list(first_person["is_first_trip"]) == [True, False, False]
    assert list(first_person["is_last_trip"]) == [False, False, True]
    assert first_person["raw_mid_extra_0"].isna().all()
    assert list(first_person["mode"]) == ["bike", "bike", "bike"]


# ---------------------------------------------------------------------------
# Departure-time model on the spliced home-office chains (issue #123 Task 4, ADR-0114).
# The replaced rows carry a DONOR's day placed on a RECEIVING person, so the start-time model
# must be applied with the RECEIVING person's own attributes (ruling A-R7); ``departure_time=None``
# keeps today's eqasim jitter byte-identically.
# ---------------------------------------------------------------------------

SRV_REFERENCE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "eqasim-data", "data", "braunschweig", "srv")


def _receiving_persons():
    """Synthetic-schema attributes for the fixture's persons: p2 (the replaced one) is an
    employed adult, so its mapping cell is ``(employed, <first purpose of the donor chain>)``."""
    return pd.DataFrame({
        "person_id": ["p1", "p2", "p3", "p4", "p5"],
        "household_id": [1, 2, 3, 4, 5],
        "age": [40, 41, 42, 43, 44],
        "employed": [True, True, True, True, False],
    })


def _departure_time_settings(model="srv_mapped", **overrides):
    from braunschweig.popsim.departure_time_model import load_departure_time_reference

    settings = dict(
        model=model,
        reference=load_departure_time_reference(SRV_REFERENCE_DIR) if model == "srv_mapped" else None,
        min_reference_n=200, min_model_n=1, max_median_shift_hours=2.0,
        persons=_receiving_persons())
    settings.update(overrides)
    return plan_replacement.DepartureTimeSettings(**settings)


def test_replaced_rows_use_the_departure_time_model_when_settings_are_given():
    """With settings the replaced rows go through ``apply_departure_time_model`` instead of the
    eqasim jitter: the offset is recorded once per person, the whole donor chain moves rigidly,
    the first departure lands inside the SrV support of the receiving person's cell, and no
    untouched row is affected."""
    from braunschweig.popsim.departure_time_model import BIN_MINUTES

    trips = _trips_fixture()
    # The real pre-assignment table always carries the recorded offset (trips_stage.run writes
    # it); build_day_trips only keeps output columns the INPUT frame has, so a fixture without
    # it would silently drop the very column this test reads.
    trips[OFFSET_COLUMN] = 0.0
    settings = _departure_time_settings()
    day_trips, diagnostics = plan_replacement.build_day_trips(
        trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture(),
        random_seed=RANDOM_SEED, departure_time=settings)

    p2_rows = day_trips[day_trips["person_id"] == "p2"].sort_values("trip_index").reset_index(drop=True)
    assert len(p2_rows) == 3
    assert p2_rows[OFFSET_COLUMN].nunique() == 1          # one offset for the whole chain
    donor = _donor_trips_fixture().sort_values("trip_index").reset_index(drop=True)
    offset = float(p2_rows[OFFSET_COLUMN].iloc[0])
    assert np.allclose(p2_rows["departure_time"], np.round(donor["departure_time"] + offset))
    assert np.allclose(p2_rows["arrival_time"], np.round(donor["arrival_time"] + offset))

    # The donor's first leg goes to "work" and p2 is employed, so the mapping cell is
    # (employed, work); the mapped first departure must sit in a bin that cell has mass in.
    cell = settings.reference[(settings.reference["segment"] == "employed")
                              & (settings.reference["purpose"] == "work")]
    support = set(cell.loc[cell["share_derounded"] > 0.0, "bin_15min"].astype(int))
    assert int(p2_rows["departure_time"].iloc[0] // (BIN_MINUTES * 60)) in support
    # The model ran and reported itself, so a run log can state which model produced the day.
    assert diagnostics["departure_time"]["model"] == "srv_mapped"
    assert diagnostics["departure_time"]["n_persons"] == 1

    # Untouched persons keep their original rows exactly.
    untouched = ["p1", "p4", "p5"]
    pd.testing.assert_frame_equal(
        day_trips[day_trips["person_id"].isin(untouched)][list(trips.columns)]
        .sort_values(["person_id", "trip_index"]).reset_index(drop=True),
        trips[trips["person_id"].isin(untouched)]
        .sort_values(["person_id", "trip_index"]).reset_index(drop=True))


def test_departure_time_none_keeps_the_eqasim_jitter_byte_identically():
    """The default (``departure_time=None``) must reproduce the pre-Task-4 output exactly, so
    turning the model off is a genuine no-op rather than "close enough"."""
    trips = _trips_fixture()
    args = (trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture())
    a, _ = plan_replacement.build_day_trips(*args, random_seed=RANDOM_SEED)
    b, _ = plan_replacement.build_day_trips(*args, random_seed=RANDOM_SEED, departure_time=None)
    pd.testing.assert_frame_equal(a, b)

    # ... and it IS the eqasim jitter, not a model that happens to agree: the same donor rows
    # jittered directly with the same seed give the same times.
    expected = plan_replacement.apply_per_person_jitter(
        _donor_trips_fixture().sort_values("trip_index").reset_index(drop=True)
        .assign(person_id="p2"), RANDOM_SEED)
    p2_rows = a[a["person_id"] == "p2"].sort_values("trip_index").reset_index(drop=True)
    assert p2_rows["departure_time"].tolist() == expected["departure_time"].tolist()


def test_eqasim_uniform_settings_are_byte_identical_to_no_settings_at_all():
    """The reporting-day stage ALWAYS passes settings, so its OFF path is
    ``DepartureTimeSettings(model="eqasim_uniform")`` -- which must produce exactly the frame the
    pre-Task-4 ``departure_time=None`` call produced, or turning the feature off would still move
    every spliced home-office day."""
    trips = _trips_fixture()
    args = (trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture())
    a, _ = plan_replacement.build_day_trips(*args, random_seed=RANDOM_SEED)
    b, diagnostics = plan_replacement.build_day_trips(
        *args, random_seed=RANDOM_SEED,
        departure_time=_departure_time_settings(model="eqasim_uniform"))
    pd.testing.assert_frame_equal(a, b)
    assert diagnostics["departure_time"]["model"] == "eqasim_uniform"


def test_the_settings_use_the_receiving_persons_attributes_not_the_donors():
    """Ruling A-R7: the mapping cell comes from the RECEIVING person. Two runs that differ ONLY
    in the receiving person's age (school-age vs employed adult) must map the same donor chain
    into different reference cells, i.e. produce different times."""
    trips = _trips_fixture()
    args = (trips, _states_fixture(), _matches_fixture(), _donor_trips_fixture())
    adult = _departure_time_settings()
    child_persons = _receiving_persons()
    child_persons.loc[child_persons["person_id"] == "p2", ["age", "employed"]] = [10, False]
    child = _departure_time_settings(persons=child_persons)

    a, _ = plan_replacement.build_day_trips(*args, random_seed=RANDOM_SEED, departure_time=adult)
    b, _ = plan_replacement.build_day_trips(*args, random_seed=RANDOM_SEED, departure_time=child)
    p2_a = a[a["person_id"] == "p2"]["departure_time"].tolist()
    p2_b = b[b["person_id"] == "p2"]["departure_time"].tolist()
    assert p2_a != p2_b


def test_the_departure_time_settings_are_frozen():
    """A frozen dataclass: the settings are read by the replacement and reported in the run log,
    so they must not be mutated between the two.

    The expected exception is ``dataclasses.FrozenInstanceError`` specifically (fix round 1): a
    bare ``Exception`` would also pass if the assignment failed for an unrelated reason -- e.g.
    the attribute having been renamed -- and would then no longer prove the class is frozen.
    """
    settings = _departure_time_settings(model="eqasim_uniform")
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.model = "srv_mapped"
