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

**The rule is currently implemented THREE times and they must not diverge.** This module,
``braunschweig.synthesis.commute_day.state_stage._escort_person_ids`` and the escort-leg mask in
``braunschweig.synthesis.commute_day.plan_replacement`` each apply it independently -- a deliberate
cross-module-avoidance choice, since importing one into another would devalidate stage caches that
have nothing to do with the importing change. The cost is that a future edit to one (a purpose
alias, a third trip end) would silently disagree with the others, and ADR-0110 Amendment 2's
"stranded children = 0 by construction" depends on the day-absence gate and
``plan_replacement``'s stranded-children metric using the SAME rule.
``tests/test_escort_duty.py`` therefore pins not only the three ``ESCORT_PURPOSE`` literals but
the three MASKS, on a shared fixture. Consolidating them is tracked separately; until then, an
edit here must be made in all three places and the pin test is what catches a miss.
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


def escort_person_ids(trips: pd.DataFrame) -> set:
    """Person ids with an escort leg on either trip end of their pre-assignment reporting day.

    ``trips`` needs ``person_id``, ``following_purpose`` and ``preceding_purpose``; a missing
    column raises a ``ValueError`` naming it. The share of persons with a trip that carry an
    escort leg is logged (fallback transparency: a consumer that receives an EMPTY set while the
    trip table has rows is looking at an ``escort_purpose: false`` trip table, not at a population
    that escorts nobody -- the consumer decides whether that is a warning in its context).
    """
    missing = [column for column in _REQUIRED_COLUMNS if column not in trips.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the trips frame lacks column(s) {missing!r}; "
                         f"escort duty needs {list(_REQUIRED_COLUMNS)!r}")
    is_escort = ((trips["following_purpose"] == ESCORT_PURPOSE)
                 | (trips["preceding_purpose"] == ESCORT_PURPOSE))
    escort_persons = set(trips.loc[is_escort, "person_id"])
    n_persons = int(trips["person_id"].nunique())
    logger.info("%s %d/%d persons with a trip (%.1f%%) have an %r leg on their pre-assignment day",
                _LOG_TAG, len(escort_persons), n_persons,
                100.0 * len(escort_persons) / max(n_persons, 1), ESCORT_PURPOSE)
    return escort_persons
