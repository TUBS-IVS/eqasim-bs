"""Smoke / A-B metrics for one arm of the portal-trips 1 % run (eqasim-bs#442). Read-only.

Usage: python smoke_metrics_i442.py <output_dir> <prefix> <cache_dir> <arm>
Prints one JSON object. All figures are measured from this run's own files.
"""
import glob
import gzip
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

import pandas as pd

out_dir, prefix, cache_dir, arm = sys.argv[1:5]
SIM_END_S = 30 * 3600
res = {"arm": arm}

# Simulation output (iteration 0 only: matsim_last_iteration 0)
sim = glob.glob(os.path.join(cache_dir, "matsim.simulation.run__*.cache", "simulation_output"))
sim = max(sim, key=os.path.getmtime)
res["simulation_output"] = sim

# 1. stuck agents (stuckAndAbort events), split residents vs freight by person id pattern
n_stuck, stuck_modes = 0, {}
stuck_persons = set()
with gzip.open(os.path.join(sim, "output_events.xml.gz"), "rt", encoding="utf-8") as fh:
    for line in fh:
        if 'type="stuckAndAbort"' in line:
            n_stuck += 1
            el = ET.fromstring(line.strip())
            stuck_modes[el.get("legMode")] = stuck_modes.get(el.get("legMode"), 0) + 1
            stuck_persons.add(el.get("person"))
res["stuck_and_abort_events"] = n_stuck
res["stuck_by_leg_mode"] = stuck_modes
res["stuck_freight"] = sum(1 for p in stuck_persons if p and ("freight" in p or "truck" in p))
res["stuck_persons"] = len(stuck_persons)

# 2. input plans (written population): plans ending after the simulation end, non-monotonic ends,
#    portal gate activities (portalGate marker)
pop = os.path.join(out_dir, f"{prefix}population.xml.gz")
n_persons = n_after_end = n_nonmono = n_gate_acts = n_outside_acts = 0
person_has_gate = 0
ctx = ET.iterparse(gzip.open(pop, "rb"), events=("end",))
for _, el in ctx:
    if el.tag != "person":
        continue
    n_persons += 1
    plan = el.find("plan")
    ends, last_time, has_gate = [], None, False
    for act in plan.findall("activity"):
        et = act.get("end_time")
        if et:
            h, m, s = (int(x) for x in et.split(":"))
            ends.append(h * 3600 + m * 60 + s)
        if act.get("type") == "outside":
            n_outside_acts += 1
            attrs = act.find("attributes")
            if attrs is not None and any(a.get("name") == "portalGate" and a.text == "true" for a in attrs):
                n_gate_acts += 1
                has_gate = True
    for leg in plan.findall("leg"):
        dep = leg.get("dep_time")
    if ends and max(ends) > SIM_END_S:
        n_after_end += 1
    if any(b < a for a, b in zip(ends, ends[1:])):
        n_nonmono += 1
    person_has_gate += has_gate
    el.clear()
res.update({"input_persons": n_persons, "input_plans_with_end_after_30h": n_after_end,
            "input_plans_nonmonotonic_end_times": n_nonmono, "input_outside_activities": n_outside_acts,
            "input_portal_gate_activities": n_gate_acts, "input_persons_with_portal_gate": person_has_gate})

# 3. pipeline trips: arrival beyond the sim end
trips = pd.read_csv(os.path.join(out_dir, f"{prefix}trips.csv"), sep=";")
res["pipeline_trips"] = int(len(trips))
res["pipeline_persons_trip_after_30h"] = int(trips.loc[trips["arrival_time"] > SIM_END_S, "person_id"].nunique())

# 4. simulated trips: walk legs > 20 km (euclidean), cross-boundary trips by mode
et = pd.read_csv(os.path.join(sim, "eqasim_trips.csv.gz"), sep=";")
res["sim_trips"] = int(len(et))
walk_far = et[(et["mode"] == "walk") & (et["euclidean_distance"] > 20000)]
res["sim_walk_trips_over_20km"] = int(len(walk_far))
touch = (et["preceding_purpose"] == "outside") | (et["following_purpose"] == "outside")
res["sim_trips_touching_outside"] = int(touch.sum())
res["sim_trips_touching_outside_by_mode"] = et.loc[touch, "mode"].value_counts().to_dict()
inreg = et[~touch & (et["mode"] != "outside")]
res["sim_in_region_mode_share_pct"] = {k: round(v * 100, 2) for k, v in inreg["mode"].value_counts(normalize=True).items()}
allm = et[et["mode"] != "outside"]
res["sim_all_trips_mode_share_pct"] = {k: round(v * 100, 2) for k, v in allm["mode"].value_counts(normalize=True).items()}

# 5. gate facility to link distance (prepared facilities carry linkRefId)
fac_files = glob.glob(os.path.join(cache_dir, "braunschweig.matsim.simulation.prepare__*.cache", "*facilities*.xml.gz"))
net_files = glob.glob(os.path.join(cache_dir, "braunschweig.matsim.simulation.prepare__*.cache", "*network*.xml.gz"))
if fac_files and net_files:
    fac_file = max(fac_files, key=os.path.getmtime)
    net_file = max(net_files, key=os.path.getmtime)
    portal = {}
    for _, el in ET.iterparse(gzip.open(fac_file, "rb"), events=("end",)):
        if el.tag == "facility":
            if el.get("id", "").startswith("portal_"):
                portal[el.get("id")] = (float(el.get("x")), float(el.get("y")), (el.get("linkRefId") or el.get("linkId")))
            el.clear()
    nodes, links = {}, {}
    wanted = {v[2] for v in portal.values()}
    for _, el in ET.iterparse(gzip.open(net_file, "rb"), events=("end",)):
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            if el.get("id") in wanted:
                links[el.get("id")] = (el.get("from"), el.get("to"))
            el.clear()
    dists = []
    for fid, (x, y, lid) in portal.items():
        if lid in links:
            (fx, fy), (tx, ty) = nodes[links[lid][0]], nodes[links[lid][1]]
            dists.append(math.hypot(x - (fx + tx) / 2, y - (fy + ty) / 2))
    s = pd.Series(dists, dtype=float)
    res["portal_facilities_prepared"] = len(portal)
    res["portal_facility_link_midpoint_distance_m"] = {
        "n": int(len(s)), "median": round(float(s.median()), 1) if len(s) else None,
        "p90": round(float(s.quantile(0.9)), 1) if len(s) else None, "max": round(float(s.max()), 1) if len(s) else None}
    res["prepared_files"] = [fac_file, net_file]
else:
    res["portal_facilities_prepared"] = "prepared facilities/network not found"

print(json.dumps(res, indent=1, default=str))
