"""Who is stuck on a link, and where do those trucks start and end? Read-only probe for eqasim-bs#444.

Usage: python stuck_link_probe_i444.py <simulation_output_dir> <link_id> [<link_id> ...]

For each link: its from/to node coordinates (EPSG:25832, metres), and for every truck agent with a
stuckAndAbort event on it: the person id prefix, the first and last activity link and type of its
selected plan, and the stuck time. Prints JSON.
"""
import collections
import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sim, links = sys.argv[1], sys.argv[2:]
ATTR = re.compile(r'(\w+)="([^"]*)"')

stuck = {}
with gzip.open(os.path.join(sim, "output_events.xml.gz"), "rt", encoding="utf-8") as fh:
    for line in fh:
        if 'type="stuckAndAbort"' in line and 'legMode="truck"' in line:
            a = dict(ATTR.findall(line))
            if a.get("link") in links:
                stuck[a["person"]] = (a["link"], float(a["time"]))

plans = {}
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_plans.xml.gz"), "rb"), events=("end",)):
    if el.tag == "person":
        if el.get("id") in stuck:
            plan = next(p for p in el.findall("plan") if p.get("selected") == "yes")
            acts = plan.findall("activity")
            plans[el.get("id")] = {"first": (acts[0].get("type"), acts[0].get("link")),
                                   "last": (acts[-1].get("type"), acts[-1].get("link"))}
        el.clear()

net_links, nodes = {}, {}
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_network.xml.gz"), "rb"), events=("end",)):
    if el.tag == "node":
        nodes[el.get("id")] = (round(float(el.get("x")), 1), round(float(el.get("y")), 1))
        el.clear()
    elif el.tag == "link":
        if el.get("id") in links:
            net_links[el.get("id")] = (el.get("from"), el.get("to"))
        el.clear()

out = {}
for link in links:
    persons = [p for p, (lk, _) in stuck.items() if lk == link]
    fr, to = net_links.get(link, (None, None))
    out[link] = {
        "from_xy": nodes.get(fr), "to_xy": nodes.get(to), "truck_stuck": len(persons),
        "stuck_times_s": sorted(collections.Counter(stuck[p][1] for p in persons).items()),
        "person_id_prefixes": collections.Counter(re.sub(r"[0-9]+$", "", p) for p in persons).most_common(),
        "first_activity": collections.Counter("%s@%s" % plans[p]["first"] for p in persons).most_common(),
        "last_activity": collections.Counter("%s@%s" % plans[p]["last"] for p in persons).most_common(),
    }
print(json.dumps(out, indent=1))
