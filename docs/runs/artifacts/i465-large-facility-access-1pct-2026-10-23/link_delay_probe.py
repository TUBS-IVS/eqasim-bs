"""Issue #465 road-side probe: links where departing vehicles saturate the outflow and through traffic waits.

Reads a MATSim output directory (network, events, activities, config) read-only and writes CSVs to --out.
Delay of one traversal = time left link - time entered link - freespeed travel time (length / freespeed).
A departure traversal starts at 'vehicle enters traffic' (relativePosition 1.0 -> free time ~0).
"""
import argparse
import collections
import csv
import gzip
import os
import re
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument("--run", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--events", default=None)
parser.add_argument("--delay-threshold-s", type=float, default=3600.0)
args = parser.parse_args()
os.makedirs(args.out, exist_ok=True)

config = open(os.path.join(args.run, "output_config.xml"), encoding="utf-8").read()
qsim = config[config.index('<module name="qsim"'):]
fcf = float(re.search(r'name="flowCapacityFactor" value="([^"]+)"', qsim).group(1))
print("qsim flowCapacityFactor", fcf)

links = {}
with gzip.open(os.path.join(args.run, "output_network.xml.gz")) as f:
    current = None
    for event, element in ET.iterparse(f, events=("start", "end")):
        if event == "start" and element.tag == "link":
            current = dict(element.attrib)
        elif event == "end" and element.tag == "attribute" and current is not None:
            current[element.attrib["name"]] = element.text
        elif event == "end" and element.tag == "link":
            links[current["id"]] = current
            current = None
            element.clear()
print("links", len(links))

# facilities per link and activity counts (final plans)
activities_per_link = collections.Counter()
facility_activities = collections.Counter()
facility_link = {}
with gzip.open(os.path.join(args.run, "output_activities.csv.gz"), "rt") as f:
    for row in csv.DictReader(f, delimiter=";"):
        activities_per_link[row["link_id"]] += 1
        facility_activities[row["facility_id"]] += 1
        facility_link[row["facility_id"]] = row["link_id"]

enter = {}  # vehicle -> (link, time, kind)
traversals = []  # link, vehicle, kind, t_in, t_out, delay
departures = collections.defaultdict(list)
pattern = re.compile(r'(\w+)="([^"]*)"')
with gzip.open(args.events or os.path.join(args.run, "output_events.xml.gz"), "rt") as f:
    for line in f:
        if "link" not in line:
            continue
        if 'type="entered link"' in line:
            a = dict(pattern.findall(line))
            enter[a["vehicle"]] = (a["link"], float(a["time"]), "through")
        elif 'type="vehicle enters traffic"' in line:
            a = dict(pattern.findall(line))
            enter[a["vehicle"]] = (a["link"], float(a["time"]), "departure")
            departures[a["link"]].append((float(a["time"]), a["vehicle"]))
        elif 'type="left link"' in line or 'type="vehicle leaves traffic"' in line:
            a = dict(pattern.findall(line))
            v = a["vehicle"]
            if v in enter and enter[v][0] == a["link"]:
                link_id, t_in, kind = enter.pop(v)
                t_out = float(a["time"])
                l = links[link_id]
                free = float(l["length"]) / float(l["freespeed"]) if kind == "through" else 0.0
                if 'leaves traffic' in line:
                    kind = kind + "_arrival"
                    free = 0.0
                delay = t_out - t_in - free
                if delay > 60:
                    traversals.append((link_id, v, kind, t_in, t_out, delay))
        elif 'type="vehicle aborts"' in line or 'stuckAndAbort' in line:
            pass
print("traversals with delay > 60 s", len(traversals))

with open(os.path.join(args.out, "delayed_traversals.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["link", "vehicle", "kind", "t_in", "t_out", "delay_s"])
    w.writerows(traversals)

by_link = collections.defaultdict(list)
for t in traversals:
    by_link[t[0]].append(t)

rows = []
for link_id, ts in by_link.items():
    big = [t for t in ts if t[5] >= args.delay_threshold_s]
    if not big:
        continue
    l = links[link_id]
    window_start = min(t[3] for t in big)
    window_end = max(t[4] for t in big)
    deps = [d for d in departures[link_id] if window_start <= d[0] <= window_end]
    cap_sim = float(l["capacity"]) * fcf
    hours = max((window_end - window_start) / 3600.0, 1e-9)
    facs = sorted(((facility_activities[fid], fid) for fid, lk in facility_link.items() if lk == link_id), reverse=True)
    rows.append({
        "link": link_id,
        "highway": l.get("osm:way:highway"),
        "name": l.get("osm:way:name"),
        "osm_lanes": l.get("osm:way:lanes"),
        "permlanes": l["permlanes"],
        "capacity": l["capacity"],
        "length": round(float(l["length"]), 1),
        "sim_capacity_veh_h": round(cap_sim, 2),
        "n_delay_ge_threshold": len(big),
        "n_through_ge_threshold": sum(1 for t in big if t[2].startswith("through")),
        "n_departure_ge_threshold": sum(1 for t in big if t[2].startswith("departure")),
        "max_delay_h": round(max(t[5] for t in big) / 3600.0, 2),
        "window": "%.2f-%.2f h" % (window_start / 3600, window_end / 3600),
        "departures_in_window": len(deps),
        "departures_per_h_in_window": round(len(deps) / hours, 2),
        "departure_share_of_capacity": round(len(deps) / hours / cap_sim, 2) if cap_sim > 0 else None,
        "activities_on_link": activities_per_link[link_id],
        "top_facilities": " ".join("%s:%d" % (fid, n) for n, fid in facs[:3]),
    })
rows.sort(key=lambda r: -r["n_delay_ge_threshold"])
with open(os.path.join(args.out, "links_delay_ge_threshold.csv"), "w", newline="") as f:
    if rows:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
print("links with a traversal delay >= %.0f s: %d" % (args.delay_threshold_s, len(rows)))
print("traversals >= threshold:", sum(r["n_delay_ge_threshold"] for r in rows),
      "through:", sum(r["n_through_ge_threshold"] for r in rows))
for r in rows[:25]:
    print(r)

# tertiary capacity conventions across the network
conv = collections.Counter()
for l in links.values():
    if "car" in l["modes"].split(","):
        conv[(l.get("osm:way:highway"), l.get("osm:way:lanes"), l["permlanes"], l["capacity"], l.get("oneway"))] += 1
with open(os.path.join(args.out, "capacity_conventions.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["highway", "osm_lanes", "permlanes", "capacity", "oneway", "n_links"])
    for k, n in sorted(conv.items(), key=lambda kv: -kv[1]):
        w.writerow(list(k) + [n])
