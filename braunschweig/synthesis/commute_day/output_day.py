"""synpp stage: the eqasim synthesis output on the REPORTING-DAY view (ADR-0104, #244).

Aliased to ``synthesis.output``. Two things distinguish it from the vendored
``synthesis.output``:

1. **The finished day.** ``persons.csv``, ``activities.csv``, ``trips.csv`` and the spatial
   exports must describe the day the simulation actually runs, so the trips and activities
   frames come from ``synthesis.population.trips.final`` / ``...activities.final`` instead of
   the pre-assignment views.
2. **The ``commute_day_state`` person attribute.** The drawn reporting-day state
   (``at_workplace`` / ``home`` / ``absent``) is exported through the EXISTING optional-column
   mechanism of ``synthesis.output`` (``PERSON_OPTIONAL_OUTPUT_COLUMNS`` /
   ``select_person_output_columns``): the state column is merged into the enriched persons
   frame BEFORE the vendored writer selects its columns, so the attribute is written by that
   one writer rather than by a second pass over the finished CSV.

The eqasim writer itself is NOT re-implemented: ``configure`` and ``execute`` are the vendored
ones, run through the proxies of :mod:`braunschweig.synthesis.commute_day.day_view`.

With ``commute_day_state_enabled`` false the ``.final`` aliases are pass-throughs of the
pre-assignment views AND the enriched frame is handed on untouched, so no ``commute_day_state``
column exists, ``select_person_output_columns`` returns the legacy list, and every output file
is byte-identical to the vendored stage's.
"""
from __future__ import annotations

import hashlib
import inspect
import logging

import synthesis.output as base

from braunschweig.synthesis.commute_day import day_view as _day_view
from braunschweig.synthesis.commute_day.day_view import (
    ConfigureDayViewContext,
    StageOverrideContext,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day output]"

ENRICHED_STAGE = "synthesis.population.enriched"
TRIPS_STAGE = "synthesis.population.trips"
ACTIVITIES_STAGE = "synthesis.population.activities"
DAY_TRIPS_STAGE = "synthesis.population.trips.final"
DAY_ACTIVITIES_STAGE = "synthesis.population.activities.final"
STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"

KEY_ENABLED = "commute_day_state_enabled"
DEFAULT_ENABLED = True

#: Column the state stage carries and this stage exports; it must be one of
#: ``synthesis.output.PERSON_OPTIONAL_OUTPUT_COLUMNS`` for the vendored writer to pick it up.
STATE_COLUMN = "commute_day_state"

#: Pure module whose source this stage's cache token must cover (see :func:`validate`): the
#: shim decides WHICH frames the vendored writer sees, so an edit to it changes this output.
_HELPER_MODULES = (_day_view,)


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
    base.configure(ConfigureDayViewContext(context))
    context.stage(STATE_STAGE)
    context.config(KEY_ENABLED, DEFAULT_ENABLED)


def attach_commute_day_state(persons, states):
    """Left-join ``commute_day_state`` onto the enriched persons frame.

    ``states`` is the state stage's ``states`` frame -- EXACTLY one row per worker -- so every
    person WITHOUT an assigned workplace (non-workers, children) keeps a missing value, which
    ``pandas.DataFrame.to_csv`` writes as an empty field. That absence is meaningful and is not
    filled with a substitute: a person who never works has no reporting-day commute state.

    The coverage rate is logged, and a coverage of zero raises: the only way a finished
    population contains no worker with a state is a broken ``person_id`` join between the state
    stage and the enriched population, which would otherwise ship an all-empty column that
    reads like a measured "no worker works today" (CLAUDE.md "Fallback transparency").
    """
    for frame, columns, what in ((persons, ("person_id",), "the enriched persons frame"),
                                 (states, ("person_id", STATE_COLUMN), "the states frame")):
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError(f"{_LOG_TAG} {what} is missing the required column(s) {missing} "
                             f"(present: {sorted(frame.columns)[:20]})")
    if STATE_COLUMN in persons.columns:
        raise ValueError(
            f"{_LOG_TAG} the enriched persons frame already carries a {STATE_COLUMN!r} column; "
            "merging the state stage on top of it would produce two ambiguous columns. Check "
            "whether an upstream stage started to emit that name.")

    merged = persons.merge(states[["person_id", STATE_COLUMN]], on="person_id", how="left",
                           validate="one_to_one")
    n_with_state = int(merged[STATE_COLUMN].notna().sum())
    counts = merged[STATE_COLUMN].value_counts().to_dict()
    logger.info("%s %d/%d persons (%.1f%%) carry a reporting-day state: %s; the remainder have "
                "no assigned workplace and keep an empty field", _LOG_TAG, n_with_state,
                len(merged), 100.0 * n_with_state / max(len(merged), 1),
                {str(key): int(value) for key, value in sorted(counts.items())})
    if n_with_state == 0:
        raise ValueError(
            f"{_LOG_TAG} not one of the {len(merged)} persons in the enriched population was "
            f"matched to a row of the state frame ({len(states)} rows); this is a broken "
            "person_id join, not a population without workers. Check the id types on both "
            "sides before exporting an all-empty column.")
    return merged


def execute(context):
    day_trips = context.stage(DAY_TRIPS_STAGE)
    day_activities = context.stage(DAY_ACTIVITIES_STAGE)
    persons = context.stage(ENRICHED_STAGE)

    if bool(context.config(KEY_ENABLED)):
        states = context.stage(STATE_STAGE)["states"]
        persons = attach_commute_day_state(persons, states)
    else:
        # Untouched frame -> no commute_day_state column -> select_person_output_columns
        # returns the legacy list -> byte-identical persons.csv.
        logger.info("%s %s is false -- the persons output keeps the legacy column set.",
                    _LOG_TAG, KEY_ENABLED)

    return base.execute(StageOverrideContext(context, {
        TRIPS_STAGE: day_trips,
        ACTIVITIES_STAGE: day_activities,
        ENRICHED_STAGE: persons,
    }))
