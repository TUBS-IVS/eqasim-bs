"""Unit tests for braunschweig.synthesis.commute_day.plan_replacement (Phase B Task 3, #244).

Synthetic frames only. Covers the reporting-day plan replacement: absent persons get zero rows,
home persons with a match get the donor's chain (renumbered, jittered once), home persons
without a match and at_workplace persons pass through unchanged, and untouched rows stay
byte-identical to the input (ruling R2).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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
    assert any("donor_id key or dtype mismatch" in message for message in caplog.messages)


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
