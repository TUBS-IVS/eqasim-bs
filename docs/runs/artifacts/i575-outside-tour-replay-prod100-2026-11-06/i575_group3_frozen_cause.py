"""#575 group-3 root cause: are the 489 unconnected "outside_tour" legs of the B3 probe frozen, and if so, why?

Read-only. The B3 long-walk probe (``b3b_long_walk_probe.py``) routed EVERY pt leg of the 1,576 persons with a frozen
walk of 5 km or more and labelled each non-portal, non-in-commuter leg ``outside_tour`` without asking whether its own
tour is frozen. This probe takes the legs of that label without a connection (``check`` False), finds each leg's
origin activity in the uncut population (``braunschweig.matsim.scenario.population``, leg index i = activity i ->
i+1) and the same activity in the prepared population (the file the plan check and MATSim read: routed and cut),
matched by type, coordinates and end time (coordinates and type only when prepare moved the time), and reports per leg:

* ``frozen``: the plan check's rule (``plan_check.leg_tour_frozen``: the home-based tour touches an ``outside``
  activity) on the prepared plan, for the leg leaving that activity;
* for a frozen leg: the cause, from the ``outside`` activities of its tour in the prepared plan: per such activity the
  routing modes of the trips before and after it and whether the uncut plan has an ``outside`` activity there;
* ``prepared_mode``: the leg's mode in the prepared plan (walk with a ``walkFallbackFrom`` stamp = pt as walk).

Writes ``group3_frozen_cause.csv.gz`` and prints the counts.

Usage on felix:
  python i575_group3_frozen_cause.py --code ~/prod100_20261031/eqasim-bs --cache ~/prod100_20261031/cache \
      --legs ~/scratch_b3/out_b2/legs_connection.csv.gz --out ~/scratch_i575/group3_cause
"""
from __future__ import annotations

import argparse
import collections
import gzip
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

POPULATION_STEM = "braunschweig.matsim.scenario.population__d66536c43b770db3cd52e1a76a1c4449"
PREPARE_STEM = "braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63"
PREPARED_FILE = "braunschweig_100pct_prod20261031_population.xml.gz"
UNCUT_FILE = "population.xml.gz"


def read_plans(path: Path, persons: set, parse_time) -> dict:
    """``{person_id: (activities, legs)}``; activity = (type, end_s, x, y), leg = (mode, routingMode, walkFallbackFrom)."""
    plans, root = {}, None
    with gzip.open(path, "rb") as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                if root is None:
                    root = element
                continue
            if element.tag != "person":
                continue
            person_id = element.get("id")
            if person_id in persons:
                plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
                acts, legs = [], []
                for child in plan:
                    if child.tag == "activity":
                        end = child.get("end_time")
                        acts.append((child.get("type"), parse_time(end) if end else None,
                                     round(float(child.get("x")), 1), round(float(child.get("y")), 1)))
                    elif child.tag == "leg":
                        attributes = {}
                        node = child.find("attributes")
                        if node is not None:
                            for attribute in node.findall("attribute"):
                                attributes[attribute.get("name")] = (attribute.text or "").strip()
                        legs.append((child.get("mode"), attributes.get("routingMode"),
                                     attributes.get("walkFallbackFrom")))
                plans[person_id] = (acts, legs)
            element.clear()
            root.clear()
    return plans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("--code", "--cache", "--legs", "--out"):
        parser.add_argument(name, required=True)
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(args.code).expanduser()))
    import pandas as pd
    from braunschweig.matsim.simulation import plan_check as C

    cache = Path(args.cache).expanduser()
    legs_table = pd.read_csv(Path(args.legs).expanduser(), dtype={"identifier": str})
    group = legs_table[(legs_table["label"] == "outside_tour") & ~legs_table["check"].astype(bool)].copy()
    group["person_id"] = group["identifier"].str.split(":").str[0]
    group["leg_index"] = group["identifier"].str.split(":").str[1].astype(int)
    persons = set(group["person_id"])
    print(f"group-3 legs {len(group)}, persons {len(persons)}", flush=True)

    t0 = time.time()
    uncut = read_plans(cache / (POPULATION_STEM + ".cache") / UNCUT_FILE, persons, C.parse_time_s)
    print(f"uncut plans {len(uncut)} in {time.time() - t0:.0f} s", flush=True)
    prepared = read_plans(cache / (PREPARE_STEM + ".cache") / PREPARED_FILE, persons, C.parse_time_s)
    print(f"prepared plans {len(prepared)} in {time.time() - t0:.0f} s", flush=True)

    rows = []
    for row in group.itertuples():
        acts_u, legs_u = uncut[row.person_id]
        origin = acts_u[row.leg_index]
        acts_p, legs_p = prepared[row.person_id]
        frozen = C.leg_tour_frozen([(a[0],) for a in acts_p])
        candidates = [k for k, a in enumerate(acts_p[:-1]) if a[0] == origin[0] and a[2:] == origin[2:]]
        exact = [k for k in candidates if acts_p[k][1] == origin[1]]
        match = "time" if len(exact) == 1 else ("place" if len(candidates) == 1 else
                                               ("ambiguous" if candidates else "none"))
        k = exact[0] if len(exact) == 1 else (candidates[0] if len(candidates) == 1 else None)
        record = {"identifier": row.identifier, "distance_km": row.distance_km, "match": match,
                  "uncut_outside_in_plan": any(a[0] == C.OUTSIDE for a in acts_u)}
        if k is None:
            rows.append(record)
            continue
        record["frozen"] = bool(frozen[k])
        record["prepared_mode"] = legs_p[k][0] + ("(walkFallbackFrom=" + legs_p[k][2] + ")" if legs_p[k][2] else "")
        # The tour of leg k in the prepared plan: legs between the home activities around it.
        start = max([j for j in range(0, k + 1) if j == 0 or acts_p[j][0] == C.HOME])
        end = min([j for j in range(k + 1, len(acts_p)) if acts_p[j][0] == C.HOME] or [len(acts_p) - 1])
        causes = []
        for j in range(start, end + 1):
            if acts_p[j][0] != C.OUTSIDE:
                continue
            before = legs_p[j - 1][1] or legs_p[j - 1][0] if j > 0 else "-"
            after = legs_p[j][1] or legs_p[j][0] if j < len(legs_p) else "-"
            causes.append(f"{before}>outside>{after}")
        record["tour_outside"] = ";".join(causes)
        record["tour_n_outside"] = len(causes)
        rows.append(record)
    frame = pd.DataFrame(rows)
    frame.to_csv(out / "group3_frozen_cause.csv.gz", index=False)
    print("match:", frame["match"].value_counts().to_dict())
    print("frozen:", frame["frozen"].value_counts(dropna=False).to_dict())
    frozen_rows = frame[frame["frozen"].eq(True)]
    print("frozen legs, outside activities in the tour by adjacent routing modes:",
          collections.Counter(c for s in frozen_rows["tour_outside"] for c in s.split(";") if c).most_common(15))
    print("prepared mode of the legs:", frame["prepared_mode"].value_counts(dropna=False).head(10).to_dict())
    print("persons with an outside activity in the UNCUT plan:", frame["uncut_outside_in_plan"].value_counts().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
