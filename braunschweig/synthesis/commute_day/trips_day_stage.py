"""synpp stage: the reporting-day trips table (ADR-0104, issue #244, Phase B Task 4).

Aliased to ``synthesis.population.trips.final``: the REPORTING-DAY view of the population's day,
built from the pre-assignment ``synthesis.population.trips`` view by
:func:`braunschweig.synthesis.commute_day.plan_replacement.build_day_trips` (which owns every
replacement rule and documents it). The pre-assignment view itself is left untouched, so the
commute distances and primary locations derived from it -- and therefore the ASSIGNED distance
class the state draw depends on -- cannot become circular.

With ``commute_day_state_enabled`` FALSE the stage returns the pre-assignment frame ITSELF (the
very same object, not a copy), so the reporting-day view is byte-identical to the pre-assignment
one and the alias is a pure pass-through.
"""
from __future__ import annotations

import hashlib
import inspect
import logging

from braunschweig.synthesis.commute_day import plan_replacement as _plan_replacement
from braunschweig.synthesis.commute_day.plan_replacement import build_day_trips

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day trips]"

#: Pure module whose source this stage's cache token must cover (see :func:`validate`): every
#: plan-replacement rule lives there, not here.
_HELPER_MODULES = (_plan_replacement,)

KEY_ENABLED = "commute_day_state_enabled"
DEFAULT_ENABLED = True

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
    context.stage("synthesis.population.trips")
    context.stage("braunschweig.synthesis.commute_day.state_stage")
    context.stage("braunschweig.synthesis.commute_day.home_office_donors_stage")
    context.config("random_seed")
    context.config(KEY_ENABLED, DEFAULT_ENABLED)


def matches_from_states(states):
    """The donor matches carried by the state frame: ``home`` persons with a donor.

    ``braunschweig.synthesis.commute_day.state_stage`` already downgraded every ``home`` person
    WITHOUT a donor to ``at_workplace``, so both conditions together are the state frame's own
    statement of "this person's day is to be replaced by that donor's day" -- re-deriving it here
    from either condition alone would silently diverge from the state stage's decision.
    """
    is_replaced = (states["commute_day_state"] == STATE_HOME) & states["donor_id"].notna()
    return states.loc[is_replaced, list(MATCH_COLUMNS)].reset_index(drop=True)


def execute(context):
    trips = context.stage("synthesis.population.trips")
    if not bool(context.config(KEY_ENABLED)):
        logger.info("%s %s is false -- the reporting-day trips are the pre-assignment trips "
                    "(%d rows, unchanged).", _LOG_TAG, KEY_ENABLED, len(trips))
        return trips

    state_output = context.stage("braunschweig.synthesis.commute_day.state_stage")
    donor_attributes, donor_trips, _donor_diagnostics = context.stage(
        "braunschweig.synthesis.commute_day.home_office_donors_stage")
    states = state_output["states"]
    matches = matches_from_states(states)

    # Ruling R9: the attributes carry n_trips, which is what lets build_day_trips tell an
    # EXPECTED immobile donor day (n_trips == 0) apart from a donor_id join failure -- both look
    # like "no rows for this donor" in donor_trips alone.
    day_trips, diagnostics = build_day_trips(trips, states, matches, donor_trips,
                                             random_seed=int(context.config("random_seed")),
                                             donor_attributes=donor_attributes)
    logger.info("%s reporting-day trips: %d rows for %d persons (from %d rows for %d persons); "
                "diagnostics: %s", _LOG_TAG, len(day_trips), day_trips["person_id"].nunique(),
                len(trips), trips["person_id"].nunique(), diagnostics)
    return day_trips
