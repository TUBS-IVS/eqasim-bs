"""Premise check for a planned mode rule (issue #679 follow-up): what would "same facility and a leisure end -> walk
or bicycle only" catch?

Planned Java rule (not implemented): a trip whose origin and destination activity carry the SAME facility id, and where
at least one of the two activities is of type ``leisure``, may only use walk or bicycle.

Input per arm: the prepared population of the preparation stage (``<prefix>population.xml.gz`` in the
``braunschweig.matsim.simulation.prepare`` stage folder) and the standalone mode choice's input trip table
(``mode_choice/input_trips.csv.gz``, the trips as the initial mode choice READ them, with their mode before the
choice). The prepared population is written after the initial mode choice, which changes legs only, so its activities
(types, facility ids, coordinates) are the ones the mode choice read. Residents only (persons without a
``subpopulation`` attribute); freight, in-commuters and student in-commuters are counted apart. Trip k of a person runs
from main activity k to main activity k + 1 (activity types ending in " interaction" are skipped); the mode before the
choice is the ``mode`` of input trip (person_id, person_trip_id = k), the mode after is the main mode of the legs.

For arm C the loop activities are the ``loop_origin`` rows of the secondary location result (``--loop-cache``, the
newest pipeline.json entry as in i679_ab_metrics.resolve_stage), matched by person and main-activity index; a key is
"aligned" when the plan's activity at that index is a ``leisure`` activity.

It also reports, for the plans read, the modes after the choice per class, every caught trip that ends up with a
motorised mode (with whether the person's plan has an ``outside`` activity, whether the trip's home-based tour touches
an ``outside`` activity and whether that tour has more than 6 trips, the two kinds of tour the mode choice does not
re-choose in this configuration), and
the main modes of all resident trips. ``--plans-path`` reads another plans file (e.g. MATSim's output_plans.xml.gz)
against the same input trip table.

Usage: python i679_loop_mode_rule_premise.py --prepare-dir PREP --name C [--loop-cache CACHE] [--plans-path P] --out out.json
"""
import argparse
import collections
import gzip
import json
import pickle
import sys
import xml.etree.ElementTree as ET

import pandas as pd

from i679_ab_metrics import STAGES, resolve_stage

LEISURE = "leisure"


def iter_plans(path):
    with gzip.open(path, "rb") as handle:
        person_id, subpopulation = None, ""
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if event == "start" and elem.tag == "person":
                person_id, subpopulation = elem.get("id"), ""
            elif event == "end" and elem.tag == "attribute" and elem.get("name") == "subpopulation":
                subpopulation = elem.text or ""
            elif event == "end" and elem.tag == "plan":
                if elem.get("selected", "yes") != "yes":
                    continue
                acts, trips, legs = [], [], []
                for child in elem:
                    if child.tag == "activity":
                        if child.get("type", "").endswith(" interaction"):
                            continue
                        if acts:
                            trips.append(legs)
                        legs = []
                        acts.append({"type": child.get("type"), "facility": child.get("facility"),
                                     "x": child.get("x"), "y": child.get("y")})
                    elif child.tag == "leg":
                        routing = None
                        for sub in child:
                            if sub.tag == "attributes":
                                for attribute in sub:
                                    if attribute.get("name") == "routingMode":
                                        routing = attribute.text
                        legs.append((child.get("mode"), routing))
                yield person_id, subpopulation, acts, trips
            elif event == "end" and elem.tag == "person":
                elem.clear()


def main_mode(legs):
    routing = {leg[1] for leg in legs if leg[1]}
    if len(routing) == 1:
        return routing.pop()
    modes = {leg[0] for leg in legs}
    return modes.pop() if len(modes) == 1 else "+".join(sorted(modes))


def counter(c):
    return {"|".join(k) if isinstance(k, tuple) else str(k): v for k, v in sorted(c.items())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prepare-dir", required=True)
    parser.add_argument("--plans-file", default="braunschweig_1pct_i679_population.xml.gz")
    parser.add_argument("--plans-path", help="plans file to read instead of <prepare-dir>/<plans-file> (e.g. a MATSim "
                        "output_plans.xml.gz); the mode before the initial choice still comes from the prepare dir")
    parser.add_argument("--name", required=True)
    parser.add_argument("--loop-cache")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    trips_in = pd.read_csv(f"{args.prepare_dir}/mode_choice/input_trips.csv.gz", sep=";",
                           usecols=["person_id", "person_trip_id", "mode"], dtype={"person_id": str})
    mode_before = {(p, int(t)): m for p, t, m in trips_in.itertuples(index=False)}

    loops = {}
    if args.loop_cache:
        path, key, _ = resolve_stage(args.loop_cache, STAGES["secondary"])
        with open(path, "rb") as handle:
            secondary, _conv = pickle.load(handle)
        rows = secondary.loc[secondary["loop_origin"].fillna("").astype(str) != "",
                             ["person_id", "activity_index", "loop_origin", "location_id"]]
        loops = {(str(p), int(a)): (o, str(l)) for p, a, o, l in rows.itertuples(index=False)}

    persons = collections.Counter()
    activities_without_facility = collections.Counter()
    activities = collections.Counter()
    caught = collections.Counter()
    caught_by_types_mode_before = collections.Counter()
    caught_by_mode_after = collections.Counter()
    same_facility_no_leisure = collections.Counter()
    loop_check = collections.Counter()
    loop_facility_examples = []
    c_classes = collections.Counter()
    c_other = collections.Counter()
    trips_without_input_row = 0
    class_by_mode_after = collections.Counter()
    motorised_caught = collections.Counter()
    motorised_examples = []
    resident_modes_after = collections.Counter()
    residents_trips = 0

    for person_id, subpopulation, acts, trips in iter_plans(args.plans_path or f"{args.prepare_dir}/{args.plans_file}"):
        group = subpopulation or "resident"
        persons[group] += 1
        if group != "resident":
            continue
        for a in acts:
            activities[a["type"]] += 1
            if not a["facility"]:
                activities_without_facility[a["type"]] += 1
        loop_indices = {k for k in range(len(acts)) if (person_id, k) in loops}
        # loop facility check (question 1)
        for k in loop_indices:
            origin_kind, location_id = loops[(person_id, k)]
            if acts[k]["type"] != LEISURE:
                loop_check["misaligned"] += 1
                continue
            same = acts[k]["facility"] == acts[k - 1]["facility"] if k > 0 else False
            loop_check[f"aligned|{origin_kind}|{'same_facility_as_previous' if same else 'different_facility'}"] += 1
            if not same and len(loop_facility_examples) < 5:
                loop_facility_examples.append({"person_id": person_id, "activity_index": k, "origin": origin_kind,
                                               "previous": acts[k - 1], "loop": acts[k], "location_id": location_id})
        # tours as the run's tour finder cuts them (DiscreteModeChoice tourFinder ActivityBased with activityTypes
        # "outside, home" in the prepared config): a new tour at every departure from a home or outside activity after
        # the first trip; per tour its trip count (TourLength filter: maximumLength 6) and whether it touches an
        # outside activity (OutsideFilter)
        tour_of_trip, tour = [], 0
        for k in range(len(trips)):
            if k > 0 and acts[k]["type"] in ("home", "outside"):
                tour += 1
            tour_of_trip.append(tour)
        tour_size = collections.Counter(tour_of_trip)
        tour_outside = collections.defaultdict(bool)
        for k, tid in enumerate(tour_of_trip):
            if acts[k]["type"] == "outside" or acts[k + 1]["type"] == "outside":
                tour_outside[tid] = True
        for k, legs in enumerate(trips):
            residents_trips += 1
            o, d = acts[k], acts[k + 1]
            before = mode_before.get((person_id, k))
            if before is None:
                trips_without_input_row += 1
                before = "<no input row>"
            after = main_mode(legs)
            resident_modes_after[after] += 1
            same_facility = bool(o["facility"]) and o["facility"] == d["facility"]
            if not same_facility:
                continue
            if LEISURE in (o["type"], d["type"]):
                caught[before] += 1
                caught_by_types_mode_before[(o["type"], d["type"], before)] += 1
                caught_by_mode_after[after] += 1
                cls = "all"
                if loops:
                    into_loop = (k + 1) in loop_indices and d["type"] == LEISURE
                    leaves_loop = k in loop_indices and o["type"] == LEISURE
                    if into_loop:
                        cls = "a_loop_leg"
                    elif leaves_loop:
                        cls = "b_leaves_a_loop_activity"
                    else:
                        cls = "c_other"
                        c_other[(o["type"], d["type"], before)] += 1
                    c_classes[cls] += 1
                class_by_mode_after[(cls, after)] += 1
                if after not in ("walk", "bicycle"):
                    frozen = any(a["type"] == "outside" for a in acts)
                    tid = tour_of_trip[k]
                    tour_kind = ("tour_touches_outside" if tour_outside[tid] else "tour_without_outside") +                         ("|tour_over_6_trips" if tour_size[tid] > 6 else "|tour_up_to_6_trips")
                    motorised_caught[(cls, o["type"], d["type"], before, after,
                                      "plan_has_outside" if frozen else "no_outside", tour_kind)] += 1
                    if len(motorised_examples) < 10:
                        motorised_examples.append({"person_id": person_id, "trip": k, "class": cls, "origin": o,
                                                   "destination": d, "mode_before": before, "mode_after": after,
                                                   "plan_has_outside": frozen})
            else:
                same_facility_no_leisure[before] += 1

    result = {
        "arm": args.name,
        "persons_by_group": dict(persons),
        "resident_trips": residents_trips,
        "resident_trips_without_input_row": trips_without_input_row,
        "resident_activities_by_type": dict(activities),
        "resident_activities_without_facility_by_type": dict(activities_without_facility),
        "rule_caught_trips": int(sum(caught.values())),
        "rule_caught_by_mode_before": dict(caught),
        "rule_caught_by_mode_after_initial_choice": dict(caught_by_mode_after),
        "rule_caught_by_origin_type_destination_type_mode_before": counter(caught_by_types_mode_before),
        "same_facility_without_leisure_end_by_mode_before": dict(same_facility_no_leisure),
        "rule_caught_by_class_and_mode_after": counter(class_by_mode_after),
        "rule_caught_motorised_after_by_class_types_modes_outside": counter(motorised_caught),
        "rule_caught_motorised_after_examples": motorised_examples,
        "resident_trips_by_mode_after": dict(resident_modes_after),
        "plans_read": (args.plans_path or f"{args.prepare_dir}/{args.plans_file}").split("/cache/")[-1],
    }
    if loops:
        result.update({
            "loop_keys": len(loops),
            "loop_facility_check": dict(loop_check),
            "loop_facility_mismatch_examples": loop_facility_examples,
            "rule_caught_classes": dict(c_classes),
            "rule_caught_other_by_origin_type_destination_type_mode_before": counter(c_other),
        })
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
