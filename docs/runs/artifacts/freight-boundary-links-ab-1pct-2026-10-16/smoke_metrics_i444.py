"""Smoke / A-B metrics for one arm of the freight boundary-link 1 % run (eqasim-bs#444, ADR-0144). Read-only.

Usage: python smoke_metrics_i444.py <cache_dir> <arm> [<watch_link_id> ...]

Prints one JSON object. Every figure is measured from this arm's own files (sample counts at the run's
sampling rate, never expanded):
  * stuckAndAbort events of the last simulated iteration by leg mode, and the truck ones by link
    (top 15 links plus each watched link, default 501844, the A2-west residential link of #444);
  * truck departures / arrivals (how many freight legs started and finished at all);
  * the first and last activity link of every freight plan in the simulation output plans
    (freight = a plan with a truck leg), counted overall and for the watched links;
  * the attributes (capacity, freespeed, lanes, modes, length) of the watched and top stuck links;
  * the injection summaries the prepare stage wrote (freight_injection_summary.csv, and in the ON arm
    freight_boundary_link_summary.csv).
"""
import collections
import glob
import gzip
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

cache_dir, arm = sys.argv[1:3]
watch = sys.argv[3:] or ["501844"]
res = {"arm": arm, "watched_links": watch}


def newest(pattern):
    hits = glob.glob(pattern)
    if not hits:
        raise FileNotFoundError("nothing matches %s" % pattern)
    return max(hits, key=os.path.getmtime)


sim = newest(os.path.join(cache_dir, "matsim.simulation.run__*.cache", "simulation_output"))
prep = newest(os.path.join(cache_dir, "braunschweig.matsim.simulation.prepare__*.cache"))
res["simulation_output"] = sim
res["prepare_stage_dir"] = prep

# 1. events: stuck by mode, truck stuck by link, truck departures/arrivals
ATTR = re.compile(r'(\w+)="([^"]*)"')
stuck_by_mode = collections.Counter()
truck_stuck_by_link = collections.Counter()
truck_stuck_time_by_link = collections.defaultdict(list)
truck_departures = truck_arrivals = 0
stuck_persons = set()
with gzip.open(os.path.join(sim, "output_events.xml.gz"), "rt", encoding="utf-8") as fh:
    for line in fh:
        if "stuckAndAbort" in line:
            a = dict(ATTR.findall(line))
            if a.get("type") != "stuckAndAbort":
                continue
            mode = a.get("legMode", "null")
            stuck_by_mode[mode] += 1
            stuck_persons.add(a.get("person"))
            if mode == "truck":
                truck_stuck_by_link[a.get("link")] += 1
                truck_stuck_time_by_link[a.get("link")].append(float(a.get("time", "nan")))
        elif 'legMode="truck"' in line:
            if 'type="departure"' in line:
                truck_departures += 1
            elif 'type="arrival"' in line:
                truck_arrivals += 1
res["stuck_and_abort_events"] = sum(stuck_by_mode.values())
res["stuck_persons"] = len(stuck_persons)
res["stuck_by_leg_mode"] = dict(stuck_by_mode.most_common())
res["truck_stuck"] = stuck_by_mode.get("truck", 0)
res["truck_stuck_on_watched_links"] = {w: truck_stuck_by_link.get(w, 0) for w in watch}
res["truck_stuck_top_links"] = [
    {"link": link, "truck_stuck": n,
     "first_stuck_time_s": min(truck_stuck_time_by_link[link]), "last_stuck_time_s": max(truck_stuck_time_by_link[link])}
    for link, n in truck_stuck_by_link.most_common(15)]
res["truck_stuck_distinct_links"] = len(truck_stuck_by_link)
res["truck_departures"] = truck_departures
res["truck_arrivals"] = truck_arrivals

# 2. freight plans in the simulation output: first / last activity link
first_links, last_links = collections.Counter(), collections.Counter()
n_freight = 0
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_plans.xml.gz"), "rb"), events=("end",)):
    if el.tag != "person":
        continue
    plan = next((p for p in el.findall("plan") if p.get("selected") == "yes"), None)
    if plan is not None and any(leg.get("mode") == "truck" for leg in plan.findall("leg")):
        acts = plan.findall("activity")
        n_freight += 1
        first_links[acts[0].get("link")] += 1
        last_links[acts[-1].get("link")] += 1
    el.clear()
res["freight_plans"] = n_freight
res["freight_first_activity_on_watched_links"] = {w: first_links.get(w, 0) for w in watch}
res["freight_last_activity_on_watched_links"] = {w: last_links.get(w, 0) for w in watch}
res["freight_first_activity_top_links"] = first_links.most_common(10)
res["freight_last_activity_top_links"] = last_links.most_common(10)

# 3. network attributes of the watched and top stuck links
wanted = set(watch) | {d["link"] for d in res["truck_stuck_top_links"]}
links = {}
for _, el in ET.iterparse(gzip.open(os.path.join(sim, "output_network.xml.gz"), "rb"), events=("end",)):
    if el.tag == "link":
        if el.get("id") in wanted:
            info = {k: el.get(k) for k in ("from", "to", "length", "freespeed", "capacity", "permlanes", "modes")}
            attrs = el.find("attributes")
            if attrs is not None:
                for at in attrs:
                    if at.get("name") in ("osm:way:highway", "osm:way:id", "type"):
                        info[at.get("name")] = at.text
            links[el.get("id")] = info
        el.clear()
    elif el.tag == "node":
        el.clear()
res["link_attributes"] = links

# 4. injection summaries of the prepare stage (printed verbatim as rows)
for name in ("freight_injection_summary.csv", "freight_boundary_link_summary.csv"):
    path = os.path.join(prep, name)
    res[name] = open(path, encoding="utf-8").read().strip().splitlines() if os.path.exists(path) else None

print(json.dumps(res, indent=1, default=str))
