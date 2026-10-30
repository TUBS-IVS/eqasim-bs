"""Count the in-commuter pt trips that a routed population realises as walks (eqasim-bs#528, method of #488).

An in-commuter is a person whose selected plan has an activity whose facility id starts with the given prefix:
``ic_edu_`` for the student in-commuters of ``braunschweig.synthesis.student_incommuters``, ``ic_work_`` for the
employee in-commuters of ``braunschweig.synthesis.incommuters``. A trip is the legs between two main activities
(``... interaction`` activities are skipped). Its routing mode is the ``routingMode`` attribute of its legs.

When the pt router finds no connection that beats walking, the routed trip has only walk legs AND routing mode
walk (the router rewrites it), so the planned mode is not on the trip itself. As in the #488 analysis, a walk-only
trip is attributed to the person's commute mode, taken from the person's OTHER trips: "pt" when one of them is
routed pt, else the other routing modes, else "walk_only" (every trip of the plan is a walk). For the student
in-commuters the synthesis draws only car or pt and a car trip never becomes a walk, so their "walk_only" plans are
pt days whose trips all became walks. A walk is "long" above the distance bound (routed distance summed over its
legs, default 5000 m, the bound of the #488 analysis).

Prints the in-commuters by the routing modes of their plan, the walk-only trips by the commute mode they are
attributed to, and for the long ones the direction, departure hour, distance and whether the walk fallback stamped
them (``walkFallbackFrom``, ADR-0153 / WP-B). Only aggregate counts and ids are printed; the population is
streamed. Read-only.

Usage: python scripts/measure_incommuter_pt_walks.py <population.xml.gz> [--prefix ic_edu_] [--bound-m 5000]
"""
from __future__ import annotations

import argparse
import collections
import gzip
import statistics
import xml.etree.ElementTree as ET

N_EXAMPLES = 8


def leg_attributes(leg) -> dict:
    node = leg.find("attributes")
    return {} if node is None else {a.get("name"): (a.text or "").strip() for a in node.findall("attribute")}


def parse_hours(text):
    if not text:
        return None
    hours, minutes, seconds = text.split(":")
    return int(hours) + int(minutes) / 60 + float(seconds) / 3600


def plan_trips(plan) -> list:
    """(origin activity, legs, destination activity) per trip of a selected plan."""
    trips, legs, origin = [], [], None
    for element in plan:
        if element.tag == "activity":
            if element.get("type", "").endswith("interaction"):
                continue
            if origin is not None:
                trips.append((origin, legs, element))
            legs, origin = [], element
        elif element.tag == "leg":
            legs.append(element)
    return trips


def trip_record(person_id, origin, legs, destination) -> dict:
    attributes = [leg_attributes(leg) for leg in legs]
    routing = sorted({a.get("routingMode") for a in attributes if a.get("routingMode")})
    distance_m = sum(float(leg.find("route").get("distance") or 0.0)
                     for leg in legs if leg.find("route") is not None)
    return dict(person=person_id, origin=origin.get("type"), destination=destination.get("type"),
                departure_h=parse_hours(origin.get("end_time")) or parse_hours(legs[0].get("dep_time") if legs else None),
                routing_mode=",".join(routing) or "none", leg_modes={leg.get("mode") for leg in legs},
                distance_km=distance_m / 1000.0, stamped=any(a.get("walkFallbackFrom") for a in attributes))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("population", help="routed MATSim population (.xml.gz)")
    parser.add_argument("--prefix", default="ic_edu_", help="facility id prefix of the in-commuters (default ic_edu_)")
    parser.add_argument("--bound-m", type=float, default=5000.0, help="long-walk distance bound in m (default 5000)")
    args = parser.parse_args(argv)

    persons_by_modes = collections.Counter()
    persons_by_commute = collections.Counter()
    walks = []
    with gzip.open(args.population, "rb") as handle:
        for _, person in ET.iterparse(handle):
            if person.tag != "person":
                continue
            plan = next((p for p in person.findall("plan") if p.get("selected") == "yes"), None)
            if plan is None or not any(e.tag == "activity" and (e.get("facility") or "").startswith(args.prefix)
                                       for e in plan):
                person.clear()
                continue
            trips = [trip_record(person.get("id"), *trip) for trip in plan_trips(plan)]
            persons_by_modes["+".join(sorted({t["routing_mode"] for t in trips}))] += 1
            other_modes = {t["routing_mode"] for t in trips if t["leg_modes"] != {"walk"}}
            commute = ("pt" if "pt" in other_modes
                       else "+".join(sorted(m for m in other_modes if m != "walk")) or "walk_only")
            persons_by_commute[commute] += 1
            for trip in trips:
                if trip["leg_modes"] == {"walk"}:
                    walks.append(dict(trip, commute_mode=commute))
            person.clear()

    long_walks = [t for t in walks if t["distance_km"] * 1000.0 > args.bound_m]
    print("population:", args.population)
    print("in-commuters (facility prefix %s): %d" % (args.prefix, sum(persons_by_modes.values())))
    print("  by routing modes of their plan:", dict(sorted(persons_by_modes.items())))
    print("  by commute mode (from the trips that are not walks):", dict(sorted(persons_by_commute.items())))
    print("walk-only trips: %d of %d persons, by commute mode %s"
          % (len(walks), len({t["person"] for t in walks}),
             dict(collections.Counter(t["commute_mode"] for t in walks))))
    print("  of which long (> %.0f m): %d of %d persons, by commute mode %s"
          % (args.bound_m, len(long_walks), len({t["person"] for t in long_walks}),
             dict(collections.Counter(t["commute_mode"] for t in long_walks))))
    for mode in ("pt", "walk_only"):
        persons = {t["person"] for t in long_walks if t["commute_mode"] == mode}
        print("  %s persons with at least one long walk: %d of %d" % (mode, len(persons), persons_by_commute[mode]))
    if long_walks:
        distances = sorted(t["distance_km"] for t in long_walks)
        print("  long walks stamped walkFallbackFrom:", sum(t["stamped"] for t in long_walks))
        print("  long walks by direction:",
              dict(collections.Counter((t["origin"], t["destination"]) for t in long_walks)))
        print("  long walks by departure hour:",
              sorted(collections.Counter(int(t["departure_h"]) for t in long_walks
                                         if t["departure_h"] is not None).items()))
        print("  long walk distance km min/median/max: %.1f / %.1f / %.1f"
              % (distances[0], statistics.median(distances), distances[-1]))
        print("  examples (person, commute mode, direction, departure h, km):")
        for t in long_walks[:N_EXAMPLES]:
            print("    %s %s %s->%s %.2f %.1f" % (t["person"], t["commute_mode"], t["origin"], t["destination"],
                                                 t["departure_h"] if t["departure_h"] is not None else float("nan"),
                                                 t["distance_km"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
