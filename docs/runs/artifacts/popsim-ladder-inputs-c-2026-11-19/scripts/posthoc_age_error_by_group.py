"""Post hoc, exploratory: where the 100 m age error of the method arms sits (issue #660).

NOT part of the pre-registered checks of ``popsim-ladder-inputs-c-2026-11-19`` and written after its
results were known. It reads the as-run outputs of the arms under ``--root`` (``<arm>/<batch>/output/
final_expanded_household_ids.csv``, joined to the seed persons of the first arm) and judges every arm
against the SAME integer 100 m age x sex targets of the first arm (``--arms``, default arm 8 first), at
several aggregations of the 22 bands:

* ``22 bands (age x sex)`` (reproduces the pooled WAPE of the diagnostics on view int100);
* ``11 age bands (sexes summed)``, ``2 sexes (ages summed)``, ``persons per cell``;
* ``4 groups (sexes summed)``: 0-17, 18-29, 30-59, 60+, and the same ``4 groups x sex``;
* ``3 groups (sexes summed)``: 0-17, 18-59, 60+.

WAPE = sum over cells and groups of |synthetic - target| / sum of the targets, pooled over the batches
(the estimator of ``popsim_batch_diagnostics.fit.control_fit``). The groups are sums of the bands of the
controls (``control_spec.tier0_backbone_catalog``, fine teen bands), so every aggregation has the same
target total.

Output ``--out`` (CSV, long): ``arm, aggregation, group, abs_error, target_sum, wape``; ``group`` is
``ALL`` for the whole aggregation and a single band or group for the per-group rows.

Usage (eqasim environment; no eqasim-bs import needed):
  python posthoc_age_error_by_group.py --root <server root> --out <csv> \
      --arms arm_8 arm_9 arm_10 --batches batch_000 batch_013 batch_028 batch_032
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

CELL_ID = "ZENSUS100m"
HOUSEHOLD_ID = "H_ID"
# Lower edges of the 11 age bands of the 100 m person controls and their labels.
EDGES = [0, 10, 15, 18, 20, 30, 40, 50, 60, 70, 80, 200]
LABELS = ["0_9", "10_14", "15_17", "18_19", "20_29", "30_39", "40_49", "50_59", "60_69", "70_79", "80_plus"]
BROAD4 = {"0_9": "0-17", "10_14": "0-17", "15_17": "0-17", "18_19": "18-29", "20_29": "18-29",
          "30_39": "30-59", "40_49": "30-59", "50_59": "30-59", "60_69": "60+", "70_79": "60+", "80_plus": "60+"}
BROAD3 = {label: ("18-59" if group in ("18-29", "30-59") else group) for label, group in BROAD4.items()}
SEX_OF_CODE = {1: "M", 2: "F"}
BANDS = [f"{sex}_AGE_{label}" for sex in ("M", "F") for label in LABELS]


def _label(band: str) -> str:
    return band.split("_AGE_")[1]


AGGREGATIONS = {
    "22 bands (age x sex)": {band: band for band in BANDS},
    "11 age bands (sexes summed)": {band: _label(band) for band in BANDS},
    "4 groups x sex": {band: f"{band[0]} {BROAD4[_label(band)]}" for band in BANDS},
    "4 groups (sexes summed)": {band: BROAD4[_label(band)] for band in BANDS},
    "3 groups (sexes summed)": {band: BROAD3[_label(band)] for band in BANDS},
    "2 sexes (ages summed)": {band: band[0] for band in BANDS},
    "persons per cell": {band: "all" for band in BANDS},
}


def read_targets(folder: Path) -> pd.DataFrame:
    """The integer 100 m age x sex targets of a batch folder, indexed by cell, columns ``BANDS``."""
    table = pd.read_csv(folder / "data" / "control_totals_ZENSUS100m.csv")
    absent = [band for band in BANDS if f"{band}_agg_{CELL_ID}" not in table.columns]
    if absent:
        raise ValueError(f"{folder}: the 100 m table lacks the band column(s) {absent}")
    out = table[[f"{band}_agg_{CELL_ID}" for band in BANDS]].astype(float)
    out.columns = BANDS
    out.index = table[CELL_ID].astype(str)
    return out


def read_synthetic(folder: Path, seed_persons: pd.DataFrame, cells) -> pd.DataFrame:
    """Synthetic persons per cell and band of a run folder (every drawn household with its persons)."""
    expanded = pd.read_csv(folder / "output" / "final_expanded_household_ids.csv", usecols=[CELL_ID, HOUSEHOLD_ID])
    merged = expanded.merge(seed_persons, on=HOUSEHOLD_ID, how="left")
    if merged["HP_ALTER"].isna().any():
        raise ValueError(f"{folder}: drawn households without seed persons")
    sex = merged["HP_SEX"].map(SEX_OF_CODE)
    if sex.isna().any():
        raise ValueError(f"{folder}: sex codes outside {sorted(SEX_OF_CODE)}")
    band = pd.cut(merged["HP_ALTER"], bins=EDGES, right=False, labels=LABELS).astype(str)
    counts = (merged.assign(band=sex + "_AGE_" + band).groupby([CELL_ID, "band"]).size().unstack(fill_value=0))
    counts.index = counts.index.astype(str)
    outside = sorted(set(counts.index) - set(cells))
    if outside:
        raise ValueError(f"{folder}: {len(outside)} cell(s) of the output are not in the target table")
    return counts.reindex(index=cells, columns=BANDS, fill_value=0).astype(float)


def aggregate(frame: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    return frame.T.groupby(mapping).sum().T


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--arms", nargs="+", required=True, help="first arm: the targets and the seed")
    parser.add_argument("--batches", nargs="+", required=True)
    args = parser.parse_args(argv)
    reference = args.arms[0]
    sums: dict = {}
    for batch in args.batches:
        targets = read_targets(args.root / reference / batch)
        seed_persons = pd.read_csv(args.root / reference / batch / "data" / "seed_persons.csv",
                                   usecols=[HOUSEHOLD_ID, "HP_ALTER", "HP_SEX"])
        for arm in args.arms:
            synthetic = read_synthetic(args.root / arm / batch, seed_persons, targets.index)
            for name, mapping in AGGREGATIONS.items():
                target, drawn = aggregate(targets, mapping), aggregate(synthetic, mapping)
                error = (drawn - target).abs().sum()
                for group in target.columns:
                    for key in ((arm, name, "ALL"), (arm, name, group)):
                        entry = sums.setdefault(key, [0.0, 0.0])
                        entry[0] += float(error[group])
                        entry[1] += float(target[group].sum())
    rows = [{"arm": arm, "aggregation": name, "group": group, "abs_error": error, "target_sum": target,
             "wape": error / target if target > 0 else float("nan")}
            for (arm, name, group), (error, target) in sums.items()]
    table = pd.DataFrame(rows)
    # A single-group aggregation would list its only group twice: keep its ALL row.
    single = table.groupby(["arm", "aggregation"])["group"].transform("count") == 2
    table = table[~(single & (table["group"] != "ALL"))]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    pd.set_option("display.width", 200)
    print(table[table["group"] == "ALL"].pivot(index="aggregation", columns="arm", values="wape").round(4).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
