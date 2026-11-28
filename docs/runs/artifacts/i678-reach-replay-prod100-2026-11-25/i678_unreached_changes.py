"""#678 B9 step 2: where do the resident trips come from that the initial rule does NOT reach but that differ after the
initial mode choice (a changed mode, or a new walk over 5 km)?

Classifies every resident trip of the INPUT population with plan_check.leg_reach under the initial settings of the
branch (isolate true, length 11) and the loop settings (isolate false, length 11), then compares it with the same trip
after the initial mode choice. Cross-tabulates, for the trips the initial rule does not reach: class, mode before and
after, whether a leg carried the walkFallbackFrom stamp before and after, and the unfit class before and after.
Residents: no subpopulation attribute and no ic_ facility in the UNCUT plan (as i678_reach_analysis_v2.py).

Usage: python i678_unreached_changes.py <eqasim-bs clone> <uncut> <input> <after_initial> <out json>
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import sys
import xml.etree.ElementTree as ET

clone, uncut_path, input_path, after_path, out_path = sys.argv[1:6]
sys.path.append(clone)
from braunschweig.matsim.simulation import plan_check as pc  # noqa: E402


def persons(path):
    root = None
    with gzip.open(path, "rb") as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                root = element if root is None else root
                continue
            if element.tag != "person":
                continue
            subpop = None
            node = element.find("attributes")
            if node is not None:
                for attribute in node.findall("attribute"):
                    if attribute.get("name") == "subpopulation":
                        subpop = (attribute.text or "").strip() or None
            plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
            activities, legs = [], []
            for child in plan if plan is not None else []:
                if child.tag == "activity":
                    x, y = child.get("x"), child.get("y")
                    activities.append((child.get("type"), None, float(x) if x else math.nan, float(y) if y else math.nan,
                                       child.get("facility")))
                elif child.tag == "leg":
                    legs.append((child.get("mode"), None, pc.leg_attribute(child, pc.WALK_FALLBACK_ATTRIBUTE),
                                 pc.leg_attribute(child, pc.ROUTING_MODE_ATTRIBUTE)))
            yield element.get("id"), subpop, activities, legs
            element.clear()
            root.clear()


def trips_of(activities, legs):
    trips = pc.split_trips(activities, len(legs))
    out = []
    for trip in trips:
        trip_legs = [legs[i] for i in trip]
        o, d = activities[trip[0]], activities[trip[-1] + 1]
        distance = math.hypot(d[2] - o[2], d[3] - o[3])
        unfit = pc.unfit_mode(trip_legs, distance, pt_as_walk_bound_min_distance_m=5000.0, walk_max_distance_m=5000.0,
                              bicycle_max_distance_m=30000.0)
        stamped = any(leg[2] for leg in trip_legs)
        out.append((pc.trip_mode(trip_legs), unfit, stamped, "+".join(leg[0] for leg in trip_legs)))
    return trips, out


def main():
    incommuters = set()
    for pid, subpop, activities, _ in persons(uncut_path):
        if not subpop and any((a[4] or "").startswith("ic_") for a in activities):
            incommuters.add(pid)
    before = {}
    for pid, subpop, activities, legs in persons(input_path):
        if subpop or pid in incommuters or not legs:
            continue
        trips, records = trips_of(activities, legs)
        plain = [a[:4] for a in activities]
        init = pc.leg_reach(plain, legs, isolate_outside_trips=True, max_tour_length=11)
        loop = pc.leg_reach(plain, legs, isolate_outside_trips=False, max_tour_length=11)
        before[pid] = [(r, init[t[0]], loop[t[0]]) for r, t in zip(records, trips)]
    table = collections.Counter()
    for pid, subpop, activities, legs in persons(after_path):
        if pid not in before:
            continue
        _, records = trips_of(activities, legs)
        for (rb, c_init, c_loop), ra in zip(before[pid], records):
            if c_init == pc.REACHED:
                continue
            changed_mode = rb[0] != ra[0]
            new_walk = ra[1] == "walk" and rb[1] != "walk"
            if changed_mode or new_walk or rb[1] != ra[1]:
                key = (c_init, c_loop, f"{rb[0]}->{ra[0]}", f"stamp {rb[2]}->{ra[2]}", f"unfit {rb[1]}->{ra[1]}",
                       f"legs {rb[3]} -> {ra[3]}" if new_walk else "")
                table[key] += 1
    rows = {" | ".join(k): v for k, v in table.most_common()}
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=1)
    for key, value in list(rows.items())[:60]:
        print(f"{value:>7}  {key}")


if __name__ == "__main__":
    main()
