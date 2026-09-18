"""Escort duty on the pre-assignment reporting day (issue #425).

One pure question: which persons carry an escort leg in their own (pre-assignment) trip table?
An escort leg evidences presence at home on that day
(ADR-0104 Assumption 4), which is why the far-commuter model
(``braunschweig.synthesis.commute_day.state_stage``) never sets such a person ``absent`` and why
the general day-absence draw (``braunschweig.synthesis.day_absence``, ADR-0110 Amendment 2)
excludes them from its individual residual stage.

Both trip ends are inspected: the escorting person's outbound leg ARRIVES at the escort
activity (``following_purpose``), the return leg DEPARTS from it (``preceding_purpose``).

This module deliberately carries no synpp stage logic and no donor-pool knowledge, so it can be
folded into a stage's helper-module hash (``_HELPER_MODULES``) without pulling a whole stage
module along.

**This module is the SINGLE implementation of the rule** (issue #425). It previously existed
three times -- here, in ``braunschweig.synthesis.commute_day.state_stage._escort_person_ids`` and
as an inline mask in ``braunschweig.synthesis.commute_day.plan_replacement`` -- which was a
deliberate cross-module-avoidance choice at the time. Both now call this function, and both
re-export :data:`ESCORT_PURPOSE` from here for callers that refer to it by their module name.
What each keeps is only what is genuinely its own: ``state_stage`` its donor-pool guard
(an empty escort set beside a donor pool that carries escorting donors), ``plan_replacement``
nothing but the diagnostic it computes from the set.

This matters beyond tidiness: ADR-0110 Amendment 2's "stranded children = 0 by construction"
holds only while the day-absence gate and ``plan_replacement``'s stranded-children metric select
the SAME persons, and three independent copies could have diverged on a purpose alias or a third
trip end without any test failing. ``tests/test_escort_duty.py`` pins the three call sites to the
same behaviour on a shared fixture, so a future re-divergence fails rather than passes quietly.

Both consuming stages fold this module into their cache token (``_HELPER_MODULES`` of
``state_stage`` and of ``trips_day_stage``, the latter because it hashes ``plan_replacement``,
whose source no longer contains the rule), so an edit here devalidates every stage that depends
on the definition -- exactly as an inline copy would have.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_LOG_TAG = "[escort duty]"

#: Purpose label of an escort activity in the synthetic trip table (``following_purpose`` /
#: ``preceding_purpose``). Pinned equal to the local copies in ``state_stage`` and
#: ``plan_replacement`` by ``tests/test_escort_duty.py``.
ESCORT_PURPOSE = "escort"

_REQUIRED_COLUMNS = ("person_id", "following_purpose", "preceding_purpose")


def escort_person_ids(trips: pd.DataFrame, *, log: bool = True) -> set:
    """Person ids with an escort leg on either trip end of their pre-assignment reporting day.

    ``trips`` needs ``person_id``, ``following_purpose`` and ``preceding_purpose``; a missing
    column raises a ``ValueError`` naming it. The share of persons with a trip that carry an
    escort leg is logged (fallback transparency: a consumer that receives an EMPTY set while the
    trip table has rows is looking at an ``escort_purpose: false`` trip table, not at a population
    that escorts nobody -- the consumer decides whether that is a warning in its context).

    ``log=False`` suppresses that line for a caller that only needs the set for a DIAGNOSTIC
    (``plan_replacement``'s stranded-children metric) rather than owning a decision based on it,
    so one run does not repeat the same rate once per caller.
    """
    missing = [column for column in _REQUIRED_COLUMNS if column not in trips.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the trips frame lacks column(s) {missing!r}; "
                         f"escort duty needs {list(_REQUIRED_COLUMNS)!r}")
    is_escort = ((trips["following_purpose"] == ESCORT_PURPOSE)
                 | (trips["preceding_purpose"] == ESCORT_PURPOSE))
    escort_persons = set(trips.loc[is_escort, "person_id"])
    if log:
        n_persons = int(trips["person_id"].nunique())
        logger.info("%s escort duty: %d/%d persons with a trip (%.1f%%) have an %r leg on their "
                    "pre-assignment day", _LOG_TAG, len(escort_persons), n_persons,
                    100.0 * len(escort_persons) / max(n_persons, 1), ESCORT_PURPOSE)
    return escort_persons
