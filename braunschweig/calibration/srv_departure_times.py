"""SrV 2023 departure-time and activity-duration references (issue #123, Phase 0 Task 2).

Builds the two committed aggregate tables that the departure-time model (Task 3,
``braunschweig.popsim.departure_time_model``) and the planned analysis stage
``braunschweig.analysis.synthesis.departure_time_vs_srv`` (spec
``2026-09-09-departure-time-srv-mapping-design.md`` section 2.1.3) both read:
``srv2023_departure_time_reference.csv`` (the 15-minute departure-bin distribution of a person's
FIRST trip, LATER trips, and ALL trips, per harmonised purpose and person group) and
``srv2023_activity_duration_reference.csv`` (the duration-band distribution of the activity that
follows a trip). Both are built on the same LOCAL-ONLY SrV 2023 "Braunschweig und RGB"
scientific-use microdata as ``srv_plan_structure.py``.

Calibration vs. validation (spec section 2.2, review ruling A-R9): within the departure-time
table, position ``"first"`` is the model's CALIBRATED target -- ``srv_mapped`` quantile-maps a
person's de-rounded FIRST departure onto this distribution's ``(purpose, group)`` cell. Positions
``"later"`` / ``"all"``, and the ENTIRE activity-duration table, are the model's HOLD-OUT
(validation) reference: later trips follow the donor's own durations and are never mapped: a
faithful "later"/duration match is evidence the model has not distorted anything it was not meant
to touch, not something the model is calibrated against.

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
departure-time model's mapping cell (spec 2.2) is keyed on (purpose, group) only. A harmonised
person whose ``group`` is not one of :data:`braunschweig.calibration.srv_plan_structure.GROUPS`
makes it impossible to place that person's legs into a specific-group segment, so both table
builders RAISE naming the offending value rather than silently leaving that person out of every
group segment while still counting them under ``"all"`` (review ruling A-R10 fix round 1).

Purposes: :data:`PURPOSE_ALL` (``"all"``, ALL legs of the segment/position pooled regardless of
purpose) plus the seven named purposes of ``srv_plan_structure.PURPOSES``
(:data:`PURPOSES_WITH_ALL`). The pooled purpose exists so the departure-time model's coarsening
ladder for a thin cell -- ``(purpose, group) -> (purpose, "all" segment) -> ("all" purpose, "all"
segment)`` (spec 2.2) -- is fully reachable on the COMMITTED table, not only computable from the
raw microdata at model-build time. A leg whose destination purpose is
``srv_plan_structure.UNKNOWN_PURPOSE`` (``harmonise_srv`` does NOT exclude these -- it merely
cannot map the raw SrV code to one of the seven named purposes, and still counts the leg as a
trip) is therefore EXCLUDED from every named-purpose cell (it cannot be attributed to a specific
purpose) but INCLUDED in the purpose-``"all"`` pooled cells (its departure time and, where
measurable, its following activity's duration are both still valid observations) -- counted and
logged, never silently dropped (CLAUDE.md fallback transparency); see the ``n_legs_unknown_purpose_*``
keys on the returned tables' ``DataFrame.attrs``.

De-rounding (issue #123, Task 1 rule, ``braunschweig.calibration.reported_time_precision``): a
reported departure minute is de-rounded ONCE per leg by drawing an offset uniformly inside its
reporting-precision cell (+/- 7.5 min for a quarter-hour report, +/- 2.5 min for a five-minute
report, 0 for an exact report); the SAME offset is reused wherever that leg contributes to the
table (its own segment AND the "all" segment; its own purpose-cell AND the purpose-"all" pooled
cell; its own position AND the "all" position), so :func:`departure_time_table` draws the offsets
ONCE, over every valid leg, with the ``rng`` the caller passes in (the extraction script seeds it
with :data:`SRV_DEROUNDING_SEED` so the committed table is reproducible). A leg whose ``dep_min``
is NaN, negative, or not integer-valued cannot be de-rounded
(``reported_time_precision.deround_minutes_of_day`` raises on exactly those inputs), so this
module filters such legs out BEFORE calling it and counts the exclusion -- see the
``n_legs_excluded_*`` keys on ``DataFrame.attrs``.

Departure-time binning: :data:`BIN_MINUTES` (15) x :data:`N_BINS` (112) covers 0-28 h, wide enough
for the small share of trips SrV records past midnight (a "25:30" reported departure). A bin index
computed from a departure outside that window is CLIPPED into ``[0, N_BINS - 1]`` and counted
(``n_bins_clipped_derounded`` / ``n_bins_clipped_as_reported`` on ``attrs``) rather than dropped,
so the leg's weight is not silently lost from its (segment, purpose, position) denominator.

Table shape (review ruling A-R9, fix round 1 -- Task 3's ``load_departure_time_reference``
validates exactly this): :func:`departure_time_table` is DENSE over ``bin_15min`` -- EVERY
(segment, purpose, position) cell carries all :data:`N_BINS` bins, 0.0 included, never omitted. A
cell with zero contributing legs is still EMITTED, with ``n_unweighted`` 0 and BOTH share columns
NaN for all its bins (never a fabricated 0.0, never a dropped row) -- the same "always emit the
fixed key, let the metric go NaN when the group is empty" convention as
``srv_absence.build_absence_household_by_size``'s per-size-class rows.
:func:`activity_duration_table` is DENSE over ``band`` for any (segment, purpose) that has at
least one included leg (only six bands, so a consumer should not have to special-case a missing
one) but, unlike the departure-time table, a (segment, purpose) combination with ZERO contributing
legs is OMITTED entirely (not emitted as an all-NaN placeholder): Task 3 does not read this table
programmatically (it is a hold-out reference, read by humans and the analysis stage), and the
purpose grid that occurs at all per segment is itself informative here.

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

#: Trip position within a person's reporting day: "first" = the person's seq-minimum trip
#: (the model's CALIBRATED target), "later" = every other trip, "all" = both combined (all three
#: are HOLD-OUT except "first" -- see the module docstring).
POSITIONS = ("first", "later", "all")

#: Pooled purpose: every leg of the segment/position, regardless of its own purpose (including an
#: unknown destination purpose) -- see the module docstring for why this exists and what it
#: includes. Combined with the seven named purposes of srv_plan_structure.PURPOSES.
PURPOSE_ALL = "all"
PURPOSES_WITH_ALL = (PURPOSE_ALL,) + SRV.PURPOSES

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
    """Left-join ``group`` from ``persons`` onto ``trips`` by ``pid``, validating both sides.

    Raises if ``persons`` has a duplicate ``pid`` (a merge would silently multiply rows), if any
    non-missing ``group`` value falls outside ``srv_plan_structure.GROUPS`` (review ruling A-R10:
    such a person would otherwise be silently absent from every specific-segment bucket while
    still counted under "all", with no indication anything was wrong), or if a trip's ``pid`` is
    not in ``persons`` at all (mirrors ``srv_plan_structure.person_level``'s identical guard).
    """
    if persons["pid"].duplicated().any():
        duplicates = persons.loc[persons["pid"].duplicated(), "pid"].tolist()
        raise ValueError("%s: persons frame has duplicate pid value(s) %s" % (caller, duplicates))
    unrecognised = sorted(set(persons["group"].dropna().unique()) - set(SRV.GROUPS))
    if unrecognised:
        raise ValueError(
            "%s: persons frame has group value(s) %s outside srv_plan_structure.GROUPS %s; every "
            "harmonised person must belong to one of the defined employment/life-phase groups"
            % (caller, unrecognised, list(SRV.GROUPS)))
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


def _purpose_masks(purpose: pd.Series):
    """(purpose_label, boolean mask over legs) pairs: PURPOSE_ALL (every leg, unknown-purpose
    destinations included) + one per named purpose of srv_plan_structure.PURPOSES (an
    unknown-purpose leg never matches a named mask -- see the module docstring)."""
    yield PURPOSE_ALL, pd.Series(True, index=purpose.index)
    for named in SRV.PURPOSES:
        yield named, purpose == named


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


def _dense_bin_rows(segment_label: str, purpose_label: str, position_label: str,
                    subset: pd.DataFrame) -> list:
    """The N_BINS rows of one (segment, purpose, position) cell, dense over ``bin_15min``.

    An empty cell (``n_unweighted == 0``) or one whose legs carry zero total weight gets NaN in
    both share columns for every bin -- never a fabricated 0.0, never a dropped row (review ruling
    A-R9; see the module docstring's "Table shape" paragraph).
    """
    n_unweighted = len(subset)
    total_weight = float(subset["weight"].sum()) if n_unweighted else 0.0
    if n_unweighted == 0 or not total_weight > 0:
        if n_unweighted and not total_weight > 0:
            logger.warning(
                "%s segment=%s purpose=%s position=%s has %d leg(s) but zero total weight; "
                "shares recorded as NaN for the whole cell (cannot normalise over zero weight)",
                _LOG_TAG, segment_label, purpose_label, position_label, n_unweighted)
        return [{"universe": UNIVERSE, "segment": segment_label, "purpose": purpose_label,
                 "position": position_label, "bin_15min": b, "share_derounded": float("nan"),
                 "share_as_reported": float("nan"), "n_unweighted": n_unweighted}
                for b in range(N_BINS)]
    by_derounded = subset.groupby("bin_derounded")["weight"].sum() / total_weight
    by_as_reported = subset.groupby("bin_as_reported")["weight"].sum() / total_weight
    return [{"universe": UNIVERSE, "segment": segment_label, "purpose": purpose_label,
             "position": position_label, "bin_15min": b,
             "share_derounded": float(by_derounded.get(b, 0.0)),
             "share_as_reported": float(by_as_reported.get(b, 0.0)), "n_unweighted": n_unweighted}
            for b in range(N_BINS)]


def departure_time_table(persons: pd.DataFrame, trips: pd.DataFrame,
                         rng: np.random.RandomState) -> pd.DataFrame:
    """15-minute departure-bin distribution per (segment, purpose, position), DENSE over bins.

    ``persons``/``trips`` are :func:`srv_plan_structure.harmonise_srv` output (or an
    equivalently-shaped hand-built fixture in tests). Every valid leg (a non-NaN, non-negative,
    integer-valued ``dep_min``) is de-rounded EXACTLY ONCE via
    ``reported_time_precision.deround_minutes_of_day(dep_min, rng)``; the same de-rounded value is
    then reused for every (segment, purpose, position) combination that leg contributes to,
    including the pooled ``PURPOSE_ALL`` cells.

    Returns a long table with columns :data:`DEPARTURE_TIME_COLUMNS`, DENSE over ``bin_15min``
    (see the module docstring's "Table shape" paragraph -- every cell carries all
    :data:`N_BINS` rows, an empty cell NaN throughout); shares are normalised within each
    (segment, purpose, position) so that ``share_derounded`` and, independently,
    ``share_as_reported`` each sum to 1.0 over that triple's non-NaN rows. ``n_unweighted``
    repeats the triple's unweighted leg count on every one of its rows.

    Diagnostics (CLAUDE.md fallback transparency) are attached to ``DataFrame.attrs`` rather than
    changed into the return type, so the interface stays a plain DataFrame for callers that only
    want the table: ``n_legs_total``, ``n_legs_excluded_invalid_dep_min`` (+ the NaN / negative /
    non-integer breakdown), ``n_bins_clipped_derounded``, ``n_bins_clipped_as_reported``,
    ``n_legs_unknown_purpose_raw`` / ``n_legs_unknown_purpose_valid_dep_min`` (legs whose
    destination purpose ``harmonise_srv`` could not map to a named purpose -- excluded from every
    named-purpose cell, included only in the ``PURPOSE_ALL`` pooled cells).

    Raises
    ------
    ValueError
        If ``persons`` has a duplicate ``pid``, a ``group`` value outside
        ``srv_plan_structure.GROUPS``, or a trip references a ``pid`` not in ``persons``.
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

    n_unknown_raw = int((t["purpose"] == SRV.UNKNOWN_PURPOSE).sum())
    n_unknown_valid = int((valid["purpose"] == SRV.UNKNOWN_PURPOSE).sum())
    logger.info(
        "%s unknown-purpose (destination) legs: %d/%d raw (%.2f%%); %d of the %d valid-dep_min "
        "legs are unknown-purpose -- excluded from every named-purpose cell, included only in "
        "the purpose='%s' pooled cells", _LOG_TAG, n_unknown_raw, n_total,
        100.0 * n_unknown_raw / n_total if n_total else float("nan"), n_unknown_valid, n_valid,
        PURPOSE_ALL)

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
        for purpose_label, purpose_mask in _purpose_masks(valid["purpose"]):
            for position_label in POSITIONS:
                if position_label == "first":
                    position_mask = valid["is_first"]
                elif position_label == "later":
                    position_mask = ~valid["is_first"]
                else:
                    position_mask = pd.Series(True, index=valid.index)
                subset = valid[segment_mask & purpose_mask & position_mask]
                rows.extend(_dense_bin_rows(segment_label, purpose_label, position_label, subset))

    table = pd.DataFrame(rows, columns=DEPARTURE_TIME_COLUMNS)
    table.attrs["n_legs_total"] = n_total
    table.attrs["n_legs_excluded_invalid_dep_min"] = n_invalid
    table.attrs["n_legs_excluded_nan"] = diagnostics["n_nan"]
    table.attrs["n_legs_excluded_negative"] = diagnostics["n_negative"]
    table.attrs["n_legs_excluded_non_integer"] = diagnostics["n_non_integer"]
    table.attrs["n_bins_clipped_derounded"] = n_clip_derounded
    table.attrs["n_bins_clipped_as_reported"] = n_clip_as_reported
    table.attrs["n_legs_unknown_purpose_raw"] = n_unknown_raw
    table.attrs["n_legs_unknown_purpose_valid_dep_min"] = n_unknown_valid
    return table


def activity_duration_table(persons: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Duration-band distribution of the activity following each trip, per (segment, purpose).

    ``persons``/``trips`` are :func:`srv_plan_structure.harmonise_srv` output. The activity
    duration is the NEXT trip's ``dep_min`` minus the current trip's ``arr_min`` within the same
    ``pid``; purpose is the current trip's DESTINATION purpose (the activity it leads into),
    including the pooled :data:`PURPOSE_ALL` (every purpose, unknown destinations included -- see
    the module docstring). No de-rounding is applied (as-reported minutes only -- durations are
    the model's HOLD-OUT dimension, never mapped).

    A trip with no following trip (the day's last trip) or a missing ``arr_min`` /
    following-``dep_min`` has no measurable duration and is excluded and counted
    (``n_legs_excluded_no_next_or_missing_time``); a measured duration outside
    ``[0, srv_plan_structure.WORK_ACTIVITY_MAX_H]`` hours is excluded and counted separately
    (``n_legs_excluded_out_of_range``) rather than clipped.

    Returns a long table with columns :data:`ACTIVITY_DURATION_COLUMNS`, DENSE over ``band`` for
    every (segment, purpose) that has >= 1 included leg (a combination with zero legs is omitted
    entirely -- see the module docstring's "Table shape" paragraph), ``share`` summing to 1.0.
    Diagnostics are attached to ``DataFrame.attrs``: ``n_legs_total``,
    ``n_legs_excluded_no_next_or_missing_time``, ``n_legs_excluded_out_of_range``,
    ``n_legs_included``, ``n_legs_unknown_purpose_raw`` / ``n_legs_unknown_purpose_included``
    (unknown-destination legs -- excluded from every named-purpose cell, included only in the
    ``PURPOSE_ALL`` pooled cells).

    Raises
    ------
    ValueError
        If ``persons`` has a duplicate ``pid``, a ``group`` value outside
        ``srv_plan_structure.GROUPS``, or a trip references a ``pid`` not in ``persons``.
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

    n_unknown_raw = int((t["purpose"] == SRV.UNKNOWN_PURPOSE).sum())
    n_unknown_included = int((valid["purpose"] == SRV.UNKNOWN_PURPOSE).sum())
    logger.info(
        "%s unknown-purpose (destination) legs: %d/%d raw (%.2f%%); %d of the %d included legs "
        "are unknown-purpose -- excluded from every named-purpose cell, included only in the "
        "purpose='%s' pooled cells", _LOG_TAG, n_unknown_raw, n_total,
        100.0 * n_unknown_raw / n_total if n_total else float("nan"), n_unknown_included,
        len(valid), PURPOSE_ALL)

    valid["band"] = pd.cut(valid["duration_h"], bins=list(DURATION_BAND_EDGES_H),
                           labels=list(DURATION_BAND_LABELS), include_lowest=True)

    rows = []
    for segment_label, segment_mask in _segment_masks(valid["group"]):
        for purpose_label, purpose_mask in _purpose_masks(valid["purpose"]):
            subset = valid[segment_mask & purpose_mask]
            n_unweighted = len(subset)
            if n_unweighted == 0:
                continue
            total_weight = float(subset["weight"].sum())
            if not total_weight > 0:
                logger.warning(
                    "%s segment=%s purpose=%s has %d leg(s) but zero total weight; skipped",
                    _LOG_TAG, segment_label, purpose_label, n_unweighted)
                continue
            by_band = subset.groupby("band", observed=False)["weight"].sum() / total_weight
            for band in DURATION_BAND_LABELS:
                rows.append({
                    "universe": UNIVERSE, "segment": segment_label, "purpose": purpose_label,
                    "band": band, "share": float(by_band.get(band, 0.0)),
                    "n_unweighted": n_unweighted,
                })

    table = pd.DataFrame(rows, columns=ACTIVITY_DURATION_COLUMNS)
    table.attrs["n_legs_total"] = n_total
    table.attrs["n_legs_excluded_no_next_or_missing_time"] = n_no_next
    table.attrs["n_legs_excluded_out_of_range"] = n_out_of_range
    table.attrs["n_legs_included"] = len(valid)
    table.attrs["n_legs_unknown_purpose_raw"] = n_unknown_raw
    table.attrs["n_legs_unknown_purpose_included"] = n_unknown_included
    return table
