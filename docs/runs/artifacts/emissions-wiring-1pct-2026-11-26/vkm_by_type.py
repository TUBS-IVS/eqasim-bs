#!/usr/bin/env python3
"""Measurement helper for issue #688. NOT pipeline code. Read-only on the archive.

Vehicle-kilometres per vehicle type of a MATSim run archive, from output_events.

Convention: every "entered link" event adds the full length of the entered link
to the vehicle's type. The link a vehicle departs from is not counted, the link
it arrives on is counted in full (the usual MATSim link-based convention).
Numbers are sample values (not scaled by the sampling rate).
"""
import argparse
import collections
import gzip
import json
import re
import subprocess
import sys

LINK = re.compile(r'<link id="([^"]+)"[^>]* length="([^"]+)"')
VEHICLE = re.compile(r'<vehicle id="([^"]+)" type="([^"]+)"')
ENTERED = re.compile(r'type="entered link" link="([^"]+)" vehicle="([^"]+)"')
ENTERS_TRAFFIC = re.compile(r'vehicle="([^"]+)" networkMode="([^"]+)"')


def read_pairs(path, pattern, convert=str):
    result = {}
    with gzip.open(path, "rt", encoding="utf-8") as reader:
        for line in reader:
            match = pattern.search(line)
            if match:
                result[match.group(1)] = convert(match.group(2))
    return result


def group_of(type_id):
    if type_id is None:
        return "UNKNOWN_VEHICLE"
    if type_id.startswith("TRANSIT:"):
        return "transit"
    if type_id in ("default_car", "default_car_passenger", "heavy_truck"):
        return type_id
    if type_id.startswith("car_"):
        return "fleet_typed_car"
    return "OTHER:" + type_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--json", default=None, help="optional result file")
    args = parser.parse_args()
    archive = args.archive.rstrip("/")

    lengths = read_pairs(archive + "/output_network.xml.gz", LINK, float)
    types = read_pairs(archive + "/output_vehicles.xml.gz", VEHICLE)
    transit = read_pairs(archive + "/output_transitVehicles.xml.gz", VEHICLE)
    for vehicle_id, type_id in transit.items():
        types.setdefault(vehicle_id, "TRANSIT:" + type_id)
    print("links %d, vehicles %d (of these transit %d)" % (len(lengths), len(types), len(transit)),
          flush=True)

    metres = collections.Counter()
    entries = collections.Counter()
    departures = collections.Counter()
    modes = collections.Counter()
    moving = collections.defaultdict(set)
    lines = 0
    process = subprocess.Popen(
        ["gzip", "-dc", archive + "/output_events.xml.gz"],
        stdout=subprocess.PIPE, universal_newlines=True, encoding="utf-8",
    )
    for line in process.stdout:
        lines += 1
        if 'type="entered link"' in line:
            match = ENTERED.search(line)
            group = group_of(types.get(match.group(2)))
            metres[group] += lengths[match.group(1)]
            entries[group] += 1
            moving[group].add(match.group(2))
        elif 'type="vehicle enters traffic"' in line:
            match = ENTERS_TRAFFIC.search(line)
            group = group_of(types.get(match.group(1)))
            departures[group] += 1
            modes[match.group(2)] += 1
    if process.wait() != 0:
        raise SystemExit("gzip failed")

    vehicles_by_group = collections.Counter(group_of(t) for t in types.values())
    total = sum(metres.values())
    car = metres["default_car"] + metres["fleet_typed_car"]
    print("event lines %d, network modes at departure %s" % (lines, dict(modes)))
    print("%-24s %10s %10s %12s %12s %14s %8s" % (
        "group", "vehicles", "moving", "departures", "link entries", "vehicle-km", "share"))
    rows = []
    for group in sorted(set(vehicles_by_group) | set(metres)):
        row = {
            "group": group,
            "vehicles": vehicles_by_group[group],
            "vehicles_with_link_entry": len(moving[group]),
            "departures": departures[group],
            "link_entries": entries[group],
            "vehicle_km": metres[group] / 1000.0,
            "share_of_all_vehicle_km": metres[group] / total if total else None,
        }
        rows.append(row)
        print("%-24s %10d %10d %12d %12d %14.1f %7.2f%%" % (
            group, row["vehicles"], row["vehicles_with_link_entry"], row["departures"],
            row["link_entries"], row["vehicle_km"], 100.0 * (row["share_of_all_vehicle_km"] or 0)))
    summary = {
        "archive": archive,
        "event_lines": lines,
        "total_vehicle_km": total / 1000.0,
        "car_vehicle_km": car / 1000.0,
        "default_car_share_of_car_vehicle_km": metres["default_car"] / car if car else None,
        "default_car_share_of_car_vehicles": (
            vehicles_by_group["default_car"]
            / float(vehicles_by_group["default_car"] + vehicles_by_group["fleet_typed_car"])
            if vehicles_by_group["default_car"] + vehicles_by_group["fleet_typed_car"] else None),
        "convention": "full link length per 'entered link' event; sample values, unscaled",
        "rows": rows,
    }
    print("car vehicle-km %.1f; default_car share of car vehicle-km %s; of car vehicles %s" % (
        summary["car_vehicle_km"], summary["default_car_share_of_car_vehicle_km"],
        summary["default_car_share_of_car_vehicles"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as writer:
            json.dump(summary, writer, indent=2)


if __name__ == "__main__":
    sys.exit(main())
