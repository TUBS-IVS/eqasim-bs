"""Trace long-distance rail between Wolfsburg Hbf and Braunschweig Hbf through the PT supply layers.

For each MATSim schedule (unmapped pt2matsim output, mapped schedule, prepared cordon-cut schedule) count the
departures of long-distance lines (tariff scope table) riding WOB Hbf -> BS Hbf and BS Hbf -> WOB Hbf, matched by
stop facility name. For the cleaned GTFS directory count the trips of the same lines by direction, all service
days and on the pt2matsim service date. Read-only diagnostic.

Usage: python trace_long_distance_layers.py <scopes.csv> <date YYYYMMDD> <cleaned_gtfs_dir> <schedule.xml.gz>...
"""
import csv, gzip, sys, xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime

WOB, BS = "Wolfsburg Hbf", "Braunschweig Hbf"
scopes_path, service_date, gtfs_dir, schedules = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
with open(scopes_path, newline="", encoding="utf-8") as f:
    scope = {r["line_id"]: r for r in csv.DictReader(f)}
long_distance = {k for k, r in scope.items() if r["tariff_scope"] == "long_distance"}

def direction(names):
    if WOB in names and BS in names:
        return "WOB->BS" if names.index(WOB) < names.index(BS) else "BS->WOB"
    if WOB in names:
        return "WOB only"
    if BS in names:
        return "BS only"
    return None

def matsim_layer(path):
    stops, counts = {}, Counter()
    with gzip.open(path, "rb") as f:
        line_id = None
        for ev, el in ET.iterparse(f, events=("start", "end")):
            if ev == "start" and el.tag == "transitLine":
                line_id = el.get("id")
            if ev == "end" and el.tag == "stopFacility":
                stops[el.get("id")] = el.get("name") or ""
            if ev == "end" and el.tag == "transitRoute":
                if line_id in long_distance:
                    names = [stops.get(s.get("refId"), "") for s in el.find("routeProfile")]
                    d = direction(names)
                    if d:
                        counts[d] += len(el.find("departures"))
                el.clear()
    return counts

def active(service_id, calendar, dates, day):
    on = False
    row = calendar.get(service_id)
    if row and row["start_date"] <= day <= row["end_date"]:
        weekday = datetime.strptime(day, "%Y%m%d").strftime("%A").lower()
        on = row[weekday] == "1"
    exc = dates.get((service_id, day))
    if exc == "1":
        on = True
    elif exc == "2":
        on = False
    return on

def gtfs_layer(directory, day):
    def rows(name):
        with open(f"{directory}/{name}", newline="", encoding="utf-8-sig") as f:
            yield from csv.DictReader(f)
    stop_name = {r["stop_id"]: r["stop_name"] for r in rows("stops.txt")}
    trips = {r["trip_id"]: r for r in rows("trips.txt") if r["route_id"] in long_distance}
    calendar = {r["service_id"]: r for r in rows("calendar.txt")}
    dates = {(r["service_id"], r["date"]): r["exception_type"] for r in rows("calendar_dates.txt")}
    seq = defaultdict(list)
    for r in rows("stop_times.txt"):
        if r["trip_id"] in trips:
            seq[r["trip_id"]].append((int(r["stop_sequence"]), stop_name.get(r["stop_id"], "")))
    all_days, on_day = Counter(), Counter()
    for trip_id, s in seq.items():
        d = direction([n for _, n in sorted(s)])
        if d:
            all_days[d] += 1
            if active(trips[trip_id]["service_id"], calendar, dates, day):
                on_day[d] += 1
    return all_days, on_day, len(trips)

all_days, on_day, n_trips = gtfs_layer(gtfs_dir, service_date)
print(f"[cleaned GTFS] long-distance trips of scoped lines: {n_trips}")
print(f"  all service days: {dict(all_days)}")
print(f"  active on {service_date}: {dict(on_day)}")
for path in schedules:
    print(f"[{path.split('/')[-2][:60]}] departures: {dict(matsim_layer(path))}")
