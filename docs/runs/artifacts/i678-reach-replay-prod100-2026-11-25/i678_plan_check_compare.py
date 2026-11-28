"""#678 B9 step 2: the branch's plan check counts (plan_check.summarise with the loop settings of the branch) on the
INPUT population and on the population AFTER the initial mode choice, to locate the exceeded threshold of the replay.

The replay's plan check (on the final population, after the lift) exceeded share_plans_non_monotonic_large in the
frozen resident block. This script counts the same block, with the same code and keys, on the two earlier
populations; it writes nothing but its output JSON.

Usage: python i678_plan_check_compare.py <eqasim-bs clone> <out json> <name=population> ...
"""
from __future__ import annotations

import json
import sys
import time

clone, out_path = sys.argv[1:3]
sys.path.append(clone)

import yaml  # noqa: E402

from braunschweig.matsim.simulation import plan_check as pc  # noqa: E402
from braunschweig.matsim.simulation.mode_choice_reach import ReachSettings  # noqa: E402

values = yaml.safe_load(open(f"{clone}/configs/base_bs.yml", encoding="utf-8"))["config"]
KEYS = ("n_persons", "n_plans_late", "share_plans_late", "n_plans_non_monotonic", "n_plans_non_monotonic_large",
        "share_plans_non_monotonic_large", "non_monotonic_by_lateness_band", "n_pt_trips", "n_pt_as_walk_long",
        "share_pt_as_walk_long", "n_unreached_unfit", "n_trips_fixed_border", "n_trips_fixed_length",
        "n_walk_trips_over_reached", "n_bicycle_trips_over_reached")
result = {}
for argument in sys.argv[3:]:
    name, path = argument.split("=", 1)
    started = time.time()
    summary = pc.summarise(
        pc.iter_person_plans(path), simulation_end_s=pc.MAX_PLAN_TIME_SECONDS,
        walk_max_distance_m=float(values[pc.KEY_WALK_MAX_DISTANCE_M]),
        bicycle_max_distance_m=float(values[pc.KEY_BICYCLE_MAX_DISTANCE_M]),
        leg_max_duration_s=float(values[pc.KEY_LEG_MAX_DURATION_S]),
        pt_as_walk_bound_min_distance_m=float(values[pc.KEY_PT_AS_WALK_BOUND_MIN_DISTANCE_M]),
        non_monotonic_bound_min_lateness_s=float(values[pc.KEY_NON_MONOTONIC_BOUND_MIN_LATENESS_S]),
        reach=ReachSettings(bool(values["braunschweig.mode_choice.isolate_outside_trips"]),
                            int(values["braunschweig.mode_choice.max_tour_length"])))
    result[name] = {f"{sub}/{block}": {k: data.get(k) for k in KEYS}
                    for sub, blocks in summary["subpopulations"].items() for block, data in blocks.items()}
    result[name]["seconds"] = round(time.time() - started)
    print(name, json.dumps(result[name]), flush=True)
with open(out_path, "w", encoding="utf-8") as handle:
    json.dump(result, handle, indent=2)
