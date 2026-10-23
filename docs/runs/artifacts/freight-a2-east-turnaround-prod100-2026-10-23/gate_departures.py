"""#465: real departures on the links of the gate facilities (gate_links.py output), from a MATSim events file.

Counts, per facility link, 'departure' events by legMode and 'vehicle enters traffic' events (network departures),
and the peak hour of the network departures against the link's simulated capacity (capacity x flowCapacityFactor).
Usage: gate_departures.py <run> <gate_links.csv> <flowCapacityFactor> > out.txt"""
import collections, csv, gzip, re, sys, xml.etree.ElementTree as ET
run, gl, fcf = sys.argv[1], sys.argv[2], float(sys.argv[3])
watch = {r["facility_link"] for r in csv.DictReader(open(gl))}
cap = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    for ev, el in ET.iterparse(f, events=("end",)):
        if el.tag == "link":
            if el.attrib["id"] in watch: cap[el.attrib["id"]] = float(el.attrib["capacity"])
            el.clear()
kv = re.compile(r'(\w+)="([^"]*)"')
dep = collections.defaultdict(collections.Counter); net = collections.defaultdict(collections.Counter)
with gzip.open(f"{run}/output_events.xml.gz", "rt") as f:
    for line in f:
        if 'type="departure"' in line or 'type="vehicle enters traffic"' in line:
            d = dict(kv.findall(line))
            if d.get("link") not in watch: continue
            if d["type"] == "departure": dep[d["link"]][d["legMode"]] += 1
            else: net[d["link"]][int(float(d["time"]) // 3600)] += 1
print("link capacity sim_cap_per_h departures_by_legMode network_departures peak_hour peak_n peak_share_of_sim_cap")
for l in sorted(watch, key=lambda x: -sum(net[x].values())):
    n = sum(net[l].values()); pk = net[l].most_common(1)
    h, c = pk[0] if pk else ("", 0)
    print(l, cap.get(l), cap.get(l, 0) * fcf, dict(dep[l]), n, h, c, round(c / (cap[l] * fcf), 3) if l in cap else "")
