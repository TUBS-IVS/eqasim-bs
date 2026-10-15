"""i444 probe 2: every distinct freight boundary endpoint, the link MATSim snapped it to, and the nearest major link."""
import collections
import gzip
import math
import sys
import xml.etree.ElementTree as ET

network_path, plans_path = sys.argv[1], sys.argv[2]
MAJOR = {"motorway", "trunk", "primary", "motorway_link", "trunk_link"}

nodes, links = {}, {}
with gzip.open(network_path, "rb") as fh:
    for _ev, el in ET.iterparse(fh, events=("end",)):
        if el.tag == "node":
            nodes[el.get("id")] = (float(el.get("x")), float(el.get("y")))
            el.clear()
        elif el.tag == "link":
            attrs = {a.get("name"): a.text for a in el.iter("attribute")}
            links[el.get("id")] = dict(el.attrib, hw=attrs.get("osm:way:highway"))
            el.clear()


def segment_distance(px, py, lid):
    ax, ay = nodes[links[lid]["from"]]
    bx, by = nodes[links[lid]["to"]]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx - px, ay + t * dy - py)


for lid in ("501844", "199731", "199736", "450785"):
    l = links[lid]
    print(lid, l["hw"], "from", nodes[l["from"]], "to", nodes[l["to"]])

# (role, x, y, snapped link) -> count; role = start for incoming/transit, end for outgoing/transit.
endpoints = collections.Counter()
with gzip.open(plans_path, "rb") as fh:
    for _ev, el in ET.iterparse(fh, events=("end",)):
        if el.tag != "person":
            continue
        if el.get("id").startswith("freight"):
            attrs = {a.get("name"): a.text for a in el.iter("attribute")}
            trip_type = attrs.get("trip_type")
            plan = next((p for p in el.findall("plan") if p.get("selected") == "yes"), None)
            acts = plan.findall("activity")
            if trip_type in ("incoming", "transit"):
                a = acts[0]
                endpoints[("start", a.get("x"), a.get("y"), a.get("link"))] += 1
            if trip_type in ("outgoing", "transit"):
                a = acts[-1]
                endpoints[("end", a.get("x"), a.get("y"), a.get("link"))] += 1
        el.clear()

major_ids = [lid for lid, l in links.items() if l["hw"] in MAJOR and "car" in l["modes"]]
print("\nBOUNDARY ENDPOINTS with >= 50 trucks: role count x y | snapped link hw cap dist | nearest major link hw cap dist")
total = collections.Counter()
minor = collections.Counter()
for (role, x, y, lid), n in endpoints.most_common():
    total[role] += n
    hw = links[lid]["hw"]
    if hw not in MAJOR:
        minor[role] += n
    if n < 50:
        continue
    px, py = float(x), float(y)
    best = min(major_ids, key=lambda m: segment_distance(px, py, m))
    print("  %-5s %6d %10.1f %11.1f | %-8s %-13s %6s %6.0f | %-8s %-13s %6s %6.0f" % (
        role, n, px, py, lid, hw, links[lid]["capacity"], segment_distance(px, py, lid),
        best, links[best]["hw"], links[best]["capacity"], segment_distance(px, py, best)))
for role in total:
    print("ROLE %s: %d boundary endpoints, %d snapped to a non-major link (%.1f %%)"
          % (role, total[role], minor[role], 100.0 * minor[role] / total[role]))
