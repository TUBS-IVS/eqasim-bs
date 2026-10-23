"""#465: analyse bbox events around one outward portal link and the network-end turnaround loop.

Trips are rebuilt per vehicle from bbox_events.csv (enters_traffic .. leaves_traffic, or bbox entry .. exit).
"""
import csv, collections, sys, gzip, xml.etree.ElementTree as ET
run, d, portal_link, loop_entry, fcf = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
links = {}
with gzip.open(f"{run}/output_network.xml.gz") as f:
    for ev, el in ET.iterparse(f, events=("end",)):
        if el.tag == "link": links[el.attrib["id"]] = el.attrib; el.clear()
def free(l): return float(links[l]["length"]) / float(links[l]["freespeed"])
trips = collections.defaultdict(list)  # vehicle -> list of trips; trip = dict(start, links=[(link, t_in, t_out)])
open_trip = {}
for r in csv.DictReader(open(f"{d}/bbox_events.csv")):
    v, t, l, k = r["vehicle"], float(r["time"]), r["link"], r["type"]
    if k == "enters_traffic":
        tr = {"start": l, "t0": t, "links": [[l, t, None]], "end": None}; open_trip[v] = tr; trips[v].append(tr)
    elif k == "entered":
        tr = open_trip.get(v)
        if tr is None or tr["end"] is not None:  # first link of a trip that entered the bbox driving
            tr = {"start": None, "t0": t, "links": [], "end": None}; open_trip[v] = tr; trips[v].append(tr)
        tr["links"].append([l, t, None])
    elif k == "left":
        tr = open_trip.get(v)
        if tr and tr["links"] and tr["links"][-1][0] == l: tr["links"][-1][2] = t
    elif k == "leaves_traffic":
        tr = open_trip.get(v)
        if tr: tr["end"] = l; open_trip.pop(v)
all_trips = [(v, tr) for v, ts in trips.items() for tr in ts]
print("trips in bbox", len(all_trips))
# 1) departures on the portal link and where they go
dep = [(v, tr) for v, tr in all_trips if tr["start"] == portal_link]
via_loop = [x for x in dep if any(l[0] == loop_entry for l in x[1]["links"])]
print(f"departures on portal link {portal_link}: {len(dep)}; of these through turnaround entry {loop_entry}: {len(via_loop)}")
# 2) who uses the loop entry link
users = [(v, tr) for v, tr in all_trips if any(l[0] == loop_entry for l in tr["links"])]
origin = collections.Counter("dep_portal_link" if tr["start"] == portal_link else ("dep_in_bbox:" + tr["start"] if tr["start"] else "entered_bbox") for v, tr in users)
print(f"trips through turnaround entry {loop_entry}: {len(users)}; origin:", origin.most_common(8))
endl = collections.Counter(tr["end"] or "left_bbox" for v, tr in users)
print("  their end in bbox:", endl.most_common(5))
kinds = collections.Counter(v.split("_")[0] if "freight" in v else ("truck" if "truck" in v else "car") for v, tr in users)
print("  vehicle kinds:", kinds.most_common(5))
# 3) hourly flow and delay on the loop entry and the portal link
for L in (portal_link, loop_entry):
    cap = float(links[L]["capacity"]) * fcf
    flow = collections.Counter(); dmax = collections.defaultdict(float)
    for v, tr in all_trips:
        for l, ti, to in tr["links"]:
            if l == L and to is not None:
                h = int(to // 3600); flow[h] += 1
                if ti is not None and not (tr["start"] == L and tr["links"][0][0] == L and tr["links"][0][1] == ti):
                    dmax[h] = max(dmax[h], (to - ti - free(L)) / 3600)
    print(f"link {L} {links[L].get('modes','')} sim cap {cap:.0f}/h: hour:left(maxdelay_h)",
          " ".join(f"{h}:{flow[h]}({dmax[h]:.1f})" for h in sorted(flow) if flow[h] >= 0.25 * cap or dmax[h] >= 0.5))
# 4) delayed (>=1h) through traversals on the portal link: where did they come from, where do they go
thr = []
for v, tr in all_trips:
    for i, (l, ti, to) in enumerate(tr["links"]):
        if l == portal_link and to is not None and not (i == 0 and tr["start"] == l) and to - ti - free(l) >= 3600:
            thr.append((v, tr))
print(f"through traversals >= 1 h on {portal_link}: {len(thr)}; via turnaround {loop_entry}:",
      sum(any(l[0] == loop_entry for l in tr["links"]) for v, tr in thr),
      "; ending in bbox:", collections.Counter(tr["end"] or "left_bbox" for v, tr in thr).most_common(4),
      "; origin:", collections.Counter(tr["start"] or "entered_bbox" for v, tr in thr).most_common(4))
# 5) hourly departures on the portal link by vehicle kind, and trips that never finished (no leaves_traffic)
def kind(v): return "freight_transit" if v.startswith("freight_transit") else ("freight_other" if v.startswith("freight") else "person")
hd = collections.defaultdict(collections.Counter)
for v, tr in dep: hd[int(tr["t0"] // 3600)][kind(v)] += 1
print("departures on portal link per hour:", {h: dict(c) for h, c in sorted(hd.items())})
print("departures on portal link by kind:", collections.Counter(kind(v) for v, tr in dep))
unfinished = [x for x in dep if x[1]["end"] is None and not any(l[0] == loop_entry for l in x[1]["links"])]
print("portal-link departures that never reached the turnaround entry and never left traffic:", len(unfinished))
print("turnaround users by kind:", collections.Counter(kind(v) for v, tr in users))
