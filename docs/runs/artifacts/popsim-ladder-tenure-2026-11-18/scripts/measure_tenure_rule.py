"""Measure what the tenure rule changes on ALL model cells and how sensitive that is (issue #657).

Purpose: the committed, region-wide measurement of ``tenure_targets.limit_owner_households_to_dwellings``
(the owner households of a cell are limited to its dwellings). The two ladder batches show the rule on two
batches; this shows it on every cell of the model, and how much the result depends on the dwelling count
and on the threshold ``min_households_outside``.

Model cells: the ``ZENSUS100m`` column of ``data/geo_cross_walk.csv`` of every ``batch_*`` folder under
``--work-dir`` (read only, listed with ``popsim_batch_diagnostics.batch_folder.list_batch_folders``). A cell
that is not in the prepared grid, or that is in two batches, ends the run with exit 1.

Scale: the figures are the FLOAT cell values BEFORE the integerisation within the 1 km squares (the rule's
own scale). The integer control tables of a batch differ from them by that rounding.

For each dwelling column and each threshold (5, 10, 20 households outside any dwelling) the library
function is called on the model cells (never re-implemented) and one row of ``rule_sensitivity.csv`` is
written. The dwelling columns are the rule's own (``control_spec.OWNER_QUOTE_DWELLING_COLUMN``, the harmonised
rooms table), the raw rooms table, and the occupied dwellings derived from the vacancy table; a candidate
that the grid lacks is skipped and named in the log and ``measure_log.json``. "Changed" in every column and
table means SELECTED BY THE RULE (households minus dwellings at least the threshold), whether or not a
count moved: the mask is ``tenure_targets.cells_outside_dwellings``, the selection the rule itself uses. If
its count differs from ``cells_changed`` of the rule's report, the run ends with exit 1.

Inputs: ``--cells-100m`` the prepared 100 m cell parquet, ``--work-dir`` a directory with ``batch_*`` folders.
Outputs in ``--out`` (must not exist): ``rule_sensitivity.csv``; for the configured rule (harmonised rooms
table, default threshold) ``rule_by_batch.csv`` and ``changed_cells.csv``; ``measure_log.json`` (arguments,
library commit, totals, skipped columns, sha256 of the three CSVs, or the error text). Exit 0 on success, 1 on
any failure (the log is written), 2 on unusable arguments. ``--library-commit`` defaults to the environment
variable EQASIM_BS_COMMIT; the script refuses to run without one.

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python measure_tenure_rule.py --cells-100m <prepared grid parquet> --work-dir <dir with batch_* folders> \
      --out <new dir> [--library-commit <sha>]
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics import batch_folder
from braunschweig.popsim import control_spec, mid, tenure_targets

CELL_ID = "ZENSUS100m"
OWNER_BASE = next(name for name, _ in control_spec._TIER2_TENURE_ENTRIES if name.startswith("Eigentuemer"))
RENTER_BASE = next(name for name, _ in control_spec._TIER2_TENURE_ENTRIES if name.startswith("Mieter"))
RULE_DWELLING_COLUMN = control_spec.OWNER_QUOTE_DWELLING_COLUMN
# Candidate dwelling counts, the rule's own first (it must exist); the other two are skipped when absent.
DWELLING_COLUMNS = (
    RULE_DWELLING_COLUMN,
    "Insgesamt_Wohnungen_Wohnungen_nach_Zahl_der_Raeume_100m_Gitter",  # rooms table, raw
    "BewohntWhg_Leerstand_100m_Gitter",  # occupied dwellings derived from the vacancy table
)
CONFIGURED_THRESHOLD = tenure_targets.DEFAULT_MIN_HOUSEHOLDS_OUTSIDE
THRESHOLDS = (5.0, CONFIGURED_THRESHOLD, 20.0)
# A changed cell is "high owner" if its owner count before the rule is at least this share of its households.
HIGH_OWNER_SHARE = 0.9
EXIT_FAILURE = 1
EXIT_BAD_ARGUMENTS = 2


def counts(series: pd.Series) -> pd.Series:
    """A count column as float, NaN (Zensus suppression) read as 0, as the rule reads it."""
    return pd.to_numeric(series, errors="coerce").fillna(0.0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_cells(work_dir: Path) -> pd.Series:
    """``cell id -> batch name`` of every ``batch_*`` folder under ``work_dir`` (read only)."""
    folders = batch_folder.list_batch_folders(work_dir)
    if not folders:
        raise ValueError(f"no batch_* folder under {work_dir}")
    owner = {}
    for folder in folders:
        table = pd.read_csv(folder / "data" / "geo_cross_walk.csv", dtype=str, keep_default_na=False,
                            usecols=[CELL_ID])
        for cell in table[CELL_ID]:
            if cell in owner:
                raise ValueError(f"cell {cell} is in two batches ({owner[cell]} and {folder.name})")
            owner[cell] = folder.name
    return pd.Series(owner, name="batch")


def load_model_cells(cells_path: Path, batches: pd.Series):
    """The grid rows of the model cells, with a ``batch`` column, and the skipped candidate columns."""
    requested = [control_spec.HH_TOTAL_CENSUS_COLUMN, OWNER_BASE, RENTER_BASE, *DWELLING_COLUMNS]
    cells = mid.load_control_cells(cells_path, requested)
    required = [control_spec.HH_TOTAL_CENSUS_COLUMN, OWNER_BASE, RENTER_BASE, RULE_DWELLING_COLUMN]
    absent = [c for c in required if c not in cells.columns]
    if absent:
        raise ValueError(f"the prepared grid {cells_path} has no column(s) {absent}")
    skipped = [c for c in DWELLING_COLUMNS if c not in cells.columns]
    if cells[CELL_ID].duplicated().any():
        raise ValueError(f"the prepared grid {cells_path} holds a cell id more than once")
    cells = cells[cells[CELL_ID].isin(set(batches.index))].reset_index(drop=True)
    missing = sorted(set(batches.index) - set(cells[CELL_ID]))
    if missing:
        raise ValueError(f"{len(missing)} of {len(batches)} model cells are not in the prepared grid "
                         f"{cells_path} (first: {missing[0]})")
    cells["batch"] = cells[CELL_ID].map(batches)
    return cells, skipped


def measure_one(cells: pd.DataFrame, dwelling_column: str, threshold: float):
    """Run the library rule once; return ``(row, result_frame, changed_mask, report)``.

    ``changed_mask`` is the rule's own selection; ``ValueError`` if its count is not the report's.
    """
    result, report = tenure_targets.limit_owner_households_to_dwellings(
        cells, owner_col=OWNER_BASE, renter_col=RENTER_BASE,
        household_total_col=control_spec.HH_TOTAL_CENSUS_COLUMN, dwelling_col=dwelling_column,
        min_households_outside=threshold)
    households = counts(cells[control_spec.HH_TOTAL_CENSUS_COLUMN])
    dwellings = counts(cells[dwelling_column])
    owner_before, owner_after = counts(cells[OWNER_BASE]), counts(result[OWNER_BASE])
    renter_before, renter_after = counts(cells[RENTER_BASE]), counts(result[RENTER_BASE])
    changed = tenure_targets.cells_outside_dwellings(
        cells, household_total_col=control_spec.HH_TOTAL_CENSUS_COLUMN, dwelling_col=dwelling_column,
        min_households_outside=threshold)
    if int(changed.sum()) != report["cells_changed"]:
        raise ValueError(f"{dwelling_column}@{threshold}: the selection has {int(changed.sum())} cells, "
                         f"the rule reported {report['cells_changed']}")
    high = changed & (owner_before >= HIGH_OWNER_SHARE * households)
    total_households = float(households.sum())
    row = {
        "dwelling_column": dwelling_column, "min_households_outside": threshold,
        "is_configured_rule": dwelling_column == RULE_DWELLING_COLUMN and threshold == CONFIGURED_THRESHOLD,
        "cells_seen": report["cells_seen"], "cells_changed": report["cells_changed"],
        "households_in_changed_cells": float(households[changed].sum()),
        "households_outside_dwellings": float((households - dwellings)[changed].sum()),
        "households_moved_owner_to_renter": report["households_moved_owner_to_renter"],
        "moved_share_of_all_households": (report["households_moved_owner_to_renter"] / total_households
                                          if total_households else float("nan")),
        "high_owner_cells_changed": int(high.sum()),
        "households_in_high_owner_cells_changed": float(households[high].sum()),
        "batches_touched": int(cells.loc[changed, "batch"].nunique()),
        "cells_with_nan_dwelling_count": report["cells_with_nan_dwelling_count"],
        "cells_with_nan_tenure_count": report["cells_with_nan_tenure_count"],
        "cells_with_owner_above_household_total": report["cells_with_owner_above_household_total"],
    }
    return row, result, changed, report


def by_batch_table(cells: pd.DataFrame, result: pd.DataFrame, changed: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({
        "batch": cells["batch"], "cells": 1, "cells_changed": changed.astype(int),
        "households": counts(cells[control_spec.HH_TOTAL_CENSUS_COLUMN]),
        "owner_before": counts(cells[OWNER_BASE]), "owner_after": counts(result[OWNER_BASE])})
    table = frame.groupby("batch", sort=True).sum().reset_index()
    table["households_moved"] = table["owner_before"] - table["owner_after"]
    return table


def changed_cells_table(cells: pd.DataFrame, result: pd.DataFrame, changed: pd.Series) -> pd.DataFrame:
    frame = pd.DataFrame({
        "cell": cells[CELL_ID], "batch": cells["batch"],
        "households": counts(cells[control_spec.HH_TOTAL_CENSUS_COLUMN]),
        "dwellings": counts(cells[RULE_DWELLING_COLUMN]),
        "owner_before": counts(cells[OWNER_BASE]), "owner_after": counts(result[OWNER_BASE]),
        "renter_before": counts(cells[RENTER_BASE]), "renter_after": counts(result[RENTER_BASE])})
    return frame[changed].sort_values("cell").reset_index(drop=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cells-100m", required=True, type=Path, help="prepared 100 m cell parquet")
    parser.add_argument("--work-dir", required=True, type=Path, help="directory holding batch_* folders")
    parser.add_argument("--out", required=True, type=Path, help="new output directory (must not exist)")
    parser.add_argument("--library-commit", default=os.environ.get("EQASIM_BS_COMMIT"))
    args = parser.parse_args(argv)
    if not args.library_commit:
        print("refuse: no library commit (--library-commit or EQASIM_BS_COMMIT)", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    if args.out.exists():
        print(f"refuse: {args.out} exists", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS

    args.out.mkdir(parents=True)
    log = {"arguments": {"cells_100m": str(args.cells_100m), "work_dir": str(args.work_dir),
                         "out": str(args.out)},
           "library_commit": args.library_commit, "scale": "float cell values before the integerisation",
           "error": None}
    try:
        batches = model_cells(args.work_dir)
        cells, skipped = load_model_cells(args.cells_100m, batches)
        used = [c for c in DWELLING_COLUMNS if c not in skipped]
        rows, configured = [], None
        for column in used:
            for threshold in THRESHOLDS:
                row, result, changed, report = measure_one(cells, column, threshold)
                rows.append(row)
                if row["is_configured_rule"]:
                    configured = (result, changed, report)
        paths = {"rule_sensitivity.csv": args.out / "rule_sensitivity.csv",
                 "rule_by_batch.csv": args.out / "rule_by_batch.csv",
                 "changed_cells.csv": args.out / "changed_cells.csv"}
        pd.DataFrame(rows).to_csv(paths["rule_sensitivity.csv"], index=False)
        result, changed, report = configured
        by_batch_table(cells, result, changed).to_csv(paths["rule_by_batch.csv"], index=False)
        changed_cells_table(cells, result, changed).to_csv(paths["changed_cells.csv"], index=False)
        log.update({
            "batches": sorted(set(batches)), "skipped_dwelling_columns": skipped,
            "totals": {"cells": len(cells),
                       "households": float(counts(cells[control_spec.HH_TOTAL_CENSUS_COLUMN]).sum()),
                       "owner_households_before": float(counts(cells[OWNER_BASE]).sum()),
                       "owner_households_after_configured_rule": float(counts(result[OWNER_BASE]).sum()),
                       "dwellings_by_column": {c: float(counts(cells[c]).sum()) for c in used}},
            "configured_rule_report": report,
            "files_sha256": {name: sha256(path) for name, path in paths.items()}})
    except Exception as error:  # noqa: BLE001 - recorded in the log, then exit 1
        log["error"] = f"{type(error).__name__}: {error}"
    (args.out / "measure_log.json").write_text(json.dumps(log, indent=2, default=float), encoding="utf-8")
    print(json.dumps({k: log.get(k) for k in ("error", "skipped_dwelling_columns", "totals")}, indent=2,
                     default=float))
    return EXIT_FAILURE if log["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
