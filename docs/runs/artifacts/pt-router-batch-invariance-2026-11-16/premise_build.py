"""Build the premise sample for the router prefetch check (audit H, F-H): real task sets plus shifted copies."""
import csv
import random

C = "/home/felix/prod100_20261031/cache/"
SRCS = {
    "ot": "braunschweig.synthesis.outside_tour_modes.stage__e4615b7799e3661dd55de11d3ef273ff.cache/outside_tour_pt_router/router_tasks.csv",
    "pg": "braunschweig.synthesis.portal_trips.stage__3bf655a528d6076561ce70276e1f8d1a.cache/pt_router/router_tasks.csv",
    "ic": "braunschweig.synthesis.incommuters__735abeff3315009a7538b415aca5cd37.cache/incommuter_pt_router/router_tasks.csv",
    "st": "braunschweig.synthesis.student_incommuters__741d29509ea1724583f3da017b2842d9.cache/incommuter_pt_router/router_tasks.csv",
}
HDR = ["identifier", "origin_x", "origin_y", "destination_x", "destination_y", "departure_time"]

rows = []
for key, path in SRCS.items():
    for r in csv.DictReader(open(C + path)):
        for off in (0.0, -1800.0, 3600.0):
            ident = "%s:%s:%+.0f" % (key, r["identifier"], off)
            rows.append([ident, r["origin_x"], r["origin_y"], r["destination_x"], r["destination_y"],
                         repr(float(r["departure_time"]) + off)])


def write(name, rs):
    with open(name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HDR)
        w.writerows(rs)


write("all.csv", rows)
shuffled = rows[:]
random.Random(7).shuffle(shuffled)
for i in range(4):
    write("part%d.csv" % i, shuffled[i::4])
write("tiny.csv", rows[3:8])
print(len(rows))
