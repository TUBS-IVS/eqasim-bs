"""Read-only probe on prod100_20261031: which tours can the discrete mode choice never re-choose, why, and which of
their trips do not touch an ``outside`` activity at all.

Pass 1 reads the UNCUT population (``braunschweig.matsim.scenario.population``) and stores per person the kind
(resident / in-commuter) and one code per home-based tour: ``P`` = the tour has an ``outside`` activity already in the
synthesis (portal stay), ``D`` = an activity of the tour lies outside the cut extent, ``I`` = every activity inside.

Pass 2 reads the PREPARED population (routed and cut, the file MATSim reads) and counts, per kind and cause:
the frozen home-based tours (a trip touches an ``outside`` activity: the rule of ``plan_check.leg_tour_frozen`` and of
the production mode choice, tour finder on [home, outside] + OutsideFilter), their trips that touch ``outside`` and
their trips that do not ("inside trips": frozen today, free with eqasim's IsolatedOutsideTrips tour finder), with
straight-line distance bands per routing mode. Free tours are counted for context, those longer than the mode
choice's tour length filter (6 trips) separately.

Model output of one run, NOT a validation. Usage on felix:
  python frozen_scope_probe.py ~/prod100_20261031/cache ~/scratch_longwalk/frozen_scope.json
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

CACHE = Path(sys.argv[1]).expanduser()
OUT = Path(sys.argv[2]).expanduser()
POPULATION_STEM = "braunschweig.matsim.scenario.population__d66536c43b770db3cd52e1a76a1c4449"
PREPARE_STEM = "braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63"
PREPARED = CACHE / (PREPARE_STEM + ".cache") / "braunschweig_100pct_prod20261031_population.xml.gz"
UNCUT = CACHE / (POPULATION_STEM + ".cache") / "population.xml.gz"
EXTENT = CACHE / (PREPARE_STEM + ".cache") / "cordon_extent.gpkg"
HOME, OUTSIDE = "home", "outside"
MAX_TOUR_LENGTH = 6
CAUSE = {"P": "portal", "D": "destination_outside_extent", "I": "cut_only"}


def parse_person(element):
    """(person id, subpopulation, main activities, trips) of the selected plan.

    activity = (type, x, y, facility); trip = (routing mode, all legs walk, walkFallbackFrom stamp, leg modes).
    """
    subpopulation = None
    node = element.find("attributes")
    if node is not None:
        for attribute in node.findall("attribute"):
            if attribute.get("name") == "subpopulation":
                subpopulation = (attribute.text or "").strip()
    plans = element.findall("plan")
    plan = next((p for p in plans if p.get("selected") == "yes"), plans[0] if plans else None)
    activities, trips = [], []
    current = None
    if plan is not None:
        for child in plan:
            if child.tag == "activity":
                kind = child.get("type")
                if kind.endswith(" interaction"):
                    continue
                if activities and current is not None:
                    trips.append((current[0], current[1], current[2], tuple(current[3])))
                current = None
                activities.append((kind, float(child.get("x")), float(child.get("y")), child.get("facility")))
            elif child.tag == "leg":
                mode = child.get("mode")
                routing, stamp = None, None
                attributes = child.find("attributes")
                if attributes is not None:
                    for attribute in attributes.findall("attribute"):
                        name = attribute.get("name")
                        if name == "routingMode":
                            routing = (attribute.text or "").strip()
                        elif name == "walkFallbackFrom":
                            stamp = (attribute.text or "").strip()
                if current is None:
                    current = [routing or mode, mode == "walk", stamp, [mode]]
                else:
                    current[1] = current[1] and mode == "walk"
                    current[2] = current[2] or stamp
                    current[3].append(mode)
    return element.get("id"), subpopulation, activities, trips


def iter_persons(path):
    root = None
    with gzip.open(path, "rb") as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = element
                continue
            if element.tag != "person":
                continue
            yield parse_person(element)
            element.clear()
            root.clear()


def tours_of(activities, n_trips):
    """Tour index per trip: a new home-based tour starts at every home activity after the first activity."""
    result, tour = [], 0
    for index in range(n_trips):
        if index > 0 and activities[index][0] == HOME:
            tour += 1
        result.append(tour)
    return result


def band(distance_m, edges):
    for edge in edges:
        if distance_m <= edge:
            return f"<={edge // 1000}km"
    return f">{edges[-1] // 1000}km"


def main() -> int:
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.prepared import prep

    extent = gpd.read_file(EXTENT).geometry.unary_union
    prepared_extent = prep(extent)
    t0 = time.time()

    # Pass 1: uncut population.
    kind_of, codes_of = {}, {}
    n_uncut = 0
    for person_id, subpopulation, activities, trips in iter_persons(UNCUT):
        n_uncut += 1
        incommuter = any((a[3] or "").startswith("ic_") for a in activities)
        kind_of[person_id] = subpopulation or ("incommuter" if incommuter else "resident")
        if not trips:
            codes_of[person_id] = ""
            continue
        tour_of = tours_of(activities, len(trips))
        codes = []
        for tour in range(tour_of[-1] + 1):
            indices = [i for i, t in enumerate(tour_of) if t == tour]
            members = activities[indices[0]:indices[-1] + 2]
            if any(a[0] == OUTSIDE for a in members):
                codes.append("P")
            elif any(not prepared_extent.contains(Point(a[1], a[2])) for a in members):
                codes.append("D")
            else:
                codes.append("I")
        codes_of[person_id] = "".join(codes)
    print(f"pass 1: uncut persons {n_uncut} in {time.time() - t0:.0f} s; kinds "
          f"{dict(collections.Counter(kind_of.values()))}", flush=True)

    # Pass 2: prepared population.
    stats = collections.Counter()
    cut_modes = collections.Counter()
    person_causes = collections.Counter()
    n_prepared = 0
    for person_id, subpopulation, activities, trips in iter_persons(PREPARED):
        n_prepared += 1
        kind = subpopulation or kind_of.get(person_id)
        if kind is None:
            match = re.match(r"[A-Za-z_]+", person_id)
            kind = "other:" + (match.group(0) if match else "numeric_not_in_uncut")
        stats[(kind, "persons")] += 1
        n = len(trips)
        if n == 0:
            stats[(kind, "persons_without_trips")] += 1
            continue
        tour_of = tours_of(activities, n)
        n_tours = tour_of[-1] + 1
        touch = [activities[i][0] == OUTSIDE or activities[i + 1][0] == OUTSIDE for i in range(n)]
        codes = codes_of.get(person_id, "")
        matched = len(codes) == n_tours
        stats[(kind, "tours")] += n_tours
        stats[(kind, "trips")] += n
        causes = set()
        for tour in range(n_tours):
            indices = [i for i, t in enumerate(tour_of) if t == tour]
            frozen = any(touch[i] for i in indices)
            if frozen:
                cause = CAUSE[codes[tour]] if matched else "unmatched_tours"
                causes.add(cause)
                key = (kind, "frozen", cause)
                stats[key + ("tours",)] += 1
                n_inside = 0
                modes_cut = set()
                for i in indices:
                    routing, all_walk, stamp, _ = trips[i]
                    if touch[i]:
                        stats[key + ("trips_touching_outside",)] += 1
                        if routing != OUTSIDE:
                            modes_cut.add(routing)
                        continue
                    n_inside += 1
                    stats[key + ("inside_trips",)] += 1
                    distance = math.hypot(activities[i + 1][1] - activities[i][1],
                                          activities[i + 1][2] - activities[i][2])
                    label = "pt_as_walk" if (all_walk and stamp) else routing
                    stats[key + ("inside_trips_by_mode", label)] += 1
                    if label in ("walk", "pt_as_walk"):
                        stats[key + ("inside_" + label, band(distance, (2000, 5000, 10000, 20000)))] += 1
                    elif label in ("bicycle", "bike"):
                        stats[key + ("inside_bicycle", band(distance, (10000, 20000, 30000)))] += 1
                if n_inside:
                    stats[key + ("tours_with_inside_trips",)] += 1
                cut_modes[(kind, cause, "+".join(sorted(modes_cut)))] += 1
            else:
                long_tour = len(indices) > MAX_TOUR_LENGTH
                key = (kind, "free_long_tour" if long_tour else "free")
                stats[key + ("tours",)] += 1
                for i in indices:
                    routing, all_walk, stamp, _ = trips[i]
                    stats[key + ("trips",)] += 1
                    distance = math.hypot(activities[i + 1][1] - activities[i][1],
                                          activities[i + 1][2] - activities[i][2])
                    label = "pt_as_walk" if (all_walk and stamp) else routing
                    if label in ("walk", "pt_as_walk"):
                        stats[key + (label, band(distance, (2000, 5000, 10000, 20000)))] += 1
                    elif label in ("bicycle", "bike"):
                        stats[key + ("bicycle", band(distance, (10000, 20000, 30000)))] += 1
        if causes:
            stats[(kind, "persons_frozen")] += 1
            person_causes[(kind, "+".join(sorted(causes)))] += 1
        if n_prepared % 200000 == 0:
            print(f"pass 2: {n_prepared} persons, {time.time() - t0:.0f} s", flush=True)
    print(f"pass 2: prepared persons {n_prepared} in {time.time() - t0:.0f} s", flush=True)

    result = {
        "stats": {" | ".join(map(str, k)): v for k, v in sorted(stats.items(), key=lambda kv: tuple(map(str, kv[0])))},
        "cut_modes": {" | ".join(k): v for k, v in cut_modes.most_common(60)},
        "person_causes": {" | ".join(k): v for k, v in person_causes.most_common(40)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1))
    for section, values in result.items():
        print("=== " + section)
        for key, value in values.items():
            print(f"{value:>10}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
