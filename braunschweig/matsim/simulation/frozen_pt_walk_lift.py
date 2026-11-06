"""Lift the long pt-as-walk trips of frozen resident tours to car_passenger in prepare (eqasim-bs#575, ADR-0189).

A home-based tour that touches an ``outside`` activity after the scenario cut is frozen for the discrete mode choice
(``plan_check.leg_tour_frozen``). A pt-intended trip of such a tour that the router turned into a walk (no
connection) would stay a walk of many kilometres in every iteration. This step runs after the cut and before the
plan check and replaces each such trip whose straight-line distance is STRICTLY above
``braunschweig.plan_check.pt_as_walk_bound_min_distance_m`` (the bound of the plan check; no second number) by ONE
leg ``car_passenger`` (routingMode ``car_passenger``, the original ``dep_time``, no route, no ``trav_time``, leg
attribute ``liftedFrom`` = ``pt``). MATSim's PrepareForSim routes a leg without a route at the start. ASSUMPTION
(ADR-0156, ADR-0187): a trip that no timetable serves is made as a lift, not as a walk of many kilometres.

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

Observability: one INFO line with the lift rate (WARNING above ``warn_share``) and ``frozen_pt_walk_lift.json``.
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
#: ASSUMPTION (ADR-0189, #575): WARN when more than this share of the pt-intended trips of frozen resident tours is
#: lifted; a high share means the router finds no connection for many trips (a symptom, not a reference value).
KEY_WARN_SHARE = "braunschweig.frozen_pt_walk_lift.warn_share"
DEFAULT_WARN_SHARE = 0.05
LIFTED_MODE = "car_passenger"
LIFTED_FROM_ATTRIBUTE = "liftedFrom"
_POPULATION_START_TAG = re.compile(r"<population\b[^>]*>")


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
        if not any(plan_check._leg_attribute(leg, WALK_FALLBACK_ATTRIBUTE) == PT_MODE
                   or plan_check._leg_attribute(leg, ROUTING_MODE_ATTRIBUTE) == PT_MODE for leg in legs):
            continue
        counts["n_pt_trips_frozen"] += 1
        if not all(leg.get("mode") in WALK_MODES for leg in legs):
            continue
        _, _, ox, oy = activities[trip[0]]
        _, _, dx, dy = activities[trip[-1] + 1]
        distance_m = math.hypot(dx - ox, dy - oy)
        if distance_m <= min_distance_m:
            continue
        counts["n_long_pt_as_walk_before"] += 1
        counts["n_lifted"] += 1
        counts["lifted_by_distance_band"][plan_check._band_label(
            distance_m, plan_check.PT_AS_WALK_DISTANCE_BAND_EDGES_M, plan_check.PT_AS_WALK_DISTANCE_BAND_LABELS)] += 1
        replacements.append((leg_positions[trip[0]], leg_positions[trip[-1]], _lifted_leg(legs[0].get("dep_time"))))
    for first, last, new_leg in reversed(replacements):
        children[first:last + 1] = [new_leg]
    if replacements:
        for child in list(plan):
            plan.remove(child)
        plan.extend(children)


def lift_frozen_pt_walks(source_path, target_path, *, min_distance_m: float, warn_share: float) -> dict:
    """Write ``target_path`` as ``source_path`` with the long pt-as-walk trips of frozen resident tours lifted.

    Returns the report: ``n_persons`` (residents examined), ``n_pt_trips_frozen`` (pt-intended trips of frozen
    resident tours), ``n_long_pt_as_walk_before`` (of them all-walk trips over ``min_distance_m``), ``n_lifted``,
    ``lifted_by_distance_band`` and the two parameters. ``source_path`` and ``target_path`` must differ.
    """
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"{_LOG_TAG} population not found: {source_path}")
    counts = {"n_persons": 0, "n_pt_trips_frozen": 0, "n_long_pt_as_walk_before": 0, "n_lifted": 0,
              "lifted_by_distance_band": dict.fromkeys(plan_check.PT_AS_WALK_DISTANCE_BAND_LABELS, 0)}
    gzipped = str(target_path).endswith(".gz")
    out = gzip.open(target_path, "wt", encoding="utf-8") if gzipped else open(target_path, "w", encoding="utf-8")
    with out:
        out.write(_raw_header(source_path) + "\n")
        with plan_check._open(source_path) as handle:
            _stream_elements(handle, out, min_distance_m, counts)
        out.write("</population>\n")
    share = counts["n_lifted"] / counts["n_pt_trips_frozen"] if counts["n_pt_trips_frozen"] else 0.0
    report = dict(counts, share_lifted=share, min_distance_m=float(min_distance_m), warn_share=float(warn_share))
    message = (f"{_LOG_TAG} lifted {counts['n_lifted']} of {counts['n_pt_trips_frozen']} pt-intended trips in frozen "
               f"resident tours ({100.0 * share:.2f}%), of {counts['n_persons']} persons; long pt-as-walk trips in "
               f"frozen tours before {counts['n_long_pt_as_walk_before']}; by distance "
               f"{counts['lifted_by_distance_band']}")
    if share > warn_share:
        logger.warning("%s exceeds the warn share %.2f%%: the router finds no pt connection for many trips of "
                       "tours the mode choice never re-chooses", message, 100.0 * warn_share)
    else:
        logger.info("%s", message)
    return report


def _raw_header(source_path) -> str:
    """The raw text up to and including the ``<population ...>`` start tag."""
    header = b""
    with plan_check._open(source_path) as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                raise ValueError(f"{_LOG_TAG} {source_path} has no <population> start tag")
            header += chunk
            text = header.decode("utf-8", errors="ignore")
            match = _POPULATION_START_TAG.search(text)
            if match:
                return text[:match.end()]


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
    report = lift_frozen_pt_walks(
        population_path, temporary_path,
        min_distance_m=float(context.config(plan_check.KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M)),
        warn_share=float(context.config(KEY_WARN_SHARE)))
    os.replace(temporary_path, population_path)
    with open(os.path.join(directory, REPORT_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return report
