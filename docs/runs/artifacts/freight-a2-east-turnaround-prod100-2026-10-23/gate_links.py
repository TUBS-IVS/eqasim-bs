"""#465: for every road gate, compare the gate link (cordon_gates) with the link MATSim gave the facilities placed at
the gate (in-commuter gate homes at the gate point, portal_gate_* at the inside endpoint, and the outside_* the
cutter made from them). Counts activities per facility in the final plans."""
import csv, gzip, pickle, sys, collections, re, xml.etree.ElementTree as ET
run, gates_pickle, out = sys.argv[1], sys.argv[2], sys.argv[3]
gates = pickle.load(open(gates_pickle, "rb"))["gates"]
links = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    cur = None
    for ev, el in ET.iterparse(f, events=("start", "end")):
        if ev == "start" and el.tag == "link": cur = dict(el.attrib)
        elif ev == "end" and el.tag == "attribute" and cur is not None: cur[el.attrib["name"]] = el.text
        elif ev == "end" and el.tag == "link": links[cur["id"]] = cur; cur = None; el.clear()
fac = {}
pat = re.compile(r'<facility id="([^"]+)" linkId="([^"]+)" x="([^"]+)" y="([^"]+)"')
with gzip.open(f"{run}/output_facilities.xml.gz", "rt") as f:
    for line in f:
        m = pat.search(line)
        if m: fac[m.group(1)] = (m.group(2), float(m.group(3)), float(m.group(4)))
acts = collections.Counter()
with gzip.open(f"{run}/output_activities.csv.gz", "rt") as f:
    for r in csv.DictReader(f, delimiter=";"): acts[r["facility_id"]] += 1
def cls(l): return (f'{links[l].get("osm:way:highway","?")}/{float(links[l]["capacity"]):.0f}' if l in links else "NOT_IN_NETWORK")
with open(out, "w", newline="") as fo:
    w = csv.writer(fo)
    w.writerow(["gate_id", "gate_link", "gate_class", "facility", "facility_link", "facility_class", "same_link", "activities"])
    for g in gates.itertuples():
        gx, gy = g.geometry.x, g.geometry.y
        near = [fid for fid, (l, x, y) in fac.items() if abs(x - gx) < 0.01 and abs(y - gy) < 0.01]
        near += [fid for fid in fac if fid == "portal_" + g.gate_id]
        for fid in sorted(set(near)):
            l = fac[fid][0]
            w.writerow([g.gate_id, g.link_id, cls(str(g.link_id)), fid, l, cls(l), str(l) == str(g.link_id), acts[fid]])
