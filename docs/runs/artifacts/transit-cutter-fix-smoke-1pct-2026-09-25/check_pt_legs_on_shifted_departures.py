"""Simulated PT legs (eqasim_pt.csv.gz) on departures that the transit schedule cutter shifted: boarding time
versus the timetable time at the access stop in the mapped (pre-cut) schedule. Read-only.

Every leg with a transit line is looked up by (line, route, departure id, access stop) in both schedules.
Legs without a transit line id carry no ride (every ride field empty, boarding time 0) and are counted
apart. A leg WITH a line that is missing from either schedule means the lookup is broken, so the check fails
instead of silently leaving the leg out of its denominator.

Usage: python check_pt_legs_on_shifted_departures.py <mapped schedule> <cut schedule> <eqasim_pt.csv.gz>
"""
import gzip
import sys
import xml.etree.ElementTree as ET

import pandas as pd


def seconds(value):
    hours, minutes, secs = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def stop_times(path):
    """(line, route, departure id, stop facility) -> scheduled departure time at that stop, first visit."""
    times = {}
    with gzip.open(path, "rb") as f:
        line = None
        for event, element in ET.iterparse(f, events=("start", "end")):
            if event == "start" and element.tag == "transitLine":
                line = element.get("id")
            if event == "end" and element.tag == "transitRoute":
                offsets = [(s.get("refId"), seconds(s.get("departureOffset") or s.get("arrivalOffset")))
                           for s in element.find("routeProfile")]
                for departure in element.find("departures"):
                    time = seconds(departure.get("departureTime"))
                    for stop, offset in offsets:
                        times.setdefault((line, element.get("id"), departure.get("id"), stop), time + offset)
                element.clear()
    return times


def main(mapped_path, cut_path, legs_path):
    mapped, cut = stop_times(mapped_path), stop_times(cut_path)
    legs = pd.read_csv(legs_path, sep=";", dtype=str)
    without_line = legs["transit_line_id"].isna()
    rides = legs[~without_line].copy()
    keys = list(zip(rides["transit_line_id"], rides["transit_route_id"], rides["departure_id"], rides["access_stop_id"]))
    rides["mapped_time"] = [mapped.get(key) for key in keys]
    rides["cut_time"] = [cut.get(key) for key in keys]
    unmatched = rides["mapped_time"].isna() | rides["cut_time"].isna()
    if unmatched.any():
        example = rides.loc[unmatched, ["transit_line_id", "transit_route_id", "departure_id", "access_stop_id"]].head(3)
        raise SystemExit(f"{int(unmatched.sum())} of {len(rides)} legs with a transit line have no timetable key in both "
                         f"schedules; the check cannot be complete, e.g.\n{example.to_string(index=False)}")
    rides["boarding_time"] = rides["boarding_time"].astype(float)
    rides["shift_min"] = (rides["cut_time"] - rides["mapped_time"]) / 60.0
    shifted = rides[rides["shift_min"].abs() > 0.5]
    print(f"PT legs {len(legs)}: {int(without_line.sum())} without a transit line id (no ride), {len(rides)} rides checked, "
          f"all found in both schedules")
    print(f"rides on a shifted departure: {len(shifted)} of {len(rides)} ({len(shifted) / len(rides):.1%}); "
          f"persons {shifted['person_id'].nunique()}")
    if len(shifted):
        print("shift of the boarded departure at the access stop [min], quantiles:",
              shifted["shift_min"].quantile([0.0, 0.25, 0.5, 0.75, 1.0]).round(1).to_dict())
        print("boarding time minus mapped timetable time [min], median:",
              round(((shifted["boarding_time"] - shifted["mapped_time"]) / 60.0).median(), 1))
        print(shifted.groupby("transit_mode").size().to_dict())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
