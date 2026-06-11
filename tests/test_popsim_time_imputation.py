"""Tests for cascade stage A time imputation (braunschweig.popsim.time_imputation).

MiD rbW chains (time code 701) and k.A. chains (code 99) have NaN times after
mid_time_seconds, but their purposes, modes and the MiD-imputed per-trip
duration ``wegmin_imp1`` (minutes) are real.  Stage A keeps the person's own
chain and imputes ONLY the times: the first departure and the between-trip
activity durations are drawn from empirical pools built from the valid persons
in the same table.  Tiny synthetic fixtures only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim.time_imputation import impute_chain_times


def _base_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal trips frame with the columns stage A consumes."""
    df = pd.DataFrame(rows)
    for col in ("departure_time", "arrival_time", "activity_duration",
                "trip_duration", "wegmin_imp1"):
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df


def _valid_person(person_id: str, dep0_s: float, purposes: list[str],
                  trip_s: float = 600.0, activity_s: float = 28200.0) -> list[dict]:
    """Rows for one VALID person: sequential trips, real times, no NaN."""
    rows = []
    dep = dep0_s
    for i, purpose in enumerate(purposes):
        arr = dep + trip_s
        is_last = i == len(purposes) - 1
        rows.append({
            "person_id": person_id, "W_ID": i + 1,
            "departure_time": dep, "arrival_time": arr,
            "trip_duration": trip_s,
            "activity_duration": float("nan") if is_last else activity_s,
            "following_purpose": purpose,
            "wegmin_imp1": trip_s / 60.0,
        })
        dep = arr + activity_s
    return rows


def _nan_time_person(person_id: str, purposes: list[str],
                     wegmin: list[float]) -> list[dict]:
    """Rows for one NaN-time (coded-time) person: real wegmin_imp1, NaN times."""
    return [{
        "person_id": person_id, "W_ID": i + 1,
        "departure_time": float("nan"), "arrival_time": float("nan"),
        "trip_duration": float("nan"), "activity_duration": float("nan"),
        "following_purpose": purpose, "wegmin_imp1": wegmin[i],
    } for i, purpose in enumerate(purposes)]


MAX_PLAN_S = 36 * 3600.0


def test_impute_reconstructs_chain_from_own_durations_and_pools():
    # One valid person provides the pools: first departure 08:00 (work),
    # activity duration 30600s at work.  One nan-time person, 2 trips,
    # wegmin_imp1 = 10 and 15 minutes (work then home).
    rows = _valid_person("v1", 8 * 3600.0, ["work", "home"],
                         trip_s=600.0, activity_s=30600.0)
    rows += _nan_time_person("n1", ["work", "home"], [10.0, 15.0])
    df = _base_frame(rows)

    out, report = impute_chain_times(
        df, {"n1"}, rng=np.random.RandomState(0),
        max_plan_time_seconds=MAX_PLAN_S,
    )

    assert report.n_candidates == 1
    assert report.n_imputed == 1
    assert report.n_skipped == 0
    assert report.imputed_persons == {"n1"}

    chain = out[out["person_id"] == "n1"].sort_values("W_ID")
    dep = chain["departure_time"].to_numpy()
    arr = chain["arrival_time"].to_numpy()
    assert not np.isnan(dep).any() and not np.isnan(arr).any()
    # dep_0 must come from the first-departure pool (only one value: 08:00).
    assert dep[0] == 8 * 3600.0
    # arr_i = dep_i + wegmin_imp1_i * 60 (the person's OWN MiD durations).
    assert arr[0] == dep[0] + 10.0 * 60.0
    assert arr[1] == dep[1] + 15.0 * 60.0
    # Monotone chain: next departure after previous arrival.
    assert dep[1] >= arr[0]
    # trip_duration recomputed from the imputed times.
    assert (chain["trip_duration"].to_numpy() == arr - dep).all()
    # The valid person's rows are untouched.
    v1 = out[out["person_id"] == "v1"].sort_values("W_ID")
    assert v1["departure_time"].iloc[0] == 8 * 3600.0


def test_impute_is_deterministic_per_seed():
    # Several valid persons -> pools with multiple distinct values, so two
    # different seeds (almost surely) produce different chains while the same
    # seed always reproduces identical times.
    rows: list[dict] = []
    for i, dep0_h in enumerate([6, 7, 8, 9, 10]):
        rows += _valid_person(f"v{i}", dep0_h * 3600.0, ["work", "home"],
                              trip_s=600.0 + 60.0 * i,
                              activity_s=20000.0 + 1000.0 * i)
    rows += _nan_time_person("n1", ["work", "home"], [10.0, 15.0])
    df = _base_frame(rows)

    out_a, _ = impute_chain_times(df, {"n1"}, rng=np.random.RandomState(7),
                                  max_plan_time_seconds=MAX_PLAN_S)
    out_b, _ = impute_chain_times(df, {"n1"}, rng=np.random.RandomState(7),
                                  max_plan_time_seconds=MAX_PLAN_S)
    out_c, _ = impute_chain_times(df, {"n1"}, rng=np.random.RandomState(8),
                                  max_plan_time_seconds=MAX_PLAN_S)

    times_a = out_a.loc[out_a["person_id"] == "n1",
                        ["departure_time", "arrival_time"]].to_numpy()
    times_b = out_b.loc[out_b["person_id"] == "n1",
                        ["departure_time", "arrival_time"]].to_numpy()
    times_c = out_c.loc[out_c["person_id"] == "n1",
                        ["departure_time", "arrival_time"]].to_numpy()
    assert (times_a == times_b).all()
    assert (times_a != times_c).any()


def test_impute_skips_person_when_chain_exceeds_plan_bound():
    # The only valid anchor departs at 35h; the candidate's own trip lasts
    # 120 min -> last arrival 37h > 36h bound on EVERY attempt -> skipped,
    # times stay NaN (stage B handles the person).
    rows = _valid_person("v1", 35 * 3600.0, ["work"], trip_s=600.0)
    rows += _nan_time_person("n1", ["work"], [120.0])
    df = _base_frame(rows)

    out, report = impute_chain_times(
        df, {"n1"}, rng=np.random.RandomState(0),
        max_plan_time_seconds=MAX_PLAN_S, max_attempts=5,
    )

    assert report.n_imputed == 0
    assert report.n_skipped == 1
    assert report.skipped_persons == {"n1"}
    chain = out[out["person_id"] == "n1"]
    assert chain["departure_time"].isna().all()
    assert chain["arrival_time"].isna().all()


def test_impute_skips_person_with_missing_wegmin(caplog):
    # A NaN (or coded >= 9994) wegmin_imp1 on ANY trip disqualifies the whole
    # person from stage A (no partial chain reconstruction).
    rows = _valid_person("v1", 8 * 3600.0, ["work", "home"])
    rows += _nan_time_person("n1", ["work", "home"], [10.0, float("nan")])
    rows += _nan_time_person("n2", ["work", "home"], [10.0, 9994.0])
    df = _base_frame(rows)

    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.time_imputation"):
        out, report = impute_chain_times(
            df, {"n1", "n2"}, rng=np.random.RandomState(0),
            max_plan_time_seconds=MAX_PLAN_S,
        )

    assert report.n_candidates == 2
    assert report.n_imputed == 0
    assert report.skipped_persons == {"n1", "n2"}
    assert out.loc[out["person_id"].isin(["n1", "n2"]), "departure_time"].isna().all()
    # The skip is logged (no silent fallback).
    assert any("skipped" in record.getMessage() for record in caplog.records)


def test_pool_excludes_late_first_departures(caplog):
    # v_late is a VALID person (no NaN, chain within the bound) but its first
    # departure is 25h (a midnight-shifted diary): it must NOT contribute
    # anchors or activity durations.  v_early is then the ONLY pool
    # contributor, so the candidate's imputed chain is fully determined by it:
    # dep_0 = 8h (v_early's anchor) and the work dwell = v_early's 30600s.
    rows = _valid_person("v_early", 8 * 3600.0, ["work", "home"],
                         trip_s=600.0, activity_s=30600.0)
    rows += _valid_person("v_late", 25 * 3600.0, ["work", "home"],
                          trip_s=600.0, activity_s=3600.0)
    rows += _nan_time_person("n1", ["work", "home"], [10.0, 15.0])
    df = _base_frame(rows)

    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.time_imputation"):
        out, report = impute_chain_times(
            df, {"n1"}, rng=np.random.RandomState(0),
            max_plan_time_seconds=MAX_PLAN_S,
        )

    assert report.n_imputed == 1
    chain = out[out["person_id"] == "n1"].sort_values("W_ID")
    dep = chain["departure_time"].to_numpy()
    arr = chain["arrival_time"].to_numpy()
    # The anchor comes from v_early (the only within-day contributor), and the
    # work dwell from v_early's pool entry, NOT from v_late's 3600s.
    assert dep[0] == 8 * 3600.0
    assert dep[1] - arr[0] == 30600.0
    # The pool exclusion is logged (no silent filtering).
    assert any("excluded" in record.getMessage() for record in caplog.records)


def test_dwell_scaling_saves_overflowing_chain():
    # Pools force overflow: the single anchor is 4h and the single work-dwell
    # is 28h.  The candidate has 3 trips -> 2 drawn dwells of 28h each, so the
    # blind reconstruction ends at ~60.5h > 36h on EVERY attempt.  The
    # deterministic dwell-scaling pass must then shrink the dwells
    # proportionally (floored at MIN_ACTIVITY_SECONDS) so the chain ends
    # exactly within the bound -> the person is IMPUTED, not skipped.
    from braunschweig.popsim.time_imputation import MIN_ACTIVITY_SECONDS

    rows = _valid_person("v1", 4 * 3600.0, ["work", "home"],
                         trip_s=600.0, activity_s=28 * 3600.0)
    rows += _nan_time_person("n1", ["work", "work", "home"], [10.0, 10.0, 10.0])
    df = _base_frame(rows)

    out, report = impute_chain_times(
        df, {"n1"}, rng=np.random.RandomState(0),
        max_plan_time_seconds=MAX_PLAN_S, max_attempts=5,
    )

    assert report.n_imputed == 1
    assert report.n_skipped == 0
    assert report.n_dwell_scaled == 1

    chain = out[out["person_id"] == "n1"].sort_values("W_ID")
    dep = chain["departure_time"].to_numpy()
    arr = chain["arrival_time"].to_numpy()
    assert not np.isnan(dep).any() and not np.isnan(arr).any()
    # The chain ends within the plan bound.
    assert arr[-1] <= MAX_PLAN_S
    # Trip durations stay the person's OWN wegmin_imp1.
    assert np.allclose(arr - dep, 10.0 * 60.0)
    # Every scaled dwell respects the 5-minute activity floor.
    dwells = dep[1:] - arr[:-1]
    assert (dwells >= MIN_ACTIVITY_SECONDS).all()


def test_fixed_trip_durations_alone_exceeding_bound_still_skipped():
    # The candidate's own trip durations sum to 40h > the 36h bound: no anchor
    # and no dwell scaling can fit the chain -> skipped to stage B (unchanged
    # semantics for true impossibility).
    rows = _valid_person("v1", 8 * 3600.0, ["work", "home"])
    rows += _nan_time_person("n1", ["work", "home"], [2000.0, 400.0])
    df = _base_frame(rows)

    out, report = impute_chain_times(
        df, {"n1"}, rng=np.random.RandomState(0),
        max_plan_time_seconds=MAX_PLAN_S, max_attempts=5,
    )

    assert report.n_imputed == 0
    assert report.n_skipped == 1
    assert report.n_dwell_scaled == 0
    assert report.skipped_persons == {"n1"}
    chain = out[out["person_id"] == "n1"]
    assert chain["departure_time"].isna().all()
    assert chain["arrival_time"].isna().all()


def test_e2e_build_validated_trip_table_keeps_own_chain(caplog):
    # rbW-style person pA: all four time fields carry the design code 701,
    # but wegmin_imp1 is real -> stage A must KEEP pA's own chain (own
    # purposes / wegkm_imp), impute only the times; pB is the valid same-cell
    # donor that feeds the pools (and would be the stage B resample donor —
    # which must NOT be used here).
    import logging

    from braunschweig.popsim import trips as popsim_trips

    persons = pd.DataFrame({
        "person_id": ["pA", "pB"], "H_ID": [1, 2], "P_ID": [1, 1],
        "ZENSUS100m": ["c1", "c1"],
    })
    wege = pd.DataFrame({
        "H_ID":      [1, 1, 2, 2],
        "P_ID":      [1, 1, 1, 1],
        "W_ID":      [1, 2, 1, 2],
        # pA: work then home; pB (donor): shop then home.
        "W_ZWECK":   [1, 8, 4, 8],
        "hvm_imp":   [4, 4, 5, 5],
        "W_SZS":     [701, 701, 9, 17],
        "W_SZM":     [701, 701, 0, 0],
        "W_AZS":     [701, 701, 9, 17],
        "W_AZM":     [701, 701, 10, 15],
        "wegkm_imp": [5.0, 6.0, 1.0, 2.0],
        "wegmin_imp1": [10.0, 15.0, 10.0, 15.0],
    })

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.time_imputation"):
        table, report = popsim_trips.build_validated_trip_table(
            persons, wege, resample=True, resample_cell_col="ZENSUS100m",
            random_seed=0,
        )

    chain = table[table["person_id"] == "pA"].sort_values("trip_index")
    # pA keeps the OWN chain: own purposes and own wegkm_imp, not pB's.
    assert chain["following_purpose"].tolist() == ["work", "home"]
    assert chain["wegkm_imp"].tolist() == [5.0, 6.0]
    # Times are imputed and consistent.
    dep = chain["departure_time"].to_numpy()
    arr = chain["arrival_time"].to_numpy()
    assert not np.isnan(dep).any() and not np.isnan(arr).any()
    assert (arr >= dep).all() and dep[1] >= arr[0]
    assert arr[0] == dep[0] + 10.0 * 60.0
    assert arr[1] == dep[1] + 15.0 * 60.0
    # Whole table is valid and NaN-free (report reflects the final state).
    assert report.is_valid
    assert table["departure_time"].notna().all()
    # The imputation rate is logged with imputed=1 (observable, no silent path).
    assert any("imputed 1/1" in record.getMessage() for record in caplog.records)
