"""#465 portal probe: every outside_* facility with its link, road class and whether the link's downstream
continuation reaches a turnaround (the link's reverse direction) within --max-km of driving, i.e. whether a
vehicle departing there must drive outward and U-turn to get back into the region."""
import gzip, sys, csv, heapq, math, collections, xml.etree.ElementTree as ET
run, out = sys.argv[1], sys.argv[2]
prefix = sys.argv[3] if len(sys.argv) > 3 else "outside_"
links = {}; nodes = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    cur = None
    for ev, el in ET.iterparse(f, events=("start", "end")):
        if ev == "start" and el.tag == "link": cur = dict(el.attrib)
        elif ev == "end" and el.tag == "attribute" and cur is not None: cur[el.attrib["name"]] = el.text
        elif ev == "end" and el.tag == "link": links[cur["id"]] = cur; cur = None; el.clear()
        elif ev == "end" and el.tag == "node": nodes[el.attrib["id"]] = (float(el.attrib["x"]), float(el.attrib["y"])); el.clear()
out_of = collections.defaultdict(list)
for l in links.values():
    if "car" in l["modes"]: out_of[l["from"]].append(l["id"])
fac = []
with gzip.open(f"{run}/output_facilities.xml.gz", "rt") as f:
    for line in f:
        if f"<facility id=\"{prefix}" in line:
            a = dict(x.split('="') for x in line.strip().strip("<>").replace('facility ', '').rstrip('"').split('" '))
            fac.append(a)
xs = [p[0] for p in nodes.values()]; ys = [p[1] for p in nodes.values()]
cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
def reach_back(lid, limit=30000.0):
    """Shortest car driving distance from the end of lid to the from-node of lid (= back where it started)."""
    start, goal = links[lid]["to"], links[lid]["from"]
    dist = {start: 0.0}; pq = [(0.0, start)]
    while pq:
        d, n = heapq.heappop(pq)
        if n == goal: return d
        if d > limit or d > dist.get(n, 1e18): continue
        for o in out_of[n]:
            nd = d + float(links[o]["length"]); m = links[o]["to"]
            if nd < dist.get(m, 1e18): dist[m] = nd; heapq.heappush(pq, (nd, m))
    return float("nan")
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["facility", "link", "highway", "capacity", "lanes", "outward", "return_detour_m", "x", "y"])
    for a in fac:
        l = links[a["linkId"]]; fx, fy = nodes[l["from"]]; tx, ty = nodes[l["to"]]
        outward = math.hypot(tx - cx, ty - cy) > math.hypot(fx - cx, fy - cy)
        w.writerow([a["id"], a["linkId"], l.get("osm:way:highway", ""), l["capacity"], l["permlanes"], outward,
                    (lambda v: "" if v != v else round(v))(reach_back(a["linkId"])), a["x"], a["y"]])
print("facilities", len(fac), "centre", round(cx), round(cy))
