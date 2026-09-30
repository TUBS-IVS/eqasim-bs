"""Add parking attributes to an existing MATSim plans file for the direct parking-zone smoke.

Modes:
  legacy  -- person attribute isParis (home activity within the 8 km ring around BS Hbf, an APPROXIMATION of
             the pipeline's is_urban_resident = inside_braunschweig) and activity attribute isParis (activity
             within the ring), exactly the writer rule of matsim/scenario/population.py.
  zones   -- activity attributes parkingZone / parkingFree and person attribute residentParkingZone computed
             with the real package functions (braunschweig.parking.zones / attach) from the committed zone
             release; requires the integration worktree on sys.path (--repo).

The plans file is processed line by line (MATSim XML is line oriented); activities and persons keep every
other attribute unchanged. Aggregate counts are printed; nothing else is written.
"""
from __future__ import annotations

import argparse
import gzip
import math
import re
import sys
from pathlib import Path

HBF_E, HBF_N, RING_M = 605170.0, 5790274.0, 8000.0

PERSON_RE = re.compile(r'^\s*<person id="([^"]+)"')
ACTIVITY_RE = re.compile(r'^(\s*)<activity type="([^"]+)"([^>]*?)(/?)>\s*$')
XY_RE = re.compile(r'x="([^"]+)"\s+y="([^"]+)"')
INTERACTION = "interaction"


def within_ring(x: float, y: float) -> bool:
    return (x - HBF_E) ** 2 + (y - HBF_N) ** 2 <= RING_M ** 2


def read_lines(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return stream.readlines()


def first_pass(lines):
    """Per person: list of (activity_index, purpose, x, y) for real activities (interactions skipped)."""
    activities, person = {}, None
    for line in lines:
        m = PERSON_RE.match(line)
        if m:
            person = m.group(1)
            activities[person] = []
            continue
        m = ACTIVITY_RE.match(line)
        if m and person is not None:
            purpose = m.group(2)
            if INTERACTION in purpose:
                continue
            xy = XY_RE.search(m.group(3))
            activities[person].append((len(activities[person]), purpose, float(xy.group(1)), float(xy.group(2))))
    return activities


def attribute_line(indent: str, name: str, java_type: str, value: str) -> str:
    return f'{indent}\t<attribute name="{name}" class="{java_type}">{value}</attribute>\n'


def rewrite(lines, person_attrs, activity_attrs):
    """person_attrs: person -> list of (name, type, value); activity_attrs: (person, index) -> list."""
    out, person, act_index, counts = [], None, 0, {"person_attrs": 0, "activity_attrs": 0}
    i = 0
    while i < len(lines):
        line = lines[i]
        m = PERSON_RE.match(line)
        if m:
            person, act_index = m.group(1), 0
            out.append(line)
            # the writer always emits <attributes> directly after <person>
            if i + 1 < len(lines) and lines[i + 1].strip() == "<attributes>":
                out.append(lines[i + 1])
                indent = lines[i + 1][: len(lines[i + 1]) - len(lines[i + 1].lstrip())]
                for name, java_type, value in person_attrs.get(person, []):
                    out.append(attribute_line(indent, name, java_type, value))
                    counts["person_attrs"] += 1
                i += 2
                continue
            i += 1
            continue
        m = ACTIVITY_RE.match(line)
        if m and person is not None and INTERACTION not in m.group(2):
            indent, self_closing = m.group(1), m.group(4) == "/"
            extra = activity_attrs.get((person, act_index), [])
            act_index += 1
            if not extra:
                out.append(line)
                i += 1
                continue
            if self_closing:
                out.append(line.replace("/>", ">", 1).rstrip("\n").rstrip() + "\n")
                out.append(f"{indent}\t<attributes>\n")
                for name, java_type, value in extra:
                    out.append(attribute_line(indent + "\t", name, java_type, value))
                out.append(f"{indent}\t</attributes>\n")
                out.append(f"{indent}</activity>\n")
                counts["activity_attrs"] += len(extra)
                i += 1
                continue
            out.append(line)
            # an open activity: attributes block follows (routingMode) or not
            if i + 1 < len(lines) and lines[i + 1].strip() == "<attributes>":
                out.append(lines[i + 1])
                ind2 = lines[i + 1][: len(lines[i + 1]) - len(lines[i + 1].lstrip())]
                for name, java_type, value in extra:
                    out.append(attribute_line(ind2, name, java_type, value))
                i += 2
            else:
                out.append(f"{indent}\t<attributes>\n")
                for name, java_type, value in extra:
                    out.append(attribute_line(indent + "\t", name, java_type, value))
                out.append(f"{indent}\t</attributes>\n")
                i += 1
            counts["activity_attrs"] += len(extra)
            continue
        out.append(line)
        i += 1
    return out, counts


def legacy_attributes(activities):
    person_attrs, activity_attrs = {}, {}
    for person, acts in activities.items():
        home = next(((x, y) for _, p, x, y in acts if p == "home"), None)
        resident = home is not None and within_ring(*home)
        person_attrs[person] = [("isParis", "java.lang.Boolean", "true" if resident else "false")]
        for index, purpose, x, y in acts:
            activity_attrs[(person, index)] = [("isParis", "java.lang.Boolean", "true" if within_ring(x, y) else "false")]
    return person_attrs, activity_attrs


def zone_attributes(activities, repo: Path, data_path: Path, random_seed: int, shift: float):
    sys.path.insert(0, str(repo))
    import pandas as pd
    import geopandas as gpd
    from shapely.geometry import Point
    from braunschweig.parking import zones as pz
    from braunschweig.parking import attach

    zones = pz.load_zone_polygons(data_path / "braunschweig/parking/parking_zones_2026.geojson")
    tariffs = pz.load_tariffs(data_path / "braunschweig/parking/parking_tariffs_2026.csv")
    pz.cross_validate(zones, tariffs)
    shares = pd.read_csv(data_path / "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv", comment="#")
    rows = [(person, index, purpose, x, y) for person, acts in activities.items() for index, purpose, x, y in acts]
    frame = pd.DataFrame(rows, columns=["person_id", "activity_index", "purpose", "x", "y"])
    locations = gpd.GeoDataFrame(frame[["person_id", "activity_index"]],
                                 geometry=[Point(x, y) for x, y in zip(frame["x"], frame["y"])], crs="EPSG:25832")
    acts = attach.attach_parking_zones(frame[["person_id", "activity_index", "purpose"]], locations, zones)
    persons = pd.DataFrame({"person_id": sorted(activities)})
    persons = attach.attach_resident_zones(persons, acts, tariffs)
    acts = attach.draw_parking_free(acts, tariffs, shares, random_seed, shift)
    person_attrs, activity_attrs = {}, {}
    for row in persons.itertuples(index=False):
        if isinstance(row.resident_parking_zone, str):
            person_attrs[row.person_id] = [("residentParkingZone", "java.lang.String", row.resident_parking_zone)]
    for row in acts.itertuples(index=False):
        extra = []
        if isinstance(row.parking_zone, str):
            extra.append(("parkingZone", "java.lang.String", row.parking_zone))
        if bool(row.parking_free):
            extra.append(("parkingFree", "java.lang.Boolean", "true"))
        if extra:
            activity_attrs[(row.person_id, row.activity_index)] = extra
    print("zones:", acts["parking_zone"].notna().sum(), "of", len(acts), "activities in a zone;",
          "parking_free:", int(acts["parking_free"].sum()), "; residents:", len(person_attrs))
    return person_attrs, activity_attrs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["legacy", "zones", "strip"], required=True,
                        help="strip = only remove existing isParis attributes (OFF arm)")
    parser.add_argument("--strip-isparis", action="store_true",
                        help="remove existing isParis attributes before adding the mode's attributes")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo", help="integration worktree root (zones mode)")
    parser.add_argument("--data-path", help="eqasim-data/data root with the committed parking files (zones mode)")
    parser.add_argument("--random-seed", type=int, default=1234)
    parser.add_argument("--shift", type=float, default=0.0)
    args = parser.parse_args(argv)
    lines = read_lines(Path(args.input))
    if args.strip_isparis or args.mode == "strip":
        before = len(lines)
        lines = [line for line in lines if 'name="isParis"' not in line]
        print("stripped isParis attribute lines:", before - len(lines))
    activities = first_pass(lines)
    if args.mode == "strip":
        person_attrs, activity_attrs = {}, {}
    elif args.mode == "legacy":
        person_attrs, activity_attrs = legacy_attributes(activities)
    else:
        person_attrs, activity_attrs = zone_attributes(activities, Path(args.repo), Path(args.data_path),
                                                       args.random_seed, args.shift)
    out, counts = rewrite(lines, person_attrs, activity_attrs)
    with gzip.open(Path(args.output), "wt", encoding="utf-8") as stream:
        stream.writelines(out)
    print("persons:", len(activities), "attributes written:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
