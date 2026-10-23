"""#465: where do vehicles start/end inside the bbox (link, road class, x), split by vehicle kind; and who
passes the portal link eastward (left 450785 -> entered 450786)."""
import csv, gzip, sys, collections, xml.etree.ElementTree as ET
run, d = sys.argv[1], sys.argv[2]
links = {}; nodes = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    cur = None
    for ev, el in ET.iterparse(f, events=("start", "end")):
        if ev == "start" and el.tag == "link": cur = dict(el.attrib)
        elif ev == "end" and el.tag == "attribute" and cur is not None: cur[el.attrib["name"]] = el.text
        elif ev == "end" and el.tag == "link": links[cur["id"]] = cur; cur = None; el.clear()
        elif ev == "end" and el.tag == "node": nodes[el.attrib["id"]] = float(el.attrib["x"])
def kind(v):
    if v.startswith("freight_transit"): return "freight_transit"
    if v.startswith("freight"): return "freight_other"
    if v.startswith("ic_") or "incomm" in v: return "incommuter"
    return "resident/other"
c = collections.Counter(); east = collections.Counter(); last = {}
for r in csv.DictReader(open(f"{d}/bbox_events.csv")):
    t, v, l = r["type"], r["vehicle"], r["link"]
    if t in ("enters_traffic", "leaves_traffic"):
        x = nodes[links[l]["to"]]
        if x > 649683: c[(t, kind(v), l, links[l].get("osm:way:highway", ""), round(x))] += 1
    if t == "entered" and l == "450786" and last.get(v) == "450785": east[kind(v)] += 1
    if t == "entered": last[v] = l
print("vehicles passing the portal link eastward (450785 -> 450786):", dict(east))
print("starts/ends east of the portal link end (x > 649683):")
for k, n in c.most_common(25): print(n, k)
