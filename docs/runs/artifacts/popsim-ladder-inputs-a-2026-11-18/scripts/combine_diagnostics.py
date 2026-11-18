"""Combine the diagnostics directories of one arm that were written in separate runs (issue #658, A8).

Arm 4 of batch_000 and batch_032 comes from popsim-ladder-tenure-2026-11-18, arm 4 of batch_013 and
batch_028 from the baseline run of this record; compare_input_arms.py reads one diagnostics directory
per arm. This script concatenates the per-batch tables the comparison reads (control_fit.csv,
household_conservation.csv, solver_paths.csv, rounding_shift.csv, input_consistency.csv) from every
source directory into ``--out``, refusing a batch that appears in two sources and a table whose
columns differ between sources. A table absent from a source is reported in combine_log.json and
left out for that source (the comparison then fails the check that needs it, it never passes it).

Usage::

    python combine_diagnostics.py --out <dir> <diag dir> [<diag dir> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

TABLES = ("control_fit.csv", "household_conservation.csv", "solver_paths.csv",
          "rounding_shift.csv", "input_consistency.csv")


def combine(sources: list, out: Path) -> dict:
    """Write the concatenated tables into ``out`` and return the log dict."""
    out.mkdir(parents=True, exist_ok=False)
    log = {"sources": [str(s) for s in sources], "tables": {}}
    for name in TABLES:
        frames, absent, columns = [], [], None
        for source in sources:
            path = source / name
            if not path.is_file():
                absent.append(str(source))
                continue
            frame = pd.read_csv(path, dtype={"batch": str, "key": str})
            # Rows without a batch are pooled over the source's own batches (e.g. control_fit.csv
            # scope "all"); pooled over a different batch set they would be wrong after the
            # concatenation, so they are dropped and counted. The comparison reads per-batch rows.
            pooled = frame["batch"].isna()
            if pooled.any():
                log.setdefault("dropped_pooled_rows", {}).setdefault(name, {})[str(source)] = int(pooled.sum())
                frame = frame[~pooled]
            if columns is None:
                columns = list(frame.columns)
            elif list(frame.columns) != columns:
                raise SystemExit(f"{name}: columns of {source} differ from the first source")
            frames.append(frame)
        if not frames:
            log["tables"][name] = {"absent_in": absent, "rows": 0}
            continue
        table = pd.concat(frames, ignore_index=True)
        batches_per_source = [set(f["batch"]) for f in frames]
        seen = set()
        for batches in batches_per_source:
            if seen & batches:
                raise SystemExit(f"{name}: batch(es) {sorted(seen & batches)} in two sources")
            seen |= batches
        table.to_csv(out / name, index=False)
        log["tables"][name] = {"absent_in": absent, "rows": int(len(table)), "batches": sorted(seen)}
    (out / "combine_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(combine(args.sources, args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
