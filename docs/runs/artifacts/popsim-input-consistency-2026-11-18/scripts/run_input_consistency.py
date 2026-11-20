"""Measure the contradictions between the PopulationSim control tables of existing batch folders.

Baseline of issue #658 (Part A, Task A2). Table reads only: for every batch folder given, the
control tables ``data/control_totals_{KREIS,ZENSUS1km,ZENSUS100m}.csv`` and
``data/geo_cross_walk.csv`` are read and passed to
``braunschweig.popsim.input_consistency.summarize`` by the same reader the batch diagnostics
use (``braunschweig.analysis.popsim_batch_diagnostics.run._input_consistency``), so this
script and the diagnostics table ``input_consistency.csv`` measure the same thing. No
PopulationSim output is read and nothing is re-run.

Usage (from the root of an archive of the branch, in the ``eqasim`` environment)::

    python run_input_consistency.py --set prod100 --work-dir <popsim_work dir> \
        --out <dir>/input_consistency_prod100.csv
    python run_input_consistency.py --set arm3 --folder <batch folder> [--folder ...] \
        --out <dir>/input_consistency_arm3.csv

``--work-dir`` takes every ``batch_*`` folder under it (``batch_folder.list_batch_folders``).
The output CSV carries a ``set`` and a ``batch`` column in front of the ``summarize`` columns;
a JSON file next to it (same stem, ``.json``) records the folders read, the number of rows,
the batches that could not be measured and the code commit given with ``--commit``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics import batch_folder
from braunschweig.analysis.popsim_batch_diagnostics import run as diagnostics_run

logger = logging.getLogger("run_input_consistency")


def _folders(args: argparse.Namespace) -> list:
    """The batch folders to read, as ``(name, path)`` pairs, in a stable order."""
    folders = []
    if args.work_dir:
        for folder in batch_folder.list_batch_folders(args.work_dir):
            path = Path(getattr(folder, "path", folder))
            folders.append((path.name, path))
    for value in args.folder or []:
        path = Path(value)
        folders.append((path.name, path))
    if not folders:
        raise SystemExit("no batch folder given (--work-dir or --folder)")
    names = [name for name, _ in folders]
    if len(set(names)) != len(names):
        raise SystemExit(f"duplicate batch names: {sorted(names)}")
    return folders


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--set", required=True, help="label of the folder set, e.g. prod100 or arm3")
    parser.add_argument("--work-dir", help="directory holding batch_* folders")
    parser.add_argument("--folder", action="append", help="one batch folder (repeatable)")
    parser.add_argument("--out", required=True, help="output CSV path")
    parser.add_argument("--commit", default="unknown", help="eqasim-bs commit of the code used")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    frames, notes = [], []
    for name, path in _folders(args):
        table, note = diagnostics_run._input_consistency(SimpleNamespace(name=name, path=path))
        notes.append(note)
        if table is None:
            continue
        table = table.copy()
        table.insert(0, "batch", name)
        table.insert(0, "set", args.set)
        frames.append(table)
    diagnostics_run._log_input_consistency_notes(notes)
    if not frames:
        logger.error("no batch could be measured")
        return 1
    result = pd.concat(frames, ignore_index=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)
    meta = {
        "set": args.set,
        "commit": args.commit,
        "folders": [str(p) for _, p in _folders(args)],
        "rows": int(len(result)),
        "unmeasured": [n["batch"] for n in notes if not n["measured"]],
        "skipped": sorted({s for n in notes for s in n["skipped"]}),
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info("wrote %s (%d rows, %d batches)", out, len(result), len(frames))
    return 0


if __name__ == "__main__":
    sys.exit(main())
