"""Measure how freight boundary endpoints are linked, before and after the #444 rule.

For every boundary endpoint of the injected freight agents (start of incoming/transit trips, end of
outgoing/transit trips) in a finished MATSim run, this reports

- the link MATSim's nearest-node snapping chose (read from the output plans), and
- the link the boundary-link rule of ``FreightBoundaryLinkSelector`` (eqasim-java-bs) would choose: the
  closest eligible car link, by perpendicular distance, whose heading agrees with the trip direction,
  within a maximum distance.

Two eligibility filters are compared: an OSM highway-class allowlist and a minimum link capacity
(veh/h). The rule is re-implemented here independently of the Java class so the measurement doubles as
a cross-check of it. The maximum distance is swept to show the fallback share each value implies.

Output: a JSON report (per filter and distance: fallback share, links chosen, capacity of the chosen
links) plus a printed summary. CRS: the network's (EPSG:25832 for Braunschweig); distances in metres.

Usage:
    python scripts/measure_freight_boundary_links.py --network output_network.xml.gz \
        --plans output_plans.xml.gz --output freight_boundary_links_report.json
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import math
import xml.etree.ElementTree as ET

DEFAULT_HIGHWAY_TYPES = ("motorway", "motorway_link", "trunk", "trunk_link", "primary")
DEFAULT_MINIMUM_CAPACITIES_VEH_PER_HOUR = (1000.0, 1500.0)
DEFAULT_DISTANCES_M = (100.0, 200.0, 300.0, 500.0)
HIGHWAY_ATTRIBUTE = "osm:way:highway"


def read_network(path):
    """Return (nodes {id: (x, y)}, links {id: {from, to, modes, capacity, highway}})."""
    nodes, links = {}, {}
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
    return nodes, links


def read_boundary_endpoints(path):
    """Count boundary endpoints by (role, x, y, other_x, other_y, snapped_link)."""
    endpoints = collections.Counter()
    with gzip.open(path, "rb") as fh:
        for _event, element in ET.iterparse(fh, events=("end",)):
            if element.tag != "person":
                continue
            if element.get("id").startswith("freight"):
                attributes = {a.get("name"): a.text for a in element.iter("attribute")}
                trip_type = attributes.get("trip_type")
                plan = next(p for p in element.findall("plan") if p.get("selected") == "yes")
                activities = plan.findall("activity")
                first, last = activities[0], activities[-1]
                if trip_type in ("incoming", "transit"):
                    endpoints[("start", first.get("x"), first.get("y"), last.get("x"), last.get("y"),
                               first.get("link"))] += 1
                if trip_type in ("outgoing", "transit"):
                    endpoints[("end", last.get("x"), last.get("y"), first.get("x"), first.get("y"),
                               last.get("link"))] += 1
            element.clear()
    return endpoints


def segment_distance_m(point, start, end):
    """Perpendicular distance from point to the segment start-end, in CRS units (metres)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    t = 0.0 if length_squared == 0.0 else max(0.0, min(1.0, (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared))
    return math.hypot(start[0] + t * dx - point[0], start[1] + t * dy - point[1])


def select_link(direction, nearby, nodes, links, maximum_distance_m):
    """The boundary-link rule over pre-computed (distance, link_id) pairs: closest heading-consistent
    candidate within the distance, ties to the lower link id."""
    best = None
    for distance, link_id in nearby:
        if distance > maximum_distance_m:
            continue
        start, end = nodes[links[link_id]["from"]], nodes[links[link_id]["to"]]
        heading = (end[0] - start[0]) * direction[0] + (end[1] - start[1]) * direction[1]
        if heading <= 0.0:
            continue
        if best is None or (distance, link_id) < best:
            best = (distance, link_id)
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--network", required=True)
    parser.add_argument("--plans", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--highway-types", default=",".join(DEFAULT_HIGHWAY_TYPES))
    parser.add_argument("--minimum-capacities-veh-per-hour",
                        default=",".join(str(c) for c in DEFAULT_MINIMUM_CAPACITIES_VEH_PER_HOUR))
    parser.add_argument("--distances-m", default=",".join(str(d) for d in DEFAULT_DISTANCES_M))
    args = parser.parse_args()

    highway_types = set(args.highway_types.split(","))
    distances_m = [float(d) for d in args.distances_m.split(",")]
    largest_m = max(distances_m)
    nodes, links = read_network(args.network)
    car_links = [link_id for link_id, link in links.items() if "car" in link["modes"]]
    filters = {"highway_classes": lambda link: link["highway"] in highway_types}
    for capacity in (float(c) for c in args.minimum_capacities_veh_per_hour.split(",")):
        filters["capacity_ge_%d" % capacity] = (lambda minimum: lambda link: link["capacity"] >= minimum)(capacity)

    endpoints = read_boundary_endpoints(args.plans)
    total = sum(endpoints.values())
    print("car links %d, distinct boundary endpoint rows %d, boundary endpoints %d"
          % (len(car_links), len(endpoints), total))

    # Distances depend only on the point, so compute them once per distinct point over all car links
    # (vectorised); the exact scalar distance is then re-evaluated for the few links within range.
    import numpy as np
    from_xy = np.array([nodes[links[link_id]["from"]] for link_id in car_links])
    to_xy = np.array([nodes[links[link_id]["to"]] for link_id in car_links])
    delta = to_xy - from_xy
    length_squared = (delta ** 2).sum(axis=1)
    points = sorted({(float(x), float(y)) for (_role, x, y, _ox, _oy, _link) in endpoints})
    nearby_by_point = {}
    for point in points:
        offset = np.array(point) - from_xy
        t = np.where(length_squared > 0, (offset * delta).sum(axis=1) / np.where(length_squared > 0, length_squared, 1),
                     0.0).clip(0.0, 1.0)
        distances = np.hypot(from_xy[:, 0] + t * delta[:, 0] - point[0], from_xy[:, 1] + t * delta[:, 1] - point[1])
        nearby_by_point[point] = sorted(
            (segment_distance_m(point, nodes[links[car_links[i]]["from"]], nodes[links[car_links[i]]["to"]]),
             car_links[i]) for i in np.flatnonzero(distances <= largest_m + 1.0))
        nearby_by_point[point] = [(d, l) for d, l in nearby_by_point[point] if d <= largest_m]

    snapped_minor = sum(count for key, count in endpoints.items() if links[key[5]]["highway"] not in highway_types)
    snapped_low_capacity = sum(count for key, count in endpoints.items() if links[key[5]]["capacity"] < 1000.0)
    print("before (nearest-node snapping): %d endpoints on a non-major class (%.2f %%), %d on a link below"
          " 1000 veh/h (%.2f %%)" % (snapped_minor, 100.0 * snapped_minor / total,
                                     snapped_low_capacity, 100.0 * snapped_low_capacity / total))

    report = {"network": args.network, "plans": args.plans, "highway_types": sorted(highway_types),
              "boundary_endpoints": total, "before_non_major_class_endpoints": snapped_minor,
              "before_below_1000_veh_per_hour_endpoints": snapped_low_capacity, "filters": {}}
    for name, eligible in filters.items():
        report["filters"][name] = {}
        for distance_m in distances_m:
            fallback = 0
            changed = 0
            chosen = collections.Counter()
            chosen_capacity = collections.Counter()
            for (role, x, y, other_x, other_y, snapped), count in endpoints.items():
                point, other = (float(x), float(y)), (float(other_x), float(other_y))
                direction = (other[0] - point[0], other[1] - point[1]) if role == "start" \
                    else (point[0] - other[0], point[1] - other[1])
                nearby = [(d, l) for d, l in nearby_by_point[point] if eligible(links[l])]
                selected = select_link(direction, nearby, nodes, links, distance_m)
                if selected is None:
                    fallback += count
                    continue
                chosen[(role, x, y, selected[1], round(selected[0], 1), links[selected[1]]["highway"],
                        links[selected[1]]["capacity"])] += count
                chosen_capacity[links[selected[1]]["capacity"]] += count
                if selected[1] != snapped:
                    changed += count
            report["filters"][name][str(distance_m)] = {
                "fallback_endpoints": fallback, "fallback_share": round(fallback / total, 5),
                "changed_endpoints": changed,
                "selected_capacity_histogram": {str(k): v for k, v in sorted(chosen_capacity.items())},
                "top_selected": [list(key) + [n] for key, n in chosen.most_common(25)],
            }
            print("%-18s %5.0f m: fallback %6d (%.2f %%), link changed %6d, min selected capacity %s"
                  % (name, distance_m, fallback, 100.0 * fallback / total, changed,
                     min(chosen_capacity) if chosen_capacity else None))

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("written", args.output)


if __name__ == "__main__":
    main()
