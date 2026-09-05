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

import hashlib
import inspect
import logging

import synthesis.population.spatial.locations as base

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

#: Pure module whose source this stage's cache token must cover (see :func:`validate`): the
#: shim decides WHICH frame the vendored stage sees, so an edit to it changes this output.
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
    base.configure(ConfigureDayViewContext(context, _STAGE_MAP))


def execute(context):
    day_activities = context.stage(DAY_ACTIVITIES_STAGE)
    locations = base.execute(
        StageOverrideContext(context, {ACTIVITIES_STAGE: day_activities}))
    logger.info("%s located %d activities of %d persons from the reporting-day activities "
                "(%d rows)", _LOG_TAG, len(locations), locations["person_id"].nunique(),
                len(day_activities))
    return locations
