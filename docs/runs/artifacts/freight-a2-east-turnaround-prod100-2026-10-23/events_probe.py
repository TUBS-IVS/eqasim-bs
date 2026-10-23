"""#465 portal probe: one read-only pass over a MATSim events file.

Writes to --out:
  bbox_events.csv   link events (entered/left link, vehicle enters/leaves traffic) on links whose from-node lies in --bbox
  portal_acts.csv   actstart/actend at facilities outside_* (person, link, time, facility)
  link_hour_left.csv  per link and hour: number of 'left link' events (all links)
"""
import argparse, collections, csv, gzip, os, re, xml.etree.ElementTree as ET
p = argparse.ArgumentParser()
p.add_argument("--run", required=True); p.add_argument("--events", default=None)
p.add_argument("--out", required=True); p.add_argument("--bbox", required=True, help="xmin,ymin,xmax,ymax")
a = p.parse_args(); os.makedirs(a.out, exist_ok=True)
xmin, ymin, xmax, ymax = map(float, a.bbox.split(","))
nodes = {}; link_from = {}
with gzip.open(os.path.join(a.run, "output_network.xml.gz")) as f:
    for ev, el in ET.iterparse(f, events=("end",)):
        if el.tag == "node": nodes[el.attrib["id"]] = (float(el.attrib["x"]), float(el.attrib["y"])); el.clear()
        elif el.tag == "link": link_from[el.attrib["id"]] = el.attrib["from"]; el.clear()
inbox = {l for l, n in link_from.items() if xmin <= nodes[n][0] <= xmax and ymin <= nodes[n][1] <= ymax}
print("links in bbox", len(inbox), flush=True)
kv = re.compile(r'(\w+)="([^"]*)"')
hour_left = collections.Counter()
be = open(os.path.join(a.out, "bbox_events.csv"), "w", newline=""); bw = csv.writer(be); bw.writerow(["time", "type", "vehicle", "link"])
pa = open(os.path.join(a.out, "portal_acts.csv"), "w", newline=""); pw = csv.writer(pa); pw.writerow(["time", "type", "person", "link", "facility"])
n = 0
with gzip.open(a.events or os.path.join(a.run, "output_events.xml.gz"), "rt") as f:
    for line in f:
        n += 1
        if n % 50_000_000 == 0: print("lines", n, flush=True)
        if 'type="left link"' in line:
            d = dict(kv.findall(line)); hour_left[(d["link"], int(float(d["time"]) // 3600))] += 1
            if d["link"] in inbox: bw.writerow([d["time"], "left", d["vehicle"], d["link"]])
        elif 'type="entered link"' in line or 'enters traffic' in line or 'leaves traffic' in line:
            d = dict(kv.findall(line))
            if d["link"] in inbox:
                t = {"entered link": "entered", "vehicle enters traffic": "enters_traffic", "vehicle leaves traffic": "leaves_traffic"}[d["type"]]
                bw.writerow([d["time"], t, d["vehicle"], d["link"]])
        elif 'facility="outside_' in line and ('type="actstart"' in line or 'type="actend"' in line):
            d = dict(kv.findall(line)); pw.writerow([d["time"], d["type"], d["person"], d["link"], d["facility"]])
be.close(); pa.close()
with open(os.path.join(a.out, "link_hour_left.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["link", "hour", "left"])
    for (l, h), c in sorted(hour_left.items()): w.writerow([l, h, c])
print("done lines", n)
