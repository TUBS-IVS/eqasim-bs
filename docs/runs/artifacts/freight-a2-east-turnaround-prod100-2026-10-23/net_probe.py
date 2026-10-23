"""#465 portal probe: neighbourhood of selected links in a MATSim output network (read-only)."""
import gzip, sys, xml.etree.ElementTree as ET, collections, csv
run = sys.argv[1]; targets = sys.argv[2].split(",")
links = {}; nodes = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    cur = None
    for ev, el in ET.iterparse(f, events=("start", "end")):
        if ev == "start" and el.tag == "link": cur = dict(el.attrib)
        elif ev == "end" and el.tag == "attribute" and cur is not None: cur[el.attrib["name"]] = el.text
        elif ev == "end" and el.tag == "link": links[cur["id"]] = cur; cur = None; el.clear()
        elif ev == "end" and el.tag == "node": nodes[el.attrib["id"]] = (el.attrib["x"], el.attrib["y"]); el.clear()
out_of = collections.defaultdict(list); into = collections.defaultdict(list)
for l in links.values(): out_of[l["from"]].append(l["id"]); into[l["to"]].append(l["id"])
def d(i):
    l = links[i]
    return f'{i} {l.get("osm:way:highway","?")} cap={l["capacity"]} lanes={l["permlanes"]} len={float(l["length"]):.0f} fs={float(l["freespeed"])*3.6:.0f}km/h modes={l["modes"]} {l["from"]}->{l["to"]}'
for t in targets:
    print("==", d(t), "from", nodes[links[t]["from"]], "to", nodes[links[t]["to"]])
    for u in into[links[t]["from"]]: print("   in :", d(u))
    for u in out_of[links[t]["from"]]: print("   sib:", d(u))
    for u in out_of[links[t]["to"]]: print("   out:", d(u))
    for u in into[links[t]["to"]]: print("   merge:", d(u))
print("== downstream walk from", targets[0])
cur = targets[0]
seen = set()
for step in range(40):
    outs = out_of[links[cur]["to"]]
    print(step, d(cur), nodes[links[cur]["to"]], "outs:", outs, "ins at to:", len(into[links[cur]["to"]]))
    nxt = [o for o in outs if links[o]["to"] != links[cur]["from"]]
    if not nxt or cur in seen: break
    seen.add(cur); cur = nxt[0]
