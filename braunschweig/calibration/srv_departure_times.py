"""SrV 2023 departure-time and activity-duration references (issue #123, Phase 0 Task 2).

Builds the two committed aggregate tables that the departure-time model (Task 3,
``braunschweig.popsim.departure_time_model``) reads to replace eqasim's uninformative uniform
per-person jitter: ``srv2023_departure_time_reference.csv`` (the 15-minute departure-bin
distribution of a person's FIRST trip, LATER trips, and ALL trips, per harmonised purpose and
person group) and ``srv2023_activity_duration_reference.csv`` (the duration-band distribution of
the activity that follows a trip). Both are VALIDATION-AND-CALIBRATION references built on the
same LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata as
``srv_plan_structure.py``: the departure-time table is the CALIBRATED dimension of the
departure-time model (spec ``2026-09-09-departure-time-srv-mapping-design.md`` section 2.2), the
activity-duration table is a HOLD-OUT reference (durations are never touched by the model).

The module is a pure builder over already-harmonised SrV person/trip frames (no file-system
access, no synpp dependency); ``scripts/extract_srv_departure_times.py`` is the only caller that
touches the raw microdata, via ``srv_plan_structure.harmonise_srv``. Both table builders expect
exactly the harmonised schema of that function (``persons``: ``pid, weight, group, ...``;
``trips``: ``pid, seq, weight, purpose, prev_purpose, dep_min, arr_min``), so the SAME frames feed
both this module and ``srv_plan_structure``.

Universe: both tables are built on ``srv_plan_structure.UNIVERSE_AT_HOME_ZERO`` ONLY (every
delivered person with a valid expansion weight; matches a synthetic population, in which every
person exists and starts the day at home) -- ``harmonise_srv`` already returns exactly that
universe, so no further person filtering happens here. The ``universe`` column is carried on both
tables (constant ``"at_home_zero"``) purely for schema symmetry with
``srv2023_plan_structure_reference.csv``, which also emits the ``at_home_only`` sensitivity; that
sensitivity is NOT built here (ASSUMPTION: the departure-time and activity-duration references do
not need the away-from-home sensitivity that the plan-structure reference carries, because a
departure or arrival time cannot be observed for a person who reported no trip at all).

Segments: ``"all"`` plus the five harmonised employment x life-phase groups of
``srv_plan_structure.GROUPS`` (:data:`SEGMENTS`) -- no age-band, sex or Kreis segments, because the
departure-time model's mapping cell (spec 2.2) is keyed on (purpose, group) only.

De-rounding (issue #123, Task 1 rule, ``braunschweig.calibration.reported_time_precision``): a
reported departure minute is de-rounded ONCE per leg by drawing an offset uniformly inside its
reporting-precision cell (+/- 7.5 min for a quarter-hour report, +/- 2.5 min for a five-minute
report, 0 for an exact report); the SAME offset is reused wherever that leg contributes to the
table (its own segment AND the "all" segment; its own position AND the "all" position), so
:func:`departure_time_table` draws the offsets ONCE, over every valid leg, with the ``rng`` the
caller passes in (the extraction script seeds it with :data:`SRV_DEROUNDING_SEED` so the committed
table is reproducible). A leg whose ``dep_min`` is NaN, negative, or not integer-valued cannot be
de-rounded (``reported_time_precision.deround_minutes_of_day`` raises on exactly those inputs), so
this module filters such legs out BEFORE calling it and counts the exclusion -- see the
``n_legs_excluded_*`` keys on the returned table's ``DataFrame.attrs`` (CLAUDE.md's fallback
transparency rule: an exclusion this module makes is never silent, whether or not it is later
surfaced by a caller).

Departure-time binning: :data:`BIN_MINUTES` (15) x :data:`N_BINS` (112) covers 0-28 h, wide enough
for the small share of trips SrV records past midnight (a "25:30" reported departure). A bin index
computed from a departure outside that window is CLIPPED into ``[0, N_BINS - 1]`` and counted
(``n_bins_clipped_derounded`` / ``n_bins_clipped_as_reported`` on ``attrs``) rather than dropped,
so the leg's weight is not silently lost from its (segment, purpose, position) denominator.

Table shape: :func:`departure_time_table` is SPARSE -- for a given (segment, purpose, position)
only the bins that carry non-zero weight in EITHER ``share_derounded`` or ``share_as_reported``
get a row (a bin with zero weight in one of the two columns and non-zero in the other still gets
one row, with an explicit 0.0, never a missing row); this keeps the file a manageable size given
112 possible bins. :func:`activity_duration_table` is DENSE -- every (segment, purpose) group with
at least one measured activity emits all six of :data:`DURATION_BAND_LABELS`, most of them 0.0,
because there are only six bands and a consumer should not have to special-case a missing one. A
(segment, purpose[, position]) combination with zero contributing legs is OMITTED from both tables
entirely (never emitted as an all-NaN placeholder row): unlike the plan-structure reference's fixed
segment x metric grid, here the grid of purposes that occur at all per segment is itself
informative (a manufactured NaN row for, say, "child_0_5 x work" would misrepresent a cell that is
empty by construction, not an unrelated failure to compute a metric), and the departure-time
model's own coarsening ladder (spec 2.2) is exactly what handles a thin or absent cell downstream.

Activity duration: reuses ``srv_plan_structure.WORK_ACTIVITY_MAX_H`` (20 h) as the plausibility
ceiling for EVERY purpose, not only "work" (the original convention in ``srv_plan_structure`` was
scoped to work activities; this module generalises it, since an implausible duration is
implausible regardless of the activity type, and no purpose-specific literature value is available
-- ASSUMPTION). Duration = the next trip's ``dep_min`` minus the current trip's ``arr_min``, within
the same ``pid``; a trip with no following trip (the last trip of the reporting day) has no
measurable activity and is excluded and counted (``n_legs_excluded_no_next_or_missing_time``), as
is a trip whose own ``arr_min`` or the following trip's ``dep_min`` is missing. A duration outside
``[0, WORK_ACTIVITY_MAX_H]`` hours is excluded and counted separately
(``n_legs_excluded_out_of_range``) rather than silently clipped, because an implausible duration
usually signals a data or joining error, not a real but extreme activity.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration import reported_time_precision as RP
from braunschweig.calibration import srv_plan_structure as SRV

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv departure times]"

DEPARTURE_TIME_TABLE = "srv2023_departure_time_reference.csv"
ACTIVITY_DURATION_TABLE = "srv2023_activity_duration_reference.csv"

#: 15-minute departure bin width and count: 112 bins x 15 min = 28 h, wide enough for the small
#: share of SrV trips reported past midnight (e.g. a "25:30" departure).
BIN_MINUTES = 15
N_BINS = 112

#: Trip position within a person's reporting day: "first" = the person's seq-minimum trip,
#: "later" = every other trip, "all" = both combined (so a (segment, purpose) cell can be read
#: either split by position or pooled, from the same table).
POSITIONS = ("first", "later", "all")

#: Activity-duration bands (hours); the upper edge is +inf so every in-range duration (see
#: WORK_ACTIVITY_MAX_H below) falls into exactly one band.
DURATION_BAND_EDGES_H = (0, 0.5, 1, 2, 4, 8, np.inf)
DURATION_BAND_LABELS = ("0-0.5h", "0.5-1h", "1-2h", "2-4h", "4-8h", "8h+")

#: Fixed seed for the ONE de-rounding draw per leg, so the committed departure-time reference is
#: reproducible byte-for-byte from the raw microdata (CLAUDE.md scientific-reproducibility rule).
#: Distinct from the departure-time MODEL's own runtime seed (Task 3, DEPARTURE_TIME_SEED_OFFSET);
#: this seed only ever regenerates a committed reference file, never a simulation run.
SRV_DEROUNDING_SEED = 20260909

#: Both tables are built on this universe only -- see the module docstring.
UNIVERSE = SRV.UNIVERSE_AT_HOME_ZERO

#: "all" plus the five harmonised employment x life-phase groups (srv_plan_structure.GROUPS).
SEGMENTS = ("all",) + SRV.GROUPS

DEPARTURE_TIME_COLUMNS = ["universe", "segment", "purpose", "position", "bin_15min",
                          "share_derounded", "share_as_reported", "n_unweighted"]
ACTIVITY_DURATION_COLUMNS = ["universe", "segment", "purpose", "band", "share", "n_unweighted"]


def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s" % (name, missing))


def _attach_group(trips: pd.DataFrame, persons: pd.DataFrame, caller: str) -> pd.DataFrame:
    """Left-join ``group`` from ``persons`` onto ``trips`` by ``pid``, raising on an orphan.

    A trip whose person is not in ``persons`` would otherwise be silently dropped from every
    segment (it can never match a group) while still counting towards ``n_legs_total`` -- raising
    here mirrors ``srv_plan_structure.person_level``'s identical guard.
    """
    if persons["pid"].duplicated().any():
        duplicates = persons.loc[persons["pid"].duplicated(), "pid"].tolist()
        raise ValueError("%s: persons frame has duplicate pid value(s) %s" % (caller, duplicates))
    merged = trips.merge(persons[["pid", "group"]], on="pid", how="left")
    orphaned = int(merged["group"].isna().sum())
    if orphaned:
        raise ValueError(
            "%s: %d of %d trips belong to a pid that is not in the person frame; restrict trips "
            "to the person universe before calling %s" % (caller, orphaned, len(merged), caller))
    return merged


def _segment_masks(group: pd.Series):
    """(segment_label, boolean mask over legs) pairs: 'all' + one per harmonised group."""
    yield "all", pd.Series(True, index=group.index)
    for segment in SRV.GROUPS:
        yield segment, group == segment


def _classify_dep_min(dep_min: pd.Series) -> dict:
    """Split ``dep_min`` into valid vs. NaN / negative / non-integer-valued (mutually exclusive,
    checked in that order, mirroring ``reported_time_precision.half_width_minutes``): these three
    conditions are exactly what that function raises on, so they must be filtered out before it is
    called, and counted rather than silently dropped (CLAUDE.md fallback transparency)."""
    values = pd.to_numeric(dep_min, errors="coerce")
    is_nan = values.isna()
    is_negative = (~is_nan) & (values < 0)
    is_non_integer = (~is_nan) & (~is_negative) & (~np.isclose(values, np.round(values)))
    invalid = is_nan | is_negative | is_non_integer
    return {
        "mask_valid": ~invalid,
        "n_total": int(len(values)),
        "n_nan": int(is_nan.sum()),
        "n_negative": int(is_negative.sum()),
        "n_non_integer": int(is_non_integer.sum()),
        "n_invalid": int(invalid.sum()),
    }


def _bin_from_minutes(minutes: np.ndarray):
    """``floor(minutes / BIN_MINUTES)`` clipped into ``[0, N_BINS - 1]``; returns
    ``(bins, n_clipped)`` -- the count of values the clip actually changed."""
    raw_bin = np.floor(np.asarray(minutes, dtype=float) / BIN_MINUTES)
    clipped = np.clip(raw_bin, 0, N_BINS - 1)
    n_clipped = int((raw_bin != clipped).sum())
    return clipped.astype(int), n_clipped


def departure_time_table(persons: pd.DataFrame, trips: pd.DataFrame,
                         rng: np.random.RandomState) -> pd.DataFrame:
    """15-minute departure-bin distribution per (segment, purpose, position).

    ``persons``/``trips`` are :func:`srv_plan_structure.harmonise_srv` output (or an
    equivalently-shaped hand-built fixture in tests). Every valid leg (a non-NaN, non-negative,
    integer-valued ``dep_min``) is de-rounded EXACTLY ONCE via
    ``reported_time_precision.deround_minutes_of_day(dep_min, rng)``; the same de-rounded value is
    then reused for every (segment, purpose, position) combination that leg contributes to.

    Returns a long table with columns :data:`DEPARTURE_TIME_COLUMNS`, SPARSE over ``bin_15min``
    (see the module docstring); shares are normalised within each (segment, purpose, position) so
    that ``share_derounded`` and, independently, ``share_as_reported`` each sum to 1.0 over that
    triple's rows. ``n_unweighted`` repeats the triple's unweighted leg count on every one of its
    rows.

    Diagnostics (CLAUDE.md fallback transparency) are attached to ``DataFrame.attrs`` rather than
    changed into the return type, so the interface stays a plain DataFrame for callers that only
    want the table: ``n_legs_total``, ``n_legs_excluded_invalid_dep_min`` (+ the NaN / negative /
    non-integer breakdown), ``n_bins_clipped_derounded``, ``n_bins_clipped_as_reported``.
    """
    _require_columns(persons, ["pid", "weight", "group"], "persons")
    _require_columns(trips, ["pid", "seq", "weight", "purpose", "dep_min"], "trips")

    t = trips.copy()
    # "first" is a structural property of trip order (seq), independent of whether THIS leg's
    # dep_min later turns out to be invalid -- computed on the full per-pid trip set.
    t["is_first"] = t["seq"] == t.groupby("pid")["seq"].transform("min")
    t = _attach_group(t, persons, "departure_time_table")

    diagnostics = _classify_dep_min(t["dep_min"])
    n_total = diagnostics["n_total"]
    n_invalid = diagnostics["n_invalid"]
    n_valid = n_total - n_invalid
    logger.info(
        "%s dep_min validity: %d/%d valid (%.1f%%), %d excluded before de-rounding (NaN %d, "
        "negative %d, non-integer-valued %d)", _LOG_TAG, n_valid, n_total,
        100.0 * n_valid / n_total if n_total else float("nan"), n_invalid, diagnostics["n_nan"],
        diagnostics["n_negative"], diagnostics["n_non_integer"])
    if n_total and n_invalid / n_total > 0.5:
        logger.warning(
            "%s more than half of all legs (%d/%d) have an invalid dep_min and are excluded from "
            "the departure-time reference; this usually signals an upstream problem (a bad join, "
            "an unfiltered sentinel) rather than genuinely missing reports", _LOG_TAG, n_invalid,
            n_total)

    valid = t[diagnostics["mask_valid"]].copy()
    dep_min = valid["dep_min"].to_numpy(dtype=float)
    derounded, _offset = RP.deround_minutes_of_day(dep_min, rng)
    valid["bin_derounded"], n_clip_derounded = _bin_from_minutes(derounded)
    valid["bin_as_reported"], n_clip_as_reported = _bin_from_minutes(dep_min)
    logger.info(
        "%s departure bins clipped into [0, %d]: de-rounded %d/%d (%.2f%%), as-reported %d/%d "
        "(%.2f%%)", _LOG_TAG, N_BINS - 1, n_clip_derounded, len(valid),
        100.0 * n_clip_derounded / len(valid) if len(valid) else float("nan"),
        n_clip_as_reported, len(valid),
        100.0 * n_clip_as_reported / len(valid) if len(valid) else float("nan"))

    rows = []
    for segment_label, segment_mask in _segment_masks(valid["group"]):
        for purpose in SRV.PURPOSES:
            purpose_mask = valid["purpose"] == purpose
            for position_label in POSITIONS:
                if position_label == "first":
                    position_mask = valid["is_first"]
                elif position_label == "later":
                    position_mask = ~valid["is_first"]
                else:
                    position_mask = pd.Series(True, index=valid.index)
                subset = valid[segment_mask & purpose_mask & position_mask]
                n_unweighted = len(subset)
                if n_unweighted == 0:
                    continue
                total_weight = float(subset["weight"].sum())
                if not total_weight > 0:
                    logger.warning(
                        "%s segment=%s purpose=%s position=%s has %d leg(s) but zero total "
                        "weight; skipped (cannot normalise a share over zero weight)", _LOG_TAG,
                        segment_label, purpose, position_label, n_unweighted)
                    continue
                by_derounded = subset.groupby("bin_derounded")["weight"].sum() / total_weight
                by_as_reported = subset.groupby("bin_as_reported")["weight"].sum() / total_weight
                for b in sorted(set(by_derounded.index) | set(by_as_reported.index)):
                    rows.append({
                        "universe": UNIVERSE, "segment": segment_label, "purpose": purpose,
                        "position": position_label, "bin_15min": int(b),
                        "share_derounded": float(by_derounded.get(b, 0.0)),
                        "share_as_reported": float(by_as_reported.get(b, 0.0)),
                        "n_unweighted": n_unweighted,
                    })

    table = pd.DataFrame(rows, columns=DEPARTURE_TIME_COLUMNS)
    table.attrs["n_legs_total"] = n_total
    table.attrs["n_legs_excluded_invalid_dep_min"] = n_invalid
    table.attrs["n_legs_excluded_nan"] = diagnostics["n_nan"]
    table.attrs["n_legs_excluded_negative"] = diagnostics["n_negative"]
    table.attrs["n_legs_excluded_non_integer"] = diagnostics["n_non_integer"]
    table.attrs["n_bins_clipped_derounded"] = n_clip_derounded
    table.attrs["n_bins_clipped_as_reported"] = n_clip_as_reported
    return table


def activity_duration_table(persons: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Duration-band distribution of the activity following each trip, per (segment, purpose).

    ``persons``/``trips`` are :func:`srv_plan_structure.harmonise_srv` output. The activity
    duration is the NEXT trip's ``dep_min`` minus the current trip's ``arr_min`` within the same
    ``pid``; purpose is the current trip's DESTINATION purpose (the activity it leads into). No
    de-rounding is applied (as-reported minutes only -- durations are the model's HOLD-OUT
    dimension, never mapped).

    A trip with no following trip (the day's last trip) or a missing ``arr_min`` /
    following-``dep_min`` has no measurable duration and is excluded and counted
    (``n_legs_excluded_no_next_or_missing_time``); a measured duration outside
    ``[0, srv_plan_structure.WORK_ACTIVITY_MAX_H]`` hours is excluded and counted separately
    (``n_legs_excluded_out_of_range``) rather than clipped.

    Returns a long table with columns :data:`ACTIVITY_DURATION_COLUMNS`, DENSE over ``band`` (see
    the module docstring): every (segment, purpose) with at least one included leg emits all of
    :data:`DURATION_BAND_LABELS`, ``share`` summing to 1.0. Diagnostics are attached to
    ``DataFrame.attrs``: ``n_legs_total``, ``n_legs_excluded_no_next_or_missing_time``,
    ``n_legs_excluded_out_of_range``, ``n_legs_included``.
    """
    _require_columns(persons, ["pid", "weight", "group"], "persons")
    _require_columns(trips, ["pid", "seq", "weight", "purpose", "dep_min", "arr_min"], "trips")

    t = trips.sort_values(["pid", "seq"]).copy()
    t["next_dep_min"] = t.groupby("pid")["dep_min"].shift(-1)
    t = _attach_group(t, persons, "activity_duration_table")

    n_total = len(t)
    has_next = t["next_dep_min"].notna() & t["arr_min"].notna()
    n_no_next = int((~has_next).sum())
    measurable = t[has_next].copy()
    measurable["duration_h"] = (measurable["next_dep_min"] - measurable["arr_min"]) / 60.0
    in_range = ((measurable["duration_h"] >= 0)
               & (measurable["duration_h"] <= SRV.WORK_ACTIVITY_MAX_H))
    n_out_of_range = int((~in_range).sum())
    valid = measurable[in_range].copy()
    logger.info(
        "%s activity durations: %d/%d legs measurable (%.1f%%; %d have no following trip or a "
        "missing arrival/departure time); of those, %d/%d excluded as outside [0, %g] h (%.1f%%),"
        " %d retained", _LOG_TAG, len(measurable), n_total,
        100.0 * len(measurable) / n_total if n_total else float("nan"), n_no_next, n_out_of_range,
        len(measurable), SRV.WORK_ACTIVITY_MAX_H,
        100.0 * n_out_of_range / len(measurable) if len(measurable) else float("nan"), len(valid))

    valid["band"] = pd.cut(valid["duration_h"], bins=list(DURATION_BAND_EDGES_H),
                           labels=list(DURATION_BAND_LABELS), include_lowest=True)

    rows = []
    for segment_label, segment_mask in _segment_masks(valid["group"]):
        for purpose in SRV.PURPOSES:
            subset = valid[segment_mask & (valid["purpose"] == purpose)]
            n_unweighted = len(subset)
            if n_unweighted == 0:
                continue
            total_weight = float(subset["weight"].sum())
            if not total_weight > 0:
                logger.warning(
                    "%s segment=%s purpose=%s has %d leg(s) but zero total weight; skipped",
                    _LOG_TAG, segment_label, purpose, n_unweighted)
                continue
            by_band = subset.groupby("band", observed=False)["weight"].sum() / total_weight
            for band in DURATION_BAND_LABELS:
                rows.append({
                    "universe": UNIVERSE, "segment": segment_label, "purpose": purpose,
                    "band": band, "share": float(by_band.get(band, 0.0)),
                    "n_unweighted": n_unweighted,
                })

    table = pd.DataFrame(rows, columns=ACTIVITY_DURATION_COLUMNS)
    table.attrs["n_legs_total"] = n_total
    table.attrs["n_legs_excluded_no_next_or_missing_time"] = n_no_next
    table.attrs["n_legs_excluded_out_of_range"] = n_out_of_range
    table.attrs["n_legs_included"] = len(valid)
    return table
