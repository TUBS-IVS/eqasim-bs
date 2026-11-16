"""Agents removed from the simulation (stuckAndAbort) per leg mode, during the day vs at the simulation end. Read-only.

Usage: python pt_removals_i466.py <output_events.xml.gz> [<simulation end s, default 108000>]

#466 counts public-transport passengers removed at the stop during the day: a stuckAndAbort event with legMode pt
before the end of the simulated day (30 h = 108000 s, ADR-0146). An event at or after the end is the end-of-day
abort of an agent still on its way, a different mechanism. Freight agents (person id starting with "freight") are
reported separately. Streams the events file; memory stays bounded.
"""
import collections
import gzip
import sys
import xml.etree.ElementTree as ET

events_path = sys.argv[1]
end_s = float(sys.argv[2]) if len(sys.argv) > 2 else 108000.0

counts = collections.Counter()
daytime_pt = []
with gzip.open(events_path, "rb") as handle:
    for _, element in ET.iterparse(handle):
        if element.tag == "event" and element.get("type") == "stuckAndAbort":
            person = element.get("person", "")
            time_s = float(element.get("time"))
            group = "freight" if person.startswith("freight") else "persons"
            when = "day" if time_s < end_s else "end"
            mode = element.get("legMode", "unknown")
            counts[(group, mode, when)] += 1
            if group == "persons" and mode == "pt" and when == "day":
                daytime_pt.append((person, time_s, element.get("link")))
        element.clear()

print(f"events: {events_path}")
print(f"simulation end: {end_s:.0f} s")
for key in sorted(counts):
    print("stuckAndAbort group %s legMode %s %s: %d" % (key + (counts[key],)))
print(f"persons removed during the day with legMode pt: {len(daytime_pt)}")
for person, time_s, link in daytime_pt:
    print(f"  person {person} time_h {time_s / 3600.0:.2f} link {link}")
