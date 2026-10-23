"""#465: planned departures (demand) vs insertions on one link, per hour.

Planned departure = a 'departure' event of a network leg (legMode truck or car) on the link: the agent ends its
activity and the vehicle joins the link's waiting list. Insertion = 'vehicle enters traffic' in bbox_events.csv
of events_probe.py: the vehicle moves from the waiting list into the buffer. waiting_at_end = cumulative planned -
cumulative inserted at the end of the hour (vehicles on the waiting list).

The departure events are pre-extracted with
  zcat output_events.xml.gz | grep 'type="departure"' | grep 'link="<link>"' > departures_<link>.xml
Usage: planned_departures.py <departures_<link>.xml> <bbox_events.csv> <link>"""
import collections, csv, re, sys
departures, events, link = sys.argv[1], sys.argv[2], sys.argv[3]
NETWORK_MODES = {"truck", "car"}
kv = re.compile(r'(\w+)="([^"]*)"')
planned = collections.Counter(); modes = collections.Counter()
for line in open(departures):
    d = dict(kv.findall(line))
    modes[d["legMode"]] += 1
    if d["link"] == link and d["legMode"] in NETWORK_MODES: planned[int(float(d["time"]) // 3600)] += 1
inserted = collections.Counter()
for r in csv.DictReader(open(events)):
    if r["type"] == "enters_traffic" and r["link"] == link: inserted[int(float(r["time"]) // 3600)] += 1
print(f"departure events on {link} by legMode: {dict(modes)}")
print(f"network-leg departures (planned) {sum(planned.values())}, insertions {sum(inserted.values())}")
print("hour planned inserted waiting_at_end")
cp = ci = 0
for h in range(0, max(list(planned) + list(inserted)) + 1):
    cp += planned[h]; ci += inserted[h]
    print(h, planned[h], inserted[h], cp - ci)
