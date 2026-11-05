"""Replay the synthesis.population.sampled stage on the cached input of a production run, with two versions of the module.

The stage is replayed OUTSIDE synpp, with the arguments synpp passes it: the cached output of the parent stage as
``source``, the run's ``random_seed`` and ``sampling_rate``. The check that makes this a replay and not a guess is the
first comparison below: the unmodified module must reproduce the run's own cached output.

    python replay_sampled_stage.py --cache-dir <run>/cache --source <parent cache name> --output <stage cache name> \
        --old-module <sampled.py of the run> --new-module <sampled.py to test> \
        --output-hash-module <braunschweig/synpp_fork/output_hash.py> --seed 1234 --sampling-rate 1.0 \
        --scratch <empty dir> --results <results.json>

Read only on the cache directory. Writes two pickles into ``--scratch`` (removed again) and ``--results``.

What it reports (all printed, all in the results file):

1. the input: size, whether ``household_size`` equals the number of person rows of every household, and how many rows
   numpy's default (unstable) sort places differently from a stable sort;
2. the old module and the new module run on the same input: elapsed time and every warning the stage raised, the new
   module with numpy's VisibleDeprecationWarning turned into an error (as it is from numpy 1.24 on);
3. old output, new output and the run's cached output compared pairwise, exactly (shape, columns, dtypes, index and the
   number of mismatching cells per column, NaN equal to NaN);
4. the digest the fork's early cutoff compares: both outputs written to disk exactly as the fork writes a stage result
   (``pickle.dump(result, f, protocol=4)``), hashed with the fork's own ``hash_stage_output``, and compared with the
   ``output_hash`` recorded in the run's ``pipeline.json``, plus how many cache entries list the stage as a parent and
   how many of them recorded its digest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pickle
import platform
import shutil
import sys
import time
import warnings

import numpy as np
import pandas as pd

try:  # numpy 1.25 moved the warning classes into numpy.exceptions
    from numpy.exceptions import VisibleDeprecationWarning
except ImportError:
    from numpy import VisibleDeprecationWarning


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExecuteContext:
    """The part of synpp's ExecuteContext the stage uses."""

    def __init__(self, source, random_seed, sampling_rate):
        self._source = source
        self._config = {"random_seed": random_seed, "sampling_rate": sampling_rate}

    def config(self, name):
        return self._config[name]

    def stage(self, name):
        assert name == "source", name
        return self._source


def run_stage(module, source, random_seed, sampling_rate, numpy_warning_is_error):
    """Run ``module.execute`` once and return (output, seconds, warnings raised)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if numpy_warning_is_error:
            warnings.simplefilter("error", VisibleDeprecationWarning)
        started = time.perf_counter()
        output = module.execute(ExecuteContext(source, random_seed, sampling_rate))
        seconds = time.perf_counter() - started
    raised = [{"category": warning.category.__name__, "location": "%s:%d" % (os.path.basename(warning.filename), warning.lineno),
               "message": str(warning.message)[:110]} for warning in caught]
    return output, seconds, raised


def compare_frames(left, right):
    """Exact comparison: shape, columns, dtypes, index and the number of mismatching cells per column."""
    report = {
        "shape_left": list(left.shape),
        "shape_right": list(right.shape),
        "columns_equal": list(left.columns) == list(right.columns),
        "dtypes_equal": [str(t) for t in left.dtypes] == [str(t) for t in right.dtypes],
        "index_equal": bool(left.index.equals(right.index)),
    }
    mismatches = {}
    if report["columns_equal"] and report["shape_left"] == report["shape_right"]:
        for column in left.columns:
            a, b = left[column], right[column]
            equal = (a.to_numpy() == b.to_numpy()) | (pd.isna(a).to_numpy() & pd.isna(b).to_numpy())
            mismatches[column] = int((~equal).sum())
    report["mismatching_cells_per_column"] = mismatches
    report["identical"] = bool(
        report["columns_equal"] and report["dtypes_equal"] and report["index_equal"]
        and report["shape_left"] == report["shape_right"] and not any(mismatches.values()))
    return report


def describe_input(source):
    keys = source["household_id"].to_numpy()
    counts = source.groupby("household_id", sort=True).size()
    declared = source.groupby("household_id", sort=True)["household_size"].agg(["min", "max"])
    weights = source.drop_duplicates("household_id")["weight"]  # the stage reads one weight per household
    return {
        # Weight 1 gives every household exactly one replica whatever the random draw: the replication then maps
        # every row to itself, so a replay on such input says little about households replicated 0, 2 or more times.
        "household_weight_min": float(weights.min()),
        "household_weight_max": float(weights.max()),
        "households_with_weight_exactly_1": int((weights == 1.0).sum()),
        "households_with_a_whole_number_weight": int((weights == np.floor(weights)).sum()),
        "person_rows": int(len(source)),
        "households": int(len(counts)),
        "household_id_sorted_in_input": bool(source["household_id"].is_monotonic_increasing),
        "households_whose_household_size_differs_from_their_row_count": int((declared["min"] != counts).sum()),
        "households_with_inconsistent_household_size_values": int((declared["max"] != declared["min"]).sum()),
        "rows_placed_differently_by_the_unstable_default_sort_than_by_a_stable_sort": int(
            (np.argsort(keys, kind="quicksort") != np.argsort(keys, kind="stable")).sum()),
        "columns": int(source.shape[1]),
    }


def write_cache_entry(scratch, name, frame):
    """Write a stage result the way the fork does (braunschweig/synpp_fork/pipeline.py)."""
    os.makedirs(os.path.join(scratch, name + ".cache"), exist_ok=True)
    started = time.perf_counter()
    with open(os.path.join(scratch, name + ".p"), "wb+") as handle:
        pickle.dump(frame, handle, protocol=4)
    return time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--source", required=True, help="cache entry name of the parent stage, without .p")
    parser.add_argument("--output", required=True, help="cache entry name of the stage, without .p")
    parser.add_argument("--old-module", required=True)
    parser.add_argument("--new-module", required=True)
    parser.add_argument("--output-hash-module", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--sampling-rate", type=float, required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()

    results = {
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                        "platform": platform.platform()},
        "arguments": {"random_seed": args.seed, "sampling_rate": args.sampling_rate, "source": args.source,
                      "output": args.output},
        "files_sha256": {path: sha256_file(path) for path in
                         (args.old_module, args.new_module, args.output_hash_module, os.path.abspath(__file__))},
    }
    output_hash = load_module("output_hash", args.output_hash_module)
    os.makedirs(args.scratch, exist_ok=True)

    started = time.perf_counter()
    with open(os.path.join(args.cache_dir, args.source + ".p"), "rb") as handle:
        source = pickle.load(handle)
    results["source_load_seconds"] = round(time.perf_counter() - started, 1)
    results["input"] = describe_input(source)
    print("input:", json.dumps(results["input"]), flush=True)

    old_module = load_module("sampled_old", args.old_module)
    new_module = load_module("sampled_new", args.new_module)

    old_output, seconds, raised = run_stage(old_module, source, args.seed, args.sampling_rate, False)
    results["old_module_run"] = {"seconds": round(seconds, 1), "warnings": raised, "output_rows": int(len(old_output)),
                                 "output_households": int(old_output["household_id"].nunique())}
    print("old module:", json.dumps(results["old_module_run"]), flush=True)

    new_output, seconds, raised = run_stage(new_module, source, args.seed, args.sampling_rate, True)
    results["new_module_run_numpy_warning_is_error"] = {
        "seconds": round(seconds, 1), "warnings": raised, "output_rows": int(len(new_output)),
        "output_households": int(new_output["household_id"].nunique())}
    print("new module:", json.dumps(results["new_module_run_numpy_warning_is_error"]), flush=True)
    del source

    started = time.perf_counter()
    with open(os.path.join(args.cache_dir, args.output + ".p"), "rb") as handle:
        cached_output = pickle.load(handle)
    results["cached_output_load_seconds"] = round(time.perf_counter() - started, 1)
    results["comparison"] = {
        "old_module_vs_cached_run_output": compare_frames(old_output, cached_output),
        "new_module_vs_cached_run_output": compare_frames(new_output, cached_output),
        "new_module_vs_old_module": compare_frames(new_output, old_output),
    }
    for name, report in results["comparison"].items():
        print("compare", name, "identical =", report["identical"], json.dumps(report), flush=True)
    del cached_output

    with open(os.path.join(args.cache_dir, "pipeline.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    recorded = meta[args.output]["output_hash"]
    children = [name for name, entry in meta.items() if args.output in (entry.get("dependencies") or {})]
    recorded_digest_of_this_stage = {
        name: (meta[name].get("dependency_output_hashes") or {}).get(args.output) for name in children}
    results["recorded"] = {
        "output_hash": recorded,
        "entries_listing_the_stage_as_a_parent": len(children),
        "of_which_record_its_digest": sum(1 for value in recorded_digest_of_this_stage.values() if value),
        "of_which_record_exactly_the_digest_above": sum(
            1 for value in recorded_digest_of_this_stage.values() if value == recorded["digest"]),
    }
    results["cached_output_file_pickle_sha256"] = sha256_file(os.path.join(args.cache_dir, args.output + ".p"))

    digests = {}
    for label, frame in (("old_module", old_output), ("new_module", new_output)):
        name = "%s__%s" % (label, args.output)
        write_seconds = write_cache_entry(args.scratch, name, frame)
        started = time.perf_counter()
        digests[label] = output_hash.hash_stage_output(args.scratch, name)
        digests[label]["write_seconds"] = round(write_seconds, 1)
        digests[label]["hash_seconds"] = round(time.perf_counter() - started, 1)
        os.remove(os.path.join(args.scratch, name + ".p"))
        shutil.rmtree(os.path.join(args.scratch, name + ".cache"))
    results["replayed_output_hash"] = digests
    results["output_hash_equal_to_recorded"] = {
        label: digests[label]["digest"] == recorded["digest"] for label in digests}
    results["pickle_sha256_equal_to_recorded"] = {
        label: digests[label]["pickle"] == recorded["pickle"] for label in digests}
    print("recorded:", json.dumps(results["recorded"]), flush=True)
    print("replayed output hashes:", json.dumps(digests), flush=True)
    print("output hash equal to the one recorded by the run:", json.dumps(results["output_hash_equal_to_recorded"]),
          flush=True)

    with open(args.results, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
