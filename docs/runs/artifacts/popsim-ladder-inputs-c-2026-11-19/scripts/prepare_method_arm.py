"""Prepare one batch folder of a control-level or rounding measurement arm (issue #660).

Copies ``configs/`` and ``data/`` of a base batch folder (arm 8 of the household-universe ladder) to
``--out``, creates an empty ``output/`` and applies ONE ``--step`` (each step is measured on its own
against the base):

* ``age_sex_at_1km``: the 22 age x sex rows of ``configs/controls.csv`` (the 100 m person bands of
  ``control_spec.tier0_backbone_catalog`` in the variant ``controls.csv`` carries, i.e.
  ``M_AGE_0_9_agg`` ... ``F_AGE_80_plus_agg``) move to the 1 km level: geography ``ZENSUS1km``,
  target ``<band>_ZENSUS1km_target``, control_field ``<band>_ZENSUS1km``. importance, seed_table,
  expression and the row order stay. Raises unless exactly 22 such rows exist and the 1 km table
  holds every ``<band>_ZENSUS1km`` column. No data table changes.
* ``float_census_controls``: the integer 100 m columns of four groups are replaced by their
  UNROUNDED per-cell values, the 1 km columns by the per-parent sums of the new 100 m values. The
  groups (each only when its ``_ZENSUS100m`` columns are in the 100 m table and in
  ``controls.csv``; a group present in neither is skipped and recorded):

  1. ``household_size``: the six household-size controls of ``control_spec.tier1_controls``
     (``_TIER1_HH_SIZE_ENTRIES``; not the household total ``HH_TOTAL_CENSUS_COLUMN``);
  2. ``tenure``: ``control_spec._TIER2_TENURE_ENTRIES``;
  3. ``building_type``: ``control_spec._TIER2_BUILDING_TYPE_ENTRIES``;
  4. ``age_sex``: the 22 age x sex bands.

  The float values are computed as the stage computes them before the integerisation
  (``build_float_cells``): census sources of all model cells of ``--kreise`` (the Kreis-level
  fallbacks pool all cells of a Kreis), aggregated by ``prepared_cells.add_aggregated_controls``;
  building type on the basis ``--building-type-basis`` (default ``arm2_integer_dwellings``, the
  construction the ladder folders carry since arm 2, see ``BASIS_ARM2``; ``float_dwellings`` is the
  household rescale of ADR-0198 as ``stage._add_aggregated_control_columns`` does it); tenure limited by ``stage._limit_owner_targets_to_dwellings``; restricted to the batch's cells
  in loader order. The age bands then get the household-less-cell move on floats
  (``household_less_cells.move_persons_to_household_cells`` with the integer household total
  integerised within the 1 km parents, as ``mid.build_control_totals`` does). Each household group
  is rescaled per cell to the cell's INTEGER household total of the base 100 m table
  (``rescale_to_households``); the age bands are not rescaled.

  SELF-CHECK before writing: integerising the float values the way the pipeline did
  (``mid.build_control_totals`` for groups 1-3; ``prepare_household_arm.rebuild_person_tables`` with
  the move for the age bands) must give the base folder's integer columns in every cell; otherwise
  nothing is written and the problem names group, column and number of differing cells.

Reused by import (no copy): ``prepare_household_arm`` of Part B
(``popsim-ladder-inputs-b-2026-11-19/scripts``) and, through it, Part A's helpers.

``prepare_log.json`` in ``--out``: arguments, step, library commit, sha256 of the input parquet, the
step report, per file whether it changed and per column / row what changed
(``prepare_household_arm.compare_folders``), problems, ok. Exit 0: only the step's rows / column
values changed; 1: anything else changed, a check failed or the step raised; 2: unusable arguments.

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python prepare_method_arm.py --base <arm 8>/batch_000 --out <new arm>/batch_000 \
      --step float_census_controls --kreise 03101,03102,... --cells-100m <zensus100m prepared parquet> \
      [--move-persons-max 20 --tenure-min-households-outside 10] --commit <eqasim-bs sha>
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.popsim import building_type_targets, folders, mid, prepared_cells, stage, tenure_targets
from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import controls as ctrl
from braunschweig.popsim.household_less_cells import move_persons_to_household_cells

PART_B_SCRIPTS = Path(__file__).resolve().parents[2] / "popsim-ladder-inputs-b-2026-11-19" / "scripts"
if str(PART_B_SCRIPTS) not in sys.path:
    sys.path.append(str(PART_B_SCRIPTS))
import prepare_household_arm as part_b  # noqa: E402  (Part B's helpers, imported, not copied)

part_a = part_b.part_a
logger = logging.getLogger("prepare_method_arm")

STEP_AGE_SEX = "age_sex_at_1km"
STEP_FLOAT = "float_census_controls"
STEPS = (STEP_AGE_SEX, STEP_FLOAT)
CELL_ID, PARENT_ID = part_b.CELL_ID, part_b.PARENT_ID
CONTROLS, TABLE_100M, TABLE_1KM = part_b.CONTROLS, part_b.TABLE_100M, part_b.TABLE_1KM
CROSSWALK = part_b.CROSSWALK
GRID_TABLES = part_b.GRID_TABLES
AGE_SEX_CONTROL_COUNT = 22  # the fine-teen backbone: 11 bands x 2 sexes
GROUP_SIZE, GROUP_TENURE, GROUP_BUILDING, GROUP_AGE = "household_size", "tenure", "building_type", "age_sex"
HOUSEHOLD_GROUPS = (GROUP_SIZE, GROUP_TENURE, GROUP_BUILDING)
# The tiers the stage passes to the owner limit; with tier2 among them the catalog has the tenure control.
STAGE_CONTROL_TIERS = ("tier0", "tier1", "tier2", "tier3")
# A rescale fallback rate above this share of the cells with households is logged as a WARNING.
RESCALE_FALLBACK_WARN_RATE = 0.01
# Basis of the unrounded building-type values (--building-type-basis):
# * float_dwellings: the pipeline's rule (ADR-0198): the FLOAT census dwelling counts of all model cells
#   rescaled to the households, then integerised;
# * arm2_integer_dwellings: the construction the ladder folders carry since arm 2
#   (popsim-ladder-arm2-2026-11-17, prepare_arm2.rescale_targets, a recorded deviation from the
#   pipeline): the dwelling counts integerised within the 1 km parents of the batch (as the production
#   run wrote them), then rescaled on the batch's cells to the integer household total of the folder
#   (parents 1 km, then Kreis of the crosswalk). Its floats are what the folders' building-type
#   integers were rounded from, so a measurement on top of those folders changes only the rounding.
BASIS_FLOAT_DWELLINGS = "float_dwellings"
BASIS_ARM2 = "arm2_integer_dwellings"
BUILDING_TYPE_BASES = (BASIS_ARM2, BASIS_FLOAT_DWELLINGS)


# --------------------------------------------------------------------------- controls
def age_band_controls(controls: pd.DataFrame) -> tuple:
    """``(backbone person controls at 100 m, fine_teen)`` of the variant ``controls.csv`` carries."""
    fine_teen = part_a.fine_teen_age_bands_of(controls)
    backbone, _ = part_b.backbone_band_map(fine_teen)
    return backbone, fine_teen


def move_age_rows_to_1km(controls: pd.DataFrame, table_1km_columns) -> tuple:
    """``(new controls, removed rows, added rows)``: the 22 band rows rewritten in place at 1 km."""
    backbone, _ = age_band_controls(controls)
    band_of = {f"{c.name}{mid.SUFFIX_100M}": c.name for c in backbone}
    is_age = controls["control_field"].isin(band_of)
    if len(backbone) != AGE_SEX_CONTROL_COUNT or int(is_age.sum()) != AGE_SEX_CONTROL_COUNT:
        raise ValueError(f"{CONTROLS}: {int(is_age.sum())} age x sex row(s) at 100 m found "
                         f"({len(backbone)} bands in the variant), expected {AGE_SEX_CONTROL_COUNT}")
    bands = controls.loc[is_age, "control_field"].map(band_of)
    absent = [f"{b}{mid.SUFFIX_1KM}" for b in bands if f"{b}{mid.SUFFIX_1KM}" not in set(table_1km_columns)]
    if absent:
        raise ValueError(f"{TABLE_1KM} lacks the 1 km band column(s) {absent}")
    clash = sorted(set(bands + mid.SUFFIX_1KM) & set(controls["control_field"]))
    if clash:
        raise ValueError(f"{CONTROLS} already holds 1 km band row(s) {clash}")
    new = controls.copy()
    new.loc[is_age, "geography"] = cs.GEO_1KM
    new.loc[is_age, "target"] = bands + mid.SUFFIX_1KM + "_target"
    new.loc[is_age, "control_field"] = bands + mid.SUFFIX_1KM
    return new, controls[is_age], new[is_age]


def group_catalog() -> dict:
    """The catalog controls of the three household groups."""
    size_names = {name for name, _, _ in cs._TIER1_HH_SIZE_ENTRIES}
    size = [c for c in cs.tier1_controls() if c.name in size_names]
    if len(size) != 6 or any(c.name == cs.HH_TOTAL_CENSUS_COLUMN for c in size):
        raise ValueError(f"the tier-1 catalog has {len(size)} household-size controls, expected 6")
    tier2 = cs.tier2_controls()
    tenure = [c for c in tier2 if c.name in {name for name, _ in cs._TIER2_TENURE_ENTRIES}]
    building = [c for c in tier2 if c.name in {name for name, _, _ in cs._TIER2_BUILDING_TYPE_ENTRIES}]
    return {GROUP_SIZE: size, GROUP_TENURE: tenure, GROUP_BUILDING: building}


def active_groups(controls: pd.DataFrame, table_100m_columns) -> tuple:
    """``(active {group: catalog controls}, skipped {group: reason})``.

    A group is active when all its ``_ZENSUS100m`` columns are in the 100 m table and in
    ``controls.csv``, skipped when none is; a partial group raises (a part cannot be rescaled).
    """
    catalog = group_catalog()
    skipped = {}
    try:
        catalog[GROUP_AGE] = age_band_controls(controls)[0]
    except ValueError as error:
        skipped[GROUP_AGE] = f"no person backbone variant in {CONTROLS}: {error}"
    fields, present = set(controls["control_field"]), set(table_100m_columns)
    active = {}
    for group, members in catalog.items():
        found = [c for c in members if f"{c.name}{mid.SUFFIX_100M}" in fields & present]
        if not found:
            skipped[group] = f"no {mid.SUFFIX_100M} column in both {TABLE_100M} and {CONTROLS}"
        elif len(found) != len(members):
            raise ValueError(f"group {group}: only {len(found)} of {len(members)} columns in both "
                             f"{TABLE_100M} and {CONTROLS}")
        else:
            active[group] = members
    return active, skipped


# --------------------------------------------------------------------------- float values
def build_float_cells(args, batch_ids, active: dict) -> tuple:
    """``(batch cells, report)``: the unrounded group values the stage integerises, batch cells only.

    Columns: ``ZENSUS100m``, the household total and the base names of the active groups' controls,
    rows in the loader's order. Computed on all model cells of ``--kreise`` (module docstring).
    """
    catalog = [c for members in active.values() for c in members]
    load = [cs.HH_TOTAL_CENSUS_COLUMN, *cs.source_columns_union(catalog)]
    if GROUP_TENURE in active:
        load.append(cs.OWNER_QUOTE_DWELLING_COLUMN)
    cells, filter_report = part_b.load_kreis_cells(args.cells_100m, args.kreise, list(dict.fromkeys(load)))
    cells = prepared_cells.add_aggregated_controls(cells, cs.build_aggregation_map(catalog))
    report = {"zgb_cell_filter": filter_report,
              "building_type_basis": getattr(args, "building_type_basis", BASIS_FLOAT_DWELLINGS)}
    if GROUP_BUILDING in active and report["building_type_basis"] == BASIS_FLOAT_DWELLINGS:
        # As stage._add_aggregated_control_columns: the Kreis level of the share fallback from the ARS,
        # on a copy.
        columns = [c.name for c in active[GROUP_BUILDING]]
        work = cells.copy()
        work[folders.GEO_KREIS] = stage.derive_geo_kreis_from_ars(work[mid._ARS_COLUMN])
        work, report["building_type_rescale"] = (
            building_type_targets.rescale_building_type_to_households_with_report(work, columns))
        cells = cells.copy()
        for column in columns:
            cells[column] = work[column]
    if GROUP_TENURE in active:
        cells, report["tenure_owner_limit"] = stage._limit_owner_targets_to_dwellings(
            cells, "catalog", "mid", STAGE_CONTROL_TIERS, enabled=True,
            min_households_outside=args.tenure_min_households_outside)
        if report["tenure_owner_limit"] is None:
            raise ValueError("the owner limit did not run: the catalog has no active tenure control")
    batch = part_b.batch_cells_in_loader_order(cells, batch_ids)
    keep = [CELL_ID, cs.HH_TOTAL_CENSUS_COLUMN, *dict.fromkeys(c.name for c in catalog)]
    return batch[keep], report


def building_type_arm2_floats(batch: pd.DataFrame, crosswalk: pd.DataFrame, base_100m: pd.DataFrame,
                              columns) -> tuple:
    """``(frame, report)``: unrounded building-type values of the basis ``arm2_integer_dwellings``.

    ``batch`` carries the FLOAT dwelling sums of ``columns`` (no household rescale) in loader order.
    Step 1 integerises them within the 1 km parents of the batch (``mid.build_control_totals``, as the
    production run wrote the folders); step 2 rescales those integers on the batch's cells in the
    order of the base 100 m table to its integer household total with
    ``building_type_targets.rescale_building_type_to_households`` (parents ``ZENSUS1km``, ``KREIS`` of
    the crosswalk), exactly as ``prepare_arm2.rescale_targets`` of popsim-ladder-arm2-2026-11-17 did
    before it integerised. Returns the frame in base-table order with ``ZENSUS100m``, ``ZENSUS1km`` and
    the float columns (suffixed ``_ZENSUS100m``); the report holds the integer dwelling and float sums.
    """
    dwellings = mid.build_control_totals(batch, crosswalk, list(columns))[CELL_ID].set_index(CELL_ID)
    household_field = f"{cs.HH_TOTAL_CENSUS_COLUMN}{mid.SUFFIX_100M}"
    fields = [f"{c}{mid.SUFFIX_100M}" for c in columns]
    frame = pd.DataFrame({CELL_ID: base_100m[CELL_ID].to_numpy(),
                          household_field: pd.to_numeric(base_100m[household_field]).to_numpy()})
    for column, field in zip(columns, fields):
        values = dwellings[field].reindex(frame[CELL_ID])
        if values.isna().any():
            raise ValueError(f"{int(values.isna().sum())} cell(s) of {TABLE_100M} have no dwelling value for {field}")
        frame[field] = values.to_numpy()
    geo = crosswalk[[CELL_ID, PARENT_ID, folders.GEO_KREIS]].drop_duplicates(CELL_ID)
    frame = frame.merge(geo, on=CELL_ID, how="left", validate="one_to_one")
    if frame[PARENT_ID].isna().any():
        raise ValueError(f"{TABLE_100M} holds 100 m cells that are not in {CROSSWALK}")
    dwelling_sum = float(frame[fields].to_numpy(dtype="float64").sum())
    frame = building_type_targets.rescale_building_type_to_households(
        frame, fields, household_total_col=household_field, parent_cols=(PARENT_ID, folders.GEO_KREIS))
    report = {"integer_dwelling_sum_100m": dwelling_sum,
              "float_sum_after_rescale_100m": float(frame[fields].to_numpy(dtype="float64").sum()),
              "household_total_100m": float(frame[household_field].sum())}
    return frame, report


def apply_arm2_building_type(batch: pd.DataFrame, frame: pd.DataFrame, columns) -> pd.DataFrame:
    """``batch`` with the building-type columns replaced by the floats of ``frame`` (matched by cell id)."""
    out = batch.copy()
    lookup = frame.set_index(CELL_ID)
    for column in columns:
        values = lookup[f"{column}{mid.SUFFIX_100M}"].reindex(out[CELL_ID])
        if values.isna().any():
            raise ValueError(f"{int(values.isna().sum())} batch cell(s) without an arm-2 building-type value")
        out[column] = values.to_numpy()
    return out


def rescale_to_households(values: pd.DataFrame, households, base_integers: pd.DataFrame, *, group: str) -> tuple:
    """``(rescaled values, report)``: each row ``v_i * H / sum(v)`` with the integer household total ``H``.

    ``values`` (floats; NaN read as 0 and counted, as ``mid.build_control_totals``), ``households``
    and ``base_integers`` (the base folder's integers of the same columns) are aligned row by row.
    A row with ``H`` 0 becomes 0. A row with ``H`` > 0 but a float sum of 0 cannot be rescaled: it keeps
    the base integers (FALLBACK, counted with its households and logged as a rate). Pure apart from
    the log line.
    """
    numbers = values.astype("float64")
    nan_values = int(numbers.isna().to_numpy().sum())
    v = numbers.fillna(0.0).to_numpy()
    h = pd.Series(households).astype("float64").to_numpy()
    if np.isnan(h).any() or (h < 0).any():
        raise ValueError(f"{group}: the household totals are missing or negative")
    base = np.asarray(base_integers, dtype="float64")
    total = v.sum(axis=1)
    with_households = h > 0
    primary = with_households & (total > 0)
    fallback = with_households & ~(total > 0)
    zeroed = ~with_households & (total > 0)
    out = np.zeros_like(v)
    out[primary] = v[primary] * (h[primary] / total[primary])[:, None]
    out[fallback] = base[fallback]
    n_with = int(with_households.sum())
    rate = float(fallback.sum()) / n_with if n_with else 0.0
    report = {"cells_seen": int(len(v)), "cells_with_households": n_with,
              "primary_cells": int(primary.sum()), "fallback_cells": int(fallback.sum()),
              "fallback_households": float(h[fallback].sum()), "fallback_rate": rate,
              "cells_without_households_zeroed": int(zeroed.sum()),
              "float_sum_dropped_without_households": float(total[zeroed].sum()),
              "nan_values_read_as_zero": nan_values}
    level = logging.WARNING if rate > RESCALE_FALLBACK_WARN_RATE else logging.INFO
    logger.log(level, "[prepare] %s rescale: primary %d/%d (%.2f%%), fallback (base integers kept) %d "
               "(%.2f%%, %.0f households)", group, report["primary_cells"], n_with,
               100.0 * (1.0 - rate) if n_with else 0.0, report["fallback_cells"], 100.0 * rate,
               report["fallback_households"])
    return pd.DataFrame(out, index=values.index, columns=values.columns), report


def move_age_floats(batch: pd.DataFrame, crosswalk: pd.DataFrame, age_cols, move_persons_max: float) -> tuple:
    """``(moved float bands, report)``: the float part of ``mid.build_control_totals``' move.

    NaN read as 0 and counted, the household total integerised within the 1 km parents, then
    ``move_persons_to_household_cells`` with the bands as person and threshold columns.
    """
    work = batch[[CELL_ID, cs.HH_TOTAL_CENSUS_COLUMN, *age_cols]].copy()
    work[PARENT_ID] = work[CELL_ID].map(crosswalk.set_index(CELL_ID)[PARENT_ID])
    if work[PARENT_ID].isna().any():
        raise ValueError(f"{int(work[PARENT_ID].isna().sum())} batch cell(s) without a 1 km parent in {CROSSWALK}")
    nan_values = {}
    for column in [cs.HH_TOTAL_CENSUS_COLUMN, *age_cols]:
        n_nan = int(work[column].isna().sum())
        if n_nan:
            nan_values[column] = n_nan
            work[column] = work[column].fillna(0)
    published = work[cs.HH_TOTAL_CENSUS_COLUMN].astype("float64")
    households = ctrl.integerize_within_parents(work, value_col=cs.HH_TOTAL_CENSUS_COLUMN, parent_col=PARENT_ID)
    moved, report = move_persons_to_household_cells(
        work, parent_col=PARENT_ID, households=households, person_cols=list(age_cols),
        threshold_cols=list(age_cols), max_persons=float(move_persons_max), published_households=published)
    report["nan_values_read_as_zero"] = nan_values
    return moved[list(age_cols)].astype("float64"), report


def self_check(batch: pd.DataFrame, crosswalk: pd.DataFrame, base_100m: pd.DataFrame, active: dict,
               move_persons_max: float, building_type_frame: pd.DataFrame | None = None) -> dict:
    """Integerise the float values as the pipeline did and count the cells that differ from the base.

    With ``building_type_frame`` (basis ``arm2_integer_dwellings``) the building-type floats are
    integerised in that frame's row order (the base table's) with ``controls.integerize_within_parents``,
    as ``prepare_arm2.rescale_targets`` did.
    """
    base = base_100m.set_index(CELL_ID)
    record = {"groups": {}, "problems": []}
    for group, members in active.items():
        columns = [c.name for c in members]
        if group == GROUP_BUILDING and building_type_frame is not None:
            built_frame = pd.DataFrame({CELL_ID: building_type_frame[CELL_ID].to_numpy()})
            for column in columns:
                field = f"{column}{mid.SUFFIX_100M}"
                built_frame[field] = ctrl.integerize_within_parents(
                    building_type_frame, value_col=field, parent_col=PARENT_ID).to_numpy()
            tables = {CELL_ID: built_frame}
        elif group == GROUP_AGE:
            # The move and the integerisation run per column, so the age bands come out the same
            # without the employed columns the stage moves with them.
            tables, _ = part_b.rebuild_person_tables(batch, crosswalk, age_cols=columns, employed_cols=[],
                                                     move_persons_max=move_persons_max)
        else:
            tables = mid.build_control_totals(batch, crosswalk, columns)
        built = tables[CELL_ID].set_index(CELL_ID)
        differing = {}
        for column in columns:
            name = f"{column}{mid.SUFFIX_100M}"
            old = pd.to_numeric(base[name]).reindex(built.index).to_numpy(dtype="float64")
            count = int((built[name].to_numpy(dtype="float64") != old).sum())
            if count:
                differing[name] = count
                record["problems"].append(f"self-check {group}: {name} differs from the base folder in "
                                          f"{count} cell(s)")
        record["groups"][group] = {"columns_checked": len(columns), "differing_cells": differing}
    record["ok"] = not record["problems"]
    return record


def float_group_values(batch: pd.DataFrame, crosswalk: pd.DataFrame, base_100m: pd.DataFrame, active: dict,
                       move_persons_max: float) -> tuple:
    """``(values indexed by ZENSUS100m, per-group report)``: the float columns to write at 100 m."""
    base = base_100m.set_index(CELL_ID).loc[batch[CELL_ID]]
    household_column = f"{cs.HH_TOTAL_CENSUS_COLUMN}{mid.SUFFIX_100M}"
    if household_column not in base.columns:
        raise ValueError(f"{TABLE_100M} lacks the household total {household_column}")
    households = pd.to_numeric(base[household_column]).to_numpy()
    values, reports = pd.DataFrame({CELL_ID: batch[CELL_ID].to_numpy()}), {}
    for group, members in active.items():
        columns = [c.name for c in members]
        base_integers = base[[f"{c}{mid.SUFFIX_100M}" for c in columns]].apply(pd.to_numeric).to_numpy()
        if group == GROUP_AGE:
            new, report = move_age_floats(batch, crosswalk, columns, move_persons_max)
            report = {"move": report}
        else:
            new, report = rescale_to_households(batch[columns].reset_index(drop=True), households,
                                                base_integers, group=group)
            report = {"rescale": report}
        new = new.reset_index(drop=True)
        report["float_sum_100m"] = float(new.to_numpy().sum())
        report["integer_sum_100m_base"] = float(base_integers.sum())
        for column in columns:
            values[column] = new[column].to_numpy()
        reports[group] = report
    return values.set_index(CELL_ID), reports


def float_text(values) -> list:
    """Full-precision text (``repr``) of float values, no rounding."""
    return [repr(float(v)) for v in values]


# --------------------------------------------------------------------------- steps
def step_age_sex(state: dict, log: dict) -> None:
    controls, removed, added = move_age_rows_to_1km(state["controls"], state["tables"][TABLE_1KM].columns)
    state["controls"] = controls
    part_b._allow(state["allowed"], CONTROLS, "removed_rows", map(tuple, removed.to_numpy().tolist()))
    part_b._allow(state["allowed"], CONTROLS, "added_rows", map(tuple, added.to_numpy().tolist()))
    log["step_report"] = {"moved_control_fields": added["control_field"].tolist()}


def step_float(state: dict, args, log: dict) -> None:
    t100, t1km = state["tables"][TABLE_100M], state["tables"][TABLE_1KM]
    active, skipped = active_groups(state["controls"], t100.columns)
    if not active:
        raise ValueError(f"no group to replace: {skipped}")
    batch, build_report = build_float_cells(args, t100[CELL_ID], active)
    building_type_frame = None
    if GROUP_BUILDING in active and args.building_type_basis == BASIS_ARM2:
        columns = [c.name for c in active[GROUP_BUILDING]]
        building_type_frame, build_report["building_type_arm2"] = building_type_arm2_floats(
            batch, state["crosswalk"], t100, columns)
        batch = apply_arm2_building_type(batch, building_type_frame, columns)
    check = self_check(batch, state["crosswalk"], t100, active, args.move_persons_max,
                       building_type_frame=building_type_frame)
    record = {"groups": {g: [c.name for c in m] for g, m in active.items()}, "groups_skipped": skipped,
              "build_report": build_report, "self_check": check, "move_persons_max": args.move_persons_max}
    log["step_report"] = record
    if not check["ok"]:
        state["problems"].extend(check["problems"])
        return  # nothing is written: the floats are not the values the pipeline rounded
    values, record["group_reports"] = float_group_values(batch, state["crosswalk"], t100, active,
                                                         args.move_persons_max)
    for group, report in record["group_reports"].items():
        rate = report.get("rescale", {}).get("fallback_rate", 0.0)
        if rate > RESCALE_FALLBACK_WARN_RATE:
            state["problems"].append(f"{group}: rescale fallback rate {rate:.4f} above {RESCALE_FALLBACK_WARN_RATE}")
    parent_of = state["crosswalk"].set_index(CELL_ID)[PARENT_ID]
    parent_sums = values.groupby(values.index.map(parent_of)).sum()
    # 1 km sums against the base 1 km integers. The age bands are not rescaled, so their float parent
    # sums must stay within rounding of the base (a parent with cells outside the batch, or floats that
    # are not what was rounded, would break that): 1 or more is a problem. The household groups are
    # rescaled to the integer household total by design, so their 1 km sums move; the largest change is
    # reported.
    base_1km = t1km.set_index(PARENT_ID)
    age_columns = {c.name for c in active.get(GROUP_AGE, [])}
    far, max_change = {}, {}
    for column in values.columns:
        name = f"{column}{mid.SUFFIX_1KM}"
        old = pd.to_numeric(base_1km[name]).reindex(parent_sums.index).to_numpy(dtype="float64")
        diff = np.abs(parent_sums[column].to_numpy() - old)
        max_change[name] = float(diff.max()) if len(diff) else 0.0
        if column in age_columns and int((diff >= 1.0).sum()):
            far[name] = int((diff >= 1.0).sum())
    record["max_abs_1km_change_against_base"] = max_change
    record["age_parents_off_by_one_or_more"] = far
    if far:
        state["problems"].append(f"1 km sums of the float age bands differ from the base 1 km integers by 1 or more: {far}")
        return
    for table, (key, suffix) in GRID_TABLES.items():
        frame = state["tables"][table]
        source = values if key == CELL_ID else parent_sums
        for column in values.columns:
            name = f"{column}{suffix}"
            new = source[column].reindex(frame[key])
            if new.isna().any():
                raise ValueError(f"{table}: {int(new.isna().sum())} row(s) without a value for {name}")
            frame[name] = float_text(new.to_numpy())
            part_b._allow(state["allowed"], table, "changed_columns", [name])


# --------------------------------------------------------------------------- main
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, type=Path, help="base batch folder (arm 8)")
    parser.add_argument("--out", required=True, type=Path, help="new batch folder (must not exist)")
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument("--kreise", help="comma list of the model's Kreis codes (float step)")
    parser.add_argument("--cells-100m", type=Path, help="prepared 100 m cell parquet (float step)")
    parser.add_argument("--move-persons-max", type=float, default=part_b.MOVE_PERSONS_MAX_DEFAULT,
                        help="persons bound of a household-less donor cell (default: the stage's)")
    parser.add_argument("--tenure-min-households-outside", type=float,
                        default=tenure_targets.DEFAULT_MIN_HOUSEHOLDS_OUTSIDE,
                        help="households outside dwellings before the owner limit acts (default: the stage's)")
    parser.add_argument("--building-type-basis", choices=BUILDING_TYPE_BASES, default=BASIS_ARM2,
                        help="basis of the unrounded building-type values (see BASIS_* in the module); "
                        f"default {BASIS_ARM2}, the construction of the ladder folders")
    parser.add_argument("--commit", required=True, help="eqasim-bs commit of the library code")
    return parser.parse_args(argv)


def check_arguments(args) -> str | None:
    """The reason the arguments are unusable, or None."""
    if args.step == STEP_FLOAT:
        absent = [n for n in ("kreise", "cells_100m") if getattr(args, n) is None]
        if absent:
            return f"{STEP_FLOAT} needs --{', --'.join(a.replace('_', '-') for a in absent)}"
        if args.move_persons_max <= 0 or args.tenure_min_households_outside <= 0:
            return "--move-persons-max and --tenure-min-households-outside must be positive"
    if args.kreise is not None:
        args.kreise = [k.strip() for k in args.kreise.split(",") if k.strip()]
    if args.out.exists():
        return f"{args.out} exists"
    for sub in ("configs", "data"):
        if not (args.base / sub).is_dir():
            return f"{args.base / sub} is not a directory"
    return None


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    reason = check_arguments(args)
    if reason:
        print(f"refuse: {reason}", file=sys.stderr)
        return part_b.EXIT_BAD_ARGUMENTS
    out = args.out
    log = {"arguments": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
           "step": args.step, "commit": args.commit, "step_report": None}
    state = {"out": out, "allowed": {}, "problems": []}
    files = {}
    # Any failure ends in a log with the error text and exit 1; a half-built folder never stays
    # without a log.
    try:
        out.mkdir(parents=True)
        shutil.copytree(args.base / "configs", out / "configs")
        shutil.copytree(args.base / "data", out / "data")
        (out / "output").mkdir()
        log["inputs"] = ({"cells_100m": {"path": str(args.cells_100m),
                                         "sha256": part_b._sha_or_none(Path(args.cells_100m))}}
                         if args.cells_100m is not None else {})
        terminators = {n: part_b.line_terminator(out / n) for n in (CONTROLS, TABLE_100M, TABLE_1KM)}
        state["controls"] = part_b.read_text_table(out / CONTROLS)
        state["tables"] = {n: part_b.read_text_table(out / n) for n in GRID_TABLES}
        state["crosswalk"] = part_b.read_text_table(out / CROSSWALK)
        if args.step == STEP_AGE_SEX:
            step_age_sex(state, log)
        else:
            step_float(state, args, log)
        for name, frame in {CONTROLS: state["controls"], **state["tables"]}.items():
            if not frame.equals(part_b.read_text_table(args.base / name)):
                part_b.write_text_table(frame, out / name, terminators[name])
        files, unexpected = part_b.compare_folders(args.base, out, state["allowed"])
        state["problems"].extend(unexpected)
    except Exception as error:  # noqa: BLE001 - recorded in the log, then exit 1
        logger.exception("prepare failed")
        state["problems"].append(f"{type(error).__name__}: {error}")
    log.update({"files": files, "problems": state["problems"], "ok": not state["problems"]})
    log_path = out / "prepare_log.json" if out.is_dir() else out.with_name(out.name + ".prepare_log.json")
    log_path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    changed = {n: e.get("columns_changed") or "rows/file" for n, e in files.items() if e["changed"]}
    print(json.dumps({"out": str(out), "step": args.step, "changed": changed,
                      "problems": state["problems"]}, indent=2, default=str))
    return part_b.EXIT_UNEXPECTED_CHANGE if state["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
