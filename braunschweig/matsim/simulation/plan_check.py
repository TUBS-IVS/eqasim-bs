"""Plan feasibility check on the written MATSim population, after the cut (eqasim-bs#443, ADR-0146).

Nothing between the population writer and MATSim used to check whether a plan can be lived in one simulated
day. This module streams the prepared population file (the file MATSim reads, after the cut and the freight
injection; located through the written config's ``plans.inputPlansFile``) and reports, per subpopulation and
per class, plans with an activity end time after the simulated day, plans whose last planned arrival (the last
leg's departure plus its travel time) is after the simulated day (report only), plans with non-monotonic
activity end times, walk and bicycle legs over a straight-line distance and legs over a duration. The class
``frozen`` holds the plans (person level) and legs (tour level) whose tour touches an ``outside`` activity --
the tours the discrete mode choice never re-chooses (``OutsideFilter``); ``in_region`` is informational,
because the mode choice repairs it. The threshold bounds the frozen residents' late plans, plans with a
planned lateness (non-monotonic plans) over ``non_monotonic_bound_min_lateness_s`` and pt-as-walk trips over
``pt_as_walk_bound_min_distance_m`` (ADR-0188, #576: shorter walks and smaller lateness are reported in bands, not
bounded; the totals stay reported). It is reported always and fails the stage only when
``braunschweig.plan_check.fail_on_threshold`` is true; freight is reported, never failed on (#444 owns its defects).

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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
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
#: ASSUMPTION (ADR-0146, ADR-0188): the share of frozen resident plans that may be late or have a planned lateness
#: over ``non_monotonic_bound_min_lateness_s``; the A/B calibrates it.
KEY_MAX_FROZEN_DEFECT_SHARE = "braunschweig.plan_check.max_frozen_defect_share"
DEFAULT_MAX_FROZEN_DEFECT_SHARE = 0.005
#: Leg attribute that eqasim stamps on a leg its router replaced by a walk (value: the mode that was requested,
#: ``pt``); the Java constant carries the same name (eqasim-bs#465).
WALK_FALLBACK_ATTRIBUTE = "walkFallbackFrom"
#: Leg attribute MATSim writes (plans v6) for the routing mode a trip was requested with; a trip with value ``pt``
#: is a pt-intended trip, whatever the router made of it.
ROUTING_MODE_ATTRIBUTE = "routingMode"
PT_MODE = "pt"
#: The jar entry whose constant pool holds the attribute name when the Java fork stamps the fallback legs
#: (``PlanRouter.WALK_FALLBACK_ATTRIBUTE``, eqasim-bs#465).
PLAN_ROUTER_CLASS_ENTRY = "org/eqasim/core/scenario/routing/PlanRouter.class"
#: The jar entry of the scenario cutter's plan cutter. It rebuilds every cut trip from fresh legs; when it keeps the
#: stamp of a walk-fallback trip, javac inlines ``PlanRouter.WALK_FALLBACK_ATTRIBUTE`` into its constant pool. A
#: cutter without the name drops the stamp of every trip it rebuilds (eqasim-bs#487).
PLAN_CUTTER_CLASS_ENTRY = "org/eqasim/core/scenario/cutter/population/PlanCutter.class"
#: The classes that must carry the stamp, with the issue that introduced each requirement.
STAMP_CLASS_ENTRIES = ((PLAN_ROUTER_CLASS_ENTRY, "#465"), (PLAN_CUTTER_CLASS_ENTRY, "#487"))
#: Leg modes that make a pt-intended trip "walk-only" (no pt leg was produced).
WALK_MODES = frozenset({"walk", "non_network_walk"})
#: Activity types ending like this are router-made trip stops (``pt interaction``); they do not end a trip.
INTERACTION_SUFFIX = "interaction"
#: ASSUMPTION (#465): the share of the pt-intended TRIPS (routingMode ``pt``) of frozen resident tours that may be
#: realised as walks over ``pt_as_walk_bound_min_distance_m`` (ADR-0188, #576). It is a threshold for flagging a
#: symptom (e.g. no timetable connection or a missing stop) for tours the mode choice never re-chooses, not a
#: reference value and not proof of a broken router.
KEY_MAX_FROZEN_PT_AS_WALK_SHARE = "braunschweig.plan_check.max_frozen_pt_as_walk_share"
DEFAULT_MAX_FROZEN_PT_AS_WALK_SHARE = 0.02
#: ASSUMPTION (ADR-0188, #576): the anchor of ``walk_max_distance_m`` (ADR-0146): a pt trip realised as a walk
#: below it is a plausible walk.
KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M = "braunschweig.plan_check.pt_as_walk_bound_min_distance_m"
DEFAULT_PT_AS_WALK_BOUND_MIN_DISTANCE_M = 5000.0
#: ASSUMPTION (ADR-0188, #576): the owner's tolerated planned lateness (2026-11-06): a non-monotonic plan whose
#: planned arrival at some activity is at most this much after that activity's planned end time is reported, not
#: bounded.
KEY_NON_MONOTONIC_BOUND_MIN_LATENESS_S = "braunschweig.plan_check.non_monotonic_bound_min_lateness_s"
DEFAULT_NON_MONOTONIC_BOUND_MIN_LATENESS_S = 900.0
#: Report-only bands (not config); half-open ``[lo, hi)``, labels ``0_1km`` ... ``10km_plus`` / ``0_1min`` ...
#: ``60min_plus``. A block always carries every label so the json shape is stable.
PT_AS_WALK_DISTANCE_BAND_EDGES_M = (1000.0, 2000.0, 5000.0, 10000.0)
PT_AS_WALK_DISTANCE_BAND_LABELS = ("0_1km", "1_2km", "2_5km", "5_10km", "10km_plus")
NON_MONOTONIC_LATENESS_BAND_EDGES_S = (60.0, 300.0, 900.0, 3600.0)
NON_MONOTONIC_LATENESS_BAND_LABELS =("0_1min", "1_5min", "5_15min", "15_60min", "60min_plus")
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
            "n_plans_non_monotonic_without_trav_time": 0,
            "n_walk_legs_over": 0, "n_bicycle_legs_over": 0, "n_legs_over_duration": 0,
            "n_pt_trips": 0, "n_pt_as_walk": 0, "n_pt_as_walk_attr_without_walk": 0,
            "pt_as_walk_by_hour": {},
            "pt_as_walk_by_distance_band": dict.fromkeys(PT_AS_WALK_DISTANCE_BAND_LABELS, 0), "n_pt_as_walk_long": 0,
            "non_monotonic_by_lateness_band": dict.fromkeys(NON_MONOTONIC_LATENESS_BAND_LABELS, 0),
            "n_plans_non_monotonic_large": 0}


def _band_label(value: float, edges: tuple, labels: tuple) -> str:
    """The label of the half-open band ``[lo, hi)`` that holds ``value`` (the last label is open-ended)."""
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def planned_lateness_s(person: PersonPlan) -> tuple:
    """The planned lateness of a plan and whether a leg lacked ``trav_time`` (counted as 0 s, a lower bound).

    The agent leaves activity 0 at its end time and reaches activity i at the departure from i-1 plus the leg's
    travel time. MATSim ends an activity whose end time has passed at once, so it leaves i at the later of arrival
    and end time (at arrival when i has no end time: zero dwell). The lateness is the largest ``arrival - end
    time`` (at least 0) over the activities with an end time; the last activity only receives an arrival.
    """
    lateness_s, without_trav_time, departure_s = 0.0, False, None
    for index, (_, end, _, _) in enumerate(person.activities):
        if index == 0:
            arrival_s = end
        else:
            travel = person.legs[index - 1][1] if index - 1 < len(person.legs) else None
            without_trav_time = without_trav_time or travel is None
            arrival_s = None if departure_s is None else departure_s + (travel or 0.0)
            if end is not None and arrival_s is not None:
                lateness_s = max(lateness_s, arrival_s - end)
        if end is None:
            departure_s = arrival_s
        else:
            departure_s = end if arrival_s is None else max(arrival_s, end)
    return lateness_s, without_trav_time


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
              leg_max_duration_s: float, pt_as_walk_bound_min_distance_m: float,
              non_monotonic_bound_min_lateness_s: float) -> dict:
    """Count plan and leg defects per subpopulation and per class (frozen / in_region).

    ``n_plans_late`` counts plans with an activity END time after the simulated day. ``n_plans_arrival_late``
    counts plans whose last planned arrival (end time of the last leg's origin activity plus the leg's travel
    time) is after the simulated day; a plan whose last leg lacks the end time or the travel time cannot be
    evaluated and is not counted. The latter is report only (it is not part of the failure rule).

    The pt counters are per TRIP (``split_trips``) and all of one block: ``n_pt_trips`` are the trips requested as pt
    (leg attribute ``routingMode`` = pt, or a leg stamped ``walkFallbackFrom`` = pt), assigned to the class of the
    trip's first leg; ``n_pt_as_walk`` the pt-intended trips realised as walk, in the same class (so numerator
    and denominator share one block), by either rule: a walk leg carries the ``walkFallbackFrom`` stamp (eqasim's
    ``PlanRouter``), or no leg carries the stamp, every leg is a walk mode and a leg has ``routingMode`` = pt (a pt
    trip the discrete mode choice's ``TripRouter`` realised as walk only; it sets ``routingMode`` but never the
    stamp, and the population read here is that re-routed one after eqasim-bs#542 / ADR-0178). The hour is that of
    the stamped walk leg, else of the trip's first leg; ``share_pt_as_walk`` = ``n_pt_as_walk / n_pt_trips``
    (reported, not bounded; 0 when the block has no pt-intended trip). Each pt-as-walk trip also gets a straight-line
    distance (origin of its first leg to destination of its last leg, as the walk-leg check measures): it is counted
    in ``pt_as_walk_by_distance_band`` (report only) and, when strictly above ``pt_as_walk_bound_min_distance_m``,
    in ``n_pt_as_walk_long``; ``share_pt_as_walk_long`` = ``n_pt_as_walk_long / n_pt_trips`` is the bounded share
    (ADR-0188). Likewise a non-monotonic plan gets its planned lateness: the plan is walked forward (leave activity
    0 at its end time; arrive at activity i at the departure from i-1 plus the leg's ``trav_time``; leave i at the
    later of arrival and end time, at arrival when i has no end), and the lateness is the largest ``max(0, arrival
    - end time)`` over the activities with an end time. It is counted in ``non_monotonic_by_lateness_band`` (report
    only) and, when strictly above ``non_monotonic_bound_min_lateness_s``, in ``n_plans_non_monotonic_large``;
    ``share_plans_non_monotonic_large`` = ``n_plans_non_monotonic_large / n_persons`` is the bounded share. A leg
    without ``trav_time`` counts as 0 s (the lateness is a lower bound); such plans are counted in
    ``n_plans_non_monotonic_without_trav_time`` (report only). The totals stay reported. A jar without the
    stamp makes ``n_pt_as_walk`` read 0 whatever the router did: ``require_walk_fallback_stamp`` guards that in
    ``run``. ``n_legs_with_routing_mode`` and ``n_pt_or_stamped_legs`` tell ``_log_summary`` when ``n_pt_trips`` is meaningless (no leg carries a
    ``routingMode`` although pt or stamped legs exist).
    """
    blocks: dict = {}
    n_legs_with_routing_mode = n_pt_or_stamped_legs = 0
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
        # Non-monotonic: some end time is earlier than an earlier one (ADR-0188).
        latest_end_s, non_monotonic = None, False
        for end in ends:
            if latest_end_s is not None and end < latest_end_s:
                non_monotonic = True
            latest_end_s = end if latest_end_s is None else max(latest_end_s, end)
        if non_monotonic:
            block["n_plans_non_monotonic"] += 1
            lateness_s, without_trav_time = planned_lateness_s(person)
            block["n_plans_non_monotonic_without_trav_time"] += without_trav_time
            block["non_monotonic_by_lateness_band"][_band_label(
                lateness_s, NON_MONOTONIC_LATENESS_BAND_EDGES_S, NON_MONOTONIC_LATENESS_BAND_LABELS)] += 1
            if lateness_s > non_monotonic_bound_min_lateness_s:
                block["n_plans_non_monotonic_large"] += 1
        for index, (mode, travel, walk_fallback_from, routing_mode) in enumerate(person.legs):
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
            n_legs_with_routing_mode += routing_mode is not None
            n_pt_or_stamped_legs += mode == PT_MODE or walk_fallback_from is not None
        for trip in split_trips(person.activities, len(person.legs)):
            trip_legs = [person.legs[index] for index in trip]
            if not any(leg[2] == PT_MODE or leg[3] == PT_MODE for leg in trip_legs):
                continue
            trip_block = sub[FROZEN if frozen_legs[trip[0]] else IN_REGION]
            trip_block["n_pt_trips"] += 1
            stamped_walk = next((index for index in trip
                                 if person.legs[index][2] is not None and person.legs[index][0] == "walk"), None)
            # The mode choice's own router (TripRouter) sets routingMode but never the stamp: a pt trip it realised
            # as walk legs only is a pt-as-walk trip too (eqasim-bs#542, ADR-0178).
            walk_only_pt = (not any(leg[2] is not None for leg in trip_legs)
                            and all(leg[0] in WALK_MODES for leg in trip_legs)
                            and any(leg[3] == PT_MODE for leg in trip_legs))
            if stamped_walk is not None or walk_only_pt:
                trip_block["n_pt_as_walk"] += 1
                _, _, trip_ox, trip_oy = person.activities[trip[0]]
                _, _, trip_dx, trip_dy = person.activities[trip[-1] + 1]
                trip_distance_m = math.hypot(trip_dx - trip_ox, trip_dy - trip_oy)
                trip_block["pt_as_walk_by_distance_band"][_band_label(
                    trip_distance_m, PT_AS_WALK_DISTANCE_BAND_EDGES_M, PT_AS_WALK_DISTANCE_BAND_LABELS)] += 1
                if trip_distance_m > pt_as_walk_bound_min_distance_m:
                    trip_block["n_pt_as_walk_long"] += 1
                departure = person.activities[trip[0] if stamped_walk is None else stamped_walk][1]
                hour = "unknown" if departure is None else str(int(departure // 3600.0))
                by_hour = trip_block["pt_as_walk_by_hour"]
                by_hour[hour] = by_hour.get(hour, 0) + 1
            # A stamp that survived on a leg that is no longer a walk (e.g. a later replanning changed its mode):
            # reported on its own, never part of the pt-as-walk counter.
            trip_block["n_pt_as_walk_attr_without_walk"] += sum(
                1 for index in trip if person.legs[index][2] is not None and person.legs[index][0] != "walk")
    for sub in blocks.values():
        for block in sub.values():
            n = block["n_persons"]
            block["share_plans_late"] = block["n_plans_late"] / n if n else 0.0
            block["share_plans_arrival_late"] = block["n_plans_arrival_late"] / n if n else 0.0
            block["share_plans_non_monotonic"] = block["n_plans_non_monotonic"] / n if n else 0.0
            block["share_plans_non_monotonic_large"] = block["n_plans_non_monotonic_large"] / n if n else 0.0
            block["share_pt_as_walk"] = block["n_pt_as_walk"] / block["n_pt_trips"] if block["n_pt_trips"] else 0.0
            block["share_pt_as_walk_long"] = (block["n_pt_as_walk_long"] / block["n_pt_trips"]
                                              if block["n_pt_trips"] else 0.0)
    return {"simulation_end_s": float(simulation_end_s), "n_legs_with_routing_mode": n_legs_with_routing_mode,
            "n_pt_or_stamped_legs": n_pt_or_stamped_legs,
            "thresholds": {"walk_max_distance_m": walk_max_distance_m, "bicycle_max_distance_m": bicycle_max_distance_m,
                           "leg_max_duration_s": leg_max_duration_s,
                           "pt_as_walk_bound_min_distance_m": pt_as_walk_bound_min_distance_m,
                           "non_monotonic_bound_min_lateness_s": non_monotonic_bound_min_lateness_s},
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
    return {"share_plans_late": max_frozen_defect_share, "share_plans_non_monotonic_large": max_frozen_defect_share,
            "share_pt_as_walk_long": max_frozen_pt_as_walk_share}


def find_threshold_violations(summary: dict, max_frozen_defect_share: float,
                              max_frozen_pt_as_walk_share: float) -> list:
    """Names of the failure-rule shares of the frozen resident block that exceed their allowed share.

    ``share_plans_late`` and ``share_plans_non_monotonic_large`` (share of persons; only plans with a planned
    lateness over ``non_monotonic_bound_min_lateness_s``) are compared with ``max_frozen_defect_share``;
    ``share_pt_as_walk_long`` (share of pt-intended trips; only pt-as-walk trips over
    ``pt_as_walk_bound_min_distance_m``) with ``max_frozen_pt_as_walk_share``. The all-trip / all-plan totals
    (``share_pt_as_walk``, ``share_plans_non_monotonic``) are reported but not bounded.
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    return [key for key, allowed in _allowed_shares(max_frozen_defect_share, max_frozen_pt_as_walk_share).items()
            if frozen.get(key, 0.0) > allowed]


def check_thresholds(summary: dict, max_frozen_defect_share: float, max_frozen_pt_as_walk_share: float, *,
                     fail_on_threshold: bool) -> list:
    """Apply the failure rule to the frozen resident plans and return the violated share names.

    Above an allowed share (late plans, plans with a planned lateness over the bound, long pt-as-walk trips;
    ADR-0188) the
    check raises when ``fail_on_threshold`` is true, and logs at ERROR (naming the shares) and continues otherwise:
    the threshold is an ASSUMPTION until the A/B of #443 calibrated it, so an uncalibrated threshold must not abort a run (ADR-0146). The ERROR is never silent.
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    violations = find_threshold_violations(summary, max_frozen_defect_share, max_frozen_pt_as_walk_share)
    allowed_shares = _allowed_shares(max_frozen_defect_share, max_frozen_pt_as_walk_share)
    for key in violations:
        unit, count_key = (("pt-intended trips", "n_pt_trips") if key == "share_pt_as_walk_long"
                           else ("persons", "n_persons"))
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
            # Only the frozen resident block is judged by the failure rule (check_thresholds).
            bounded = ", bounded" if (subpopulation, name) == (RESIDENTS, FROZEN) else ""
            logger.info("%s %s/%s: persons %d, activity end after the day %d (%.2f%%), last arrival after the day "
                        "%d (%.2f%%), non-monotonic %d (%.2f%%), planned lateness over %.0f s %d (%.2f%%%s), by lateness %s, "
                        "walk legs over %d, "
                        "bicycle legs over %d, legs over duration %d of %d, pt as walk %d of %d pt-intended trips "
                        "(%.2f%%), over %.0f m %d (%.2f%%%s), by distance %s, walk-fallback attribute "
                        "without walk %d, pt as walk by departure hour %s", _LOG_TAG, subpopulation, name,
                        block["n_persons"], block["n_plans_late"], 100.0 * block["share_plans_late"],
                        block["n_plans_arrival_late"], 100.0 * block["share_plans_arrival_late"],
                        block["n_plans_non_monotonic"], 100.0 * block["share_plans_non_monotonic"],
                        summary["thresholds"]["non_monotonic_bound_min_lateness_s"],
                        block["n_plans_non_monotonic_large"], 100.0 * block["share_plans_non_monotonic_large"],
                        bounded, block["non_monotonic_by_lateness_band"],
                        block["n_walk_legs_over"], block["n_bicycle_legs_over"], block["n_legs_over_duration"],
                        block["n_legs"], block["n_pt_as_walk"], block["n_pt_trips"], 100.0 * block["share_pt_as_walk"],
                        summary["thresholds"]["pt_as_walk_bound_min_distance_m"],
                        block["n_pt_as_walk_long"], 100.0 * block["share_pt_as_walk_long"], bounded,
                        block["pt_as_walk_by_distance_band"], block["n_pt_as_walk_attr_without_walk"],
                        _format_by_hour(block["pt_as_walk_by_hour"]))
    if summary["n_pt_or_stamped_legs"] and not summary["n_legs_with_routing_mode"]:
        logger.warning("%s %d legs have mode pt or carry the %s stamp, but no leg carries a %s attribute: the pt "
                       "trip counts (n_pt_trips, share_pt_as_walk) are meaningless for this population. Check "
                       "that the population was written with MATSim plans v6 routing modes.", _LOG_TAG,
                       summary["n_pt_or_stamped_legs"], WALK_FALLBACK_ATTRIBUTE, ROUTING_MODE_ATTRIBUTE)


def require_walk_fallback_stamp(jar_path) -> None:
    """Raise unless the eqasim jar at ``jar_path`` stamps the walk fallback and keeps the stamp through the cut.

    A jar built before the stamp was added (#465) writes no ``walkFallbackFrom`` leg attribute, and a jar whose
    scenario cutter rebuilds the cut trips without it (#487) keeps only the stamps of the cutter's own routing; in
    both cases ``n_pt_as_walk`` undercounts whatever the router did: a silent failure. The check opens every entry
    of ``STAMP_CLASS_ENTRIES`` and requires the attribute name ``WALK_FALLBACK_ATTRIBUTE`` (UTF-8, as the class
    constant pool holds it) in its bytes. Raises ``FileNotFoundError`` for a missing jar and ``RuntimeError`` for a
    file that is no zip archive, a jar without one of the classes, or a class without the name; the message names
    the jar, the class, the issue and the fix. Logs one INFO line on success.
    """
    path = Path(jar_path)
    if not path.is_file():
        raise FileNotFoundError(f"{_LOG_TAG} the eqasim jar to check for the walk fallback stamp does not exist: {path}")
    try:
        with zipfile.ZipFile(path) as jar:
            for entry, issue in STAMP_CLASS_ENTRIES:
                fix = ("rebuild the jar from an eqasim-java-bs revision with the walk fallback stamp (check it out in "
                       f"the tree eqasim_source_path points to), see eqasim-bs{issue}")
                try:
                    class_bytes = jar.read(entry)
                except KeyError:
                    raise RuntimeError(f"{_LOG_TAG} the eqasim jar {path} does not contain {entry}; {fix}") from None
                if WALK_FALLBACK_ATTRIBUTE.encode("utf-8") not in class_bytes:
                    raise RuntimeError(f"{_LOG_TAG} the eqasim jar {path} predates the walk fallback stamp: its "
                                       f"{entry} does not contain {WALK_FALLBACK_ATTRIBUTE}, so pt-to-walk fallbacks "
                                       f"would not be stamped or would lose the stamp and the pt-as-walk count would "
                                       f"undercount; {fix}")
    except zipfile.BadZipFile as error:
        raise RuntimeError(f"{_LOG_TAG} cannot read the eqasim jar {path} as a zip archive: {error}") from error
    logger.info("%s the eqasim jar %s stamps the walk fallback (%s found in %s)", _LOG_TAG, path,
                WALK_FALLBACK_ATTRIBUTE, ", ".join(entry for entry, _ in STAMP_CLASS_ENTRIES))


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
    # Resolved exactly as matsim.simulation.run and matsim.runtime.eqasim.run do.
    jar_path = "%s/%s" % (context.path("matsim.runtime.eqasim"), context.stage("matsim.runtime.eqasim"))
    require_walk_fallback_stamp(jar_path)
    population_path = resolve_population_path(config_path)
    summary = summarise(iter_person_plans(population_path), simulation_end_s=MAX_PLAN_TIME_SECONDS,
                        walk_max_distance_m=float(context.config(KEY_WALK_MAX_DISTANCE_M)),
                        bicycle_max_distance_m=float(context.config(KEY_BICYCLE_MAX_DISTANCE_M)),
                        leg_max_duration_s=float(context.config(KEY_LEG_MAX_DURATION_S)),
                        pt_as_walk_bound_min_distance_m=float(context.config(KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M)),
                        non_monotonic_bound_min_lateness_s=float(context.config(KEY_NON_MONOTONIC_BOUND_MIN_LATENESS_S)))
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
