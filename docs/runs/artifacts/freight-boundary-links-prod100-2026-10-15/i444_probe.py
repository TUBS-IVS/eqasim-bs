"""i444 probe: which links do freight start/end activities snap to, and what is around link 501844."""
import collections
import gzip
import math
import sys
import xml.etree.ElementTree as ET

network_path, plans_path = sys.argv[1], sys.argv[2]
FOCUS = "501844"
RADIUS_M = 1500.0

nodes = {}
links = {}
with gzip.open(network_path, "rb") as fh:
    for _ev, el in ET.iterparse(fh, events=("end",)):
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            attrs = {a.get("name"): a.text for a in el.iter("attribute")}
            links[el.get("id")] = dict(el.attrib, osm_type=attrs.get("osm:way:highway"))
            el.clear()

fx, fy = nodes[links[FOCUS]["from"]]
tx, ty = nodes[links[FOCUS]["to"]]
cx, cy = (fx + tx) / 2, (fy + ty) / 2
print("FOCUS", FOCUS, links[FOCUS], "from", (fx, fy), "to", (tx, ty))

near = []
for lid, l in links.items():
    ax, ay = nodes[l["from"]]
    bx, by = nodes[l["to"]]
    d = math.hypot((ax + bx) / 2 - cx, (ay + by) / 2 - cy)
    if d <= RADIUS_M and ("car" in l["modes"] or "truck" in l["modes"]):
        near.append((d, lid, l["from"], l["to"], l.get("osm_type"), l["capacity"], l["freespeed"],
                     l["permlanes"], l["length"], l["modes"]))
near.sort()
print("\nCAR LINKS WITHIN %.0f m OF FOCUS MIDPOINT (dist, id, from, to, highway, cap, vfree, lanes, len, modes)" % RADIUS_M)
for row in near:
    print("  %7.0f %-12s %-14s %-14s %-14s %8s %7s %4s %8s %s" % row)

# Freight start/end activity links by trip type.
start_links = collections.defaultdict(collections.Counter)
end_links = collections.defaultdict(collections.Counter)
start_xy = collections.defaultdict(list)
with gzip.open(plans_path, "rb") as fh:
    for _ev, el in ET.iterparse(fh, events=("end",)):
        if el.tag != "person":
            continue
        pid = el.get("id")
        if not pid.startswith("freight"):
            el.clear()
            continue
        attrs = {a.get("name"): a.text for a in el.iter("attribute")}
        trip_type = attrs.get("trip_type", "?")
        plan = next((p for p in el.findall("plan") if p.get("selected") == "yes"), None)
        acts = plan.findall("activity") if plan is not None else []
        if len(acts) >= 2:
            start_links[trip_type][acts[0].get("link")] += 1
            end_links[trip_type][acts[-1].get("link")] += 1
            if acts[0].get("link") == FOCUS or acts[-1].get("link") == FOCUS:
                start_xy[trip_type].append((acts[0].get("x"), acts[0].get("y"), acts[-1].get("x"), acts[-1].get("y")))
        el.clear()

for label, table in (("START", start_links), ("END", end_links)):
    print("\nFREIGHT %s LINKS (top 8 per trip type)" % label)
    for trip_type, counter in sorted(table.items()):
        total = sum(counter.values())
        print("  %s total %d, focus %d" % (trip_type, total, counter.get(FOCUS, 0)))
        for lid, n in counter.most_common(8):
            l = links.get(lid, {})
            print("     %-12s %6d  %-14s cap %s" % (lid, n, l.get("osm_type"), l.get("capacity")))

print("\nSAMPLE COORDS OF FREIGHT WITH FOCUS AS START OR END")
for trip_type, rows in start_xy.items():
    print(" ", trip_type, len(rows), collections.Counter(r[:2] for r in rows).most_common(3),
          collections.Counter(r[2:] for r in rows).most_common(3))
