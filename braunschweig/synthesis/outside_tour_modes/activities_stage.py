"""synpp stage: the activities of the simulated day (eqasim-bs#465, ADR-0159 decision 4).

Aliased to ``synthesis.population.activities.simulated``: the vendored eqasim trips->activities transform
(``synthesis.population.activities``) applied to ``synthesis.population.trips.simulated``, so that the population
writer and the synthesis output build each plan's activity times from the same trips whose modes and times they
write. The timetable check of the outside-tour stage may move a whole outside tour (``pt_connections``); without
such a move this frame equals ``synthesis.population.activities.final`` (modes do not enter the activities).

The transform is not re-implemented: the shim of ``commute_day.activities_day_stage`` answers the two stage names
the vendored module reads (and hands it a COPY of the trips, which it mutates). The chain solver, the portal stage
and the analyses keep reading ``activities.final``.
"""
from __future__ import annotations

import logging

import synthesis.population.activities as base

from braunschweig import synpp_tokens
from braunschweig.synthesis.commute_day import activities_day_stage as _activities_day_stage
from braunschweig.synthesis.outside_tour_modes import config_keys as _config_keys
from braunschweig.synthesis.outside_tour_modes.config_keys import PERSONS_STAGE, SIMULATED_TRIPS_ALIAS

logger = logging.getLogger(__name__)

_LOG_TAG = "[outside_tour_modes.activities]"

#: The vendored transform and the shim that feeds it produce this stage's output (hashed by validate()).
_HELPER_MODULES = (base, _activities_day_stage, _config_keys)


def validate(context):
    """synpp validation token: the structured source token of the vendored transform and the shim module."""
    return synpp_tokens.source_token(_HELPER_MODULES, (), stage_label=__name__ + ".validate()")


def configure(context):
    context.stage(SIMULATED_TRIPS_ALIAS)
    context.stage(PERSONS_STAGE)


def execute(context):
    trips = context.stage(SIMULATED_TRIPS_ALIAS)
    persons = context.stage(PERSONS_STAGE)
    activities = base.execute(_activities_day_stage._ActivitiesShimContext(trips, persons))
    logger.info("%s simulated-day activities: %d rows for %d persons (from %d simulated-day trips)", _LOG_TAG,
                len(activities), activities["person_id"].nunique(), len(trips))
    return activities
