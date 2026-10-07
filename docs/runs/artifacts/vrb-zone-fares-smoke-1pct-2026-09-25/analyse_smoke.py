"""Analysis of the #430 VRB zone fare 1 % ON/OFF smoke (run manifest vrb-zone-fares-smoke-1pct-2026-09-25).

Input: an evidence directory with the layout of felix:/home/felix/i430_data, i.e. ``on_result/`` and
``off_result/`` (each with ``matsim_output/``) plus ``cache_off/braunschweig.data.vrb.zone_polygons__*.cache/``.
The small inputs are committed next to this script; the large ones (eqasim_trips, output_legs,
output_persons) stay on the server and are identified by sha256 in the manifest.

Output: ``summary.json`` in the evidence directory with the fare outcome shares (iterations 1-20 and the
last iteration), the fallback share, the prepared-input coverage, the point zones, the MATSim stopwatch
runtimes, the mode shares of both arms and the share of final-plan PT trips that contain a long-distance
ride, by ticket category. Descriptive smoke figures only: no reference is compared.

Usage: python analyse_smoke.py <evidence-directory>
"""
import glob
import json
import sys
from pathlib import Path

import pandas as pd

INFORMATIONAL = {"day_ticket_cap_applied"}
FALLBACK = {"category_missing", "category_unknown", "line_scope_missing", "vrb_pair_undefined_fallback",
            "external_rail_beyond_bands"}


def seconds(value):
    if pd.isna(value) or str(value).strip() == "":
        return float("nan")
    hours, minutes, secs = str(value).strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def main(root: Path) -> dict:
    on, off = root / "on_result", root / "off_result"
    summary = {}

    frames = [pd.read_csv(path) for path in glob.glob(str(on / "matsim_output/ITERS/it.*/*.vrb_fare_outcomes.csv"))]
    outcomes = pd.concat(frames).pivot_table(index="iteration", columns="outcome", values="count",
                                             aggfunc="sum", fill_value=0).sort_index()
    quote_columns = [column for column in outcomes.columns if column not in INFORMATIONAL]
    outcomes["quotes"] = outcomes[quote_columns].sum(axis=1)
    fallback_columns = [column for column in quote_columns if column in FALLBACK]
    outcomes["fallback"] = outcomes[fallback_columns].sum(axis=1) if fallback_columns else 0
    replanned = outcomes.loc[outcomes.index >= 1]
    totals = replanned[quote_columns].sum()
    last = outcomes.loc[outcomes.index.max()]
    summary["quotes_iterations_1_20"] = int(totals.sum())
    summary["outcomes_iterations_1_20_share"] = {c: round(float(totals[c] / totals.sum()), 4) for c in quote_columns
                                                 if totals[c] > 0}
    summary["outcomes_last_iteration"] = {c: int(last[c]) for c in quote_columns if last[c] > 0}
    summary["fallback_quotes_iterations_1_20"] = int(replanned["fallback"].sum())
    summary["day_ticket_cap_applied_iterations_1_20"] = int(replanned.get("day_ticket_cap_applied", pd.Series(0)).sum())

    inputs = json.loads(next(on.glob("*vrb_fare_inputs_report.json")).read_text(encoding="utf-8"))
    summary["inputs"] = {key: inputs[key] for key in ("facilities", "zoned_facilities", "facility_zone_coverage",
                                                     "schedule_lines", "lines_with_scope", "line_scope_coverage")}
    summary["inputs"]["zoned_facilities_zone_55_56"] = {z: inputs["zoned_facilities_by_zone"].get(z, 0) for z in ("55", "56")}
    summary["inputs"]["pre_cut_zone_tool"] = {k: inputs["pre_cut_zone_tool"][k] for k in ("facilities", "zoned", "boundary_ties")}
    zones = json.loads(next((root / "cache_off").glob("*/zone_polygons_report.json")).read_text(encoding="utf-8"))
    summary["zone_polygons"] = {key: zones[key] for key in ("polygon_count", "point_zone_count", "invalid_repaired",
                                                           "point_zones", "trimmed_overlaps")}

    runtime = {}
    for name, arm in (("off", off), ("on", on)):
        stopwatch = pd.read_csv(arm / "matsim_output/stopwatch.csv", sep=";")
        stopwatch.columns = [column.strip() for column in stopwatch.columns]
        stopwatch = stopwatch[stopwatch["iteration"] >= 1]
        runtime[name] = {"iterations": int(len(stopwatch)),
                         "replanning_s_mean": round(stopwatch["replanning"].map(seconds).mean(), 1),
                         "mobsim_s_mean": round(stopwatch["mobsim"].map(seconds).mean(), 1),
                         "iteration_s_mean": round(stopwatch["iteration.1"].map(seconds).mean(), 1)}
    runtime["ratio_on_off"] = {key: round(runtime["on"][key] / runtime["off"][key], 3)
                               for key in ("replanning_s_mean", "mobsim_s_mean", "iteration_s_mean")}
    summary["runtime_iterations_1_20"] = runtime

    for name, arm in (("off", off), ("on", on)):
        modestats = pd.read_csv(arm / "matsim_output/modestats.csv", sep=";")
        summary[f"modestats_last_iteration_{name}"] = {k: round(float(v), 4) for k, v in modestats.iloc[-1].items()
                                                       if k != "iteration"}
        trips = pd.read_csv(arm / "matsim_output/eqasim_trips.csv.gz", sep=";")
        summary[f"eqasim_trips_{name}"] = int(len(trips))
        summary[f"eqasim_trip_mode_share_{name}"] = trips["mode"].value_counts(normalize=True).round(4).to_dict()

    legs = pd.read_csv(on / "matsim_output/output_legs.csv.gz", sep=";", low_memory=False)
    persons = pd.read_csv(on / "matsim_output/output_persons.csv.gz", sep=";", low_memory=False)
    scopes = pd.read_csv(next(on.glob("*vrb_line_scopes.csv")), dtype=str)
    long_distance_lines = set(scopes.loc[scopes["tariff_scope"] == "long_distance", "line_id"])
    pt = legs[legs["mode"] == "pt"].copy()
    pt["long_distance"] = pt["transit_line"].astype(str).isin(long_distance_lines)
    pt_trips = pt.groupby("trip_id").agg(person=("person", "first"), long_distance=("long_distance", "any"))
    pt_trips = pt_trips.join(persons.set_index("person")["ptSubscriptionType"], on="person")
    by_category = pt_trips.groupby("ptSubscriptionType")["long_distance"].agg(["sum", "count"])
    summary["final_plan_pt_trips"] = int(len(pt_trips))
    summary["final_plan_pt_trips_with_long_distance"] = int(pt_trips["long_distance"].sum())
    summary["final_plan_pt_trips_by_category"] = {category: {"pt_trips": int(row["count"]),
                                                             "with_long_distance": int(row["sum"])}
                                                  for category, row in by_category.iterrows()}

    (root / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1])), indent=1, default=str))
