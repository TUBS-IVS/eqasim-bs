"""Judge an input-consistency table with the stage guard's bounds (issue #658, ADR-0203).

Reads the table of run_input_consistency.py and reports, for the grid pairs (cars, bicycles, employed;
bound 1e-6) and the age pairs (persons_0_17, persons_18plus; bound 0.05 in Kreis parts with at least
1,000 persons of the universe, the production values of kreis_fine_tolerance / kreis_fine_min_persons),
the largest share, the number of rows above the bound, and the rows above it. The age residual is also
reported for the smaller parts and for persons_all (not gated by the stage), as the distribution the
0.05 bound is meant to sit above. Writes guard_summary.json and prints it.

Usage::

    python guard_summary.py <input_consistency.csv> <out.json> [--tolerance 0.05] [--min-persons 1000]
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

GRID_PAIRS = ("cars_0", "cars_1", "cars_2", "cars_3plus", "bicycles_0", "bicycles_1", "bicycles_2",
              "bicycles_3", "bicycles_4plus", "employed")
AGE_PAIRS = ("persons_0_17", "persons_18plus")


def summarize(table: pd.DataFrame, tolerance: float, min_persons: float) -> dict:
    kreis = table[table["scope"] == "kreis"]
    grid = kreis[kreis["check"].isin(GRID_PAIRS)]
    age = kreis[kreis["check"].isin(AGE_PAIRS)]
    gated = age[age["base"] >= min_persons]
    small = age[age["base"] < min_persons]
    quantiles = [0.5, 0.9, 0.99, 1.0]
    return {
        "batches": int(table["batch"].nunique()),
        "kreis_parts": int(kreis[["batch", "key"]].drop_duplicates().shape[0]),
        "grid_pairs": {"rows": int(len(grid)), "max_share": float(grid["share"].max()),
                       "rows_above_1e-6": int((grid["share"] > 1e-6).sum())},
        "age_pairs_gated": {
            "rows": int(len(gated)), "tolerance": tolerance, "min_persons": min_persons,
            "max_share": float(gated["share"].max()),
            "share_quantiles": {str(q): float(gated["share"].quantile(q)) for q in quantiles},
            "rows_above_tolerance": int((gated["share"] > tolerance).sum()),
            "above": gated[gated["share"] > tolerance][["batch", "key", "check", "amount", "base", "share"]]
            .to_dict("records")},
        "age_pairs_small_parts_reported": {
            "rows": int(len(small)), "max_share": float(small["share"].max()) if len(small) else None,
            "rows_above_tolerance": int((small["share"] > tolerance).sum())},
        "persons_all_reported": {
            "max_share": float(kreis.loc[kreis["check"] == "persons_all", "share"].max()),
            "max_share_parts_ge_min": float(kreis.loc[(kreis["check"] == "persons_all")
                                                      & (kreis["base"] >= min_persons), "share"].max())},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("table")
    parser.add_argument("out")
    parser.add_argument("--tolerance", type=float, default=0.05)
    parser.add_argument("--min-persons", type=float, default=1000.0)
    args = parser.parse_args(argv)
    table = pd.read_csv(args.table, dtype={"batch": str, "key": str})
    result = summarize(table, args.tolerance, args.min_persons)
    text = json.dumps(result, indent=2)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
