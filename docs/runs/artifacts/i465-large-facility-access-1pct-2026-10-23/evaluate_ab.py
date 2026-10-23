"""Issue #465 / ADR-0169: evaluate the continued 1 % A/B arms against the success criteria of the design (spec 7).

Per arm (a directory holding simulation_output/): last iteration and car travel hours (ph_modestats.csv, last
iteration and mean of the last five), mode shares (modestats.csv, last iteration), car legs and stuck agents from the
final events, through traversals delayed >= 1 h (from a link_delay_probe output directory), and at the in-scope large
sites (coordinates from the parking arm's large_facility_access_summary.csv): car share and car trip distance of the
trips that start or end there. For the parking arm also criterion (a): per access-group link and hour, car departures
against the simulated link capacity (capacity x flowCapacityFactor).
"""
import argparse
import collections
import csv
import gzip
import json
import re
import statistics
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument("--arm", action="append", required=True, help="name=directory=probe_directory")
parser.add_argument("--summary", required=True, help="large_facility_access_summary.csv of the parking arm")
parser.add_argument("--groups", required=True, help="large_facility_access_groups.csv of the parking arm")
parser.add_argument("--parking-arm", required=True)
parser.add_argument("--flow-capacity-factor", type=float, default=0.01)
parser.add_argument("--out", required=True)
args = parser.parse_args()

in_scope = {}
for row in csv.DictReader(open(args.summary), delimiter=";"):
    if row["in_scope"] == "true":
        in_scope[(round(float(row["x"]), 1), round(float(row["y"]), 1))] = row["site_key"]
group_links = {}
for row in csv.DictReader(open(args.groups), delimiter=";"):
    group_links[row["link_id"]] = (row["site_key"], int(row["persons"]), float(row["link_capacity_veh_per_h"]))
pattern = re.compile(r'(\w+)="([^"]*)"')
report = {}
for spec in args.arm:
    name, directory, probe = spec.split("=")
    out = directory + "/simulation_output"
    arm = {}
    ph = list(csv.DictReader(open(out + "/ph_modestats.csv"), delimiter=";"))
    arm["last_iteration"] = int(ph[-1]["Iteration"])
    arm["car_travel_h_last"] = float(ph[-1]["car_travel"])
    arm["car_travel_h_mean_last5"] = statistics.mean(float(row["car_travel"]) for row in ph[-5:])
    arm["walk_travel_h_last"] = float(ph[-1]["walk_travel"])
    modes = list(csv.DictReader(open(out + "/modestats.csv"), delimiter=";"))[-1]
    arm["mode_shares_last"] = {key: round(float(value), 4) for key, value in modes.items() if key != "Iteration"}

    legs, long_legs, stuck = [], 0, collections.Counter()
    start = {}
    departures = collections.Counter()
    with gzip.open(out + "/output_events.xml.gz", "rt") as handle:
        for line in handle:
            if 'type="departure"' in line:
                a = dict(pattern.findall(line))
                if a.get("legMode") == "car":
                    start[a["person"]] = float(a["time"])
            elif 'type="arrival"' in line:
                a = dict(pattern.findall(line))
                if a.get("legMode") == "car" and a["person"] in start:
                    duration = float(a["time"]) - start.pop(a["person"])
                    legs.append(duration)
                    long_legs += duration > 3600
            elif 'type="vehicle enters traffic"' in line:
                a = dict(pattern.findall(line))
                if a.get("networkMode") == "car" and a["link"] in group_links:
                    departures[(a["link"], int(float(a["time"]) // 3600))] += 1
            elif 'type="stuckAndAbort"' in line:
                stuck[dict(pattern.findall(line)).get("legMode")] += 1
    arm["car_legs"] = len(legs)
    arm["car_legs_over_1h"] = long_legs
    arm["car_leg_hours"] = round(sum(legs) / 3600.0, 1)
    arm["stuck_by_mode"] = dict(stuck)
    if name == args.parking_arm:
        over = []
        for (link, hour), count in departures.items():
            capacity = group_links[link][2] * args.flow_capacity_factor
            if count > capacity:
                over.append({"link": link, "hour": hour, "car_departures": count, "simulated_capacity_veh_h": capacity,
                             "site": group_links[link][0]})
        arm["criterion_a_link_hours_over_capacity"] = sorted(over, key=lambda row: -row["car_departures"])
        arm["access_links_with_departures"] = len({link for link, _ in departures})

    delayed = [row for row in csv.DictReader(open(probe + "/delayed_traversals.csv"))
               if row["kind"] == "through" and float(row["delay_s"]) >= 3600]
    arm["through_traversals_ge_1h"] = len(delayed)
    links = collections.Counter(row["link"] for row in delayed)
    arm["through_ge_1h_links"] = len(links)
    arm["through_ge_1h_top_links"] = links.most_common(10)

    trips = {"car": 0, "all": 0, "car_distance_m": []}
    with gzip.open(out + "/output_trips.csv.gz", "rt") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            ends = {(round(float(row["start_x"]), 1), round(float(row["start_y"]), 1)),
                    (round(float(row["end_x"]), 1), round(float(row["end_y"]), 1))}
            if ends & in_scope.keys():
                trips["all"] += 1
                if row["main_mode"] == "car":
                    trips["car"] += 1
                    trips["car_distance_m"].append(float(row["traveled_distance"]))
    arm["in_scope_site_trips"] = trips["all"]
    arm["in_scope_site_car_share"] = round(trips["car"] / trips["all"], 4) if trips["all"] else None
    arm["in_scope_site_car_trip_distance_m_mean"] = round(statistics.mean(trips["car_distance_m"]), 1) \
        if trips["car_distance_m"] else None
    report[name] = arm
    print(name, json.dumps({k: v for k, v in arm.items() if k not in ("criterion_a_link_hours_over_capacity",)},
                           default=str))
    if name == args.parking_arm:
        print("criterion (a) link-hours over capacity:", len(arm["criterion_a_link_hours_over_capacity"]),
              arm["criterion_a_link_hours_over_capacity"][:10])
json.dump(report, open(args.out, "w"), indent=1, default=str)
