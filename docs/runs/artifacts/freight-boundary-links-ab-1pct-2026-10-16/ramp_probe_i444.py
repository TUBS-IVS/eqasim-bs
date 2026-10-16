"""Ramp bottleneck probe for eqasim-bs#444 (links 182357 / 18416). Read-only.

Usage: python ramp_probe_i444.py <simulation_output_dir> <link_id> [<link_id> ...]

Prints JSON with:
  * topology: every link entering / leaving the from- and to-node of each probed link
    (highway, capacity veh/h, freespeed km/h, lanes, length m, modes, bearing deg);
  * the stuck trucks on the probed links: departure time, the route window of 12 links around the
    probed link (with highway/capacity), the time each one entered the probed link;
  * the load on each probed link: link-enter events per hour, split truck vs other vehicles.
"""
import collections
import gzip
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

sim, probe = sys.argv[1], sys.argv[2:]
ATTR = re.compile(r'(\w+)="([^"]*)"')

nodes, links = {}, {}
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_network.xml.gz"), "rb"), events=("end",)):
    if el.tag == "node":
        nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
        el.clear()
    elif el.tag == "link":
        info = {k: el.get(k) for k in ("from", "to", "length", "freespeed", "capacity", "permlanes", "modes")}
        attrs = el.find("attributes")
        if attrs is not None:
            for at in attrs:
                if at.get("name") in ("osm:way:highway", "osm:way:id", "osm:way:name", "osm:way:ref", "osm:way:oneway"):
                    info[at.get("name")] = at.text
        links[el.get("id")] = info
        el.clear()

out_of, into = collections.defaultdict(list), collections.defaultdict(list)
for lid, info in links.items():
    out_of[info["from"]].append(lid)
    into[info["to"]].append(lid)


def describe(lid):
    i = links[lid]
    (fx, fy), (tx, ty) = nodes[i["from"]], nodes[i["to"]]
    return {"link": lid, "hw": i.get("osm:way:highway"), "ref": i.get("osm:way:ref"), "name": i.get("osm:way:name"),
            "cap": float(i["capacity"]), "kmh": round(float(i["freespeed"]) * 3.6), "lanes": float(i["permlanes"]),
            "len": round(float(i["length"])), "modes": i["modes"], "osm": i.get("osm:way:id"),
            "bearing": round(math.degrees(math.atan2(tx - fx, ty - fy)) % 360),
            "from_xy": (round(fx), round(fy)), "to_xy": (round(tx), round(ty))}


res = {"topology": {}}
for lid in probe:
    i = links[lid]
    res["topology"][lid] = {
        "self": describe(lid),
        "into_from_node": [describe(x) for x in into[i["from"]]],
        "out_of_from_node": [describe(x) for x in out_of[i["from"]] if x != lid],
        "into_to_node": [describe(x) for x in into[i["to"]] if x != lid],
        "out_of_to_node": [describe(x) for x in out_of[i["to"]]],
    }

# stuck trucks on the probed links and the load per hour
stuck, enters = {}, collections.defaultdict(lambda: collections.Counter())
first_enter = {}
departures = {}
with gzip.open(os.path.join(sim, "output_events.xml.gz"), "rt", encoding="utf-8") as fh:
    for line in fh:
        if 'type="entered link"' in line:
            a = dict(ATTR.findall(line))
            if a["link"] in probe:
                h = int(float(a["time"]) // 3600)
                kind = "truck" if "freight" in a["vehicle"] else "other"
                enters[a["link"]][(h, kind)] += 1
                first_enter.setdefault((a["vehicle"], a["link"]), float(a["time"]))
        elif 'type="stuckAndAbort"' in line and 'legMode="truck"' in line:
            a = dict(ATTR.findall(line))
            if a["link"] in probe:
                stuck[a["person"]] = a["link"]
        elif 'type="departure"' in line and 'legMode="truck"' in line:
            a = dict(ATTR.findall(line))
            departures.setdefault(a["person"], float(a["time"]))

routes = {}
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_plans.xml.gz"), "rb"), events=("end",)):
    if el.tag == "person":
        if el.get("id") in stuck:
            plan = next(p for p in el.findall("plan") if p.get("selected") == "yes")
            for leg in plan.findall("leg"):
                route = leg.find("route")
                if leg.get("mode") == "truck" and route is not None:
                    routes[el.get("id")] = route.text.split()
        el.clear()

res["stuck_trucks"] = []
for person, lid in sorted(stuck.items()):
    r = routes.get(person, [])
    k = r.index(lid) if lid in r else None
    window = r[max(0, k - 6):k + 6] if k is not None else []
    vehicle_enter = [t for (v, l), t in first_enter.items() if l == lid and v.startswith(person)]
    res["stuck_trucks"].append({
        "person": person, "stuck_link": lid, "departure_s": departures.get(person),
        "route_length_links": len(r), "position_in_route": k,
        "entered_stuck_link_s": min(vehicle_enter) if vehicle_enter else None,
        "route_window": [(x, links[x].get("osm:way:highway"), float(links[x]["capacity"])) for x in window]})
res["enters_per_hour"] = {lid: sorted(("%02d" % h, kind, n) for (h, kind), n in c.items()) for lid, c in enters.items()}
print(json.dumps(res, indent=1))
