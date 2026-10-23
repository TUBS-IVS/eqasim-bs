"""#465: for the >= 1 h delayed traversals of the upstream links (link_delay_probe.py delayed_traversals.csv),
count those whose trip continued to one of the target links. Usage:
upstream_probe.py <delayed_traversals.csv> <bbox_events.csv> <upstream,links> <target,links>"""
import csv, collections, sys
delayed, events, ups, tgt = sys.argv[1], sys.argv[2], set(sys.argv[3].split(",")), set(sys.argv[4].split(","))
targets = collections.defaultdict(list)
for r in csv.DictReader(open(delayed)):
    if r["link"] in ups and float(r["delay_s"]) >= 3600: targets[r["vehicle"]].append((r["link"], float(r["t_out"])))
seq = collections.defaultdict(list)
for r in csv.DictReader(open(events)):
    if r["vehicle"] in targets and r["type"] in ("entered", "leaves_traffic", "enters_traffic"):
        seq[r["vehicle"]].append((float(r["time"]), r["type"], r["link"]))
res = collections.Counter()
for v, lst in targets.items():
    for link, tout in lst:
        reached = False
        for t, ty, l in (x for x in seq[v] if x[0] >= tout):
            if ty != "entered": break
            if l in tgt: reached = True; break
        res[(link, reached)] += 1
print("delayed >= 1 h traversals, (link, continued to a target link in the same trip):", dict(sorted(res.items())))
