"""#465 / ADR-0167: where do the trips of the gate facilities cross the cordon after the cut?

Selects the persons who use a facility placed at a road gate in the UNCUT population (an in-commuter home at a gate
point, ``home_*`` at a gate coordinate, or a ``portal_gate_*`` stay) and classifies every ``outside`` activity of
these persons in the final plans of a MATSim run (``output_activities.csv.gz``) by

- role: ``departure`` (the next activity is inside, or it is the first activity) or ``arrival`` (the previous one is
  inside, or it is the last activity); an outside activity between two outside ones is ``between``;
- the link it sits on in the run's (cut) network: road class, capacity in veh/h, and direction against the cut
  extent (``inward``: from-node outside, to-node inside; ``outward``: the reverse; ``other``).

A gate trip is on the right link when a departure sits on an inward link and an arrival on an outward link, both of at
least ``--minimum-capacity`` veh/h. The summary counts only the outside activities whose link midpoint lies within
``--gate-radius`` metres of a road gate point (the gate trips); the CSV has every outside activity of these persons
with its ``nearest_gate_m``. Writes ``<out>/gate_outside_links.csv`` (one row per outside activity) and
prints the counts. Read only; run with the eqasim env.

Usage: python gate_outside_links.py RUN_OUTPUT_DIR UNCUT_POPULATION GATES_PICKLE EXTENT_GPKG OUT [--minimum-capacity 1000]
"""
import argparse
import collections
import csv
import gzip
import os
import pickle
import re
import xml.etree.ElementTree as ET

import geopandas as gpd
import shapely

parser = argparse.ArgumentParser()
parser.add_argument("run")
parser.add_argument("population")
parser.add_argument("gates")
parser.add_argument("extent")
parser.add_argument("out")
parser.add_argument("--minimum-capacity", type=float, default=1000.0)
parser.add_argument("--gate-radius", type=float, default=500.0)
args = parser.parse_args()

gates = pickle.load(open(args.gates, "rb"))["gates"]
gate_xy = {(round(p.x, 3), round(p.y, 3)) for p in gates.geometry}
extent = gpd.read_file(args.extent).geometry.union_all() if hasattr(gpd.GeoSeries, "union_all") \
    else gpd.read_file(args.extent).geometry.unary_union

# 1) persons with a gate facility in the uncut population (activity coordinates are written by the population writer)
persons = set()
kind = {}
with gzip.open(args.population, "rt", encoding="utf-8") as handle:
    person = None
    for line in handle:
        m = re.search(r'<person id="([^"]+)"', line)
        if m:
            person = m.group(1)
            continue
        if "<activity" not in line:
            continue
        facility = re.search(r'facility="([^"]+)"', line)
        x, y = re.search(r'\bx="([^"]+)"', line), re.search(r'\by="([^"]+)"', line)
        fid = facility.group(1) if facility else ""
        if fid.startswith("portal_gate_"):
            persons.add(person)
            kind[person] = "portal"
        elif fid.startswith("home_") and x and y and (round(float(x.group(1)), 3), round(float(y.group(1)), 3)) in gate_xy:
            persons.add(person)
            kind.setdefault(person, "gate_home")

# 2) the run's network
nodes, links = {}, {}
with gzip.open(os.path.join(args.run, "output_network.xml.gz")) as handle:
    current = None
    for event, el in ET.iterparse(handle, events=("start", "end")):
        if event == "start" and el.tag == "link":
            current = dict(el.attrib)
        elif event == "end" and el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif event == "end" and el.tag == "attribute" and current is not None:
            current[el.get("name")] = el.text
        elif event == "end" and el.tag == "link":
            links[current["id"]] = current
            current = None
            el.clear()


def direction(link):
    fx, fy = nodes[link["from"]]
    tx, ty = nodes[link["to"]]
    f_in, t_in = shapely.contains_xy(extent, fx, fy), shapely.contains_xy(extent, tx, ty)
    return "inward" if (not f_in and t_in) else "outward" if (f_in and not t_in) else "other"


# 3) the outside activities of these persons in the final plans
by_person = collections.defaultdict(list)
with gzip.open(os.path.join(args.run, "output_activities.csv.gz"), "rt") as handle:
    for row in csv.DictReader(handle, delimiter=";"):
        if row["person"] in persons:
            by_person[row["person"]].append(row)

rows = []
for person, acts in by_person.items():
    acts.sort(key=lambda r: int(r["activity_number"]))
    for i, act in enumerate(acts):
        if act["activity_type"] != "outside":
            continue
        prev_inside = i > 0 and acts[i - 1]["activity_type"] != "outside"
        next_inside = i + 1 < len(acts) and acts[i + 1]["activity_type"] != "outside"
        role = "arrival" if (prev_inside or i == len(acts) - 1) and not next_inside else \
            "departure" if (next_inside or i == 0) and not prev_inside else "between" if not (prev_inside or next_inside) \
            else "arrival_and_departure"
        link = links.get(act["link_id"])
        capacity = float(link["capacity"]) if link else float("nan")
        d = direction(link) if link else "missing"
        ok = (capacity >= args.minimum_capacity) and ((role == "departure" and d == "inward")
                                                      or (role == "arrival" and d == "outward"))
        if link:
            (fx, fy), (tx, ty) = nodes[link["from"]], nodes[link["to"]]
            mx, my = (fx + tx) / 2.0, (fy + ty) / 2.0
            nearest = min(((gx - mx) ** 2 + (gy - my) ** 2) ** 0.5 for gx, gy in gate_xy)
        else:
            nearest = float("nan")
        rows.append({"person": person, "nearest_gate_m": round(nearest, 1), "kind": kind[person],
                     "activity_number": act["activity_number"], "role": role,
                     "link_id": act["link_id"], "road_class": (link or {}).get("osm:way:highway", ""),
                     "capacity_veh_per_h": capacity, "direction": d, "on_right_link": ok})

os.makedirs(args.out, exist_ok=True)
with open(os.path.join(args.out, "gate_outside_links.csv"), "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["person"])
    writer.writeheader()
    writer.writerows(rows)

print("persons with a gate facility: %d (%s)" % (len(persons), dict(collections.Counter(kind.values()))))
print("outside activities of these persons in the final plans: %d" % len(rows))
rows = [r for r in rows if r["nearest_gate_m"] <= args.gate_radius]
print("of them within %.0f m of a road gate point (the summary below): %d" % (args.gate_radius, len(rows)))
for key in ("kind", "role", "direction"):
    print(key, dict(collections.Counter(r[key] for r in rows)))
low = [r for r in rows if not r["capacity_veh_per_h"] >= args.minimum_capacity]
print("on a link below %.0f veh/h: %d" % (args.minimum_capacity, len(low)))
print("road classes:", dict(collections.Counter(r["road_class"] for r in rows)))
print("on the right link (departure inward / arrival outward, >= minimum capacity): %d of %d"
      % (sum(r["on_right_link"] for r in rows), len(rows)))
print("not on the right link, by kind/role/direction/class:",
      dict(collections.Counter((r["kind"], r["role"], r["direction"], r["road_class"]) for r in rows
                               if not r["on_right_link"])))
