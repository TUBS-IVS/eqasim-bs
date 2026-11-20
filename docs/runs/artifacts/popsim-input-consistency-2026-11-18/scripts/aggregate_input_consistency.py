"""Aggregate an ``input_consistency`` CSV of ``run_input_consistency.py`` to one row per check.

Sums ``cells``, ``amount`` and ``base`` over all batches (and Kreis parts) of a folder set,
per ``scope`` and ``key``. The sums are the figures the run manifest compares with the earlier
scratch measurements (issue #658, Task A2). ``ratio_reported`` rows carry a ratio, not a
contradiction: their ``amount`` (survey minus grid) is summed as an absolute value and labelled.

Usage::

    python aggregate_input_consistency.py <input_consistency.csv> <out.csv>
"""

from __future__ import annotations

import sys

import pandas as pd


def aggregate(table: pd.DataFrame) -> pd.DataFrame:
    """One row per ``set, scope, key, check``: number of rows, sums of cells, amount and base."""
    judged = table[table["check"] != "skipped"].copy()
    judged["abs_amount"] = judged["amount"].abs()
    grouped = judged.groupby(["set", "scope", "key", "check"], dropna=False).agg(
        rows=("amount", "size"),
        cells=("cells", "sum"),
        amount=("amount", "sum"),
        abs_amount=("abs_amount", "sum"),
        base=("base", "sum"),
    ).reset_index()
    skipped = table[table["check"] == "skipped"].groupby(["set", "key"]).size()
    if len(skipped):
        print("skipped (name: batches):", skipped.to_dict())
    return grouped


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    table = pd.read_csv(argv[0], dtype={"batch": str, "key": str})
    result = aggregate(table)
    result.to_csv(argv[1], index=False)
    with pd.option_context("display.max_rows", 200, "display.width", 200):
        print(result.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
