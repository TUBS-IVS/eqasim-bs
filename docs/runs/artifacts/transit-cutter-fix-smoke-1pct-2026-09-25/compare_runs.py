"""Before/after comparison of two MATSim runs of the transit schedule cutter fix smoke
(run manifest transit-cutter-fix-smoke-1pct-2026-09-25).

``before`` is a run on the scenario cut by the OLD TransitScheduleCutter, ``after`` a run on the scenario cut by
the fixed one; population, fare inputs, configuration and iterations are the same. Input: the two MATSim output
directories (each with modestats.csv, stopwatch.csv, eqasim_pt.csv.gz, output_transitSchedule.xml.gz and
ITERS/it.*/*vrb_fare_outcomes.csv). Output (stdout, JSON): per run the last-iteration mode shares, the MATSim
runtimes of iterations 1-20, the final-iteration PT legs by transit mode, the PT legs alighting or boarding at
the stations named on the command line (e.g. the PT gate stations the old cut left without rail arrivals) and
the long-distance fare outcome count of iterations 1-20. Descriptive figures only: no reference is compared.

Usage: python compare_runs.py <before output dir> <after output dir> [<station name> ...]
"""
import glob
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


def seconds(value):
    if pd.isna(value) or str(value).strip() == "":
        return float("nan")
    hours, minutes, secs = str(value).strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(secs)


def stop_names(schedule_path):
    names = {}
    with gzip.open(schedule_path, "rb") as f:
        for _, element in ET.iterparse(f, events=("end",)):
            if element.tag == "stopFacility":
                names[element.get("id")] = element.get("name") or ""
            elif element.tag == "transitLine":
                element.clear()
    return names


def summary(directory: Path, stations):
    result = {}
    modestats = pd.read_csv(directory / "modestats.csv", sep=";")
    result["modestats_last_iteration"] = {k: round(float(v), 4) for k, v in modestats.iloc[-1].items() if k != "iteration"}

    stopwatch = pd.read_csv(directory / "stopwatch.csv", sep=";")
    stopwatch.columns = [c.strip() for c in stopwatch.columns]
    stopwatch = stopwatch[stopwatch["iteration"] >= 1]
    result["runtime_iterations_1_20_s_mean"] = {c: round(stopwatch[c].map(seconds).mean(), 1)
                                                for c in ("replanning", "mobsim", "iteration.1")}

    legs = pd.read_csv(directory / "eqasim_pt.csv.gz", sep=";", dtype=str)
    result["pt_legs"] = int(len(legs))
    result["pt_legs_by_mode"] = legs["transit_mode"].value_counts().to_dict()
    names = stop_names(directory / "output_transitSchedule.xml.gz")
    access = legs["access_stop_id"].map(names)
    egress = legs["egress_stop_id"].map(names)
    result["pt_legs_at_stations"] = {s: {"boarding": int((access == s).sum()), "alighting": int((egress == s).sum())}
                                     for s in stations}

    frames = [pd.read_csv(p) for p in glob.glob(str(directory / "ITERS/it.*/*vrb_fare_outcomes.csv"))]
    if frames:
        outcomes = pd.concat(frames)
        outcomes = outcomes[outcomes["iteration"] >= 1]
        totals = outcomes.groupby("outcome")["count"].sum()
        result["long_distance_flat_quotes_iterations_1_20"] = int(totals.get("long_distance_flat", 0))
        result["quotes_iterations_1_20"] = int(totals.drop(labels=["day_ticket_cap_applied"], errors="ignore").sum())
    return result


if __name__ == "__main__":
    before, after, stations = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    print(json.dumps({"before": summary(before, stations), "after": summary(after, stations)}, indent=1, ensure_ascii=False))
