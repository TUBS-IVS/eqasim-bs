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

XML approach: the file is streamed with ``iterparse`` (memory stays bounded). The header up to and including the
``<population ...>`` start tag is copied as raw text (XML declaration, DOCTYPE and population attributes stay as
they are); every element below the root is then re-serialised from the parsed tree, one at a time. A person that is
not repaired keeps all its elements, attributes and attribute order, but not its original whitespace. Re-serialising
was chosen over a line-based text edit because the output of the MATSim writers is not guaranteed to be
pretty-printed one element per line.

Observability: one log line with the lift rate (ERROR above ``max_share``, which is the plan check's bound
``plan_check.KEY_MAX_FROZEN_PT_AS_WALK_SHARE``, else INFO) and ``frozen_pt_walk_lift.json``.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import re
import xml.etree.ElementTree as ET

from braunschweig.matsim.simulation import plan_check
from braunschweig.matsim.simulation.plan_check import (
    PT_MODE, RESIDENTS, ROUTING_MODE_ATTRIBUTE, WALK_FALLBACK_ATTRIBUTE, WALK_MODES)

logger = logging.getLogger(__name__)

_LOG_TAG = "[frozen_pt_walk_lift]"
REPORT_FILE_NAME = "frozen_pt_walk_lift.json"
KEY_ENABLED = "braunschweig.frozen_pt_walk_lift.enabled"
DEFAULT_ENABLED = True
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


def _lifted_leg(departure_time: str | None) -> ET.Element:
    leg = ET.Element("leg", {"mode": LIFTED_MODE})
    if departure_time:
        leg.set("dep_time", departure_time)
    attributes = ET.SubElement(leg, "attributes")
    attributes.append(_string_attribute(ROUTING_MODE_ATTRIBUTE, LIFTED_MODE))
    attributes.append(_string_attribute(LIFTED_FROM_ATTRIBUTE, PT_MODE))
    return leg


def _repair_person(person: ET.Element, min_distance_m: float, counts: dict) -> None:
    """Count the pt-intended trips of the frozen tours of one resident and lift the long pt-as-walk ones in place."""
    plan = next((p for p in person.findall("plan") if p.get("selected") == "yes"), person.find("plan"))
    if plan is None:
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
    frozen_legs = plan_check.leg_tour_frozen(activities)
    replacements = []
    for trip in plan_check.split_trips(activities, n_legs):
        if not frozen_legs[trip[0]]:
            continue
        legs = [children[leg_positions[index]] for index in trip]
        stamped = any(plan_check.leg_attribute(leg, WALK_FALLBACK_ATTRIBUTE) == PT_MODE for leg in legs)
        if not (stamped or any(plan_check.leg_attribute(leg, ROUTING_MODE_ATTRIBUTE) == PT_MODE for leg in legs)):
            continue
        counts["n_pt_trips_frozen"] += 1
        if not all(leg.get("mode") in WALK_MODES for leg in legs):
            continue
        counts["n_pt_as_walk_frozen"] += 1
        _, _, ox, oy = activities[trip[0]]
        _, _, dx, dy = activities[trip[-1] + 1]
        distance_m = math.hypot(dx - ox, dy - oy)
        if distance_m <= min_distance_m:
            continue
        counts["n_lifted"] += 1
        counts["n_lifted_stamped" if stamped else "n_lifted_routing_mode_only"] += 1
        counts["lifted_by_distance_band"][plan_check.band_label(
            distance_m, plan_check.PT_AS_WALK_DISTANCE_BAND_EDGES_M, plan_check.PT_AS_WALK_DISTANCE_BAND_LABELS)] += 1
        replacements.append((leg_positions[trip[0]], leg_positions[trip[-1]], _lifted_leg(legs[0].get("dep_time"))))
    for first, last, new_leg in reversed(replacements):
        children[first:last + 1] = [new_leg]
    if replacements:
        for child in list(plan):
            plan.remove(child)
        plan.extend(children)


def lift_frozen_pt_walks(source_path, target_path, *, min_distance_m: float, max_share: float) -> dict:
    """Write ``target_path`` as ``source_path`` with the long pt-as-walk trips of frozen resident tours lifted.

    Returns the report: ``n_persons`` (residents examined), ``n_pt_trips_frozen`` (pt-intended trips of frozen
    resident tours), ``n_pt_as_walk_frozen`` (of them the all-walk trips, at any distance), ``n_lifted`` (of those
    the trips over ``min_distance_m``), ``n_lifted_stamped`` / ``n_lifted_routing_mode_only`` (the lifted trips by
    detection: a ``walkFallbackFrom`` = pt stamp, or ``routingMode`` = pt without the stamp),
    ``lifted_by_distance_band``, ``min_distance_m``, ``max_share`` (the bound used) and ``bound_exceeded``.``source_path`` and ``target_path`` must differ. The
    output is written in the encoding of the source's XML declaration (UTF-8 without one).
    """
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"{_LOG_TAG} population not found: {source_path}")
    counts = {"n_persons": 0, "n_pt_trips_frozen": 0, "n_pt_as_walk_frozen": 0, "n_lifted": 0,
              "n_lifted_stamped": 0, "n_lifted_routing_mode_only": 0,
              "lifted_by_distance_band": dict.fromkeys(plan_check.PT_AS_WALK_DISTANCE_BAND_LABELS, 0)}
    header, encoding = _raw_header(source_path)
    gzipped = str(target_path).endswith(".gz")
    out = (gzip.open(target_path, "wt", encoding=encoding) if gzipped
           else open(target_path, "w", encoding=encoding))
    with out:
        out.write(header + "\n")
        with plan_check.open_population(source_path) as handle:
            _stream_elements(handle, out, min_distance_m, counts)
        out.write("</population>\n")
    share = counts["n_lifted"] / counts["n_pt_trips_frozen"] if counts["n_pt_trips_frozen"] else 0.0
    exceeded = share > max_share
    report = dict(counts, share_lifted=share, min_distance_m=float(min_distance_m), max_share=float(max_share),
                  bound_exceeded=bool(exceeded))
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


def _stream_elements(handle, out, min_distance_m: float, counts: dict) -> None:
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
            _repair_person(element, min_distance_m, counts)
        element.tail = None
        out.write(ET.tostring(element, encoding="unicode") + "\n")
        root.clear()


def run(context, result: str) -> dict:
    """Lift in the cut population of the prepare stage (located as ``plan_check.run`` does) and write the report.

    The rewritten file replaces the population atomically (``os.replace``); the report goes to
    ``frozen_pt_walk_lift.json`` in the stage directory.
    """
    directory = context.path()
    config_path = os.path.join(directory, result)
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"{_LOG_TAG} written config not found: {config_path}")
    population_path = plan_check.resolve_population_path(config_path)
    folder, name = os.path.split(population_path)
    temporary_path = os.path.join(folder, "lift_tmp_" + name)
    try:
        report = lift_frozen_pt_walks(
            population_path, temporary_path,
            min_distance_m=float(context.config(plan_check.KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M)),
            max_share=float(context.config(plan_check.KEY_MAX_FROZEN_PT_AS_WALK_SHARE)))
        os.replace(temporary_path, population_path)
    finally:
        # After a successful replace the temporary file is gone; after a failure the original stays untouched.
        if os.path.exists(temporary_path):
            os.remove(temporary_path)
    with open(os.path.join(directory, REPORT_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return report
