"""#465 / ADR-0167: which link did every facility at a road gate get?

Adapted from ``gate_links.py`` of run freight-a2-east-turnaround-prod100-2026-10-23 (branch
runs/i465-a2-east-portal-queue), which measured the defect: for every road gate of ``braunschweig.synthesis.
cordon_gates`` it lists the facilities at the gate (homes at the gate point, ``portal_<gate_id>``) of a facilities
file and the link they carry. Added here: the link the ADR-0167 rule selects for the gate (``gate_facility_links.csv``
of the facilities stage, optional) and whether the facility carries it, plus the road class and capacity of the
facility link from a network file.

Usage: python gate_facility_table.py FACILITIES_XML_GZ NETWORK_XML_GZ GATES_PICKLE OUT_CSV [SELECTION_CSV]
"""
import collections
import csv
import gzip
import pickle
import re
import sys
import xml.etree.ElementTree as ET

facilities_path, network_path, gates_path, out_path = sys.argv[1:5]
selection_path = sys.argv[5] if len(sys.argv) > 5 else None

gates = pickle.load(open(gates_path, "rb"))["gates"]
expected = {}
if selection_path:
    with open(selection_path) as handle:
        expected = {r["gate_id"]: r["facility_link_id"] for r in csv.DictReader(handle)}

links = {}
with gzip.open(network_path) as handle:
    current = None
    for event, el in ET.iterparse(handle, events=("start", "end")):
        if event == "start" and el.tag == "link":
            current = dict(el.attrib)
        elif event == "end" and el.tag == "attribute" and current is not None:
            current[el.get("name")] = el.text
        elif event == "end" and el.tag == "link":
            links[current["id"]] = current
            current = None
            el.clear()

facilities = {}
id_re, link_re = re.compile(r'<facility\b[^>]*\bid="([^"]+)"'), re.compile(r'\blinkId="([^"]+)"')
x_re, y_re = re.compile(r'\bx="([^"]+)"'), re.compile(r'\by="([^"]+)"')
with gzip.open(facilities_path, "rt", encoding="utf-8") as handle:
    for line in handle:
        m = id_re.search(line)
        if m:
            link = link_re.search(line)
            facilities[m.group(1)] = (link.group(1) if link else "", float(x_re.search(line).group(1)),
                                      float(y_re.search(line).group(1)))


def describe(link_id):
    link = links.get(link_id)
    return ("%s/%.0f" % (link.get("osm:way:highway", "?"), float(link["capacity"]))) if link else "NOT_IN_NETWORK"


rows = []
for gate in gates.itertuples():
    near = [f for f, (_l, x, y) in facilities.items() if abs(x - gate.geometry.x) < 0.01 and abs(y - gate.geometry.y) < 0.01]
    near += [f for f in facilities if f == "portal_" + gate.gate_id]
    for facility in sorted(set(near)):
        link = facilities[facility][0]
        rows.append({"gate_id": gate.gate_id, "gate_link": gate.link_id, "gate_class": describe(str(gate.link_id)),
                     "facility": facility, "facility_link": link, "facility_class": describe(link),
                     "expected_link": expected.get(gate.gate_id, ""),
                     "on_expected_link": bool(expected.get(gate.gate_id)) and link == expected.get(gate.gate_id)})
with open(out_path, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)

print("gate facilities: %d" % len(rows))
print("on the expected link: %d" % sum(r["on_expected_link"] for r in rows))
print("facility link classes:", dict(collections.Counter(r["facility_class"].split("/")[0] for r in rows)))
low = [r for r in rows if r["facility_class"] != "NOT_IN_NETWORK" and float(r["facility_class"].split("/")[1]) < 1000]
print("on a link below 1000 veh/h: %d" % len(low))
print("not on the expected link:", [(r["gate_id"], r["facility"], r["facility_link"], r["facility_class"],
                                     r["expected_link"]) for r in rows if not r["on_expected_link"]][:20])
