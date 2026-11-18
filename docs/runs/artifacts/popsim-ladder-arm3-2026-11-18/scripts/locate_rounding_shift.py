"""Locate where the rounding shift of one control arises inside a batch (issue #646, post hoc).

Design goal G4 compares, per control and batch, the integer and the float total over the 100 m cells
(``fit.rounding_shift``). This script splits both totals by 1 km parent and puts next to them, per
parent: the household control, the households of the parent's 1 km pool, the float, integer-part and
integer household sums of its cells, the households of the expanded output, and the fork's path record
of the parent's simultaneous rounding call. For every flagged parent (its call reports a defect, or its
expanded households differ from the household control) it lists the cells with their controls.

It only reads one batch folder that still has its checkpoint store (``output/pipeline.h5``) and writes
``parents.csv``, ``cells_of_flagged_parents.csv`` and ``summary.json`` into a new directory.

Sources and universes:
- weights, incidence and the control's target sum ``T`` come from the store (last checkpoint of each
  table; weight rows are household groups), exactly as ``fit.misfit_split`` reads them, so ``T``, the
  float sum ``F`` and the integer sum ``I`` of the summary are those of the G4 check;
- the ``*_control`` columns come from ``data/control_totals_ZENSUS100m.csv`` (all cells of the batch,
  including cells without households, which the store's lowest-level table leaves out);
- the ``*_expanded`` columns count the households of ``final_expanded_household_ids.csv`` with the
  incidence rebuilt from the seed tables;
- ``households_integer_parts`` is the sum over a cell's weight rows of ``floor(balanced_weight)``.

The script stops if the integer weights of the store do not reproduce the expanded households of every
cell (households and the chosen control): the split would then not describe the output.

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python locate_rounding_shift.py --batch <batch folder> --out <new dir> \
      [--control building_type_sonstiges_ZENSUS100m_target]
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics import batch_folder, fit

LEVEL = "ZENSUS100m"
PARENT = "ZENSUS1km"
DEFAULT_CONTROL = "building_type_sonstiges_ZENSUS100m_target"
# Controls listed per cell of a flagged parent: short label of the output columns -> control target.
CELL_CONTROLS = {
    "one_person": "1_Person_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target",
    "owner": "EigentuemerHH_Tenure_100m_Gitter_ZENSUS100m_target",
    "renter": "MieterHH_Tenure_100m_Gitter_ZENSUS100m_target",
    "building_one_two_family": "building_type_ein_zweifamilienhaus_ZENSUS100m_target",
    "building_multi_family": "building_type_mehrfamilienhaus_ZENSUS100m_target",
    "building_other": "building_type_sonstiges_ZENSUS100m_target",
}
DEFECT_COLUMNS = ("hh_excess", "zone_shortfall", "row_shortfall", "hh_unreachable", "hh_outside_lp")
PATH_COLUMNS = ("status", "zones", "rows", "flow_wanted", "flow_achieved", *DEFECT_COLUMNS)
OUTPUT_FILES = ("final_expanded_household_ids.csv", "synthetic_households.csv", "synthetic_persons.csv")
# Tolerance for "the expanded households equal the integer weights" (both are whole numbers).
COUNT_TOLERANCE = 1e-6


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_store(store_path: Path, chunk_rows: int) -> tuple:
    """Incidence, the 100 m controls and the positive weight rows of both levels (last checkpoints)."""
    with pd.HDFStore(str(store_path), mode="r") as store:
        incidence = store.get(fit._current_key(store, "incidence_table"))
        controls = store.get(fit._current_key(store, f"{LEVEL}_controls"))
        weights, _ = fit._read_weights(store, LEVEL, chunk_rows)
        parent_weights, _ = fit._read_weights(store, PARENT, chunk_rows)
    if not incidence.index.is_unique:
        raise ValueError(f"{store_path}: household group ids of the incidence table are not unique")
    if not controls.index.is_unique:
        raise ValueError(f"{store_path}: zone ids of {LEVEL}_controls are not unique")
    return incidence, controls, weights, parent_weights


def weighted_sums(weights: pd.DataFrame, zone_col: str, incidence: pd.DataFrame, targets: list,
                  weight_col: str, *, integer_parts: bool = False) -> pd.DataFrame:
    """Per zone, the sum over its weight rows of weight x incidence; one column per target."""
    position = incidence.index.get_indexer(weights["H_ID"])
    if (position < 0).any():
        unknown = weights["H_ID"][position < 0].unique()[:10].tolist()
        raise ValueError(f"{zone_col} weights: household group id(s) {unknown} are not in the incidence table")
    weight = weights[weight_col].to_numpy(dtype=float)
    if integer_parts:
        weight = np.floor(weight)
    values = incidence[targets].to_numpy(dtype=float)[position] * weight[:, None]
    return pd.DataFrame(values, columns=targets).groupby(weights[zone_col].to_numpy()).sum()


def expanded_sums(batch: batch_folder.BatchFolder, targets: list) -> pd.DataFrame:
    """Per 100 m cell, the sum over its expanded households of their incidence; one column per target."""
    incidence = batch_folder.household_incidence(batch)
    position = incidence.index.get_indexer(batch.expanded[batch.household_id_col])
    if (position < 0).any():
        raise ValueError(f"batch {batch.name}: expanded household ids are missing from the seed households")
    values = incidence[targets].to_numpy(dtype=float)[position]
    return pd.DataFrame(values, columns=targets).groupby(batch.expanded[LEVEL].to_numpy()).sum()


def read_path_record(batch_path: Path) -> pd.DataFrame:
    """All simultaneous integerizer calls of the batch's path record, one row per call."""
    record = pd.read_csv(batch_path / "output" / "integerizer_paths.csv")
    missing = [c for c in ("row_type", "kind", "trace_label", *PATH_COLUMNS) if c not in record.columns]
    if missing:
        raise ValueError(f"integerizer_paths.csv of {batch_path} lacks column(s) {missing}")
    calls = record[(record["row_type"] == "call") & (record["kind"] == "simul")].copy()
    if calls["trace_label"].duplicated().any():
        repeated = calls.loc[calls["trace_label"].duplicated(), "trace_label"].unique()[:5].tolist()
        raise ValueError(f"path record of {batch_path}: several simultaneous calls for {repeated}")
    for column in PATH_COLUMNS[1:]:
        calls[column] = pd.to_numeric(calls[column], errors="raise")
    return calls


def build_tables(batch: batch_folder.BatchFolder, control: str, chunk_rows: int) -> tuple:
    """Return ``(parents, cells, summary)`` for one batch and one control."""
    total = batch.total_hh_control
    targets = list(dict.fromkeys([total, control, *CELL_CONTROLS.values()]))
    store_path = batch.path / "output" / "pipeline.h5"
    incidence, store_controls, weights, parent_weights = read_store(store_path, chunk_rows)
    missing = [t for t in targets if t not in incidence.columns or t not in store_controls.columns]
    if missing:
        raise ValueError(f"batch {batch.name}: control(s) {missing} are not {LEVEL} controls of the store")

    crosswalk = pd.read_csv(batch.path / "data" / "geo_cross_walk.csv", dtype=str)[[LEVEL, PARENT]]
    crosswalk = crosswalk.drop_duplicates()
    if crosswalk[LEVEL].duplicated().any():
        raise ValueError(f"batch {batch.name}: a {LEVEL} cell has several {PARENT} parents")
    parent_of = crosswalk.set_index(LEVEL)[PARENT]
    cells_index = parent_of.index

    input_controls = batch_folder.control_totals(batch, LEVEL)[targets]
    input_controls.index = input_controls.index.astype(str)
    unknown_cells = input_controls.index.difference(cells_index)
    if len(unknown_cells):
        raise ValueError(f"batch {batch.name}: {len(unknown_cells)} control cell(s) are not in the crosswalk")

    float_cell = weighted_sums(weights, LEVEL, incidence, targets, "balanced_weight")
    integer_cell = weighted_sums(weights, LEVEL, incidence, targets, "integer_weight")
    parts_cell = weighted_sums(weights, LEVEL, incidence, [total], "balanced_weight", integer_parts=True)
    pool_parent = weighted_sums(parent_weights, PARENT, incidence, targets, "integer_weight")
    expanded_cell = expanded_sums(batch, targets)
    for name, table in (("float", float_cell), ("integer", integer_cell), ("expanded", expanded_cell)):
        outside = table.index.astype(str).difference(cells_index)
        if len(outside):
            raise ValueError(f"batch {batch.name}: {len(outside)} cell(s) of the {name} sums are not in the "
                             "crosswalk")

    def per_cell(table: pd.DataFrame) -> pd.DataFrame:
        table = table.copy()
        table.index = table.index.astype(str)
        return table.reindex(cells_index, fill_value=0.0)

    control_cell = per_cell(input_controls)
    float_cell, integer_cell, expanded_cell = per_cell(float_cell), per_cell(integer_cell), per_cell(expanded_cell)
    parts_cell = per_cell(parts_cell)

    # The split is only about the output if the integer weights reproduce the expanded households.
    for target in (total, control):
        difference = (integer_cell[target] - expanded_cell[target]).abs()
        if difference.max() > COUNT_TOLERANCE:
            raise ValueError(
                f"batch {batch.name}: the integer weights of the store give other cell totals of {target} "
                f"than the expanded output in {int((difference > COUNT_TOLERANCE).sum())} cell(s) "
                f"(largest difference {difference.max():g}); is the store from another run?")

    cells = pd.DataFrame({
        PARENT: parent_of.to_numpy(), LEVEL: cells_index.to_numpy(),
        "households_control": control_cell[total].to_numpy(),
        "households_float": float_cell[total].to_numpy(),
        "households_integer_parts": parts_cell[total].to_numpy(),
        "households_expanded": expanded_cell[total].to_numpy()})
    for label, target in CELL_CONTROLS.items():
        cells[f"{label}_control"] = control_cell[target].to_numpy()
        cells[f"{label}_float"] = float_cell[target].to_numpy()
        cells[f"{label}_expanded"] = expanded_cell[target].to_numpy()
    cells["control_float"] = float_cell[control].to_numpy()
    cells["control_integer"] = integer_cell[control].to_numpy()

    grouped = cells.groupby(PARENT)
    parents = pd.DataFrame({
        "cells": grouped[LEVEL].size(),
        "cells_with_households": grouped["households_control"].apply(lambda s: int((s > 0).sum())),
        "households_control": grouped["households_control"].sum(),
        "households_float": grouped["households_float"].sum(),
        "households_integer_parts": grouped["households_integer_parts"].sum(),
        "households_expanded": grouped["households_expanded"].sum(),
        "control_float": grouped["control_float"].sum(),
        "control_integer": grouped["control_integer"].sum()})
    target_of_cell = pd.Series(control_cell[control].to_numpy(), index=parent_of.to_numpy())
    parents["control_target"] = target_of_cell.groupby(level=0).sum().reindex(parents.index)
    pool_parent.index = pool_parent.index.astype(str)
    outside = pool_parent.index.difference(parents.index)
    if len(outside):
        raise ValueError(f"batch {batch.name}: {len(outside)} parent(s) of the {PARENT} weights are not in the "
                         "crosswalk")
    pool_parent = pool_parent.reindex(parents.index, fill_value=0.0)
    parents["households_pool_1km"] = pool_parent[total]
    parents["control_pool_1km"] = pool_parent[control]
    parents["control_integer_minus_float"] = parents["control_integer"] - parents["control_float"]

    calls = read_path_record(batch.path)
    prefix = f"{PARENT}_"
    parent_calls = calls[calls["trace_label"].astype(str).str.startswith(prefix)].copy()
    parent_calls.index = parent_calls["trace_label"].str[len(prefix):]
    outside = parent_calls.index.difference(parents.index)
    if len(outside):
        raise ValueError(f"batch {batch.name}: path-record parent(s) {outside[:5].tolist()} are not in the "
                         "crosswalk")
    for column in PATH_COLUMNS:
        parents[f"call_{column}"] = parent_calls[column].reindex(parents.index)
    defect = parents[[f"call_{c}" for c in DEFECT_COLUMNS]].fillna(0.0).sum(axis=1) > 0
    household_gap = (parents["households_expanded"] - parents["households_control"]).abs() > COUNT_TOLERANCE
    parents["flagged"] = defect | household_gap
    parents = parents.reset_index().rename(columns={"index": PARENT})
    parents = parents[[
        PARENT, "flagged", "cells", "cells_with_households", "households_control", "households_pool_1km",
        "households_float", "households_integer_parts", "households_expanded",
        *[f"call_{c}" for c in PATH_COLUMNS],
        "control_target", "control_pool_1km", "control_float", "control_integer",
        "control_integer_minus_float"]]

    flagged_ids = parents.loc[parents["flagged"], PARENT].tolist()
    flagged_cells = cells[cells[PARENT].isin(flagged_ids)].drop(columns=["control_float", "control_integer"])
    flagged_cells = flagged_cells.sort_values([PARENT, LEVEL], ignore_index=True)

    in_flagged = parents["flagged"].to_numpy()
    shift_by_parent = parents["control_integer_minus_float"].to_numpy()
    target_store = float(store_controls[control].sum())
    float_sum, integer_sum = float(parents["control_float"].sum()), float(parents["control_integer"].sum())
    household_gap_by_parent = (parents["households_expanded"] - parents["households_control"]).to_numpy()
    flagged_labels = [prefix + parent for parent in flagged_ids]
    summary = {
        "batch": batch.name,
        "control": control,
        "level": LEVEL,
        "g4_terms_of_the_control": {
            "target_sum_T": target_store, "float_sum_F": float_sum, "integer_sum_I": integer_sum,
            "shift": abs(integer_sum - float_sum) / target_store if target_store else None},
        "parents": {
            "in_crosswalk": int(len(parents)),
            "with_simultaneous_call": int(parents["call_status"].notna().sum()),
            "flagged": flagged_ids},
        "control_integer_minus_float": {
            "all_parents": float(shift_by_parent.sum()),
            "flagged_parents": float(shift_by_parent[in_flagged].sum()),
            "other_parents": float(shift_by_parent[~in_flagged].sum()),
            "sum_of_absolute_values_other_parents": float(np.abs(shift_by_parent[~in_flagged]).sum())},
        "households_expanded_minus_control": {
            "all_parents": float(household_gap_by_parent.sum()),
            "flagged_parents": float(household_gap_by_parent[in_flagged].sum()),
            "sum_of_absolute_values_other_parents": float(np.abs(household_gap_by_parent[~in_flagged]).sum())},
        "path_record_totals": {
            column: {
                "all_simultaneous_calls": float(calls[column].fillna(0.0).sum()),
                "calls_of_flagged_parents": float(
                    calls.loc[calls["trace_label"].isin(flagged_labels), column].fillna(0.0).sum())}
            for column in DEFECT_COLUMNS},
        "checks": {
            "integer_weights_reproduce_the_expanded_cell_totals": True,
            "largest_absolute_difference_pool_minus_float_per_parent": {
                "households": float((parents["households_pool_1km"] - parents["households_float"]).abs().max()),
                "control": float((parents["control_pool_1km"] - parents["control_float"]).abs().max())}},
        "outputs_sha256": {name: _sha256(batch.path / "output" / name) for name in OUTPUT_FILES},
        "store_bytes": store_path.stat().st_size,
        "eqasim_bs_commit": os.environ.get("EQASIM_BS_COMMIT", "unknown"),
    }
    return parents, flagged_cells, summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--control", default=DEFAULT_CONTROL)
    parser.add_argument("--chunk-rows", type=int, default=5_000_000)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"refuse: {args.out} exists")

    batch = batch_folder.load_batch_folder(args.batch)
    if not batch.has_store:
        parser.error(f"{args.batch} has no checkpoint store (output/pipeline.h5)")
    parents, cells, summary = build_tables(batch, args.control, args.chunk_rows)

    args.out.mkdir(parents=True)
    parents.to_csv(args.out / "parents.csv", index=False)
    cells.to_csv(args.out / "cells_of_flagged_parents.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
