"""A/B metrics of the stroll and dog-walk loop placement (issue #679, task C7 step 1).

Compares two isolated 1 % synthesis runs of the same eqasim-bs commit and seed that differ only in
``secondary_loop_zero_displacement`` (arm OFF: false, arm ON: true). Every figure is computed from the
synpp stage outputs of the two run caches and the two run logs; nothing is typed in by hand.

Inputs (per arm): the synpp ``working_directory`` (``RUN_DIR/cache``) and the run log
(``RUN_DIR/logs/run_<timestamp>.log``). The stage pickle of a stage is the entry of ``pipeline.json``
with that stage name and the newest ``updated`` time whose ``.p`` file exists; the chosen hash and time
are written to the output so the reader can check that it is the run's own product.

Stages read:
- ``braunschweig.synthesis.portal_trips.trips_final`` (alias ``synthesis.population.trips.final``): the
  trips the chain solver works on (purposes, times, modes).
- ``braunschweig.synthesis.outside_tour_modes.stage`` (alias ``synthesis.population.trips.simulated``):
  the trips as the MATSim population is written (modes of the tours touching outside re-judged).
- ``braunschweig.synthesis.outside_tour_modes.activities_stage`` (activities of the simulated day) and
  ``braunschweig.synthesis.commute_day.activities_day_stage`` (activities.final).
- ``braunschweig.synthesis.commute_day.spatial_locations_day`` (alias
  ``synthesis.population.spatial.locations``): one geometry per activity.
- ``braunschweig.synthesis.locations.secondary_chainsolvers``: ``(df_locations, df_convergence)``; in the
  ON arm ``df_locations`` carries ``loop_origin`` and the stage folder holds
  ``secondary_locations_report.json``.

Leg length: straight line in metres between the geometries of the activity before and after the trip
(trip ``t`` of a person runs from activity ``t`` to activity ``t + 1``), in the CRS of the locations
frame (projected, metric; recorded in the output). Mode: the mode of ``trips.simulated``.

Loop legs: the trips INTO an activity that the ON arm placed by the loop rule (a row of the ON arm's
secondary result with a non-empty ``loop_origin``). The same (person_id, activity_index) keys are applied
to the OFF arm, whose trips and activities are identical (checked here). Loops of persons with an
anchored or linked activity are flagged but kept on the solver in the ON arm; they carry no
``loop_origin`` and are therefore NOT in this loop-leg set (their count is in the stage report).

Equality: ``trips.final`` is upstream of the secondary locations and must be identical between the arms;
``trips.simulated`` and the simulated activities are downstream of them (the outside-tour mode stage
judges the tours touching outside on their located places), so the counts per purpose must be equal
while modes and times may differ; ``differences_downstream_of_locations`` counts the differing rows per
column and the mode transitions.

Out-and-back strolls (added for arm E against arm D): when the ON arm's secondary result carries the column
``is_out_and_back_stroll`` (the marker of the out-and-back split, docs/codebase/notes/leisure-loops.md), the block
``out_and_back_strolls`` takes the ON arm's marked activities as keys and reports, in BOTH arms, the leg INTO each
(trip ``activity_index - 1``) and the leg AFTER it (trip ``activity_index``, the diary way home): count, written mode,
straight-line length per mode and in total, and the diary mode (``trips.final``). For the OFF arm it also reports how
its own secondary result treated the same keys (``loop_origin`` of the OFF rows, empty = no loop row).
``--stroll-keys-out`` writes the per-key table (one row per synthetic person; it stays on the run server).

Usage (eqasim conda env, run from outside the repository):
    python i679_ab_metrics.py --off-cache OFF/cache --on-cache ON/cache \
        --off-log OFF/logs/run_x.log --on-log ON/logs/run_y.log --out metrics.json
"""
import argparse
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd

STAGES = {
    "trips_final": "braunschweig.synthesis.portal_trips.trips_final",
    "trips_simulated": "braunschweig.synthesis.outside_tour_modes.stage",
    "activities_simulated": "braunschweig.synthesis.outside_tour_modes.activities_stage",
    "activities_final": "braunschweig.synthesis.commute_day.activities_day_stage",
    "locations": "braunschweig.synthesis.commute_day.spatial_locations_day",
    "secondary": "braunschweig.synthesis.locations.secondary_chainsolvers",
}
REPORT_FILENAME = "secondary_locations_report.json"
LEG_MODES = ("walk", "bicycle")
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)
WALK_LONG_M = 5000.0
BICYCLE_LONG_M = 30000.0
LOG_TAG = "[braunschweig.secondary_chainsolvers]"


def resolve_stage(cache, stage):
    """Return (pickle path, entry key, entry) of the newest pipeline.json entry of ``stage``."""
    with open(os.path.join(cache, "pipeline.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    candidates = []
    for key, entry in meta.items():
        name, _, digest = key.rpartition("__")
        if name != stage:
            continue
        path = os.path.join(cache, key + ".p")
        if os.path.isfile(path):
            candidates.append((float(entry.get("updated") or 0.0), key, path, entry))
    if not candidates:
        raise RuntimeError(f"no pickle of {stage} in {cache}")
    candidates.sort()
    updated, key, path, entry = candidates[-1]
    return path, key, entry


def load_stage(cache, stage, provenance):
    path, key, entry = resolve_stage(cache, stage)
    config = entry.get("config", {})
    provenance[stage] = {
        "key": key, "updated_unix": entry.get("updated"),
        "sampling_rate": config.get("sampling_rate"),
        "secondary_loop_zero_displacement": config.get("secondary_loop_zero_displacement"),
    }
    with open(path, "rb") as handle:
        return pickle.load(handle), key


def counts_by(frame, column):
    return {str(k): int(v) for k, v in frame[column].astype(str).value_counts().sort_index().items()}


def distribution(values):
    values = np.asarray(values, dtype=float)
    out = {"n": int(values.size)}
    if values.size == 0:
        return out
    out.update({
        "mean_m": float(values.mean()),
        "min_m": float(values.min()),
        "max_m": float(values.max()),
        "n_zero": int((values == 0.0).sum()),
        "sum_km": float(values.sum() / 1000.0),
    })
    for q in QUANTILES:
        out[f"p{int(round(q * 100))}_m"] = float(np.quantile(values, q))
    return out


def leg_lengths(trips, locations):
    """Trips with the straight-line length between their two activities (NaN when a geometry is missing)."""
    loc = locations[["person_id", "activity_index", "geometry"]]
    legs = trips[["person_id", "trip_index", "mode", "preceding_purpose", "following_purpose"]].copy()
    legs["from_index"] = legs["trip_index"]
    legs["to_index"] = legs["trip_index"] + 1
    legs = legs.merge(loc.rename(columns={"activity_index": "from_index", "geometry": "g_from"}),
                      on=["person_id", "from_index"], how="left")
    legs = legs.merge(loc.rename(columns={"activity_index": "to_index", "geometry": "g_to"}),
                      on=["person_id", "to_index"], how="left")
    missing = legs["g_from"].isna() | legs["g_to"].isna()
    length = np.full(len(legs), np.nan)
    ok = ~missing.to_numpy()
    gf = legs.loc[ok, "g_from"].to_numpy()
    gt = legs.loc[ok, "g_to"].to_numpy()
    length[ok] = [a.distance(b) for a, b in zip(gf, gt)]
    legs["length_m"] = length
    return legs.drop(columns=["g_from", "g_to"]), int(missing.sum())


def leg_summary(legs, loop_keys):
    """Distribution per mode: all legs, without loop legs, loop legs only, legs leaving a loop activity, and the
    legs INTO a leisure activity that is not a loop (the legs the loop-free leisure estimates act on)."""
    into = pd.MultiIndex.from_frame(legs[["person_id", "to_index"]].rename(columns={"to_index": "a"}))
    out_of = pd.MultiIndex.from_frame(legs[["person_id", "from_index"]].rename(columns={"from_index": "a"}))
    is_loop_leg = into.isin(loop_keys)
    leaves_loop = out_of.isin(loop_keys)
    summary = {}
    for mode in LEG_MODES:
        of_mode = (legs["mode"] == mode).to_numpy()
        valid = ~np.isnan(legs["length_m"].to_numpy())
        block = {}
        to_leisure = (legs["following_purpose"].astype(str) == "leisure").to_numpy()
        for label, mask in (("all", of_mode), ("without_loop_legs", of_mode & ~is_loop_leg),
                            ("loop_legs", of_mode & is_loop_leg), ("legs_leaving_a_loop", of_mode & leaves_loop),
                            ("leisure_without_loop_legs", of_mode & ~is_loop_leg & to_leisure)):
            values = legs["length_m"].to_numpy()[mask & valid]
            block[label] = distribution(values)
            block[label]["n_missing_geometry"] = int((mask & ~valid).sum())
            block[label]["n_over_5km"] = int((values > WALK_LONG_M).sum())
            block[label]["n_over_30km"] = int((values > BICYCLE_LONG_M).sum())
        summary[mode] = block
    summary["loop_legs_by_mode"] = {str(k): int(v) for k, v in legs.loc[is_loop_leg, "mode"].value_counts().items()}
    summary["n_loop_legs"] = int(is_loop_leg.sum())
    return summary


def log_lines(path):
    lines = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if LOG_TAG in line and any(word in line for word in (
                    "success rate", "fallback", "FALLBACK", "PRIMARY", "carla", "loops:", "unbounded")):
                lines.append(line.rstrip("\n"))
    return lines


def arm_metrics(cache, log_path, provenance):
    trips_final, _ = load_stage(cache, STAGES["trips_final"], provenance)
    trips_sim, _ = load_stage(cache, STAGES["trips_simulated"], provenance)
    acts_sim, _ = load_stage(cache, STAGES["activities_simulated"], provenance)
    acts_final, _ = load_stage(cache, STAGES["activities_final"], provenance)
    locations, _ = load_stage(cache, STAGES["locations"], provenance)
    secondary, secondary_key = load_stage(cache, STAGES["secondary"], provenance)
    df_secondary, df_convergence = secondary

    report_path = os.path.join(cache, secondary_key + ".cache", REPORT_FILENAME)
    report = None
    if os.path.isfile(report_path):
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)

    leisure_persons = acts_sim.loc[acts_sim["purpose"].astype(str) == "leisure", "person_id"].nunique()
    metrics = {
        "persons_in_trips_final": int(trips_final["person_id"].nunique()),
        "trips_final_n": int(len(trips_final)),
        "trips_final_by_following_purpose": counts_by(trips_final, "following_purpose"),
        "trips_final_by_mode": counts_by(trips_final, "mode"),
        "trips_simulated_n": int(len(trips_sim)),
        "trips_simulated_by_following_purpose": counts_by(trips_sim, "following_purpose"),
        "trips_simulated_by_mode": counts_by(trips_sim, "mode"),
        "activities_simulated_n": int(len(acts_sim)),
        "activities_simulated_by_purpose": counts_by(acts_sim, "purpose"),
        "activities_final_n": int(len(acts_final)),
        "activities_final_by_purpose": counts_by(acts_final, "purpose"),
        "persons_with_leisure_activity_simulated": int(leisure_persons),
        "persons_simulated": int(acts_sim["person_id"].nunique()),
        "locations_n": int(len(locations)),
        "locations_crs": str(getattr(locations, "crs", None)),
        "secondary_rows": int(len(df_secondary)),
        "secondary_has_loop_origin_column": "loop_origin" in df_secondary.columns,
        "convergence": {
            "problems": int(len(df_convergence)),
            "valid": int(df_convergence["valid"].sum()),
            "success_rate": float(df_convergence["valid"].mean()) if len(df_convergence) else None,
            "problems_by_size": {str(k): int(v) for k, v in df_convergence["size"].value_counts().sort_index().items()},
            "valid_by_size": {str(k): int(v) for k, v in df_convergence.groupby("size")["valid"].sum().items()},
        },
        "stage_report": report,
        "log_lines": log_lines(log_path),
    }
    return metrics, trips_final, trips_sim, acts_sim, locations, df_secondary


def frames_equal(a, b, columns, keys):
    a = a[columns].sort_values(keys).reset_index(drop=True)
    b = b[columns].sort_values(keys).reset_index(drop=True)
    if len(a) != len(b):
        return False
    return bool(a.astype(str).equals(b.astype(str)))


def column_differences(a, b, columns, keys):
    """Rows that differ per column between two frames with the same keys (None when the keys differ)."""
    a = a[columns].sort_values(keys).reset_index(drop=True)
    b = b[columns].sort_values(keys).reset_index(drop=True)
    if len(a) != len(b) or not a[keys].astype(str).equals(b[keys].astype(str)):
        return None
    out = {}
    for column in columns:
        if column in keys:
            continue
        differs = a[column].astype(str) != b[column].astype(str)
        out[column] = {"rows": int(differs.sum()), "persons": int(a.loc[differs, "person_id"].nunique())}
    return out


def mode_transitions(a, b):
    """Counts of (mode OFF -> mode ON) for the trips whose mode differs between the arms."""
    merged = a[["person_id", "trip_index", "mode"]].merge(
        b[["person_id", "trip_index", "mode"]], on=["person_id", "trip_index"], suffixes=("_off", "_on"))
    changed = merged[merged["mode_off"].astype(str) != merged["mode_on"].astype(str)]
    return {f"{k[0]}->{k[1]}": int(v) for k, v in changed.groupby(["mode_off", "mode_on"]).size().items()}


def out_and_back_strolls(sec_on, sec_off, tf_on, legs_by_arm, keys_out=None):
    """Legs into and after the ON arm's out-and-back strolls, in both arms (None without the marker column)."""
    if "is_out_and_back_stroll" not in sec_on.columns:
        return None
    marked = sec_on["is_out_and_back_stroll"].fillna(False).astype(bool)
    keys = sec_on.loc[marked, ["person_id", "activity_index"]].drop_duplicates().copy()
    if "loop_origin" in sec_off.columns:
        off_rows = sec_off[["person_id", "activity_index", "loop_origin"]].copy()
        off_rows["loop_origin"] = off_rows["loop_origin"].fillna("").astype(str)
        off_rows = off_rows.groupby(["person_id", "activity_index"], as_index=False)["loop_origin"].agg(
            lambda values: "|".join(sorted(set(values))))
        keys = keys.merge(off_rows.rename(columns={"loop_origin": "off_loop_origin"}),
                          on=["person_id", "activity_index"], how="left")
        keys["off_loop_origin"] = keys["off_loop_origin"].fillna("<no OFF row>").replace("", "<not a loop row>")
    else:
        keys["off_loop_origin"] = "<OFF result has no loop_origin column>"
    block = {
        "n_keys": int(len(keys)),
        "rows_marked_in_on_secondary": int(marked.sum()),
        "persons": int(keys["person_id"].nunique()),
        "off_treatment_of_the_keys": {str(k): int(v) for k, v in keys["off_loop_origin"].value_counts().items()},
    }
    table = keys.copy()
    for role, offset in (("into", -1), ("way_home", 0)):
        role_keys = keys[["person_id", "activity_index"]].assign(trip_index=keys["activity_index"] + offset)
        diary = role_keys.merge(tf_on[["person_id", "trip_index", "mode", "following_purpose"]],
                                on=["person_id", "trip_index"], how="left")
        role_block = {
            "diary_mode": {str(k): int(v) for k, v in diary["mode"].astype(str).value_counts().items()},
            "diary_following_purpose": {str(k): int(v) for k, v in diary["following_purpose"].astype(str).value_counts().items()},
            "n_without_trip": int(diary["mode"].isna().sum()),
        }
        table = table.merge(diary[["person_id", "activity_index", "mode"]].rename(
            columns={"mode": f"{role}_diary_mode"}), on=["person_id", "activity_index"], how="left")
        for arm, legs in legs_by_arm.items():
            sel = role_keys.merge(legs[["person_id", "trip_index", "mode", "length_m"]],
                                  on=["person_id", "trip_index"], how="left")
            arm_block = {
                "n": int(sel["mode"].notna().sum()),
                "by_mode": {str(k): int(v) for k, v in sel["mode"].dropna().astype(str).value_counts().items()},
                "all_modes": distribution(sel["length_m"].dropna().to_numpy()),
            }
            for mode in sorted(sel["mode"].dropna().astype(str).unique()):
                arm_block[mode] = distribution(sel.loc[sel["mode"].astype(str) == mode, "length_m"].dropna().to_numpy())
            role_block[arm] = arm_block
            table = table.merge(sel[["person_id", "activity_index", "mode", "length_m"]].rename(
                columns={"mode": f"{role}_mode_{arm}", "length_m": f"{role}_length_m_{arm}"}),
                on=["person_id", "activity_index"], how="left")
        block[role] = role_block
    if keys_out:
        table.to_csv(keys_out, index=False)
    return block


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--off-cache", required=True)
    parser.add_argument("--on-cache", required=True)
    parser.add_argument("--off-log", required=True)
    parser.add_argument("--on-log", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--loop-keys-out", help="CSV of the ON arm's loop activities (person_id, activity_index, loop_origin)")
    parser.add_argument("--stroll-keys-out", help="CSV of the ON arm's out-and-back strolls with the legs into and "
                        "after them in both arms (person_id, activity_index, ...)")
    args = parser.parse_args(argv)

    provenance = {"off": {}, "on": {}}
    off, tf_off, ts_off, as_off, loc_off, sec_off = arm_metrics(args.off_cache, args.off_log, provenance["off"])
    on, tf_on, ts_on, as_on, loc_on, sec_on = arm_metrics(args.on_cache, args.on_log, provenance["on"])

    trip_cols = ["person_id", "trip_index", "preceding_purpose", "following_purpose",
                 "departure_time", "arrival_time", "mode"]
    act_cols = [c for c in ["person_id", "activity_index", "purpose", "start_time", "end_time"] if c in as_off.columns]
    equality = {
        "trips_final_identical": frames_equal(tf_off, tf_on, trip_cols, ["person_id", "trip_index"]),
        "trips_simulated_identical": frames_equal(ts_off, ts_on, trip_cols, ["person_id", "trip_index"]),
        "activities_simulated_identical": frames_equal(as_off, as_on, act_cols, ["person_id", "activity_index"]),
        "trips_final_by_purpose_equal": off["trips_final_by_following_purpose"] == on["trips_final_by_following_purpose"],
        "trips_simulated_by_purpose_equal": off["trips_simulated_by_following_purpose"] == on["trips_simulated_by_following_purpose"],
        "activities_simulated_by_purpose_equal": off["activities_simulated_by_purpose"] == on["activities_simulated_by_purpose"],
        "activities_final_by_purpose_equal": off["activities_final_by_purpose"] == on["activities_final_by_purpose"],
        "leisure_participation_equal": off["persons_with_leisure_activity_simulated"] == on["persons_with_leisure_activity_simulated"],
    }
    # trips.simulated / activities.simulated are downstream of the secondary locations (the outside-tour mode
    # stage judges modes and times on the located tours), so they may differ; say where.
    differences = {
        "trips_simulated_by_column": column_differences(ts_off, ts_on, trip_cols, ["person_id", "trip_index"]),
        "trips_simulated_mode_transitions": mode_transitions(ts_off, ts_on),
        "activities_simulated_by_column": column_differences(as_off, as_on, act_cols, ["person_id", "activity_index"]),
    }
    # Whether the differing trips belong to persons with an outside activity (the only persons the
    # outside-tour mode stage acts on).
    merged = ts_off[trip_cols].merge(ts_on[trip_cols], on=["person_id", "trip_index"], suffixes=("_off", "_on"))
    changed = merged[(merged["mode_off"].astype(str) != merged["mode_on"].astype(str))
                     | (merged["departure_time_off"].astype(str) != merged["departure_time_on"].astype(str))]
    outside_persons = set(tf_off.loc[(tf_off["following_purpose"].astype(str) == "outside")
                                     | (tf_off["preceding_purpose"].astype(str) == "outside"), "person_id"])
    differences["changed_trips_of_persons_with_outside_activity"] = {
        "changed_trips": int(len(changed)),
        "of_persons_with_outside": int(changed["person_id"].isin(outside_persons).sum()),
    }

    loops = sec_on.loc[sec_on["loop_origin"].fillna("").astype(str) != "",
                       ["person_id", "activity_index", "loop_origin", "location_id"]].copy() \
        if "loop_origin" in sec_on.columns else pd.DataFrame(columns=["person_id", "activity_index", "loop_origin", "location_id"])
    loop_keys = pd.MultiIndex.from_frame(loops[["person_id", "activity_index"]].rename(columns={"activity_index": "a"}))
    legs_off, miss_off = leg_lengths(ts_off, loc_off)
    legs_on, miss_on = leg_lengths(ts_on, loc_on)
    if args.loop_keys_out:
        # One row per loop activity of the ON arm with the trip that leads into it (trip_index =
        # activity_index - 1): its diary mode (trips.final), its written mode in both arms
        # (trips.simulated) and its straight-line length in both arms.
        out = loops[["person_id", "activity_index", "loop_origin"]].copy()
        out["trip_index"] = out["activity_index"] - 1
        out = out.merge(tf_on[["person_id", "trip_index", "mode"]].rename(columns={"mode": "diary_mode"}),
                        on=["person_id", "trip_index"], how="left")
        for arm, legs in (("off", legs_off), ("on", legs_on)):
            out = out.merge(legs[["person_id", "trip_index", "mode", "length_m"]].rename(
                columns={"mode": f"mode_written_{arm}", "length_m": f"length_m_{arm}"}),
                on=["person_id", "trip_index"], how="left")
        out.to_csv(args.loop_keys_out, index=False)
    # The loop activity's purpose and its place kind in the ON arm, for the reader.
    loop_activity_purposes = as_on.merge(loops[["person_id", "activity_index"]], on=["person_id", "activity_index"])
    result = {
        "provenance": provenance,
        "equality": equality,
        "differences_downstream_of_locations": differences,
        "off": off,
        "on": on,
        "loop_activities_placed_by_rule": {
            "n": int(len(loops)),
            "by_origin": {str(k): int(v) for k, v in loops["loop_origin"].value_counts().items()},
            "by_activity_purpose": counts_by(loop_activity_purposes, "purpose") if len(loop_activity_purposes) else {},
            "persons": int(loops["person_id"].nunique()),
        },
        "legs": {
            "definition": "straight line between consecutive activity geometries of synthesis.population.spatial.locations; "
                          "mode of trips.simulated; loop legs = trips into an ON-arm loop_origin activity",
            "off": {"n_legs": int(len(legs_off)), "n_missing_geometry": miss_off, **leg_summary(legs_off, loop_keys)},
            "on": {"n_legs": int(len(legs_on)), "n_missing_geometry": miss_on, **leg_summary(legs_on, loop_keys)},
        },
    }
    strolls = out_and_back_strolls(sec_on, sec_off, tf_on, {"off": legs_off, "on": legs_on}, args.stroll_keys_out)
    if strolls is not None:
        result["out_and_back_strolls"] = strolls
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, default=str)
    print(json.dumps({"equality": result["equality"], "differences": differences}, indent=2))
    return 0 if all(result["equality"][k] for k in (
        "trips_final_by_purpose_equal", "activities_simulated_by_purpose_equal", "leisure_participation_equal")) else 3


if __name__ == "__main__":
    sys.exit(main())
