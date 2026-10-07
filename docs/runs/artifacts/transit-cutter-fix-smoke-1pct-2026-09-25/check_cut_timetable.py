"""Timetable check of a cut transit schedule (eqasim TransitScheduleCutter) against the uncut, mapped one.

For every route of the cut schedule, the kept stops must be a contiguous part of the same route in the mapped
schedule, and every departure must serve every kept stop at the mapped time (departure time plus the stop's
departure offset). Routes are matched by (line id, route id), departures by id; the kept part is located by
its stop facility ids, so loop routes that visit a stop twice are matched at the right occurrence.
Read-only. Output: one summary line per route class (entering / starting at the original first stop) with the
number of departures and of (departure, kept stop) pairs whose time differs, plus the distribution of the
difference at the first kept stop in minutes.

Usage: python check_cut_timetable.py <mapped schedule.xml.gz> <cut schedule.xml.gz>
"""
import gzip
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import Counter


def seconds(value):
    hours, minutes, secs = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def read_routes(path):
    routes = {}
    with gzip.open(path, "rb") as f:
        line = None
        for event, element in ET.iterparse(f, events=("start", "end")):
            if event == "start" and element.tag == "transitLine":
                line = element.get("id")
            if event == "end" and element.tag == "transitRoute":
                stops = [(s.get("refId"), seconds(s.get("departureOffset") or s.get("arrivalOffset")))
                         for s in element.find("routeProfile")]
                departures = {d.get("id"): seconds(d.get("departureTime")) for d in element.find("departures")}
                routes[(line, element.get("id"))] = (stops, departures)
                element.clear()
    return routes


def locate(kept_ids, original_ids):
    for start in range(len(original_ids) - len(kept_ids) + 1):
        if original_ids[start:start + len(kept_ids)] == kept_ids:
            return start
    return None


def main(mapped_path, cut_path):
    mapped, cut = read_routes(mapped_path), read_routes(cut_path)
    departures, wrong_pairs, first_stop_shift_min, unmatched = Counter(), Counter(), {}, 0
    for key, (cut_stops, cut_departures) in cut.items():
        mapped_stops, mapped_departures = mapped[key]
        start = locate([s for s, _ in cut_stops], [s for s, _ in mapped_stops])
        if start is None:
            unmatched += 1
            continue
        kind = "starts_at_original_first_stop" if start == 0 else "entering"
        for departure_id, cut_time in cut_departures.items():
            departures[kind] += 1
            mapped_time = mapped_departures[departure_id]
            for k, (_, cut_offset) in enumerate(cut_stops):
                if abs((cut_time + cut_offset) - (mapped_time + mapped_stops[start + k][1])) > 0.5:
                    wrong_pairs[kind] += 1
            first_stop_shift_min.setdefault(kind, []).append(
                ((cut_time + cut_stops[0][1]) - (mapped_time + mapped_stops[start][1])) / 60.0)
    print(f"cut routes {len(cut)}, not a contiguous part of their mapped route: {unmatched}")
    for kind, n in departures.items():
        shifts = first_stop_shift_min[kind]
        shifted = [s for s in shifts if abs(s) > 0.5 / 60.0]
        print(f"{kind}: {n} departures, {len(shifted)} with a shifted time at the first kept stop, "
              f"{wrong_pairs[kind]} (departure, kept stop) pairs off the mapped time"
              + (f"; shift min {min(shifted):.0f} min, median {statistics.median(shifted):.0f} min, "
                 f"max {max(shifted):.1f} min" if shifted else ""))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
