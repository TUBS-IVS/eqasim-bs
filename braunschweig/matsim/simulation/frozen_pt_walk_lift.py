"""Lift the long pt-as-walk trips of frozen resident tours to car_passenger in prepare (eqasim-bs#575, ADR-0189).

A home-based tour that touches an ``outside`` activity after the scenario cut is frozen for the discrete mode choice
(``plan_check.leg_tour_frozen``). A pt-intended trip of such a tour that the router turned into a walk (no
connection) would stay a walk of many kilometres in every iteration. This step runs after the cut and before the
plan check and replaces each such trip whose straight-line distance is STRICTLY above
``braunschweig.plan_check.pt_as_walk_bound_min_distance_m`` (the bound of the plan check; no second number) by ONE
leg ``car_passenger`` (routingMode ``car_passenger``, the original ``dep_time``, no route, no ``trav_time``, leg
attribute ``liftedFrom`` = ``pt``). The lifted leg has no ``trav_time`` and no route: MATSim's PrepareForSim routes
it at the start (``car_passenger`` is a routing network mode in this config: ``routing.networkModes`` includes it,
``accessEgressType`` is ``accessEgressModeToLink``). PrepareForSim routes the WHOLE plan of a person with a route-less
leg, so the person's other trips are routed again too (effect measured in the run manifest of ADR-0189).
``plan_check.planned_lateness_s`` counts a missing
``trav_time`` as 0 s, so the planned lateness measured after this step is a lower bound. ASSUMPTION (ADR-0156,
ADR-0187): a trip that no timetable serves is made as a lift, not as a walk of many kilometres.

Selected plan only: prepare writes one plan per person, so the selected plan (the first plan when none is marked
``selected="yes"``, as ``plan_check`` reads it) is the plan. Any further plan of a person is copied unchanged.

Scope: residents only (no ``subpopulation`` attribute or ``plan_check.RESIDENTS``). In-commuters and freight keep
their plans: they have their own strategies and scope. Activities and their end times stay unchanged; only the
``interaction`` activities inside a lifted trip disappear with its legs. The step is idempotent (a lifted leg is
not a walk).

The detection rules (trip split, tour freeze, pt-intended, walk modes) are those of ``plan_check``; they are
imported, not copied.

Wider repair (#678). With reach settings (``mode_choice_reach.ReachSettings``, the settings of the mode choice LOOP)
the scope is every resident trip whose ``plan_check.leg_reach`` class is not ``REACHED`` (fixed at the border, as a
vehicle leg, or by tour length) instead of the trips of frozen tours. With ``all_modes`` two more cases are lifted the
same way (``liftedFrom`` = the original mode): a walk trip (mode walk, only walk legs) whose straight line is strictly
above ``plan_check.KEY_WALK_MAX_DISTANCE_M``, and a bicycle trip strictly above
``plan_check.KEY_BICYCLE_MAX_DISTANCE_M``. A bicycle is a vehicle: when a bicycle trip is lifted, every other bicycle
trip of its home-based tour (split at ``home`` only) is lifted with it, whatever its distance and reach class, so no
later trip needs the bicycle left behind (counted as ``n_lifted_with_tour``). Car trips are never lifted. A trip's
mode is that of ``plan_check.leg_reach``: the routing mode of its first leg, else that leg's mode; a pt-intended trip
is judged by the pt rule only. Without reach settings and without ``all_modes`` the output is today's.
``max_share_other_modes`` bounds the lifted walk and bicycle trips over all resident trips (ERROR above it; the
plan check owns the failure, as for the pt share).

XML approach: the file is streamed with ``iterparse`` (memory stays bounded). The header up to and including the
``<population ...>`` start tag is copied as raw text (XML declaration, DOCTYPE and population attributes stay as
they are); every element below the root is then re-serialised from the parsed tree, one at a time. A person that is
not repaired keeps all its elements, attributes and attribute order, but not its original whitespace. Re-serialising
was chosen over a line-based text edit because the output of the MATSim writers is not guaranteed to be
pretty-printed one element per line.

Observability: one log line with the pt lift rate (ERROR above ``max_share``, which is the plan check's bound
``plan_check.KEY_MAX_FROZEN_PT_AS_WALK_SHARE``, else INFO), one with the scope and the lifted walk and bicycle trips
by reason (ERROR above ``max_share_other_modes``, else INFO), and ``frozen_pt_walk_lift.json``. Residents whose
selected plan has no leg are skipped and counted (``n_persons_without_legs``).
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from braunschweig.matsim.simulation import mode_choice_reach, plan_check
from braunschweig.matsim.simulation.mode_choice_reach import ReachSettings
from braunschweig.matsim.simulation.plan_check import (
    BICYCLE_MODE, FIXED_BORDER, FIXED_LENGTH, FIXED_VEHICLE, PT_MODE, REACHED, RESIDENTS, ROUTING_MODE_ATTRIBUTE,
    WALK_FALLBACK_ATTRIBUTE, WALK_MODE)

logger = logging.getLogger(__name__)

_LOG_TAG = "[frozen_pt_walk_lift]"
REPORT_FILE_NAME = "frozen_pt_walk_lift.json"
KEY_ENABLED = "braunschweig.frozen_pt_walk_lift.enabled"
DEFAULT_ENABLED = True
# Also lift long walk and bicycle trips, with the scope of the loop's reach settings (#678); false = the pt rule on
# frozen tours only, the population byte-identical to the lift before #678.
KEY_ALL_MODES = "braunschweig.frozen_pt_walk_lift.all_modes"
DEFAULT_ALL_MODES = True
# Bound on lifted walk and bicycle trips / all resident trips (share, 0..1). ASSUMPTION; reason: ADR-0206 Rationale.
KEY_MAX_SHARE_OTHER_MODES = "braunschweig.frozen_pt_walk_lift.max_share_other_modes"
DEFAULT_MAX_SHARE_OTHER_MODES = 0.002
WALK_FALLBACK_POSITION = 2   # position of the walkFallbackFrom value in the plan_check leg tuples
LIFTED_FROM_MODES = (PT_MODE, WALK_MODE, BICYCLE_MODE)
UNREACHED_REASONS = (FIXED_BORDER, FIXED_VEHICLE, FIXED_LENGTH)
LIFTED_MODE = "car_passenger"
LIFTED_FROM_ATTRIBUTE = "liftedFrom"
_POPULATION_START_TAG = re.compile(rb"<population\b[^>]*>")
_XML_ENCODING = re.compile(rb"^\s*<\?xml[^>]*\bencoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")


def _string_attribute(name: str, value: str) -> ET.Element:
    element = ET.Element("attribute", {"name": name, "class": "java.lang.String"})
    element.text = value
    return element


def _subpopulation(person: ET.Element) -> str:
    attributes = person.find("attributes")
    if attributes is not None:
        for attribute in attributes.findall("attribute"):
            if attribute.get("name") == "subpopulation" and (attribute.text or "").strip():
                return attribute.text.strip()
    return RESIDENTS


def _lifted_leg(departure_time: str | None, lifted_from: str) -> ET.Element:
    leg = ET.Element("leg", {"mode": LIFTED_MODE})
    if departure_time:
        leg.set("dep_time", departure_time)
    attributes = ET.SubElement(leg, "attributes")
    attributes.append(_string_attribute(ROUTING_MODE_ATTRIBUTE, LIFTED_MODE))
    attributes.append(_string_attribute(LIFTED_FROM_ATTRIBUTE, lifted_from))
    return leg


@dataclass(frozen=True)
class _LiftRules:
    min_distance_m: float
    reach: ReachSettings | None
    all_modes: bool
    walk_max_distance_m: float
    bicycle_max_distance_m: float


def _scope_classes(person: ET.Element, activities: list, leg_tuples: list, reach: ReachSettings | None) -> list:
    """Per leg: ``REACHED`` (out of scope) or the reason the loop does not reach its trip.

    Without reach settings the scope is the old tour rule (``plan_check.leg_tour_frozen``), whose trips are reported
    as ``FIXED_BORDER``: a home-based tour touching ``outside`` is the border class of the coarse rule without the
    length filter.
    """
    if reach is None:
        return [FIXED_BORDER if frozen else REACHED for frozen in plan_check.leg_tour_frozen(activities)]
    try:
        return plan_check.leg_reach(activities, leg_tuples, isolate_outside_trips=reach.isolate_outside_trips,
                                    max_tour_length=reach.max_tour_length)
    except ValueError as error:
        raise ValueError(f"{_LOG_TAG} person {person.get('id')}: {error}") from error


def _repair_person(person: ET.Element, rules: _LiftRules, counts: dict) -> None:
    """Count the trips of one resident the mode choice does not reach and lift the unfitting ones in place."""
    plan = next((p for p in person.findall("plan") if p.get("selected") == "yes"), person.find("plan"))
    if plan is None:
        counts["n_persons_without_legs"] += 1
        return
    children = list(plan)
    activities, activity_positions, leg_positions = [], [], []
    for position, child in enumerate(children):
        if child.tag == "activity":
            end = child.get("end_time")
            if child.get("x") is None or child.get("y") is None:
                raise ValueError(f"{_LOG_TAG} person {person.get('id')}: activity {len(activities)} "
                                 f"({child.get('type')}) has no x/y coordinates; the straight-line distance of a "
                                 "trip cannot be measured (a population written with link or facility only is "
                                 "not supported)")
            activities.append((child.get("type"), plan_check.parse_time_s(end) if end else None,
                               float(child.get("x")), float(child.get("y"))))
            activity_positions.append(position)
        elif child.tag == "leg":
            leg_positions.append(position)
    n_legs = len(leg_positions)
    if n_legs > max(len(activities) - 1, 0):
        raise ValueError(f"{_LOG_TAG} person {person.get('id')}: {n_legs} legs for {len(activities)} activities; "
                         "a plan has at most activities - 1 legs (corrupt written population)")
    if n_legs == 0:
        counts["n_persons_without_legs"] += 1
        return
    all_legs = [children[position] for position in leg_positions]
    leg_tuples = [(leg.get("mode"), None, plan_check.leg_attribute(leg, WALK_FALLBACK_ATTRIBUTE),
                   plan_check.leg_attribute(leg, ROUTING_MODE_ATTRIBUTE)) for leg in all_legs]
    classes = _scope_classes(person, activities, leg_tuples, rules.reach)
    trips = plan_check.split_trips(activities, n_legs)
    counts["n_resident_trips"] += len(trips)

    lifted = {}                  # trip position -> original mode it is lifted from
    bicycle_trips_by_tour = {}   # home-based tour index -> positions of its bicycle trips
    lifted_bicycle_tours = set()
    tour_of_trip = plan_check.home_tours(activities, trips)
    for position, trip in enumerate(trips):
        home_tour = tour_of_trip[position]
        legs = [leg_tuples[index] for index in trip]
        stamped = any(leg[WALK_FALLBACK_POSITION] == PT_MODE for leg in legs)
        pt_intended = plan_check.trip_is_pt_intended(legs)
        mode = plan_check.trip_mode(legs)
        if not pt_intended and mode == BICYCLE_MODE:
            bicycle_trips_by_tour.setdefault(home_tour, []).append(position)
        reason = classes[trip[0]]
        if reason == REACHED:
            continue
        counts["n_unreached_trips"] += 1
        counts["n_unreached_by_reason"][reason] += 1
        _, _, ox, oy = activities[trip[0]]
        _, _, dx, dy = activities[trip[-1] + 1]
        distance_m = math.hypot(dx - ox, dy - oy)
        all_walk = plan_check.trip_is_all_walk(legs)
        # The one rule of "the mode does not fit the distance": the plan check counts what is left of it.
        unfit = plan_check.unfit_mode(legs, distance_m, pt_as_walk_bound_min_distance_m=rules.min_distance_m,
                                      walk_max_distance_m=rules.walk_max_distance_m,
                                      bicycle_max_distance_m=rules.bicycle_max_distance_m)
        if pt_intended:
            # A pt-intended trip is judged by the pt rule only (today's rule), never as a walk.
            counts["n_pt_trips_frozen"] += 1
            if not all_walk:
                continue
            counts["n_pt_as_walk_frozen"] += 1
            if unfit != PT_MODE:
                continue
            counts["n_lifted"] += 1
            counts["n_lifted_stamped" if stamped else "n_lifted_routing_mode_only"] += 1
            counts["lifted_by_distance_band"][plan_check.band_label(
                distance_m, plan_check.PT_AS_WALK_DISTANCE_BAND_EDGES_M,
                plan_check.PT_AS_WALK_DISTANCE_BAND_LABELS)] += 1
            lifted[position] = PT_MODE
        elif not rules.all_modes or unfit is None:
            continue
        else:
            lifted[position] = unfit
            if unfit == BICYCLE_MODE:
                lifted_bicycle_tours.add(home_tour)
        counts["n_lifted_by_mode"][lifted[position]] += 1
        counts["n_lifted_by_reason"][reason] += 1

    # The bicycle stays with its rider: the other bicycle trips of a tour with a lifted bicycle trip go along, also
    # those before it, each reported under its own reach class.
    for tour in sorted(lifted_bicycle_tours):
        for position in bicycle_trips_by_tour[tour]:
            if position in lifted:
                continue
            lifted[position] = BICYCLE_MODE
            reason = classes[trips[position][0]]
            if reason == REACHED:
                # Outside the step's scope. The reach rules make it impossible: a home tour without a border trip is
                # one run, so all its trips share one class, and in a border home tour every bicycle trip is fixed at
                # the border or as a vehicle leg. Kept visible, not raised, under the key "reached".
                logger.warning("%s person %s: bicycle trip %d is lifted with its tour although the mode choice "
                               "reaches it", _LOG_TAG, person.get("id"), position)
            counts["n_lifted_with_tour"] += 1
            counts["n_lifted_by_mode"][BICYCLE_MODE] += 1
            counts["n_lifted_by_reason"][reason] = counts["n_lifted_by_reason"].get(reason, 0) + 1

    replacements = [(leg_positions[trips[position][0]], leg_positions[trips[position][-1]],
                     _lifted_leg(all_legs[trips[position][0]].get("dep_time"), lifted_from))
                    for position, lifted_from in sorted(lifted.items())]
    for first, last, new_leg in reversed(replacements):
        children[first:last + 1] = [new_leg]
    if replacements:
        for child in list(plan):
            plan.remove(child)
        plan.extend(children)


def lift_frozen_pt_walks(source_path, target_path, *, min_distance_m: float, max_share: float,
                         reach: ReachSettings | None = None, all_modes: bool = False,
                         walk_max_distance_m: float = plan_check.DEFAULT_WALK_MAX_DISTANCE_M,
                         bicycle_max_distance_m: float = plan_check.DEFAULT_BICYCLE_MAX_DISTANCE_M,
                         max_share_other_modes: float = DEFAULT_MAX_SHARE_OTHER_MODES) -> dict:
    """Write ``target_path`` as ``source_path`` with the unfitting trips the mode choice does not reach lifted.

    The scope is the trips of frozen resident tours without ``reach``, and every resident trip whose
    ``plan_check.leg_reach`` class under ``reach`` is not ``REACHED`` with it; "frozen" in the pt key names below
    means "in scope". Returns the report: ``n_persons`` (residents examined), ``n_persons_without_legs`` (of them
    skipped: no plan or no leg), ``n_resident_trips``, ``n_unreached_trips`` (in scope) and
    ``n_unreached_by_reason``; ``n_pt_trips_frozen`` (pt-intended trips in scope), ``n_pt_as_walk_frozen`` (of them
    the all-walk trips, at any distance), ``n_lifted`` (of those the trips over ``min_distance_m``),
    ``n_lifted_stamped`` / ``n_lifted_routing_mode_only`` (the lifted pt trips by detection: a ``walkFallbackFrom`` =
    pt stamp, or ``routingMode`` = pt without the stamp), ``lifted_by_distance_band`` (lifted pt trips),
    ``share_lifted`` (``n_lifted`` / ``n_pt_trips_frozen``), ``max_share`` and ``bound_exceeded``;
    ``n_lifted_by_mode`` (``pt``, ``walk``, ``bicycle``), ``n_lifted_by_reason`` (all lifted trips by reach class;
    a bicycle trip taken along that the loop reaches adds the key ``reached``), ``n_lifted_with_tour`` (bicycle
    trips lifted only because another bicycle trip of their tour was), ``share_lifted_other_modes`` (lifted walk and
    bicycle trips / ``n_resident_trips``), ``max_share_other_modes``, ``bound_exceeded_other_modes`` and the
    settings used. ``source_path`` and ``target_path`` must differ. The output is written in the encoding of the
    source's XML declaration (UTF-8 without one).
    """
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"{_LOG_TAG} population not found: {source_path}")
    rules = _LiftRules(float(min_distance_m), reach, bool(all_modes), float(walk_max_distance_m),
                       float(bicycle_max_distance_m))
    counts = {"n_persons": 0, "n_persons_without_legs": 0, "n_resident_trips": 0, "n_unreached_trips": 0,
              "n_unreached_by_reason": dict.fromkeys(UNREACHED_REASONS, 0),
              "n_pt_trips_frozen": 0, "n_pt_as_walk_frozen": 0, "n_lifted": 0,
              "n_lifted_stamped": 0, "n_lifted_routing_mode_only": 0,
              "lifted_by_distance_band": dict.fromkeys(plan_check.PT_AS_WALK_DISTANCE_BAND_LABELS, 0),
              "n_lifted_by_mode": dict.fromkeys(LIFTED_FROM_MODES, 0),
              "n_lifted_by_reason": dict.fromkeys(UNREACHED_REASONS, 0), "n_lifted_with_tour": 0}
    header, encoding = _raw_header(source_path)
    gzipped = str(target_path).endswith(".gz")
    out = (gzip.open(target_path, "wt", encoding=encoding) if gzipped
           else open(target_path, "w", encoding=encoding))
    with out:
        out.write(header + "\n")
        with plan_check.open_population(source_path) as handle:
            _stream_elements(handle, out, rules, counts)
        out.write("</population>\n")
    share = counts["n_lifted"] / counts["n_pt_trips_frozen"] if counts["n_pt_trips_frozen"] else 0.0
    exceeded = share > max_share
    n_lifted_other = counts["n_lifted_by_mode"][WALK_MODE] + counts["n_lifted_by_mode"][BICYCLE_MODE]
    share_other = n_lifted_other / counts["n_resident_trips"] if counts["n_resident_trips"] else 0.0
    exceeded_other = share_other > max_share_other_modes
    report = dict(counts, share_lifted=share, min_distance_m=float(min_distance_m), max_share=float(max_share),
                  bound_exceeded=bool(exceeded), share_lifted_other_modes=share_other,
                  max_share_other_modes=float(max_share_other_modes),
                  bound_exceeded_other_modes=bool(exceeded_other), all_modes=bool(all_modes),
                  reach=None if reach is None else {"isolate_outside_trips": bool(reach.isolate_outside_trips),
                                                    "max_tour_length": int(reach.max_tour_length)},
                  walk_max_distance_m=float(walk_max_distance_m), bicycle_max_distance_m=float(bicycle_max_distance_m))
    _log_scope_and_other_modes(report)
    message = (f"{_LOG_TAG} lifted {counts['n_lifted']} of {counts['n_pt_trips_frozen']} pt-intended trips in frozen "
               f"resident tours ({100.0 * share:.2f}%), of {counts['n_persons']} persons; pt-intended trips realised "
               f"as walk in frozen tours at any distance {counts['n_pt_as_walk_frozen']}; lifted by detection: "
               f"stamp {counts['n_lifted_stamped']}, routingMode only {counts['n_lifted_routing_mode_only']}; "
               f"lifted by distance {counts['lifted_by_distance_band']}")
    if exceeded:
        # Logs at ERROR and never raises: the lifted trips leave the plan check's bounded share, so prepare hands this
        # report to ``plan_check.run``, which records the share in plan_check.json and fails together with the others.
        logger.error("%s exceeds the bound %.2f%% of %s (the plan check's frozen pt-as-walk share): the router finds "
                     "no pt connection for many trips of tours the mode choice never re-chooses; the lifted trips "
                     "no longer count in the plan check's bounded share", message, 100.0 * max_share,
                     plan_check.KEY_MAX_FROZEN_PT_AS_WALK_SHARE)
    else:
        logger.info("%s", message)
    return report


def _log_scope_and_other_modes(report: dict) -> None:
    """One line on the scope (unreached trips by reason, skipped persons) and the lifted trips by mode and reason."""
    n_trips = report["n_resident_trips"]

    def rate(count: int) -> str:
        return f"{count} ({100.0 * count / n_trips:.3f}%)" if n_trips else f"{count}"

    scope = ("reach settings of the loop " + str(report["reach"]) if report["reach"] is not None
             else "frozen tours (no reach settings)")
    reasons = ", ".join(f"{reason} {rate(count)}" for reason, count in report["n_unreached_by_reason"].items())
    lifted = ", ".join(f"{mode} {rate(count)}" for mode, count in report["n_lifted_by_mode"].items())
    lifted_reasons = ", ".join(f"{reason} {rate(count)}" for reason, count in report["n_lifted_by_reason"].items())
    message = (f"{_LOG_TAG} scope {scope}: {rate(report['n_unreached_trips'])} of {n_trips} resident trips not "
               f"reached ({reasons}); residents skipped without legs {report['n_persons_without_legs']} of "
               f"{report['n_persons']}; all_modes {report['all_modes']}; lifted by mode {lifted}; by reason "
               f"{lifted_reasons}; bicycle trips taken along with their tour {rate(report['n_lifted_with_tour'])}; "
               f"walk and bicycle lifted {100.0 * report['share_lifted_other_modes']:.3f}% of resident trips")
    if report["bound_exceeded_other_modes"]:
        logger.error("%s exceeds the bound %.3f%% of %s: many walk or bicycle trips the mode choice does not reach "
                     "have a mode that does not fit their distance", message,
                     100.0 * report["max_share_other_modes"], KEY_MAX_SHARE_OTHER_MODES)
    else:
        logger.info("%s", message)


def _raw_header(source_path) -> tuple:
    """The raw text up to and including the ``<population ...>`` start tag, and its encoding.

    The encoding is that of the XML declaration (UTF-8 without one); the decoding is strict.
    """
    header = b""
    with plan_check.open_population(source_path) as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                raise ValueError(f"{_LOG_TAG} {source_path} has no <population> start tag")
            header += chunk
            match = _POPULATION_START_TAG.search(header)
            if match:
                declared = _XML_ENCODING.search(header[:match.start()])
                encoding = declared.group(1).decode("ascii") if declared else "utf-8"
                return header[:match.end()].decode(encoding), encoding


def _stream_elements(handle, out, rules: _LiftRules, counts: dict) -> None:
    """Write every child of the root, repairing the residents' persons; memory stays bounded."""
    depth, root = 0, None
    for event, element in ET.iterparse(handle, events=("start", "end")):
        if event == "start":
            depth += 1
            if root is None:
                root = element
            continue
        depth -= 1
        if depth != 1:
            continue
        if element.tag == "person" and _subpopulation(element) == RESIDENTS:
            counts["n_persons"] += 1
            _repair_person(element, rules, counts)
        element.tail = None
        out.write(ET.tostring(element, encoding="unicode") + "\n")
        root.clear()


def run(context, result: str) -> dict:
    """Lift in the cut population of the prepare stage (located as ``plan_check.run`` does) and write the report.

    Reads ``KEY_ALL_MODES``, ``KEY_MAX_SHARE_OTHER_MODES``, the plan check's pt, walk and bicycle distance keys and
    pt share bound, and (with ``all_modes``) the loop's reach keys of ``mode_choice_reach``; the stage declares
    them. The rewritten file replaces the population atomically (``os.replace``); the report goes to
    ``frozen_pt_walk_lift.json`` in the stage directory.
    """
    directory = context.path()
    config_path = os.path.join(directory, result)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"{_LOG_TAG} written config not found: {config_path}")
    population_path = plan_check.resolve_population_path(config_path)
    folder, name = os.path.split(population_path)
    temporary_path = os.path.join(folder, "lift_tmp_" + name)
    all_modes = mode_choice_reach.parse_bool(KEY_ALL_MODES, context.config(KEY_ALL_MODES))
    max_share_other_modes = float(context.config(KEY_MAX_SHARE_OTHER_MODES))
    if not 0.0 <= max_share_other_modes <= 1.0:
        raise ValueError(f"{_LOG_TAG} config key {KEY_MAX_SHARE_OTHER_MODES} is a share in [0, 1], got "
                         f"{max_share_other_modes!r}")
    try:
        report = lift_frozen_pt_walks(
            population_path, temporary_path,
            min_distance_m=float(context.config(plan_check.KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M)),
            max_share=float(context.config(plan_check.KEY_MAX_FROZEN_PT_AS_WALK_SHARE)),
            # Without all_modes no reach settings: the scope stays the frozen tours, the population today's.
            reach=mode_choice_reach.loop_settings(context) if all_modes else None,
            all_modes=all_modes,
            walk_max_distance_m=float(context.config(plan_check.KEY_WALK_MAX_DISTANCE_M)),
            bicycle_max_distance_m=float(context.config(plan_check.KEY_BICYCLE_MAX_DISTANCE_M)),
            max_share_other_modes=max_share_other_modes)
        os.replace(temporary_path, population_path)
    finally:
        # After a successful replace the temporary file is gone; after a failure the original stays untouched.
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
    with open(os.path.join(directory, REPORT_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return report
