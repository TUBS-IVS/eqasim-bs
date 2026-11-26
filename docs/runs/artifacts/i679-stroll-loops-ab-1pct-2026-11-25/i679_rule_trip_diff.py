"""Trip-by-trip mode comparison for the mode rule of arm D (issue #679, follow-up of the evidence manifest).

1. Per arm (C, D): do the trips the rule catches (residents; same facility id at both ends and one end of type
   ``leisure``) change their main mode between the prepared population (after the initial mode choice) and MATSim's
   output_plans after iteration 5?
2. Between the arms, prepared population C vs D (and output_plans C vs D): the trips whose main mode differs, split into
   caught and non-caught trips, the net mode change of each part, and for the non-caught trips whether they lie in a
   tour that contains a caught trip (tours as the run's tour finder cuts them: a new tour at every departure from a
   home or an outside activity) or at least belong to a person with a caught trip.
Trips are keyed by (person id, index of the trip in the selected plan); trip and main-mode definitions:
``i679_loop_mode_rule_premise.iter_plans`` / ``main_mode``. Residents only.

Usage: python i679_rule_trip_diff.py --c-prepared P --c-it5 P --d-prepared P --d-it5 P --out out.json
"""
import argparse
import collections
import json
import sys

from i679_loop_mode_rule_premise import LEISURE, iter_plans, main_mode


def read(path):
    """{(person, k): (mode, caught, tour key)} for the residents of a plans file."""
    out = {}
    for person_id, subpopulation, acts, trips in iter_plans(path):
        if subpopulation:
            continue
        tour = 0
        for k, legs in enumerate(trips):
            if k > 0 and acts[k]["type"] in ("home", "outside"):
                tour += 1
            o, d = acts[k], acts[k + 1]
            caught = bool(o["facility"]) and o["facility"] == d["facility"] and LEISURE in (o["type"], d["type"])
            out[(person_id, k)] = (main_mode(legs), caught, (person_id, tour))
    return out


def net(changes):
    c = collections.Counter()
    for a, b in changes:
        c[a] -= 1
        c[b] += 1
    return {k: v for k, v in sorted(c.items()) if v}


def within(x, y):
    keys = set(x) & set(y)
    caught = [k for k in keys if x[k][1]]
    changed = [k for k in caught if x[k][0] != y[k][0]]
    return {"caught_trips": len(caught), "caught_trips_with_changed_mode": len(changed),
            "transitions": dict(collections.Counter(f"{x[k][0]}->{y[k][0]}" for k in changed))}


def between(c, d):
    keys = set(c) & set(d)
    differ = [k for k in keys if c[k][0] != d[k][0]]
    caught = [k for k in differ if c[k][1] or d[k][1]]
    other = [k for k in differ if not (c[k][1] or d[k][1])]
    caught_tours = {c[k][2] for k in keys if c[k][1]}
    caught_persons = {k[0] for k in keys if c[k][1]}
    return {
        "common_trips": len(keys),
        "trips_with_different_mode": len(differ),
        "caught": {"trips": len(caught), "net_mode_change_c_to_d": net((c[k][0], d[k][0]) for k in caught)},
        "not_caught": {
            "trips": len(other),
            "net_mode_change_c_to_d": net((c[k][0], d[k][0]) for k in other),
            "transitions": dict(collections.Counter(f"{c[k][0]}->{d[k][0]}" for k in other)),
            "in_a_tour_with_a_caught_trip": sum(1 for k in other if c[k][2] in caught_tours),
            "of_a_person_with_a_caught_trip": sum(1 for k in other if k[0] in caught_persons),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("c-prepared", "c-it5", "d-prepared", "d-it5"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    cp, ci, dp, di = read(args.c_prepared), read(args.c_it5), read(args.d_prepared), read(args.d_it5)
    result = {
        "within_arm_prepared_vs_iteration5": {"C": within(cp, ci), "D": within(dp, di)},
        "between_arms_C_vs_D": {"prepared": between(cp, dp), "iteration5": between(ci, di)},
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
