"""Simulated PT legs (eqasim_pt.csv.gz) on departures that the cutter shifted: boarding time versus the
timetable time at the access stop in the mapped (pre-cut) schedule. Read-only.

Usage: python check_pt_legs_on_shifted_departures.py <mapped schedule> <cut schedule> <eqasim_pt.csv.gz>
"""
import gzip, sys, xml.etree.ElementTree as ET
import pandas as pd

def t(s):
    h, m, x = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(x)

def read(path):
    stop_time = {}
    with gzip.open(path, "rb") as f:
        line = None
        for ev, el in ET.iterparse(f, events=("start", "end")):
            if ev == "start" and el.tag == "transitLine":
                line = el.get("id")
            if ev == "end" and el.tag == "transitRoute":
                offs = [(s.get("refId"), t(s.get("departureOffset") or s.get("arrivalOffset"))) for s in el.find("routeProfile")]
                for d in el.find("departures"):
                    dep = t(d.get("departureTime"))
                    for stop, off in offs:
                        stop_time.setdefault((line, el.get("id"), d.get("id"), stop), dep + off)
                el.clear()
    return stop_time

mapped, cut = read(sys.argv[1]), read(sys.argv[2])
legs = pd.read_csv(sys.argv[3], sep=";", dtype=str)
legs["boarding_time"] = legs["boarding_time"].astype(float)
keys = list(zip(legs["transit_line_id"], legs["transit_route_id"], legs["departure_id"], legs["access_stop_id"]))
legs["mapped_time"] = [mapped.get(k) for k in keys]
legs["cut_time"] = [cut.get(k) for k in keys]
legs["shift_min"] = (legs["cut_time"] - legs["mapped_time"]) / 60.0
shifted = legs[legs["shift_min"].abs() > 0.5]
print(f"PT legs {len(legs)}; with timetable key found {legs['mapped_time'].notna().sum()}; on a shifted departure {len(shifted)} "
      f"({len(shifted) / len(legs):.1%}); persons {shifted['person_id'].nunique()}")
if len(shifted):
    print("shift of the boarded departure at the access stop [min], quantiles:",
          shifted["shift_min"].quantile([0.0, 0.25, 0.5, 0.75, 1.0]).round(1).to_dict())
    print("boarding time minus mapped timetable time [min], median:",
          round(((shifted["boarding_time"] - shifted["mapped_time"]) / 60.0).median(), 1))
    print(shifted.groupby("transit_mode").size().to_dict())
