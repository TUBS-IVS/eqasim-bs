"""Post hoc, exploratory: is the 100 m fit error in the cells whose targets contradict each other? (issue #660)

NOT part of the pre-registered checks of ``popsim-ladder-inputs-c-2026-11-19`` and written after its
results were known. For the 100 m targets of one arm (``--targets-arm``) every cell is classed by how its
person targets (sum of the 22 age x sex bands) relate to the persons its household-size targets need
(``minimum`` = sum over the size classes of size x households, the class of six or more counted as six,
the definition of ``braunschweig.popsim.input_consistency.cell_contradictions``):

* ``consistent``: households and persons above 0 and the gap is at most ``--tolerance`` persons (0.5);
* ``persons_short``: persons below the minimum by more than the tolerance;
* ``persons_over``: no household of six or more, persons above the minimum by more than the tolerance
  (with a household of six or more there is no maximum, so such a cell is ``consistent``);
* ``not_judged``: no household or no person targeted.

Then the as-run outputs of ``--output-arms`` are judged against those targets per class and per cell-size
bin (targeted households 1-10, 11-30, 31 and more; ``ALL`` for both keys): pooled WAPE (sum of absolute
errors over cells and controls / sum of targets) of the six household-size controls, of the 22 age x sex
bands, of four age groups with the sexes summed (0-17, 18-29, 30-59, 60+) and of the persons per cell.
The cell-size bins are there because small cells have larger relative errors whatever their targets.

Output ``--out`` (CSV): ``targets_arm, output_arm, cell_class, size_bin, cells, households, persons,
gap_persons, <metric>_abs_error, <metric>_target, <metric>_wape`` for the four metrics ``size``,
``age22``, ``age4``, ``persons``.

Usage (eqasim environment; imports only its sibling ``posthoc_age_error_by_group``):
  python posthoc_fit_by_cell_consistency.py --root <server root> --out <csv> --targets-arm arm_8 \
      --output-arms arm_8 arm_9 --batches batch_000 batch_013 batch_028 batch_032
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
import posthoc_age_error_by_group as age_probe  # noqa: E402  (bands, groups and the synthetic persons reader)

CELL_ID = age_probe.CELL_ID
HOUSEHOLD_ID = age_probe.HOUSEHOLD_ID
SIZE_SUFFIX = "_Groesse_des_privaten_Haushalts_100m_Gitter"
SIZE_COLUMNS = [f"{name}{SIZE_SUFFIX}" for name in
                ("1_Person", "2_Personen", "3_Personen", "4_Personen", "5_Personen", "6_Personen_und_mehr")]
HOUSEHOLD_TOTAL = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
SEED_SIZE = "H_GR"
CLASSES = ("consistent", "persons_short", "persons_over", "not_judged")
SIZE_BINS = ("1-10", "11-30", "31+")
METRICS = ("size", "age22", "age4", "persons")
OPEN_CLASS_TOLERANCE = 1e-9


def read_cell_targets(folder: Path) -> pd.DataFrame:
    """Household total, the six size targets and the 22 band targets of a batch folder, by cell."""
    table = pd.read_csv(folder / "data" / "control_totals_ZENSUS100m.csv")
    wanted = {HOUSEHOLD_TOTAL: f"{HOUSEHOLD_TOTAL}_{CELL_ID}"}
    wanted.update({c: f"{c}_{CELL_ID}" for c in SIZE_COLUMNS})
    absent = [name for name in wanted.values() if name not in table.columns]
    if absent:
        raise ValueError(f"{folder}: the 100 m table lacks {absent}")
    out = table[list(wanted.values())].astype(float)
    out.columns = list(wanted)
    out.index = table[CELL_ID].astype(str)
    return out.join(age_probe.read_targets(folder))


def classify(targets: pd.DataFrame, tolerance: float) -> pd.DataFrame:
    """``cell_class``, ``size_bin`` and ``gap_persons`` per cell (module docstring)."""
    households = targets[HOUSEHOLD_TOTAL]
    persons = targets[age_probe.BANDS].sum(axis=1)
    weights = np.arange(1, len(SIZE_COLUMNS) + 1, dtype=float)
    minimum = pd.Series(targets[SIZE_COLUMNS].to_numpy() @ weights, index=targets.index)
    open_class = targets[SIZE_COLUMNS[-1]] > OPEN_CLASS_TOLERANCE
    judged = (households > 0) & (persons > 0)
    short = judged & (minimum - persons > tolerance)
    over = judged & ~open_class & (persons - minimum > tolerance)
    cell_class = pd.Series("consistent", index=targets.index)
    cell_class[short] = "persons_short"
    cell_class[over] = "persons_over"
    cell_class[~judged] = "not_judged"
    gap = pd.Series(0.0, index=targets.index)
    gap[short] = (minimum - persons)[short]
    gap[over] = (persons - minimum)[over]
    size_bin = pd.cut(households, bins=[-np.inf, 10, 30, np.inf], labels=list(SIZE_BINS)).astype(str)
    return pd.DataFrame({"cell_class": cell_class, "size_bin": size_bin, "gap_persons": gap,
                         "households": households, "persons": persons})


def read_synthetic_sizes(folder: Path, seed_households: pd.DataFrame, cells) -> pd.DataFrame:
    """Drawn households per cell and size class (six and more in the last class)."""
    expanded = pd.read_csv(folder / "output" / "final_expanded_household_ids.csv", usecols=[CELL_ID, HOUSEHOLD_ID])
    merged = expanded.merge(seed_households, on=HOUSEHOLD_ID, how="left")
    if merged[SEED_SIZE].isna().any():
        raise ValueError(f"{folder}: drawn households without a seed household size")
    size = merged[SEED_SIZE].clip(upper=len(SIZE_COLUMNS)).astype(int)
    if (size < 1).any():
        raise ValueError(f"{folder}: seed household sizes below 1")
    counts = merged.assign(size=size).groupby([CELL_ID, "size"]).size().unstack(fill_value=0)
    counts.index = counts.index.astype(str)
    counts = counts.reindex(index=cells, columns=range(1, len(SIZE_COLUMNS) + 1), fill_value=0).astype(float)
    counts.columns = SIZE_COLUMNS
    return counts


def cell_errors(targets: pd.DataFrame, sizes: pd.DataFrame, persons: pd.DataFrame) -> pd.DataFrame:
    """Per cell the absolute error and the target sum of the four metrics."""
    band_target = targets[age_probe.BANDS]
    groups = age_probe.AGGREGATIONS["4 groups (sexes summed)"]
    out = pd.DataFrame(index=targets.index)
    out["size_abs_error"] = (sizes - targets[SIZE_COLUMNS]).abs().sum(axis=1)
    out["size_target"] = targets[SIZE_COLUMNS].sum(axis=1)
    out["age22_abs_error"] = (persons - band_target).abs().sum(axis=1)
    out["age22_target"] = band_target.sum(axis=1)
    out["age4_abs_error"] = (age_probe.aggregate(persons, groups) - age_probe.aggregate(band_target, groups)).abs().sum(axis=1)
    out["age4_target"] = out["age22_target"]
    out["persons_abs_error"] = (persons.sum(axis=1) - band_target.sum(axis=1)).abs()
    out["persons_target"] = out["age22_target"]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--targets-arm", required=True)
    parser.add_argument("--output-arms", nargs="+", required=True)
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--tolerance", type=float, default=0.5, help="persons; a gap up to it is consistent")
    args = parser.parse_args(argv)
    parts = []
    for batch in args.batches:
        folder = args.root / args.targets_arm / batch
        targets = read_cell_targets(folder)
        classes = classify(targets, args.tolerance)
        seed_households = pd.read_csv(folder / "data" / "seed_households.csv", usecols=[HOUSEHOLD_ID, SEED_SIZE])
        seed_persons = pd.read_csv(folder / "data" / "seed_persons.csv", usecols=[HOUSEHOLD_ID, "HP_ALTER", "HP_SEX"])
        for arm in args.output_arms:
            run = args.root / arm / batch
            errors = cell_errors(targets, read_synthetic_sizes(run, seed_households, targets.index),
                                 age_probe.read_synthetic(run, seed_persons, targets.index))
            parts.append(classes.join(errors).assign(output_arm=arm, cells=1))
    cells = pd.concat(parts, ignore_index=True)
    value_columns = ["cells", "households", "persons", "gap_persons",
                     *[f"{m}_{part}" for m in METRICS for part in ("abs_error", "target")]]
    frames = []
    for keys in (["cell_class", "size_bin"], ["cell_class"], ["size_bin"], []):
        grouped = cells.groupby(["output_arm", *keys], sort=False)[value_columns].sum().reset_index()
        for missing in ("cell_class", "size_bin"):
            if missing not in keys:
                grouped[missing] = "ALL"
        frames.append(grouped)
    table = pd.concat(frames, ignore_index=True)
    for metric in METRICS:
        table[f"{metric}_wape"] = table[f"{metric}_abs_error"] / table[f"{metric}_target"].where(table[f"{metric}_target"] > 0)
    table.insert(0, "targets_arm", args.targets_arm)
    order = ["targets_arm", "output_arm", "cell_class", "size_bin", *value_columns, *[f"{m}_wape" for m in METRICS]]
    table = table[order].sort_values(["output_arm", "cell_class", "size_bin"]).reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    pd.set_option("display.width", 220)
    show = table[table["size_bin"] == "ALL"][["output_arm", "cell_class", "cells", "households", "persons", "gap_persons",
                                              *[f"{m}_wape" for m in METRICS]]]
    print(show.round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
