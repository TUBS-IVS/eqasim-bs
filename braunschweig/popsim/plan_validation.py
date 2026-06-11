"""Logical validation (and repair) of popsim_mid activity-chain plans.

Mirrors the invariants eqasim enforces on HTS trips (data/hts/hts.py) and adds
the ones eqasim only assumes (home chain-closure). Reports issues per person and
can repair fixable plans (delegating time repair to eqasim's fix_trip_times).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Hard import: the eqasim hts helpers are a non-negotiable dependency for time
# repair.  If the import fails, the module cannot function — fail loudly instead
# of falling back to a re-implementation.
from data.hts import hts  # noqa: E402

logger = logging.getLogger(__name__)

# How long (seconds) after the last arrival the synthetic return-home trip departs.
# Represents the minimum dwell time at the last activity before heading home.
HOME_CLOSURE_DWELL_S = 3600.0

# Absolute plan-time bound (seconds).  Chains crossing midnight may legitimately
# pass 24h via the +24h shift in hts.fix_trip_times, but beyond 36h a chain is
# pathological (very late / multi-shift repaired chains) and is resampled from a
# same-cell donor instead.  This is the authoritative constant; trips_stage
# imports it for its final backstop assertion.
MAX_PLAN_TIME_SECONDS = 36 * 3600


@dataclass(frozen=True)
class RepairReport:
    """Summary of how ``repair_trips`` classified each person."""

    n_persons: int
    n_valid: int
    n_repaired: int
    n_unfixable: int
    repaired_persons: set
    unfixable_persons: set


@dataclass(frozen=True)
class PlanIssue:
    person_id: object
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    n_persons: int
    n_invalid_persons: int
    issues: list
    issue_counts: dict

    @property
    def is_valid(self) -> bool:
        return self.n_invalid_persons == 0

    @property
    def n_invalid(self) -> int:
        return self.n_invalid_persons


def _eqasim_fix_trip_times(df_trips: pd.DataFrame) -> pd.DataFrame:
    """Delegate time repair to ``hts.fix_trip_times``.

    Ensures the columns required by the eqasim helper exist (``trip_id`` as a
    sortable integer, ``is_first_trip``, ``is_last_trip``) before calling it.
    The helper modifies its input in-place and also returns it; we return the
    result so callers receive the (possibly re-sorted) frame.

    ``hts.fix_trip_times`` uses ``shift()`` on the frame as passed, so the data
    must be sorted by ``(person_id, trip_id)`` before the call.  If only a
    string ``trip_key`` is available, a temporary integer order is derived from
    within-person rank to satisfy the sort requirement.
    """
    df = df_trips.copy()

    # Ensure an integer trip_id exists for compute_first_last / fix_trip_times.
    if "trip_id" not in df.columns:
        # Derive a temporary integer order from within-person departure-time rank.
        df = df.sort_values(["person_id", "departure_time"])
        df["trip_id"] = df.groupby("person_id").cumcount()
    else:
        df = df.sort_values(["person_id", "trip_id"])

    # Ensure is_first_trip / is_last_trip exist (fix_trip_times relies on them).
    if "is_first_trip" not in df.columns or "is_last_trip" not in df.columns:
        df = hts.compute_first_last(df)
    else:
        # Re-derive to guarantee consistency after any sort/append operations.
        df = hts.compute_first_last(df)

    df = hts.fix_trip_times(df)
    return df


def _append_return_home(df_trips: pd.DataFrame, dwell_s: float) -> pd.DataFrame:
    """Append a synthetic return-home trip for each person whose day does not end at home.

    For each person whose last trip (by departure_time order) has
    ``following_purpose != "home"``, one row is appended with:

    - ``departure_time = last.arrival_time + dwell_s``
      (assumed minimum dwell at the last activity before returning home)
    - ``arrival_time = departure_time + travel_time``
      where ``travel_time = last.arrival_time - last.departure_time`` (symmetric
      assumption: the return trip takes the same time as the outbound leg; if
      ``trip_duration`` is available and non-NaN that is preferred)
    - ``preceding_purpose = last.following_purpose``
    - ``following_purpose = "home"``
    - ``mode = last.mode`` if the column exists, else ``"car"`` as a conservative
      default (the mode assumption is logged; Task 7 replaces unfixable persons
      by resampling so the default is rarely load-bearing)
    - ``is_first_trip = False``, ``is_last_trip = True``; the old last trip's
      ``is_last_trip`` is flipped to ``False``

    All other columns are set to ``NaN`` / their dtype default so downstream
    processing can identify synthetic rows.  The caller must re-run
    ``hts.compute_activity_duration`` to keep ``activity_duration`` consistent.
    """
    df = df_trips.copy()
    df = df.sort_values(["person_id", "departure_time"]).reset_index(drop=True)

    # Identify the index of each person's last trip.
    last_idx = df.groupby("person_id", sort=False)["departure_time"].idxmax()

    rows_to_append = []
    idx_to_flip = []  # old last-trip indices that need is_last_trip -> False

    for person_id, idx in last_idx.items():
        last = df.loc[idx]
        if last["following_purpose"] == "home":
            continue  # already ends at home; nothing to do

        # Symmetric travel-time estimate: same duration as the outbound trip.
        # Prefer explicit trip_duration if available and non-NaN; otherwise use
        # the difference of times recorded in the row.
        if "trip_duration" in df.columns and not pd.isna(last.get("trip_duration", np.nan)):
            travel_time = float(last["trip_duration"])
        else:
            travel_time = max(0.0, float(last["arrival_time"]) - float(last["departure_time"]))

        dep = float(last["arrival_time"]) + dwell_s
        arr = dep + travel_time

        mode = last["mode"] if "mode" in df.columns and not pd.isna(last.get("mode")) else "car"

        new_row = {col: np.nan for col in df.columns}
        new_row.update({
            "person_id": person_id,
            "departure_time": dep,
            "arrival_time": arr,
            "preceding_purpose": last["following_purpose"],
            "following_purpose": "home",
            "mode": mode,
            "is_first_trip": False,
            "is_last_trip": True,
        })
        # Carry over trip_id as a placeholder so sort remains stable; will be
        # re-derived by compute_first_last after appending.
        if "trip_id" in df.columns:
            max_trip_id = df[df["person_id"] == person_id]["trip_id"].max()
            new_row["trip_id"] = max_trip_id + 1

        rows_to_append.append(new_row)
        idx_to_flip.append(idx)

    if not rows_to_append:
        return df

    # Flip the old last trips to is_last_trip=False before the new rows claim True.
    df.loc[idx_to_flip, "is_last_trip"] = False

    new_df = pd.DataFrame(rows_to_append)
    df = pd.concat([df, new_df], ignore_index=True)
    df = df.sort_values(["person_id", "departure_time"]).reset_index(drop=True)
    return df


class PlanValidator:
    """Validate trip/activity chains for logical consistency.

    Checks (per person, on the time-sorted trips):
    - ``departure_after_arrival``: each trip departs at or before it arrives;
    - ``negative_activity_duration``: gap to the next trip is non-negative;
    - ``trip_overlap``: a trip does not arrive after the next trip departs;
    - ``not_time_sorted``: departure times are non-decreasing;
    - ``time_exceeds_bound``: no departure/arrival time exceeds
      ``max_plan_time_seconds`` (post-midnight-shift chains beyond ~36h are
      pathological and routed to the resample);
    - (optional) ``home_closure``: the day starts and ends at home (Task 4).
    """

    def __init__(self, *, require_home_closure: bool = True,
                 max_plan_time_seconds: float = MAX_PLAN_TIME_SECONDS):
        self.require_home_closure = require_home_closure
        self.max_plan_time_seconds = max_plan_time_seconds

    def validate_trips(self, df_trips: pd.DataFrame) -> ValidationReport:
        issues: list = []
        persons = df_trips["person_id"].nunique()
        for person_id, group in df_trips.sort_values(
            ["person_id", "departure_time"]
        ).groupby("person_id", sort=False):
            issues.extend(self._check_person(person_id, group))
        invalid = {i.person_id for i in issues}
        counts: dict = {}
        for i in issues:
            counts[i.code] = counts.get(i.code, 0) + 1
        report = ValidationReport(persons, len(invalid), issues, counts)
        log = logger.warning if issues else logger.info
        log("[popsim.plan_validation] %d/%d persons invalid; issues %s",
            len(invalid), persons, counts)
        return report

    def _check_person(self, person_id, group: pd.DataFrame) -> list:
        out: list = []
        dep = group["departure_time"].to_numpy()
        arr = group["arrival_time"].to_numpy()
        # NaN times (MiD coded times 99 "keine Angabe" / 701 rbW are NaN'd by
        # mid_time_seconds).  All NaN comparisons below are False, so without an
        # explicit check a NaN-time person would silently pass every time check.
        # ANY NaN time makes the whole person unfixable (a mixed-NaN chain has no
        # reconstructible timeline), so one issue per person is sufficient.
        if np.isnan(dep).any() or np.isnan(arr).any():
            out.append(PlanIssue(person_id, "nan_times",
                                 "a trip has a NaN departure or arrival time "
                                 "(MiD coded time 99/701)"))
        # Times beyond the absolute plan bound: the +24h midnight shift in
        # hts.fix_trip_times is legitimate, but a chain past 36h is pathological
        # (very late / multi-shift) and must be resampled.  NaN compares False,
        # so NaN-time persons are flagged by "nan_times" above, not here.
        if (dep > self.max_plan_time_seconds).any() or (arr > self.max_plan_time_seconds).any():
            out.append(PlanIssue(person_id, "time_exceeds_bound",
                                 f"a trip time exceeds the {self.max_plan_time_seconds:.0f}s "
                                 f"(~{self.max_plan_time_seconds / 3600.0:.0f}h) plan bound"))
        if (arr < dep).any():
            out.append(PlanIssue(person_id, "departure_after_arrival",
                                 "a trip arrives before it departs"))
        if len(dep) > 1 and (dep[1:] < dep[:-1]).any():
            out.append(PlanIssue(person_id, "not_time_sorted",
                                 "departure times are not non-decreasing"))
        # activity duration = next departure - this arrival (all but last trip).
        if len(dep) > 1:
            gap = dep[1:] - arr[:-1]
            if (gap < 0).any():
                out.append(PlanIssue(person_id, "negative_activity_duration",
                                     "a between-trip activity has negative duration"))
            if (arr[:-1] > dep[1:]).any():
                out.append(PlanIssue(person_id, "trip_overlap",
                                     "a trip arrives after the next trip departs"))
        if self.require_home_closure:
            first_origin = group.iloc[0]["preceding_purpose"]
            last_dest = group.iloc[-1]["following_purpose"]
            if first_origin != "home":
                out.append(PlanIssue(person_id, "no_home_start",
                                     "the day does not start at home"))
            if last_dest != "home":
                out.append(PlanIssue(person_id, "no_home_end",
                                     "the day does not end at home"))
        return out

    def repair_trips(
        self, df_trips: pd.DataFrame, *, dwell_s: float = HOME_CLOSURE_DWELL_S
    ) -> tuple[pd.DataFrame, RepairReport]:
        """Enforce home-END closure (when require_home_closure) and classify per person.

        Pipeline:
        1. Validate before repair to identify which persons have issues.
        2. Delegate time repair to ``hts.fix_trip_times`` (eqasim helper).
        3. If ``require_home_closure``, append a synthetic return-home trip for
           every person whose day does not end at home.
        4. Re-run ``hts.compute_activity_duration`` so the appended trip is
           consistent with the rest of the chain.
        5. Validate after repair.
        6. Classify: ``valid`` = clean before; ``repaired`` = bad before, clean
           after; ``unfixable`` = still bad after repair.
        7. Log rates explicitly (no silent fallback).

        Returns (fixed_df, RepairReport).  Never filters the donor pool (preserves
        H_GEW weighting).  The unfixable set is what Task 7 (resample) replaces.

        Args:
            df_trips: Trip table with at least ``person_id``, ``departure_time``,
                ``arrival_time``, ``preceding_purpose``, ``following_purpose``,
                ``is_first_trip``, ``is_last_trip``.
            dwell_s: Dwell time (seconds) added after the last arrival before the
                synthetic return-home trip departs (default ``HOME_CLOSURE_DWELL_S``).

        Returns:
            Tuple of (repaired DataFrame, RepairReport).
        """
        # --- Step 1: validate before repair -----------------------------------
        before = self.validate_trips(df_trips)
        persons_with_issues_before = {i.person_id for i in before.issues}

        # --- Step 2: time repair via eqasim helper ----------------------------
        fixed = _eqasim_fix_trip_times(df_trips)

        # --- Step 2b: identify NaN-time persons (MiD coded times 99/701) -------
        # ANY NaN departure/arrival time makes the person unfixable: an all-NaN
        # person crashes _append_return_home (groupby idxmax yields NaN ->
        # df.loc[NaN] KeyError), and a mixed-NaN person would get a bogus
        # return-home trip anchored on a partial timeline.  These persons are
        # therefore excluded from the home-closure repair entirely; the explicit
        # "nan_times" validation issue routes them to the unfixable set, which
        # the caller replaces by the same-cell resample.
        nan_time_rows = fixed["departure_time"].isna() | fixed["arrival_time"].isna()
        nan_time_persons = set(fixed.loc[nan_time_rows, "person_id"].unique())

        # --- Step 2c: identify persons whose POST-repair times exceed the bound.
        # hts.fix_trip_times legitimately shifts midnight-crossing trips by +24h,
        # but very late / multi-shift chains can end beyond MAX_PLAN_TIME_SECONDS
        # (~36h).  Such chains are pathological-but-real repaired chains, not
        # coded-time leaks (those are NaN'd upstream).  Like NaN-time persons
        # they are excluded from the home-closure append (a return-home trip
        # anchored on a >36h arrival would only extend the broken timeline) and
        # flow to the unfixable set via the "time_exceeds_bound" validation
        # issue, which the caller replaces by the same-cell resample.
        bound_rows = (
            (fixed["departure_time"] > self.max_plan_time_seconds)
            | (fixed["arrival_time"] > self.max_plan_time_seconds)
        )
        bound_persons = set(fixed.loc[bound_rows, "person_id"].unique())
        closure_excluded_persons = nan_time_persons | bound_persons

        # --- Step 3: home-end closure (NaN-time / out-of-bound persons excluded)
        n_closure_appended = 0
        if self.require_home_closure:
            closable = fixed[~fixed["person_id"].isin(closure_excluded_persons)]
            excluded = fixed[fixed["person_id"].isin(closure_excluded_persons)]
            # Count persons that need closure before appending.
            df_sorted = closable.sort_values(["person_id", "departure_time"])
            last_rows = df_sorted.groupby("person_id", sort=False).last()
            needs_closure = (last_rows["following_purpose"] != "home").sum()
            n_closure_appended = int(needs_closure)
            closable = _append_return_home(closable, dwell_s)
            fixed = pd.concat([closable, excluded], ignore_index=True)

        # --- Step 4: recompute trip_id / trip_index / trip_duration / activity_duration ----
        # After _append_return_home the appended row has NaN trip_index and trip_duration,
        # and the per-person max+1 trip_id can collide across persons (ENTD's trip_id is
        # globally unique via arange(len)).  Re-derive everything on the final sorted frame.
        fixed = fixed.sort_values(["person_id", "departure_time"]).reset_index(drop=True)
        # Global trip_id: 0..n-1 (mirrors entd/cleaned.py and build_trip_table).
        fixed["trip_id"] = range(len(fixed))
        # compute_first_last re-derives is_first/last_trip from the integer trip_id.
        fixed = hts.compute_first_last(fixed)
        # trip_duration: re-derived so the appended row gets a finite value.
        fixed["trip_duration"] = fixed["arrival_time"] - fixed["departure_time"]
        # activity_duration: NaN on the last trip of each person (correct and expected).
        hts.compute_activity_duration(fixed)  # modifies in-place, no return
        # trip_index: per-person 0-based cumcount consumed by activities.py.
        # cumcount() yields int64, so no NaN and no float promotion.
        fixed["trip_index"] = fixed.groupby("person_id").cumcount()

        # --- Step 5: validate after repair ------------------------------------
        after = self.validate_trips(fixed)
        persons_with_issues_after = {i.person_id for i in after.issues}

        # --- Step 6: classify per person --------------------------------------
        # valid subtracts issues_after as well: a person can be clean BEFORE
        # repair yet pushed over the plan-time bound BY the +24h midnight shifts
        # in fix_trip_times; such a person is unfixable, not valid.
        all_persons = set(df_trips["person_id"].unique())
        valid_persons = all_persons - persons_with_issues_before - persons_with_issues_after
        repaired_persons = persons_with_issues_before - persons_with_issues_after
        unfixable_persons = persons_with_issues_after

        n_persons = len(all_persons)
        n_valid = len(valid_persons)
        n_repaired = len(repaired_persons)
        n_unfixable = len(unfixable_persons)

        # --- Step 7: log rates explicitly (no silent fallback) ----------------
        logger.info(
            "[popsim.plan_validation] repair: %d valid, %d repaired, %d unfixable "
            "(of %d total); home-end closure appended to %d persons "
            "(%.1f%% of pool); %d persons (%.1f%%) have NaN trip times "
            "(MiD coded times 99/701) and %d persons (%.1f%%) exceed the "
            "%.0fh plan-time bound after the midnight-shift repair; both groups "
            "were excluded from closure repair and routed to the resample",
            n_valid, n_repaired, n_unfixable, n_persons,
            n_closure_appended,
            100.0 * n_closure_appended / n_persons if n_persons > 0 else 0.0,
            len(nan_time_persons),
            100.0 * len(nan_time_persons) / n_persons if n_persons > 0 else 0.0,
            len(bound_persons),
            100.0 * len(bound_persons) / n_persons if n_persons > 0 else 0.0,
            self.max_plan_time_seconds / 3600.0,
        )

        report = RepairReport(
            n_persons=n_persons,
            n_valid=n_valid,
            n_repaired=n_repaired,
            n_unfixable=n_unfixable,
            repaired_persons=repaired_persons,
            unfixable_persons=unfixable_persons,
        )
        return fixed, report


def resample_chains(
    df_trips: pd.DataFrame,
    unfixable_persons,
    person_cells: dict,
    donor_chains: dict,
    *,
    rng,
    donor_weights: dict | None = None,
) -> pd.DataFrame:
    """Replace each unfixable person's chain with a same-cell donor's valid chain.

    Every unfixable person is guaranteed to survive: either their trips are
    replaced by a donor chain drawn from the same matching cell, or — as a
    last-resort fallback when no same-cell donor exists — they receive a
    minimal home-only plan (one row, preceding_purpose="home",
    following_purpose="home").  The fallback rate is counted and logged; a
    high rate signals cells without any valid donor chain and should be
    treated as a bug signal (not silently accepted).

    Args:
        df_trips: Full trip table.  Rows whose ``person_id`` is in
            ``unfixable_persons`` are removed and replaced.
        unfixable_persons: Iterable of person_ids whose chains must be
            resampled (typically ``RepairReport.unfixable_persons``).
        person_cells: Mapping ``person_id -> matching-cell key`` (same cell
            vocabulary as ``donor_chains``).
        donor_chains: Mapping ``cell key -> list[pd.DataFrame]``, each
            DataFrame being one donor's valid trip chain (without
            ``person_id``).
        rng: ``numpy.random.RandomState`` instance used for all draws.
            Must be seeded by the caller for determinism.
        donor_weights: Optional mapping ``cell key -> list[float]`` aligned
            with ``donor_chains[cell]``.  When provided, donors are drawn
            proportional to the given weights (e.g. MiD H_GEW donor weight)
            so the resample preserves the weighted activity-pattern
            distribution.  When ``None``, donors are drawn uniformly.

    Returns:
        A new DataFrame with the unfixable persons' rows replaced.  Row
        order is not guaranteed; callers should sort by
        ``(person_id, departure_time)`` if needed.
    """
    # Keep all trips that belong to NON-unfixable persons.
    kept = df_trips[~df_trips["person_id"].isin(unfixable_persons)].copy()

    new_rows: list[pd.DataFrame] = []
    n_resampled = 0
    n_home_only = 0

    # Sort the unfixable person IDs so the RNG draw order is deterministic
    # regardless of whether the caller passes a set (whose iteration order
    # varies with Python's hash randomisation) or a list.
    for person_id in sorted(unfixable_persons):
        cell = person_cells.get(person_id)
        pool = donor_chains.get(cell) if cell is not None else None

        if not pool:
            # No same-cell donor available.  Last-resort fallback: a single
            # home-only "stay" row (no outgoing trip).  This is observable
            # and counted below; a high rate signals a cell-coverage gap.
            n_home_only += 1
            new_rows.append(pd.DataFrame([{
                "person_id": person_id,
                "departure_time": float("nan"),
                "arrival_time": float("nan"),
                "preceding_purpose": "home",
                "following_purpose": "home",
                "is_first_trip": True,
                "is_last_trip": True,
                "mode": None,
            }]))
            continue

        # Weighted (or uniform) draw of a donor chain index.
        weights = None if donor_weights is None else donor_weights.get(cell)
        if weights is not None:
            probs = np.asarray(weights, dtype=float)
            if len(weights) != len(pool):
                raise ValueError(
                    f"[popsim.plan_validation] resample_chains: donor_weights for cell "
                    f"'{cell}' has {len(weights)} entries but donor_chains has {len(pool)} "
                    f"chains — lengths must match."
                )
            if probs.sum() <= 0.0:
                raise ValueError(
                    f"[popsim.plan_validation] resample_chains: donor_weights for cell "
                    f"'{cell}' sum to {probs.sum()} (must be > 0); cannot normalise."
                )
            probs = probs / probs.sum()
            choice = int(rng.choice(len(pool), p=probs))
        else:
            choice = int(rng.randint(len(pool)))

        chain = pool[choice].copy()
        chain["person_id"] = person_id
        new_rows.append(chain)
        n_resampled += 1

    # Observable fallback rate — no silent fallback.
    n_total = n_resampled + n_home_only
    logger.info(
        "[popsim.plan_validation] resample: %d/%d unfixable persons replaced from a "
        "same-cell donor; %d (%.1f%%) had NO same-cell donor and fell back to a "
        "home-only plan (a high rate signals cells without a valid donor chain).",
        n_resampled, n_total, n_home_only,
        100.0 * n_home_only / n_total if n_total > 0 else 0.0,
    )

    if new_rows:
        return pd.concat([kept, *new_rows], ignore_index=True)
    return kept
