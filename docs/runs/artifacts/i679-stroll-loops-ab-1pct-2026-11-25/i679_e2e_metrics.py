"""End-to-end metrics of the loop placement (issue #679, task C7 step 2).

Reads the MATSim output of the ON arm (``simulation_output`` of ``matsim.simulation.run``) and reports
- the trips of the SELECTED plans of the final iteration whose origin and destination activity have the
  same coordinate (zero straight-line length), by main mode and by destination activity type;
- the same for the trips that lead into a loop activity of the synthesis (the ON arm's
  ``loop_origin`` rows, ``--loop-keys`` CSV), matched by person id and main-activity index, with a
  cross table origin kind | diary mode | main mode | zero length or not | aligned (the plan's
  activity at that index is a leisure activity) or misaligned (the plan numbering differs for that
  person, e.g. after the cut);
- the stuckAndAbort events of the final iteration, by subpopulation, with
  ``braunschweig.analysis.stuck_agents`` of the run's own commit.

A trip is the sequence of legs between two main activities (activity types not ending in
" interaction"); its main mode is the leg attribute ``routingMode`` where present, else the mode of
its single leg. Main-activity index k of a person's selected plan is compared with the synthesis
``activity_index`` k (residents only: the loop keys hold resident person ids only).

Usage (eqasim conda env):
    python i679_e2e_metrics.py --sim-output DIR --loop-keys loops.csv --repo RUN_DIR/eqasim-bs --out e2e.json
"""
import argparse
import collections
import gzip
import json
import sys
import xml.etree.ElementTree as ET

import pandas as pd


def iter_selected_plans(path):
    """Yield (person_id, [main activities], [trips]) of each person's selected plan."""
    with gzip.open(path, "rb") as handle:
        person_id = None
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if event == "start" and elem.tag == "person":
                person_id = elem.get("id")
            elif event == "end" and elem.tag == "plan":
                if elem.get("selected") == "yes":
                    acts, trips, legs = [], [], []
                    for child in elem:
                        if child.tag == "activity":
                            if child.get("type", "").endswith(" interaction"):
                                continue
                            if acts:
                                trips.append(legs)
                            legs = []
                            acts.append((child.get("type"), child.get("x"), child.get("y"), child.get("facility")))
                        elif child.tag == "leg":
                            routing = None
                            distance = None
                            for sub in child:
                                if sub.tag == "attributes":
                                    for attribute in sub:
                                        if attribute.get("name") == "routingMode":
                                            routing = attribute.text
                                elif sub.tag == "route":
                                    distance = sub.get("distance")
                            legs.append((child.get("mode"), routing, distance))
                    yield person_id, acts, trips
            elif event == "end" and elem.tag == "person":
                elem.clear()


def main_mode(legs):
    routing = {leg[1] for leg in legs if leg[1]}
    if len(routing) == 1:
        return routing.pop()
    modes = {leg[0] for leg in legs}
    return modes.pop() if len(modes) == 1 else "+".join(sorted(modes))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sim-output", required=True, help="simulation_output directory of matsim.simulation.run")
    parser.add_argument("--loop-keys", required=True)
    parser.add_argument("--repo", required=True, help="eqasim-bs worktree of the run (for stuck_agents)")
    parser.add_argument("--simulation-end-s", type=float, default=108000.0)
    parser.add_argument("--plans-file", default="output_plans.xml.gz", help="plans file name inside --sim-output")
    parser.add_argument("--plans-only", action="store_true",
                        help="skip the stuck-agent count (for a standalone mode choice output without events)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    loop_keys = pd.read_csv(args.loop_keys)
    loops = {(str(p), int(a)): origin for p, a, origin in loop_keys[["person_id", "activity_index", "loop_origin"]].itertuples(index=False)}
    # Optional columns written by i679_ab_metrics.py --loop-keys-out: the diary mode of the trip into the loop.
    diary = ({(str(p), int(a)): str(m) for p, a, m in loop_keys[["person_id", "activity_index", "diary_mode"]].itertuples(index=False)}
             if "diary_mode" in loop_keys.columns else {})
    crosstab = collections.Counter()

    zero_by_mode = collections.Counter()
    zero_by_dest = collections.Counter()
    zero_route_distance_positive = collections.Counter()
    loop_by_mode = collections.Counter()
    loop_by_mode_zero = collections.Counter()
    loop_dest_type = collections.Counter()
    loop_found = 0
    n_trips = 0
    n_persons = 0
    for person_id, acts, trips in iter_selected_plans(f"{args.sim_output}/{args.plans_file}"):
        n_persons += 1
        for k, legs in enumerate(trips):
            n_trips += 1
            origin, dest = acts[k], acts[k + 1]
            zero = origin[1] == dest[1] and origin[2] == dest[2]
            mode = main_mode(legs)
            if zero:
                zero_by_mode[mode] += 1
                zero_by_dest[dest[0]] += 1
                if any(leg[2] not in (None, "", "0.0", "0") and float(leg[2]) > 0 for leg in legs):
                    zero_route_distance_positive[mode] += 1
            if (person_id, k + 1) in loops:
                loop_found += 1
                loop_by_mode[mode] += 1
                loop_dest_type[dest[0]] += 1
                if zero:
                    loop_by_mode_zero[mode] += 1
                # aligned: the plan's main activity k+1 is the leisure activity of the synthesis key
                aligned = dest[0] == "leisure"
                crosstab[(loops[(person_id, k + 1)], diary.get((person_id, k + 1), "?"), mode,
                          "zero" if zero else "nonzero", "aligned" if aligned else "misaligned")] += 1

    stuck = None
    if not args.plans_only:
        sys.path.append(args.repo)
        from braunschweig.analysis import stuck_agents
        events = stuck_agents.scan_stuck_events(f"{args.sim_output}/output_events.xml.gz")
        subpopulations = stuck_agents.person_subpopulations(f"{args.sim_output}/output_persons.csv.gz")
        events = events.assign(subpopulation=events["person"].astype(str).map(subpopulations).fillna("unknown"))
        stuck = {
            "events": int(len(events)),
            "persons": int(events["person"].nunique()),
            "by_subpopulation": {str(k): int(v) for k, v in events.groupby("subpopulation")["person"].nunique().items()},
            "at_simulation_end": int((events["time"].astype(float) >= args.simulation_end_s).sum()),
            "by_leg_mode": {str(k): int(v) for k, v in events["leg_mode"].astype(str).value_counts().items()},
        }

    result = {
        "persons_with_selected_plan": n_persons,
        "trips": n_trips,
        "zero_length_trips_by_main_mode": dict(zero_by_mode),
        "zero_length_trips_by_destination_type": dict(zero_by_dest),
        "zero_length_trips_with_positive_route_distance_by_mode": dict(zero_route_distance_positive),
        "loop_keys": int(len(loops)),
        "loop_trips_found_in_plans": loop_found,
        "loop_trips_by_main_mode": dict(loop_by_mode),
        "loop_trips_zero_length_by_main_mode": dict(loop_by_mode_zero),
        "loop_trips_by_destination_type": dict(loop_dest_type),
        "loop_trips_by_origin_diary_mode_main_mode_length_alignment": {
            "|".join(key): count for key, count in sorted(crosstab.items())},
        "stuck": stuck,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
