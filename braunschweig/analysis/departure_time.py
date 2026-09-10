"""Model side of the SrV 2023 departure-time comparison (issue #123, ADR-0114).

Three questions, one module:

1. **What did the departure-time model actually change?** Every trip row of the popsim_mid
   build carries the offset the model applied
   (:data:`braunschweig.popsim.departure_time_model.OFFSET_COLUMN`), so a realised time
   decomposes exactly as ``realised = pre_offset + offset``. Together with the donor's RAW
   reported MiD time (``W_SZS``/``W_SZM`` through
   :func:`braunschweig.popsim.trips.mid_time_seconds`) that gives a THREE-way view of the same
   leg -- raw / pre-offset / realised -- and :func:`decomposition` reports the hourly profile of
   all three side by side.

   The two differences mean DIFFERENT things and must not be read as one:
   ``pre_offset -> realised`` is the departure-time MODEL and nothing else, while
   ``raw -> pre_offset`` is everything the TRIP BUILD did before the model ran -- the
   midnight-crossing and inconsistent-chain repairs of ``data.hts.hts.fix_trip_times``, the
   unfixable-plan resampling of :mod:`braunschweig.popsim.plan_validation`, and, on the
   reporting-day view, the donor day a spliced home-office chain brought with it. Attributing a
   raw-to-pre-offset shift to the model would credit (or blame) it for a repair it never made.

Two clocks
----------
``mid_time_seconds`` decodes the MiD report on a 0..23 h clock: it returns NaN for anything
outside that range, because a value outside it is a design code (99, 701), not a time. The trip
build's times are on a 0..47 h clock instead -- ``fix_trip_times`` shifts a midnight-crossing leg
and every following leg of that chain by +24 h so a chain stays monotone. A leg repaired that way
therefore appears at hour 0-3 in the ``raw`` column and at hour 24-27 in ``pre_offset`` /
``realised``, a full 24 h apart, with no error anywhere: the two columns are simply not on the
same clock. Two consequences a reader must know. First, ``n_hours_clipped_raw`` is structurally
always 0 -- the raw column cannot reach hour 28 -- so it says nothing about after-midnight
behaviour. Second, the size of a raw-vs-pre-offset difference is not a shift size until the 24 h
cases are separated out, which is what ``n_legs_raw_pre_offset_differ_by_24h`` counts (a
difference within one minute of exactly 1440 min). Compare the raw column with the other two
WITHIN a clock day; treat the hour 24-27 rows of the pre-offset and realised columns as the
after-midnight tail the raw column folds back onto hours 0-3.
2. **Does the realised start-time distribution match SrV?** :func:`bin_comparison` puts the
   model's realised departures into the SAME 15-minute bins the committed reference uses and
   :func:`emd_table` scores each ``(segment, purpose, position)`` cell against BOTH reference
   columns with :func:`braunschweig.calibration.metrics.emd_on_bands`.
3. **Does anything the model was NOT fitted to also improve?** The ``srv_mapped`` model maps the
   FIRST departure of a chain only, so ``position == "first"`` is the CALIBRATED dimension and
   everything else is HOLD-OUT: the later legs, the pooled ``all`` position, and the activity
   durations of :func:`activity_duration_comparison`. :data:`DIMENSION_CALIBRATED` /
   :data:`DIMENSION_HOLDOUT` label every EMD row accordingly, so a reader can never mistake a
   fitted dimension for evidence.

De-rounded AND as-reported, deliberately
----------------------------------------
The committed reference carries two share columns for every bin: ``share_derounded`` (the SrV
report spread over the half-width of its own reporting grid) and ``share_as_reported`` (the raw
clock-time grid, with its 82 % spike on the full hour). Every comparison here reports the model
against BOTH. The de-rounded column is the scientifically meaningful target -- a synthetic
population should reproduce the underlying behaviour, not the survey's rounding artefact -- but a
model whose own times are still grid-locked would score better against ``share_as_reported``, and
hiding that would make the de-rounded number unreadable. Reporting both is what makes the
difference between the two an observable quantity rather than an assumption.

Universe and taxonomy
---------------------
Segments, purposes, positions and the 15-minute bin geometry are IMPORTED from
:mod:`braunschweig.calibration.srv_departure_times`, the module that built the committed
reference -- never re-typed here. The two sides of a comparison must be produced by one
definition or they can silently disagree (CLAUDE.md "Validate metric apples-to-apples"). The
purpose of a leg is its DESTINATION purpose (``following_purpose``), harmonised by the very
function the plan-structure comparison uses, and the pooled ``"all"`` purpose includes legs whose
purpose could not be mapped, exactly as the reference builder does.

Unlike :mod:`braunschweig.analysis.plan_structure` this module does NOT segment by home Kreis, so
it needs no VG250 join and no home-location stage. That also means it makes no claim about the
SrV survey universe (SrV covers seven of the eight ZGB Kreise): the comparison is over the whole
synthetic population against the whole SrV sample, which is stated in the stage's ``summary.md``
as a limitation rather than silently assumed away.

This module is pure: no synpp context, no file system. The synpp stage
:mod:`braunschweig.analysis.synthesis.departure_time_vs_srv` loads the references, supplies the
frames and writes the report.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration import metrics
from braunschweig.calibration import srv_departure_times as SRVDT
from braunschweig.calibration import srv_plan_structure as SRV
# harmonise_purpose maps an eqasim purpose column onto the seven harmonised purposes and WARNS
# with the share of unmapped values. It is imported rather than re-implemented so both model-side
# comparisons (plan structure and departure times) map purposes by ONE rule; plan_structure is
# hashed in the stage's validation token for exactly this reason.
from braunschweig.analysis.plan_structure import harmonise_purpose
# The MiD raw-time decoder, including the design-code handling (99 "keine Angabe", 701 "rbW")
# that makes a coded report NaN instead of a bogus clock time. One decoder, one rule.
from braunschweig.popsim.trips import mid_time_seconds
from braunschweig.popsim.departure_time_model import OFFSET_COLUMN

logger = logging.getLogger(__name__)

_LOG_TAG = "[departure time model side]"

#: The MiD Wege columns carrying the reported start time of a leg (hour, minute).
RAW_HOUR_COLUMNS = ("W_SZS", "W_SZM")

#: Columns required on the ``synthesis.population.enriched`` person frame.
MODEL_PERSON_COLUMNS = ("person_id", "age", "employed")
#: Columns required on the trips frame, on top of :data:`OFFSET_COLUMN`. The raw MiD time
#: columns are OPTIONAL (see :func:`harmonise_model_times`).
MODEL_TRIP_COLUMNS = ("person_id", "trip_index", "departure_time", "arrival_time",
                      "following_purpose")

SECONDS_PER_MINUTE = 60.0
MINUTES_PER_HOUR = 60.0
#: One clock day in minutes -- the exact offset ``data.hts.hts.fix_trip_times`` adds to a
#: midnight-crossing chain, and therefore the raw-vs-pre-offset difference such a leg shows.
MINUTES_PER_DAY = 1440.0
#: How far from exactly :data:`MINUTES_PER_DAY` a raw-vs-pre-offset difference may sit and still
#: be counted as the midnight shift rather than a genuine time change. One minute: the raw MiD
#: report has minute resolution, and no repair or model produces a shift within a minute of 24 h
#: by any other route.
MIDNIGHT_SHIFT_TOLERANCE_MINUTES = 1.0

#: The comparison taxonomy, taken from the reference builder rather than re-typed.
SEGMENTS = SRVDT.SEGMENTS
PURPOSES = SRVDT.PURPOSES_WITH_ALL
POSITIONS = SRVDT.POSITIONS
SEGMENT_ALL = "all"
PURPOSE_ALL = SRVDT.PURPOSE_ALL
POSITION_FIRST, POSITION_LATER, POSITION_ALL = SRVDT.POSITIONS

#: Reported departure hours. ``SRV.HOUR_CLIP`` is the clip the SrV-side plan-structure metric
#: already applies (``_departure_hour_shares``), so the two sides treat an after-midnight
#: continuation identically: a departure beyond hour 27 is CLIPPED into hour 27, and the number
#: of legs the clip actually moved is counted per time column and reported (never silent).
HOURS = tuple(range(SRV.HOUR_CLIP[0], SRV.HOUR_CLIP[1] + 1))

#: EMD dimension labels. ``srv_mapped`` maps the FIRST departure of a chain and nothing else, so
#: only ``position == "first"`` is a fitted dimension; every other position -- and every activity
#: duration -- is hold-out evidence.
DIMENSION_CALIBRATED = "calibrated"
DIMENSION_HOLDOUT = "holdout"

HARMONISED_COLUMNS = ["pid", "seq", "purpose", "position", "segment_group",
                      "raw_dep_min", "pre_offset_dep_min", "realised_dep_min", "arr_min"]
DECOMPOSITION_COLUMNS = ["segment", "purpose", "position", "hour",
                         "share_raw", "share_pre_offset", "share_realised", "n_raw", "n"]
COMPARISON_COLUMNS = ["segment", "purpose", "position", "bin_15min", "share_model",
                      "share_srv_derounded", "share_srv_as_reported", "n_model", "n_srv"]
EMD_COLUMNS = ["segment", "purpose", "position", "emd_vs_derounded", "emd_vs_as_reported",
               "n_model", "n_srv", "dimension"]
DURATION_COLUMNS = ["segment", "purpose", "band", "share_model", "share_srv", "delta_pp",
                    "n_model", "n_srv"]

#: A reference cell's two share columns must each sum to 1 before they can be fed to
#: ``emd_on_bands`` (which assumes normalised inputs). This is an ALIAS, not a second copy:
#: the tolerance lives once, next to the builder that normalises the committed shares
#: (:data:`braunschweig.calibration.srv_departure_times.REFERENCE_SUM_TOLERANCE`, which
#: ``departure_time_model`` re-exports under the same name), so this module cannot accept a
#: reference cell the model's own loader rejects, or the other way round. Since final fix wave
#: item 4 the sum-to-1 check itself is delegated entirely to
#: :func:`braunschweig.calibration.srv_departure_times.validate_departure_time_table` (called from
#: :func:`_reference_cells`), so this alias is no longer READ here; it is kept as the identity
#: :func:`test_the_tolerance_constant_has_exactly_one_home` pins.
SHARE_SUM_TOLERANCE = SRVDT.REFERENCE_SUM_TOLERANCE

#: Below this share of legs with a usable raw MiD time the raw column of the decomposition
#: describes only a minority of the day and the stage WARNS. It is not an error: the
#: reporting-day view legitimately splices donor days whose extra columns were nulled
#: (``braunschweig.synthesis.commute_day.plan_replacement``), so a coverage well below 1 is
#: expected there -- but a coverage near 0 on the PRE-ASSIGNMENT view would mean the raw columns
#: never arrived at all, and that must be visible (CLAUDE.md "Fallback transparency").
RAW_COVERAGE_WARN_THRESHOLD = 0.5


def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s; got %s"
                         % (name, missing, sorted(frame.columns)))


# --------------------------------------------------------------------------- harmonisation

def harmonise_model_times(persons: pd.DataFrame, trips: pd.DataFrame, groups: pd.Series,
                          *, trips_view: str | None = None) -> pd.DataFrame:
    """Decompose every model leg into its raw, pre-offset and realised departure time.

    Parameters
    ----------
    persons:
        ``synthesis.population.enriched``; must carry :data:`MODEL_PERSON_COLUMNS`. Only the
        person ids are read here -- the harmonised group comes from ``groups``, so the group
        RULE lives in exactly one place.
    trips:
        The trips view (``synthesis.population.trips`` or ``...trips.final``); must carry
        :data:`MODEL_TRIP_COLUMNS` and :data:`OFFSET_COLUMN`. ``departure_time`` /
        ``arrival_time`` are SECONDS since midnight and become MINUTES here, matching the
        reference builder's ``dep_min`` / ``arr_min``. :data:`RAW_HOUR_COLUMNS` are OPTIONAL:
        their absence makes ``raw_dep_min`` NaN throughout and is reported in ``attrs``, because
        a trips view that never carried them is a legitimate input, while a SILENT all-NaN raw
        column would look like a measured result.
    groups:
        Harmonised employment x life-phase group per person, indexed by ``person_id`` -- the
        output of :func:`braunschweig.popsim.departure_time_model.person_groups`, i.e. the SAME
        rule the committed reference was segmented by.
    trips_view:
        Name of the trips view the frame came from, used only to make the missing-offset error
        message diagnosable.

    Returns
    -------
    pd.DataFrame
        One row per leg with :data:`HARMONISED_COLUMNS`, sorted by ``(pid, seq)``:

        * ``raw_dep_min`` -- the donor's REPORTED MiD start time (NaN for a coded/absent report)
        * ``pre_offset_dep_min`` -- ``realised - offset``, the model's own pre-offset time
        * ``realised_dep_min`` -- what MATSim simulates
        * ``position`` -- :data:`POSITION_FIRST` for the person's lowest ``seq``, else
          :data:`POSITION_LATER` (a structural property of trip order, exactly as the reference
          builder defines it)

        Diagnostics on ``DataFrame.attrs``: ``n_legs``, ``n_legs_with_raw_time``,
        ``share_legs_with_raw_time``, ``raw_columns_present``, ``n_legs_without_offset``.

    Raises
    ------
    ValueError
        If :data:`OFFSET_COLUMN` is absent (the message names the column, the trips view and the
        ``departure_time_model`` config key that writes it), if a required column is missing, or
        if a trip belongs to a person that is not in ``persons`` -- such a leg would be dropped
        by the group join and shrink every distribution invisibly.
    """
    _require_columns(persons, MODEL_PERSON_COLUMNS, "persons")
    _require_columns(trips, MODEL_TRIP_COLUMNS, "trips")
    if OFFSET_COLUMN not in trips.columns:
        raise ValueError(
            "the trips frame (view %r) carries no %r column, so a realised departure time "
            "cannot be decomposed into its pre-offset time and the applied offset. That column "
            "is written by braunschweig.popsim.trips_stage for every departure_time_model "
            "(config key 'departure_time_model'); its absence means the trips table was built "
            "before issue #123 or by a build that does not run the model at all. Rebuild the "
            "trips stage, or point departure_time_trips_view at a view that has it. Present "
            "columns: %s" % (trips_view if trips_view is not None else "unknown",
                             OFFSET_COLUMN, sorted(trips.columns)))

    orphaned = trips.loc[~trips["person_id"].isin(set(persons["person_id"])), "person_id"]
    if len(orphaned) > 0:
        raise ValueError(
            "%d of %d trips belong to a person that is not in the person frame (e.g. %s); the "
            "trips view and synthesis.population.enriched must describe the same population"
            % (len(orphaned), len(trips), sorted(orphaned.unique().tolist())[:5]))

    realised_seconds = pd.to_numeric(trips["departure_time"], errors="coerce")
    offset_seconds = pd.to_numeric(trips[OFFSET_COLUMN], errors="coerce")
    n_legs = int(len(trips))
    n_without_offset = int(offset_seconds.isna().sum())
    if n_without_offset:
        logger.warning(
            "%s %d/%d leg(s) (%.2f%%) carry no finite %s; their pre-offset time is NaN and they "
            "are excluded from the pre-offset column of the decomposition (never counted as a "
            "zero offset)", _LOG_TAG, n_without_offset, n_legs,
            100.0 * n_without_offset / n_legs if n_legs else float("nan"), OFFSET_COLUMN)

    # The raw MiD columns are optional. Their PRESENCE and their per-leg coverage are two
    # different facts and are reported separately: an absent column means "this view never had
    # the raw time", a present-but-coded value means "this respondent did not report one".
    raw_columns_present = all(column in trips.columns for column in RAW_HOUR_COLUMNS)
    if raw_columns_present:
        raw_seconds = mid_time_seconds(trips, *RAW_HOUR_COLUMNS)
    else:
        raw_seconds = pd.Series(float("nan"), index=trips.index)
        logger.warning(
            "%s the trips frame carries no %s column(s); the raw (as-reported MiD) column of "
            "the decomposition is NaN throughout -- only the pre-offset and realised profiles "
            "are measured", _LOG_TAG, list(RAW_HOUR_COLUMNS))

    seq = pd.to_numeric(trips["trip_index"], errors="coerce").astype(int)
    is_first = seq == seq.groupby(trips["person_id"].values).transform("min")

    group_by_pid = pd.Series(groups)
    frame = pd.DataFrame({
        "pid": trips["person_id"].to_numpy(),
        "seq": seq.to_numpy(),
        "purpose": harmonise_purpose(trips["following_purpose"], "following_purpose"),
        "position": np.where(is_first.to_numpy(), POSITION_FIRST, POSITION_LATER),
        "segment_group": trips["person_id"].map(group_by_pid).to_numpy(),
        "raw_dep_min": raw_seconds.to_numpy(dtype=float) / SECONDS_PER_MINUTE,
        "pre_offset_dep_min": ((realised_seconds - offset_seconds).to_numpy(dtype=float)
                               / SECONDS_PER_MINUTE),
        "realised_dep_min": realised_seconds.to_numpy(dtype=float) / SECONDS_PER_MINUTE,
        "arr_min": (pd.to_numeric(trips["arrival_time"], errors="coerce").to_numpy(dtype=float)
                    / SECONDS_PER_MINUTE),
    })

    unknown_group = frame["segment_group"].isna()
    if unknown_group.any():
        raise ValueError(
            "%d of %d legs belong to a person with no harmonised group; person_groups() must "
            "cover every person in the trips view (missing person ids e.g. %s)"
            % (int(unknown_group.sum()), len(frame),
               sorted(frame.loc[unknown_group, "pid"].unique().tolist())[:5]))

    frame = frame.sort_values(["pid", "seq"])[HARMONISED_COLUMNS].reset_index(drop=True)

    n_raw = int(np.isfinite(frame["raw_dep_min"]).sum())
    share_raw = n_raw / n_legs if n_legs else float("nan")
    message = ("%s raw MiD departure time available for %d/%d leg(s) (%.2f%%); the "
               "pre-offset and realised profiles cover every leg")
    arguments = (_LOG_TAG, n_raw, n_legs, 100.0 * share_raw if n_legs else float("nan"))
    if n_legs and share_raw < RAW_COVERAGE_WARN_THRESHOLD:
        logger.warning(message + " -- below the %.0f%% coverage threshold, so the raw column "
                       "describes a minority of the day (expected on the reporting-day view, "
                       "where a spliced donor day carries no raw MiD columns)",
                       *arguments, 100.0 * RAW_COVERAGE_WARN_THRESHOLD)
    else:
        logger.info(message, *arguments)

    # Two clocks (see the module docstring): a leg that fix_trip_times moved past midnight sits
    # 24 h below its pre-offset time in the raw column, with nothing wrong anywhere. Counting
    # those legs explicitly is what keeps the remaining raw-vs-pre-offset differences readable as
    # actual shifts -- and it is the only way to see the after-midnight tail at all, since the raw
    # column cannot reach hour 28 and n_hours_clipped_raw is therefore structurally 0.
    difference = (frame["pre_offset_dep_min"] - frame["raw_dep_min"]).abs()
    is_midnight_shift = (np.isfinite(difference)
                         & ((difference - MINUTES_PER_DAY).abs()
                            <= MIDNIGHT_SHIFT_TOLERANCE_MINUTES))
    n_midnight = int(is_midnight_shift.sum())
    if n_midnight:
        logger.info(
            "%s %d/%d leg(s) with a raw time (%.2f%% of them) sit exactly %g h below their "
            "pre-offset time: fix_trip_times moved them past midnight, so their raw value is on "
            "the 0-23 h clock while pre_offset/realised are on the 0-47 h one -- not a shift the "
            "departure-time model made", _LOG_TAG, n_midnight, n_raw,
            100.0 * n_midnight / n_raw if n_raw else float("nan"),
            MINUTES_PER_DAY / MINUTES_PER_HOUR)

    frame.attrs["n_legs"] = n_legs
    frame.attrs["n_legs_with_raw_time"] = n_raw
    frame.attrs["share_legs_with_raw_time"] = share_raw
    frame.attrs["raw_columns_present"] = raw_columns_present
    frame.attrs["n_legs_without_offset"] = n_without_offset
    frame.attrs["n_legs_raw_pre_offset_differ_by_24h"] = n_midnight
    return frame


# --------------------------------------------------------------------------- cell iteration

def _iter_cells():
    """Every ``(segment, purpose, position)`` cell of the comparison taxonomy, in table order."""
    for segment in SEGMENTS:
        for purpose in PURPOSES:
            for position in POSITIONS:
                yield segment, purpose, position


def _select_cell(frame: pd.DataFrame, segment: str, purpose: str, position: str) -> pd.DataFrame:
    """Restrict ``frame`` (or a pre-aggregated table of it) to one cell.

    ``all`` is a POOLED level on each of the three axes, not a stored value: the pooled purpose
    therefore includes legs whose destination purpose could not be mapped, exactly as the
    reference builder's ``_purpose_masks`` does.
    """
    selected = frame
    if segment != SEGMENT_ALL:
        selected = selected[selected["segment_group"] == segment]
    if purpose != PURPOSE_ALL:
        selected = selected[selected["purpose"] == purpose]
    if position != POSITION_ALL:
        selected = selected[selected["position"] == position]
    return selected


def _counts_by(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
    """Per-``(segment_group, purpose, position, value)`` leg counts of a FINITE value column.

    Aggregating once and pooling the ``all`` levels afterwards keeps the whole comparison linear
    in the number of legs; masking the full trip table 144 times (once per cell) would copy a
    full-scale population's trips for every cell.
    """
    valid = frame[np.isfinite(frame[value_column])].copy()
    # Both callers group on an INTEGER-valued index (a clock hour or a 15-minute bin) that was
    # produced by floor/clip and therefore arrives as float; the non-finite rows are gone by
    # now, so the cast is total and it keeps the group keys comparable to the integer values
    # _cell_shares looks up.
    valid[value_column] = valid[value_column].astype(int)
    keys = ["segment_group", "purpose", "position", value_column]
    return valid.groupby(keys, observed=True, dropna=False).size().rename("n").reset_index()


def _cell_shares(counts: pd.DataFrame, value_column: str, values) -> tuple:
    """``(shares, n)`` over ``values`` for one already-selected cell.

    An empty cell yields ``n == 0`` and NaN for every share -- never a fabricated zero
    distribution, which would read as "the model puts no mass here" instead of "there is
    nothing to measure" (review ruling A-R9 on the reference side, applied to the model side).
    """
    total = int(counts["n"].sum())
    if total == 0:
        return np.full(len(values), float("nan")), 0
    by_value = counts.groupby(value_column, observed=True)["n"].sum()
    return np.array([float(by_value.get(value, 0)) / total for value in values]), total


# --------------------------------------------------------------------------- decomposition

def _hours_of(minutes: pd.Series) -> tuple:
    """``(hour, n_clipped)``: ``floor(minutes / 60)`` clipped into :data:`HOURS`.

    Clipping rather than dropping keeps a leg that departs after 28:00 (a legitimate
    after-midnight continuation of the reporting day) in its cell's denominator; the number of
    legs the clip actually moved is returned so it can be reported instead of hidden.
    """
    raw_hour = np.floor(minutes.to_numpy(dtype=float) / MINUTES_PER_HOUR)
    clipped = np.clip(raw_hour, HOURS[0], HOURS[-1])
    # A non-finite value stays non-finite through floor/clip and is dropped by _counts_by; it
    # must NOT be counted as clipped (NaN != NaN is True), which is what a bare inequality here
    # would report.
    n_clipped = int(np.sum(np.isfinite(raw_hour) & (raw_hour != clipped)))
    return clipped, n_clipped


def decomposition(frame: pd.DataFrame) -> pd.DataFrame:
    """Hourly profile of the raw, pre-offset and realised departure times, per cell.

    ``frame`` is :func:`harmonise_model_times` output. Returns :data:`DECOMPOSITION_COLUMNS`,
    DENSE over :data:`HOURS` for every ``(segment, purpose, position)`` cell.

    Each share column is normalised over the legs of that cell with a FINITE value in the
    corresponding time column; ``n`` is the cell's total leg count and ``n_raw`` the subset with
    a usable raw MiD time, so a reader can see the raw column's denominator directly. A cell
    with no usable value in a column carries NaN for that column's shares -- the case the
    reporting-day view produces for every spliced donor day, whose raw MiD columns were nulled by
    ``braunschweig.synthesis.commute_day.plan_replacement``. The denominators of the pre-offset
    and realised columns (which normally equal ``n``) are reported on ``DataFrame.attrs``
    together with the per-column clip counts.
    """
    work = frame.copy()
    hours = {}
    clips = {}
    for column, label in (("raw_dep_min", "raw"), ("pre_offset_dep_min", "pre_offset"),
                          ("realised_dep_min", "realised")):
        work["hour_" + label], clips[label] = _hours_of(work[column])
        hours[label] = _counts_by(work, "hour_" + label)
    sizes = work.groupby(["segment_group", "purpose", "position"],
                         observed=True).size().rename("n").reset_index()

    rows = []
    totals = {"raw": 0, "pre_offset": 0, "realised": 0}
    for segment, purpose, position in _iter_cells():
        n_total = int(_select_cell(sizes, segment, purpose, position)["n"].sum())
        shares = {}
        counts = {}
        for label in ("raw", "pre_offset", "realised"):
            cell = _select_cell(hours[label], segment, purpose, position)
            shares[label], counts[label] = _cell_shares(cell, "hour_" + label, HOURS)
        for index, hour in enumerate(HOURS):
            rows.append({"segment": segment, "purpose": purpose, "position": position,
                         "hour": hour,
                         "share_raw": shares["raw"][index],
                         "share_pre_offset": shares["pre_offset"][index],
                         "share_realised": shares["realised"][index],
                         "n_raw": counts["raw"], "n": n_total})
        if segment == SEGMENT_ALL and purpose == PURPOSE_ALL and position == POSITION_ALL:
            totals = dict(counts)

    table = pd.DataFrame(rows, columns=DECOMPOSITION_COLUMNS)
    for label in ("raw", "pre_offset", "realised"):
        table.attrs["n_legs_finite_" + label] = int(totals[label])
        table.attrs["n_hours_clipped_" + label] = int(clips[label])
    logger.info("%s decomposition: %d cell(s) x %d hour(s); finite times raw %d, pre-offset %d, "
                "realised %d; hours clipped into [%d, %d] raw %d, pre-offset %d, realised %d",
                _LOG_TAG, len(rows) // len(HOURS), len(HOURS), totals["raw"],
                totals["pre_offset"], totals["realised"], HOURS[0], HOURS[-1],
                clips["raw"], clips["pre_offset"], clips["realised"])
    return table


# --------------------------------------------------------------------------- bin comparison

def _reference_cells(reference: pd.DataFrame) -> dict:
    """``(segment, purpose, position) -> (derounded, as_reported, n_unweighted)`` vectors.

    Structural validity -- dense bins per cell, ONE ``n_unweighted`` per cell, both share columns
    summing to 1 within :data:`SHARE_SUM_TOLERANCE` -- is delegated ENTIRELY to
    :func:`braunschweig.calibration.srv_departure_times.validate_departure_time_table`, the SAME
    shared validator :func:`load_departure_time_reference` already runs at load time (final fix
    wave item 4): this function used to re-implement the identical dense-bin and sum-to-1 checks a
    second time, which could only ever agree with the shared validator by construction and cost a
    second maintenance site if it ever drifted. ``bin_comparison`` (this function's only caller)
    is, however, reachable with a frame that never went through the shared loader -- several tests
    below hand it a hand-built reference directly -- so the validator is called HERE too, rather
    than only trusted to have run upstream.

    A cell whose ``n_unweighted`` is 0 (the builder's EMPTY-cell encoding, NaN shares on every
    bin) is returned with ``None`` vectors so callers skip it explicitly instead of reading NaN
    as a distribution.
    """
    required = ["segment", "purpose", "position", "bin_15min", "share_derounded",
                "share_as_reported", "n_unweighted"]
    _require_columns(reference, required, "departure-time reference")
    # positions=None: unlike the model's own loader this comparison keeps all three positions
    # (later/all are the hold-out dimensions), so no restriction is imposed here either.
    SRVDT.validate_departure_time_table(reference, positions=None,
                                        source="reference frame (bin_comparison)")
    cells = {}
    for key, cell in reference.groupby(["segment", "purpose", "position"], sort=False):
        ordered = cell.sort_values("bin_15min")
        n_unweighted = int(ordered["n_unweighted"].iloc[0])
        if n_unweighted <= 0:
            cells[key] = (None, None, n_unweighted)
            continue
        cells[key] = (ordered["share_derounded"].to_numpy(dtype=float),
                      ordered["share_as_reported"].to_numpy(dtype=float), n_unweighted)
    return cells


def bin_comparison(frame: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Model vs SrV 15-minute departure-bin shares per ``(segment, purpose, position)``.

    The model side bins the REALISED departure times -- what MATSim simulates -- by the reference
    builder's own rule (``floor(minutes / 15)`` clipped into ``[0, N_BINS - 1]``, with the clip
    counted), so the two sides share one bin geometry. The SrV side is read from the committed
    table's two share columns; see the module docstring for why both are reported.

    A cell is compared only when BOTH sides carry legs. A cell that is empty on either side is
    SKIPPED and counted on ``DataFrame.attrs`` (``n_cells_compared``,
    ``n_cells_skipped_model_empty``, ``n_cells_skipped_reference_empty``,
    ``n_cells_skipped_not_in_reference``) -- comparing against a fabricated zero vector would
    produce a well-formed EMD that measures nothing.

    Returns :data:`COMPARISON_COLUMNS`, dense over ``bin_15min`` within every compared cell.
    """
    reference_cells = _reference_cells(reference)

    # Legs whose realised time is not finite are dropped BEFORE binning: bin_from_minutes
    # would turn a NaN into a garbage integer bin and count it as clipped.
    work = frame[np.isfinite(frame["realised_dep_min"])].copy()
    bins, n_clipped = SRVDT.bin_from_minutes(work["realised_dep_min"].to_numpy(dtype=float))
    work["bin_15min"] = bins
    counts = _counts_by(work, "bin_15min")

    rows = []
    n_compared = 0
    n_model_empty = 0
    n_reference_empty = 0
    n_not_in_reference = 0
    for segment, purpose, position in _iter_cells():
        entry = reference_cells.get((segment, purpose, position))
        if entry is None:
            n_not_in_reference += 1
            continue
        derounded, as_reported, n_srv = entry
        shares, n_model = _cell_shares(
            _select_cell(counts, segment, purpose, position), "bin_15min",
            range(SRVDT.N_BINS))
        if derounded is None:
            n_reference_empty += 1
            continue
        if n_model == 0:
            n_model_empty += 1
            continue
        n_compared += 1
        for b in range(SRVDT.N_BINS):
            rows.append({"segment": segment, "purpose": purpose, "position": position,
                         "bin_15min": b, "share_model": float(shares[b]),
                         "share_srv_derounded": float(derounded[b]),
                         "share_srv_as_reported": float(as_reported[b]),
                         "n_model": n_model, "n_srv": n_srv})

    table = pd.DataFrame(rows, columns=COMPARISON_COLUMNS)
    table.attrs["n_cells_compared"] = n_compared
    table.attrs["n_cells_skipped_model_empty"] = n_model_empty
    table.attrs["n_cells_skipped_reference_empty"] = n_reference_empty
    table.attrs["n_cells_skipped_not_in_reference"] = n_not_in_reference
    table.attrs["n_bins_clipped_model"] = int(n_clipped)
    logger.info("%s 15-minute comparison: %d cell(s) compared, %d skipped (model side empty), "
                "%d skipped (reference cell empty), %d absent from the reference; %d model leg "
                "bin(s) clipped into [0, %d]", _LOG_TAG, n_compared, n_model_empty,
                n_reference_empty, n_not_in_reference, int(n_clipped), SRVDT.N_BINS - 1)
    return table


def emd_table(comparison: pd.DataFrame) -> pd.DataFrame:
    """Earth Mover's Distance per compared cell, against BOTH reference columns.

    ``comparison`` is :func:`bin_comparison` output. Returns :data:`EMD_COLUMNS`; ``dimension``
    is :data:`DIMENSION_CALIBRATED` for ``position == "first"`` (the only dimension the
    ``srv_mapped`` model is fitted on) and :data:`DIMENSION_HOLDOUT` for every other position, so
    a hold-out result can never be read as a fitted one.

    The EMD is :func:`braunschweig.calibration.metrics.emd_on_bands` over the 112-bin vectors:
    the mean absolute CDF difference normalised so that moving all mass across the whole day is
    1.0 and moving it by one 15-minute bin is 1/111.
    """
    _require_columns(comparison, COMPARISON_COLUMNS, "comparison")
    rows = []
    for (segment, purpose, position), cell in comparison.groupby(
            ["segment", "purpose", "position"], sort=False):
        ordered = cell.sort_values("bin_15min")
        model = ordered["share_model"].to_numpy(dtype=float)
        rows.append({
            "segment": segment, "purpose": purpose, "position": position,
            "emd_vs_derounded": metrics.emd_on_bands(
                model, ordered["share_srv_derounded"].to_numpy(dtype=float)),
            "emd_vs_as_reported": metrics.emd_on_bands(
                model, ordered["share_srv_as_reported"].to_numpy(dtype=float)),
            "n_model": int(ordered["n_model"].iloc[0]),
            "n_srv": int(ordered["n_srv"].iloc[0]),
            "dimension": (DIMENSION_CALIBRATED if position == POSITION_FIRST
                          else DIMENSION_HOLDOUT),
        })
    return pd.DataFrame(rows, columns=EMD_COLUMNS)


# --------------------------------------------------------------------- activity durations

def activity_duration_comparison(frame: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Model vs SrV duration-band shares of the activity following each leg (HOLD-OUT).

    The duration is the NEXT leg's REALISED departure minus this leg's arrival within the same
    person, in hours; the purpose is this leg's DESTINATION purpose, i.e. the activity the leg
    leads into -- the same two definitions
    :func:`braunschweig.calibration.srv_departure_times.activity_duration_table` used to build
    the committed reference. A leg with no following leg or a missing time has no measurable
    duration and is excluded and counted; a duration outside
    ``[0, srv_plan_structure.WORK_ACTIVITY_MAX_H]`` hours is excluded and counted separately,
    never clipped.

    Durations are a HOLD-OUT dimension: no departure-time model touches them directly (a
    per-person offset moves a whole chain, leaving every within-person duration unchanged), so a
    change here can only come from which donor day a person received.

    ``reference`` is the committed activity-duration table (``segment``, ``purpose``, ``band``,
    ``share``, ``n_unweighted``). Rows are emitted for the reference's cells that the model can
    also measure; a model cell with no reference counterpart, and a reference cell with no model
    leg, are counted on ``DataFrame.attrs`` rather than compared against a fabricated zero.

    Returns :data:`DURATION_COLUMNS`; ``delta_pp`` is ``(share_model - share_srv) * 100``.
    """
    _require_columns(reference, ["segment", "purpose", "band", "share", "n_unweighted"],
                     "activity-duration reference")

    work = frame.sort_values(["pid", "seq"]).copy()
    work["next_dep_min"] = work.groupby("pid")["realised_dep_min"].shift(-1)
    n_total = int(len(work))
    measurable = np.isfinite(work["next_dep_min"]) & np.isfinite(work["arr_min"])
    n_no_next = int((~measurable).sum())
    work = work[measurable].copy()
    work["duration_h"] = (work["next_dep_min"] - work["arr_min"]) / MINUTES_PER_HOUR
    in_range = (work["duration_h"] >= 0) & (work["duration_h"] <= SRV.WORK_ACTIVITY_MAX_H)
    n_out_of_range = int((~in_range).sum())
    work = work[in_range].copy()
    logger.info("%s activity durations: %d/%d leg(s) measurable (%d have no following leg or a "
                "missing time), %d excluded as outside [0, %g] h, %d retained", _LOG_TAG,
                n_total - n_no_next, n_total, n_no_next, n_out_of_range, SRV.WORK_ACTIVITY_MAX_H,
                len(work))

    work["band"] = pd.cut(work["duration_h"], bins=list(SRVDT.DURATION_BAND_EDGES_H),
                          labels=list(SRVDT.DURATION_BAND_LABELS),
                          include_lowest=True).astype(str)
    counts = work.groupby(["segment_group", "purpose", "position", "band"],
                          observed=True).size().rename("n").reset_index()

    reference_cells = {
        key: cell.set_index("band")
        for key, cell in reference.groupby(["segment", "purpose"], sort=False)}

    rows = []
    n_compared = 0
    n_model_empty = 0
    n_model_only = 0
    for segment in SEGMENTS:
        for purpose in PURPOSES:
            # Durations pool every position: an activity follows a leg regardless of where in
            # the chain that leg sits, exactly as the reference builder measures them.
            cell = _select_cell(counts, segment, purpose, POSITION_ALL)
            shares, n_model = _cell_shares(cell, "band", SRVDT.DURATION_BAND_LABELS)
            srv_cell = reference_cells.get((segment, purpose))
            if srv_cell is None:
                if n_model > 0:
                    n_model_only += 1
                continue
            if n_model == 0:
                n_model_empty += 1
                continue
            n_compared += 1
            n_srv = int(srv_cell["n_unweighted"].iloc[0])
            for index, band in enumerate(SRVDT.DURATION_BAND_LABELS):
                share_srv = (float(srv_cell.loc[band, "share"]) if band in srv_cell.index
                             else float("nan"))
                rows.append({"segment": segment, "purpose": purpose, "band": band,
                             "share_model": float(shares[index]), "share_srv": share_srv,
                             "delta_pp": (float(shares[index]) - share_srv) * 100.0,
                             "n_model": n_model, "n_srv": n_srv})

    table = pd.DataFrame(rows, columns=DURATION_COLUMNS)
    table.attrs["n_legs_total"] = n_total
    table.attrs["n_legs_excluded_no_next_or_missing_time"] = n_no_next
    table.attrs["n_legs_excluded_out_of_range"] = n_out_of_range
    table.attrs["n_legs_included"] = int(len(work))
    table.attrs["n_cells_compared"] = n_compared
    table.attrs["n_cells_skipped_model_empty"] = n_model_empty
    table.attrs["n_cells_model_only"] = n_model_only
    if n_model_only:
        logger.warning("%s %d (segment, purpose) cell(s) have model activities but no reference "
                       "cell; they are NOT in activity_duration.csv (the committed table omits a "
                       "combination with no SrV leg) and are counted here instead", _LOG_TAG,
                       n_model_only)
    logger.info("%s activity-duration comparison: %d cell(s) compared, %d skipped (no model "
                "activity), %d model-only", _LOG_TAG, n_compared, n_model_empty, n_model_only)
    return table
