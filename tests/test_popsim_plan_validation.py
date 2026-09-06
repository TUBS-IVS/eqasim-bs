from __future__ import annotations
import pandas as pd
import pytest
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


# ---------------------------------------------------------------------------
# Task 2.3 A: NaN trip times (MiD coded times 99/701 -> NaN) must be flagged as
# an explicit issue and classified unfixable WITHOUT crashing repair_trips.
# ---------------------------------------------------------------------------

def test_nan_times_flagged_and_unfixable_no_crash():
    import pandas as pd, numpy as np
    from braunschweig.popsim.plan_validation import PlanValidator
    df = pd.DataFrame({
        "person_id": [1, 1, 2, 2],
        "departure_time": [np.nan, np.nan, 28800.0, 61200.0],
        "arrival_time":   [np.nan, np.nan, 30600.0, 63000.0],
        "preceding_purpose": ["home", "work", "home", "work"],
        "following_purpose": ["work", "home", "work", "home"],
        "is_first_trip": [True, False, True, False],
        "is_last_trip":  [False, True, False, True],
        "mode": ["car"] * 4,
    })
    v = PlanValidator(require_home_closure=True)
    report = v.validate_trips(df)
    assert report.issue_counts.get("nan_times", 0) >= 1     # person 1 flagged
    fixed, rep = v.repair_trips(df)                          # must NOT crash (KeyError: nan today)
    assert 1 in rep.unfixable_persons
    assert 2 not in rep.unfixable_persons


def test_nan_times_mixed_person_is_unfixable_and_gets_no_bogus_closure():
    """F4: a person with SOME NaN times is treated as unfixable as a whole;
    the home-closure repair must not append a return-home trip for them."""
    import numpy as np

    df = pd.DataFrame({
        "person_id": ["m", "m"],
        "departure_time": [8 * 3600.0, np.nan],
        "arrival_time": [8 * 3600.0 + 1800.0, np.nan],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "leisure"],
        "is_first_trip": [True, False],
        "is_last_trip": [False, True],
        "mode": ["car", "car"],
    })
    v = pv.PlanValidator(require_home_closure=True)
    fixed, rep = v.repair_trips(df)
    assert "m" in rep.unfixable_persons
    # No synthetic return-home trip may be appended to a NaN-time person.
    assert len(fixed[fixed["person_id"] == "m"]) == 2


def test_times_exceeding_bound_flagged_and_unfixable():
    """Times beyond MAX_PLAN_TIME_SECONDS (36h) mark the person unfixable.

    Chains crossing midnight may legitimately pass 24h via the +24h shift in
    hts.fix_trip_times, but beyond 36h a chain is pathological and must be
    routed to the same-cell resample instead of surviving into the jitter."""
    import pandas as pd
    from braunschweig.popsim.plan_validation import PlanValidator
    df = pd.DataFrame({
        "person_id": [1, 1, 2, 2],
        "departure_time": [28800.0, 140000.0, 28800.0, 61200.0],   # person 1: 2nd trip ~38.9h
        "arrival_time":   [30600.0, 141000.0, 30600.0, 63000.0],
        "preceding_purpose": ["home", "work", "home", "work"],
        "following_purpose": ["work", "home", "work", "home"],
        "is_first_trip": [True, False, True, False],
        "is_last_trip":  [False, True, False, True],
        "mode": ["car"] * 4,
    })
    v = PlanValidator(require_home_closure=True)
    report = v.validate_trips(df)
    assert report.issue_counts.get("time_exceeds_bound", 0) >= 1
    fixed, rep = v.repair_trips(df)
    assert 1 in rep.unfixable_persons
    assert 2 not in rep.unfixable_persons


# ---------------------------------------------------------------------------
# Task 5: empirical closure dwell + marked synthetic closure rows
# ---------------------------------------------------------------------------

def _open_trips():
    return pd.DataFrame({
        "person_id": ["p"], "departure_time": [8 * 3600], "arrival_time": [8 * 3600 + 1800],
        "preceding_purpose": ["home"], "following_purpose": ["work"],
        "is_first_trip": [True], "is_last_trip": [True], "trip_key": ["p_1"],
    })


def test_repair_marks_synthetic_closure_and_uses_dwell_model():
    from braunschweig.popsim.closure_dwell import ClosureDwellModel
    fixed, _ = pv.PlanValidator().repair_trips(_open_trips())
    assert fixed["is_synthetic_closure"].tolist() == [False, True]
    assert fixed.loc[1, "trip_key"] == "p_closure"
    assert fixed.loc[1, "departure_time"] == 8 * 3600 + 1800 + pv.HOME_CLOSURE_DWELL_S
    model = ClosureDwellModel.fixed(2 * 3600.0)
    fixed2, _ = pv.PlanValidator().repair_trips(_open_trips(), dwell_model=model)
    assert fixed2.loc[1, "departure_time"] == 8 * 3600 + 1800 + 2 * 3600


def test_repair_without_open_end_has_no_synthetic_rows():
    fixed, _ = pv.PlanValidator().repair_trips(_good_trips())
    assert not fixed["is_synthetic_closure"].any()


# ---------------------------------------------------------------------------
# Fix round 1, CRITICAL-1: a second repair_trips pass on an already-repaired
# table (the normal production path via trips._impute_nan_time_unfixable's
# stage-A re-repair) must not reset an earlier pass's is_synthetic_closure
# flag back to False.
# ---------------------------------------------------------------------------

def test_is_synthetic_closure_survives_a_second_repair_pass():
    fixed_once, _ = pv.PlanValidator().repair_trips(_open_trips())
    assert fixed_once.sort_values("departure_time")["is_synthetic_closure"].tolist() == [False, True]

    # A second repair_trips call on the already-closed output (the chain now
    # ends at home, so no NEW row is appended) must leave the earlier True
    # flag and the "_closure" trip_key untouched.
    fixed_twice, _ = pv.PlanValidator().repair_trips(fixed_once)
    ordered = fixed_twice.sort_values("departure_time")
    assert ordered["is_synthetic_closure"].tolist() == [False, True], (
        "a second repair_trips pass must not reset an earlier closure row's "
        "is_synthetic_closure flag back to False"
    )
    closure_row = fixed_twice[fixed_twice["is_synthetic_closure"]]
    assert (closure_row["trip_key"] == "p_closure").all()


# ---------------------------------------------------------------------------
# Fix round 1, Important-2: dwell_model must be threaded into the stage-A
# re-repair inside trips._impute_nan_time_unfixable -- for a stage-A person
# that re-repair IS their only home-end closure.
# ---------------------------------------------------------------------------

def test_dwell_model_threaded_into_stage_a_reimpute_closure():
    import numpy as np

    from braunschweig.popsim import trips as popsim_trips
    from braunschweig.popsim.closure_dwell import ClosureDwellModel

    # v1: a complete, valid, already home-closed donor chain (home->work->home)
    # that feeds the stage-A empirical pools (first-departure @8:00 for "work").
    # n1: a coded-time (NaN) person with ONE real trip home->work (own
    # wegmin_imp1=12min); stage A imputes its times, and since the chain still
    # does not end at home the re-repair inside _impute_nan_time_unfixable
    # must append the closure -- using the given dwell_model, not the constant.
    raw = pd.DataFrame({
        "person_id": ["v1", "v1", "n1"],
        "departure_time": [8 * 3600.0, 8 * 3600.0 + 600.0 + 30600.0, np.nan],
        "arrival_time": [8 * 3600.0 + 600.0, 8 * 3600.0 + 600.0 + 30600.0 + 600.0, np.nan],
        "preceding_purpose": ["home", "work", "home"],
        "following_purpose": ["work", "home", "work"],
        "is_first_trip": [True, False, True],
        "is_last_trip": [False, True, True],
        "wegmin_imp1": [10.0, 10.0, 12.0],
        "W_ID": [1, 2, 1],
    })
    validator = pv.PlanValidator(require_home_closure=True)
    table, repair_report = validator.repair_trips(raw)
    assert "n1" in repair_report.unfixable_persons, "n1's NaN times must be unfixable after pass 1"
    assert "v1" not in repair_report.unfixable_persons

    model = ClosureDwellModel.fixed(2 * 3600.0)
    table2, report2 = popsim_trips._impute_nan_time_unfixable(
        table, repair_report, validator, random_seed=0, dwell_model=model,
    )

    n1 = table2[table2["person_id"] == "n1"].sort_values("departure_time")
    assert len(n1) == 2, "n1's imputed single trip does not end at home -> a closure row must be appended"
    closure_row = n1[n1["is_synthetic_closure"]]
    original_row = n1[~n1["is_synthetic_closure"]]
    assert len(closure_row) == 1 and len(original_row) == 1
    assert closure_row.iloc[0]["departure_time"] == pytest.approx(
        original_row.iloc[0]["arrival_time"] + 2 * 3600.0
    ), "the appended closure departure must use the dwell_model's dwell (2h), not the constant HOME_CLOSURE_DWELL_S"


# ---------------------------------------------------------------------------
# Fix round 1, Important-3: the appended closure departure must be capped so
# the arrival never exceeds MAX_PLAN_TIME_SECONDS, even with a long empirical
# dwell draw and an already-late last arrival.
# ---------------------------------------------------------------------------

def test_repair_caps_empirical_closure_dwell_at_plan_time_bound():
    import numpy as np

    from braunschweig.popsim.closure_dwell import ClosureDwellModel

    # A single donor observation with a 19.5h "work" activity duration (well
    # within max_dwell_s=24h) -- deliberately long so an uncapped draw would
    # push the appended arrival far past the 36h plan bound.
    donor = pd.DataFrame({
        "person_id": [9, 9],
        "departure_time": [8 * 3600.0, 28 * 3600.0],
        "arrival_time": [8 * 3600.0 + 1800.0, 28 * 3600.0 + 1800.0],
        "following_purpose": ["work", "home"],
    })
    model = ClosureDwellModel.from_trips(
        donor, rng=np.random.RandomState(0), min_obs=1, max_dwell_s=24 * 3600,
    )

    # Last activity ("work") arrives only 30 minutes before the plan bound;
    # travel_time back is 1800s, so ANY dwell > 0 would already exceed the bound.
    df = pd.DataFrame({
        "person_id": ["p"],
        "departure_time": [pv.MAX_PLAN_TIME_SECONDS - 3600],
        "arrival_time": [pv.MAX_PLAN_TIME_SECONDS - 1800],
        "preceding_purpose": ["home"], "following_purpose": ["work"],
        "is_first_trip": [True], "is_last_trip": [True],
    })
    fixed, _ = pv.PlanValidator().repair_trips(df, dwell_model=model)

    closure = fixed[fixed["is_synthetic_closure"]]
    assert len(closure) == 1
    assert closure.iloc[0]["arrival_time"] <= pv.MAX_PLAN_TIME_SECONDS, (
        "the appended arrival must never exceed the plan-time bound, even with "
        "a long empirical dwell draw"
    )
    assert model.report["n_capped"] == 1


# ---------------------------------------------------------------------------
# Fix round 1, Important-4: repair_trips must log an explicit rate line (not
# the raw report dict) and WARN when the global-fallback rate is a majority.
# ---------------------------------------------------------------------------

def test_log_closure_dwell_rates_logs_explicit_rate_line(caplog):
    import logging

    report = {
        "n_draws": 10, "n_fallback_purpose_marginal": 2,
        "n_fallback_global": 1, "n_capped": 1,
    }
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.plan_validation"):
        pv._log_closure_dwell_rates(report)

    info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "closure dwell" in m and "10 draws total" in m
        and "cell 7/10" in m and "purpose-marginal fallback 2/10" in m
        and "global fallback 1/10" in m and "capped 1/10" in m
        for m in info_messages
    ), info_messages
    # 1/10 = 10% global fallback, well below the 50% warn threshold.
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


def test_log_closure_dwell_rates_warns_on_majority_global_fallback(caplog):
    import logging

    majority_report = {
        "n_draws": 10, "n_fallback_purpose_marginal": 0,
        "n_fallback_global": 6, "n_capped": 0,
    }
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.plan_validation"):
        pv._log_closure_dwell_rates(majority_report)
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("global-fallback rate" in m for m in warnings), warnings

    caplog.clear()
    minority_report = {
        "n_draws": 10, "n_fallback_purpose_marginal": 0,
        "n_fallback_global": 4, "n_capped": 0,
    }
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.plan_validation"):
        pv._log_closure_dwell_rates(minority_report)
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


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
