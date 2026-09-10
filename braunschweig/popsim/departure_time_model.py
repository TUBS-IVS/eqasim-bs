"""The departure-time model (issue #123, ADR-0114): three interchangeable start-time models.

eqasim gives every person's whole day ONE uniform offset drawn from
``+/- min(1800 s, first departure)`` (:func:`braunschweig.popsim.trips_stage.apply_per_person_jitter`).
That draw carries no information: it exists only to break the survey's clock-time grid, and it
BIASES early chains (the ``min(1800, first_departure)`` clip shrinks the interval for anybody
departing before 0:30 and, being symmetric, cannot move a start time toward the observed morning
peak). This module offers three models behind one dispatch function
(:func:`apply_departure_time_model`), selected per run by the ``departure_time_model`` config key
(Task 4):

``eqasim_uniform``
    The unchanged eqasim jitter -- delegated to ``apply_per_person_jitter`` so the OFF path stays
    byte-identical (this is the code default; a change of realised departure times must be an
    explicit, flagged decision).
``derounded``
    Only step (i): the reported first departure is DE-ROUNDED inside the half-width of the
    MiD/SrV reporting grid (+/- 7.5 min on the quarter hour, +/- 2.5 min on the five-minute grid,
    0 for a to-the-minute report -- the single rule in
    :mod:`braunschweig.calibration.reported_time_precision`, Task 1). This replaces eqasim's
    arbitrary +/- 30 min with the survey's ACTUAL reporting precision and nothing else.
``srv_mapped``
    Step (i) plus step (ii): the de-rounded first departure is quantile-mapped, RANK-PRESERVING
    within its ``(first purpose x harmonised person group)`` cell, onto the de-rounded SrV
    first-departure distribution of that cell (the committed
    ``srv2023_departure_time_reference.csv``, Task 2), so the synthetic population's realised
    start-time distribution reproduces the SrV one instead of the donor's own reporting grid
    blurred by a uniform draw.

In every model the WHOLE chain of a person moves by ONE offset (departure and arrival of every
trip), so within-chain ordering, trip durations and activity durations are untouched: only the
day's start time is modelled here. For a mapped person the de-rounding cancels out of the total
offset by construction (``total = derounding + (target - (reported + derounding))``); its role is
to place the person INSIDE the reported quarter hour before ranking, which is what breaks the
ties between the many persons reporting the same clock time -- the de-rounding draw is the ONLY
random component of ``srv_mapped``, and the mapping itself is deterministic given the person_id
order.

Scope and hold-out (spec ``2026-09-09-departure-time-srv-mapping-design.md`` section 2.2, ruling
A-R9): ONLY the FIRST departure is calibrated. Later departures, trip durations and activity
durations follow the donor diary and are the model's hold-out validation dimension -- they are
never mapped here.

Seeding (ruling A-R2): the de-rounding draws come from
``np.random.RandomState(random_seed + DEPARTURE_TIME_SEED_OFFSET)``, so the model consumes its own
RNG stream and cannot shift any other stage's draws; ``DEPARTURE_TIME_SEED_OFFSET`` was verified
unused elsewhere in the repository on 2026-09-09.

Import direction (ruling A-R3): :data:`OFFSET_COLUMN` is declared HERE, and ``trips_stage``
imports it at module level and re-exports it under its historical name. This module therefore
imports ``trips_stage.apply_per_person_jitter`` LAZILY, inside
:func:`apply_departure_time_model`, so there is no circular import.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.calibration import reported_time_precision as reported_precision
from braunschweig.calibration import srv_plan_structure
from braunschweig.calibration.srv_departure_times import (BIN_MINUTES, DEPARTURE_TIME_COLUMNS,
                                                          DEPARTURE_TIME_TABLE, N_BINS,
                                                          PURPOSE_ALL)
from braunschweig.popsim.attributes import EMPLOYED_TAET
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

logger = logging.getLogger(__name__)

_LOG_TAG = "[departure time]"

#: Column recording the per-person departure-time offset actually applied by
#: :func:`braunschweig.popsim.trips_stage.apply_per_person_jitter` and by
#: :func:`apply_departure_time_model`: the SAME offset, already rounded to whole seconds, that was
#: added to both ``departure_time`` and ``arrival_time``. Recorded so later analysis can decompose
#: a model time as ``departure_time = raw_departure_time + offset`` without re-deriving the offset
#: from the RNG stream.
OFFSET_COLUMN = "departure_time_offset_seconds"

#: The three departure-time models -- see the module docstring. ``eqasim_uniform`` is the code
#: default everywhere (Task 4): a change of realised departure times must be explicitly chosen.
MODEL_EQASIM_UNIFORM = "eqasim_uniform"
MODEL_DEROUNDED = "derounded"
MODEL_SRV_MAPPED = "srv_mapped"
MODELS = (MODEL_EQASIM_UNIFORM, MODEL_DEROUNDED, MODEL_SRV_MAPPED)

#: Added to a run's ``random_seed`` for the de-rounding draws so this model owns its own RNG
#: stream (ruling A-R2; the value was verified unused elsewhere in the repository).
DEPARTURE_TIME_SEED_OFFSET = 7361

#: Coarsening ladder of the quantile mapping, most specific first: the person's own
#: ``(purpose, group)`` reference cell, then that purpose pooled over all groups, then the fully
#: pooled cell; ``unmapped`` means no rung was usable (too few reference observations, too few
#: model persons in the cell, or no first departure at all) -- those persons keep ONLY their
#: de-rounding offset.
LEVEL_PURPOSE_GROUP = "purpose_group"
LEVEL_PURPOSE_ALL = "purpose_all"
LEVEL_ALL_ALL = "all_all"
LEVEL_UNMAPPED = "unmapped"
LEVEL_LABELS = (LEVEL_PURPOSE_GROUP, LEVEL_PURPOSE_ALL, LEVEL_ALL_ALL, LEVEL_UNMAPPED)

#: Reference position consumed by the model: the person's FIRST trip of the day (the calibrated
#: target; "later"/"all" are hold-out -- see the module docstring).
REFERENCE_POSITION = "first"

#: Pooled reference segment label (``srv_departure_times.SEGMENTS[0]``); the pooled PURPOSE label
#: is imported from that module as ``PURPOSE_ALL`` so the two files cannot disagree.
SEGMENT_ALL = "all"

#: Tolerance for the "``share_derounded`` sums to 1" check on a non-empty reference cell (the
#: committed table is written with full float precision, so the residual is ~1e-11).
REFERENCE_SUM_TOLERANCE = 1e-6

#: Above this share of persons that could not be mapped, ``srv_mapped`` logs a WARNING: a high
#: unmapped share means most persons kept the donor's own start times and the mapping did NOT
#: happen, which must never pass silently (CLAUDE.md fallback transparency).
UNMAPPED_SHARE_WARN_THRESHOLD = 0.05

#: Above this share of persons mapped BELOW the most specific rung (i.e. at ``purpose_all``,
#: ``all_all`` or not at all), ``srv_mapped`` logs a WARNING naming the per-level split. Such a run
#: has calibrated most persons against a POOLED distribution rather than their own
#: (purpose, group) one, which is a weaker claim and must be visible in the log.
#: ASSUMPTION: 0.25 is an observability threshold chosen to surface a ladder that is doing most of
#: the work -- it is NOT a scientific bound, and no source states one.
COARSENED_SHARE_WARN = 0.25

#: Above this share of persons whose MiD ``P_TAET`` is missing or outside the substantive code
#: range (and are therefore grouped as NOT employed), :func:`persons_from_mid_schema` warns.
UNKNOWN_TAET_WARN_THRESHOLD = 0.10

#: Substantive MiD ``P_TAET`` code range (1..17); 99 is item non-response -- see
#: :mod:`braunschweig.popsim.attributes`, which imputes it for the population's own ``employed``
#: attribute. This module does NOT impute (see :func:`persons_from_mid_schema`).
_TAET_SUBSTANTIVE_CODES = frozenset(range(1, 18))


# --------------------------------------------------------------------------- helpers
def _require_columns(frame: pd.DataFrame, required, what: str) -> None:
    """Raise naming every missing column (and what was present), so a schema mismatch is
    diagnosable from the message alone."""
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(
            "departure_time_model: %s is missing required column(s) %s; present columns are %s"
            % (what, missing, list(frame.columns)))


# --------------------------------------------------------------------------- person schemas
def persons_from_mid_schema(persons: pd.DataFrame) -> pd.DataFrame:
    """Adapt MiD-schema person attributes to the harmonised frame :func:`person_groups` consumes.

    ``HP_ALTER`` -> ``age`` and ``P_TAET in`` :data:`braunschweig.popsim.attributes.EMPLOYED_TAET`
    -> ``employed`` (ruling A-R7). This is the adapter for the popsim trip build, whose person
    frame carries the raw MiD columns.

    ASSUMPTION: a missing or non-response ``P_TAET`` (99, NaN, anything outside the substantive
    1..17 range) is treated as NOT employed here, i.e. that person is grouped by age instead. This
    module deliberately does NOT run the imputation of
    :func:`braunschweig.popsim.attributes.map_employed`: the value is only used to pick a mapping
    CELL, and an unknown-status person then lands in the age-based group, which is exactly the
    coarsening the harmonised group rule already applies to every non-employed person. The share
    of such persons is logged as ``n/total`` and warned above
    :data:`UNKNOWN_TAET_WARN_THRESHOLD` -- never silent (CLAUDE.md fallback transparency).

    Raises
    ------
    ValueError
        If ``person_id``, ``HP_ALTER`` or ``P_TAET`` is missing (the message names them).
    """
    _require_columns(persons, ["person_id", "HP_ALTER", "P_TAET"], "persons frame (MiD schema)")
    taet = pd.to_numeric(persons["P_TAET"], errors="coerce")
    unknown = ~taet.isin(_TAET_SUBSTANTIVE_CODES)
    n_unknown, n_total = int(unknown.sum()), int(len(persons))
    if n_unknown:
        message = ("%s persons_from_mid_schema: %d/%d person(s) (%.2f%%) have a P_TAET outside "
                   "the substantive 1..17 range (missing or item non-response) and are grouped as "
                   "NOT employed")
        arguments = (_LOG_TAG, n_unknown, n_total, 100.0 * n_unknown / n_total)
        if n_total and n_unknown / n_total > UNKNOWN_TAET_WARN_THRESHOLD:
            logger.warning(message + "; above the %.0f%% threshold -- check the person frame",
                           *arguments, 100.0 * UNKNOWN_TAET_WARN_THRESHOLD)
        else:
            logger.info(message, *arguments)
    return pd.DataFrame({
        "person_id": persons["person_id"].to_numpy(),
        "age": pd.to_numeric(persons["HP_ALTER"], errors="coerce").to_numpy(),
        "employed": taet.isin(EMPLOYED_TAET).to_numpy(),
    })


def persons_from_synthetic_schema(persons: pd.DataFrame) -> pd.DataFrame:
    """Adapt a synthetic-population person frame (``age``/``employed`` already harmonised) to the
    frame :func:`person_groups` consumes (ruling A-R7).

    This is the adapter for the commute-day plan replacement, which receives the ALREADY
    harmonised synthetic persons; it only selects and re-orders the three columns, so a schema
    drift on the caller's side surfaces here rather than as a silently different grouping.

    Raises
    ------
    ValueError
        If ``person_id``, ``age`` or ``employed`` is missing (the message names them).
    """
    _require_columns(persons, ["person_id", "age", "employed"],
                     "persons frame (synthetic-population schema)")
    return persons[["person_id", "age", "employed"]].reset_index(drop=True).copy()


def person_groups(persons: pd.DataFrame) -> pd.Series:
    """Harmonised employment x life-phase group per person, indexed by ``person_id``.

    Delegates to :func:`braunschweig.calibration.srv_plan_structure.harmonised_group`, which is
    the SAME rule the SrV reference was segmented by -- the model's mapping cell and the
    reference's segment must be produced by one function or they can silently disagree.

    Parameters
    ----------
    persons:
        Harmonised person frame with ``person_id``, ``age`` and ``employed`` -- see
        :func:`persons_from_mid_schema` / :func:`persons_from_synthetic_schema`.

    Raises
    ------
    ValueError
        If a required column is missing or ``person_id`` has duplicates (a duplicate would make
        the per-person reindex in :func:`apply_departure_time_model` ambiguous).
    """
    _require_columns(persons, ["person_id", "age", "employed"], "persons frame")
    duplicated = persons["person_id"].duplicated()
    if duplicated.any():
        examples = persons.loc[duplicated, "person_id"].unique()[:5].tolist()
        raise ValueError(
            "departure_time_model: persons frame has %d duplicate person_id row(s) (e.g. %s); "
            "one attribute row per person is required" % (int(duplicated.sum()), examples))
    groups = np.asarray(srv_plan_structure.harmonised_group(persons)).tolist()
    return pd.Series(groups, index=pd.Index(persons["person_id"].to_numpy(), name="person_id"),
                     name="group")


# --------------------------------------------------------------------------- reference table
def _validate_reference_frame(reference: pd.DataFrame, source: str) -> None:
    """Validate the departure-time reference: position, dense bins, and shares summing to 1.

    Checked per ``(segment, purpose)`` cell: exactly the bins ``0..N_BINS-1``, each once; one
    single ``n_unweighted`` value across those rows (it is a per-cell count, repeated per bin);
    and, for a cell with ``n_unweighted > 0``, a finite ``share_derounded`` summing to 1 within
    :data:`REFERENCE_SUM_TOLERANCE`. A cell with ``n_unweighted == 0`` is legitimately EMPTY (the
    builder emits it with NaN shares rather than dropping it) and means "no reference for this
    cell" -- the coarsening ladder skips it; it is never read as a zero distribution.
    """
    _require_columns(reference, ["segment", "purpose", "bin_15min", "share_derounded",
                                 "n_unweighted"], "departure-time reference (%s)" % source)
    if "position" in reference.columns:
        positions = set(reference["position"].unique())
        if positions != {REFERENCE_POSITION}:
            raise ValueError(
                "departure_time_model: departure-time reference (%s) carries position(s) %s; the "
                "model consumes position '%s' only -- filter it with "
                "load_departure_time_reference (positions 'later'/'all' are hold-out references "
                "and must never enter the mapping)"
                % (source, sorted(positions), REFERENCE_POSITION))
    expected_bins = list(range(N_BINS))
    for (segment, purpose), cell in reference.groupby(["segment", "purpose"], sort=True):
        bins = sorted(cell["bin_15min"].tolist())
        if bins != expected_bins:
            raise ValueError(
                "departure_time_model: departure-time reference (%s) cell segment=%s purpose=%s "
                "carries %d bin(s) instead of the dense %d bins 0..%d (missing %s, duplicated %s)"
                % (source, segment, purpose, len(bins), N_BINS, N_BINS - 1,
                   sorted(set(expected_bins) - set(bins))[:5],
                   sorted({b for b in bins if bins.count(b) > 1})[:5]))
        # n_unweighted is a per-CELL count repeated on every bin row; a varying value means the
        # rows of two different cells were merged (or the file was hand-edited), which would make
        # every threshold decision below depend on which row happened to be read first.
        counts = cell["n_unweighted"].unique()
        if len(counts) != 1:
            raise ValueError(
                "departure_time_model: departure-time reference (%s) cell segment=%s purpose=%s "
                "carries %d different n_unweighted value(s) %s across its %d bin rows; the count "
                "is a per-cell property and must be identical on every bin"
                % (source, segment, purpose, len(counts), sorted(counts.tolist())[:5], N_BINS))
        if int(cell["n_unweighted"].iloc[0]) <= 0:
            continue                       # legitimately empty cell -- see the docstring
        total = float(cell["share_derounded"].sum())
        if not np.isfinite(total) or abs(total - 1.0) > REFERENCE_SUM_TOLERANCE:
            raise ValueError(
                "departure_time_model: departure-time reference (%s) cell segment=%s purpose=%s "
                "has n_unweighted=%d but its share_derounded values sum to %r instead of 1.0 "
                "(tolerance %g)" % (source, segment, purpose, int(cell["n_unweighted"].iloc[0]),
                                    total, REFERENCE_SUM_TOLERANCE))


def load_departure_time_reference(srv_dir: str) -> pd.DataFrame:
    """Load the committed SrV departure-time reference, filtered to the FIRST-trip position.

    Parameters
    ----------
    srv_dir:
        Directory holding ``srv2023_departure_time_reference.csv`` (in a run:
        ``<data_path>/braunschweig/srv``). The file's ``#`` provenance header is skipped.

    Returns
    -------
    pd.DataFrame
        The table's ``position == "first"`` rows, validated by :func:`_validate_reference_frame`.

    Raises
    ------
    FileNotFoundError
        If the table is not in ``srv_dir`` (the message names the expected path).
    ValueError
        If the table is malformed -- see :func:`_validate_reference_frame`.
    """
    path = os.path.join(srv_dir, DEPARTURE_TIME_TABLE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            "departure_time_model: the SrV departure-time reference %s was not found at %s; it is "
            "committed under eqasim-data/data/braunschweig/srv and is rebuilt by "
            "scripts/extract_srv_departure_times.py" % (DEPARTURE_TIME_TABLE, path))
    table = pd.read_csv(path, comment="#")
    _require_columns(table, DEPARTURE_TIME_COLUMNS, "departure-time reference (%s)" % path)
    first = table[table["position"] == REFERENCE_POSITION].copy()
    if first.empty:
        raise ValueError(
            "departure_time_model: departure-time reference %s carries no position == '%s' row"
            % (path, REFERENCE_POSITION))
    _validate_reference_frame(first, source=path)
    n_cells = int(first.groupby(["segment", "purpose"], sort=False).ngroups)
    n_empty = int((first.groupby(["segment", "purpose"], sort=False)["n_unweighted"].first() <= 0)
                  .sum())
    logger.info("%s loaded the SrV departure-time reference %s: %d cell(s), %d/%d empty "
                "(n_unweighted 0 -- no reference for that cell)",
                _LOG_TAG, path, n_cells, n_empty, n_cells)
    return first


def _reference_cells(reference: pd.DataFrame, source: str) -> dict:
    """``{(purpose, segment): (share_derounded per bin, n_unweighted)}`` -- validated and ordered
    by ``bin_15min`` so the array index IS the bin index."""
    _validate_reference_frame(reference, source)
    cells = {}
    for (segment, purpose), cell in reference.groupby(["segment", "purpose"], sort=False):
        ordered = cell.sort_values("bin_15min")
        cells[(str(purpose), str(segment))] = (ordered["share_derounded"].to_numpy(dtype=float),
                                               int(ordered["n_unweighted"].iloc[0]))
    return cells


# --------------------------------------------------------------------------- de-rounding
def derounding_offsets(first_departure_seconds, rng: np.random.RandomState) -> np.ndarray:
    """De-rounding offsets in SECONDS for reported departure times given in seconds.

    The reporting-precision rule (:mod:`braunschweig.calibration.reported_time_precision`) is
    stated on the reported MINUTE, so the seconds are floored to whole minutes for the half-width
    lookup: MiD and SrV both record a departure as a whole number of minutes, so the floor is a
    no-op on real survey input. A sub-minute residue (which the reporting grid cannot carry, and
    which only a synthetic input can have) is counted, logged and PRESERVED -- the returned offset
    is added to the original seconds value by the caller, not to the floored one.

    Parameters
    ----------
    first_departure_seconds:
        Reported first-departure times, in seconds since midnight. Must be finite and
        non-negative: a person without a first departure is not de-rounded at all (the caller
        counts them), and a negative departure is an upstream error, not a rounding artefact.
    rng:
        ``np.random.RandomState`` for the ONE vectorised uniform draw (see
        :func:`reported_time_precision.deround_minutes_of_day`).

    Returns
    -------
    np.ndarray
        Offset in seconds per element, ``U(-h, +h)`` with ``h`` that element's reporting half
        width (0 for a to-the-minute report).

    Raises
    ------
    ValueError
        If any value is NaN or negative; the message names the count as ``n/total``.
    """
    seconds = np.asarray(first_departure_seconds, dtype=float)
    total = seconds.size
    n_nan = int(np.isnan(seconds).sum())
    if n_nan > 0:
        raise ValueError(
            "departure_time_model.derounding_offsets: %d/%d departure time(s) are NaN; a person "
            "without a first departure must be excluded (and counted) by the caller before "
            "de-rounding" % (n_nan, total))
    n_negative = int((seconds < 0).sum())
    if n_negative > 0:
        raise ValueError(
            "departure_time_model.derounding_offsets: %d/%d departure time(s) are negative; a "
            "reported departure time cannot precede midnight of the reporting day (fix the "
            "upstream trip build)" % (n_negative, total))
    n_sub_minute = int((seconds % 60.0 != 0).sum())
    if n_sub_minute:
        logger.info(
            "%s derounding_offsets: %d/%d departure time(s) are not a whole number of minutes; "
            "the reporting half-width is looked up on the floored minute and the sub-minute "
            "residue is preserved", _LOG_TAG, n_sub_minute, total)
    _derounded, offset_minutes = reported_precision.deround_minutes_of_day(
        np.floor(seconds / 60.0), rng)
    return offset_minutes * 60.0


# --------------------------------------------------------------------------- quantile mapping
def _inverse_cdf(quantiles: np.ndarray, shares: np.ndarray) -> np.ndarray:
    """Inverse CDF of a 15-minute-bin distribution, linear WITHIN the bin (seconds).

    The reference is a histogram, so a quantile only identifies the bin; the position inside that
    bin is interpolated uniformly from the quantile's position within the bin's share (the
    standard piecewise-uniform histogram inverse). Without it every mapped person in a cell would
    receive the same bin-edge time, i.e. an artificial 15-minute comb in the realised departure
    times.
    """
    cdf = np.concatenate([[0.0], np.cumsum(shares)])
    cdf = cdf / cdf[-1]                       # the committed shares sum to 1 within 1e-6, not exactly
    bins = np.clip(np.searchsorted(cdf, quantiles, side="right") - 1, 0, len(shares) - 1)
    lower, upper = cdf[bins], cdf[bins + 1]
    width = upper - lower
    # A zero-width step cannot be interpolated (no mass in that bin); it is only reachable through
    # the clip above, and the bin centre is the neutral choice there.
    within = np.where(width > 0.0, (quantiles - lower) / np.where(width > 0.0, width, 1.0), 0.5)
    return (bins + within) * BIN_MINUTES * 60.0


def _rung_is_usable(entry, min_reference_n: int) -> bool:
    """Whether a ladder rung's REFERENCE side can be mapped onto: the cell exists, carries at
    least ``min_reference_n`` unweighted observations, and is not the EMPTY cell the builder emits
    with NaN shares (``n_unweighted`` 0) -- an empty cell means "no reference for this cell" and is
    never read as a zero distribution."""
    if entry is None:
        return False
    shares, n_reference = entry
    if n_reference < max(min_reference_n, 1):
        return False
    return bool(np.isfinite(shares).all()) and bool(shares.sum() > 0)


def quantile_map_first_departures(first_departure_seconds, cells, reference: pd.DataFrame, *,
                                  min_reference_n: int, min_model_n: int):
    """Rank-preserving quantile mapping of de-rounded first departures onto the SrV reference.

    The person with the k-th smallest de-rounded first departure of a mapping set receives that
    set's ``(k + 0.5) / n`` reference quantile: the ORDER of the persons is preserved exactly,
    only the spacing is re-shaped to the survey's.

    Coarsening ladder (spec ``2026-09-09-departure-time-srv-mapping-design.md`` section 2.2,
    ruling A-R11) -- ``(purpose, group) -> (purpose, all groups) -> (all purposes, all groups) ->
    unmapped`` (:data:`LEVEL_LABELS`). A rung is taken when BOTH sides are thick enough:

    * the REFERENCE cell of that rung exists, is non-empty and has ``n_unweighted >=
      min_reference_n`` (:func:`_rung_is_usable`), and
    * the MODEL persons POOLED at that rung number at least ``min_model_n``.

    Pooling is what makes a thin cell CLIMB rather than drop out: the ``(purpose, "all")`` rung
    pools the model persons of every group that was too thin (or had no usable reference) for its
    own ``(purpose, group)`` rung, and the ``("all", "all")`` rung pools everyone still unplaced.
    Ten school-age education persons are therefore ranked TOGETHER with the other thin education
    persons against the pooled education reference, instead of being left with the donor's own
    start times. Only a person for whom no rung is usable at all stays ``unmapped``. This matters
    scientifically: with a per-cell gate a smaller sample would silently calibrate a smaller share
    of the population, so the calibrated share would depend on the sample rate far more than the
    ladder makes unavoidable.

    Ranking happens WITHIN the pooled model set of the rung a person maps at, over the union of
    every contributing cell, so the pooled set has ONE common rank order. Determinism: a rung's
    positions are sorted ascending (the values arrive in ``person_id`` order) and the rank sort is
    stable, so ties in the de-rounded time are broken by ``person_id`` and the result depends
    neither on the input table's row order nor on any dict iteration order.

    Parameters
    ----------
    first_departure_seconds:
        De-rounded first departure per person, in seconds, ORDERED BY ``person_id``.
    cells:
        ``(purpose, group)`` tuple per person, aligned element-wise with the values.
    reference:
        The ``position == "first"`` reference frame (:func:`load_departure_time_reference`).
    min_reference_n:
        Minimum ``n_unweighted`` of a rung's reference cell for that rung to be usable.
    min_model_n:
        Minimum number of model persons POOLED at a rung for that rung to be usable.

    Returns
    -------
    (offsets_seconds, cell_report):
        ``offsets_seconds`` -- the MAPPING offset per person (0 for an unmapped person; the
        caller adds the de-rounding offset).
        ``cell_report`` -- one entry per ORIGINAL ``(purpose, group)`` cell:
        ``{"n_model", "n_model_pooled", "n_reference", "level", "median_shift_min",
        "median_abs_shift_min"}``. ``n_model`` counts the cell's OWN persons, ``n_model_pooled``
        the persons ranked together at the rung it mapped at (the two are equal at
        ``purpose_group`` level); the medians are over the cell's own persons, so a caller can
        guard per cell (:func:`apply_departure_time_model` warns above ``max_median_shift_hours``)
        -- the offsets themselves are NEVER clipped here, since a large shift is a finding about
        the donor, not something to hide.
    """
    values = np.asarray(first_departure_seconds, dtype=float)
    cell_tuples = [(str(purpose), str(group)) for purpose, group in cells]
    if len(cell_tuples) != values.size:
        raise ValueError(
            "departure_time_model.quantile_map_first_departures: %d cell(s) for %d value(s); the "
            "cells must be aligned element-wise with the first departures"
            % (len(cell_tuples), values.size))
    offsets = np.zeros(values.size, dtype=float)
    report = {}
    if values.size == 0:
        return offsets, report

    reference_cells = _reference_cells(reference, source="reference frame")
    positions_by_cell = pd.DataFrame(
        {"purpose": [cell[0] for cell in cell_tuples],
         "group": [cell[1] for cell in cell_tuples]}).groupby(["purpose", "group"],
                                                              sort=True).indices

    # ---- rung 1: the person's own (purpose, group) cell
    rung_by_cell = {}                       # cell -> (level, reference key)
    pending = sorted(positions_by_cell)     # cells not yet placed on a rung
    for cell in list(pending):
        if (_rung_is_usable(reference_cells.get(cell), min_reference_n)
                and positions_by_cell[cell].size >= min_model_n):
            rung_by_cell[cell] = (LEVEL_PURPOSE_GROUP, cell)
            pending.remove(cell)

    # ---- rung 2: (purpose, "all") -- pools every still-pending group of that purpose
    by_purpose = {}
    for cell in pending:
        by_purpose.setdefault(cell[0], []).append(cell)
    for purpose, purpose_cells in sorted(by_purpose.items()):
        key = (purpose, SEGMENT_ALL)
        pooled = sum(positions_by_cell[cell].size for cell in purpose_cells)
        if _rung_is_usable(reference_cells.get(key), min_reference_n) and pooled >= min_model_n:
            for cell in purpose_cells:
                rung_by_cell[cell] = (LEVEL_PURPOSE_ALL, key)
                pending.remove(cell)

    # ---- rung 3: ("all", "all") -- pools everyone still unplaced
    pooled_key = (PURPOSE_ALL, SEGMENT_ALL)
    pooled = sum(positions_by_cell[cell].size for cell in pending)
    if _rung_is_usable(reference_cells.get(pooled_key), min_reference_n) and pooled >= min_model_n:
        for cell in list(pending):
            rung_by_cell[cell] = (LEVEL_ALL_ALL, pooled_key)
            pending.remove(cell)

    # ---- map each rung's POOLED model set in one common rank order
    cells_by_rung = {}
    for cell, rung in rung_by_cell.items():
        cells_by_rung.setdefault(rung, []).append(cell)
    n_pooled_by_cell = {}
    for (level, key), rung_cells in sorted(cells_by_rung.items()):
        rung_positions = np.sort(np.concatenate([positions_by_cell[cell]
                                                 for cell in sorted(rung_cells)]))
        shares, _n_reference = reference_cells[key]
        order = rung_positions[np.argsort(values[rung_positions], kind="stable")]
        quantiles = (np.arange(order.size) + 0.5) / order.size
        offsets[order] = _inverse_cdf(quantiles, shares) - values[order]
        for cell in rung_cells:
            n_pooled_by_cell[cell] = int(rung_positions.size)

    for cell, positions in sorted(positions_by_cell.items()):
        level, key = rung_by_cell.get(cell, (LEVEL_UNMAPPED, None))
        report[cell] = {
            "n_model": int(positions.size),
            "n_model_pooled": n_pooled_by_cell.get(cell, int(positions.size)),
            "n_reference": int(reference_cells[key][1]) if key is not None else 0,
            "level": level,
            "median_shift_min": float(np.median(offsets[positions]) / 60.0),
            "median_abs_shift_min": float(np.median(np.abs(offsets[positions])) / 60.0),
        }
    return offsets, report


# --------------------------------------------------------------------------- dispatch
def _empty_diagnostics(model: str, n_persons: int, n_trips: int) -> dict:
    """Diagnostics for a model that does no mapping (``eqasim_uniform``) -- the mapping-specific
    counts are NaN/empty rather than 0, so "not applicable" cannot be misread as "nothing was
    unmapped"."""
    return {"model": model, "n_persons": n_persons, "n_trips": n_trips, "cells": {},
            "n_persons_by_level": {}, "share_unmapped": float("nan"), "n_persons_unmapped": 0,
            "n_persons_without_first_departure": 0, "n_clipped_lower": 0, "n_clipped_upper": 0,
            "n_clip_bound_conflicts": 0, "n_cells_over_median_shift_guard": 0}


def apply_departure_time_model(table: pd.DataFrame, persons: pd.DataFrame, *, model: str,
                               random_seed: int, reference: pd.DataFrame = None,
                               min_reference_n: int = 200, min_model_n: int = 50,
                               max_median_shift_hours: float = 2.0):
    """Apply one of the three departure-time models to a trip table (see the module docstring).

    Every person's whole chain is shifted by ONE offset -- ``derounding + mapping`` for
    ``srv_mapped``, ``derounding`` alone for ``derounded`` -- clipped so that the person's first
    departure stays non-negative and their last arrival stays within
    :data:`braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS`. The offset is rounded to
    whole seconds BEFORE it is applied (so the chain shifts rigidly -- see the comment at the
    rounding), added to ``departure_time`` and ``arrival_time``, and written to
    :data:`OFFSET_COLUMN`. Row order and index of ``table`` are preserved; the table is modified
    in place AND returned (as ``apply_per_person_jitter`` does).

    Parameters
    ----------
    table:
        Trip table with ``person_id``, ``trip_index``, ``departure_time``, ``arrival_time``
        (seconds) and, for ``srv_mapped``, ``following_purpose`` (the first trip's destination
        purpose IS the mapping cell's purpose, matching the reference, which is keyed on the
        leg's destination purpose).
    persons:
        HARMONISED person attributes (``person_id``, ``age``, ``employed``) covering every person
        in ``table`` -- see :func:`persons_from_mid_schema` /
        :func:`persons_from_synthetic_schema` (ruling A-R7). Unused by ``eqasim_uniform``, which
        delegates unchanged.
    model:
        One of :data:`MODELS`.
    random_seed:
        The run's seed; the model draws from ``random_seed + DEPARTURE_TIME_SEED_OFFSET``.
    reference:
        The first-trip reference frame, REQUIRED for ``srv_mapped``
        (:func:`load_departure_time_reference`).
    min_reference_n, min_model_n:
        Thresholds of the coarsening ladder -- see :func:`quantile_map_first_departures`.
    max_median_shift_hours:
        A cell whose median |mapping shift| exceeds this is WARNED about, naming the cell and both
        its medians. The offsets are NOT clipped to it: a large shift means the donor's start
        times for that cell are far from the SrV ones, which is a finding to report, not to hide.

    Returns
    -------
    (table, diagnostics):
        ``diagnostics`` carries ``model``, ``n_persons``, ``n_trips``, the per-cell ``cells``
        report, ``n_persons_by_level`` / ``share_unmapped`` / ``n_persons_unmapped``,
        ``n_persons_without_first_departure``, ``n_clipped_lower`` / ``n_clipped_upper`` /
        ``n_clip_bound_conflicts`` and ``n_cells_over_median_shift_guard``. Mapping-specific
        entries are empty/NaN for the models that do no mapping.

    Raises
    ------
    ValueError
        If ``model`` is unknown, ``srv_mapped`` is requested without a ``reference``, a required
        column is missing, or a person in ``table`` has no attribute row in ``persons``.
    """
    if model not in MODELS:
        raise ValueError(
            "departure_time_model: unknown model %r; expected one of %s"
            % (model, list(MODELS)))

    if model == MODEL_EQASIM_UNIFORM:
        # Lazy import (ruling A-R3): trips_stage imports THIS module at module level for
        # OFFSET_COLUMN, so importing it back here at module level would be a cycle.
        from braunschweig.popsim.trips_stage import apply_per_person_jitter
        n_persons = int(table["person_id"].nunique()) if len(table) else 0
        logger.info("%s model=%s: delegating to the eqasim per-person jitter (%d trip row(s), "
                    "%d person(s)) -- unchanged behaviour", _LOG_TAG, model, len(table), n_persons)
        return apply_per_person_jitter(table, random_seed), _empty_diagnostics(
            model, n_persons, len(table))

    if model == MODEL_SRV_MAPPED and reference is None:
        raise ValueError(
            "departure_time_model: model '%s' requires a departure-time reference; pass "
            "reference=load_departure_time_reference(<data_path>/braunschweig/srv)"
            % MODEL_SRV_MAPPED)

    required = ["person_id", "trip_index", "departure_time", "arrival_time"]
    if model == MODEL_SRV_MAPPED:
        required.append("following_purpose")
    _require_columns(table, required, "trip table")
    if len(table) == 0:
        table[OFFSET_COLUMN] = np.zeros(0, dtype=float)
        logger.info("%s model=%s: empty trip table, nothing to shift", _LOG_TAG, model)
        return table, _empty_diagnostics(model, 0, 0)

    # ---- per-person view, ordered by person_id (the deterministic order of every draw below)
    positions = pd.DataFrame({
        "person_id": table["person_id"].to_numpy(),
        "trip_index": pd.to_numeric(table["trip_index"], errors="coerce").to_numpy(),
        "position": np.arange(len(table)),
    }).sort_values(["person_id", "trip_index"], kind="stable")
    first_position = positions.groupby("person_id", sort=True)["position"].first()
    person_order = first_position.index.to_numpy()
    first_position = first_position.to_numpy()
    n_persons = person_order.size

    departure = pd.to_numeric(table["departure_time"], errors="coerce").to_numpy(dtype=float)
    arrival = pd.to_numeric(table["arrival_time"], errors="coerce").to_numpy(dtype=float)
    first_departure = departure[first_position]
    # The CLIP bounds are taken from the per-person extremes over ALL rows, not from the first
    # trip_index row and its chain: a chain whose rows are not ordered by departure time (an
    # upstream defect, but one this model must not turn into a negative time) would otherwise get
    # a lower bound that leaves its true earliest departure below zero.
    earliest_departure = (pd.Series(departure).groupby(table["person_id"].to_numpy()).min()
                          .reindex(person_order).to_numpy(dtype=float))
    last_arrival = (pd.Series(arrival).groupby(table["person_id"].to_numpy()).max()
                    .reindex(person_order).to_numpy(dtype=float))

    groups = person_groups(persons).reindex(person_order)
    n_missing_attributes = int(groups.isna().sum())
    if n_missing_attributes:
        examples = groups.index[groups.isna()][:5].tolist()
        raise ValueError(
            "departure_time_model: %d/%d person(s) in the trip table have no attribute row in the "
            "persons frame (e.g. %s); the departure-time model needs age/employment for every "
            "person to pick a mapping cell" % (n_missing_attributes, n_persons, examples))

    # ---- (i) de-rounding: the only random draw of this model
    has_first_departure = np.isfinite(first_departure)
    n_without_first_departure = int((~has_first_departure).sum())
    rng = np.random.RandomState(random_seed + DEPARTURE_TIME_SEED_OFFSET)
    derounding = np.zeros(n_persons, dtype=float)
    derounding[has_first_departure] = derounding_offsets(first_departure[has_first_departure], rng)

    # ---- (ii) quantile mapping onto the SrV first-departure distribution
    mapping = np.zeros(n_persons, dtype=float)
    cell_report = {}
    n_persons_by_level = {}
    share_unmapped = float("nan")
    n_unmapped = 0
    if model == MODEL_SRV_MAPPED:
        purposes = table["following_purpose"].to_numpy()[first_position]
        cells = list(zip(purposes[has_first_departure],
                         groups.to_numpy()[has_first_departure]))
        mapped, cell_report = quantile_map_first_departures(
            first_departure[has_first_departure] + derounding[has_first_departure], cells,
            reference, min_reference_n=min_reference_n, min_model_n=min_model_n)
        mapping[has_first_departure] = mapped
        n_persons_by_level = {label: 0 for label in LEVEL_LABELS}
        for entry in cell_report.values():
            n_persons_by_level[entry["level"]] += entry["n_model"]
        # A person without a first departure could not be mapped either -- counted as unmapped so
        # share_unmapped covers every person the mapping did not reach.
        n_persons_by_level[LEVEL_UNMAPPED] += n_without_first_departure
        n_unmapped = n_persons_by_level[LEVEL_UNMAPPED]
        share_unmapped = n_unmapped / n_persons
    # The offset is rounded to whole seconds HERE, before it is applied, so that every trip of a
    # chain shifts by exactly the same integer amount: rounding each row's shifted time separately
    # would let two rows of one chain round in different directions (the mapping targets land on
    # exact half seconds, and float addition is not associative), changing that person's trip and
    # activity durations by a second -- durations are this model's hold-out dimension and must not
    # move at all. It also makes `departure_time - OFFSET_COLUMN` reproduce the pre-model time
    # exactly -- but only for WHOLE-SECOND input times: np.round is half-to-even, so for a raw time
    # with a fractional part the recovered value is the rounded raw time, not the raw time itself
    # (this pipeline's trip tables carry whole seconds, so the distinction is theoretical here).
    offset = np.round(derounding + mapping)

    # ---- guards: keep the shifted chain inside [0, MAX_PLAN_TIME_SECONDS]
    # Whole-second bounds (ceil/floor), applied AFTER the rounding above, so the clipped offset is
    # itself an integer and the bound holds exactly rather than up to half a second.
    lower_bound = np.ceil(-earliest_departure)                       # earliest departure >= 0
    upper_bound = np.floor(MAX_PLAN_TIME_SECONDS - last_arrival)     # last arrival <= plan bound
    above = np.isfinite(upper_bound) & (offset > upper_bound)
    offset = np.where(above, upper_bound, offset)
    below = np.isfinite(lower_bound) & (offset < lower_bound)
    offset = np.where(below, lower_bound, offset)
    n_clipped_upper, n_clipped_lower = int(above.sum()), int(below.sum())
    # Both bounds firing means the chain is longer than the plan bound allows even at departure 0:
    # non-negativity wins (it is asserted below), so those chains stay beyond the bound and are
    # reported rather than silently truncated.
    n_conflicts = int((above & below).sum())

    # ---- apply: one offset per person, every trip of the chain
    row_offset = (pd.Series(offset, index=pd.Index(person_order, name="person_id"))
                  .reindex(table["person_id"].to_numpy()).to_numpy(dtype=float))
    table["departure_time"] = np.round(departure + row_offset)
    table["arrival_time"] = np.round(arrival + row_offset)
    table[OFFSET_COLUMN] = row_offset           # already whole seconds -- see the rounding above

    shifted_departure = table["departure_time"].to_numpy(dtype=float)
    shifted_arrival = table["arrival_time"].to_numpy(dtype=float)
    finite = np.isfinite(shifted_departure) & np.isfinite(shifted_arrival)
    # A raise, not an assert: `python -O` strips asserts, and a negative time silently entering the
    # MATSim plans is exactly the class of defect this check exists to stop.
    n_negative_departure = int((shifted_departure[finite] < 0.0).sum())
    n_negative_arrival = int((shifted_arrival[finite] < 0.0).sum())
    if n_negative_departure or n_negative_arrival:
        raise ValueError(
            "departure_time_model: %d/%d departure time(s) and %d/%d arrival time(s) are negative "
            "after the model; the lower clip (offset >= -earliest departure per person) should "
            "make this impossible -- do not use this trip table"
            % (n_negative_departure, len(table), n_negative_arrival, len(table)))

    diagnostics = {
        "model": model, "n_persons": n_persons, "n_trips": len(table), "cells": cell_report,
        "n_persons_by_level": n_persons_by_level, "share_unmapped": share_unmapped,
        "n_persons_unmapped": n_unmapped,
        "n_persons_without_first_departure": n_without_first_departure,
        "n_clipped_lower": n_clipped_lower, "n_clipped_upper": n_clipped_upper,
        "n_clip_bound_conflicts": n_conflicts, "n_cells_over_median_shift_guard": 0,
    }
    diagnostics["n_cells_over_median_shift_guard"] = _log_diagnostics(
        diagnostics, max_median_shift_hours=max_median_shift_hours, n_finite=int(finite.sum()))
    return table, diagnostics


def _log_diagnostics(diagnostics: dict, *, max_median_shift_hours: float, n_finite: int) -> int:
    """Log every count as ``n/total (rate)`` under :data:`_LOG_TAG` and return the number of cells
    that tripped the median-shift guard (CLAUDE.md fallback transparency: a mapping that did not
    happen, or happened far too violently, must never pass silently).

    Two of the thresholds here -- :data:`UNMAPPED_SHARE_WARN_THRESHOLD` and
    :data:`COARSENED_SHARE_WARN` -- are OBSERVABILITY assumptions, not scientific bounds: they
    decide when a run's ladder behaviour is loud enough to look at, and nothing else. Only
    ``max_median_shift_hours`` is a caller-configured parameter."""
    n_persons = diagnostics["n_persons"]
    model = diagnostics["model"]
    logger.info("%s model=%s: %d person(s), %d trip row(s) shifted", _LOG_TAG, model, n_persons,
                diagnostics["n_trips"])

    levels = ""
    if diagnostics["n_persons_by_level"]:
        levels = ", ".join(
            "%s %d/%d (%.1f%%)" % (label, diagnostics["n_persons_by_level"][label], n_persons,
                                   100.0 * diagnostics["n_persons_by_level"][label] / n_persons)
            for label in LEVEL_LABELS)
        logger.info("%s mapping level: %s", _LOG_TAG, levels)
        n_coarsened = sum(diagnostics["n_persons_by_level"][label]
                          for label in (LEVEL_PURPOSE_ALL, LEVEL_ALL_ALL, LEVEL_UNMAPPED))
        if n_coarsened / n_persons > COARSENED_SHARE_WARN:
            logger.warning(
                "%s %d/%d person(s) (%.1f%%) were mapped BELOW their own (purpose, group) cell -- "
                "above the %.0f%% threshold. Their start times are calibrated against a POOLED "
                "reference (or not at all), which is a weaker claim than a per-cell calibration; "
                "level split: %s", _LOG_TAG, n_coarsened, n_persons,
                100.0 * n_coarsened / n_persons, 100.0 * COARSENED_SHARE_WARN, levels)
    if diagnostics["n_persons_without_first_departure"]:
        logger.warning(
            "%s %d/%d person(s) (%.2f%%) have no first departure and were left unshifted; a "
            "trip table should not contain a person without a departure time -- check upstream",
            _LOG_TAG, diagnostics["n_persons_without_first_departure"], n_persons,
            100.0 * diagnostics["n_persons_without_first_departure"] / n_persons)
    share_unmapped = diagnostics["share_unmapped"]
    if np.isfinite(share_unmapped) and share_unmapped > UNMAPPED_SHARE_WARN_THRESHOLD:
        logger.warning(
            "%s %d/%d person(s) (%.1f%%) stayed unmapped -- above the %.0f%% threshold. Those "
            "persons keep the donor's own first departure (de-rounded only), so the SrV start-time "
            "calibration did NOT happen for them; check min_reference_n / min_model_n and the "
            "reference's cell coverage", _LOG_TAG, diagnostics["n_persons_unmapped"], n_persons,
            100.0 * share_unmapped, 100.0 * UNMAPPED_SHARE_WARN_THRESHOLD)

    n_over_guard = 0
    for (purpose, group), entry in sorted(diagnostics["cells"].items()):
        logger.info("%s cell purpose=%s group=%s: level=%s n_model=%d (pooled at that rung %d) "
                    "n_reference=%d median shift %.1f min (median |shift| %.1f min)", _LOG_TAG,
                    purpose, group, entry["level"], entry["n_model"], entry["n_model_pooled"],
                    entry["n_reference"], entry["median_shift_min"], entry["median_abs_shift_min"])
        if entry["level"] == LEVEL_UNMAPPED:
            continue
        if entry["median_abs_shift_min"] > max_median_shift_hours * 60.0:
            n_over_guard += 1
            logger.warning(
                "%s cell purpose=%s group=%s: median |mapping shift| %.1f min (signed median "
                "%.1f min) exceeds max_median_shift_hours=%.2f h (%.0f min) over %d person(s) "
                "mapped at level %s onto %d reference observation(s). The offsets are NOT clipped "
                "to the guard: such a shift means the donor's start times for this cell are far "
                "from the SrV ones -- inspect the cell before trusting the run",
                _LOG_TAG, purpose, group, entry["median_abs_shift_min"], entry["median_shift_min"],
                max_median_shift_hours, max_median_shift_hours * 60.0, entry["n_model"],
                entry["level"], entry["n_reference"])

    n_trips = diagnostics["n_trips"]
    logger.info("%s clipped to [0, %d s]: lower %d/%d (%.2f%%), upper %d/%d (%.2f%%)", _LOG_TAG,
                MAX_PLAN_TIME_SECONDS, diagnostics["n_clipped_lower"], n_persons,
                100.0 * diagnostics["n_clipped_lower"] / n_persons,
                diagnostics["n_clipped_upper"], n_persons,
                100.0 * diagnostics["n_clipped_upper"] / n_persons)
    if diagnostics["n_clip_bound_conflicts"]:
        logger.warning(
            "%s %d/%d person(s) have a chain longer than the plan bound allows even at departure "
            "0; the non-negativity clip won and their last arrival stays beyond %d s (a "
            "pre-existing chain-length problem, not one this model introduced)", _LOG_TAG,
            diagnostics["n_clip_bound_conflicts"], n_persons, MAX_PLAN_TIME_SECONDS)
    if n_finite < n_trips:
        logger.warning("%s %d/%d trip row(s) carry a non-finite departure or arrival time after "
                       "the model; they were left as they were", _LOG_TAG, n_trips - n_finite,
                       n_trips)
    return n_over_guard
