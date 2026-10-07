"""Stop-sequence check of a cut transit schedule (eqasim TransitScheduleCutter) against the uncut, mapped one.

Replays the cutter's stop-sequence rule on every mapped route with the cordon extent, once with the OLD end
index of an outgoing crossing (the crossing's index, which drops the last inside stop) and once with the FIXED
end index (index + 1), and counts for each rule how many routes differ from the given cut schedule. The rule
that reproduces the cut schedule with 0 differences is the one the cut was made with. It then reports what
the old rule loses against the fixed one: routes and departures dropped entirely (by line and agency),
departures that lose their last inside stop, and stations of the PT cordon gates
(braunschweig.data.cordon_pt_gates) left without any rail arrival.
Read-only. CRS: stop coordinates and the extent in EPSG:25832.

Usage: python check_cut_stop_sequences.py <cordon_extent.gpkg> <mapped schedule.xml.gz> <cut schedule.xml.gz>
       <line_scopes.csv> [<cordon_pt_gates.p>]
"""
import csv
import gzip
import pickle
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

import geopandas as gpd
from shapely.geometry import Point
from shapely.prepared import prep


def read_schedule(path):
    stops, routes = {}, {}
    with gzip.open(path, "rb") as f:
        line = None
        for event, element in ET.iterparse(f, events=("start", "end")):
            if event == "start" and element.tag == "transitLine":
                line = element.get("id")
            if event == "end" and element.tag == "stopFacility":
                stops[element.get("id")] = (float(element.get("x")), float(element.get("y")), element.get("name") or "")
            if event == "end" and element.tag == "transitRoute":
                routes[(line, element.get("id"))] = ([s.get("refId") for s in element.find("routeProfile")],
                                                     len(element.find("departures")), element.findtext("transportMode"))
                element.clear()
    return stops, routes


def reduce(sequence, inside, end_offset):
    """TransitScheduleCutter.reduceStopSequence with the end index of an outgoing crossing = index + end_offset."""
    flags = [inside[s] for s in sequence]
    crossings = [(i, flags[i]) for i in range(len(sequence) - 1) if flags[i] != flags[i + 1]]  # (index, outgoing)
    if not crossings:
        kept = sequence if flags[0] else []
    else:
        first, last = crossings[0], crossings[-1]
        start = 0 if first[1] else first[0] + 1
        end = last[0] + end_offset if last[1] else len(sequence)
        kept = sequence[start:end]
    return kept if len(kept) >= 2 else []


def main(extent_path, mapped_path, cut_path, scopes_path, gates_path=None):
    polygon = prep(gpd.read_file(extent_path).geometry.union_all())
    with open(scopes_path, newline="", encoding="utf-8") as f:
        agency = {row["line_id"]: row["agency_name"] for row in csv.DictReader(f)}
    stops, mapped = read_schedule(mapped_path)
    _, cut = read_schedule(cut_path)
    inside = {sid: polygon.contains(Point(x, y)) for sid, (x, y, _) in stops.items()}
    name = lambda sid: stops[sid][2]

    old = {key: reduce(seq, inside, 0) for key, (seq, _, _) in mapped.items()}
    fixed = {key: reduce(seq, inside, 1) for key, (seq, _, _) in mapped.items()}
    for label, rule in (("old rule (end = index)", old), ("fixed rule (end = index + 1)", fixed)):
        differing = sum(rule[key] != (cut[key][0] if key in cut else []) for key in mapped)
        print(f"{label}: {differing} of {len(mapped)} mapped routes differ from the given cut schedule")

    kept_old = sum(mapped[k][1] for k in mapped if old[k])
    kept_fixed = sum(mapped[k][1] for k in mapped if fixed[k])
    print(f"departures kept: old rule {kept_old}, fixed rule {kept_fixed}")
    lost_lines = defaultdict(lambda: [0, 0, set()])
    lost_last_stop = Counter()
    for key, (seq, deps, mode) in mapped.items():
        if old[key] == fixed[key]:
            continue
        if not old[key]:
            entry = lost_lines[(key[0], mode)]
            entry[0] += 1
            entry[1] += deps
            entry[2].add(" -> ".join(name(s) for s in fixed[key]))
        else:
            lost_last_stop[mode] += deps
    print(f"old rule drops {sum(v[0] for v in lost_lines.values())} routes ({sum(v[1] for v in lost_lines.values())} departures) "
          f"entirely and removes the last inside stop from {sum(lost_last_stop.values())} departures {dict(lost_last_stop)}")
    for (line, mode), (n, deps, sequences) in sorted(lost_lines.items(), key=lambda kv: -kv[1][1]):
        if mode == "rail":
            print(f"  rail line {line} ({agency.get(line)}): {n} routes, {deps} departures, e.g. {sorted(sequences)[0]}")

    if gates_path:
        with open(gates_path, "rb") as f:
            gates = set(pickle.load(f)["stop_id"].astype(str))
        arrivals = {0: Counter(), 1: Counter()}
        for key, (seq, deps, mode) in mapped.items():
            if mode != "rail":
                continue
            for rule_id, rule in ((0, old), (1, fixed)):
                for k, sid in enumerate(rule[key]):
                    if k > 0 and sid in gates:
                        arrivals[rule_id][sid] += deps
        without = sorted((name(g), arrivals[1][g]) for g in gates if g in stops and arrivals[0][g] == 0 and arrivals[1][g] > 0)
        print(f"PT gate stations ({len(gates)}, {sum(inside.get(g, False) for g in gates)} inside the extent) without any rail "
              f"arrival under the old rule but with arrivals under the fixed rule: {len(without)}")
        for station, n in sorted(without, key=lambda r: -r[1]):
            print(f"  {station}: 0 instead of {n} rail arrivals")


if __name__ == "__main__":
    main(*sys.argv[1:6])
