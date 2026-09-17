"""Reporting-day plan replacement (ADR-0104, issue #244, Phase B Task 3; issue #370, Task 4).

Turns a ``commute_day_state`` draw (:mod:`state`) and a donor match
(:mod:`matching`) into the actual reporting-day trips table, one row per (person, trip) in the
``synthesis.population.trips`` CONTRACT (:data:`braunschweig.popsim.trips_stage.CONTRACT`). Pure
module: no file I/O, no synpp stage -- exercised only against tiny synthetic frames in
``tests/test_commute_day_plan_replacement.py``.

Since issue #370 (Task 4) it ALSO composes the general day-absence draw
(:mod:`braunschweig.synthesis.day_absence.absence`) on top of the commute-day state: a person
generally absent from the reporting day (``day_absence_state`` other than "present") loses their
rows exactly like a commute-day ``absent`` person, whether or not they even have a commute-day
state at all (a non-worker can be generally absent too). The two absence reasons are counted
separately (``n_persons_absent_commute`` / ``n_persons_absent_general`` / ``_both`` / ``_total`` in
``diagnostics``, see :func:`build_day_trips`) so a run can report the overlap rather than only the
union.

Per person, by ``commute_day_state``:

* ``absent`` -- NO rows at all (the person makes no trips on the reporting day).
* ``home`` WITH a donor match -- every one of the person's original trip rows is dropped and
  replaced by the donor's own trip chain (:func:`donor_pool.donor_trips`), with ``person_id`` set
  to the RECEIVING person, ``trip_index`` renumbered ``0..n-1`` in the donor's own order, and
  ``is_first_trip`` / ``is_last_trip`` recomputed from that renumbering. ``trip_key`` is copied
  VERBATIM from the donor's own row: it therefore still names the DONOR, not the receiving
  person, and is no longer unique once a donor is reused across several persons -- it remains
  useful purely for tracing a replaced row back to its donor trip, never as a per-row identifier;
  no downstream code in this pipeline relies on it for anything but within-person ordering
  (``trip_index`` is authoritative for that). Any column the input
  ``trips`` table carries beyond the CONTRACT plus ``euclidean_distance`` and ``trip_key`` (raw
  MiD extras the donor pool intentionally does not carry, see ``donor_pool.donor_trips``) is set
  to ``NaN`` on the replaced rows -- there is no donor-side value to copy, and inventing one would
  violate CLAUDE.md's ban on invented data -- EXCEPT the columns in :data:`_RECOMPUTED_COLUMNS`
  (currently only :data:`braunschweig.popsim.departure_time_model.OFFSET_COLUMN`), which the
  per-person jitter below RECOMPUTES for the receiving person a few lines later and which must
  therefore never be nulled in between (ruling A-R8, issue #123 review fix round 1: nulling then
  immediately overwriting inflated ``n_extra_columns_nulled`` by one without ever producing an
  observable null). The departure-time model
  (:func:`braunschweig.popsim.departure_time_model.apply_departure_time_model`, issue #123 Task
  4 -- by default the unchanged per-person jitter
  :func:`braunschweig.popsim.trips_stage.apply_per_person_jitter`) is applied EXACTLY ONCE,
  across all replaced rows together, keyed by the RECEIVING person_id (ruling R2) -- the donor's
  own chain was built by :func:`donor_pool.donor_trips` WITHOUT that jitter for precisely this
  reason (applying it twice would double-jitter the same donor day once copied onto a person).
  Which model runs is selected by the OPTIONAL ``departure_time`` argument of
  :func:`build_day_trips` (a :class:`DepartureTimeSettings`); with ``None`` the replaced rows
  get today's jitter byte-identically.
* ``home`` WITHOUT a donor match (:func:`matching.match_home_office_donors` found no replaceable
  cell) -- the person's original rows are kept UNCHANGED and counted in
  ``diagnostics["n_home_unmatched"]``; ADR-0104 leaves it to the state stage (a later Phase B
  task) to decide how to treat them (downgrading to ``at_workplace`` is the documented default).
* ``at_workplace``, or any person absent from ``states`` altogether (e.g. a non-worker who was
  never given a commute-day state) -- rows UNCHANGED.

The output is sorted by ``(person_id, trip_index)` with the CONTRACT columns first, followed by
the input ``trips`` table's remaining columns in their original relative order.
"""
from __future__ import annotations

import dataclasses
import logging

import numpy as np
import pandas as pd

from braunschweig.popsim import departure_time_model as _departure_time_model
from braunschweig.popsim.departure_time_model import (MODEL_EQASIM_UNIFORM, OFFSET_COLUMN,
                                                      apply_departure_time_model,
                                                      persons_from_synthetic_schema)
from braunschweig.popsim.trips_stage import CONTRACT, apply_per_person_jitter

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day plan replacement]"

#: Extra columns a donor trips frame is documented to carry beyond the CONTRACT (see
#: ``donor_pool.donor_trips``): copied verbatim onto replaced rows, never nulled.
_DONOR_EXTRA_COLUMNS = ("euclidean_distance", "trip_key")

#: Columns that ``apply_per_person_jitter`` (and, from Task 4 onward, ``apply_departure_time_model``)
#: RECOMPUTES on the replaced rows from the RECEIVING person's own random draw, regardless of
#: whatever value the donor's own chain carried for them -- so, unlike a genuine "no donor-side
#: value" extra column, nulling them first only to have that jitter/model call overwrite them a
#: few lines later would inflate ``n_extra_columns_nulled`` by one for a null that is never
#: actually observable in the output (ruling A-R8, issue #123 review fix round 1). Declared as an
#: explicit tuple -- not derived from ``apply_per_person_jitter``'s signature -- so Task 4's
#: ``apply_departure_time_model`` can extend it with its own recomputed columns without touching
#: the nulling logic below; keep it generic (not a single hard-coded name check) for that reason.
_RECOMPUTED_COLUMNS = (OFFSET_COLUMN,)

#: Share of matched donors with zero rows in ``donor_trips`` -- EXCLUDING the donors the donor
#: pool flags ``is_immobile`` (ruling R9) -- above which the replacement warns: what remains after
#: that split can no longer be explained by the donor pool's own immobility and points at a
#: dropped chain or a ``donor_id`` key/dtype mismatch.
WARN_DONORS_WITHOUT_TRIPS_SHARE = 0.01

STATE_ABSENT = "absent"
STATE_HOME = "home"
STATE_AT_WORKPLACE = "at_workplace"

#: The "present" value of a ``general_absence`` frame's ``day_absence_state`` column, i.e.
#: ``braunschweig.synthesis.day_absence.absence.STATE_PRESENT`` duplicated as a local constant.
#: Importing it directly would create a cross-package import from ``commute_day`` to
#: ``day_absence`` (issue #370, Task 4), which this module intentionally avoids; the two
#: constants are pinned equal by ``tests/test_day_absence.py``.
STATE_PRESENT_GENERAL = "present"

#: Escort-leg purpose, matching ``following_purpose`` / ``preceding_purpose`` of the CONTRACT
#: (issue #370 final-review fix wave, ruling R10, spec 2.1 point 4). Kept as a local literal
#: rather than importing ``braunschweig.synthesis.commute_day.state_stage.ESCORT_PURPOSE`` for the
#: same cross-module-avoidance reason as :data:`STATE_PRESENT_GENERAL` above.
ESCORT_PURPOSE = "escort"

#: Upper (inclusive) age in years counted as a "child" for :func:`build_day_trips`'s
#: ``n_children_with_absent_escorter`` diagnostic (spec 2026-09-09-general-day-absence-design.md
#: section 2.1 point 4).
CHILD_MAX_AGE_YEARS = 17


@dataclasses.dataclass(frozen=True)
class DepartureTimeSettings:
    """Everything :func:`build_day_trips` needs to run a departure-time model on replaced rows.

    Grouped into one FROZEN object rather than a row of loose keyword arguments because the values
    are only ever meaningful together: the model name decides whether the reference, the ranking
    context and the three thresholds are read at all, and a caller that passed all but one of them
    would be configuring a model that silently used a default for the one it forgot. Frozen so the
    settings a run logs are provably the settings it applied.

    Attributes
    ----------
    model:
        One of :data:`braunschweig.popsim.departure_time_model.MODELS`.
    reference:
        The ``position == "first"`` SrV reference frame, REQUIRED for ``"srv_mapped"`` and
        ``None`` otherwise (:func:`~braunschweig.popsim.departure_time_model.load_departure_time_reference`).
    min_reference_n, min_model_n:
        Thresholds of the ``"srv_mapped"`` coarsening ladder (unweighted reference observations
        per cell / model persons pooled at a rung).
    max_median_shift_hours:
        Median-|shift| warning threshold per mapping cell, in HOURS.
    persons:
        The RECEIVING population's attributes (``person_id``, ``age``, ``employed``) -- the
        synthetic-population schema, adapted by
        :func:`~braunschweig.popsim.departure_time_model.persons_from_synthetic_schema`. Ruling
        A-R7: a spliced donor day is calibrated against the cell of the person who EXECUTES it,
        never the donor's own, so this frame must describe the receiving persons.
    ranking_context:
        The POPULATION's raw first departures per mapping cell (ruling A-R18,
        :func:`~braunschweig.popsim.departure_time_model.build_ranking_context`), REQUIRED in
        practice for ``"srv_mapped"`` here and ``None`` for every other model (the dispatch
        rejects a context a model would ignore). The spliced set is only the replaced
        home-office persons, so without this base a quantile would be computed among a handful of
        them and thin cells would coarsen at small scales although the population has thousands
        of persons in the same cell. ``None`` with ``"srv_mapped"`` is still accepted and means
        "rank the replaced persons among themselves" -- the pre-A-R18 behaviour, and the reason
        the realised ranking base is logged rather than assumed.
    """

    model: str = MODEL_EQASIM_UNIFORM
    reference: pd.DataFrame = None
    min_reference_n: int = _departure_time_model.DEFAULT_MIN_REFERENCE_N
    min_model_n: int = _departure_time_model.DEFAULT_MIN_MODEL_N
    max_median_shift_hours: float = _departure_time_model.DEFAULT_MAX_MEDIAN_SHIFT_HOURS
    persons: pd.DataFrame = None
    ranking_context: pd.DataFrame = None


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def _replaced_rows(donor_blocks, person_ids, other_extra_columns):
    """Assemble every replaced person's donor block into ONE frame, without column-wise inserts.

    ``donor_blocks`` is a list of per-person donor trip frames (already ordered by
    ``trip_index``), ``person_ids`` the receiving person of each block, in the same order.

    The naive shape -- copy each donor block, then write ``person_id``, ``trip_index``,
    ``is_first_trip``, ``is_last_trip`` and every extra column into it one at a time -- performs
    ``len(donor_blocks) * (4 + len(other_extra_columns))`` single-column inserts. On the 100 %
    proof run of 2026-09-05 that was 657,888 inserts, each one emitting a pandas
    ``PerformanceWarning: DataFrame is highly fragmented``, which alone made the run log 254 MB.
    Here the blocks are concatenated ONCE, the four recomputed columns are written on the single
    combined frame (four inserts in total, not four per person), and the NaN extras are built as
    one block and joined with a single ``concat`` -- so the number of insert operations no longer
    grows with the number of replaced persons.
    """
    replaced = pd.concat(donor_blocks, ignore_index=True)
    lengths = np.fromiter((len(block) for block in donor_blocks), dtype=int,
                          count=len(donor_blocks))
    trip_index = np.concatenate([np.arange(length) for length in lengths]) if len(lengths) else \
        np.empty(0, dtype=int)
    # The LAST trip of each block, i.e. the block's own length - 1 repeated over the block; a
    # zero-length block contributes nothing, so max(n-1, 0) never has to be special-cased.
    last_index = np.repeat(np.maximum(lengths - 1, 0), lengths)
    replaced["person_id"] = np.repeat(np.asarray(person_ids), lengths)
    replaced["trip_index"] = trip_index
    replaced["is_first_trip"] = trip_index == 0
    replaced["is_last_trip"] = trip_index == last_index
    if other_extra_columns:
        # One NaN block for every extra column at once (see the docstring): there is no
        # donor-side value to copy and inventing one would violate CLAUDE.md's ban on invented
        # data, so they are nulled -- but nulled in a single operation.
        extras = pd.DataFrame(np.nan, index=replaced.index, columns=list(other_extra_columns))
        replaced = pd.concat([replaced, extras], axis=1)
    return replaced


def _apply_departure_time(replaced: pd.DataFrame, *, random_seed: int,
                          settings: DepartureTimeSettings):
    """Apply the configured start-time model to the replaced rows and report what it did.

    With ``settings=None`` this is exactly today's ``apply_per_person_jitter`` call, so the
    default path is byte-identical; with settings the model runs on the RECEIVING persons
    restricted to the replaced set -- restricted because those, and only those, are the persons
    whose day this call SHIFTS.

    The distribution they are RANKED IN is a separate question, and the answer is
    ``settings.ranking_context`` (ruling A-R18): the population's raw first departures per mapping
    cell, which the model ranks the replaced persons against without ever mapping the context
    itself. Ranking them among each other instead -- the pre-A-R18 behaviour, still reachable with
    ``ranking_context=None`` -- makes a spliced person's quantile depend on how many other
    home-office persons happen to share their cell in this run: at a smoke or a 1 % scale most
    cells then coarsen to ``all_all`` or stay unmapped although the population has thousands of
    persons in the same cell, and a rank among a handful is not a meaningful quantile anyway.

    Returns ``(replaced, diagnostics)``; ``diagnostics`` is ``None`` on the jitter path (no model
    ran, and a substituted empty report would read as "the model found nothing to do").
    """
    if settings is None:
        return apply_per_person_jitter(replaced, random_seed), None
    model_persons = None
    if settings.model != MODEL_EQASIM_UNIFORM:
        if settings.persons is None:
            raise ValueError(
                f"{_LOG_TAG} departure_time.model={settings.model!r} needs the RECEIVING "
                "persons' attributes (person_id, age, employed) to pick a mapping cell, but "
                "DepartureTimeSettings.persons is None (ruling A-R7).")
        model_persons = persons_from_synthetic_schema(settings.persons)
        model_persons = model_persons[
            model_persons["person_id"].isin(replaced["person_id"].unique())]
    replaced, diagnostics = apply_departure_time_model(
        replaced, model_persons,
        model=settings.model, random_seed=random_seed, reference=settings.reference,
        min_reference_n=int(settings.min_reference_n),
        min_model_n=int(settings.min_model_n),
        max_median_shift_hours=float(settings.max_median_shift_hours),
        ranking_context=settings.ranking_context)
    # format_level_split already carries the `unmapped n/total (rate)` term, so it is not
    # repeated here (same wording as trips_stage._log_departure_time_model's line); the ranking
    # base is named next to it (ruling A-R18) because the level split alone no longer says what a
    # quantile was computed in.
    logger.info(
        "%s departure-time model on the replaced rows: %s -- %d person(s), %d trip row(s); "
        "mapping level: %s; ranking base from %s", _LOG_TAG, diagnostics["model"],
        diagnostics["n_persons"], diagnostics["n_trips"],
        _departure_time_model.format_level_split(diagnostics),
        _departure_time_model.format_ranking_base(diagnostics))
    return replaced, diagnostics


def build_day_trips(trips: pd.DataFrame, states: pd.DataFrame, matches: pd.DataFrame,
                    donor_trips: pd.DataFrame, *, random_seed: int,
                    donor_attributes: pd.DataFrame = None,
                    general_absence: pd.DataFrame = None,
                    persons: pd.DataFrame = None,
                    departure_time: DepartureTimeSettings = None) -> tuple[pd.DataFrame, dict]:
    """Build the reporting-day trips table from a state draw and a donor match.

    ``trips`` -- the pre-assignment ``synthesis.population.trips`` table (CONTRACT columns plus
    any extras), one row per (person, trip), for the WHOLE population (workers and non-workers
    alike -- persons absent from ``states`` pass through unchanged, see the module docstring).
    ``states`` -- :func:`state.draw_states` output: needs ``person_id``, ``commute_day_state``.
    ``matches`` -- :func:`matching.match_home_office_donors` output: needs ``person_id``,
    ``donor_id`` (one row per REPLACEABLE ``home`` person; an unreplaceable one is simply
    absent). ``donor_trips`` -- :func:`donor_pool.donor_trips` output: needs ``donor_id`` plus
    the CONTRACT columns (minus ``person_id``) and ``euclidean_distance`` / ``trip_key``.
    ``donor_attributes`` -- the donor pool's attributes frame (``donor_id``, ``is_immobile``);
    OPTIONAL only so the pure-helper tests can call this function without one, and always passed
    by ``trips_day_stage``. Without it every donor absent from ``donor_trips`` is counted as
    ``n_donors_without_trips``, as before ruling R9.

    ``general_absence`` -- OPTIONAL (issue #370, Task 4):
    :func:`braunschweig.synthesis.day_absence.absence_stage` output's ``"absence"`` frame, needing
    ``person_id`` and ``day_absence_state``; every person whose state is not
    :data:`STATE_PRESENT_GENERAL` is treated as absent from the reporting day next to the
    commute-day ``absent`` persons, and their rows are removed the same way. A generally absent
    person who is ALSO a matched ``home`` person does NOT receive a spliced donor day -- they are
    excluded from the matched set BEFORE the splice loop runs, so no donor rows are ever built for
    them, and are counted under ``n_persons_absent_general`` (not ``n_persons_replaced``). With
    ``general_absence=None`` (the default) this function behaves EXACTLY as before Task 4: no
    person is generally absent and every new diagnostics key reports the pre-Task-4 value (0 for
    the general-only counters, the ``n_persons_absent`` value for ``n_persons_absent_commute`` /
    ``n_persons_absent_total``).

    ``persons`` -- OPTIONAL (issue #370 final-review fix wave, ruling R10, spec 2.1 point 4):
    the enriched population, needing ``person_id``, ``household_id``, ``age``. It feeds ONLY
    ``n_children_with_absent_escorter`` below; every other diagnostic and the returned
    ``day_trips`` frame are unaffected by whether it is given. With ``persons=None`` (the
    default) that one diagnostic is ``None`` and is logged as "not computed" rather than a
    substituted 0, so a caller can tell "nobody" apart from "not measured".

    ``departure_time`` -- OPTIONAL (issue #123, Task 4, ADR-0114): a
    :class:`DepartureTimeSettings` selecting the START-TIME model applied to the REPLACED rows.
    With ``None`` (the default) the replaced rows get today's eqasim per-person jitter
    byte-identically; with settings they go through
    :func:`braunschweig.popsim.departure_time_model.apply_departure_time_model` using the
    RECEIVING persons' attributes (ruling A-R7), so a spliced donor day starts where the SrV
    distribution of the person who executes it says -- not where the donor's diary happened to
    start. The settings' ``ranking_context`` (ruling A-R18) is the POPULATION distribution the
    replaced persons' quantiles are computed in, so a thin spliced set no longer coarsens the
    mapping; see :func:`_apply_departure_time`. Either way the model runs EXACTLY ONCE, on the
    replaced rows only (ruling R2). The
    model's own diagnostics are returned under the ``"departure_time"`` key and logged with the
    per-level split, so a run can state which model produced its reporting day.

    Returns ``(day_trips, diagnostics)``. ``diagnostics``: ``n_persons_replaced`` (``home``
    persons with a donor match, general absence excluded -- including a donor whose own day has
    ZERO trips, i.e. a fully immobile home-office day: that person legitimately ends up with no
    rows, which is the correct outcome, not an error), ``n_persons_absent`` (commute-absent
    persons; kept for backward compatibility, identical to ``n_persons_absent_commute``),
    ``n_persons_absent_commute``, ``n_persons_absent_general``, ``n_persons_absent_both`` (the
    overlap of the two), ``n_persons_absent_total`` (the union), ``n_trips_removed`` (original
    rows dropped, for both replaced and absent persons), ``n_trips_removed_general`` (the subset
    of those rows removed ONLY because of general absence, i.e. excluding persons already
    commute-absent, so the two removal reasons are never double-counted), ``n_trips_added``
    (donor rows spliced in), ``n_home_unmatched``, ``n_extra_columns_nulled`` (count of DISTINCT
    extra input columns nulled on replaced rows, see the module docstring; columns in
    :data:`_RECOMPUTED_COLUMNS` -- e.g. ``OFFSET_COLUMN`` -- are EXCLUDED from both the nulling
    and this count, because the per-person jitter/model recomputes them for the receiving person
    rather than leaving them null, ruling A-R8), and -- ruling R9 -- the SPLIT of the matched
    persons whose ``donor_id`` has no rows at all in ``donor_trips``:

    * ``n_donors_immobile`` / ``share_donors_immobile`` -- the donor pool flags the donor
      ``is_immobile`` (no row in the raw MiD Wege file at all), an immobile home-office day.
      EXPECTED (the MiD donor pool is 32.5 % immobile by construction), reported at info level,
      and the person's trip-less day is the correct outcome.
    * ``n_donors_without_trips`` / ``share_donors_without_trips`` -- the donor DID travel (or is
      absent from ``donor_attributes``, i.e. unknown) yet none of its trips arrived here. Two
      causes, neither of them benign: the donor's chain was dropped by the repair/resample cascade
      (the pool's own ``n_chain_dropped_by_resample``, a resample gap rather than real behaviour)
      or a ``donor_id`` key/dtype mismatch between ``matches`` and ``donor_trips`` silently wiped
      it. This rate -- and only this one -- warns above
      :data:`WARN_DONORS_WITHOUT_TRIPS_SHARE`.
    * ``n_donors_unknown_trip_count`` -- how many of the latter were the "absent from
      ``donor_attributes``" case (always 0 when no attributes frame is given).

    Escort-coherence diagnostics (ruling R10, spec 2.1 point 4):

    * ``n_absent_with_escort_leg`` -- of the ABSENT persons (commute- or generally-absent, i.e.
      the union counted in ``n_persons_absent_total``), how many carry an escort leg
      (``following_purpose`` or ``preceding_purpose`` == :data:`ESCORT_PURPOSE`) on their
      ORIGINAL ``trips`` rows. Always computed (no optional input needed): a matched ``home``
      person's original rows are read from ``trips``, not from the donor chain that replaces
      them, exactly like every other person.
    * ``n_children_with_absent_escorter`` -- a HOUSEHOLD-LEVEL PROXY for "this child's escorting
      adult is generally or commute-absent", ``None`` when ``persons`` is not given. The
      chainsolver link between an escort leg and the specific child it escorts does not exist at
      this stage (this module runs on the pre-assignment trips table, long before any chain is
      solved), so the closest available signal is: a person aged <=
      :data:`CHILD_MAX_AGE_YEARS` who is PRESENT (not in the union of absent persons) and lives
      in a household with at least one ABSENT member counted in ``n_absent_with_escort_leg``
      above. This can both over-count (several children in one household, only one of whom is
      actually escorted) and under-count (an escorted child living with a non-relative), but it
      is the only signal observable without the chain solver.

    Raises ``ValueError`` if ``matches["person_id"]`` contains duplicates -- each person can have
    at most one donor match; a duplicate would make the replacement for that person ambiguous.
    """
    _require_columns(trips, ("person_id", "trip_index", "preceding_purpose", "following_purpose"),
                     "trips frame")
    _require_columns(states, ("person_id", "commute_day_state"), "states frame")
    _require_columns(matches, ("person_id", "donor_id"), "matches frame")
    _require_columns(donor_trips, ("donor_id",) + tuple(c for c in CONTRACT if c != "person_id"),
                     "donor_trips frame")
    general_absent_persons = set()
    if general_absence is not None:
        _require_columns(general_absence, ("person_id", "day_absence_state"), "general_absence frame")
        general_absent_persons = set(
            general_absence.loc[general_absence["day_absence_state"] != STATE_PRESENT_GENERAL, "person_id"])
    n_duplicated_matches = int(matches["person_id"].duplicated().sum())
    if n_duplicated_matches > 0:
        raise ValueError(
            f"{_LOG_TAG} matches frame has {n_duplicated_matches} duplicate person_id value(s); "
            "each person must have at most one donor match "
            f"(duplicated: {sorted(matches.loc[matches['person_id'].duplicated(), 'person_id'].unique())}).")

    state_by_person = states.set_index("person_id")["commute_day_state"]
    donor_by_person = matches.set_index("person_id")["donor_id"]

    input_columns = list(trips.columns)
    # _RECOMPUTED_COLUMNS is excluded here (not just from the nulling below): those columns are
    # never a "no donor-side value" gap in the first place, since apply_per_person_jitter
    # overwrites them for every replaced row a few lines below regardless of what NaN-filling
    # would have written (ruling A-R8).
    known_columns = set(CONTRACT) | set(_DONOR_EXTRA_COLUMNS) | set(_RECOMPUTED_COLUMNS)
    other_extra_columns = [c for c in input_columns if c not in known_columns]
    output_columns = list(CONTRACT) + [c for c in input_columns if c not in CONTRACT]

    commute_absent_persons = set(state_by_person.index[state_by_person == STATE_ABSENT])
    home_persons = set(state_by_person.index[state_by_person == STATE_HOME])
    matched_home_persons_by_donor = home_persons & set(donor_by_person.index)
    # Ruling (issue #370, Task 4): a generally absent person must not receive a spliced donor day
    # even if the state draw matched them to one -- they are removed like any other absent person,
    # not replaced. Excluding them from the matched set BEFORE the splice loop below (rather than
    # splicing then discarding) means no donor block is ever built for them, and they are counted
    # once, under n_persons_absent_general. The SAME exclusion applies to the unmatched-home set
    # right below: a home person WITHOUT a donor who is ALSO generally absent has their rows
    # removed like any other absent person, not kept unchanged, so they must not be counted in
    # n_home_unmatched -- otherwise the "keep their ORIGINAL day unchanged" warning below would
    # misdescribe their actual outcome (their rows are in fact gone).
    matched_home_persons = matched_home_persons_by_donor - general_absent_persons
    unmatched_home_persons = home_persons - matched_home_persons_by_donor - general_absent_persons

    absent_persons = commute_absent_persons | general_absent_persons
    n_persons_absent = len(commute_absent_persons)  # unchanged meaning: commute-absent persons.
    n_home_unmatched = len(unmatched_home_persons)

    # Rows dropped entirely: absent persons (commute- or generally-absent, no rows at all) and
    # matched home persons (replaced below). Everyone else -- at_workplace, unmatched-home, and
    # persons never given a state at all -- keeps their original rows completely unchanged.
    persons_to_remove = absent_persons | matched_home_persons
    kept_rows = trips.loc[~trips["person_id"].isin(persons_to_remove)].copy()

    n_trips_removed = int(trips["person_id"].isin(persons_to_remove).sum())
    # Trips removed BECAUSE OF general absence specifically: persons already counted under the
    # commute-absent removal are excluded here, so the two removal reasons are never double-counted.
    n_trips_removed_general = int(
        trips["person_id"].isin(general_absent_persons - commute_absent_persons).sum())

    donor_groups = {donor_id: group.sort_values("trip_index").reset_index(drop=True)
                    for donor_id, group in donor_trips.groupby("donor_id", sort=False)}

    # Ruling R9: an absent donor is only an ERROR when that donor is not IMMOBILE. The decider is
    # the donor pool's own is_immobile flag (no row in the raw MiD Wege file at all, see
    # donor_pool.attach_trip_derived_attributes) and NOT n_trips == 0, which is also true of a
    # donor whose chain the repair/resample cascade dropped -- a resample gap, not real behaviour,
    # that the pool counts separately as n_chain_dropped_by_resample and that must not be excused
    # here. The pool is 32.5 % immobile BY CONSTRUCTION, which made the conflated rate (27.3 % on
    # the 2026-09-05 proof run) unreadable as a defect signal.
    donor_is_immobile = None
    if donor_attributes is not None:
        _require_columns(donor_attributes, ("donor_id", "is_immobile"), "donor_attributes frame")
        donor_is_immobile = donor_attributes.set_index("donor_id")["is_immobile"]

    donor_blocks = []
    replaced_person_ids = []
    n_donors_without_trips = 0
    n_donors_immobile = 0
    n_donors_unknown_trip_count = 0
    for person_id in sorted(matched_home_persons):
        donor_id = donor_by_person.loc[person_id]
        donor_rows = donor_groups.get(donor_id)
        if donor_rows is None:
            # The donor has no rows at all in donor_trips. With the donor attributes at hand this
            # splits into the EXPECTED case (is_immobile: a fully immobile home-office day, so the
            # person legitimately gets a trip-less day) and the SUSPICIOUS one (the donor DID
            # travel, so either its chain was dropped by the resample or a donor_id key/dtype
            # mismatch between matches and donor_trips wiped it). A donor missing from the
            # attributes frame entirely is counted as unknown and treated as suspicious -- an
            # unknown must never be read as the benign case.
            is_immobile = None
            if donor_is_immobile is not None:
                raw_is_immobile = donor_is_immobile.get(donor_id)
                if raw_is_immobile is None or pd.isna(raw_is_immobile):
                    n_donors_unknown_trip_count += 1
                else:
                    is_immobile = bool(raw_is_immobile)
            if is_immobile:
                n_donors_immobile += 1
            else:
                n_donors_without_trips += 1
            continue
        donor_blocks.append(donor_rows)
        replaced_person_ids.append(person_id)

    n_trips_added = sum(len(block) for block in donor_blocks)
    n_extra_columns_nulled = len(other_extra_columns) if donor_blocks else 0

    departure_time_diagnostics = None
    if donor_blocks:
        replaced = _replaced_rows(donor_blocks, replaced_person_ids, other_extra_columns)
        replaced = replaced.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
        # Ruling R2: the start-time model is applied EXACTLY ONCE here, on the replaced rows
        # only, keyed by the RECEIVING person_id -- never on the donor's own chain (donor_pool
        # deliberately omits it) and never on untouched rows.
        replaced, departure_time_diagnostics = _apply_departure_time(
            replaced, random_seed=random_seed, settings=departure_time)
        result = pd.concat([kept_rows, replaced], ignore_index=True, sort=False)
    else:
        # No rows to splice in at all (no home persons, no matches, or every matched donor was
        # trip-less) -- concatenating with an empty pd.DataFrame(columns=...) placeholder would
        # upcast every CONTRACT column (trip_index, is_first_trip, is_last_trip, trip_duration,
        # ...) to object, silently corrupting the dtypes of every UNTOUCHED row as a side effect.
        # kept_rows alone already carries every row and every original dtype, so it is used as-is.
        result = kept_rows

    result = result[output_columns]
    result = result.sort_values(["person_id", "trip_index"]).reset_index(drop=True)

    n_matched = len(matched_home_persons)
    share_donors_without_trips = n_donors_without_trips / max(n_matched, 1)
    share_donors_immobile = n_donors_immobile / max(n_matched, 1)

    # Ruling R10 (final-review fix wave, spec 2.1 point 4): escort-coherence diagnostics. Both
    # counts are read from the ORIGINAL trips table, not from the (possibly already-removed)
    # output rows -- an absent person's escort leg still existed before their day was cleared.
    escort_leg_mask = ((trips["preceding_purpose"] == ESCORT_PURPOSE)
                       | (trips["following_purpose"] == ESCORT_PURPOSE))
    persons_with_escort_leg = set(trips.loc[escort_leg_mask, "person_id"])
    absent_persons_with_escort_leg = absent_persons & persons_with_escort_leg
    n_absent_with_escort_leg = len(absent_persons_with_escort_leg)
    n_absent_total = len(absent_persons)
    share_absent_with_escort_leg = n_absent_with_escort_leg / max(n_absent_total, 1)
    logger.info(
        "%s n_absent_with_escort_leg: %d/%d (%.2f%%) absent person(s) (commute- or "
        "generally-absent) carry an escort leg on their original trips", _LOG_TAG,
        n_absent_with_escort_leg, n_absent_total, 100.0 * share_absent_with_escort_leg)

    n_children_with_absent_escorter = None
    if persons is None:
        logger.info("%s n_children_with_absent_escorter: not computed (no persons frame given)",
                    _LOG_TAG)
    else:
        _require_columns(persons, ("person_id", "household_id", "age"), "persons frame")
        households_with_absent_escorter = set(
            persons.loc[persons["person_id"].isin(absent_persons_with_escort_leg), "household_id"])
        is_child = persons["age"] <= CHILD_MAX_AGE_YEARS
        is_present = ~persons["person_id"].isin(absent_persons)
        lives_with_absent_escorter = persons["household_id"].isin(households_with_absent_escorter)
        n_children_with_absent_escorter = int(
            (is_child & is_present & lives_with_absent_escorter).sum())
        n_children_total = int(is_child.sum())
        share_children_with_absent_escorter = n_children_with_absent_escorter / max(n_children_total, 1)
        logger.info(
            "%s n_children_with_absent_escorter: %d/%d (%.2f%%) present children (age <= %d) "
            "live in a household with an absent member carrying an escort leg (household-level "
            "PROXY for a linked escorter -- the chainsolver link does not exist at this stage)",
            _LOG_TAG, n_children_with_absent_escorter, n_children_total,
            100.0 * share_children_with_absent_escorter, CHILD_MAX_AGE_YEARS)

    diagnostics = {
        "n_persons_replaced": n_matched,
        "n_persons_absent": n_persons_absent,           # unchanged meaning: commute-absent persons
        "n_persons_absent_commute": n_persons_absent,
        "n_persons_absent_general": len(general_absent_persons),
        "n_persons_absent_both": len(commute_absent_persons & general_absent_persons),
        "n_persons_absent_total": len(absent_persons),
        "n_trips_removed": n_trips_removed,
        "n_trips_removed_general": n_trips_removed_general,
        "n_trips_added": n_trips_added,
        "n_home_unmatched": n_home_unmatched,
        "n_extra_columns_nulled": n_extra_columns_nulled,
        "n_donors_without_trips": n_donors_without_trips,
        "n_donors_immobile": n_donors_immobile,
        "n_donors_unknown_trip_count": n_donors_unknown_trip_count,
        "share_donors_without_trips": float(share_donors_without_trips),
        "share_donors_immobile": float(share_donors_immobile),
        "n_absent_with_escort_leg": n_absent_with_escort_leg,
        "n_children_with_absent_escorter": n_children_with_absent_escorter,
        # None on the jitter path and when nothing was replaced at all -- never a substituted
        # empty report, which would read as "the model ran and found nothing".
        "departure_time": departure_time_diagnostics,
    }

    logger.info(
        "%s reporting-day trips built: %d persons replaced (+%d/-%d trips), %d/%d matched donors "
        "were IMMOBILE (%.1f%%, is_immobile, an expected trip-less home-office day) and %d/%d "
        "(%.1f%%) had zero rows although their donor did travel; %d persons absent (0 rows), %d "
        "home persons unmatched (kept unchanged, %d extra column(s) nulled on replaced rows)",
        _LOG_TAG, n_matched, n_trips_added, n_trips_removed,
        n_donors_immobile, n_matched, 100.0 * share_donors_immobile,
        n_donors_without_trips, n_matched, 100.0 * share_donors_without_trips,
        n_persons_absent, n_home_unmatched, n_extra_columns_nulled)
    logger.info("%s %d persons generally absent (%d of them also commute-absent)", _LOG_TAG,
                len(general_absent_persons), len(commute_absent_persons & general_absent_persons))
    if n_donors_unknown_trip_count > 0:
        logger.warning(
            "%s %d matched donor(s) with no rows in donor_trips are ALSO absent from the donor "
            "attributes frame, so their trip count is unknown; they are counted as "
            "n_donors_without_trips (the suspicious case), never as immobile.",
            _LOG_TAG, n_donors_unknown_trip_count)
    if n_home_unmatched > 0:
        logger.warning(
            "%s %d home person(s) had no donor match and keep their ORIGINAL (pre-home-office) "
            "day unchanged -- the state stage is expected to downgrade these to at_workplace.",
            _LOG_TAG, n_home_unmatched)
    if share_donors_without_trips > WARN_DONORS_WITHOUT_TRIPS_SHARE:
        logger.warning(
            "%s %d/%d matched donors (%.1f%%) have ZERO rows in donor_trips although the donor "
            "pool does NOT flag them immobile (or their attributes are missing altogether) -- "
            "above %.0f%%, which signals either a chain dropped by the repair/resample cascade or "
            "a donor_id key/dtype mismatch between matches and donor_trips (silently wiping every "
            "affected replaced person's day). Genuinely immobile donors are NOT counted here; "
            "they are reported as n_donors_immobile.",
            _LOG_TAG, n_donors_without_trips, n_matched, 100.0 * share_donors_without_trips,
            100.0 * WARN_DONORS_WITHOUT_TRIPS_SHARE)

    return result, diagnostics
