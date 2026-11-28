"""Count the trips whose main mode differs between two MATSim plan files (issue #679, task C7).

Used to check how many selected-plan trips the 5 MATSim iterations of arm C changed against the prepared
population (after the initial mode choice), overall, for zero-length trips and for the loop trips
(``--loop-keys``, matched as in i679_e2e_metrics.py). Trip and main-mode definitions:
``i679_e2e_metrics.iter_selected_plans`` / ``main_mode``.

Usage: python i679_plan_mode_diff.py BEFORE.xml.gz AFTER.xml.gz --loop-keys loops.csv --out diff.json
"""
import argparse
import collections
import json
import sys

import pandas as pd

from i679_e2e_metrics import iter_selected_plans, main_mode


def modes(path):
    out = {}
    for person_id, acts, trips in iter_selected_plans(path):
        for k, legs in enumerate(trips):
            zero = acts[k][1] == acts[k + 1][1] and acts[k][2] == acts[k + 1][2]
            out[(person_id, k)] = (main_mode(legs), zero)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--loop-keys", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    keys = pd.read_csv(args.loop_keys)
    loop_trips = {(str(p), int(a) - 1) for p, a in keys[["person_id", "activity_index"]].itertuples(index=False)}
    before, after = modes(args.before), modes(args.after)
    common = set(before) & set(after)
    changed = collections.Counter()
    changed_zero = collections.Counter()
    changed_loop = collections.Counter()
    for key in common:
        (m0, z0), (m1, _z1) = before[key], after[key]
        if m0 != m1:
            changed[f"{m0}->{m1}"] += 1
            if z0:
                changed_zero[f"{m0}->{m1}"] += 1
            if key in loop_trips:
                changed_loop[f"{m0}->{m1}"] += 1
    result = {"trips_before": len(before), "trips_after": len(after), "trips_common": len(common),
              "changed_total": sum(changed.values()), "changed": dict(changed),
              "changed_zero_length_total": sum(changed_zero.values()), "changed_zero_length": dict(changed_zero),
              "changed_loop_trips_total": sum(changed_loop.values()), "changed_loop_trips": dict(changed_loop)}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
