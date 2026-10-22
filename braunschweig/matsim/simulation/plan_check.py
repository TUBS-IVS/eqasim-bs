"""Plan feasibility check on the written MATSim population, after the cut (eqasim-bs#443, ADR-0146).

Nothing between the population writer and MATSim used to check whether a plan can be lived in one simulated
day. This module streams the prepared population file (the file MATSim reads, after the cut and the freight
injection; located through the written config's ``plans.inputPlansFile``) and reports, per subpopulation and
per class, plans with an activity end time after the simulated day, plans whose last planned arrival (the last
leg's departure plus its travel time) is after the simulated day (report only), plans with non-monotonic
activity end times, walk and bicycle legs over a straight-line distance and legs over a duration. The class
``frozen`` holds the plans (person level) and legs (tour level) whose tour touches an ``outside`` activity --
the tours the discrete mode choice never re-chooses (``OutsideFilter``); ``in_region`` is informational,
because the mode choice repairs it. The threshold (late or non-monotonic share of the frozen residents) is
reported always and fails the stage only when ``braunschweig.plan_check.fail_on_threshold`` is true; freight is
reported, never failed on (#444 owns its defects).

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
#: ASSUMPTION (ADR-0146): anchored in the SrV table (srv2023_mode_by_distance_band.csv): the walk share is
#: below 1 % in every group beyond 5 km straight-line except ``other`` at 5-10 km (1.05 %); work at 5-10 km is
#: 0.16 %. Not 0 %: it is a threshold for counting legs, not a reference value.
KEY_WALK_MAX_DISTANCE_M = "braunschweig.plan_check.walk_max_distance_m"
DEFAULT_WALK_MAX_DISTANCE_M = 5000.0
#: ASSUMPTION (ADR-0146): rests on thin SrV evidence. The work bicycle share at 30-45 km is 0.064 %, which is
#: ONE unweighted trip (n_cell = 1 of 666); ``other`` at 20-30 km is 0.68 % (7 of 603 trips). 30 km is a choice
#: that counts legs, not a measured limit.
KEY_BICYCLE_MAX_DISTANCE_M = "braunschweig.plan_check.bicycle_max_distance_m"
DEFAULT_BICYCLE_MAX_DISTANCE_M = 30000.0
KEY_LEG_MAX_DURATION_S = "braunschweig.plan_check.leg_max_duration_s"
DEFAULT_LEG_MAX_DURATION_S = 10800.0
#: ASSUMPTION (ADR-0146): the share of frozen resident plans that may be late or non-monotonic; the A/B calibrates it.
KEY_MAX_FROZEN_DEFECT_SHARE = "braunschweig.plan_check.max_frozen_defect_share"
DEFAULT_MAX_FROZEN_DEFECT_SHARE = 0.005
#: Leg attribute that eqasim stamps on a leg its router replaced by a walk (value: the mode that was requested,
#: ``pt``); the Java constant carries the same name (eqasim-bs#465).
WALK_FALLBACK_ATTRIBUTE = "walkFallbackFrom"
#: Leg attribute MATSim writes (plans v6) for the routing mode a trip was requested with; a trip with value ``pt``
#: is a pt-intended trip, whatever the router made of it.
ROUTING_MODE_ATTRIBUTE = "routingMode"
PT_MODE = "pt"
#: Leg modes that make a pt-intended trip "walk-only" (no pt leg was produced).
WALK_MODES = frozenset({"walk", "non_network_walk"})
#: Activity types ending like this are router-made trip stops (``pt interaction``); they do not end a trip.
INTERACTION_SUFFIX = "interaction"
#: ASSUMPTION (#465): the share of the pt-intended TRIPS (routingMode ``pt``) of frozen resident tours that may be
#: realised as walks. It is a threshold for flagging a symptom (e.g. no timetable connection or a missing stop)
#: for tours the mode choice never re-chooses, not a reference value and not proof of a broken router.
KEY_MAX_FROZEN_PT_AS_WALK_SHARE = "braunschweig.plan_check.max_frozen_pt_as_walk_share"
DEFAULT_MAX_FROZEN_PT_AS_WALK_SHARE = 0.02
#: false -> above ``max_frozen_defect_share`` the check logs at ERROR and continues; true -> it raises. Default
#: false: the threshold is an ASSUMPTION and becomes a failure only after the A/B of #443 calibrated it (ADR-0146).
KEY_FAIL_ON_THRESHOLD = "braunschweig.plan_check.fail_on_threshold"
DEFAULT_FAIL_ON_THRESHOLD = False


@dataclass
class PersonPlan:
    """The selected plan of one person, reduced to what the feasibility check needs."""

    person_id: str
    subpopulation: str
    activities: list = field(default_factory=list)   # (type, end_time_s or None, x, y)
    # (mode, trav_time_s or None, walk_fallback_from or None, routing_mode or None)
    legs: list = field(default_factory=list)


def parse_time_s(text: str) -> float:
    """Parse a MATSim HH:MM:SS time (hours may exceed 24) into seconds."""
    hours, minutes, seconds = text.split(":")
    return float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds)


def _open(path):
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def _leg_attribute(leg, name: str) -> str | None:
    """The value of the leg attribute ``name`` (e.g. ``walkFallbackFrom``, ``routingMode``), or None when absent."""
    attributes = leg.find("attributes")
    if attributes is not None:
        for attribute in attributes.findall("attribute"):
            if attribute.get("name") == name and (attribute.text or "").strip():
                return attribute.text.strip()
    return None


def iter_person_plans(population_path) -> Iterator[PersonPlan]:
    """Stream the selected plan of every person; memory stays bounded.

    Each finished person is cleared AND detached from the root (``root.clear()``): clearing only the person
    leaves one empty element per person attached to the root, which grows with the population.
    """
    root = None
    with _open(population_path) as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = element
                continue
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
                        person.legs.append((child.get("mode"), parse_time_s(travel) if travel else None,
                                            _leg_attribute(child, WALK_FALLBACK_ATTRIBUTE),
                                            _leg_attribute(child, ROUTING_MODE_ATTRIBUTE)))
            element.clear()
            root.clear()
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
    return {"n_persons": 0, "n_legs": 0, "n_plans_late": 0, "n_plans_arrival_late": 0, "n_plans_non_monotonic": 0,
            "n_walk_legs_over": 0, "n_bicycle_legs_over": 0, "n_legs_over_duration": 0,
            "n_pt_trips": 0, "n_pt_as_walk": 0, "n_pt_as_walk_attr_without_walk": 0, "n_pt_unstamped_walk": 0,
            "pt_as_walk_by_hour": {}}


def split_trips(activities: list, n_legs: int) -> list:
    """The leg indices of each trip: consecutive legs joined by an ``... interaction`` activity form one trip.

    A pt trip routed with access and egress walks (``pt interaction`` stops in between) is ONE trip, so the pt
    trip counts below are per trip, not per leg.
    """
    trips, current = [], []
    for index in range(n_legs):
        current.append(index)
        if not activities[index + 1][0].endswith(INTERACTION_SUFFIX):
            trips.append(current)
            current = []
    if current:
        trips.append(current)
    return trips


def summarise(plans, *, simulation_end_s: float, walk_max_distance_m: float, bicycle_max_distance_m: float,
              leg_max_duration_s: float) -> dict:
    """Count plan and leg defects per subpopulation and per class (frozen / in_region).

    ``n_plans_late`` counts plans with an activity END time after the simulated day. ``n_plans_arrival_late``
    counts plans whose last planned arrival (end time of the last leg's origin activity plus the leg's travel
    time) is after the simulated day; a plan whose last leg lacks the end time or the travel time cannot be
    evaluated and is not counted. The latter is report only (it is not part of the failure rule).

    The pt counters are per TRIP (``split_trips``): ``n_pt_trips`` are the trips requested as pt (leg attribute
    ``routingMode`` = pt, or a leg stamped ``walkFallbackFrom`` = pt), assigned to the class of the trip's first
    leg; ``n_pt_as_walk`` the stamped walk legs; ``share_pt_as_walk`` = ``n_pt_as_walk / n_pt_trips`` (the
    threshold's share; 0 when the block has no pt-intended trip); ``share_pt_as_walk_of_legs`` the same count
    over all legs (report only). ``n_pt_unstamped_walk`` counts pt-intended trips that consist of walk legs only
    and carry no ``walkFallbackFrom`` stamp: a fallback the eqasim jar did not stamp (a jar without the stamp, or a
    router that returned a direct walk), so that a counter of 0 cannot be mistaken for "no fallback happened".
    """
    blocks: dict = {}
    n_pt_unstamped_walk = 0
    for person in plans:
        if len(person.legs) > max(len(person.activities) - 1, 0):
            raise ValueError(f"{_LOG_TAG} person {person.person_id}: {len(person.legs)} legs for "
                             f"{len(person.activities)} activities; a plan has at most activities - 1 legs "
                             "(corrupt written population)")
        frozen_legs = leg_tour_frozen(person.activities)
        person_class = FROZEN if any(frozen_legs) else IN_REGION
        sub = blocks.setdefault(person.subpopulation, {FROZEN: _empty_block(), IN_REGION: _empty_block()})
        block = sub[person_class]
        block["n_persons"] += 1
        ends = [end for _, end, _, _ in person.activities if end is not None]
        if ends and max(ends) > simulation_end_s:
            block["n_plans_late"] += 1
        if person.legs:
            last_departure = person.activities[len(person.legs) - 1][1]
            last_travel = person.legs[-1][1]
            if last_departure is not None and last_travel is not None \
                    and last_departure + last_travel > simulation_end_s:
                block["n_plans_arrival_late"] += 1
        if any(later < earlier for earlier, later in zip(ends, ends[1:])):
            block["n_plans_non_monotonic"] += 1
        for index, (mode, travel, walk_fallback_from, _routing_mode) in enumerate(person.legs):
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
            if walk_fallback_from is not None:
                if mode == "walk":
                    leg_block["n_pt_as_walk"] += 1
                    departure = person.activities[index][1]
                    hour = "unknown" if departure is None else str(int(departure // 3600.0))
                    by_hour = leg_block["pt_as_walk_by_hour"]
                    by_hour[hour] = by_hour.get(hour, 0) + 1
                else:
                    # The attribute survived but the leg is no longer a walk (e.g. a later replanning changed
                    # its mode): reported on its own, never part of the pt-as-walk counter.
                    leg_block["n_pt_as_walk_attr_without_walk"] += 1
        for trip in split_trips(person.activities, len(person.legs)):
            trip_legs = [person.legs[index] for index in trip]
            stamped = any(leg[2] is not None for leg in trip_legs)
            pt_intended = any(leg[2] == PT_MODE or leg[3] == PT_MODE for leg in trip_legs)
            if not pt_intended:
                continue
            trip_block = sub[FROZEN if frozen_legs[trip[0]] else IN_REGION]
            trip_block["n_pt_trips"] += 1
            if not stamped and all(leg[0] in WALK_MODES for leg in trip_legs):
                trip_block["n_pt_unstamped_walk"] += 1
                n_pt_unstamped_walk += 1
    for sub in blocks.values():
        for block in sub.values():
            n = block["n_persons"]
            block["share_plans_late"] = block["n_plans_late"] / n if n else 0.0
            block["share_plans_arrival_late"] = block["n_plans_arrival_late"] / n if n else 0.0
            block["share_plans_non_monotonic"] = block["n_plans_non_monotonic"] / n if n else 0.0
            block["share_pt_as_walk"] = block["n_pt_as_walk"] / block["n_pt_trips"] if block["n_pt_trips"] else 0.0
            block["share_pt_as_walk_of_legs"] = block["n_pt_as_walk"] / block["n_legs"] if block["n_legs"] else 0.0
    return {"simulation_end_s": float(simulation_end_s), "n_pt_unstamped_walk": n_pt_unstamped_walk,
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


def _allowed_shares(max_frozen_defect_share: float, max_frozen_pt_as_walk_share: float) -> dict:
    return {"share_plans_late": max_frozen_defect_share, "share_plans_non_monotonic": max_frozen_defect_share,
            "share_pt_as_walk": max_frozen_pt_as_walk_share}


def find_threshold_violations(summary: dict, max_frozen_defect_share: float,
                              max_frozen_pt_as_walk_share: float) -> list:
    """Names of the failure-rule shares of the frozen resident block that exceed their allowed share.

    ``share_plans_late`` and ``share_plans_non_monotonic`` (share of persons) are compared with
    ``max_frozen_defect_share``; ``share_pt_as_walk`` (share of pt-intended trips) with ``max_frozen_pt_as_walk_share``.
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    return [key for key, allowed in _allowed_shares(max_frozen_defect_share, max_frozen_pt_as_walk_share).items()
            if frozen.get(key, 0.0) > allowed]


def check_thresholds(summary: dict, max_frozen_defect_share: float, max_frozen_pt_as_walk_share: float, *,
                     fail_on_threshold: bool) -> list:
    """Apply the failure rule to the frozen resident plans and return the violated share names.

    Above the allowed late / non-monotonic share the check raises when ``fail_on_threshold`` is true, and logs
    at ERROR (naming the shares) and continues otherwise: the threshold is an ASSUMPTION until the A/B of #443
    calibrated it, so an uncalibrated threshold must not abort a run (ADR-0146). The ERROR is never silent.
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    violations = find_threshold_violations(summary, max_frozen_defect_share, max_frozen_pt_as_walk_share)
    allowed_shares = _allowed_shares(max_frozen_defect_share, max_frozen_pt_as_walk_share)
    for key in violations:
        unit, count_key = ("pt-intended trips", "n_pt_trips") if key == "share_pt_as_walk" else ("persons", "n_persons")
        message = (f"{_LOG_TAG} frozen resident plans: {key} = {100.0 * frozen[key]:.2f}% of "
                   f"{frozen.get(count_key, 0)} {unit} exceeds {100.0 * allowed_shares[key]:.2f}%; the "
                   "tours the mode choice never re-chooses cannot be lived in the simulated day")
        if fail_on_threshold:
            raise ValueError(message)
        logger.error("%s (%s=false: continuing)", message, KEY_FAIL_ON_THRESHOLD)
    return violations


def _format_by_hour(by_hour: dict) -> dict:
    """The per-hour counts ordered by numeric hour, with ``unknown`` last."""
    known = sorted((key for key in by_hour if key != "unknown"), key=int)
    return {key: by_hour[key] for key in known + (["unknown"] if "unknown" in by_hour else [])}


def _log_summary(summary: dict) -> None:
    for subpopulation, classes in summary["subpopulations"].items():
        for name, block in classes.items():
            logger.info("%s %s/%s: persons %d, activity end after the day %d (%.2f%%), last arrival after the day "
                        "%d (%.2f%%), non-monotonic %d (%.2f%%), walk legs over %d, "
                        "bicycle legs over %d, legs over duration %d of %d, pt as walk %d of %d pt-intended trips "
                        "(%.2f%%; %.2f%% of all legs), walk-fallback attribute without walk %d, pt-intended walk-only "
                        "trips without the stamp %d, pt as walk by departure hour %s", _LOG_TAG, subpopulation, name,
                        block["n_persons"], block["n_plans_late"], 100.0 * block["share_plans_late"],
                        block["n_plans_arrival_late"], 100.0 * block["share_plans_arrival_late"],
                        block["n_plans_non_monotonic"], 100.0 * block["share_plans_non_monotonic"],
                        block["n_walk_legs_over"], block["n_bicycle_legs_over"], block["n_legs_over_duration"],
                        block["n_legs"], block["n_pt_as_walk"], block["n_pt_trips"], 100.0 * block["share_pt_as_walk"],
                        100.0 * block["share_pt_as_walk_of_legs"], block["n_pt_as_walk_attr_without_walk"],
                        block["n_pt_unstamped_walk"], _format_by_hour(block["pt_as_walk_by_hour"]))
            if block["n_pt_unstamped_walk"]:
                logger.warning("%s %s/%s: %d pt-intended trips (routingMode pt) consist of walk legs only and carry "
                               "no %s stamp: a pt-to-walk fallback that is not counted as pt as walk. Likely cause: "
                               "the eqasim jar was built without the %s stamp (rebuild eqasim_source_path); "
                               "otherwise the router returned a direct walk. A pt-as-walk count of 0 is not "
                               "evidence while this count is above 0.", _LOG_TAG, subpopulation, name,
                               block["n_pt_unstamped_walk"], WALK_FALLBACK_ATTRIBUTE, WALK_FALLBACK_ATTRIBUTE)


def run(context, config_name: str) -> dict:
    """Check the written population of the prepare stage and write plan_check.json.

    The frozen-resident threshold raises only when ``braunschweig.plan_check.fail_on_threshold`` is true; the
    report is written first in either case, so it exists when the check raises.
    """
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
    max_frozen_defect_share = float(context.config(KEY_MAX_FROZEN_DEFECT_SHARE))
    max_frozen_pt_as_walk_share = float(context.config(KEY_MAX_FROZEN_PT_AS_WALK_SHARE))
    fail_on_threshold = bool(context.config(KEY_FAIL_ON_THRESHOLD))
    summary["threshold"] = {"max_frozen_defect_share": max_frozen_defect_share,
                            "max_frozen_pt_as_walk_share": max_frozen_pt_as_walk_share,
                            "fail_on_threshold": fail_on_threshold,
                            "exceeded": find_threshold_violations(summary, max_frozen_defect_share,
                                                                  max_frozen_pt_as_walk_share)}
    with open(os.path.join(directory, PLAN_CHECK_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    check_thresholds(summary, max_frozen_defect_share, max_frozen_pt_as_walk_share,
                     fail_on_threshold=fail_on_threshold)
    return summary
