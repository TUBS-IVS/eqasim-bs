"""Metrics of one arm of the i453/i443 1 % A/B, read from the run's own files. Read-only.

Usage (from the arms12 worktree, so the committed #443 modules are importable):
    python ab_metrics_i453.py <arm> <output_path> <working_directory>

Every figure is measured from the FINAL MATSim iteration (the archive <output_path>/matsim_output) or from the
prepared input population of the same run; nothing is expanded from the 1 % sample. The same script, with the same
code, measures all three arms, so arm 0 (code before #453/#443) gets the plan check and the stuck categories too.
Prints one JSON object.
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

from braunschweig.analysis import stuck_agents
from braunschweig.matsim.simulation import plan_check

arm, output_path, working_directory = sys.argv[1:4]
res = {"arm": arm}

archive = os.path.join(output_path, "matsim_output")
if not os.path.exists(os.path.join(archive, "output_events.xml.gz")):
    raise FileNotFoundError(f"no MATSim archive with output_events.xml.gz under {archive}")
res["archive"] = archive

# Iterations actually run and the termination outcome.
modestats = pd.read_csv(os.path.join(archive, "modestats.csv"), sep=";")
res["last_iteration"] = int(modestats["iteration"].max())

# 1. Stuck agents of the final iteration by category (committed #443 module; end from the run's output config).
summary = stuck_agents.stuck_summary(archive)
res["stuck"] = summary

# 2. Plan check of the prepared input population (the file MATSim read), same thresholds as production.
# Arms 1 and 2 share one working directory; the arm's own prepare directory holds <prefix>config.xml.
prefix = f"braunschweig_1pct_i453_{arm}_"
configs = glob.glob(os.path.join(working_directory, "braunschweig.matsim.simulation.prepare__*.cache",
                                 f"{prefix}config.xml"))
if len(configs) != 1:
    raise ValueError(f"expected exactly one prepared {prefix}config.xml, found {configs}")
config_path = configs[0]
population_path = plan_check.resolve_population_path(config_path)
res["plan_check_input"] = {"config": config_path, "population": population_path}
res["plan_check"] = plan_check.summarise(
    plan_check.iter_person_plans(population_path), simulation_end_s=plan_check.MAX_PLAN_TIME_SECONDS,
    walk_max_distance_m=plan_check.DEFAULT_WALK_MAX_DISTANCE_M,
    bicycle_max_distance_m=plan_check.DEFAULT_BICYCLE_MAX_DISTANCE_M,
    leg_max_duration_s=plan_check.DEFAULT_LEG_MAX_DURATION_S)

# 3. Final-iteration trips: frozen (tour touches outside) versus in-region, walk/bicycle beyond the thresholds.
trips = pd.read_csv(os.path.join(archive, "eqasim_trips.csv.gz"), sep=";", low_memory=False)
persons = pd.read_csv(os.path.join(archive, "output_persons.csv.gz"), sep=";", usecols=["person", "subpopulation"],
                      dtype=str, keep_default_na=False)
residents = set(persons.loc[persons["subpopulation"].str.strip() == "", "person"])
trips["person_id"] = trips["person_id"].astype(str)
trips = trips[trips["person_id"].isin(residents)].sort_values(["person_id", "person_trip_id"]).reset_index(drop=True)
tour = trips["preceding_purpose"].eq("home").groupby(trips["person_id"]).cumsum()
touches = trips["preceding_purpose"].eq("outside") | trips["following_purpose"].eq("outside")
frozen = touches.groupby([trips["person_id"], tour]).transform("any").to_numpy()
km = trips["euclidean_distance"].astype(float).to_numpy() / 1000.0
mode = trips["mode"].astype(str).to_numpy()


def legs(mask):
    return int(np.asarray(mask).sum())


res["final_trips"] = {
    "n_resident_trips": int(len(trips)),
    "n_trips_in_frozen_tours": legs(frozen),
    "frozen": {
        "walk": legs(frozen & (mode == "walk")),
        "walk_over_5km": legs(frozen & (mode == "walk") & (km > 5)),
        "walk_over_20km": legs(frozen & (mode == "walk") & (km > 20)),
        "bicycle": legs(frozen & (mode == "bicycle")),
        "bicycle_over_30km": legs(frozen & (mode == "bicycle") & (km > 30)),
        "travel_time_over_3h": legs(frozen & (trips["travel_time"].astype(float).to_numpy() > 3 * 3600)),
    },
    "in_region": {
        "walk_over_5km": legs(~frozen & (mode == "walk") & (km > 5)),
        "walk_over_20km": legs(~frozen & (mode == "walk") & (km > 20)),
        "bicycle_over_30km": legs(~frozen & (mode == "bicycle") & (km > 30)),
    },
    "mode_share_pct_frozen_tours": {k: round(100 * v, 2) for k, v in
                                    pd.Series(mode[frozen & (mode != "outside")]).value_counts(normalize=True).items()},
    "mode_share_pct_in_region_tours": {k: round(100 * v, 2) for k, v in
                                       pd.Series(mode[~frozen & (mode != "outside")]).value_counts(normalize=True).items()},
}

# 4. The outside-tour-modes stage report: only arm 2 runs the stage with the flag on (arm 1's stage passes the frame
#    through and writes no report; arm 0's code has no such stage). The stage's cache directory is named after its
#    alias node in the DAG, so look for the report under any matching cache directory and require exactly one.
res["outside_tour_modes_report"] = None
if arm == "arm2":
    reports = glob.glob(os.path.join(working_directory, "*.cache", "outside_tour_modes_report.json"))
    if len(reports) != 1:
        raise ValueError(f"expected exactly one outside_tour_modes_report.json, found {reports}")
    res["outside_tour_modes_report"] = json.load(open(reports[0]))
    res["outside_tour_modes_report_path"] = reports[0]

print(json.dumps(res, indent=1, default=str))
