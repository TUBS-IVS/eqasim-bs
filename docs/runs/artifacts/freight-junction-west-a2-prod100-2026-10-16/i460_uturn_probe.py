"""i460 probe: are the trucks stuck west of the A2-west gate U-turning to reach a wrong-carriageway end link?

Usage: python i460_uturn_probe.py <simulation_output_dir> > i460_uturn_probe.json

Hypothesis (from the #444 analysis): westbound outgoing/transit trucks had their end activity snapped to
the EASTBOUND A2 carriageway 199731. To reach its end they pass the gate westbound, leave the A2 at the
next junction west, turn there and come back eastbound. For every truck with a stuckAndAbort event on the
focus links this prints its trip type, first/last activity link, and whether its selected route visits the
westbound carriageway, a focus link and then the eastbound carriageway (in that order).
"""
import collections
import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sim = sys.argv[1]
FOCUS = {"508524", "118285", "183348", "97299", "97263"}
WESTBOUND = {"224246", "199736"}
EASTBOUND = {"199731"}
ATTR = re.compile(r'(\w+)="([^"]*)"')

stuck = {}
with gzip.open(os.path.join(sim, "output_events.xml.gz"), "rt", encoding="utf-8") as fh:
    for line in fh:
        if 'type="stuckAndAbort"' in line and 'legMode="truck"' in line:
            a = dict(ATTR.findall(line))
            if a.get("link") in FOCUS:
                stuck[a["person"]] = (a["link"], float(a["time"]))

rows = []
# Also count, for ALL freight agents, how many end on the eastbound carriageway and route through the focus links.
all_end_east = collections.Counter()
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_plans.xml.gz"), "rb"), events=("end",)):
    if el.tag != "person":
        continue
    pid = el.get("id")
    if pid.startswith("freight"):
        attrs = {x.get("name"): x.text for x in el.iter("attribute")}
        plan = next(p for p in el.findall("plan") if p.get("selected") == "yes")
        acts = plan.findall("activity")
        # Each freight plan is walk access -> truck -> walk egress; read the TRUCK leg's link route.
        truck_leg = next((leg for leg in plan.findall("leg") if leg.get("mode") == "truck"), None)
        route_el = truck_leg.find("route") if truck_leg is not None else None
        route = route_el.text.split() if route_el is not None and route_el.text else []
        departure = truck_leg.get("dep_time") if truck_leg is not None else None
        planned_travel = truck_leg.get("trav_time") if truck_leg is not None else None
        last = acts[-1].get("link")
        through_focus = any(l in FOCUS for l in route)
        if last in EASTBOUND:
            all_end_east[(attrs.get("trip_type"), "via_focus" if through_focus else "direct")] += 1
        if pid in stuck:
            # A MATSim network route lists the links BETWEEN the start and the end link, so the end
            # link is checked via the plan's last activity link (the route text does include it).
            first_west = next((i for i, l in enumerate(route) if l in WESTBOUND), None)
            first_focus = next((i for i, l in enumerate(route) if l in FOCUS), None)
            ends_east = last in EASTBOUND
            uturn = (ends_east and first_west is not None and first_focus is not None
                     and first_west < first_focus)
            starts_residential = acts[0].get("link") == "501844"
            rows.append({"person": pid, "trip_type": attrs.get("trip_type"), "stuck_link": stuck[pid][0],
                         "stuck_time_h": round(stuck[pid][1] / 3600, 2), "first_link": acts[0].get("link"),
                         "last_link": last, "route_links": len(route), "uturn_west_focus_east": uturn,
                         "truck_departure": departure, "planned_travel": planned_travel,
                         "starts_501844_via_focus": starts_residential and first_focus is not None,
                         "after_focus": route[first_focus:first_focus + 6] if first_focus is not None else [],
                         "last_route_links": route[-4:]})
    el.clear()

summary = {
    "stuck_trucks_on_focus": len(rows),
    "by_stuck_link": collections.Counter(r["stuck_link"] for r in rows),
    "by_trip_type": collections.Counter(r["trip_type"] for r in rows),
    "by_last_link": collections.Counter(r["last_link"] for r in rows).most_common(10),
    "by_first_link": collections.Counter(r["first_link"] for r in rows).most_common(10),
    "uturn_west_focus_east": collections.Counter(r["uturn_west_focus_east"] for r in rows),
    "starts_501844_via_focus": collections.Counter(r["starts_501844_via_focus"] for r in rows),
    "explained_by_old_snapping": sum(1 for r in rows if r["uturn_west_focus_east"] or r["starts_501844_via_focus"]),
    "last_route_links_of_uturners": collections.Counter(
        tuple(r["last_route_links"]) for r in rows if r["uturn_west_focus_east"]).most_common(3),
    "stuck_time_h": collections.Counter(r["stuck_time_h"] for r in rows).most_common(5),
    "truck_departure_hour": collections.Counter(
        int(r["truck_departure"].split(":")[0]) for r in rows if r["truck_departure"]).most_common(12),
    "all_freight_ending_on_eastbound_199731": {"%s|%s" % k: v for k, v in all_end_east.items()},
}
print(json.dumps({"summary": summary, "rows_sample": rows[:20]}, indent=1, default=dict))
