"""Follow-up measurements of the 1 % arms A, B, C (issue #679, task C7 fix round).

1. Which trips of ``trips.simulated`` differ between the arms, and why. ``trips.simulated`` is the output of
   ``braunschweig.synthesis.outside_tour_modes.stage`` on ``trips.final`` (identical in every arm) and the located
   activities. That stage re-decides only the legs of the tours it flags: a home-based tour with a destination outside
   the cordon polygon or an ``outside`` purpose at either end (``outside_tour_modes.tours.outside_tour_flags``). Here the
   flags are recomputed per arm with the stage's own functions (``tours.home_based_tour_ids``, ``leg_endpoints``,
   ``destination_outside``, ``outside_tour_flags``; polygon ``data.spatial.cordon.build_cordon_polygon`` with the stage's
   ``cordon_network_buffer_fraction``), and every differing trip is classified by whether its tour is flagged in either
   arm. It also checks whether two sets of differing trips are the same trips.
2. The zero-length walk and bicycle legs that are NOT loop legs in arms B and C: how many leave a loop activity (the leg
   whose origin is the loop activity, e.g. the way home of home -> stroll -> home), by destination purpose and by the
   loop's origin kind; how many already had length 0 in arm A (same person and trip index); their diary travel time
   (``trips.final`` arrival minus departure); and the straight-line kilometres the same legs have in arm A.

Inputs: three synpp caches (or kept stage copies, ``i679_keep_stage_pickles.py``) and a cache holding the municipalities
stage; the stage pickle per arm is the newest ``pipeline.json`` entry (``i679_ab_metrics.resolve_stage``). Run with the
arm's eqasim-bs worktree on ``sys.path`` (``--repo``) so the stage's own functions are used.

Usage: python i679_followup_metrics.py --a A/cache --b B/keep --c C/cache --municipalities-cache C/cache \
           --repo C/eqasim-bs --out followup.json
"""
import argparse
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd

from i679_ab_metrics import STAGES, leg_lengths, resolve_stage

OUTSIDE_STAGE = "braunschweig.synthesis.outside_tour_modes.stage"
MUNICIPALITIES_STAGE = "data.spatial.municipalities"
MODES = ("walk", "bicycle")


def load(cache, stage):
    path, key, entry = resolve_stage(cache, stage)
    with open(path, "rb") as handle:
        return pickle.load(handle), key, entry


def arm(cache, polygon):
    trips_final, _, _ = load(cache, STAGES["trips_final"])
    trips_sim, key_sim, _ = load(cache, STAGES["trips_simulated"])
    locations, _, _ = load(cache, STAGES["locations"])
    (secondary, _convergence), _, _ = load(cache, STAGES["secondary"])
    report_path = os.path.join(cache, key_sim + ".cache", "outside_tour_modes_report.json")
    report = json.load(open(report_path, encoding="utf-8")) if os.path.isfile(report_path) else None

    from braunschweig.synthesis.outside_tour_modes import tours
    ordered = trips_final.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
    tour_ids = tours.home_based_tour_ids(ordered)
    endpoints = tours.leg_endpoints(ordered, locations)
    dest_outside = tours.destination_outside(endpoints, polygon)
    flagged = tours.outside_tour_flags(ordered, tour_ids, dest_outside)
    flags = pd.DataFrame({"person_id": ordered["person_id"], "trip_index": ordered["trip_index"],
                          "flagged": flagged, "dest_outside": dest_outside, "tour_id": tour_ids})
    loops = (secondary.loc[secondary["loop_origin"].fillna("").astype(str) != "",
                           ["person_id", "activity_index", "loop_origin"]]
             if "loop_origin" in secondary.columns else None)
    legs, _ = leg_lengths(trips_sim, locations)
    return {"trips_final": trips_final, "trips_sim": trips_sim, "flags": flags, "loops": loops, "legs": legs,
            "report": report, "key_sim": key_sim}


def diff_trips(x, y):
    cols = ["person_id", "trip_index", "mode", "departure_time", "arrival_time"]
    m = x["trips_sim"][cols].merge(y["trips_sim"][cols], on=["person_id", "trip_index"], suffixes=("_x", "_y"))
    mode = m["mode_x"].astype(str) != m["mode_y"].astype(str)
    time = (m["departure_time_x"] != m["departure_time_y"]) | (m["arrival_time_x"] != m["arrival_time_y"])
    return m.loc[mode, ["person_id", "trip_index"]], m.loc[time, ["person_id", "trip_index"]]


def classify(changed, x, y):
    f = changed.merge(x["flags"][["person_id", "trip_index", "flagged"]], on=["person_id", "trip_index"]) \
               .merge(y["flags"][["person_id", "trip_index", "flagged"]], on=["person_id", "trip_index"],
                      suffixes=("_x", "_y"))
    return {
        "trips": int(len(changed)),
        "persons": int(changed["person_id"].nunique()),
        "tour_flagged_in_both": int((f["flagged_x"] & f["flagged_y"]).sum()),
        "tour_flagged_only_in_first": int((f["flagged_x"] & ~f["flagged_y"]).sum()),
        "tour_flagged_only_in_second": int((~f["flagged_x"] & f["flagged_y"]).sum()),
        "tour_flagged_in_neither": int((~f["flagged_x"] & ~f["flagged_y"]).sum()),
    }


def keyset(frame):
    return set(map(tuple, frame[["person_id", "trip_index"]].to_numpy().tolist()))


def zero_length_non_loop(z, a):
    """Zero-length walk/bicycle legs of arm z that are not loop legs, classified; lengths of the same legs in arm a."""
    loops = z["loops"]
    loop_origin = {(p, i): o for p, i, o in loops[["person_id", "activity_index", "loop_origin"]].itertuples(index=False)}
    legs = z["legs"].copy()
    legs["is_loop_leg"] = [(p, t) in loop_origin for p, t in zip(legs["person_id"], legs["to_index"])]
    legs["leaves_loop_origin"] = [loop_origin.get((p, f), "") for p, f in zip(legs["person_id"], legs["from_index"])]
    tf = z["trips_final"][["person_id", "trip_index", "departure_time", "arrival_time", "mode"]].rename(
        columns={"mode": "diary_mode"})
    tf["diary_travel_time_s"] = tf["arrival_time"] - tf["departure_time"]
    legs = legs.merge(tf[["person_id", "trip_index", "diary_travel_time_s", "diary_mode"]],
                      on=["person_id", "trip_index"], how="left")
    legs = legs.merge(a["legs"][["person_id", "trip_index", "length_m"]].rename(columns={"length_m": "length_m_a"}),
                      on=["person_id", "trip_index"], how="left")
    out = {}
    for mode in MODES:
        sel = legs[(legs["mode"] == mode) & ~legs["is_loop_leg"] & (legs["length_m"] == 0.0)]
        leaves = sel[sel["leaves_loop_origin"] != ""]
        other = sel[sel["leaves_loop_origin"] == ""]

        def block(frame):
            return {
                "legs": int(len(frame)),
                "zero_length_in_arm_a_too": int((frame["length_m_a"] == 0.0).sum()),
                "km_in_arm_a": float(frame["length_m_a"].sum() / 1000.0),
                "diary_travel_time_s_median": (float(frame["diary_travel_time_s"].median()) if len(frame) else None),
                "diary_travel_time_s_p25": (float(frame["diary_travel_time_s"].quantile(0.25)) if len(frame) else None),
                "diary_travel_time_s_p75": (float(frame["diary_travel_time_s"].quantile(0.75)) if len(frame) else None),
                "by_destination_purpose": {str(k): int(v) for k, v in frame["following_purpose"].astype(str).value_counts().items()},
                "by_diary_mode": {str(k): int(v) for k, v in frame["diary_mode"].astype(str).value_counts().items()},
            }
        out[mode] = {
            "zero_length_non_loop_legs": int(len(sel)),
            "leaving_a_loop": dict(block(leaves), by_loop_origin={str(k): int(v) for k, v in leaves["leaves_loop_origin"].value_counts().items()}),
            "not_leaving_a_loop": block(other),
        }
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--c", required=True)
    parser.add_argument("--municipalities-cache", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    sys.path.append(args.repo)
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction

    # The municipalities stage and the buffer fraction the outside-tour stage of arm C depends on.
    _, key_c, entry_c = resolve_stage(args.c, OUTSIDE_STAGE)
    muni_keys = [k for k in entry_c["dependencies"] if k.rpartition("__")[0] == MUNICIPALITIES_STAGE]
    with open(os.path.join(args.municipalities_cache, muni_keys[0] + ".p"), "rb") as handle:
        municipalities = pickle.load(handle)
    fraction = float(entry_c["config"]["cordon_network_buffer_fraction"])
    polygon = build_cordon_polygon(municipalities, buffer_m_from_fraction(municipalities, fraction))

    arms = {name: arm(cache, polygon) for name, cache in (("A", args.a), ("B", args.b), ("C", args.c))}
    result = {"inputs": {"municipalities_key": muni_keys[0], "cordon_network_buffer_fraction": fraction,
                         "trips_simulated_keys": {n: a["key_sim"] for n, a in arms.items()}},
              "flagged_tour_legs": {n: int(a["flags"]["flagged"].sum()) for n, a in arms.items()},
              "outside_tour_reports": {n: ({k: v for k, v in a["report"].items() if not isinstance(v, (dict, list))}
                                           if a["report"] else None) for n, a in arms.items()},
              "differences": {}}
    sets = {}
    for x, y in (("A", "C"), ("B", "C"), ("A", "B")):
        mode_changed, time_changed = diff_trips(arms[x], arms[y])
        sets[(x, y, "mode")] = keyset(mode_changed)
        sets[(x, y, "time")] = keyset(time_changed)
        result["differences"][f"{x}_vs_{y}"] = {
            "mode_changed": classify(mode_changed, arms[x], arms[y]),
            "time_changed": classify(time_changed, arms[x], arms[y]),
        }
    result["same_trips"] = {
        "A_vs_C_time_changed == B_vs_C_mode_changed": sets[("A", "C", "time")] == sets[("B", "C", "mode")],
        "overlap_A_vs_C_time_and_B_vs_C_mode": len(sets[("A", "C", "time")] & sets[("B", "C", "mode")]),
        "A_vs_C_time_changed == A_vs_B_time_changed": sets[("A", "C", "time")] == sets[("A", "B", "time")],
        "B_vs_C_mode_changed_within_A_vs_C_mode_changed": len(sets[("B", "C", "mode")] & sets[("A", "C", "mode")]),
    }
    result["zero_length_non_loop_legs"] = {z: zero_length_non_loop(arms[z], arms["A"]) for z in ("B", "C")}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
    print(json.dumps(result, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
