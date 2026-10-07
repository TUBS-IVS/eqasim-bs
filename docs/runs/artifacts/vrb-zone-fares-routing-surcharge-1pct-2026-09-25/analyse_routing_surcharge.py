"""Analysis of the #430 long-distance routing surcharge check (run manifest
vrb-zone-fares-routing-surcharge-1pct-2026-09-25).

Compares two MATSim runs on the SAME prepared ON-arm scenario of the smoke vrb-zone-fares-smoke-1pct-2026-09-25:
``before`` is that smoke's ON arm (eqasim-java-bs 9998db31d, no routing surcharge) and ``after`` is a rerun of
only the MATSim stage with eqasim-java-bs 23e7c66f9 (long-distance routing surcharge valued per person and
trip). Everything else (population, network, schedule, fare inputs, config, seed, iterations) is identical, so
the differences are the effect of the surcharge plus MATSim's replanning noise.

Input: the two MATSim output directories, the line scope table of the prepared scenario, and the MATSim log of
the ``after`` run. Output: a JSON summary at ``<out-summary.json>`` (committed as ``summary.json``) with, per
run, the fare outcome counts of iterations 1-20 (``long_distance_flat`` = routed PT candidates containing a long-distance ride), the
last-iteration mode shares, the MATSim stopwatch runtimes and the final-plan PT trips containing a
long-distance ride; plus the surcharge log lines of the ``after`` run. Descriptive figures only: no reference
is compared.

Usage: python analyse_routing_surcharge.py <before-output-dir> <after-output-dir> <line-scopes.csv>
       <after-run-log> <out-summary.json>
"""
import glob
import json
import re
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


def single(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected exactly one {pattern} in {directory}, found {[m.name for m in matches]}")
    return matches[0]


def run_summary(directory: Path, long_distance_lines: set) -> dict:
    summary = {}
    frames = [pd.read_csv(path) for path in glob.glob(str(directory / "ITERS/it.*/*vrb_fare_outcomes.csv"))]
    if not frames:
        raise FileNotFoundError(f"no vrb_fare_outcomes.csv under {directory}/ITERS")
    outcomes = pd.concat(frames).pivot_table(index="iteration", columns="outcome", values="count",
                                             aggfunc="sum", fill_value=0).sort_index()
    replanned = outcomes.loc[outcomes.index >= 1]
    quote_columns = [column for column in outcomes.columns if column not in INFORMATIONAL]
    totals = replanned[quote_columns].sum()
    summary["fare_iterations"] = int(len(replanned))
    summary["quotes_iterations_1_20"] = int(totals.sum())
    summary["long_distance_flat_quotes_iterations_1_20"] = int(totals.get("long_distance_flat", 0))
    summary["long_distance_flat_share_iterations_1_20"] = round(float(totals.get("long_distance_flat", 0) / totals.sum()), 5)
    summary["fallback_quotes_iterations_1_20"] = int(sum(totals.get(column, 0) for column in FALLBACK))
    summary["day_ticket_cap_applied_iterations_1_20"] = int(replanned.get("day_ticket_cap_applied", pd.Series(0)).sum())
    summary["outcomes_iterations_1_20"] = {column: int(totals[column]) for column in quote_columns if totals[column] > 0}

    stopwatch = pd.read_csv(single(directory, "stopwatch.csv"), sep=";")
    stopwatch.columns = [column.strip() for column in stopwatch.columns]
    stopwatch = stopwatch[stopwatch["iteration"] >= 1]
    summary["runtime_iterations_1_20"] = {"iterations": int(len(stopwatch)),
                                          "replanning_s_mean": round(stopwatch["replanning"].map(seconds).mean(), 1),
                                          "mobsim_s_mean": round(stopwatch["mobsim"].map(seconds).mean(), 1),
                                          "iteration_s_mean": round(stopwatch["iteration.1"].map(seconds).mean(), 1)}

    modestats = pd.read_csv(single(directory, "modestats.csv"), sep=";")
    summary["modestats_last_iteration"] = {key: round(float(value), 4) for key, value in modestats.iloc[-1].items()
                                           if key != "iteration"}

    legs = pd.read_csv(single(directory, "output_legs.csv.gz"), sep=";", low_memory=False)
    pt = legs[legs["mode"] == "pt"].copy()
    pt["long_distance"] = pt["transit_line"].astype(str).isin(long_distance_lines)
    pt_trips = pt.groupby("trip_id")["long_distance"].any()
    summary["final_plan_pt_trips"] = int(len(pt_trips))
    summary["final_plan_pt_trips_with_long_distance"] = int(pt_trips.sum())
    return summary


def main(before: Path, after: Path, scopes_path: Path, after_log: Path, out: Path) -> dict:
    scopes = pd.read_csv(scopes_path, dtype=str)
    long_distance_lines = set(scopes.loc[scopes["tariff_scope"] == "long_distance", "line_id"])
    summary = {"long_distance_lines": len(long_distance_lines),
               "before": run_summary(before, long_distance_lines),
               "after": run_summary(after, long_distance_lines)}
    log_lines = after_log.read_text(encoding="utf-8", errors="replace").splitlines()
    summary["after_surcharge_log"] = {
        "calculator": [line.split("[vrb-fares] ", 1)[1] for line in log_lines if "long-distance routing surcharge on" in line],
        "stop_finder_last": next((line.split("[vrb-fares] ", 1)[1] for line in reversed(log_lines)
                                  if "routing surcharge valued for the trip" in line), None),
        "reference_fallback_warnings": sum(1 for line in log_lines if "without a surcharge valued for its trip" in line),
    }
    match = re.search(r"in (\d+)/(\d+) stop searches, reference surcharge in (\d+)",
                      summary["after_surcharge_log"]["stop_finder_last"] or "")
    if match:
        valued, total, reference = (int(group) for group in match.groups())
        summary["after_surcharge_log"]["stop_searches_valued_share"] = round(valued / total, 6)
    out.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(main(*(Path(argument) for argument in sys.argv[1:6])), indent=1, default=str))
