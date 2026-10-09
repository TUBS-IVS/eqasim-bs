"""Exposure of a MATSim plans file to the parking zones of two zone releases (one-off curation check, issue #436).

Parking cost zones v2, spec Amendment D, ruling R-4a-7: how many activities of the local 1 % scenario lie in the zones a
release adds, changes or removes. The check says how many synthetic activities a polygon change touches. It is no
validation of the polygons, no pipeline stage and no run of the cost model, and the 1 % sample is small (a zone of a
few hundred metres across holds a handful of activities at best).

Definitions (the numbers in the data record ``parking_zones_2026`` follow them):

* A main activity is an activity of the SELECTED plan of a person whose type does not end with " interaction" (the stop
  and access activities the router inserts). The activities of a person are numbered 0.. in plan order.
* Its location is the ``x`` and ``y`` attribute of the activity (EPSG:25832, as in the plans of the scenario).
* Its zone is the ``parking_zone`` that the production function ``braunschweig.parking.attach.attach_parking_zones``
  assigns on the polygons read by ``braunschweig.parking.zones.load_zone_polygons`` (NaN outside every zone, where
  parking is free by assumption Z1).
* It is parking free (``parking_free``) when the activity carries the attribute ``parkingFree`` with the value ``true``
  (the free-parking draw of ``attach.draw_parking_free`` writes it only where true; no attribute means not free).
* It is a car arrival when the trip that ends at it (the legs since the previous main activity) holds a leg of mode
  ``car``; a ``car_passenger`` leg does not park. The first activity of a plan has no inbound trip.
* A zone is added, removed or changed between two releases when it exists in one file only or when the two polygons
  differ by more than ``CHANGE_TOLERANCE_M2``.

Written: the long table ``--out`` (columns ``EXPOSURE_COLUMNS``, one row per release, zone and purpose with at least one
activity); printed: the plans accounting, the share of activities inside a zone per release, the added, removed and
changed zones with their activities and car arrivals before and after, and the activities whose zone changes.

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/count_zone_exposure.py \
        --plans <main checkout>/eqasim-data/output_bs/braunschweig_1pct_population.xml.gz \
        --old-zones <the earlier parking_zones_2026.geojson> \
        --new-zones eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson --out <exposure table csv>
"""
from __future__ import annotations

import argparse
import gzip
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import geopandas as gpd
import pandas as pd

# The script runs from its own directory; the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import attach  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

#: Activity types the router inserts at stops and access points; they are no destinations.
INTERACTION_SUFFIX = " interaction"
CAR_MODE = "car"
#: The activity attribute of the free-parking draw (``braunschweig.parking.attach``).
PARKING_FREE_ATTRIBUTE = "parkingFree"
#: Label of "no zone" in the transition table (parking is free there by assumption Z1).
NO_ZONE = "none"
#: Two polygons of one zone id differ when their symmetric difference exceeds this area (the overlap tolerance of the
#: zone validator: the WGS84 rounding of the stored coordinates is about 1 cm).
CHANGE_TOLERANCE_M2 = pz.OVERLAP_TOLERANCE_M2
EXPOSURE_COLUMNS = ["release", "zone_id", "purpose", "activities", "car_arrivals"]


def _carries_parking_free(activity: ElementTree.Element) -> bool:
    """Whether the activity element has the attribute ``parkingFree`` with the value ``true`` (its own attributes only)."""
    attributes = activity.find("attributes")
    if attributes is None:
        return False
    return any(attribute.get("name") == PARKING_FREE_ATTRIBUTE and (attribute.text or "").strip().lower() == "true"
               for attribute in attributes.findall("attribute"))


def read_main_activities(path) -> tuple:
    """(frame, persons) of the main activities of the selected plans in a MATSim plans file (``.xml`` or ``.xml.gz``).

    The frame has ``person_id``, ``activity_index``, ``purpose`` (the activity type), ``x`` and ``y`` (metres, as in the
    file), ``car_arrival`` and ``parking_free`` (see the module docstring), one row per main activity in file order;
    ``persons`` counts every person of the file. The file is streamed, so the 1 % population needs little memory. Raises
    ``ValueError`` for an activity without coordinates (the check needs a location, never a default) and when no person has a
    main activity in a selected plan.
    """
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    rows, persons = [], 0
    person_id, activity_index, trip_modes, in_selected_plan = "", 0, [], False
    with opener(path, "rb") as stream:
        for event, element in ElementTree.iterparse(stream, events=("start", "end")):
            tag = element.tag
            if event == "start":
                if tag == "person":
                    persons += 1
                    person_id = str(element.get("id"))
                elif tag == "plan":
                    in_selected_plan = element.get("selected") == "yes"
                    activity_index, trip_modes = 0, []
                continue
            if tag == "leg" and in_selected_plan:
                trip_modes.append(element.get("mode"))
            elif tag == "activity" and in_selected_plan and not str(element.get("type")).endswith(INTERACTION_SUFFIX):
                if element.get("x") is None or element.get("y") is None:
                    raise ValueError(f"{path}: the {element.get('type')} activity {activity_index} of person "
                                     f"{person_id} has no x/y attribute; the exposure check needs the location")
                rows.append({"person_id": person_id, "activity_index": activity_index, "purpose": element.get("type"),
                             "x": float(element.get("x")), "y": float(element.get("y")),
                             "car_arrival": CAR_MODE in trip_modes, "parking_free": _carries_parking_free(element)})
                activity_index += 1
                trip_modes = []
            elif tag == "plan":
                in_selected_plan = False
            if tag in ("leg", "activity", "person"):
                element.clear()  # stream: keep no element after its end tag
    if not rows:
        raise ValueError(f"{path}: no main activity in any selected plan of the {persons} persons; "
                         "is this a MATSim plans file with selected plans?")
    return pd.DataFrame(rows), persons


def zone_per_activity(activities: pd.DataFrame, zones_path) -> pd.Series:
    """The zone id of every activity (NaN outside every zone) on the polygons of ``zones_path``, by the production
    function ``attach_parking_zones``; indexed like ``activities``."""
    zones = pz.load_zone_polygons(zones_path)
    keys = list(attach.ACTIVITY_KEYS)
    locations = gpd.GeoDataFrame(activities[keys], geometry=gpd.points_from_xy(activities["x"], activities["y"]),
                                 crs=pz.CRS)
    attached = attach.attach_parking_zones(activities[keys + ["purpose"]], locations, zones)
    return pd.Series(attached[attach.PARKING_ZONE_COLUMN].to_numpy(dtype=object), index=activities.index,
                     name=attach.PARKING_ZONE_COLUMN)


def _count(frame: pd.DataFrame, by: list) -> pd.DataFrame:
    counts = frame.groupby(by).agg(activities=("car_arrival", "size"), car_arrivals=("car_arrival", "sum"))
    counts["car_arrivals"] = counts["car_arrivals"].astype(int)
    return counts.reset_index()


def exposure_table(activities: pd.DataFrame, zone_by_release: dict) -> pd.DataFrame:
    """Rows ``EXPOSURE_COLUMNS``: per release (the keys of ``zone_by_release``, in that order), zone and purpose the
    activities inside the zone and the car arrivals among them. ``zone_by_release`` maps a release label to the
    ``zone_per_activity`` series of that release."""
    parts = []
    for release, zone in zone_by_release.items():
        inside = activities.assign(zone_id=zone.to_numpy())[zone.notna().to_numpy()]
        counts = _count(inside, ["zone_id", "purpose"])
        counts.insert(0, "release", release)
        parts.append(counts)
    return pd.concat(parts, ignore_index=True)[EXPOSURE_COLUMNS]


def transition_table(activities: pd.DataFrame, old_zone: pd.Series, new_zone: pd.Series) -> pd.DataFrame:
    """Rows (old_zone_id, new_zone_id, activities, car_arrivals) of the activities whose zone differs between the two
    releases; ``NO_ZONE`` stands for no zone. Activities in the same zone in both releases are no row."""
    old_ids, new_ids = old_zone.fillna(NO_ZONE).to_numpy(), new_zone.fillna(NO_ZONE).to_numpy()
    moved = activities.assign(old_zone_id=old_ids, new_zone_id=new_ids)[old_ids != new_ids]
    if moved.empty:
        return pd.DataFrame(columns=["old_zone_id", "new_zone_id", "activities", "car_arrivals"])
    return _count(moved, ["old_zone_id", "new_zone_id"])


def zone_changes(old_zones_path, new_zones_path) -> pd.DataFrame:
    """Rows (zone_id, change, old_area_m2, new_area_m2, symmetric_difference_m2) of the zones that one release adds
    (``added``), drops (``removed``) or re-draws by more than ``CHANGE_TOLERANCE_M2`` (``changed``); an unchanged
    zone is no row. Areas in m2 on EPSG:25832."""
    old = pz.load_zone_polygons(old_zones_path).set_index("zone_id").geometry
    new = pz.load_zone_polygons(new_zones_path).set_index("zone_id").geometry
    rows = []
    for zone_id in sorted(set(old.index) | set(new.index)):
        old_geometry, new_geometry = old.get(zone_id), new.get(zone_id)
        if old_geometry is None:
            rows.append((zone_id, "added", 0.0, new_geometry.area, new_geometry.area))
        elif new_geometry is None:
            rows.append((zone_id, "removed", old_geometry.area, 0.0, old_geometry.area))
        else:
            difference = old_geometry.symmetric_difference(new_geometry).area
            if difference > CHANGE_TOLERANCE_M2:
                rows.append((zone_id, "changed", old_geometry.area, new_geometry.area, difference))
    return pd.DataFrame(rows, columns=["zone_id", "change", "old_area_m2", "new_area_m2", "symmetric_difference_m2"])


def _purposes_text(table: pd.DataFrame, release: str, zone_id: str) -> str:
    rows = table[(table["release"] == release) & (table["zone_id"] == zone_id)]
    return ", ".join(f"{row.purpose} {row.activities}" for row in rows.itertuples(index=False)) or "none"


def report(activities: pd.DataFrame, persons: int, zone_by_release: dict, changes: pd.DataFrame,
           table: pd.DataFrame, transitions: pd.DataFrame, plans_name: str) -> None:
    """Print the accounting, the share inside a zone per release, the changed zones and the transitions."""
    total = len(activities)
    print(f"[exposure] {persons} persons, {total} main activities ({int(activities['car_arrival'].sum())} car "
          f"arrivals) in {plans_name}")
    for release, zone in zone_by_release.items():
        inside = int(zone.notna().sum())
        print(f"[exposure] {release} release: {inside} of {total} activities ({100.0 * inside / total:.1f} %) lie in a "
              f"zone, {int(activities.loc[zone.notna().to_numpy(), 'car_arrival'].sum())} car arrivals among them")
    releases = list(zone_by_release)
    totals = table.groupby(["zone_id", "release"])[["activities", "car_arrivals"]].sum()
    print(f"[exposure] {len(changes)} added, removed or changed zones (activities / car arrivals "
          f"{' -> '.join(releases)}):")
    for row in changes.itertuples(index=False):
        figures = []
        for release in releases:
            key = (row.zone_id, release)
            figures.append(f"{int(totals.loc[key, 'activities'])} / {int(totals.loc[key, 'car_arrivals'])}"
                           if key in totals.index else "0 / 0")
        print(f"[exposure]   {row.zone_id} ({row.change}, {row.old_area_m2:.0f} -> {row.new_area_m2:.0f} m2): "
              f"{' -> '.join(figures)}; purposes {releases[-1]}: {_purposes_text(table, releases[-1], row.zone_id)}")
    print(f"[exposure] {int(transitions['activities'].sum()) if len(transitions) else 0} activities change their zone:")
    for row in transitions.itertuples(index=False):
        print(f"[exposure]   {row.old_zone_id} -> {row.new_zone_id}: {row.activities} activities, "
              f"{row.car_arrivals} car arrivals")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plans", required=True, help="MATSim plans file (.xml or .xml.gz) with the selected plans")
    parser.add_argument("--old-zones", required=True, help="the earlier release, parking_zones_2026.geojson")
    parser.add_argument("--new-zones", required=True, help="the new release, parking_zones_2026.geojson")
    parser.add_argument("--out", required=True, help="the long exposure table (csv)")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing --out file")
    args = parser.parse_args(argv)
    for label, path in (("plans file", args.plans), ("old zone file", args.old_zones), ("new zone file", args.new_zones)):
        if not Path(path).is_file():
            raise SystemExit(f"{label} not found: {path}")
    out = Path(args.out)
    if out.exists() and not args.overwrite:
        raise SystemExit(f"{out} exists; pass --overwrite to replace it")

    activities, persons = read_main_activities(args.plans)
    zone_by_release = {"old": zone_per_activity(activities, args.old_zones),
                       "new": zone_per_activity(activities, args.new_zones)}
    table = exposure_table(activities, zone_by_release)
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out, index=False, lineterminator="\n")
    report(activities, persons, zone_by_release, zone_changes(args.old_zones, args.new_zones), table,
           transition_table(activities, zone_by_release["old"], zone_by_release["new"]), Path(args.plans).name)
    print(f"[exposure] wrote {len(table)} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
