"""synpp stage: the reporting-day activities table (ADR-0104, issue #244, Phase B Task 4).

Aliased to ``synthesis.population.activities.final``: the same eqasim trips->activities transform
as ``synthesis.population.activities``, applied to the REPORTING-DAY trips
(``synthesis.population.trips.final``) instead of the pre-assignment ones.

The transform itself is NOT re-implemented: this stage calls
``synthesis.population.activities.execute`` through :class:`_ActivitiesShimContext`, a context
that answers the two stage names that module reads. Re-implementing it would create a second copy
of the eqasim activity logic that could drift from the vendored one, which is exactly what the
alias seam exists to avoid.

``synthesis.population.activities.execute`` MUTATES the trips frame it is handed (it adds
``trip_count``, ``purpose``, ``start_time``, ... columns in place), so the shim hands it a COPY:
the reporting-day trips frame is a cached synpp stage output that other stages read afterwards,
and mutating it here would corrupt them.
"""
from __future__ import annotations

import hashlib
import inspect
import logging

import synthesis.population.activities as base

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day activities]"

#: The two stage names ``synthesis.population.activities`` reads; the shim maps the first onto
#: the REPORTING-DAY trips and answers the second unchanged.
TRIPS_STAGE = "synthesis.population.trips"
ENRICHED_STAGE = "synthesis.population.enriched"
DAY_TRIPS_STAGE = "synthesis.population.trips.final"

#: Modules whose sources this stage's cache token must cover (see :func:`validate`): this
#: module only decides WHICH trips frame the vendored transform sees, and the VENDORED
#: transform itself is what produces the output -- synpp hashes only this thin module, so an
#: edit to synthesis/population/activities.py would otherwise leave a stale cached
#: ``.activities.final`` in place (same mechanism as ``output_day.py`` /
#: ``spatial_locations_day.py``).
_HELPER_MODULES = (base,)


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
    context.stage(DAY_TRIPS_STAGE)
    context.stage(ENRICHED_STAGE)


class _ActivitiesShimContext:
    """Minimal synpp ExecuteContext for ``synthesis.population.activities.execute``.

    Answers ``stage(TRIPS_STAGE)`` with a COPY of the reporting-day trips (see the module
    docstring on the mutation) and ``stage(ENRICHED_STAGE)`` with the enriched population. Any
    other stage name raises: a future eqasim version reading a third stage must be noticed here,
    not silently handed ``None``.
    """

    def __init__(self, day_trips, persons):
        self._day_trips = day_trips
        self._persons = persons

    def stage(self, name):
        if name == TRIPS_STAGE:
            return self._day_trips.copy()
        if name == ENRICHED_STAGE:
            return self._persons
        raise KeyError(
            f"{_LOG_TAG} synthesis.population.activities requested the stage {name!r}, which "
            f"this shim does not provide (it maps {TRIPS_STAGE!r} to {DAY_TRIPS_STAGE!r} and "
            f"passes {ENRICHED_STAGE!r} through). Declare the new dependency in configure() and "
            "extend the shim.")


def execute(context):
    day_trips = context.stage(DAY_TRIPS_STAGE)
    persons = context.stage(ENRICHED_STAGE)
    activities = base.execute(_ActivitiesShimContext(day_trips, persons))
    logger.info("%s reporting-day activities: %d rows for %d persons (from %d reporting-day "
                "trips)", _LOG_TAG, len(activities), activities["person_id"].nunique(),
                len(day_trips))
    return activities
