"""Locate where inside a batch the household conservation deviation arises (issue #658, post hoc).

``conservation.household_conservation`` gives one number per batch and level pair: per household group
(``H_ID``) and 1 km parent zone, ``|sum of the 100 m integer weights - the 1 km integer weight|``, summed
and divided by the parent weight. This script splits that sum by 1 km parent (``ZENSUS1km``) for the
pair ``ZENSUS1km`` -> ``ZENSUS100m`` and puts next to each parent its input facts, so that the arms of
the input-consistency ladder can be compared parent by parent: where did the deviation of arm B rise
above that of arm A, and what is special about those parents?

Per parent and arm (``parents_<label>.csv``):
- ``n_pairs``, ``parent_weight_sum``, ``abs_deviation_sum``, ``conserved_pairs``, ``conserved_pair_share``:
  the terms of ``conservation._pair_conservation``, restricted to the pairs of this parent (the pair
  ``(H_ID, parent)`` belongs to exactly one parent, so the parent rows add up to the batch row);
- ``KREIS``: the parent's Kreis (crosswalk);
- ``cells_100m`` (crosswalk), ``hh_target_100m`` (sum of ``input_consistency.HH_TOTAL_COLUMN`` over the
  cells), ``persons_target_100m`` (sum of the age x sex bands ``input_consistency.ALL_BAND_COLUMNS``),
  ``hh_target_1km`` (the same household total on the 1 km table), ``hh_target_100m_minus_1km``;
- ``cells_hh_no_persons`` / ``cells_persons_no_hh``: cells with households but no persons / persons but no
  households, as ``input_consistency.cell_contradictions`` defines ``households_without_persons`` and
  ``persons_without_households`` (cells with a missing value in either total are not judged).

The script stops if the parent rows do not add up to the batch row of ``household_conservation`` (pairs
exactly, weight sums and deviation within 1e-9): the split would then be another quantity.

Reading, crosswalk and the parent map are those of ``conservation.py`` (``fit._read_weights``,
``conservation._crosswalk``, ``conservation._zone_to_parent``); the weight rows are read once for the split
and once inside ``household_conservation``.

Outputs in ``--out`` (must not exist): ``parents_<label>.csv`` per arm, ``parents_compare.csv`` (outer
join on the parent; the deviation per arm and, for every arm but the first, its difference to the first
arm; sorted by the difference of the SECOND label, largest increase first), and ``summary.json``.
Attributes of ``parents_compare.csv`` come from the first arm that has the parent; ``summary.json``
records whether the other arms' attributes are equal (they should be: the ladder arms differ only in
the Kreis table).

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python locate_conservation.py --batch-dir <arm A batch folder> [--batch-dir <arm B batch folder> ...] \
      --labels <A> [<B> ...] --out <new dir> [--chunk-rows 5000000]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics import batch_folder, conservation, fit
from braunschweig.popsim import input_consistency as ic

logger = logging.getLogger("locate_conservation")

LEVEL = "ZENSUS100m"
PARENT = "ZENSUS1km"
KREIS = "KREIS"
HH_TOTAL_1KM = ic.HH_TOTAL_COLUMN[:-len(ic.SUFFIX_100M)] + ic.SUFFIX_1KM
# Tolerance for "the parent rows add up to the batch row" (all terms are sums of whole numbers).
SUM_TOLERANCE = 1e-9
PAIR_COLUMNS = ["n_pairs", "parent_weight_sum", "abs_deviation_sum", "conserved_pairs",
                "conserved_pair_share"]
ATTRIBUTE_COLUMNS = [KREIS, "cells_100m", "hh_target_100m", "persons_target_100m", "hh_target_1km",
                     "hh_target_100m_minus_1km", "cells_hh_no_persons", "cells_persons_no_hh"]
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
TOP_PARENTS = 10
TOP_SHARES = (1, 3, 10)


def split_by_parent(parent_weights: pd.DataFrame, sub_weights: pd.DataFrame, parent_of: pd.Series,
                    parent: str, level: str) -> pd.DataFrame:
    """The household conservation of ``conservation._pair_conservation``, split by parent zone.

    ``parent_weights`` (columns ``H_ID``, ``parent``, ``integer_weight``) and ``sub_weights`` (columns
    ``H_ID``, ``level``, ``integer_weight``) are weight rows; ``parent_of`` maps a sub zone id (text) to
    its parent zone id (text). Rows with an integer weight of 0 do not take part. Returns one row per
    parent zone that has a pair: ``n_pairs``, ``parent_weight_sum``, ``abs_deviation_sum``,
    ``conserved_pairs`` and ``conserved_pair_share``.
    """
    parent_weights = parent_weights[parent_weights["integer_weight"] > 0]
    sub_weights = sub_weights[sub_weights["integer_weight"] > 0].copy()
    mapped = sub_weights[level].astype(str).map(parent_of)
    unmapped = mapped.isna()
    if unmapped.any():
        raise ValueError(f"{level}: {int(sub_weights.loc[unmapped, level].nunique())} zone(s) with a "
                         f"positive integer weight have no {parent} parent in the crosswalk, e.g. "
                         f"{sub_weights.loc[unmapped, level].unique()[:3].tolist()}")
    sub_weights[parent] = mapped
    sub_sum = sub_weights.groupby(["H_ID", parent])["integer_weight"].sum()
    parent_weights = parent_weights.assign(**{parent: parent_weights[parent].astype(str)})
    parent_sum = parent_weights.groupby(["H_ID", parent])["integer_weight"].sum()

    pairs = pd.concat([parent_sum.rename("parent_sum"), sub_sum.rename("sub_sum")],
                      axis=1).fillna(0.0)
    pairs["deviation"] = (pairs["sub_sum"] - pairs["parent_sum"]).abs()
    pairs["conserved"] = (pairs["deviation"] == 0).astype(int)
    grouped = pairs.reset_index().groupby(parent)
    table = pd.DataFrame({
        "n_pairs": grouped.size().astype("int64"),
        "parent_weight_sum": grouped["parent_sum"].sum().astype(float),
        "abs_deviation_sum": grouped["deviation"].sum().astype(float),
        "conserved_pairs": grouped["conserved"].sum().astype("int64")})
    table["conserved_pair_share"] = table["conserved_pairs"] / table["n_pairs"]
    table.index.name = parent
    return table


def check_against_batch_row(table: pd.DataFrame, row: pd.Series) -> dict:
    """Stop unless the parent rows add up to the ``household_conservation`` row of the batch."""
    n_pairs = int(table["n_pairs"].sum())
    conserved = int(table["conserved_pairs"].sum())
    checks = {
        "n_pairs": (float(n_pairs), float(row["n_pairs"]), 0.0),
        "parent_weight_sum": (float(table["parent_weight_sum"].sum()), float(row["parent_weight_sum"]),
                              SUM_TOLERANCE),
        "abs_deviation_sum": (float(table["abs_deviation_sum"].sum()), float(row["abs_deviation_sum"]),
                              SUM_TOLERANCE),
        "conserved_pair_share": (conserved / n_pairs if n_pairs else float("nan"),
                                 float(row["conserved_pair_share"]), SUM_TOLERANCE)}
    report = {}
    for name, (split, batch_value, tolerance) in checks.items():
        equal = (math.isnan(split) and math.isnan(batch_value)) or abs(split - batch_value) <= tolerance
        report[name] = {"sum_of_parents": split, "batch_row": batch_value, "equal": bool(equal)}
        if not equal:
            raise ValueError(f"the parent rows do not add up to the household_conservation row: {name} "
                             f"{split!r} (sum of parents) vs {batch_value!r} (batch row)")
    return report


def _read_ids_as_text(path: Path, zone: str) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"missing {path}")
    table = pd.read_csv(path, dtype={zone: str})
    if zone not in table.columns:
        raise ValueError(f"{path} has no column {zone}")
    if table[zone].duplicated().any():
        raise ValueError(f"{path}: zone ids of {zone} are not unique")
    return table.set_index(zone)


def parent_attributes(data_dir: Path, crosswalk: pd.DataFrame, source: str) -> pd.DataFrame:
    """Per 1 km parent: Kreis, cell counts and the input facts of ``data/control_totals_*.csv``."""
    cell_parent = conservation._zone_to_parent(crosswalk, LEVEL, PARENT, source)
    parent_kreis = conservation._zone_to_parent(crosswalk, PARENT, KREIS, source)
    cells = _read_ids_as_text(data_dir / f"control_totals_{LEVEL}.csv", LEVEL)
    parents_1km = _read_ids_as_text(data_dir / f"control_totals_{PARENT}.csv", PARENT)
    needed = [ic.HH_TOTAL_COLUMN, *ic.ALL_BAND_COLUMNS]
    missing = [c for c in needed if c not in cells.columns]
    if missing:
        raise ValueError(f"{data_dir / f'control_totals_{LEVEL}.csv'} lacks column(s) {missing[:3]}"
                         f"{' ...' if len(missing) > 3 else ''}")
    if HH_TOTAL_1KM not in parents_1km.columns:
        raise ValueError(f"{data_dir / f'control_totals_{PARENT}.csv'} lacks the household total "
                         f"column {HH_TOTAL_1KM}")
    outside = cells.index.difference(cell_parent.index)
    if len(outside):
        raise ValueError(f"{len(outside)} cell(s) of control_totals_{LEVEL}.csv are not in the "
                         f"crosswalk, e.g. {outside[:3].tolist()}")

    households = cells[ic.HH_TOTAL_COLUMN]
    persons = cells[list(ic.ALL_BAND_COLUMNS)].sum(axis=1, skipna=False)
    judged = households.notna() & persons.notna()
    flags = pd.DataFrame({
        PARENT: cell_parent.reindex(cells.index).to_numpy(),
        "hh": households.to_numpy(), "persons": persons.to_numpy(),
        "hh_no_persons": (judged & (households > 0) & (persons <= 0)).to_numpy(),
        "persons_no_hh": (judged & (persons > 0) & (households <= 0)).to_numpy()})
    grouped = flags.groupby(PARENT)
    out = pd.DataFrame(index=pd.Index(sorted(set(cell_parent)), name=PARENT))
    out[KREIS] = parent_kreis.reindex(out.index)
    if out[KREIS].isna().any():
        raise ValueError(f"{int(out[KREIS].isna().sum())} {PARENT} parent(s) have no {KREIS} in the "
                         "crosswalk")
    out["cells_100m"] = cell_parent.groupby(cell_parent).size().reindex(out.index).astype("int64")
    out["hh_target_100m"] = grouped["hh"].sum().reindex(out.index).fillna(0.0)
    out["persons_target_100m"] = grouped["persons"].sum().reindex(out.index).fillna(0.0)
    out["hh_target_1km"] = parents_1km[HH_TOTAL_1KM].reindex(out.index)
    out["hh_target_100m_minus_1km"] = out["hh_target_100m"] - out["hh_target_1km"]
    out["cells_hh_no_persons"] = grouped["hh_no_persons"].sum().reindex(out.index).fillna(0).astype("int64")
    out["cells_persons_no_hh"] = grouped["persons_no_hh"].sum().reindex(out.index).fillna(0).astype("int64")
    return out[ATTRIBUTE_COLUMNS]


def build_parents(path: Path, chunk_rows: int) -> tuple:
    """Return ``(parents table, batch row of household_conservation, check report)`` of one batch."""
    batch = batch_folder.load_batch_folder(path)
    if not batch.has_store:
        raise FileNotFoundError(f"{path} has no checkpoint store (output/pipeline.h5)")
    with pd.HDFStore(str(path / fit._STORE), mode="r") as store:
        crosswalk, source = conservation._crosswalk(store, batch)
        parent_weights, _ = fit._read_weights(store, PARENT, chunk_rows)
        sub_weights, _ = fit._read_weights(store, LEVEL, chunk_rows)
    parent_of = conservation._zone_to_parent(crosswalk, LEVEL, PARENT, source)
    split = split_by_parent(parent_weights, sub_weights, parent_of, PARENT, LEVEL)
    del parent_weights, sub_weights

    table = conservation.household_conservation(batch, levels=(PARENT, LEVEL), chunk_rows=chunk_rows)
    rows = table[table["level"] == LEVEL]
    if len(rows) != 1:
        raise ValueError(f"{path}: household_conservation gave {len(rows)} row(s) for {LEVEL}")
    row = rows.iloc[0]
    checks = check_against_batch_row(split, row)

    attributes = parent_attributes(path / "data", crosswalk, source)
    outside = split.index.difference(attributes.index)
    if len(outside):
        raise ValueError(f"{path}: {len(outside)} {PARENT} parent(s) of the weights are not in the "
                         f"crosswalk, e.g. {outside[:3].tolist()}")
    split = split.reindex(attributes.index)
    for column in ("n_pairs", "conserved_pairs"):
        split[column] = split[column].fillna(0).astype("int64")
    split[["parent_weight_sum", "abs_deviation_sum"]] = split[["parent_weight_sum",
                                                               "abs_deviation_sum"]].fillna(0.0)
    parents = pd.concat([split[PAIR_COLUMNS], attributes], axis=1)
    parents.index.name = PARENT
    logger.info("[locate_conservation] %s: %d parents, %d with a pair, deviation sum %.0f", path,
                len(parents), int((parents["n_pairs"] > 0).sum()), parents["abs_deviation_sum"].sum())
    return parents, row, checks


def compare_arms(labels: list, tables: dict) -> tuple:
    """Outer join of the arms on the parent; returns ``(compare table, attributes_equal report)``."""
    first = labels[0]
    union = sorted(set().union(*(set(t.index) for t in tables.values())))
    attributes = tables[first][ATTRIBUTE_COLUMNS].reindex(union)
    for label in labels[1:]:
        attributes = attributes.combine_first(tables[label][ATTRIBUTE_COLUMNS].reindex(union))
    equal = {}
    for label in labels[1:]:
        common = tables[first].index.intersection(tables[label].index)
        same = True
        for column in ATTRIBUTE_COLUMNS:
            x, y = tables[first].loc[common, column], tables[label].loc[common, column]
            same = same and bool(((x == y) | (x.isna() & y.isna())).all())
        equal[label] = same
        if not same:
            logger.warning("[locate_conservation] attributes of %s differ from those of %s; the compare "
                           "table uses those of the first arm that has the parent", label, first)
    compare = attributes.copy()
    compare.index.name = PARENT
    for label in labels:
        part = tables[label][["n_pairs", "abs_deviation_sum", "conserved_pair_share"]].reindex(union)
        compare[f"n_pairs_{label}"] = part["n_pairs"].fillna(0).astype("int64")
        compare[f"abs_deviation_sum_{label}"] = part["abs_deviation_sum"].fillna(0.0)
        compare[f"conserved_pair_share_{label}"] = part["conserved_pair_share"]
    for label in labels[1:]:
        compare[f"abs_deviation_diff_{label}_minus_{first}"] = (
            compare[f"abs_deviation_sum_{label}"] - compare[f"abs_deviation_sum_{first}"])
    if len(labels) > 1:
        sort_column = f"abs_deviation_diff_{labels[1]}_minus_{first}"
        compare = compare.reset_index().sort_values([sort_column, PARENT], ascending=[False, True],
                                                    kind="mergesort", ignore_index=True)
    else:
        compare = compare.reset_index()
    return compare, equal


def _jsonable(value):
    """Native Python values for json (numpy scalars, NaN -> null)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def comparison_summary(compare: pd.DataFrame, label: str, first: str) -> dict:
    """How concentrated the change of ``label`` against ``first`` is over the parents."""
    diff = compare[f"abs_deviation_diff_{label}_minus_{first}"]
    positive = diff.clip(lower=0.0)
    net, positive_total = float(diff.sum()), float(positive.sum())
    ordered = diff.sort_values(ascending=False, kind="mergesort")
    top = {}
    for k in TOP_SHARES:
        carried = float(ordered.head(k).sum())
        top[f"top_{k}"] = {
            "increase": carried,
            "share_of_net_increase": carried / net if net > 0 else None,
            "share_of_sum_of_increases": carried / positive_total if positive_total > 0 else None}
    parents_for_half = None
    if positive_total > 0:
        share = ordered.clip(lower=0.0).cumsum() / positive_total
        parents_for_half = int(np.searchsorted(share.to_numpy(), 0.5) + 1)
    columns = [PARENT, *ATTRIBUTE_COLUMNS, f"abs_deviation_sum_{first}", f"abs_deviation_sum_{label}",
               f"abs_deviation_diff_{label}_minus_{first}", f"n_pairs_{first}", f"n_pairs_{label}"]
    top_rows = compare.loc[ordered.index[:TOP_PARENTS], columns].to_dict("records")
    return {
        "arm": label, "against": first, "net_increase_of_abs_deviation_sum": net,
        "sum_of_increases": positive_total, "sum_of_decreases": float(diff.clip(upper=0.0).sum()),
        "parents_with_increase": int((diff > 0).sum()), "parents_with_decrease": int((diff < 0).sum()),
        "parents_unchanged": int((diff == 0).sum()),
        "parents_needed_for_half_of_the_sum_of_increases": parents_for_half,
        "carried_by_top_parents": top, f"top_{TOP_PARENTS}_parents_by_increase": top_rows}


def build_summary(labels: list, paths: list, tables: dict, rows: dict, checks: dict, compare: pd.DataFrame,
                  attributes_equal: dict) -> dict:
    arms = {}
    for label, path in zip(labels, paths):
        t = tables[label]
        n_pairs, parent_sum = int(t["n_pairs"].sum()), float(t["parent_weight_sum"].sum())
        deviation = float(t["abs_deviation_sum"].sum())
        arms[label] = {
            "batch_dir": str(path), "parents": int(len(t)), "parents_with_a_pair": int((t["n_pairs"] > 0).sum()),
            "parents_with_deviation": int((t["abs_deviation_sum"] > 0).sum()),
            "n_pairs": n_pairs, "parent_weight_sum": parent_sum, "abs_deviation_sum": deviation,
            "abs_deviation_share": deviation / parent_sum if parent_sum else None,
            "conserved_pair_share": float(t["conserved_pairs"].sum()) / n_pairs if n_pairs else None,
            "household_conservation_row": {k: rows[label][k] for k in conservation.CONSERVATION_COLUMNS},
            "sum_of_parents_equals_batch_row": checks[label]}
    first = labels[0]
    return _jsonable({
        "level": LEVEL, "parent_level": PARENT, "first_arm": first,
        "arms": arms,
        "attributes_equal_to_first_arm": attributes_equal,
        "comparisons": {label: comparison_summary(compare, label, first) for label in labels[1:]},
        "sort_of_parents_compare_csv": (f"abs_deviation_diff_{labels[1]}_minus_{first}, descending"
                                        if len(labels) > 1 else None),
        "eqasim_bs_commit": os.environ.get("EQASIM_BS_COMMIT", "unknown")})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True, action="append", type=Path)
    parser.add_argument("--labels", required=True, nargs="+")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--chunk-rows", type=int, default=5_000_000)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(args.batch_dir) != len(args.labels):
        parser.error(f"{len(args.batch_dir)} --batch-dir but {len(args.labels)} --labels")
    if len(set(args.labels)) != len(args.labels):
        parser.error("labels must be unique")
    bad = [label for label in args.labels if not LABEL_PATTERN.match(label)]
    if bad:
        parser.error(f"labels must match {LABEL_PATTERN.pattern}: {bad}")
    if args.out.exists():
        parser.error(f"refuse: {args.out} exists")

    tables, rows, checks = {}, {}, {}
    for label, path in zip(args.labels, args.batch_dir):
        tables[label], rows[label], checks[label] = build_parents(path, args.chunk_rows)
    compare, attributes_equal = compare_arms(args.labels, tables)
    summary = build_summary(args.labels, args.batch_dir, tables, rows, checks, compare, attributes_equal)

    args.out.mkdir(parents=True)
    for label, table in tables.items():
        table.reset_index().to_csv(args.out / f"parents_{label}.csv", index=False)
    compare.to_csv(args.out / "parents_compare.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
