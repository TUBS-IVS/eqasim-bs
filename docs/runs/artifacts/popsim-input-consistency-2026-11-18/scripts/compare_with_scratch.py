"""Region totals of an ``input_consistency`` CSV next to the earlier scratch figures (#658, A2).

The scratch figures were measured on the run server on 2026-11-18 by an exploration script
that is NOT committed; they are written here as the values the plan of issue #658 quotes, so
that the committed measurement can be compared with them. Agreement is reported, a difference
is reported with its size; nothing is adjusted.

A second part explains the one known definitional difference: the committed check
``persons_below_size_minimum`` judges only cells that hold households AND persons (a cell with
households and no person is counted once, by ``households_without_persons``), whereas the
scratch count also included cells with households and no person. With ``--work-dir`` the script
re-reads the 100 m tables and reports the minimum persons the household sizes need in exactly
those cells (``sum k * n_k``, 6+ counted as 6), which is the expected difference.

Usage::

    python compare_with_scratch.py <input_consistency_prod100.csv> <out.csv> [--work-dir <dir>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from braunschweig.popsim import input_consistency as ic

# Figures quoted in the plan of issue #658 (Task A2), scratch measurement 2026-11-18.
SCRATCH = {
    "persons_below_size_minimum_amount": 106792,
    "persons_below_size_minimum_cells": 19341,
    "persons_without_households_amount": 20070,
    "persons_without_households_cells": 3387,
    "households_without_persons_amount": 3376,
    "households_without_persons_cells": 1057,
    "kreis_persons_0_17_abs_diff": 16371,
    "kreis_employed_abs_diff": 14418,
    "kreis_cars_abs_diff": 41082,
    "kreis_bicycles_abs_diff": 26420,
}


def committed_totals(table: pd.DataFrame) -> dict:
    """The committed counterparts of ``SCRATCH`` (sums over all batches and Kreis parts)."""
    cell = table[table["scope"] == "cell"]
    kreis = table[(table["scope"] == "kreis") & (table["check"] != "skipped")]
    out = {}
    for check in ("persons_below_size_minimum", "persons_without_households",
                  "households_without_persons"):
        rows = cell[cell["check"] == check]
        out[f"{check}_amount"] = float(rows["amount"].sum())
        out[f"{check}_cells"] = float(rows["cells"].sum())
    out["kreis_persons_0_17_abs_diff"] = float(kreis.loc[kreis["check"] == "persons_0_17", "amount"].abs().sum())
    out["kreis_employed_abs_diff"] = float(kreis.loc[kreis["check"] == "employed", "amount"].abs().sum())
    out["kreis_cars_abs_diff"] = float(kreis.loc[kreis["check"].str.startswith("cars_"), "amount"].abs().sum())
    out["kreis_bicycles_abs_diff"] = float(
        kreis.loc[kreis["check"].str.startswith("bicycles_"), "amount"].abs().sum())
    return out


def households_cells_without_persons(work_dir: Path) -> dict:
    """Minimum persons the household sizes need, in cells with households and no person."""
    cells = persons_min = 0.0
    for folder in sorted(work_dir.glob("batch_*")):
        table = pd.read_csv(folder / "data" / "control_totals_ZENSUS100m.csv")
        persons = table[list(ic.ALL_BAND_COLUMNS)].sum(axis=1)
        minimum = sum((k + 1) * table[c] for k, c in enumerate(ic.SIZE_COLUMNS))
        mask = (table[ic.HH_TOTAL_COLUMN] > 0) & (persons == 0)
        cells += float(mask.sum())
        persons_min += float(minimum[mask].sum())
    return {"cells": cells, "persons_min": persons_min}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("table")
    parser.add_argument("out")
    parser.add_argument("--work-dir")
    args = parser.parse_args(argv)
    table = pd.read_csv(args.table, dtype={"batch": str, "key": str})
    committed = committed_totals(table)
    rows = [{"figure": k, "scratch": v, "committed": committed[k], "difference": committed[k] - v}
            for k, v in SCRATCH.items()]
    result = pd.DataFrame(rows)
    result.to_csv(args.out, index=False)
    print(result.to_string(index=False))
    if args.work_dir:
        print("cells with households and no person:", households_cells_without_persons(Path(args.work_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
