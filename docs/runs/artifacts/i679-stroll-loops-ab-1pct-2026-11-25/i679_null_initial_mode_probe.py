"""Find the trips whose first leg has no routing mode (issue #679, task C7, on2 prepare failure).

The standalone mode choice of the prepare stage failed with ``DiscreteModeChoiceTrip.getInitialMode()``
null. The discrete mode choice contrib (``TripListConverter``) takes the initial mode of a trip from
``TripStructureUtils.getRoutingMode`` of the trip's FIRST leg, i.e. the leg attribute ``routingMode``.

For each population file given, this probe walks every person's selected plan, splits it into trips
between main activities (types not ending in " interaction") and records per trip: number of legs,
first-leg mode and routingMode, origin and destination facility, link and coordinate. It counts the trips
without a routing mode on the first leg (or without a leg), classifies them (same facility, same link,
same coordinate, activity types, subpopulation), and matches them against the ON arm's loop activities
(``--loop-keys``: person_id, activity_index, loop_origin; trip k of a person leads into main activity k+1).

Usage (eqasim conda env):
    python i679_null_initial_mode_probe.py --population NAME=PATH [--population NAME=PATH ...] \
        --loop-keys loops.csv --out probe.json
"""
import argparse
import collections
import gzip
import json
import sys
import xml.etree.ElementTree as ET

import pandas as pd


def iter_persons(path):
    """Yield (person_id, subpopulation, activities, trips) of each person's selected plan."""
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
                        acts.append({"type": child.get("type"), "x": child.get("x"), "y": child.get("y"),
                                     "facility": child.get("facility"), "link": child.get("link")})
                    elif child.tag == "leg":
                        routing = None
                        for sub in child:
                            if sub.tag == "attributes":
                                for attribute in sub:
                                    if attribute.get("name") == "routingMode":
                                        routing = attribute.text
                        legs.append({"mode": child.get("mode"), "routingMode": routing})
                yield person_id, subpopulation, acts, trips
            elif event == "end" and elem.tag == "person":
                elem.clear()


def probe(path, loops):
    counts = collections.Counter()
    by_class = collections.Counter()
    by_types = collections.Counter()
    by_mode = collections.Counter()
    by_subpop = collections.Counter()
    loop_total = collections.Counter()
    loop_null = collections.Counter()
    loop_null_by_position = collections.Counter()
    examples = []
    for person_id, subpopulation, acts, trips in iter_persons(path):
        counts["persons"] += 1
        for k, legs in enumerate(trips):
            counts["trips"] += 1
            origin, dest = acts[k], acts[k + 1]
            same_facility = origin["facility"] is not None and origin["facility"] == dest["facility"]
            same_link = origin["link"] is not None and origin["link"] == dest["link"]
            same_xy = origin["x"] == dest["x"] and origin["y"] == dest["y"]
            null = (not legs) or legs[0]["routingMode"] is None
            if same_xy:
                counts["trips_same_coordinate"] += 1
            if same_facility:
                counts["trips_same_facility"] += 1
            key = (person_id, k + 1)
            origin_kind = loops.get(key)
            if origin_kind is not None:
                loop_total[origin_kind] += 1
            if not null:
                continue
            counts["trips_without_routing_mode"] += 1
            by_class[f"same_facility={same_facility},same_link={same_link},same_coordinate={same_xy},legs={len(legs)}"] += 1
            by_types[f"{origin['type']}->{dest['type']}"] += 1
            by_mode[legs[0]["mode"] if legs else "<no leg>"] += 1
            by_subpop[subpopulation or "<resident>"] += 1
            if origin_kind is not None:
                loop_null[origin_kind] += 1
                position = "first_trip" if k == 0 else ("last_trip" if k == len(trips) - 1 else "middle")
                after_outside = origin["type"] == "outside"
                loop_null_by_position[f"{position},after_outside={after_outside}"] += 1
            if len(examples) < 10:
                examples.append({"person_id": person_id, "trip": k, "origin": origin, "destination": dest,
                                 "legs": legs, "loop_origin": origin_kind})
    return {
        "counts": dict(counts),
        "without_routing_mode_by_class": dict(by_class),
        "without_routing_mode_by_activity_types": dict(by_types),
        "without_routing_mode_by_first_leg_mode": dict(by_mode),
        "without_routing_mode_by_subpopulation": dict(by_subpop),
        "loop_trips_by_origin": dict(loop_total),
        "loop_trips_without_routing_mode_by_origin": dict(loop_null),
        "loop_trips_without_routing_mode_by_position": dict(loop_null_by_position),
        "examples": examples,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--population", action="append", required=True, help="NAME=PATH")
    parser.add_argument("--loop-keys", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    keys = pd.read_csv(args.loop_keys)
    loops = {(str(p), int(a)): o for p, a, o in keys[["person_id", "activity_index", "loop_origin"]].itertuples(index=False)}
    result = {"loop_keys": len(loops)}
    for item in args.population:
        name, path = item.split("=", 1)
        result[name] = probe(path, loops)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "examples"})
                      for k, v in result.items()}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
