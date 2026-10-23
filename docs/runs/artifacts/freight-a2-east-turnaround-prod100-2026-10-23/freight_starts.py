"""#465: links on which freight vehicles enter traffic inside the event box, from bbox_events.csv of
events_probe.py, with the link's road class. Usage: freight_starts.py <run> <bbox_events.csv> [top_n]"""
import collections, csv, gzip, sys, xml.etree.ElementTree as ET
run, events = sys.argv[1], sys.argv[2]; top = int(sys.argv[3]) if len(sys.argv) > 3 else 10
cls = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    cur = None
    for ev, el in ET.iterparse(f, events=("start", "end")):
        if ev == "start" and el.tag == "link": cur = dict(el.attrib)
        elif ev == "end" and el.tag == "attribute" and cur is not None: cur[el.attrib["name"]] = el.text
        elif ev == "end" and el.tag == "link":
            cls[cur["id"]] = f'{cur.get("osm:way:highway", "?")}/{float(cur["capacity"]):.0f} {cur["from"]}->{cur["to"]}'
            cur = None; el.clear()
c = collections.Counter()
for r in csv.DictReader(open(events)):
    if r["type"] == "enters_traffic" and r["vehicle"].startswith("freight"):
        c[(r["vehicle"].split("_")[1], r["link"])] += 1
for (k, l), n in c.most_common(top): print(n, k, l, cls.get(l, "?"))
