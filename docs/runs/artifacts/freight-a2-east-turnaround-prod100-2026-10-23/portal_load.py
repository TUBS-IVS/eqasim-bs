"""#465: join the portal inventory with activity counts and peak-hour departures (end_time) per outside facility."""
import csv, gzip, sys, collections
run, inv, out, fcf = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
PREFIX = sys.argv[5] if len(sys.argv) > 5 else "outside_"
acts = collections.Counter(); dep = collections.defaultdict(collections.Counter)
with gzip.open(f"{run}/output_activities.csv.gz", "rt") as f:
    for r in csv.DictReader(f, delimiter=";"):
        fid = r["facility_id"]
        if not fid.startswith(PREFIX): continue
        acts[fid] += 1
        if r.get("end_time"):
            try: dep[fid][int(float(r["end_time"]) // 3600)] += 1
            except ValueError: pass
rows = list(csv.DictReader(open(inv)))
for r in rows:
    r["activities"] = acts[r["facility"]]
    h = dep[r["facility"]].most_common(1)
    r["peak_hour"], r["peak_ends"] = (h[0] if h else ("", 0))
    r["peak_share_of_sim_cap"] = round(r["peak_ends"] / (float(r["capacity"]) * fcf), 3)
rows.sort(key=lambda r: -r["activities"])
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
