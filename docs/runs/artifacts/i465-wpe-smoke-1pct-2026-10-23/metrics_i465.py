"""Metrics of the #465 WP-E smoke (1 %), read from the run's own files. Read-only.

Usage (from the run's eqasim-bs worktree, PYTHONPATH=$PWD, so the committed modules are importable):
    python metrics_i465.py <label> <output_prefix> <output_path> <working_directory> <run log> [<run log> ...]

A copy of docs/runs/artifacts/i453-i443-ab-1pct-2026-10-16/ab_metrics_i453.py (same stuck, plan-check and
final-trip metrics, same definitions) with the output prefix as a parameter instead of the i453 arm name, plus:
the pt-as-walk counts that the plan check of this code version reports (the summarise() result of the main code),
the stage reports of the stages the run executed (read from the run logs) and the batch router files left in the
gate choice's cache directory (rows, no-route tasks, pt trips with a zero egress walk; the stage overwrites them per
router call, so only the LAST call's files remain). Every figure comes from the FINAL MATSim iteration or from the prepared
input population of the same run; nothing is expanded from the 1 % sample. Prints one JSON object.
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

from braunschweig.analysis import stuck_agents
from braunschweig.matsim.simulation import plan_check

label, prefix, output_path, working_directory = sys.argv[1:5]
res = {"label": label, "output_prefix": prefix}

archive = os.path.join(output_path, "matsim_output")
if not os.path.exists(os.path.join(archive, "output_events.xml.gz")):
    raise FileNotFoundError(f"no MATSim archive with output_events.xml.gz under {archive}")
res["archive"] = archive

modestats = pd.read_csv(os.path.join(archive, "modestats.csv"), sep=";")
res["last_iteration"] = int(modestats["iteration"].max())

# 1. Stuck agents of the final iteration by category (committed #443 module).
res["stuck"] = stuck_agents.stuck_summary(archive)

# 2. Plan check of the prepared input population (the file MATSim read), production thresholds.
configs = glob.glob(os.path.join(working_directory, "braunschweig.matsim.simulation.prepare__*.cache",
                                 f"{prefix}config.xml"))
if len(configs) != 1:
    raise ValueError(f"expected exactly one prepared {prefix}config.xml, found {configs}")
population_path = plan_check.resolve_population_path(configs[0])
res["plan_check_input"] = {"config": configs[0], "population": population_path}
res["plan_check"] = plan_check.summarise(
    plan_check.iter_person_plans(population_path), simulation_end_s=plan_check.MAX_PLAN_TIME_SECONDS,
    walk_max_distance_m=plan_check.DEFAULT_WALK_MAX_DISTANCE_M,
    bicycle_max_distance_m=plan_check.DEFAULT_BICYCLE_MAX_DISTANCE_M,
    leg_max_duration_s=plan_check.DEFAULT_LEG_MAX_DURATION_S)

# 3. Final-iteration trips: frozen (tour touches outside) versus in-region (definition of ab_metrics_i453.py).
trips = pd.read_csv(os.path.join(archive, "eqasim_trips.csv.gz"), sep=";", low_memory=False)
persons = pd.read_csv(os.path.join(archive, "output_persons.csv.gz"), sep=";", usecols=["person", "subpopulation"],
                      dtype=str, keep_default_na=False)
residents = set(persons.loc[persons["subpopulation"].str.strip() == "", "person"])
res["n_residents"] = len(residents)
trips["person_id"] = trips["person_id"].astype(str)
trips = trips[trips["person_id"].isin(residents)].sort_values(["person_id", "person_trip_id"]).reset_index(drop=True)
tour = trips["preceding_purpose"].eq("home").groupby(trips["person_id"]).cumsum()
touches = trips["preceding_purpose"].eq("outside") | trips["following_purpose"].eq("outside")
frozen = touches.groupby([trips["person_id"], tour]).transform("any").to_numpy()
km = trips["euclidean_distance"].astype(float).to_numpy() / 1000.0
mode = trips["mode"].astype(str).to_numpy()


def count(mask):
    return int(np.asarray(mask).sum())


res["final_trips"] = {
    "n_resident_trips": int(len(trips)),
    "n_trips_in_frozen_tours": count(frozen),
    "frozen": {
        "walk": count(frozen & (mode == "walk")),
        "walk_over_5km": count(frozen & (mode == "walk") & (km > 5)),
        "walk_over_20km": count(frozen & (mode == "walk") & (km > 20)),
        "bicycle": count(frozen & (mode == "bicycle")),
        "bicycle_over_30km": count(frozen & (mode == "bicycle") & (km > 30)),
        "travel_time_over_3h": count(frozen & (trips["travel_time"].astype(float).to_numpy() > 3 * 3600)),
    },
    "in_region": {
        "walk_over_5km": count(~frozen & (mode == "walk") & (km > 5)),
        "walk_over_20km": count(~frozen & (mode == "walk") & (km > 20)),
        "bicycle_over_30km": count(~frozen & (mode == "bicycle") & (km > 30)),
    },
    "mode_share_pct_frozen_tours": {k: round(100 * v, 2) for k, v in
                                    pd.Series(mode[frozen & (mode != "outside")]).value_counts(normalize=True).items()},
    "mode_share_pct_in_region_tours": {k: round(100 * v, 2) for k, v in
                                       pd.Series(mode[~frozen & (mode != "outside")]).value_counts(normalize=True).items()},
}

# 4. Stage reports of the stages this run executed (the copied working directory also holds the reports of older
#    config variants): the "Executing stage <name>__<hash>" lines of the run logs given after the four arguments.
executed = set()
for log_path in sys.argv[5:]:
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        executed.update(line.split("Executing stage ", 1)[1].split(" ", 1)[0]
                        for line in handle if "Executing stage " in line)
res["executed_stages"] = sorted(executed)
res["stage_reports"] = {}
for path in sorted(glob.glob(os.path.join(working_directory, "*.cache", "*report*.json"))):
    if os.path.basename(os.path.dirname(path))[:-len(".cache")] not in executed:
        continue
    try:
        res["stage_reports"][os.path.relpath(path, working_directory)] = json.load(open(path))
    except (ValueError, OSError) as error:
        res["stage_reports"][os.path.relpath(path, working_directory)] = f"unreadable: {error}"

# 5. Batch router outputs of the gate choice: rows, no-route tasks, pt trips with a zero egress walk. A pt trip
#    with a positive egress distance proves that its router output had no interaction activity before the last
#    leg (the leg-index comparison in BatchPublicTransportRouter then classifies exactly like the fixed code).
router = []
for trips_path in sorted(glob.glob(os.path.join(working_directory, "**", "router_trips.csv"), recursive=True)):
    work_dir = os.path.dirname(trips_path)
    entry = {"work_dir": os.path.relpath(work_dir, working_directory)}
    tasks_path = os.path.join(work_dir, "router_tasks.csv")
    rt = pd.read_csv(trips_path, dtype={"identifier": str})
    entry["trips_rows"] = int(len(rt))
    if os.path.exists(tasks_path):
        tk = pd.read_csv(tasks_path, dtype={"identifier": str})
        entry["tasks"] = int(len(tk))
        entry["no_route_tasks"] = int((~tk["identifier"].isin(set(rt["identifier"]))).sum())
    pt = rt[rt["is_only_walk"] == 0]
    entry["pt_trips"] = int(len(pt))
    entry["only_walk_trips"] = int((rt["is_only_walk"] == 1).sum())
    entry["pt_trips_zero_egress"] = int((pt["egress_distance_km"] <= 0).sum())
    entry["pt_egress_km_min_median_max"] = [round(float(x), 3) for x in
                                            (pt["egress_distance_km"].min(), pt["egress_distance_km"].median(),
                                             pt["egress_distance_km"].max())] if len(pt) else None
    entry["pt_transfer_km_max"] = round(float(pt["transfer_distance_km"].max()), 3) if len(pt) else None
    router.append(entry)
res["batch_router"] = router

print(json.dumps(res, indent=1, default=str))
