"""Compare PopulationSim batch runs with the fork's run-time options off and on (issue #656).

Reads the arm folders that ``run_evidence.sh`` of ``popsim-ladder-arm3-2026-11-18`` wrote: for each rounding
mode (``round``, ``controlled``) the folders ``<rounding>_off`` and ``<rounding>_on`` (batch_000, batch_032)
and ``<rounding>_off_b028`` and ``<rounding>_on_b028`` (batch_028); "on" = ``STORE_POSITIVE_WEIGHT_ROWS_ONLY``
and ``FAST_PATHS`` both true, "off" = both false, everything else equal.

Checks, fixed in ``docs/runs/popsim-run-performance-2026-11-18.yml`` before the runs (``met`` in the verdict):

* identity: for every rounding mode and batch the sha256 of ``final_expanded_household_ids.csv``,
  ``synthetic_households.csv`` and ``synthetic_persons.csv`` is equal with the options off and on;
* the solver-path record ``integerizer_paths.csv`` is equal apart from its two timing columns;
* reference: with ``--reference <batch>=<sha256>`` the ``round`` runs of that batch (off and on) reproduce the
  given sha256 of ``final_expanded_household_ids.csv`` (the committed hash of ladder arm 2);
* every run exits 0 and every arm reports the expected package version and commit in ``tool.json``;
* with ``--diagnostics <dense>:<positive>``: the batch diagnostics computed from the checkpoint store of the
  run with the options off and from the store of the run with them on (same rounding, same batches) agree:
  text columns equal, numeric columns within 1e-9 absolute plus 1e-9 relative.

Reported without a bound, per rounding mode, batch and option state: wall time and peak RSS (GNU time), the
size of the checkpoint store (``<batch>.store_bytes`` of ``run_arm.sh``), the seconds per pipeline step
(``timing_log.csv``), the RSS at the end of every step and the largest RSS sample (``mem.csv``), and from
the log of the runs with the options on the rows the weight tables kept
(``[positive weight rows]`` lines) and the fast-path summary of the balancers (calls on the fast kernel and
every counted fallback).

Writes ``run_performance.csv`` (long table: rounding, batch, options, metric, value) and
``run_performance_verdict.json`` into ``<out_dir>`` and prints them.

Usage:
    python compare_run_performance.py <evidence_root> <out_dir> --expected-version 0.10.0+bs.2 \
        --expected-commit <sha> [--reference batch_000=<sha256> --reference batch_032=<sha256>] \
        [--diagnostics <root>/diag_controlled_off:<root>/diag_controlled_on]
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROUNDINGS = ("round", "controlled")
ARMS = (("", ("batch_000", "batch_032")), ("_b028", ("batch_028",)))
OUTPUT_FILES = ("final_expanded_household_ids.csv", "synthetic_households.csv", "synthetic_persons.csv")
TIMING_COLUMNS = ("build_s", "solve_s")
DIAGNOSTICS_TOLERANCE = 1e-9
ELAPSED_LINE = re.compile(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)")
RSS_LINE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")
KEPT_LINE = re.compile(r"\[positive weight rows\] (\S+): kept (\d+) of (\d+) rows")
FAST_LINE = re.compile(
    r"balancer fast path: fast (\d+) of (\d+) calls \([^)]*\), fallback value cap (\d+), "
    r"fallback SVML (\d+), fallback non-finite (\d+), re-run after the delta guard (\d+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def seconds_of(elapsed: str) -> float:
    """Seconds of a GNU time elapsed field (``h:mm:ss`` or ``m:ss.ss``)."""
    seconds = 0.0
    for part in elapsed.split(":"):
        seconds = seconds * 60.0 + float(part)
    return seconds


def record_without_timing(path: Path) -> list:
    """The rows of a solver-path record as dicts without the timing columns."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [{k: v for k, v in row.items() if k not in TIMING_COLUMNS} for row in csv.DictReader(handle)]


def compare_diagnostics(dense_dir: Path, positive_dir: Path, tolerance: float) -> dict:
    """Compare the CSV tables of two diagnostics directories of the same population.

    Text columns must be equal; numeric columns must agree within ``tolerance`` absolute plus
    ``tolerance`` relative (``numpy.isclose``): float sums over the rows of a weight table can differ in
    the last bits when the zero rows are not stored, and a value that is zero up to float noise has no
    meaningful relative difference. Returns the tables compared, the columns that differ beyond that, and
    the largest absolute difference seen.
    """
    import numpy as np
    import pandas as pd

    names = sorted({p.name for p in dense_dir.glob("*.csv")} | {p.name for p in positive_dir.glob("*.csv")})
    differing, largest = [], 0.0
    for name in names:
        if not (dense_dir / name).is_file() or not (positive_dir / name).is_file():
            differing.append(f"{name}: missing on one side")
            continue
        dense, positive = pd.read_csv(dense_dir / name), pd.read_csv(positive_dir / name)
        if list(dense.columns) != list(positive.columns) or len(dense) != len(positive):
            differing.append(f"{name}: shape or columns")
            continue
        for column in dense.columns:
            a, b = dense[column], positive[column]
            if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
                a, b = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
                if not np.array_equal(np.isnan(a), np.isnan(b)):
                    differing.append(f"{name}: {column} (missing values)")
                    continue
                difference = np.nan_to_num(np.abs(a - b), nan=0.0)
                largest = max(largest, float(difference.max()) if len(difference) else 0.0)
                if not np.isclose(a, b, rtol=tolerance, atol=tolerance, equal_nan=True).all():
                    differing.append(f"{name}: {column}")
            elif not a.astype(str).equals(b.astype(str)):
                differing.append(f"{name}: {column}")
    return {"tables": names, "differing": differing, "largest_absolute_difference": largest,
            "tolerance": tolerance, "met": bool(names) and not differing}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--reference", action="append", default=[], help="<batch>=<sha256 of the expanded ids>")
    parser.add_argument("--diagnostics", help="<diagnostics dir of a dense store>:<of a positive-row store>")
    args = parser.parse_args(argv)
    reference = dict(spec.split("=", 1) for spec in args.reference)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows, checks = [], {}
    if args.diagnostics:
        dense_dir, positive_dir = args.diagnostics.split(":", 1)
        checks["diagnostics_read_both_stores_alike"] = compare_diagnostics(
            Path(dense_dir), Path(positive_dir), DIAGNOSTICS_TOLERANCE)

    def add(rounding, batch, options, metric, value):
        rows.append({"rounding": rounding, "batch": batch, "options": options, "metric": metric,
                     "value": value})

    for rounding in ROUNDINGS:
        for suffix, batches in ARMS:
            arm = {options: args.root / f"{rounding}_{options}{suffix}" for options in ("off", "on")}
            for options, folder in arm.items():
                tool = json.loads((folder / "tool.json").read_text(encoding="utf-8"))
                checks[f"tool_{folder.name}"] = {
                    "tool": tool,
                    "met": tool.get("populationsim") == args.expected_version
                    and args.expected_commit in json.dumps(tool)}
            for batch in batches:
                digests = {}
                for options, folder in arm.items():
                    out = folder / batch / "output"
                    exit_text = (folder / f"{batch}.exit").read_text().strip()
                    checks[f"exit_{folder.name}_{batch}"] = {"exit": exit_text, "met": exit_text == "exit 0"}
                    digests[options] = {name: sha256(out / name) for name in OUTPUT_FILES}
                    time_text = (folder / f"{batch}.time").read_text(errors="replace")
                    add(rounding, batch, options, "wall_time_seconds",
                        seconds_of(ELAPSED_LINE.search(time_text).group(1)))
                    # GNU time reports kibibytes; GiB is the unit of the pipeline's memory gate
                    add(rounding, batch, options, "peak_rss_gib",
                        int(RSS_LINE.search(time_text).group(1)) / 2**20)
                    store_bytes = folder / f"{batch}.store_bytes"
                    if store_bytes.is_file() and store_bytes.read_text().strip():
                        add(rounding, batch, options, "checkpoint_store_gib",
                            int(store_bytes.read_text().strip()) / 2**30)
                    with (out / "timing_log.csv").open(newline="", encoding="utf-8") as handle:
                        for step in csv.DictReader(handle):
                            add(rounding, batch, options, f"seconds_{step['model_name']}", float(step["seconds"]))
                    largest = 0
                    with (out / "mem.csv").open(newline="", encoding="utf-8") as handle:
                        for sample in csv.DictReader(handle):
                            rss = int(sample["rss"].replace("_", ""))
                            largest = max(largest, rss)
                            event = sample["event"]
                            if event.startswith("pipeline.run_model ") and event.endswith(" finished"):
                                step = event[len("pipeline.run_model "):-len(" finished")]
                                add(rounding, batch, options, f"rss_gib_after_{step}", rss / 2**30)
                    add(rounding, batch, options, "largest_rss_sample_gib", largest / 2**30)
                    log_text = (out / "populationsim.log").read_text(errors="replace")
                    for table, kept, total in KEPT_LINE.findall(log_text):
                        add(rounding, batch, options, f"rows_kept_{table}", int(kept))
                        add(rounding, batch, options, f"rows_total_{table}", int(total))
                    fast = FAST_LINE.search(log_text)
                    if fast:
                        names = ("balancer_calls_fast", "balancer_calls", "balancer_fallback_value_cap",
                                 "balancer_fallback_svml", "balancer_fallback_non_finite",
                                 "balancer_rerun_after_delta_guard")
                        for name, value in zip(names, fast.groups()):
                            add(rounding, batch, options, name, int(value))
                    if options == "on":
                        # No silent fallback: the run with FAST_PATHS on must report its fast-path rate.
                        checks[f"fast_path_reported_{rounding}_{batch}"] = {
                            "summary_line_found": bool(fast), "met": bool(fast)}
                same_outputs = digests["off"] == digests["on"]
                checks[f"identity_{rounding}_{batch}"] = {"sha256_off": digests["off"],
                                                          "sha256_on": digests["on"], "met": same_outputs}
                records = [record_without_timing(arm[o] / batch / "output" / "integerizer_paths.csv")
                           for o in ("off", "on")]
                checks[f"path_record_{rounding}_{batch}"] = {"rows": len(records[0]),
                                                             "met": records[0] == records[1]}
                if rounding == "round" and batch in reference:
                    name = OUTPUT_FILES[0]
                    checks[f"reference_{batch}"] = {
                        "reference_sha256": reference[batch], "sha256_off": digests["off"][name],
                        "sha256_on": digests["on"][name],
                        "met": digests["off"][name] == reference[batch] == digests["on"][name]}

    with (args.out_dir / "run_performance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["rounding", "batch", "options", "metric", "value"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    verdict = {"checks": checks, "all_met": all(c["met"] for c in checks.values())}
    (args.out_dir / "run_performance_verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")

    table = {}
    for row in rows:
        table.setdefault((row["rounding"], row["batch"], row["metric"]), {})[row["options"]] = row["value"]
    for (rounding, batch, metric), values in sorted(table.items()):
        print("%-10s %-9s %-52s off %12s   on %12s" % (
            rounding, batch, metric, values.get("off", ""), values.get("on", "")))
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
