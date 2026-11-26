"""Collect the per-arm numbers of the #678 1 % comparison from one isolated run directory.

Usage: python i678_ab_collect.py RUN_DIR PREFIX

Reads, read only: the newest braunschweig.matsim.simulation.prepare stage directory of RUN_DIR/cache that holds
PREFIX + "config.xml" (its initial_mode_choice.json, plan_check.json, frozen_pt_walk_lift report if present, the
population and the standalone runner log), and the newest matsim.simulation.run stage directory (modestats.csv,
stopwatch.csv, output_plans.xml.gz, output_events.xml.gz, logfile.log). Prints one JSON document to stdout.
Standard library only.
"""
import csv
import glob
import gzip
import hashlib
import json
import os
import re
import sys


def newest(pattern, must_contain=None):
    hits = [p for p in glob.glob(pattern) if must_contain is None or os.path.exists(os.path.join(p, must_contain))]
    return max(hits, key=os.path.getmtime) if hits else None


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def count_persons(path):
    pattern = re.compile(rb"<person ")
    with gzip.open(path, "rb") as handle:
        return sum(len(pattern.findall(line)) for line in handle)


def count_stuck(path):
    pattern = re.compile(rb'type="stuckAndAbort"')
    modes = {}
    total = 0
    with gzip.open(path, "rb") as handle:
        for line in handle:
            if pattern.search(line):
                total += 1
                mode = re.search(rb'legMode="([^"]*)"', line)
                key = mode.group(1).decode() if mode else "?"
                modes[key] = modes.get(key, 0) + 1
    return total, modes


def grep(path, patterns, limit=40):
    out = []
    if not path or not os.path.exists(path):
        return out
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if any(p in line for p in patterns):
                out.append(line.rstrip()[:400])
                if len(out) >= limit:
                    break
    return out


def main():
    run_dir, prefix = sys.argv[1], sys.argv[2]
    cache = os.path.join(run_dir, "cache")
    result = {"run_dir": run_dir.replace(os.path.expanduser("~"), "~")}

    prep = newest(os.path.join(cache, "braunschweig.matsim.simulation.prepare__*.cache"), prefix + "config.xml")
    result["prepare_dir"] = os.path.basename(prep) if prep else None
    if prep:
        files = {}
        for name in sorted(os.listdir(prep)):
            path = os.path.join(prep, name)
            if name.endswith(".json") and os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    files[name] = json.load(handle)
        result["prepare_json"] = files
        for name in (prefix + "config.xml", prefix + "population.xml.gz", "plan_check.json"):
            path = os.path.join(prep, name)
            if os.path.exists(path):
                result.setdefault("prepare_sha256", {})[name] = sha256(path)
        population = os.path.join(prep, prefix + "population.xml.gz")
        if os.path.exists(population):
            result["prepare_population_persons"] = count_persons(population)
        logs = sorted(glob.glob(os.path.join(prep, "mode_choice", "*.log")))
        result["initial_runner_lines"] = [l for log in logs for l in grep(log, (
            "rejected by the model's filter", "Excluded subpopulations", "Exception", "ERROR"))]

    run = newest(os.path.join(cache, "matsim.simulation.run__*.cache"), os.path.join("simulation_output", "modestats.csv"))
    result["run_dir_stage"] = os.path.basename(run) if run else None
    if run:
        out = os.path.join(run, "simulation_output")
        with open(os.path.join(out, "modestats.csv"), encoding="utf-8") as handle:
            result["modestats"] = list(csv.DictReader(handle, delimiter=";"))
        with open(os.path.join(out, "stopwatch.csv"), encoding="utf-8") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        header = rows[0]
        # The duration block follows the empty column; take the second "replanning" and "iteration" columns.
        rep = [i for i, h in enumerate(header) if h == "replanning"][-1]
        itr = [i for i, h in enumerate(header) if h == "iteration"][-1]
        mob = [i for i, h in enumerate(header) if h == "mobsim"][-1]
        result["stopwatch"] = [{"iteration": r[0], "replanning": r[rep], "mobsim": r[mob], "iteration_total": r[itr]}
                               for r in rows[1:] if len(r) > itr]
        result["output_plans_persons"] = count_persons(os.path.join(out, "output_plans.xml.gz"))
        total, modes = count_stuck(os.path.join(out, "output_events.xml.gz"))
        result["stuck_last_iteration"] = {"total": total, "by_leg_mode": modes}
        for it in sorted(glob.glob(os.path.join(out, "ITERS", "it.*", "*.events.xml.gz"))):
            total, modes = count_stuck(it)
            result.setdefault("stuck_by_iteration", {})[os.path.basename(it)] = {"total": total, "by_leg_mode": modes}
        log = os.path.join(out, "logfile.log")
        result["kept_trip_lines"] = grep(log, ("rejected by the model's filter",), limit=100)
        result["exception_lines"] = grep(log, ("Exception", "\tat org.", "SEVERE"), limit=20)
        result["warn_error_count"] = sum(1 for _ in open(os.path.join(out, "logfileWarningsErrors.log"),
                                                         encoding="utf-8", errors="replace"))
        result["error_lines"] = grep(os.path.join(out, "logfileWarningsErrors.log"), (" ERROR ",), limit=20)
    json.dump(result, sys.stdout, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
