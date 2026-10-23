"""Issue #465 A/B replay comparison: car-network legs, insertion wait and stuck agents per arm.

insertion wait = 'vehicle enters traffic' - 'departure' of the same person on a network mode (car/truck):
the time a departing vehicle spends in MATSim's waiting list before it gets a flow slot.
"""
import collections
import csv
import gzip
import re
import statistics
import sys

pattern = re.compile(r'(\w+)="([^"]*)"')
WATCH = {"129867", "426596", "354834", "671767", "708202", "641838"}

for arm_dir in sys.argv[1:]:
    events = arm_dir + "/output_events.xml.gz"
    departure = {}
    waits = []
    stuck = collections.Counter()
    leg_start = {}
    leg_times = []
    long_wait_links = collections.Counter()
    p693334 = []
    with gzip.open(events, "rt") as f:
        for line in f:
            if 'type="departure"' in line:
                a = dict(pattern.findall(line))
                if a.get("legMode") in ("car", "truck"):
                    departure[a["person"]] = float(a["time"])
                    leg_start[a["person"]] = float(a["time"])
            elif 'type="vehicle enters traffic"' in line:
                a = dict(pattern.findall(line))
                t0 = departure.pop(a["person"], None)
                if t0 is not None:
                    w = float(a["time"]) - t0
                    waits.append(w)
                    if w >= 3600:
                        long_wait_links[a["link"]] += 1
            elif 'type="arrival"' in line:
                a = dict(pattern.findall(line))
                if a.get("legMode") in ("car", "truck") and a["person"] in leg_start:
                    t0 = leg_start.pop(a["person"])
                    leg_times.append(float(a["time"]) - t0)
                    if a["person"] == "693334":
                        p693334.append((round(t0 / 3600, 2), round((float(a["time"]) - t0) / 60, 1)))
            elif 'type="stuckAndAbort"' in line:
                a = dict(pattern.findall(line))
                stuck[a.get("legMode")] += 1
    print("==", arm_dir)
    print("car/truck legs", len(leg_times), "mean min %.2f" % (statistics.mean(leg_times) / 60),
          "legs > 1 h", sum(t > 3600 for t in leg_times), "legs > 2 h", sum(t > 7200 for t in leg_times),
          "total veh-h %.1f" % (sum(leg_times) / 3600))
    print("insertion waits > 0", sum(w > 0 for w in waits), ">= 10 min", sum(w >= 600 for w in waits),
          ">= 1 h", sum(w >= 3600 for w in waits), "max h %.2f" % (max(waits) / 3600), "sum veh-h %.1f" % (sum(waits) / 3600))
    print("insertion waits >= 1 h by link", long_wait_links.most_common(8))
    print("stuckAndAbort by legMode", dict(stuck))
    print("693334 car legs (start h, duration min)", p693334)
