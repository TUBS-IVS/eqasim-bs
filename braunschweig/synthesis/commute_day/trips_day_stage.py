"""synpp stage: the reporting-day trips table (ADR-0104, issue #244, Phase B Task 4; issue #370, Task 4).

Aliased to ``synthesis.population.trips.final``: the REPORTING-DAY view of the population's day,
built from the pre-assignment ``synthesis.population.trips`` view by
:func:`braunschweig.synthesis.commute_day.plan_replacement.build_day_trips` (which owns every
replacement rule and documents it). The pre-assignment view itself is left untouched, so the
commute distances and primary locations derived from it -- and therefore the ASSIGNED distance
class the state draw depends on -- cannot become circular.

Two independent flags gate what is composed into the reporting day:

* ``commute_day_state_enabled`` (:data:`KEY_ENABLED`) -- the commute-day state/donor replacement
  (ADR-0104, Phase B).
* ``day_absence_enabled`` (:data:`KEY_DAY_ABSENCE_ENABLED`) -- the general day-absence draw
  (issue #370), read from :data:`ABSENCE_STAGE`.

Since issue #123 Task 4 (ADR-0114) the stage also selects the START-TIME model applied to the
SPLICED rows, from the same four ``departure_time_*`` config keys the pre-assignment trip build
(``braunschweig.popsim.trips_stage``) reads -- both must resolve the same model, or a home-office
person's day would follow a different start-time distribution than everybody else's. The receiving
persons' attributes come from the ALREADY declared ``synthesis.population.enriched`` frame (ruling
A-R13), so no second population stage enters this stage's DAG. For ``srv_mapped`` the stage also
builds the model's RANKING CONTEXT (ruling A-R18) from that same enriched frame and the
pre-assignment trips it is already reading: the POPULATION's raw first departures per mapping cell,
so a spliced person's quantile is computed in the population's distribution of their cell instead
of among the few home-office persons replaced in this run.

With BOTH flags FALSE the stage returns the pre-assignment frame ITSELF (the very same object, not
a copy), so the reporting-day view is byte-identical to the pre-assignment one and the alias is a
pure pass-through. With only one flag on, the other input is replaced by an empty placeholder
(:func:`empty_states` / :func:`empty_matches` / :func:`_empty_donor_trips`, or ``None`` for the
general absence frame) so :func:`build_day_trips` sees a consistent, schema-correct call either
way -- see :func:`execute`.
"""
from __future__ import annotations

import hashlib
import inspect
import logging

import pandas as pd

from braunschweig.calibration import reported_time_precision as _reported_time_precision
from braunschweig.calibration import srv_departure_times as _srv_departure_times
from braunschweig.calibration import srv_plan_structure as _srv_plan_structure
from braunschweig.popsim import departure_time_model as _departure_time_model
from braunschweig.popsim import plan_validation as _plan_validation
from braunschweig.popsim.trips_stage import CONTRACT
from braunschweig.synthesis.commute_day import plan_replacement as _plan_replacement
from braunschweig.synthesis.commute_day.plan_replacement import (DepartureTimeSettings,
                                                                 build_day_trips)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day trips]"

#: Pure modules whose source this stage's cache token must cover (see :func:`validate`): every
#: plan-replacement rule lives in ``plan_replacement``, not here, and since issue #123 Task 4 the
#: replaced rows' start times come from ``departure_time_model``. ``validate()`` hashes these
#: NON-transitively, so the four CROSS-PACKAGE modules ``departure_time_model`` itself imports at
#: module level, and whose content decides the realised times, are named here too: the
#: reporting-precision half widths, the reference table's bin geometry, the harmonised group rule
#: the mapping cell is keyed on, and ``plan_validation`` -- whose ``MAX_PLAN_TIME_SECONDS`` is the
#: upper bound the spliced offsets are CLIPPED to on the derounded/srv_mapped branch, so moving it
#: moves this stage's times. None of the four is an own-package sibling, so
#: ``tests/test_synpp_helper_hash_invariant.py`` (scoped to own-package siblings) cannot catch a
#: missing one -- this comment is the guard. (``braunschweig.popsim.attributes`` is deliberately
#: absent: only ``persons_from_mid_schema`` reads it, and this stage uses the SYNTHETIC-schema
#: adapter.)
_HELPER_MODULES = (_plan_replacement, _departure_time_model, _reported_time_precision,
                   _srv_departure_times, _srv_plan_structure, _plan_validation)

KEY_ENABLED = "commute_day_state_enabled"
DEFAULT_ENABLED = True

#: General day-absence flag (issue #370): whether the absence stage's persons are removed from the
#: reporting day next to the commute-day ``absent`` ones. Independent of :data:`KEY_ENABLED`.
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
DEFAULT_DAY_ABSENCE_ENABLED = True
#: The general day-absence synpp stage this module reads when :data:`KEY_DAY_ABSENCE_ENABLED` is on.
ABSENCE_STAGE = "braunschweig.synthesis.day_absence.absence_stage"

#: ``commute_day_state`` value whose persons get a donor's day spliced in.
STATE_HOME = "home"
#: Columns :func:`build_day_trips` needs on its ``matches`` argument.
MATCH_COLUMNS = ("person_id", "donor_id", "coarsening_level")


def validate(context):
    """synpp validation token: md5 over the pure helper modules' sources.

    synpp hashes only THIS module's source, so an edit to a helper module it imports would
    otherwise leave the cached stage output in place although the rules that produced it
    changed. The token folds those sources in, so a helper edit devalidates the stage exactly
    like an edit here (same mechanism as
    ``braunschweig.synthesis.locations.secondary_chainsolvers.validate``).
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    # All four upstream stages are declared UNCONDITIONALLY (including ABSENCE_STAGE): this is a
    # STABLE declaration list, a local convention of THIS stage, not a limitation of the test
    # harness (corrected wording, final-review fix wave, Important finding 7 -- the recorders
    # used across tests/test_commute_day_stages.py were EXTENDED with config overrides by Tasks
    # 6/7 of the #370 SDD plan, so "the stub cannot record a conditional declare" is no longer an
    # accurate justification for anything written after those tasks). state_stage and
    # home_office_donors_stage were ALREADY declared unconditionally here before this feature
    # existed (KEY_ENABLED's own OFF path is likewise trivial, see execute()), and ABSENCE_STAGE's
    # OFF path (_disabled_frame in absence_stage.py) is equally cheap, so keeping the whole list
    # unconditional avoids a branch here for a stage whose DAG cost when unused is negligible.
    # This is THIS stage's own convention, not a repo-wide rule: sibling consumers of the SAME
    # stages -- braunschweig.analysis.synthesis.plan_structure_vs_srv,
    # braunschweig.analysis.synthesis.work_participation_by_kreis,
    # braunschweig.synthesis.commute_day.output_day, braunschweig.matsim.scenario.population --
    # gate their OWN declarations on the flag in configure(), and that stays legitimate: a
    # workflow that never enables the model there must not carry the donor/state/absence chain in
    # its DAG at all.
    context.stage("synthesis.population.trips")
    context.stage("braunschweig.synthesis.commute_day.state_stage")
    context.stage("braunschweig.synthesis.commute_day.home_office_donors_stage")
    context.stage(ABSENCE_STAGE)
    # Ruling R10 (final-review fix wave, spec 2.1 point 4): declared EXPLICITLY, even though the
    # absence stage above already reads it -- synpp does not transitively expose a dependency's
    # own dependencies, so this stage must declare it itself to pass it on to
    # build_day_trips's escort-coherence diagnostics (n_children_with_absent_escorter).
    context.stage("synthesis.population.enriched")
    context.config("random_seed")
    context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED)
    # Departure-time model of the SPLICED rows (issue #123 Task 4, ADR-0114). Declared with the
    # SAME four shared constants braunschweig.popsim.trips_stage declares, so the reporting day
    # and the pre-assignment day it is built from always resolve the same start-time model --
    # two different models would give a home-office person a day drawn from a different
    # start-time distribution than everybody else's.
    #
    # Ruling A-R13: NO synthesis.population.sampled dependency is added for the receiving
    # persons' attributes. synthesis.population.enriched -- already declared above for the
    # escort-coherence diagnostic -- carries the harmonised ``age`` / ``employed`` columns the
    # SYNTHETIC-schema adapter needs, so declaring a second population stage would only widen
    # this stage's DAG for data it already has.
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, DEFAULT_DEPARTURE_TIME_MIN_MODEL_N,
        DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N, DEFAULT_DEPARTURE_TIME_MODEL,
        KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, KEY_DEPARTURE_TIME_MIN_MODEL_N,
        KEY_DEPARTURE_TIME_MIN_REFERENCE_N, KEY_DEPARTURE_TIME_MODEL,
    )
    context.config(KEY_DEPARTURE_TIME_MODEL, DEFAULT_DEPARTURE_TIME_MODEL)
    context.config(KEY_DEPARTURE_TIME_MIN_REFERENCE_N, DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N)
    context.config(KEY_DEPARTURE_TIME_MIN_MODEL_N, DEFAULT_DEPARTURE_TIME_MIN_MODEL_N)
    context.config(KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS,
                   DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS)
    # Root of the committed reference data; the SrV departure-time reference the "srv_mapped"
    # model maps onto is read from <data_path>/braunschweig/srv in execute().
    context.config("data_path")


def matches_from_states(states):
    """The donor matches carried by the state frame: ``home`` persons with a donor.

    ``braunschweig.synthesis.commute_day.state_stage`` already downgraded every ``home`` person
    WITHOUT a donor to ``at_workplace``, so both conditions together are the state frame's own
    statement of "this person's day is to be replaced by that donor's day" -- re-deriving it here
    from either condition alone would silently diverge from the state stage's decision.
    """
    is_replaced = (states["commute_day_state"] == STATE_HOME) & states["donor_id"].notna()
    return states.loc[is_replaced, list(MATCH_COLUMNS)].reset_index(drop=True)


def empty_states():
    """An empty ``states`` frame with the state stage's own schema, for the commute-OFF path.

    Imported lazily to avoid a module-level import cycle (``state_stage`` does not import this
    module, but importing it from the top level here would still tie both modules' import order
    together unnecessarily for a schema this function alone needs).
    """
    from braunschweig.synthesis.commute_day.state_stage import STATE_COLUMNS
    return pd.DataFrame({column: pd.Series(dtype=object) for column in STATE_COLUMNS})


def empty_matches():
    """An empty ``matches`` frame with :data:`MATCH_COLUMNS`, for the commute-OFF path."""
    return pd.DataFrame(columns=list(MATCH_COLUMNS))


def _empty_donor_trips():
    """An empty ``donor_trips`` frame satisfying :func:`build_day_trips`'s column check.

    Columns: ``donor_id`` plus the CONTRACT columns (minus ``person_id``, which names the
    RECEIVING person, not the donor) plus the two donor-trace extras ``euclidean_distance`` and
    ``trip_key`` -- exactly what ``plan_replacement._require_columns`` checks for the
    ``donor_trips`` argument. Used only on the commute-OFF path, where there are no donors to
    splice in at all.
    """
    columns = ("donor_id",) + tuple(c for c in CONTRACT if c != "person_id") + ("euclidean_distance", "trip_key")
    return pd.DataFrame({column: pd.Series(dtype=object) for column in columns})


def _departure_time_settings(context, persons: pd.DataFrame, trips: pd.DataFrame,
                             n_replaced: int) -> DepartureTimeSettings:
    """Build the :class:`DepartureTimeSettings` for this run from the four declared config keys.

    The SrV reference is loaded through
    :func:`braunschweig.popsim.departure_time_model.load_reference_for_model`, the same helper
    the pre-assignment trip build uses, so a missing file aborts BOTH stages with the same
    message naming the path and the key -- never a silent fall back to the eqasim jitter.
    ``persons`` is the enriched population (ruling A-R13); the plan replacement restricts it to
    the replaced persons itself.

    The RANKING CONTEXT (rulings A-R18 / A-R20) is built here, from the PRE-ASSIGNMENT ``trips``
    view and those same enriched persons. It is the POPULATION's raw first departures per mapping
    cell, so a spliced person's quantile is computed in the population's distribution of their
    cell -- the same base the pre-assignment trip build ranks in -- rather than among the handful
    of home-office persons this run happened to replace. Nothing circular is introduced: the
    context reads only the pre-assignment view's RAW (pre-model) times and the person attributes
    this stage already depends on.

    It is built LAZILY, on two conditions, and the log line says which of them decided:

    * ``srv_mapped`` only -- the two other models rank nothing, and the dispatch REJECTS a context
      they would ignore, exactly as ``load_reference_for_model`` returns no reference for them;
    * ``n_replaced > 0`` -- with nothing spliced the model never runs, so a groupby over the whole
      population's trips would be pure cost.

    The reference is still resolved FIRST and unconditionally: a misconfigured run (an unknown
    model, or ``srv_mapped`` without its committed table) must abort whenever this stage runs at
    all, not only when the state draw happens to produce a replaced person.
    """
    from braunschweig.popsim.stage.config_keys import (
        KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, KEY_DEPARTURE_TIME_MIN_MODEL_N,
        KEY_DEPARTURE_TIME_MIN_REFERENCE_N, KEY_DEPARTURE_TIME_MODEL,
    )
    model = str(context.config(KEY_DEPARTURE_TIME_MODEL))
    if model not in _departure_time_model.MODELS:
        raise ValueError(
            f"{_LOG_TAG} {KEY_DEPARTURE_TIME_MODEL}={model!r} is not a known departure-time "
            f"model; expected one of {list(_departure_time_model.MODELS)}.")
    # The reference is resolved BEFORE the context is built: a misconfigured run (a missing
    # committed table) must abort on the configuration, not after a groupby over the whole
    # population's trips.
    reference = _departure_time_model.load_reference_for_model(
        context.config("data_path"), model, config_key=KEY_DEPARTURE_TIME_MODEL)
    ranking_context, base_reason = None, "this model ranks nothing"
    if model == _departure_time_model.MODEL_SRV_MAPPED:
        if n_replaced > 0:
            ranking_context = _departure_time_model.build_ranking_context(trips, persons)
        else:
            base_reason = "no person is spliced in this run, so the model never runs"
    settings = DepartureTimeSettings(
        model=model,
        reference=reference,
        min_reference_n=int(context.config(KEY_DEPARTURE_TIME_MIN_REFERENCE_N)),
        min_model_n=int(context.config(KEY_DEPARTURE_TIME_MIN_MODEL_N)),
        max_median_shift_hours=float(context.config(KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS)),
        persons=persons,
        ranking_context=ranking_context)
    logger.info("%s departure-time model for the spliced rows: %s (min_reference_n=%d, "
                "min_model_n=%d, max_median_shift_hours=%.2f); ranking context: %s", _LOG_TAG,
                settings.model, settings.min_reference_n, settings.min_model_n,
                settings.max_median_shift_hours,
                "not built (%s)" % base_reason if ranking_context is None
                else "%d population person(s), for %d person(s) to be spliced"
                     % (len(ranking_context), n_replaced))
    return settings


def execute(context):
    trips = context.stage("synthesis.population.trips")
    commute_on = bool(context.config(KEY_ENABLED))
    absence_on = bool(context.config(KEY_DAY_ABSENCE_ENABLED))

    if not commute_on and not absence_on:
        logger.info("%s %s and %s are both false -- the reporting-day trips are the "
                    "pre-assignment trips (%d rows, unchanged).", _LOG_TAG, KEY_ENABLED,
                    KEY_DAY_ABSENCE_ENABLED, len(trips))
        return trips

    # issue #370: the general day-absence frame is independent of the commute-day model, so it is
    # read regardless of commute_on, whenever the absence model itself is on.
    general_absence = context.stage(ABSENCE_STAGE)["absence"] if absence_on else None
    # Ruling R10: always fetched (regardless of either flag) and forwarded to build_day_trips's
    # escort-coherence diagnostics -- cheap (already cached by the absence stage's own
    # dependency) and keeps the diagnostic available whenever this stage actually runs.
    persons = context.stage("synthesis.population.enriched")

    if commute_on:
        state_output = context.stage("braunschweig.synthesis.commute_day.state_stage")
        donor_attributes, donor_trips, _donor_diagnostics = context.stage(
            "braunschweig.synthesis.commute_day.home_office_donors_stage")
        states = state_output["states"]
        matches = matches_from_states(states)
    else:
        # commute_day_state_enabled is false but day_absence_enabled is true: nobody is
        # commute-absent or home-replaced, only the general-absence removal applies. Empty
        # placeholders keep build_day_trips's column contract satisfied without touching the
        # state/donor stage outputs at all (mirrors the identity-pass-through spirit of the
        # commute-OFF path: no commute-day computation is performed).
        states, matches, donor_trips, donor_attributes = (
            empty_states(), empty_matches(), _empty_donor_trips(), None)

    # Resolved on BOTH remaining paths, including the absence-only one where nothing is spliced
    # and the model therefore never runs: a misconfigured model (unknown name, or srv_mapped
    # without its committed reference) must abort whenever this stage runs at all, not only when
    # the state draw happens to produce a replaced person. The ranking context, by contrast, is
    # built only when the match count says at least one day WILL be spliced (ruling A-R20 fix
    # round, review Minor 7) -- it costs a groupby over the whole population's trips and is read
    # by nothing when no chain is replaced.
    departure_time = _departure_time_settings(context, persons, trips, len(matches))
    # Ruling R9: the attributes carry n_trips, which is what lets build_day_trips tell an
    # EXPECTED immobile donor day (n_trips == 0) apart from a donor_id join failure -- both look
    # like "no rows for this donor" in donor_trips alone.
    day_trips, diagnostics = build_day_trips(trips, states, matches, donor_trips,
                                             random_seed=int(context.config("random_seed")),
                                             donor_attributes=donor_attributes,
                                             general_absence=general_absence,
                                             persons=persons,
                                             departure_time=departure_time)
    logger.info("%s reporting-day trips: %d rows for %d persons (from %d rows for %d persons); "
                "%s=%s, %s=%s; diagnostics: %s", _LOG_TAG, len(day_trips),
                day_trips["person_id"].nunique(), len(trips), trips["person_id"].nunique(),
                KEY_ENABLED, commute_on, KEY_DAY_ABSENCE_ENABLED, absence_on, diagnostics)
    return day_trips
