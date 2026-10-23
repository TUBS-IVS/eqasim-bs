"""Probe (not committed): how parking lots around large facilities would carry a distributed car access.

For facilities >= 1000 activities (100 %-equivalent) of a run, using the OSM parking inventory of the parking
module (amenity=parking polygons, spaces = capacity tag else area / 25 m2, ASSUMPTION B-d of the parking module;
levels not applied): spaces within radius R, the nearest-first radius that covers f x activities spaces, and the
spaces-weighted mean facility-to-lot distance (a proxy of the car egress walk) under each candidate rule.
"""
import argparse
import collections
import csv
import gzip
import json
import xml.etree.ElementTree as ET

import numpy as np
import pyogrio

parser = argparse.ArgumentParser()
parser.add_argument("--inventory", required=True)
parser.add_argument("--facilities", required=True)
parser.add_argument("--activities", required=True)
parser.add_argument("--sample-size", type=float, required=True)
parser.add_argument("--minimum-activities", type=float, default=1000.0)
args = parser.parse_args()

lots = pyogrio.read_dataframe(args.inventory, layer="multipolygons").to_crs(25832)
tags = lots.tags.map(json.loads)
lots = lots[tags.map(lambda t: t.get("amenity") == "parking")].copy()
tags = lots.tags.map(json.loads)
street_side = {"street_side", "lane", "on_kerb", "half_on_kerb", "shoulder", "layby"}
lots = lots[~tags.map(lambda t: t.get("parking") in street_side)].copy()
tags = lots.tags.map(json.loads)
area = lots.geometry.area.to_numpy()
capacity = tags.map(lambda t: t.get("capacity")).to_numpy()
spaces = np.array([int(c) if (c or "").isdigit() else round(a / 25.0) for c, a in zip(capacity, area)], dtype=float)
centroids = lots.geometry.centroid
xy = np.column_stack([centroids.x.to_numpy(), centroids.y.to_numpy()])
bounds = lots.total_bounds
print("lots", len(lots), "spaces", int(spaces.sum()), "inventory bounds", bounds.round(0))

facilities = {}
with gzip.open(args.facilities, "rb") as fh:
    for _event, element in ET.iterparse(fh, events=("end",)):
        if element.tag == "facility":
            facilities[element.get("id")] = (float(element.get("x")), float(element.get("y")))
            element.clear()
counts = collections.Counter()
with gzip.open(args.activities, "rt", encoding="utf-8") as fh:
    for row in csv.DictReader(fh, delimiter=";"):
        if row["facility_id"]:
            counts[row["facility_id"]] += 1

rows = []
for fid, n in counts.items():
    size = n / args.sample_size
    if size < args.minimum_activities or fid.startswith("outside_") or fid not in facilities:
        continue
    x, y = facilities[fid]
    inside_box = bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]
    d = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
    order = np.argsort(d)
    cumulative = np.cumsum(spaces[order])
    row = {"facility": fid, "size": size, "inside_box": inside_box}
    for radius in (500, 1000, 1500, 2000):
        mask = d <= radius
        row[f"lots_{radius}"] = int(mask.sum())
        row[f"spaces_{radius}"] = float(spaces[mask].sum())
        row[f"wdist_{radius}"] = float((spaces[mask] * d[mask]).sum() / spaces[mask].sum()) if mask.any() and spaces[mask].sum() > 0 else None
    for fraction in (0.5, 1.0):
        need = fraction * size
        k = int(np.searchsorted(cumulative, need)) + 1
        k = min(k, len(order))
        chosen = order[:k]
        row[f"cover{fraction}_radius"] = float(d[chosen[-1]])
        row[f"cover{fraction}_lots"] = k
        row[f"cover{fraction}_wdist"] = float((spaces[chosen] * d[chosen]).sum() / max(spaces[chosen].sum(), 1.0))
    rows.append(row)

inside = [r for r in rows if r["inside_box"]]
print("large facilities", len(rows), "inside the inventory box", len(inside))


def q(values):
    values = [v for v in values if v is not None]
    return [round(float(np.percentile(values, p))) for p in (10, 50, 90)] if values else None


for radius in (500, 1000, 1500, 2000):
    print(f"R={radius}: facilities with no lot {sum(r[f'lots_{radius}'] == 0 for r in inside)},"
          f" with < 2 lots {sum(r[f'lots_{radius}'] < 2 for r in inside)};"
          f" spaces/size p10/50/90 {q([r[f'spaces_{radius}'] / r['size'] for r in inside])};"
          f" spaces-weighted distance p10/50/90 {q([r[f'wdist_{radius}'] for r in inside])} m")
for fraction in (0.5, 1.0):
    print(f"nearest-first cover {fraction} x size: radius p10/50/90 {q([r[f'cover{fraction}_radius'] for r in inside])} m,"
          f" lots p10/50/90 {q([r[f'cover{fraction}_lots'] for r in inside])},"
          f" spaces-weighted distance p10/50/90 {q([r[f'cover{fraction}_wdist'] for r in inside])} m")
for fid in ("work_101999", "work_102026", "work_105575", "work_97721", "uni_loc_15", "sec_b_16926"):
    r = next((r for r in rows if r["facility"] == fid), None)
    if r:
        print(fid, round(r["size"]), "inside" if r["inside_box"] else "OUTSIDE",
              {k: (round(v) if isinstance(v, float) else v) for k, v in r.items() if k.startswith(("lots_", "spaces_", "wdist_", "cover"))})
