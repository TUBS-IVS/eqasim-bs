"""Plan feasibility check on the written MATSim population, after the cut (eqasim-bs#443, ADR-0146).

Nothing between the population writer and MATSim used to check whether a plan can be lived in one simulated
day. This module streams the prepared population file (the file MATSim reads, after the cut and the freight
injection; located through the written config's ``plans.inputPlansFile``) and reports, per subpopulation and
per class, plans ending after the simulated day, plans with non-monotonic activity end times, walk and
bicycle legs over a straight-line distance and legs over a duration. The class ``frozen`` holds the plans
(person level) and legs (tour level) whose tour touches an ``outside`` activity -- the tours the discrete
mode choice never re-chooses (``OutsideFilter``); ``in_region`` is informational, because the mode choice
repairs it. Failure thresholds apply to the frozen residents only; freight is reported, never failed on
(#444 owns its defects).

The simulated day is ``braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS``; ``assert_qsim_end_time``
fails early when the written config's ``qsim.endTime`` disagrees with it (one constant, no silent drift).
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterator

from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

logger = logging.getLogger(__name__)

_LOG_TAG = "[plan_check]"
PLAN_CHECK_FILE_NAME = "plan_check.json"
RESIDENTS = "residents"
OUTSIDE = "outside"
HOME = "home"
FROZEN, IN_REGION = "frozen", "in_region"

KEY_ENABLED = "braunschweig.plan_check.enabled"
DEFAULT_ENABLED = True
#: ASSUMPTION (ADR-0146): anchored in the SrV table, 0.0 % walk share from 5 km straight-line.
KEY_WALK_MAX_DISTANCE_M = "braunschweig.plan_check.walk_max_distance_m"
DEFAULT_WALK_MAX_DISTANCE_M = 5000.0
#: ASSUMPTION (ADR-0146): SrV work bicycle share 0.1 % at 30-45 km.
KEY_BICYCLE_MAX_DISTANCE_M = "braunschweig.plan_check.bicycle_max_distance_m"
DEFAULT_BICYCLE_MAX_DISTANCE_M = 30000.0
KEY_LEG_MAX_DURATION_S = "braunschweig.plan_check.leg_max_duration_s"
DEFAULT_LEG_MAX_DURATION_S = 10800.0
#: ASSUMPTION (ADR-0146): the share of frozen resident plans that may be late or non-monotonic; the A/B calibrates it.
KEY_MAX_FROZEN_DEFECT_SHARE = "braunschweig.plan_check.max_frozen_defect_share"
DEFAULT_MAX_FROZEN_DEFECT_SHARE = 0.005


@dataclass
class PersonPlan:
    """The selected plan of one person, reduced to what the feasibility check needs."""

    person_id: str
    subpopulation: str
    activities: list = field(default_factory=list)   # (type, end_time_s or None, x, y)
    legs: list = field(default_factory=list)         # (mode, trav_time_s or None)


def parse_time_s(text: str) -> float:
    """Parse a MATSim HH:MM:SS time (hours may exceed 24) into seconds."""
    hours, minutes, seconds = text.split(":")
    return float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds)


def _open(path):
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def iter_person_plans(population_path) -> Iterator[PersonPlan]:
    """Stream the selected plan of every person; memory stays bounded (elements are cleared)."""
    with _open(population_path) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "person":
                continue
            subpopulation = RESIDENTS
            attributes = element.find("attributes")
            if attributes is not None:
                for attribute in attributes.findall("attribute"):
                    if attribute.get("name") == "subpopulation" and (attribute.text or "").strip():
                        subpopulation = attribute.text.strip()
            plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
            person = PersonPlan(person_id=element.get("id"), subpopulation=subpopulation)
            if plan is not None:
                for child in plan:
                    if child.tag == "activity":
                        end = child.get("end_time")
                        person.activities.append((child.get("type"), parse_time_s(end) if end else None,
                                                  float(child.get("x")), float(child.get("y"))))
                    elif child.tag == "leg":
                        travel = child.get("trav_time")
                        person.legs.append((child.get("mode"), parse_time_s(travel) if travel else None))
            element.clear()
            yield person


def leg_tour_frozen(activities: list) -> list:
    """Per leg (between consecutive activities): True when its home-based tour touches an outside activity."""
    n_legs = len(activities) - 1
    if n_legs <= 0:
        return []
    tour_of_leg, tour = [], 0
    for index in range(n_legs):
        if index > 0 and activities[index][0] == HOME:
            tour += 1
        tour_of_leg.append(tour)
    touches = {}
    for index in range(n_legs):
        kind = activities[index + 1][0] == OUTSIDE or activities[index][0] == OUTSIDE
        touches[tour_of_leg[index]] = touches.get(tour_of_leg[index], False) or kind
    return [touches[t] for t in tour_of_leg]


def _empty_block() -> dict:
    return {"n_persons": 0, "n_legs": 0, "n_plans_late": 0, "n_plans_non_monotonic": 0, "n_walk_legs_over": 0,
            "n_bicycle_legs_over": 0, "n_legs_over_duration": 0}


def summarise(plans, *, simulation_end_s: float, walk_max_distance_m: float, bicycle_max_distance_m: float,
              leg_max_duration_s: float) -> dict:
    """Count plan and leg defects per subpopulation and per class (frozen / in_region)."""
    blocks: dict = {}
    for person in plans:
        frozen_legs = leg_tour_frozen(person.activities)
        person_class = FROZEN if any(frozen_legs) else IN_REGION
        sub = blocks.setdefault(person.subpopulation, {FROZEN: _empty_block(), IN_REGION: _empty_block()})
        block = sub[person_class]
        block["n_persons"] += 1
        ends = [end for _, end, _, _ in person.activities if end is not None]
        if ends and max(ends) > simulation_end_s:
            block["n_plans_late"] += 1
        if any(later < earlier for earlier, later in zip(ends, ends[1:])):
            block["n_plans_non_monotonic"] += 1
        for index, (mode, travel) in enumerate(person.legs):
            leg_block = sub[FROZEN if frozen_legs[index] else IN_REGION]
            leg_block["n_legs"] += 1
            _, _, ox, oy = person.activities[index]
            _, _, dx, dy = person.activities[index + 1]
            distance = math.hypot(dx - ox, dy - oy)
            if mode == "walk" and distance > walk_max_distance_m:
                leg_block["n_walk_legs_over"] += 1
            if mode == "bicycle" and distance > bicycle_max_distance_m:
                leg_block["n_bicycle_legs_over"] += 1
            if travel is not None and travel > leg_max_duration_s:
                leg_block["n_legs_over_duration"] += 1
    for sub in blocks.values():
        for block in sub.values():
            n = block["n_persons"]
            block["share_plans_late"] = block["n_plans_late"] / n if n else 0.0
            block["share_plans_non_monotonic"] = block["n_plans_non_monotonic"] / n if n else 0.0
    return {"simulation_end_s": float(simulation_end_s),
            "thresholds": {"walk_max_distance_m": walk_max_distance_m, "bicycle_max_distance_m": bicycle_max_distance_m,
                           "leg_max_duration_s": leg_max_duration_s},
            "subpopulations": blocks}


def _read_config_param(config_path, module_name: str, param_name: str):
    """Return the value of the named param of the named config module, or None when absent."""
    root = ET.parse(config_path).getroot()
    for module in root.iter("module"):
        if module.get("name") == module_name:
            for param in module.findall("param"):
                if param.get("name") == param_name:
                    return param.get("value")
    return None


def read_qsim_end_time_s(config_path) -> float:
    """Read qsim.endTime of a written MATSim config in seconds."""
    value = _read_config_param(config_path, "qsim", "endTime")
    if value is None:
        raise ValueError(f"{_LOG_TAG} {config_path} carries no qsim.endTime")
    return parse_time_s(value)


def assert_qsim_end_time(config_path, expected_s: float) -> None:
    """Fail early when the simulated day of the config differs from the plan-time bound."""
    actual = read_qsim_end_time_s(config_path)
    if abs(actual - expected_s) > 1e-6:
        raise ValueError(f"{_LOG_TAG} qsim.endTime of {config_path} is {actual / 3600.0:.2f} h but the plan-time "
                         f"bound MAX_PLAN_TIME_SECONDS is {expected_s / 3600.0:.2f} h; the synthetic day and the "
                         "simulated day must agree (ADR-0146)")


def resolve_population_path(config_path) -> str:
    """Resolve plans.inputPlansFile of a written MATSim config against the directory of that config.

    The population file name is never assumed: the config is the single source of which file MATSim reads.
    """
    value = _read_config_param(config_path, "plans", "inputPlansFile")
    if not value:
        raise ValueError(f"{_LOG_TAG} {config_path} carries no plans.inputPlansFile; cannot locate the "
                         "written population to check")
    population_path = os.path.join(os.path.dirname(os.path.abspath(str(config_path))), value)
    if not os.path.exists(population_path):
        raise FileNotFoundError(f"{_LOG_TAG} plans.inputPlansFile of {config_path} points to {population_path}, "
                                "which does not exist")
    return population_path


def check_thresholds(summary: dict, max_frozen_defect_share: float) -> None:
    """Raise when the frozen resident plans exceed the allowed late / non-monotonic share."""
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    for key in ("share_plans_late", "share_plans_non_monotonic"):
        if frozen.get(key, 0.0) > max_frozen_defect_share:
            raise ValueError(f"{_LOG_TAG} frozen resident plans: {key} = {100.0 * frozen[key]:.2f}% of "
                             f"{frozen.get('n_persons', 0)} exceeds {100.0 * max_frozen_defect_share:.2f}%; the "
                             "tours the mode choice never re-chooses cannot be lived in the simulated day")


def _log_summary(summary: dict) -> None:
    for subpopulation, classes in summary["subpopulations"].items():
        for name, block in classes.items():
            logger.info("%s %s/%s: persons %d, late %d (%.2f%%), non-monotonic %d (%.2f%%), walk legs over %d, "
                        "bicycle legs over %d, legs over duration %d of %d", _LOG_TAG, subpopulation, name,
                        block["n_persons"], block["n_plans_late"], 100.0 * block["share_plans_late"],
                        block["n_plans_non_monotonic"], 100.0 * block["share_plans_non_monotonic"],
                        block["n_walk_legs_over"], block["n_bicycle_legs_over"], block["n_legs_over_duration"],
                        block["n_legs"])


def run(context, config_name: str) -> dict:
    """Check the written population of the prepare stage; write plan_check.json; raise above the thresholds."""
    directory = context.path()
    config_path = os.path.join(directory, config_name)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"{_LOG_TAG} written config not found: {config_path}")
    assert_qsim_end_time(config_path, MAX_PLAN_TIME_SECONDS)
    population_path = resolve_population_path(config_path)
    summary = summarise(iter_person_plans(population_path), simulation_end_s=MAX_PLAN_TIME_SECONDS,
                        walk_max_distance_m=float(context.config(KEY_WALK_MAX_DISTANCE_M)),
                        bicycle_max_distance_m=float(context.config(KEY_BICYCLE_MAX_DISTANCE_M)),
                        leg_max_duration_s=float(context.config(KEY_LEG_MAX_DURATION_S)))
    _log_summary(summary)
    with open(os.path.join(directory, PLAN_CHECK_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    check_thresholds(summary, float(context.config(KEY_MAX_FROZEN_DEFECT_SHARE)))
    return summary
