"""Measure how large facilities are attached to the road network, and what a capacity-aware access rule would do.

eqasim's ``FacilityPlacement`` attaches every facility to its ONE nearest car link that is not a motorway,
trunk or ``*_link`` (``OSMFacilityPlacementVoter``), whatever the link's capacity. All car departures of a
facility then enter the network on that link (issue #465: the VW Wolfsburg plant location, 34 303 activities in
the 100 % run, sits on a 600 veh/h tertiary link). This script reports, for a finished MATSim run,

- the size distribution of facilities (activities per facility, scaled to a 100 % population) and the capacity
  of the link each one uses now, and
- for a sweep of (minimum facility size, minimum access-link capacity, maximum distance): how many facilities
  the rule of ``LargeFacilityAccessLinkSelector`` (eqasim-java-bs) would move, how many would fall back to
  their current link (no eligible link close enough), and the capacity classes of the chosen links.

The rule is re-implemented here independently of the Java class so the measurement doubles as a cross-check:
eligible links carry car, have at least the minimum capacity and pass the placement voter (no motorway,
trunk, ``_link``); among them within the maximum perpendicular point-to-segment distance the closest wins,
ties go to the lower link id. A facility whose current link already has the minimum capacity keeps it.
Facilities whose id starts with an excluded prefix (the cordon portals ``outside_`` by default) are skipped.

Output: a JSON report plus a printed summary. CRS: the network's (EPSG:25832 for Braunschweig); metres.

Usage:
    python scripts/measure_large_facility_access.py --network output_network.xml.gz \
        --facilities output_facilities.xml.gz --activities output_activities.csv.gz --sample-size 0.01 \
        --output large_facility_access_report.json
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import xml.etree.ElementTree as ET

import numpy as np

HIGHWAY_ATTRIBUTE = "osm:way:highway"
DEFAULT_MINIMUM_ACTIVITIES = (500.0, 1000.0, 2000.0, 5000.0)
DEFAULT_MINIMUM_CAPACITIES_VEH_PER_HOUR = (1000.0, 1500.0)
DEFAULT_DISTANCES_M = (200.0, 300.0, 500.0, 1000.0)
DEFAULT_EXCLUDED_PREFIXES = ("outside_",)


def placement_eligible(highway):
    """Mirror of eqasim's ``OSMFacilityPlacementVoter``: no motorway, trunk or ``*_link`` class."""
    if highway is None:
        return True
    return not ("motorway" in highway or "trunk" in highway or "_link" in highway)


def read_network(path):
    """Return (nodes {id: (x, y)}, links {id: {from, to, modes, capacity, highway}}, capacity period s)."""
    nodes, links, period = {}, {}, 3600.0
    with gzip.open(path, "rb") as fh:
        for _event, element in ET.iterparse(fh, events=("end",)):
            if element.tag == "node":
                nodes[element.get("id")] = (float(element.get("x")), float(element.get("y")))
                element.clear()
            elif element.tag == "link":
                attributes = {a.get("name"): a.text for a in element.iter("attribute")}
                links[element.get("id")] = {
                    "from": element.get("from"), "to": element.get("to"),
                    "modes": set(element.get("modes").split(",")),
                    "capacity": float(element.get("capacity")),
                    "highway": attributes.get(HIGHWAY_ATTRIBUTE),
                }
                element.clear()
            elif element.tag == "links" and element.get("capperiod"):
                hours, minutes, seconds = (float(part) for part in element.get("capperiod").split(":"))
                period = hours * 3600.0 + minutes * 60.0 + seconds
    return nodes, links, period


def read_facilities(path):
    """Return {facility id: (x, y, link id)}."""
    facilities = {}
    with gzip.open(path, "rb") as fh:
        for _event, element in ET.iterparse(fh, events=("end",)):
            if element.tag == "facility":
                facilities[element.get("id")] = (float(element.get("x")), float(element.get("y")),
                                                 element.get("linkId"))
                element.clear()
    return facilities


def count_activities(path):
    """Activities per facility id from a MATSim ``output_activities.csv.gz`` (semicolon separated)."""
    counts = collections.Counter()
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            if row["facility_id"]:
                counts[row["facility_id"]] += 1
    return counts


def candidate_segments(nodes, links, minimum_capacity_veh_per_hour, vehicles_per_hour_per_unit):
    """Eligible links as (ids, from xy, to xy) arrays."""
    ids, start, end = [], [], []
    for link_id, link in links.items():
        if "car" not in link["modes"] or not placement_eligible(link["highway"]):
            continue
        if link["capacity"] * vehicles_per_hour_per_unit < minimum_capacity_veh_per_hour:
            continue
        ids.append(link_id)
        start.append(nodes[link["from"]])
        end.append(nodes[link["to"]])
    return np.array(ids, dtype=object), np.array(start, dtype=float), np.array(end, dtype=float)


def closest_link(point, ids, start, end, maximum_distance_m):
    """Closest segment by perpendicular distance within the maximum; ties to the lower id; None if none."""
    if len(ids) == 0:
        return None, None
    direction = end - start
    length_squared = (direction ** 2).sum(axis=1)
    relative = np.asarray(point) - start
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.where(length_squared > 0, (relative * direction).sum(axis=1) / length_squared, 0.0)
    t = np.clip(t, 0.0, 1.0)
    distance = np.hypot(*(start + t[:, None] * direction - np.asarray(point)).T)
    inside = np.flatnonzero(distance <= maximum_distance_m)
    if len(inside) == 0:
        return None, None
    best = min(inside, key=lambda i: (distance[i], ids[i]))
    return ids[best], float(distance[best])


def measure(nodes, links, period, facilities, counts, sample_size, minimum_activities, minimum_capacities,
            distances, excluded_prefixes):
    vehicles_per_hour_per_unit = 3600.0 / period
    scaled = {fid: n / sample_size for fid, n in counts.items()
              if fid in facilities and not fid.startswith(tuple(excluded_prefixes))}
    report = {"sample_size": sample_size, "facilities_with_activities": len(scaled),
              "excluded_prefixes": list(excluded_prefixes), "size_classes": [], "sweep": []}
    for threshold in minimum_activities:
        large = [fid for fid, n in scaled.items() if n >= threshold]
        current_capacity = collections.Counter(
            links[facilities[fid][2]]["capacity"] * vehicles_per_hour_per_unit for fid in large)
        report["size_classes"].append({"minimum_activities": threshold, "facilities": len(large),
                                       "current_link_capacity_veh_per_h": dict(sorted(current_capacity.items()))})
    for capacity in minimum_capacities:
        ids, start, end = candidate_segments(nodes, links, capacity, vehicles_per_hour_per_unit)
        for threshold in minimum_activities:
            large = sorted(fid for fid, n in scaled.items() if n >= threshold)
            for distance in distances:
                kept, moved, fallback = 0, [], []
                for fid in large:
                    x, y, link_id = facilities[fid]
                    if links[link_id]["capacity"] * vehicles_per_hour_per_unit >= capacity:
                        kept += 1
                        continue
                    chosen, chosen_distance = closest_link((x, y), ids, start, end, distance)
                    (moved if chosen is not None else fallback).append((fid, chosen, chosen_distance))
                needing = len(moved) + len(fallback)
                report["sweep"].append({
                    "minimum_capacity_veh_per_h": capacity, "minimum_activities": threshold,
                    "maximum_distance_m": distance, "large_facilities": len(large),
                    "already_adequate": kept, "moved": len(moved), "fallback": len(fallback),
                    "fallback_share_of_needing": round(len(fallback) / needing, 4) if needing else 0.0,
                    "moved_activities_100pct": round(sum(scaled[f] for f, _, _ in moved)),
                    "fallback_activities_100pct": round(sum(scaled[f] for f, _, _ in fallback)),
                    "chosen_highway": dict(collections.Counter(links[c]["highway"] for _, c, _ in moved)),
                    "median_moved_distance_m": round(float(np.median([d for _, _, d in moved])), 1) if moved else None,
                    "largest_fallbacks": sorted(((round(scaled[f]), f) for f, _, _ in fallback), reverse=True)[:5],
                })
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--network", required=True)
    parser.add_argument("--facilities", required=True)
    parser.add_argument("--activities", required=True)
    parser.add_argument("--sample-size", type=float, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-activities", type=float, nargs="+", default=list(DEFAULT_MINIMUM_ACTIVITIES))
    parser.add_argument("--minimum-capacities", type=float, nargs="+",
                        default=list(DEFAULT_MINIMUM_CAPACITIES_VEH_PER_HOUR))
    parser.add_argument("--distances", type=float, nargs="+", default=list(DEFAULT_DISTANCES_M))
    parser.add_argument("--excluded-prefixes", nargs="*", default=list(DEFAULT_EXCLUDED_PREFIXES))
    args = parser.parse_args()
    if not 0.0 < args.sample_size <= 1.0:
        parser.error("--sample-size must lie in (0, 1]")

    nodes, links, period = read_network(args.network)
    facilities = read_facilities(args.facilities)
    counts = count_activities(args.activities)
    report = measure(nodes, links, period, facilities, counts, args.sample_size, args.minimum_activities,
                     args.minimum_capacities, args.distances, args.excluded_prefixes)
    report["inputs"] = {"network": args.network, "facilities": args.facilities, "activities": args.activities}
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    for size_class in report["size_classes"]:
        print("facilities >= %(minimum_activities).0f activities: %(facilities)d, current link capacity %(current_link_capacity_veh_per_h)s"
              % size_class)
    for row in report["sweep"]:
        print("cap>=%(minimum_capacity_veh_per_h).0f size>=%(minimum_activities).0f dist<=%(maximum_distance_m).0f:"
              " large %(large_facilities)d adequate %(already_adequate)d moved %(moved)d fallback %(fallback)d"
              " (%(fallback_share_of_needing).3f) median %(median_moved_distance_m)s m %(chosen_highway)s"
              " largest fallbacks %(largest_fallbacks)s" % row)


if __name__ == "__main__":
    main()
