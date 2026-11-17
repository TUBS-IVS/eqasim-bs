"""Prepare ladder arm 2 of issue #647 (WP3): arm-1 batch inputs plus the WP3 seed and target repairs.

Arm 2 is arm 1 (WP2 fork, fractional weights, Gumbel draw; folders written by
``popsim-ladder-arm1-2026-11-17``'s prepare_arm.py) with ONLY the WP3 changes applied, so the
difference between the two arms is WP3 alone:

* the seed tables get the WP3 resolution with the eqasim-bs functions the stage uses
  (``attributes.resolve_binary_sex`` then ``attributes.resolve_tenure_and_building_type``, one stream
  ``RandomState(random_seed + 97531)``, in that order). The production seed is one seed group shared by
  all batches, so it is resolved ONCE and the same resolved seed is written into every batch;
* the 100 m building-type control totals are rescaled from dwellings to the household total with
  ``building_type_targets.rescale_building_type_to_households`` (parents: 1 km cell, then Kreis of the
  batch's crosswalk) and integerised per column within the 1 km parent with
  ``controls.integerize_within_parents`` (parent total = round of the float parent sum), as the stage's
  control-total writer does;
* the WP3 contract checks run in error mode on the result (``seed_contract``).

Deviation from a pipeline run, recorded in the run manifest: the pipeline rescales the FLOAT census
dwelling counts before the integerisation; the arm-1 folders hold the integerised dwelling targets,
so the rescale here starts from those; the pipeline resolves on the completed donor tables (same
records, other row order, so other individual draws). ``alter_gr1`` (MiD age band) and ``hhgr_gr``
(household size class) are not seed columns and are derived here from HP_ALTER and H_GR with the MiD
codeplan bands; they are not written back.

Usage (eqasim-bs code on PYTHONPATH, eqasim env):
  python prepare_arm2.py --src <arm-1 dir> --arm-dir <new dir> --batches batch_000 batch_032 \
      --random-seed 1234
"""
import argparse
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes, building_type_targets, controls, seed_contract

HOUSEHOLD_ID = "H_ID"
TOTAL_FIELD = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS100m"
TOTAL_TARGET = TOTAL_FIELD + "_target"
BUILDING_TYPE_FIELDS = (
    "building_type_ein_zweifamilienhaus_ZENSUS100m",
    "building_type_mehrfamilienhaus_ZENSUS100m",
    "building_type_sonstiges_ZENSUS100m",
)
# MiD 2023 codeplan B1, Personen, alter_gr1: 1 0-5, 2 6-9, 3 10-13, 4 14-17, 5 18-24, 6 25-44,
# 7 45-59, 8 60-64, 9 65 and older.
ALTER_GR1_UPPER_BOUNDS = (5, 9, 13, 17, 24, 44, 59, 64)

logger = logging.getLogger("prepare_arm2")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _alter_gr1(age: pd.Series) -> pd.Series:
    values = pd.to_numeric(age, errors="raise")
    bands = np.searchsorted(np.asarray(ALTER_GR1_UPPER_BOUNDS), values.to_numpy(), side="left") + 1
    return pd.Series(bands.astype("int64"), index=age.index)


def resolve_seed(households: pd.DataFrame, persons: pd.DataFrame, random_seed: int):
    """Resolved copies of the seed tables (raw codes kept in the *_raw columns)."""
    rng = np.random.RandomState(random_seed + 97531)
    work_persons = persons.copy()
    work_persons["alter_gr1"] = _alter_gr1(work_persons["HP_ALTER"])
    resolved_persons = attributes.resolve_binary_sex(work_persons, rng=rng)
    resolved_persons = resolved_persons.drop(columns=["alter_gr1"])
    work_households = households.copy()
    work_households["hhgr_gr"] = work_households["H_GR"].clip(upper=5).astype("int64")
    persons_for_join = resolved_persons.copy()
    persons_for_join[HOUSEHOLD_ID] = pd.to_numeric(persons_for_join[HOUSEHOLD_ID]).astype("int64")
    resolved_households = attributes.resolve_tenure_and_building_type(
        work_households, persons_for_join, rng=rng)
    resolved_households = resolved_households.drop(columns=["hhgr_gr"])
    return resolved_households, resolved_persons


def rescale_targets(totals: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """ZENSUS100m control totals with household-based integer building-type targets."""
    geo = crosswalk[["ZENSUS100m", "ZENSUS1km", "KREIS"]].drop_duplicates("ZENSUS100m")
    cells = totals.merge(geo, on="ZENSUS100m", how="left", validate="one_to_one")
    if cells["ZENSUS1km"].isna().any():
        raise ValueError("control totals hold 100 m cells that are not in the crosswalk")
    cells = building_type_targets.rescale_building_type_to_households(
        cells, BUILDING_TYPE_FIELDS, household_total_col=TOTAL_FIELD,
        parent_cols=("ZENSUS1km", "KREIS"))
    out = totals.copy()
    for column in BUILDING_TYPE_FIELDS:
        out[column] = controls.integerize_within_parents(
            cells, value_col=column, parent_col="ZENSUS1km").to_numpy().astype("int64")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--arm-dir", required=True, type=Path)
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--random-seed", type=int, required=True)
    args = parser.parse_args(argv)
    args.arm_dir.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(args.arm_dir / "prepare_arm2.log"),
                                  logging.StreamHandler()])

    seed_digests = {_sha256(args.src / b / "data" / "seed_households.csv")
                    + _sha256(args.src / b / "data" / "seed_persons.csv") for b in args.batches}
    if len(seed_digests) != 1:
        raise ValueError("the batches do not share one seed; resolve per batch instead")
    first = args.src / args.batches[0] / "data"
    households = pd.read_csv(first / "seed_households.csv")
    persons = pd.read_csv(first / "seed_persons.csv")
    resolved_households, resolved_persons = resolve_seed(households, persons, args.random_seed)

    record = {"src": str(args.src), "random_seed": args.random_seed, "fill_stream_offset": 97531,
              "batches": {}}
    for batch in args.batches:
        src = args.src / batch
        dst = args.arm_dir / batch
        shutil.copytree(src / "configs", dst / "configs")
        shutil.copytree(src / "data", dst / "data")
        (dst / "output").mkdir()
        resolved_households.to_csv(dst / "data" / "seed_households.csv", index=False)
        resolved_persons.to_csv(dst / "data" / "seed_persons.csv", index=False)
        totals_path = dst / "data" / "control_totals_ZENSUS100m.csv"
        totals = pd.read_csv(totals_path, dtype={"ZENSUS100m": str})
        crosswalk = pd.read_csv(dst / "data" / "geo_cross_walk.csv", dtype=str)
        before = totals[list(BUILDING_TYPE_FIELDS)].to_numpy().sum()
        new_totals = rescale_targets(totals, crosswalk)
        new_totals.to_csv(totals_path, index=False)
        household_total = float(new_totals[TOTAL_FIELD].sum())
        after = new_totals[list(BUILDING_TYPE_FIELDS)].to_numpy().sum()

        spec = pd.read_csv(dst / "configs" / "controls.csv")
        coverage = seed_contract.check_seed_coverage(
            resolved_households, resolved_persons, spec, TOTAL_TARGET, mode="error")
        # The batch file suffixes every control column with its geography; the check reads the
        # stage's cells naming (control_field without the suffix).
        cells = new_totals.rename(columns=lambda c: c[:-len("_ZENSUS100m")]
                                  if c.endswith("_ZENSUS100m") else c)
        cells = cells.merge(crosswalk[["ZENSUS100m", "KREIS"]].drop_duplicates("ZENSUS100m"),
                            on="ZENSUS100m", how="left")
        # Warn mode: these are the batch's INTEGER targets grouped by the crosswalk Kreis, where a
        # Kreis slice of a few households misses the tolerance by rounding alone. The stage's gate
        # runs on the float cells per resolved Kreis (real_cells_check.py); this table is recorded.
        totals_check = seed_contract.check_household_group_totals(
            cells, spec, TOTAL_TARGET, kreis_col="KREIS", mode="warn")
        coverage.to_csv(args.arm_dir / f"{batch}_seed_coverage.csv", index=False)
        totals_check.to_csv(args.arm_dir / f"{batch}_household_group_totals.csv", index=False)
        record["batches"][batch] = {
            "building_type_target_sum_before": int(before),
            "building_type_target_sum_after": int(after),
            "household_total": household_total,
            "ratio_before": before / household_total if household_total else None,
            "ratio_after": after / household_total if household_total else None,
        }
        logger.info("%s: building-type target sum %d -> %d against %d households", batch,
                    before, after, household_total)

    raw_counts = {
        "HP_SEX_raw_3_9": int(resolved_persons["HP_SEX_raw"].isin([3, 9]).sum()),
        "H_MIETE_raw_3_309": int(resolved_households["H_MIETE_raw"].isin([3, 309]).sum()),
        "H_MIETE_raw_9": int((resolved_households["H_MIETE_raw"] == 9).sum()),
        "haustyp_raw_95": int((resolved_households["haustyp_raw"] == 95).sum()),
        "seed_persons": int(len(resolved_persons)),
        "seed_households": int(len(resolved_households)),
    }
    record["resolved_records"] = raw_counts
    (args.arm_dir / "arm_settings.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
