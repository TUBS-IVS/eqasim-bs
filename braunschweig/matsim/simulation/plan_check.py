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

from braunschweig.matsim.simulation import mode_choice_reach
from braunschweig.matsim.simulation.mode_choice_reach import ReachSettings
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
#: planned arrival at each out-of-order activity is at most this much after that activity's planned end time is
#: reported, not bounded.
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
#: Name in ``threshold.exceeded`` when the share lifted by ``frozen_pt_walk_lift`` is above the pt-as-walk bound.
KEY_SHARE_LIFTED_FROZEN_PT_WALK = "share_lifted_frozen_pt_walk"
#: Name in ``threshold.exceeded`` when the lift report says the lifted walk and bicycle trips are above their bound.
KEY_SHARE_LIFTED_OTHER_MODES = "share_lifted_other_modes"
DEFAULT_FAIL_ON_THRESHOLD = False
WALK_MODE = "walk"
BICYCLE_MODE = "bicycle"


@dataclass
class PersonPlan:
    """The selected plan of one person, reduced to what the feasibility check needs."""

    person_id: str
    subpopulation: str
    activities: list = field(default_factory=list)   # (type, end_time_s or None, x, y)
    # (mode, trav_time_s or None, walk_fallback_from or None, routing_mode or None)
    legs: list = field(default_factory=list)


# Positions in the PersonPlan tuples, named for the reach classification (the older code indexes them directly).
ACTIVITY_TYPE_POSITION = 0
LEG_MODE_POSITION = 0
LEG_ROUTING_MODE_POSITION = 3


def parse_time_s(text: str) -> float:
    """Parse a MATSim HH:MM:SS time (hours may exceed 24) into seconds."""
    hours, minutes, seconds = text.split(":")
    return float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds)


def open_population(path):
    return gzip.open(path, "rb") if str(path).endswith(".gz") else open(path, "rb")


def leg_attribute(leg, name: str) -> str | None:
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
    with open_population(population_path) as handle:
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
                                            leg_attribute(child, WALK_FALLBACK_ATTRIBUTE),
                                            leg_attribute(child, ROUTING_MODE_ATTRIBUTE)))
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
            "n_plans_non_monotonic_large": 0,
            "n_trips_fixed_border": 0, "n_trips_fixed_vehicle": 0, "n_trips_fixed_length": 0,
            "n_unreached_unfit": 0, "n_walk_trips_over_reached": 0, "n_bicycle_trips_over_reached": 0}


def band_label(value: float, edges: tuple, labels: tuple) -> str:
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
    time`` (at least 0) over the OUT-OF-ORDER activities only: those whose end time lies below the latest earlier
    end time. A tight but in-order leg elsewhere in the plan is not a defect of the order and is not measured
    (ADR-0188); the delay an earlier out-of-order activity carries forward is. The last activity only receives an
    arrival.
    """
    lateness_s, without_trav_time, departure_s, latest_end_s = 0.0, False, None, None
    for index, (_, end, _, _) in enumerate(person.activities):
        if index == 0:
            arrival_s = end
        else:
            travel = person.legs[index - 1][1] if index - 1 < len(person.legs) else None
            without_trav_time = without_trav_time or travel is None
            arrival_s = None if departure_s is None else departure_s + (travel or 0.0)
            out_of_order = end is not None and latest_end_s is not None and end < latest_end_s
            if out_of_order and arrival_s is not None:
                lateness_s = max(lateness_s, arrival_s - end)
        if end is not None:
            latest_end_s = end if latest_end_s is None else max(latest_end_s, end)
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


# Reach classes of a leg: whether the discrete mode choice re-chooses the mode of its trip. The rules mirror the Java
# tour finder and filters (the [home, outside] finder, OutsideFilter, TourLengthFilter, and eqasim's finer
# IsolatedOutsideTrips finder with the vehicle rule for border tours); issue #678.
REACHED = "reached"
FIXED_BORDER = "fixed_border"
FIXED_VEHICLE = "fixed_vehicle"
FIXED_LENGTH = "fixed_length"
# Modes a border tour keeps fixed under the vehicle rule (a vehicle cannot be left behind at the border).
RESTRICTED_MODES = frozenset({"car", "bicycle"})
_REASON_COUNTERS = {FIXED_BORDER: "n_trips_fixed_border", FIXED_VEHICLE: "n_trips_fixed_vehicle",
                    FIXED_LENGTH: "n_trips_fixed_length"}


def home_tours(activities: list, trips: list) -> list:
    """Per trip (``split_trips``): the index of its home-based tour, cut at ``home`` only.

    A tour ends after a trip whose destination is ``home``; the trips before the first ``home`` (a plan that does not
    start at home) and after the last one form a tour of their own. The vehicle rule of ``leg_reach`` and the bicycle
    take-along of ``frozen_pt_walk_lift`` use this one split.
    """
    tour_of_trip, tour = [], 0
    for trip in trips:
        tour_of_trip.append(tour)
        if activities[trip[-1] + 1][ACTIVITY_TYPE_POSITION] == HOME:
            tour += 1
    return tour_of_trip


def leg_reach(activities: list, legs: list, *, isolate_outside_trips: bool, max_tour_length: int,
              restricted_modes: frozenset = RESTRICTED_MODES) -> list:
    """Per leg: REACHED, or the reason why the mode choice does not re-choose the trip of this leg.

    Works on trips (``split_trips``); every leg gets the class of its trip. A trip's mode is the routing mode of its
    first leg, or that leg's mode when it has none. A trip touches the border when its origin or destination activity
    is ``outside``. Base tours end after a trip whose destination is ``home`` or ``outside``. With
    ``isolate_outside_trips`` a base tour is cut into maximal runs of border trips and of non-border trips, else it
    is one run. A trip is, in this order:

    - ``FIXED_BORDER`` when its run starts or ends at ``outside``;
    - ``FIXED_VEHICLE`` when ``isolate_outside_trips`` is on, its mode is in ``restricted_modes`` and its home-based
      tour (cut at ``home`` only) has a border trip;
    - ``FIXED_LENGTH`` when its run has more than ``max_tour_length`` trips;
    - ``REACHED`` otherwise.
    """
    n_legs = max(len(activities) - 1, 0)
    if len(legs) != n_legs:
        raise ValueError(f"{_LOG_TAG} a plan with {len(activities)} activities needs {n_legs} legs, got {len(legs)}")
    if n_legs == 0:
        return []
    trips = split_trips(activities, n_legs)

    def origin(trip):
        return activities[trip[0]][ACTIVITY_TYPE_POSITION]

    def destination(trip):
        return activities[trip[-1] + 1][ACTIVITY_TYPE_POSITION]

    touches = [origin(trip) == OUTSIDE or destination(trip) == OUTSIDE for trip in trips]

    base_tours, current = [], []
    for position, trip in enumerate(trips):
        current.append(position)
        if destination(trip) in (HOME, OUTSIDE):
            base_tours.append(current)
            current = []
    if current:
        base_tours.append(current)

    runs = []
    for tour in base_tours:
        if not isolate_outside_trips:
            runs.append(tour)
            continue
        run = [tour[0]]
        for position in tour[1:]:
            if touches[position] == touches[run[-1]]:
                run.append(position)
            else:
                runs.append(run)
                run = [position]
        runs.append(run)

    tour_of_trip = home_tours(activities, trips)
    border_home_tours = {tour for tour, touch in zip(tour_of_trip, touches) if touch}

    trip_class = [None] * len(trips)
    for run in runs:
        run_border = origin(trips[run[0]]) == OUTSIDE or destination(trips[run[-1]]) == OUTSIDE
        for position in run:
            first_leg = legs[trips[position][0]]
            mode = first_leg[LEG_ROUTING_MODE_POSITION] or first_leg[LEG_MODE_POSITION]
            if run_border:
                trip_class[position] = FIXED_BORDER
            elif isolate_outside_trips and mode in restricted_modes and tour_of_trip[position] in border_home_tours:
                trip_class[position] = FIXED_VEHICLE
            elif len(run) > max_tour_length:
                trip_class[position] = FIXED_LENGTH
            else:
                trip_class[position] = REACHED

    reach = [None] * n_legs
    for position, trip in enumerate(trips):
        for leg_index in trip:
            reach[leg_index] = trip_class[position]
    return reach


def trip_is_pt_intended(trip_legs: list) -> bool:
    """True when a leg of the trip is stamped ``walkFallbackFrom`` = pt or carries the routing mode pt."""
    return any(leg[2] == PT_MODE or leg[LEG_ROUTING_MODE_POSITION] == PT_MODE for leg in trip_legs)


def trip_mode(trip_legs: list):
    """The mode of a trip as ``leg_reach`` reads it: the routing mode of its first leg, else that leg's mode."""
    return trip_legs[0][LEG_ROUTING_MODE_POSITION] or trip_legs[0][LEG_MODE_POSITION]


def trip_is_all_walk(trip_legs: list) -> bool:
    return all(leg[LEG_MODE_POSITION] in WALK_MODES for leg in trip_legs)


def unfit_mode(trip_legs: list, distance_m: float, *, pt_as_walk_bound_min_distance_m: float,
               walk_max_distance_m: float, bicycle_max_distance_m: float):
    """The mode a trip is lifted from when its mode does not fit its straight-line distance, else None.

    The single rule of the lift (``frozen_pt_walk_lift``) and of the plan check's unfit counters. ``distance_m`` is
    the straight line between the trip's activities and every bound is strict. A pt-intended trip is judged by the pt
    rule only: pt when all its legs are walk legs and the distance is above ``pt_as_walk_bound_min_distance_m``.
    Otherwise a walk trip (mode walk, only walk legs) above ``walk_max_distance_m`` is walk, and a bicycle trip above
    ``bicycle_max_distance_m`` is bicycle. Car and every other mode are never unfit.
    """
    if trip_is_pt_intended(trip_legs):
        return PT_MODE if trip_is_all_walk(trip_legs) and distance_m > pt_as_walk_bound_min_distance_m else None
    mode = trip_mode(trip_legs)
    if mode == WALK_MODE and trip_is_all_walk(trip_legs) and distance_m > walk_max_distance_m:
        return WALK_MODE
    if mode == BICYCLE_MODE and distance_m > bicycle_max_distance_m:
        return BICYCLE_MODE
    return None


def summarise(plans, *, simulation_end_s: float, walk_max_distance_m: float, bicycle_max_distance_m: float,
              leg_max_duration_s: float, pt_as_walk_bound_min_distance_m: float,
              non_monotonic_bound_min_lateness_s: float, reach: ReachSettings | None = None) -> dict:
    """Count plan and leg defects per subpopulation and per class (frozen / in_region).

    With ``reach`` (the settings of the mode choice LOOP) a leg is ``frozen`` when ``leg_reach`` says the mode choice
    does not re-choose its trip, and a person is frozen when at least one trip is not reached. Without ``reach`` the
    old tour rule applies (``leg_tour_frozen``) and the reason counters stay 0. Per TRIP, in the block of the trip's
    class: ``n_trips_fixed_border`` / ``n_trips_fixed_vehicle`` / ``n_trips_fixed_length`` (the reasons),
    ``n_unreached_unfit`` (an unreached trip whose mode does not fit its distance, ``unfit_mode``: the trips the lift
    must have repaired) and ``n_walk_trips_over_reached`` / ``n_bicycle_trips_over_reached`` (reached trips over the
    walk or bicycle distance bound; reported, the mode choice repairs them).

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
    - end time)`` over the out-of-order activities (end time below the latest earlier end; ``planned_lateness_s``).
    It is counted in ``non_monotonic_by_lateness_band`` (report
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
        if reach is None:
            frozen_legs, leg_classes = leg_tour_frozen(person.activities), None
        else:
            try:
                leg_classes = leg_reach(person.activities, person.legs,
                                        isolate_outside_trips=reach.isolate_outside_trips,
                                        max_tour_length=reach.max_tour_length)
            except ValueError as error:
                raise ValueError(f"{_LOG_TAG} person {person.person_id}: {error}") from error
            frozen_legs = [leg_class != REACHED for leg_class in leg_classes]
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
            block["non_monotonic_by_lateness_band"][band_label(
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
            trip_block = sub[FROZEN if frozen_legs[trip[0]] else IN_REGION]
            if leg_classes is not None and leg_classes[trip[0]] != REACHED:
                trip_block[_REASON_COUNTERS[leg_classes[trip[0]]]] += 1
            _, _, trip_ox, trip_oy = person.activities[trip[0]]
            _, _, trip_dx, trip_dy = person.activities[trip[-1] + 1]
            unfit = unfit_mode(trip_legs, math.hypot(trip_dx - trip_ox, trip_dy - trip_oy),
                               pt_as_walk_bound_min_distance_m=pt_as_walk_bound_min_distance_m,
                               walk_max_distance_m=walk_max_distance_m, bicycle_max_distance_m=bicycle_max_distance_m)
            if unfit is not None and frozen_legs[trip[0]]:
                trip_block["n_unreached_unfit"] += 1
            elif unfit == WALK_MODE:
                trip_block["n_walk_trips_over_reached"] += 1
            elif unfit == BICYCLE_MODE:
                trip_block["n_bicycle_trips_over_reached"] += 1
            if not any(leg[2] == PT_MODE or leg[3] == PT_MODE for leg in trip_legs):
                continue
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
                trip_distance_m = math.hypot(trip_dx - trip_ox, trip_dy - trip_oy)
                trip_block["pt_as_walk_by_distance_band"][band_label(
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
                              max_frozen_pt_as_walk_share: float, lift_report: dict | None = None) -> list:
    """Names of the failure-rule shares of the frozen resident block that exceed their allowed share.

    ``share_plans_late`` and ``share_plans_non_monotonic_large`` (share of persons; only plans with a planned
    lateness over ``non_monotonic_bound_min_lateness_s``) are compared with ``max_frozen_defect_share``;
    ``share_pt_as_walk_long`` (share of pt-intended trips; only pt-as-walk trips over
    ``pt_as_walk_bound_min_distance_m``) with ``max_frozen_pt_as_walk_share``. The all-trip / all-plan totals
    (``share_pt_as_walk``, ``share_plans_non_monotonic``) are reported but not bounded.

    ``lift_report`` is the report of ``frozen_pt_walk_lift`` (None when the lift is off): the trips it lifted leave the
    measured ``share_pt_as_walk_long``, so its ``share_lifted`` is compared with the same
    ``max_frozen_pt_as_walk_share`` and, above it, ``KEY_SHARE_LIFTED_FROZEN_PT_WALK`` is added to the names.
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    names = [key for key, allowed in _allowed_shares(max_frozen_defect_share, max_frozen_pt_as_walk_share).items()
             if frozen.get(key, 0.0) > allowed]
    if lift_report is not None and lift_report["share_lifted"] > max_frozen_pt_as_walk_share:
        names.append(KEY_SHARE_LIFTED_FROZEN_PT_WALK)
    if lift_report is not None and lift_report.get("bound_exceeded_other_modes"):
        names.append(KEY_SHARE_LIFTED_OTHER_MODES)
    return names


def check_thresholds(summary: dict, max_frozen_defect_share: float, max_frozen_pt_as_walk_share: float, *,
                     fail_on_threshold: bool, lift_report: dict | None = None) -> list:
    """Apply the failure rule to the frozen resident plans and return the violated share names.

    Above an allowed share (late plans, plans with a planned lateness over the bound, long pt-as-walk trips;
    ADR-0188) the
    check raises when ``fail_on_threshold`` is true, and logs at ERROR (naming the shares) and continues otherwise:
    the threshold is an ASSUMPTION until the A/B of #443 calibrated it, so an uncalibrated threshold must not abort a run (ADR-0146). The ERROR is never silent.
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    violations = find_threshold_violations(summary, max_frozen_defect_share, max_frozen_pt_as_walk_share, lift_report)
    allowed_shares = _allowed_shares(max_frozen_defect_share, max_frozen_pt_as_walk_share)
    messages = []
    for key in violations:
        if key == KEY_SHARE_LIFTED_FROZEN_PT_WALK:
            messages.append(f"{_LOG_TAG} frozen resident plans: {key} = {100.0 * lift_report['share_lifted']:.2f}% of "
                            f"{lift_report['n_pt_trips_frozen']} pt-intended trips (lifted to car_passenger by "
                            f"frozen_pt_walk_lift, no longer in share_pt_as_walk_long) exceeds "
                            f"{100.0 * max_frozen_pt_as_walk_share:.2f}%; the router finds no pt connection for them")
            continue
        if key == KEY_SHARE_LIFTED_OTHER_MODES:
            messages.append(f"{_LOG_TAG} resident plans: {key} = {100.0 * lift_report['share_lifted_other_modes']:.3f}% "
                            f"of the resident trips (walk and bicycle trips lifted to car_passenger by "
                            f"frozen_pt_walk_lift) exceeds {100.0 * lift_report['max_share_other_modes']:.3f}%; many "
                            "trips the mode choice does not reach have a mode that does not fit their distance")
            continue
        unit, count_key = (("pt-intended trips", "n_pt_trips") if key == "share_pt_as_walk_long"
                           else ("persons", "n_persons"))
        messages.append(f"{_LOG_TAG} frozen resident plans: {key} = {100.0 * frozen[key]:.2f}% of "
                        f"{frozen.get(count_key, 0)} {unit} exceeds {100.0 * allowed_shares[key]:.2f}%; the "
                        "tours the mode choice never re-chooses cannot be lived in the simulated day")
    if messages and fail_on_threshold:
        # One failure names every exceeded share.
        raise ValueError("; ".join(messages))
    for message in messages:
        logger.error("%s (%s=false: continuing)", message, KEY_FAIL_ON_THRESHOLD)
    return violations


def check_unreached_invariant(summary: dict, *, lift_all_modes: bool) -> None:
    """Stop the run when a resident trip the mode choice does not reach keeps a mode that does not fit its distance.

    After the lift no unreached resident trip may be ``unfit`` (``unfit_mode``): the lift repairs exactly those. With
    ``lift_all_modes`` (the lift covers pt, walk and bicycle) a remaining one means a lift gap or a rule drift: the
    check raises whatever ``fail_on_threshold`` says. With the lift limited to the pt rule the unfit walk and
    bicycle trips are expected: the check only logs them at ERROR (never silent).
    """
    frozen = summary["subpopulations"].get(RESIDENTS, {}).get(FROZEN, _empty_block())
    count = frozen["n_unreached_unfit"]
    if not count:
        return
    message = (f"{_LOG_TAG} {count} resident trips the mode choice does not reach keep a mode that does not fit "
               "their distance (n_unreached_unfit)")
    if lift_all_modes:
        raise ValueError(f"{message}; the lift covers all modes, so none may remain: a gap in frozen_pt_walk_lift or "
                         "a rule that drifted from the plan check")
    logger.error("%s; the lift covers only the pt rule (all_modes=false): continuing", message)


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
                        "%d (%.2f%%), non-monotonic %d (%.2f%%), planned lateness over %.0f s %d (%.2f%%%s), "
                        "by lateness %s, lateness with a leg without trav_time (lower bound) %d, walk legs over %d, "
                        "bicycle legs over %d, legs over duration %d of %d, pt as walk %d of %d pt-intended trips "
                        "(%.2f%%), over %.0f m %d (%.2f%%%s), by distance %s, walk-fallback attribute "
                        "without walk %d, pt as walk by departure hour %s", _LOG_TAG, subpopulation, name,
                        block["n_persons"], block["n_plans_late"], 100.0 * block["share_plans_late"],
                        block["n_plans_arrival_late"], 100.0 * block["share_plans_arrival_late"],
                        block["n_plans_non_monotonic"], 100.0 * block["share_plans_non_monotonic"],
                        summary["thresholds"]["non_monotonic_bound_min_lateness_s"],
                        block["n_plans_non_monotonic_large"], 100.0 * block["share_plans_non_monotonic_large"],
                        bounded, block["non_monotonic_by_lateness_band"],
                        block["n_plans_non_monotonic_without_trav_time"], block["n_walk_legs_over"], block["n_bicycle_legs_over"], block["n_legs_over_duration"],
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


def run(context, config_name: str, lift_report: dict | None = None) -> dict:
    """Check the written population of the prepare stage and write plan_check.json.

    ``lift_report`` (the return value of ``frozen_pt_walk_lift.run``, None when the lift is off) adds the lifted
    share to ``threshold.exceeded`` as ``KEY_SHARE_LIFTED_FROZEN_PT_WALK`` when it is above the bound; with None the
    written file is unchanged.

    The frozen-resident threshold raises only when ``braunschweig.plan_check.fail_on_threshold`` is true; the
    report is written first in either case, so it exists when the check raises.
    """
    directory = context.path()
    config_path = os.path.join(directory, config_name)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"{_LOG_TAG} written config not found: {config_path}")
    # A lift report without "all_modes" would silently turn the invariant's raise into an ERROR line: refuse it.
    if lift_report is not None and "all_modes" not in lift_report:
        raise ValueError(f"{_LOG_TAG} the lift report has no 'all_modes' key (keys: {sorted(lift_report)}); "
                         "pass the report frozen_pt_walk_lift.run returns")
    assert_qsim_end_time(config_path, MAX_PLAN_TIME_SECONDS)
    # Resolved exactly as matsim.simulation.run and matsim.runtime.eqasim.run do.
    jar_path = "%s/%s" % (context.path("matsim.runtime.eqasim"), context.stage("matsim.runtime.eqasim"))
    require_walk_fallback_stamp(jar_path)
    population_path = resolve_population_path(config_path)
    # The settings of the mode choice LOOP: what the trips of the written population can still be re-chosen by.
    reach = mode_choice_reach.loop_settings(context)
    summary = summarise(iter_person_plans(population_path), simulation_end_s=MAX_PLAN_TIME_SECONDS,
                        walk_max_distance_m=float(context.config(KEY_WALK_MAX_DISTANCE_M)),
                        bicycle_max_distance_m=float(context.config(KEY_BICYCLE_MAX_DISTANCE_M)),
                        leg_max_duration_s=float(context.config(KEY_LEG_MAX_DURATION_S)),
                        pt_as_walk_bound_min_distance_m=float(context.config(KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M)),
                        non_monotonic_bound_min_lateness_s=float(context.config(KEY_NON_MONOTONIC_BOUND_MIN_LATENESS_S)),
                        reach=reach)
    _log_summary(summary)
    max_frozen_defect_share = float(context.config(KEY_MAX_FROZEN_DEFECT_SHARE))
    max_frozen_pt_as_walk_share = float(context.config(KEY_MAX_FROZEN_PT_AS_WALK_SHARE))
    fail_on_threshold = bool(context.config(KEY_FAIL_ON_THRESHOLD))
    summary["threshold"] = {"max_frozen_defect_share": max_frozen_defect_share,
                            "max_frozen_pt_as_walk_share": max_frozen_pt_as_walk_share,
                            "fail_on_threshold": fail_on_threshold,
                            "exceeded": find_threshold_violations(summary, max_frozen_defect_share,
                                                                  max_frozen_pt_as_walk_share, lift_report)}
    if lift_report is not None:
        summary["threshold"]["share_lifted_frozen_pt_walk"] = lift_report["share_lifted"]
        if "share_lifted_other_modes" in lift_report:
            summary["threshold"][KEY_SHARE_LIFTED_OTHER_MODES] = lift_report["share_lifted_other_modes"]
    # The lift's own report says whether it covered all modes (None report: the lift is off or ran nothing).
    lift_all_modes = bool(lift_report is not None and lift_report["all_modes"])
    summary["reach"] = {"isolate_outside_trips": reach.isolate_outside_trips, "max_tour_length": reach.max_tour_length,
                        "lift_all_modes": lift_all_modes}
    with open(os.path.join(directory, PLAN_CHECK_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    # The report exists when either check raises.
    check_thresholds(summary, max_frozen_defect_share, max_frozen_pt_as_walk_share,
                     fail_on_threshold=fail_on_threshold, lift_report=lift_report)
    check_unreached_invariant(summary, lift_all_modes=lift_all_modes)
    return summary
