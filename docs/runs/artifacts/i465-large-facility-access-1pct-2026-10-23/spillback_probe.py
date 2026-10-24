"""Issue #465: attribute each >= 1 h through delay to its root bottleneck by following the vehicle's next links.

A vehicle delayed on link X waits in X's buffer because the link it enters next has no space (spillback) or
X's own outflow capacity is used up. The root is the first link along the vehicle's onward route whose own
departures in the window use >= 80 % of its simulated flow capacity, searched up to --depth links ahead.
"""
import argparse
import collections
import csv
import gzip
import os
import re

parser = argparse.ArgumentParser()
parser.add_argument("--run", required=True)
parser.add_argument("--probe", required=True)
parser.add_argument("--depth", type=int, default=40)
args = parser.parse_args()

delayed = []
with open(os.path.join(args.probe, "delayed_traversals.csv")) as f:
    for r in csv.DictReader(f):
        if r["kind"] == "through" and float(r["delay_s"]) >= 3600:
            delayed.append(r)
saturated = {}
with open(os.path.join(args.probe, "links_delay_ge_threshold.csv")) as f:
    for r in csv.DictReader(f):
        if r["departure_share_of_capacity"] and float(r["departure_share_of_capacity"]) >= 0.8:
            saturated[r["link"]] = r["top_facilities"]

targets = {(r["vehicle"], float(r["t_out"])): r for r in delayed}
vehicles = {r["vehicle"] for r in delayed}
route = collections.defaultdict(list)  # vehicle -> [(time, link)]
pattern = re.compile(r'(\w+)="([^"]*)"')
with gzip.open(os.path.join(args.run, "output_events.xml.gz"), "rt") as f:
    for line in f:
        if 'type="entered link"' in line or 'type="vehicle enters traffic"' in line:
            a = dict(pattern.findall(line))
            if a["vehicle"] in vehicles:
                route[a["vehicle"]].append((float(a["time"]), a["link"]))

roots = collections.Counter()
persons = set()
for r in delayed:
    v = r["vehicle"]
    persons.add(v.split(":")[0])
    seq = route[v]
    t_in = float(r["t_in"])
    idx = next(i for i, (t, l) in enumerate(seq) if l == r["link"] and abs(t - t_in) < 1e-6)
    root = r["link"] if r["link"] in saturated else None
    for t, l in seq[idx + 1: idx + 1 + args.depth]:
        if root is None and l in saturated:
            root = l
    roots[(root, saturated.get(root, ""))] += 1
print("delayed through traversals >= 1 h:", len(delayed), "distinct vehicles:", len(vehicles), "persons:", len(persons))
for (root, fac), n in roots.most_common():
    print(n, root, fac)
