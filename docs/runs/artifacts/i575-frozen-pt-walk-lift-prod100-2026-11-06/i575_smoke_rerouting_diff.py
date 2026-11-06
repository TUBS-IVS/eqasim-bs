"""#575 ADR-0189 smoke: what does MATSim's start-up routing change in the NON-lifted trips of the lifted persons?

Read-only. MATSim's PrepareForSim re-routes a plan that has any leg without a route, so a lifted person's other trips
may be routed again too. Per sample person, the trips of the lifted input plan (``lifted_sample.xml.gz``) and of the
smoke's output plan (``output_plans.xml.gz``) are paired in order (trips split at non-interaction activities, the
lifted trip excluded) and compared: the sequence of leg modes, the travel time (sum of leg trav_time), a
``walkFallbackFrom`` stamp, and the end times of the activities.

Usage: python i575_smoke_rerouting_diff.py <lifted_sample.xml.gz> <smoke output dir>
"""
import collections
import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def seconds(text):
    if not text:
        return None
    parts = [float(p) for p in text.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def trips(elements):
    """[(origin type, origin end, [legs]), ...]; a leg = (mode, trav_s, stamp, lifted)."""
    out, current, origin = [], [], None
    for element in elements:
        if element.tag == "activity":
            kind = element.get("type")
            if kind.endswith(" interaction"):
                continue
            if origin is not None:
                out.append((origin[0], origin[1], current))
            origin, current = (kind, seconds(element.get("end_time"))), []
        elif element.tag == "leg":
            names = {a.get("name"): a.text for a in element.iter("attribute")}
            current.append((element.get("mode"), seconds(element.get("trav_time")), names.get("walkFallbackFrom"),
                            "liftedFrom" in names))
    return out


def plans(path):
    out = {}
    for _, element in ET.iterparse(gzip.open(path, "rb")):
        if element.tag == "person":
            plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
            out[element.get("id")] = trips(list(plan))
            element.clear()
    return out


def main() -> int:
    before = plans(Path(sys.argv[1]))
    after = plans(Path(sys.argv[2]) / "output_plans.xml.gz")
    counts = collections.Counter()
    deltas = []
    for person, trips_in in before.items():
        trips_out = after[person]
        if len(trips_in) != len(trips_out):
            counts["persons_trip_count_differs"] += 1
            continue
        for (kind_in, end_in, legs_in), (kind_out, end_out, legs_out) in zip(trips_in, trips_out):
            if any(leg[3] for leg in legs_in):
                continue
            counts["trips_compared"] += 1
            modes_in = [leg[0] for leg in legs_in]
            modes_out = [leg[0] for leg in legs_out]
            counts["legs_mode_sequence_changed"] += modes_in != modes_out
            counts["stamp_lost"] += any(leg[2] for leg in legs_in) and not any(leg[2] for leg in legs_out)
            counts["origin_end_time_changed"] += end_in != end_out
            travel_in = sum(leg[1] or 0.0 for leg in legs_in)
            travel_out = sum(leg[1] or 0.0 for leg in legs_out)
            if abs(travel_in - travel_out) > 60.0:
                counts["travel_time_changed_over_60s"] += 1
                deltas.append((person, kind_in, "+".join(modes_in), round(travel_in), "+".join(modes_out),
                               round(travel_out)))
    print(dict(counts))
    for row in deltas[:25]:
        print("changed:", row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
