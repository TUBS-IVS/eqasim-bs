"""Cascade stage A: impute times for MiD coded-time chains from own wegmin_imp1.

About 10 % of MiD 2023 Wege carry the design code 701 in all four time fields
("bei regelmaessigen beruflichen Wegen nicht erhoben" — rbW summary records of
systematically REGULAR COMMUTERS) and ~1 % the item-nonresponse code 99.
``braunschweig.popsim.trips.mid_time_seconds`` NaNs those times, so the owning
persons are classified ``nan_times``-unfixable.  Replacing their whole chain by
a same-cell donor (the previous behaviour) systematically dilutes work travel,
because the affected group is not missing-at-random.

Stage A therefore KEEPS the person's own chain — purposes, modes and the
MiD-imputed per-trip duration ``wegmin_imp1`` (minutes; fully populated and
code-free for all rbW rows, audited) and ``wegkm_imp`` distances are real —
and imputes ONLY the missing clock times:

- ``dep_0``  ~ empirical first-departure pool of the VALID persons in the same
  table, grouped by the first trip's ``following_purpose``;
- ``arr_i  = dep_i + wegmin_imp1_i * 60`` (the person's OWN trip duration);
- ``dep_{i+1} = arr_i +`` empirical activity-duration draw, grouped by
  ``following_purpose_i`` (the activity AT that trip's destination).

ASSUMPTION: first-departure anchors and between-trip activity durations are
transferable from the valid same-purpose reporters in the same trip table to
the coded-time persons.  Only the chain anchoring/dwell times are borrowed;
trip durations and distances remain the person's own MiD values.  When a
purpose group has no pool entries, the ungrouped pool is used and the fallback
is logged (no silent fallback).

Both pools are restricted to WITHIN-BOUND contributors: persons whose entire
chain stays within ``max_plan_time_seconds`` AND whose first departure is on
the survey day (< 24 h).  Valid-but-late chains (midnight-shifted diaries,
bound-exceeding reporters) would otherwise seed late anchors / very long
dwells that systematically overflow the reconstructed chains.  The exclusion
count is logged.

Chains that still overflow after the LAST redraw attempt are not skipped
blindly: a deterministic proportional dwell-scaling pass shrinks the drawn
activity durations (floored at ``MIN_ACTIVITY_SECONDS``) so the chain ends
exactly at the bound.  Only truly impossible chains (the person's own fixed
trip durations alone exceed the bound, or the dwell budget falls below the
floor) are skipped to stage B.

``activity_duration`` is intentionally NOT recomputed here: the caller re-runs
the standard recompute sequence (``hts.compute_activity_duration`` inside
``PlanValidator.repair_trips``) on the full table after imputation, which also
applies home-end closure to the now-timed chains.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# MiD numeric missing/design codes in wegmin_imp1 would start at 9994
# (9994 "unplausibel", 9995/9999-style non-response).  Audited: the rbW rows
# carry NO codes (range 1..343 minutes), but the guard keeps a future delivery
# from silently injecting code values as multi-day durations.
WEGMIN_CODE_THRESHOLD = 9994.0

# Child-stream offset for the stage A RNG: RandomState(random_seed + OFFSET).
# Arbitrary but fixed; decorrelates the imputation draws from the resample and
# jitter streams, which both consume RandomState(random_seed) directly.
TIME_IMPUTATION_SEED_OFFSET = 4159

# Minimum activity (dwell) duration in SECONDS the proportional dwell-scaling
# pass may shrink a drawn activity to (5 minutes).  ASSUMPTION: a shorter stay
# is not a meaningful out-of-home activity; the floor keeps scaled chains
# physically plausible while letting long drawn dwells absorb the compression.
MIN_ACTIVITY_SECONDS = 300.0

# A diary day starts on the survey day: anchors at or beyond 24 h come from
# midnight-shifted (repaired) chains and must not seed new first departures.
SECONDS_PER_DAY = 86400.0


@dataclass(frozen=True)
class TimeImputationReport:
    """Summary of how ``impute_chain_times`` handled the candidate persons."""

    n_candidates: int
    n_imputed: int
    n_skipped: int
    imputed_persons: set
    skipped_persons: set
    # Chains saved by the deterministic dwell-scaling pass (counted within
    # n_imputed): the blind redraws all overflowed the plan bound, but
    # proportionally shrinking the drawn dwells made the chain fit.
    n_dwell_scaled: int = 0


def _first_trip_rows(valid: pd.DataFrame) -> pd.DataFrame:
    """One row per valid person: the first trip by departure time (stable)."""
    ordered = valid.sort_values(["person_id", "departure_time"], kind="mergesort")
    return ordered.drop_duplicates(subset="person_id", keep="first")


def impute_chain_times(
    df_trips: pd.DataFrame,
    nan_time_persons,
    *,
    rng,
    max_plan_time_seconds: float,
    max_attempts: int = 5,
) -> tuple[pd.DataFrame, TimeImputationReport]:
    """Impute departure/arrival times for coded-time persons from empirical pools.

    Parameters
    ----------
    df_trips:
        Trip table with at least ``person_id``, ``departure_time``,
        ``arrival_time``, ``activity_duration``, ``following_purpose``,
        ``wegmin_imp1`` and a within-person order column (``W_ID`` preferred;
        ``trip_index`` as fallback).  ``W_ID`` is preferred because the
        repair pass sorts by ``(person_id, departure_time)`` with NaNs last,
        which scrambles ``trip_index`` for mixed-NaN (99-coded) chains, while
        ``W_ID`` always preserves the original MiD diary order.
    nan_time_persons:
        Person ids whose times are NaN and shall be imputed (stage A
        candidates).  These persons are excluded from the empirical pools.
    rng:
        Seeded ``numpy.random.RandomState``; all draws use it (determinism is
        mandatory; iteration over candidates is sorted).
    max_plan_time_seconds:
        Absolute plan bound; a reconstructed chain whose last arrival exceeds
        it is redrawn up to ``max_attempts`` times, then the person is skipped
        (stage B / resample handles skipped persons).
    max_attempts:
        Maximum redraws per person before giving up.

    Returns
    -------
    tuple[pd.DataFrame, TimeImputationReport]
        A copy of ``df_trips`` with the imputed times written (and
        ``trip_duration`` recomputed for imputed rows), plus the report.
        ``activity_duration`` is left to the caller's standard recompute
        (see module docstring).
    """
    nan_time_persons = set(nan_time_persons)
    df = df_trips.copy()

    if "wegmin_imp1" not in df.columns:
        raise ValueError(
            "[popsim.time_imputation] column 'wegmin_imp1' is missing from the trip "
            "table; it is required to reconstruct coded-time chains (the caller must "
            "load it — see MID_WEGE_REQUIRED_COLS)."
        )
    if "W_ID" in df.columns:
        order_col = "W_ID"
    elif "trip_index" in df.columns:
        order_col = "trip_index"
    else:
        raise ValueError(
            "[popsim.time_imputation] no within-person trip order column found "
            "(need 'W_ID' or 'trip_index') — cannot reconstruct chains in diary order."
        )

    # ------------------------------------------------------------------ pools
    # Valid persons = NOT stage A candidates AND no NaN time on any trip (this
    # also excludes other coded-time persons the caller did not pass).
    nan_rows = df["departure_time"].isna() | df["arrival_time"].isna()
    persons_with_nan = set(df.loc[nan_rows, "person_id"].unique())
    valid = df[~df["person_id"].isin(nan_time_persons | persons_with_nan)]

    # Pool filter: only WITHIN-BOUND contributors feed the anchor and the
    # activity-duration pools — the entire chain must stay within the plan
    # bound AND the first departure must be on the survey day (< 24 h).
    # Valid-but-late chains (midnight-shifted diaries, bound-exceeding
    # reporters) would otherwise seed late anchors / overlong dwells that
    # systematically overflow the reconstructed chains (observed: 18.4 % of
    # stage A candidates skipped on the bound in the 1 % smoke).
    if not valid.empty:
        person_last_arrival = valid.groupby("person_id")["arrival_time"].max()
        person_first_departure = valid.groupby("person_id")["departure_time"].min()
        within_bound = (
            (person_last_arrival <= max_plan_time_seconds)
            & (person_first_departure < SECONDS_PER_DAY)
        )
        n_pool_total = int(len(within_bound))
        n_pool_excluded = int((~within_bound).sum())
        if n_pool_excluded:
            logger.info(
                "[popsim.time_imputation] pool filter: excluded %d/%d (%.1f%%) "
                "valid persons from the anchor/activity pools (first departure "
                ">= 24h or chain beyond the %.0fh plan bound).",
                n_pool_excluded, n_pool_total,
                100.0 * n_pool_excluded / n_pool_total,
                max_plan_time_seconds / 3600.0,
            )
        valid = valid[valid["person_id"].isin(within_bound[within_bound].index)]

    if valid.empty:
        # No empirical pools can be built: every candidate must go to stage B.
        logger.warning(
            "[popsim.time_imputation] no within-bound valid persons available to "
            "build the empirical pools; all %d candidates are skipped (routed to "
            "stage B).",
            len(nan_time_persons),
        )
        report = TimeImputationReport(
            n_candidates=len(nan_time_persons), n_imputed=0,
            n_skipped=len(nan_time_persons), imputed_persons=set(),
            skipped_persons=set(nan_time_persons),
        )
        return df, report

    # (a) first-departure pool, grouped by the first trip's following_purpose.
    firsts = _first_trip_rows(valid)
    departure_pool_by_purpose = {
        purpose: group["departure_time"].to_numpy()
        for purpose, group in firsts.groupby("following_purpose")
    }
    departure_pool_all = firsts["departure_time"].to_numpy()

    # (b) activity-duration pool (non-NaN, > 0), grouped by following_purpose
    # = the activity AT that trip's destination.
    activities = valid.sort_values(["person_id", "departure_time"], kind="mergesort")
    activities = activities[
        activities["activity_duration"].notna() & (activities["activity_duration"] > 0)
    ]
    activity_pool_by_purpose = {
        purpose: group["activity_duration"].to_numpy()
        for purpose, group in activities.groupby("following_purpose")
    }
    activity_pool_all = activities["activity_duration"].to_numpy()

    # ------------------------------------------------------------- per person
    imputed_persons: set = set()
    skipped_persons: set = set()
    departure_fallback_purposes: set = set()
    activity_fallback_purposes: set = set()
    n_bound_giveups = 0
    n_bad_wegmin = 0
    n_dwell_scaled = 0

    candidate_rows = df[df["person_id"].isin(nan_time_persons)]
    groups = {person_id: group for person_id, group in candidate_rows.groupby("person_id")}

    missing_from_table = nan_time_persons - set(groups)
    if missing_from_table:
        logger.warning(
            "[popsim.time_imputation] %d candidate persons have no rows in the trip "
            "table and are skipped: %s",
            len(missing_from_table), sorted(missing_from_table)[:10],
        )
        skipped_persons |= missing_from_table

    for person_id in sorted(groups):
        group = groups[person_id].sort_values(order_col, kind="mergesort")
        wegmin = group["wegmin_imp1"].astype(float)
        # Require a complete, code-free own duration on EVERY trip; partial
        # chains cannot be reconstructed and go to stage B.
        if wegmin.isna().any() or (wegmin >= WEGMIN_CODE_THRESHOLD).any() or (wegmin <= 0).any():
            skipped_persons.add(person_id)
            n_bad_wegmin += 1
            continue

        trip_durations_s = wegmin.to_numpy() * 60.0
        purposes = group["following_purpose"].tolist()

        first_departure_pool = departure_pool_by_purpose.get(purposes[0])
        if first_departure_pool is None:
            first_departure_pool = departure_pool_all
            departure_fallback_purposes.add(purposes[0])

        chain = None
        last_attempt_state = None  # (anchor, drawn_dwells) of the last full draw
        for attempt in range(max_attempts):
            # Progressive anchor tightening: an overflow is most often caused
            # by a late anchor, and re-drawing from the FULL pool just repeats
            # that failure mode.  Attempt k therefore restricts the anchor
            # pool to values below its (1 - k/max_attempts) quantile, so the
            # last attempt draws from the earliest part of the distribution —
            # maximising the dwell budget left for the deterministic
            # dwell-scaling pass below.
            if attempt == 0:
                anchor_pool = first_departure_pool
            else:
                quantile = 1.0 - attempt / max_attempts
                cutoff = float(np.quantile(first_departure_pool, quantile))
                anchor_pool = first_departure_pool[first_departure_pool <= cutoff]
                if len(anchor_pool) == 0:
                    anchor_pool = np.array([float(np.min(first_departure_pool))])
            anchor = float(rng.choice(anchor_pool))
            departure = anchor
            drawn_dwells: list = []
            departures, arrivals = [], []
            for i, duration_s in enumerate(trip_durations_s):
                arrival = departure + duration_s
                departures.append(departure)
                arrivals.append(arrival)
                if i + 1 < len(trip_durations_s):
                    pool = activity_pool_by_purpose.get(purposes[i])
                    if pool is None or len(pool) == 0:
                        pool = activity_pool_all
                        activity_fallback_purposes.add(purposes[i])
                    if len(pool) == 0:
                        # No activity-duration pool at all: cannot chain trips.
                        departures = None
                        break
                    dwell = float(rng.choice(pool))
                    drawn_dwells.append(dwell)
                    departure = arrival + dwell
            if departures is None:
                break
            last_attempt_state = (anchor, drawn_dwells)
            if arrivals[-1] <= max_plan_time_seconds:
                chain = (departures, arrivals)
                break

        if chain is None and last_attempt_state is not None:
            # Deterministic fit: the blind redraws all overflowed, but the
            # chain may still FIT by compressing the drawn dwells.  Allocate
            # the floor (MIN_ACTIVITY_SECONDS) to every activity, distribute
            # the remaining dwell budget proportionally to the drawn dwells —
            # the chain then ends exactly at the bound and every dwell stays
            # at or above the floor.  Only when even that is impossible (the
            # fixed trip durations alone exceed the bound, or the budget falls
            # below the floor) is the person skipped to stage B.
            anchor, drawn_dwells = last_attempt_state
            n_activities = len(trip_durations_s) - 1
            dwell_budget = (
                max_plan_time_seconds - anchor - float(np.sum(trip_durations_s))
            )
            if n_activities > 0 and dwell_budget > n_activities * MIN_ACTIVITY_SECONDS:
                dwells = np.asarray(drawn_dwells, dtype=float)
                remaining = dwell_budget - n_activities * MIN_ACTIVITY_SECONDS
                scaled = MIN_ACTIVITY_SECONDS + dwells / dwells.sum() * remaining
                departures, arrivals = [], []
                departure = anchor
                for i, duration_s in enumerate(trip_durations_s):
                    arrival = departure + duration_s
                    departures.append(departure)
                    arrivals.append(arrival)
                    if i + 1 < len(trip_durations_s):
                        departure = arrival + float(scaled[i])
                chain = (departures, arrivals)
                n_dwell_scaled += 1

        if chain is None:
            skipped_persons.add(person_id)
            n_bound_giveups += 1
            continue

        df.loc[group.index, "departure_time"] = chain[0]
        df.loc[group.index, "arrival_time"] = chain[1]
        imputed_persons.add(person_id)

    # Recompute trip_duration for the imputed rows from the imputed times.
    if imputed_persons:
        mask = df["person_id"].isin(imputed_persons)
        df.loc[mask, "trip_duration"] = (
            df.loc[mask, "arrival_time"] - df.loc[mask, "departure_time"]
        )

    # -------------------------------------------------------------- reporting
    n_candidates = len(nan_time_persons)
    n_imputed = len(imputed_persons)
    n_skipped = len(skipped_persons)
    logger.info(
        "[popsim.time_imputation] imputed %d/%d (%.1f%%) coded-time persons from own "
        "wegmin_imp1 + empirical anchors; skipped %d (%.1f%%) -> stage B "
        "(%d missing/coded wegmin_imp1, %d exceeded the %.0fh plan bound after "
        "%d attempts + dwell scaling, %d absent from the table); "
        "n_dwell_scaled=%d (%.1f%% of candidates saved by proportional dwell scaling).",
        n_imputed, n_candidates,
        100.0 * n_imputed / n_candidates if n_candidates > 0 else 0.0,
        n_skipped,
        100.0 * n_skipped / n_candidates if n_candidates > 0 else 0.0,
        n_bad_wegmin, n_bound_giveups, max_plan_time_seconds / 3600.0,
        max_attempts, len(missing_from_table),
        n_dwell_scaled,
        100.0 * n_dwell_scaled / n_candidates if n_candidates > 0 else 0.0,
    )
    if departure_fallback_purposes:
        logger.info(
            "[popsim.time_imputation] first-departure pool fell back to the ungrouped "
            "pool for purposes %s (no valid person starts the day with that purpose).",
            sorted(departure_fallback_purposes),
        )
    if activity_fallback_purposes:
        logger.info(
            "[popsim.time_imputation] activity-duration pool fell back to the "
            "ungrouped pool for purposes %s.",
            sorted(activity_fallback_purposes),
        )

    report = TimeImputationReport(
        n_candidates=n_candidates,
        n_imputed=n_imputed,
        n_skipped=n_skipped,
        imputed_persons=imputed_persons,
        skipped_persons=skipped_persons,
        n_dwell_scaled=n_dwell_scaled,
    )
    return df, report
