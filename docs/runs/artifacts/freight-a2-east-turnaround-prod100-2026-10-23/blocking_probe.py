"""#465: on one link, list the vehicles that leave it (not arriving) and the hourly arrivals with their longest
traversal, from bbox_events.csv of events_probe.py. Usage: blocking_probe.py <bbox_events.csv> <link>"""
import csv, collections, sys
path, link = sys.argv[1], sys.argv[2]
ent = {}; passes = []; arr = collections.Counter(); arr_delay = collections.defaultdict(list)
for r in csv.DictReader(open(path)):
    t, ty, v, l = float(r["time"]), r["type"], r["vehicle"], r["link"]
    if l != link: continue
    if ty == "entered": ent[v] = t
    elif ty == "left" and v in ent: passes.append((v, ent.pop(v), t))
    elif ty == "leaves_traffic" and v in ent:
        ti = ent.pop(v); arr[int(t // 3600)] += 1; arr_delay[int(t // 3600)].append((t - ti) / 3600)
print(f"vehicles leaving {link} onward (vehicle, entered_h, left_h):")
for v, a, b in passes: print("  ", v, round(a / 3600, 2), round(b / 3600, 2))
print(f"arrivals on {link} per hour (n, max traversal h):")
print(" ", {h: (arr[h], round(max(arr_delay[h]), 1)) for h in sorted(arr)})
print(f"still on {link} at the end:", len(ent))
