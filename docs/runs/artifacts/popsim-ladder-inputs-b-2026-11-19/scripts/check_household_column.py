"""Post hoc check of the arm-8 cell check (issue #659, whole-branch review finding 2).

``prepare_household_arm.py`` judges the move (``household_cells_check``) on the INTEGER household
column it rebuilds from the prepared 100 m parquet. PopulationSim reads the household column of the
folder's ``data/control_totals_ZENSUS100m.csv``. This script proves, per batch folder, that the two
are equal cell for cell, so the donors of the move are exactly the cells PopulationSim sees without
households (its empty sub-zones). It also counts, on the FOLDER's column, the cells with 0
households that still carry a person target, split by whether their 1 km square has a household
cell. Reads tables only; writes one JSON to ``--out``.

Usage::

    python check_household_column.py --cells-100m <prepared parquet> --kreise <comma list>
        --out <json> <arm-8 batch folder>...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
import prepare_household_arm as driver  # noqa: E402  (the driver's own loaders, not copies)
from braunschweig.popsim import control_spec as cs  # noqa: E402
from braunschweig.popsim import mid  # noqa: E402


def check_folder(folder: Path, cells: pd.DataFrame) -> dict:
    """Rebuilt vs folder household column, and household-less cells with person targets."""
    t100 = pd.read_csv(folder / "data" / "control_totals_ZENSUS100m.csv", dtype={driver.CELL_ID: str})
    crosswalk = pd.read_csv(folder / "data" / "geo_cross_walk.csv", dtype=str)
    column = f"{cs.HH_TOTAL_CENSUS_COLUMN}{mid.SUFFIX_100M}"
    batch = driver.batch_cells_in_loader_order(cells, t100[driver.CELL_ID])
    rebuilt = mid.build_control_totals(batch, crosswalk, [cs.HH_TOTAL_CENSUS_COLUMN])[driver.CELL_ID]
    rebuilt = rebuilt.set_index(driver.CELL_ID)[column].reindex(t100[driver.CELL_ID]).to_numpy()
    folder_hh = pd.to_numeric(t100[column]).to_numpy()
    mismatch = int((rebuilt != folder_hh).sum())
    age_cols = [c for c in t100.columns if "_AGE_" in c and c.endswith(mid.SUFFIX_100M)]
    persons = t100[age_cols].apply(pd.to_numeric).sum(axis=1).to_numpy()
    parent = t100[driver.CELL_ID].map(crosswalk.set_index(driver.CELL_ID)[driver.PARENT_ID])
    square_households = pd.Series(folder_hh).groupby(parent.to_numpy()).transform("sum").to_numpy()
    with_persons = (folder_hh == 0) & (persons > 0)
    return {
        "cells": int(len(t100)),
        "household_column": column,
        "cells_rebuilt_differs_from_folder": mismatch,
        "household_less_cells_with_person_target": int(with_persons.sum()),
        "of_which_in_squares_without_households": int((with_persons & (square_households == 0)).sum()),
        "of_which_with_20_or_more_persons": int((with_persons & (square_households > 0) & (persons >= 20)).sum()),
        "of_which_below_20_in_squares_with_households": int(
            (with_persons & (square_households > 0) & (persons < 20)).sum()),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cells-100m", required=True, type=Path)
    parser.add_argument("--kreise", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("folders", nargs="+", type=Path)
    args = parser.parse_args(argv)
    cells, _ = driver.load_kreis_cells(args.cells_100m, args.kreise.split(","), [cs.HH_TOTAL_CENSUS_COLUMN])
    result = {f"{folder.parent.name}/{folder.name}": check_folder(folder, cells) for folder in args.folders}
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    bad = [name for name, r in result.items() if r["cells_rebuilt_differs_from_folder"]]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
