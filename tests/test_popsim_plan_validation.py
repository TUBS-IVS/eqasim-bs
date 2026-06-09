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


# ---------------------------------------------------------------------------
# Task 7: resample_chains tests
# ---------------------------------------------------------------------------

def test_resample_replaces_unfixable_with_same_cell_donor():
    unfixable_trips = pd.DataFrame({
        "person_id": ["p1"], "departure_time": [8*3600], "arrival_time": [8*3600],
        "preceding_purpose": ["home"], "following_purpose": ["work"],
        "is_first_trip": [True], "is_last_trip": [True], "mode": ["car"],
    })
    donor_chains = {
        "X": [pd.DataFrame({
            "departure_time": [8*3600, 17*3600], "arrival_time": [8*3600+1800, 17*3600+1200],
            "preceding_purpose": ["home", "work"], "following_purpose": ["work", "home"],
            "is_first_trip": [True, False], "is_last_trip": [False, True], "mode": ["pt", "pt"],
        })],
    }
    person_cells = {"p1": "X"}
    import numpy as np
    out = pv.resample_chains(unfixable_trips, {"p1"}, person_cells, donor_chains,
                             rng=np.random.RandomState(0))
    p1 = out[out["person_id"] == "p1"].sort_values("departure_time")
    assert len(p1) == 2
    assert p1.iloc[-1]["following_purpose"] == "home"
    assert (p1["mode"] == "pt").all()


def test_resample_weighted_draw_is_deterministic_and_respects_weights():
    import numpy as np

    # Two donors: chain A (2-trip, all-"pt"), chain B (1-trip, all-"car").
    chain_a = pd.DataFrame({
        "departure_time": [8*3600, 17*3600], "arrival_time": [8*3600+1800, 17*3600+1200],
        "preceding_purpose": ["home", "work"], "following_purpose": ["work", "home"],
        "is_first_trip": [True, False], "is_last_trip": [False, True], "mode": ["pt", "pt"],
    })
    chain_b = pd.DataFrame({
        "departure_time": [8*3600], "arrival_time": [8*3600+900],
        "preceding_purpose": ["home"], "following_purpose": ["home"],
        "is_first_trip": [True], "is_last_trip": [True], "mode": ["car"],
    })
    donor_chains = {"X": [chain_a, chain_b]}
    # Weight: chain_a=0.0, chain_b=1.0 -> chain_a must NEVER be drawn.
    donor_weights = {"X": [0.0, 1.0]}

    unfixable_trips = pd.DataFrame({
        "person_id": ["p1", "p2"], "departure_time": [7*3600, 7*3600],
        "arrival_time": [7*3600, 7*3600], "preceding_purpose": ["home", "home"],
        "following_purpose": ["work", "work"], "is_first_trip": [True, True],
        "is_last_trip": [True, True], "mode": ["walk", "walk"],
    })
    person_cells = {"p1": "X", "p2": "X"}

    out1 = pv.resample_chains(
        unfixable_trips, {"p1", "p2"}, person_cells, donor_chains,
        rng=np.random.RandomState(0), donor_weights=donor_weights,
    )
    # Zero-weight donor (chain_a, "pt") must never appear.
    assert (out1[out1["person_id"].isin({"p1", "p2"})]["mode"] != "pt").all(), \
        "zero-weight donor chain_a must not be drawn"

    # Determinism: fresh RandomState(0) -> identical result.
    out2 = pv.resample_chains(
        unfixable_trips, {"p1", "p2"}, person_cells, donor_chains,
        rng=np.random.RandomState(0), donor_weights=donor_weights,
    )
    pd.testing.assert_frame_equal(
        out1.sort_values(["person_id", "departure_time"]).reset_index(drop=True),
        out2.sort_values(["person_id", "departure_time"]).reset_index(drop=True),
    )


def test_resample_home_only_fallback_when_no_donor():
    import numpy as np

    unfixable_trips = pd.DataFrame({
        "person_id": ["p_orphan"], "departure_time": [8*3600],
        "arrival_time": [8*3600], "preceding_purpose": ["home"],
        "following_purpose": ["work"], "is_first_trip": [True],
        "is_last_trip": [True], "mode": ["car"],
    })
    # donor_chains has no entry for the person's cell.
    donor_chains = {}
    person_cells = {"p_orphan": "CELL_WITH_NO_DONORS"}

    out = pv.resample_chains(
        unfixable_trips, {"p_orphan"}, person_cells, donor_chains,
        rng=np.random.RandomState(0),
    )
    p_rows = out[out["person_id"] == "p_orphan"]
    assert len(p_rows) == 1, "fallback must produce exactly one home-only row"
    assert p_rows.iloc[0]["following_purpose"] == "home"


# ---------------------------------------------------------------------------
# CRITICAL-1: repaired frame must have finite trip_index and trip_duration
# on the appended return-home row.
# IMPORTANT-3: trip_id must remain globally unique after repair.
# ---------------------------------------------------------------------------

def test_repair_trip_index_and_duration_no_nan_after_home_closure():
    """CRITICAL-1 + IMPORTANT-3: repair_trips must recompute trip_index and
    trip_duration on the appended return-home row; trip_id must be globally unique.

    A one-trip home->work person that does NOT end at home triggers the closure
    append.  Before the fix, the appended row has NaN trip_index and NaN
    trip_duration, breaking downstream sorts and hts.check_trip_times.
    """
    import numpy as np

    # One person, one trip: home -> work (does NOT end at home).
    df = pd.DataFrame({
        "person_id": ["p"],
        "departure_time": [8 * 3600.0],
        "arrival_time": [8 * 3600.0 + 1800.0],
        "preceding_purpose": ["home"],
        "following_purpose": ["work"],
        "is_first_trip": [True],
        "is_last_trip": [True],
        "mode": ["car"],
        "trip_id": [0],
        "trip_index": [0],
        "trip_duration": [1800.0],
        "activity_duration": [float("nan")],
    })
    validator = pv.PlanValidator(require_home_closure=True)
    fixed, report = validator.repair_trips(df)

    person_rows = fixed[fixed["person_id"] == "p"].sort_values("departure_time")
    assert len(person_rows) == 2, "return-home trip should have been appended"

    # CRITICAL-1a: trip_index must be integer dtype and have no NaN.
    assert pd.api.types.is_integer_dtype(person_rows["trip_index"]), (
        "trip_index must be integer dtype after repair"
    )
    assert not person_rows["trip_index"].isna().any(), (
        "trip_index must not contain NaN after repair (appended row)"
    )

    # CRITICAL-1b: trip_index must be 0..n-1 per person.
    expected_indices = list(range(len(person_rows)))
    assert list(person_rows["trip_index"]) == expected_indices, (
        f"trip_index must be 0..{len(person_rows)-1}; got {list(person_rows['trip_index'])}"
    )

    # CRITICAL-1c: trip_duration must be finite on every row (including the appended one).
    assert person_rows["trip_duration"].notna().all(), (
        "trip_duration must be finite on every row after repair"
    )
    assert (person_rows["trip_duration"] >= 0).all(), (
        "trip_duration must be non-negative after repair"
    )

    # IMPORTANT-3: trip_id must be globally unique and integer.
    assert pd.api.types.is_integer_dtype(fixed["trip_id"]), (
        "trip_id must be integer dtype after repair"
    )
    assert fixed["trip_id"].is_unique, (
        "trip_id must be globally unique after repair"
    )


def test_repair_trip_id_unique_multi_person():
    """IMPORTANT-3: trip_id stays globally unique with multiple persons."""
    import numpy as np

    # Two persons each with one trip that does not end at home -> two rows appended.
    df = pd.DataFrame({
        "person_id": ["p1", "p2"],
        "departure_time": [8 * 3600.0, 9 * 3600.0],
        "arrival_time": [8 * 3600.0 + 1800.0, 9 * 3600.0 + 900.0],
        "preceding_purpose": ["home", "home"],
        "following_purpose": ["work", "shop"],
        "is_first_trip": [True, True],
        "is_last_trip": [True, True],
        "mode": ["car", "pt"],
        "trip_id": [0, 1],
        "trip_index": [0, 0],
        "trip_duration": [1800.0, 900.0],
        "activity_duration": [float("nan"), float("nan")],
    })
    validator = pv.PlanValidator(require_home_closure=True)
    fixed, _ = validator.repair_trips(df)

    assert fixed["trip_id"].is_unique, "trip_id must be globally unique across persons after repair"
    assert pd.api.types.is_integer_dtype(fixed["trip_id"]), "trip_id must be integer"


# ---------------------------------------------------------------------------
# CRITICAL-2: resample_chains iteration order must be deterministic
# regardless of the input container type (set vs list in different orders).
# ---------------------------------------------------------------------------

def test_resample_chains_deterministic_across_set_iteration_orders():
    """CRITICAL-2: resample_chains must produce identical per-person results
    when called twice with the same RNG seed even if unfixable_persons is a
    set (whose iteration order varies with hash randomisation).

    Two distinguishable donor chains (all-pt vs all-car) and TWO unfixable
    persons in the same cell, with uniform weights.  The test calls
    resample_chains with the set given in two different explicit orderings
    (via sorted lists) and with the same fresh RNG seed each time, then
    asserts each person gets the same chain in both runs.

    Before CRITICAL-2 is fixed (sorting the set iteration), a set input can
    produce different per-person chain assignments across runs because hash
    randomisation changes the set iteration order, so person p1 consumes the
    first RNG draw in one run and the second in another.
    """
    import numpy as np

    chain_pt = pd.DataFrame({
        "departure_time": [8 * 3600, 17 * 3600],
        "arrival_time": [8 * 3600 + 1800, 17 * 3600 + 1200],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"],
        "is_first_trip": [True, False],
        "is_last_trip": [False, True],
        "mode": ["pt", "pt"],
    })
    chain_car = pd.DataFrame({
        "departure_time": [8 * 3600],
        "arrival_time": [8 * 3600 + 900],
        "preceding_purpose": ["home"],
        "following_purpose": ["home"],
        "is_first_trip": [True],
        "is_last_trip": [True],
        "mode": ["car"],
    })
    donor_chains = {"X": [chain_pt, chain_car]}
    person_cells = {"p1": "X", "p2": "X"}

    unfixable_trips = pd.DataFrame({
        "person_id": ["p1", "p2"],
        "departure_time": [7 * 3600.0, 7 * 3600.0],
        "arrival_time": [7 * 3600.0, 7 * 3600.0],
        "preceding_purpose": ["home", "home"],
        "following_purpose": ["work", "work"],
        "is_first_trip": [True, True],
        "is_last_trip": [True, True],
        "mode": ["walk", "walk"],
    })

    # Run A: unfixable_persons as sorted list ["p1", "p2"]
    out_a = pv.resample_chains(
        unfixable_trips, ["p1", "p2"], person_cells, donor_chains,
        rng=np.random.RandomState(0),
    )
    # Run B: unfixable_persons as sorted list ["p2", "p1"] (different order)
    # With a set, hash randomisation could give this order non-deterministically.
    out_b = pv.resample_chains(
        unfixable_trips, ["p2", "p1"], person_cells, donor_chains,
        rng=np.random.RandomState(0),
    )

    # Per-person mode sequences must be IDENTICAL in both runs because
    # sorted() inside resample_chains normalises the order.
    for pid in ["p1", "p2"]:
        modes_a = list(out_a[out_a["person_id"] == pid].sort_values("departure_time")["mode"])
        modes_b = list(out_b[out_b["person_id"] == pid].sort_values("departure_time")["mode"])
        assert modes_a == modes_b, (
            f"person {pid}: got modes {modes_a} vs {modes_b} — "
            "resample_chains must iterate in sorted order for determinism"
        )


# ---------------------------------------------------------------------------
# IMPORTANT-5: guard against zero-sum weights in resample_chains.
# ---------------------------------------------------------------------------

def test_resample_chains_raises_on_zero_sum_weights():
    """IMPORTANT-5: resample_chains must raise ValueError when all weights are 0."""
    import numpy as np

    chain_a = pd.DataFrame({
        "departure_time": [8 * 3600], "arrival_time": [8 * 3600 + 900],
        "preceding_purpose": ["home"], "following_purpose": ["home"],
        "is_first_trip": [True], "is_last_trip": [True], "mode": ["car"],
    })
    donor_chains = {"X": [chain_a]}
    donor_weights = {"X": [0.0]}  # all-zero -> sum = 0 -> invalid
    person_cells = {"p1": "X"}
    unfixable_trips = pd.DataFrame({
        "person_id": ["p1"], "departure_time": [7 * 3600.0],
        "arrival_time": [7 * 3600.0], "preceding_purpose": ["home"],
        "following_purpose": ["work"], "is_first_trip": [True], "is_last_trip": [True],
        "mode": ["walk"],
    })
    import pytest
    with pytest.raises(ValueError, match="X"):
        pv.resample_chains(
            unfixable_trips, ["p1"], person_cells, donor_chains,
            rng=np.random.RandomState(0), donor_weights=donor_weights,
        )


def test_resample_chains_raises_on_mismatched_weight_length():
    """IMPORTANT-5: mismatched donor_weights length must raise ValueError."""
    import numpy as np
    import pytest

    chain_a = pd.DataFrame({
        "departure_time": [8 * 3600], "arrival_time": [8 * 3600 + 900],
        "preceding_purpose": ["home"], "following_purpose": ["home"],
        "is_first_trip": [True], "is_last_trip": [True], "mode": ["car"],
    })
    chain_b = pd.DataFrame({
        "departure_time": [9 * 3600], "arrival_time": [9 * 3600 + 600],
        "preceding_purpose": ["home"], "following_purpose": ["home"],
        "is_first_trip": [True], "is_last_trip": [True], "mode": ["pt"],
    })
    donor_chains = {"Y": [chain_a, chain_b]}
    # Only one weight for two chains -> length mismatch.
    donor_weights = {"Y": [1.0]}
    person_cells = {"p1": "Y"}
    unfixable_trips = pd.DataFrame({
        "person_id": ["p1"], "departure_time": [7 * 3600.0],
        "arrival_time": [7 * 3600.0], "preceding_purpose": ["home"],
        "following_purpose": ["work"], "is_first_trip": [True], "is_last_trip": [True],
        "mode": ["walk"],
    })
    with pytest.raises(ValueError, match="Y"):
        pv.resample_chains(
            unfixable_trips, ["p1"], person_cells, donor_chains,
            rng=np.random.RandomState(0), donor_weights=donor_weights,
        )
