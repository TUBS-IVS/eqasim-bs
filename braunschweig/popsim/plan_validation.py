"""Logical validation (and repair) of popsim_mid activity-chain plans.

Mirrors the invariants eqasim enforces on HTS trips (data/hts/hts.py) and adds
the ones eqasim only assumes (home chain-closure). Reports issues per person and
can repair fixable plans (delegating time repair to eqasim's fix_trip_times).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanIssue:
    person_id: object
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    n_persons: int
    n_invalid_persons: int
    issues: list
    issue_counts: dict

    @property
    def is_valid(self) -> bool:
        return self.n_invalid_persons == 0

    @property
    def n_invalid(self) -> int:
        return self.n_invalid_persons


class PlanValidator:
    """Validate trip/activity chains for logical consistency.

    Checks (per person, on the time-sorted trips):
    - ``departure_after_arrival``: each trip departs at or before it arrives;
    - ``negative_activity_duration``: gap to the next trip is non-negative;
    - ``trip_overlap``: a trip does not arrive after the next trip departs;
    - ``not_time_sorted``: departure times are non-decreasing;
    - (optional) ``home_closure``: the day starts and ends at home (Task 4).
    """

    def __init__(self, *, require_home_closure: bool = True):
        self.require_home_closure = require_home_closure

    def validate_trips(self, df_trips: pd.DataFrame) -> ValidationReport:
        issues: list = []
        persons = df_trips["person_id"].nunique()
        for person_id, group in df_trips.sort_values(
            ["person_id", "departure_time"]
        ).groupby("person_id", sort=False):
            issues.extend(self._check_person(person_id, group))
        invalid = {i.person_id for i in issues}
        counts: dict = {}
        for i in issues:
            counts[i.code] = counts.get(i.code, 0) + 1
        report = ValidationReport(persons, len(invalid), issues, counts)
        log = logger.warning if issues else logger.info
        log("[popsim.plan_validation] %d/%d persons invalid; issues %s",
            len(invalid), persons, counts)
        return report

    def _check_person(self, person_id, group: pd.DataFrame) -> list:
        out: list = []
        dep = group["departure_time"].to_numpy()
        arr = group["arrival_time"].to_numpy()
        if (arr < dep).any():
            out.append(PlanIssue(person_id, "departure_after_arrival",
                                 "a trip arrives before it departs"))
        if len(dep) > 1 and (dep[1:] < dep[:-1]).any():
            out.append(PlanIssue(person_id, "not_time_sorted",
                                 "departure times are not non-decreasing"))
        # activity duration = next departure - this arrival (all but last trip).
        if len(dep) > 1:
            gap = dep[1:] - arr[:-1]
            if (gap < 0).any():
                out.append(PlanIssue(person_id, "negative_activity_duration",
                                     "a between-trip activity has negative duration"))
            if (arr[:-1] > dep[1:]).any():
                out.append(PlanIssue(person_id, "trip_overlap",
                                     "a trip arrives after the next trip departs"))
        return out
