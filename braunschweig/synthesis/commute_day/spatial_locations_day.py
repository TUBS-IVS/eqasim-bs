"""synpp stage: the eqasim location join on the REPORTING-DAY activities (ADR-0104, #244).

Aliased to ``synthesis.population.spatial.locations``. The vendored eqasim stage
(``synthesis.population.spatial.locations``) joins every activity of the day to its chosen
location; it must see the FINISHED day, because the reporting-day view removes the work
activities of ``home`` / ``absent`` workers and splices a donor's day in their place. Reading
the pre-assignment activities here would produce a location row set that no longer matches the
activities the MATSim population is written from.

The eqasim join logic itself is NOT re-implemented: ``configure`` and ``execute`` are the
vendored ones, run through the proxies of :mod:`braunschweig.synthesis.commute_day.day_view`,
which substitute ``synthesis.population.activities.final`` for
``synthesis.population.activities``. With ``commute_day_state_enabled`` false the ``.final``
alias is a pass-through of the pre-assignment activities, so the OFF path is byte-identical.
"""
from __future__ import annotations

import logging

import pandas as pd
import synthesis.population.spatial.locations as base

from braunschweig import synpp_tokens
from braunschweig.synthesis.commute_day import day_view as _day_view
from braunschweig.synthesis.commute_day.day_view import (
    ConfigureDayViewContext,
    StageOverrideContext,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day locations]"

ACTIVITIES_STAGE = "synthesis.population.activities"
DAY_ACTIVITIES_STAGE = "synthesis.population.activities.final"

#: Only the activities name is substituted here; the vendored stage reads no trips frame.
_STAGE_MAP = {ACTIVITIES_STAGE: DAY_ACTIVITIES_STAGE}

SECONDARY_STAGE = "synthesis.population.spatial.secondary.locations"
#: The out-and-back stroll marker of the secondary stage (issue #679) and the loop switch it belongs
#: to. Literals on purpose: importing their homes
#: (braunschweig.synthesis.locations.secondary_chainsolvers.OUT_AND_BACK_STROLL_COLUMN,
#: braunschweig.popsim.stage.config_keys.KEY_/DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT) would pull
#: their import closures into this stage's synpp token; tests/test_population_stroll_attribute.py
#: pins all three to their homes.
OUT_AND_BACK_STROLL_COLUMN = "is_out_and_back_stroll"
KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT = "secondary_loop_zero_displacement"
DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT = False

#: Modules whose sources this stage's cache token must cover (see :func:`validate`): the shim
#: decides WHICH frame the vendored stage sees, and the VENDORED join itself is what produces
#: the output -- synpp hashes only this thin module, so an edit to
#: synthesis/population/spatial/locations.py would otherwise leave a stale location frame in
#: the cache.
_HELPER_MODULES = (_day_view, base)


def validate(context):
    """synpp validation token: structured token over the pure helper modules' sources.

    synpp hashes only THIS module's source, so an edit to a helper module it imports would
    otherwise leave the cached stage output in place although the rules that produced it
    changed. The token folds those sources in, so a helper edit devalidates the stage exactly
    like an edit here (same mechanism as
    ``braunschweig.synthesis.locations.secondary_chainsolvers.validate``).

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it).
    """
    return synpp_tokens.source_token(
        _HELPER_MODULES, (),
        stage_label=__name__ + ".validate()")


def configure(context):
    base.configure(ConfigureDayViewContext(context, _STAGE_MAP))
    # The stroll split belongs to the loop switch (issue #679); declared here because synpp's
    # execute-time config(key) answers only keys configure() declared.
    context.config(KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT,
                   DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT)


def _with_out_and_back_strolls(locations, secondary):
    """``locations`` with the bool column :data:`OUT_AND_BACK_STROLL_COLUMN` (issue #679).

    True on the activities the secondary stage marked as out-and-back strolls, False on every
    other located activity (home, work, education and the other secondary ones). A marked row
    that matches no located activity raises ``ValueError``: the marker would be lost and the
    trip into that stroll would escape the Java stroll rule.
    """
    if OUT_AND_BACK_STROLL_COLUMN not in secondary.columns:
        raise ValueError(
            "%s %s is on, but the secondary location stage returned no %s column; the stroll "
            "marker cannot reach the population." % (
                _LOG_TAG, KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT, OUT_AND_BACK_STROLL_COLUMN))
    marked = secondary.loc[secondary[OUT_AND_BACK_STROLL_COLUMN].astype(bool), ["person_id", "activity_index"]]
    marked_keys = pd.MultiIndex.from_frame(marked)
    located_keys = pd.MultiIndex.from_frame(locations[["person_id", "activity_index"]])
    unmatched = marked_keys[~marked_keys.isin(located_keys)]
    if len(unmatched):
        raise ValueError(
            "%s %d out-and-back stroll rows of the secondary stage match no located activity; "
            "first (person_id, activity_index): %s" % (
                _LOG_TAG, len(unmatched), [tuple(int(v) for v in key) for key in unmatched[:5]]))
    result = locations.copy()
    result[OUT_AND_BACK_STROLL_COLUMN] = located_keys.isin(marked_keys)
    n_marked = int(result[OUT_AND_BACK_STROLL_COLUMN].sum())
    # Zero with the switch on almost certainly means the secondary stage lost the marker, hence a warning.
    log = logger.info if n_marked > 0 else logger.warning
    log("%s %s on %d/%d located activities (%.2f%%)%s", _LOG_TAG, OUT_AND_BACK_STROLL_COLUMN,
        n_marked, len(result), 100.0 * n_marked / max(len(result), 1),
        "" if n_marked > 0 else " -- none at all: check the secondary stage's stroll marker")
    return result


def execute(context):
    day_activities = context.stage(DAY_ACTIVITIES_STAGE)
    locations = base.execute(
        StageOverrideContext(context, {ACTIVITIES_STAGE: day_activities}))
    logger.info("%s located %d activities of %d persons from the reporting-day activities "
                "(%d rows)", _LOG_TAG, len(locations), locations["person_id"].nunique(),
                len(day_activities))
    if context.config(KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT):
        locations = _with_out_and_back_strolls(locations, context.stage(SECONDARY_STAGE)[0])
    return locations
