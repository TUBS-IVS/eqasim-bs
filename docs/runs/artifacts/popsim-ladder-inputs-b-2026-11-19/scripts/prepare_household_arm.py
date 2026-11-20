"""Prepare one batch folder of a household-universe ladder arm (issue #659, Part B, Task B6).

Copies ``configs/`` and ``data/`` of a base batch folder (the previous arm) to ``--out``, creates an
empty ``output/`` and applies the ``--steps`` in the given order on that one copy, each with the
branch's library functions on the real inputs:

* ``family_none``: removes the four ``..._Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m`` rows (the
  ``hh_type5`` expressions, ``control_spec._TIER1_HH_TYPE_ENTRIES``) from ``configs/controls.csv``.
  No data table changes.
* ``family_minor_child``: removes the same four rows and adds the two rows of
  ``control_spec.minor_child_controls()`` rendered by ``control_spec.render_catalog_csv`` (seed
  ``mid``), with the importance the removed rows carry (the ``hhtype`` importance group of
  ``control_spec.importance_group_for_field``, which the minor-child controls share; the four removed
  rows must agree). Data: the per-cell targets come from ``stage._add_minor_child_targets`` (the
  stage's own call of ``minor_child_targets.per_cell_targets``) on the model cells (all cells of the
  ``--kreise``, as the stage computes them: the Kreis fallback share pools all squares of a Kreis),
  are integerised within the 1 km parents by ``mid.build_control_totals`` on the batch's cells and
  appended as ``<col>_ZENSUS100m`` / ``<col>_ZENSUS1km`` to the two grid tables. Seed:
  ``hh_minor_class`` is added to ``data/seed_households.csv`` from ``data/seed_persons.csv`` with
  ``seed.derive_hh_minor_class`` (person ``H_ID`` cast to the integer household id; a household with
  no class is counted, never dropped).
* ``move_persons``: rebuilds the 100 m person columns the stage moves (the age x sex bands of the
  person backbone, ``control_spec.tier0_backbone_catalog``, built by
  ``prepared_cells.add_aggregated_controls`` from their census sources; the ``EMPLOYED_*_agg`` columns
  present in the batch's 100 m table, built by ``stage._inject_employment_grid_columns`` with the
  given census inputs) from the model cells and calls ``mid.build_control_totals`` on the batch's
  cells with the stage's move arguments (``HouseholdUniverseSettings.run_popsim_mid_kwargs``:
  household total ``control_spec.HH_TOTAL_CENSUS_COLUMN``, the bands as threshold columns,
  ``--move-persons-max``). The person columns of both grid tables are replaced.
  ``--self-check`` runs the same rebuild WITHOUT the move: every file must then be byte-identical to
  the base folder (exit 1 and a column diff otherwise), which proves the driver rebuilds what the
  pipeline built. The step writes ``household_cells_check.json`` into ``--out``: the household-less
  cells below the bound that still carry person targets (``cells_left``, must be 0; the donors in
  squares without households are counted apart) and the largest change of each person column's 1 km
  sum (must be 0).

The cells go into the integerisation in the loader's row order (the prepared parquet's order,
restricted to the batch's cells), as in ``prepare_tenure_arm.py`` of the tenure ladder.

Reused by import (no copy): ``prepare_input_arm.load_model_cells``, ``fine_teen_age_bands_of`` and
``_sha256`` of Part A (``popsim-ladder-inputs-a-2026-11-18/scripts``).

``prepare_log.json`` in ``--out``: arguments, steps, library commit, sha256 of every input file,
the step reports, per file whether it changed and per column / row what changed. Exit 0: only the
steps' rows / columns changed; 1: anything else changed, a check failed or a step raised; 2: unusable
arguments (``--out`` exists, contradicting steps, a missing input for a step).

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python prepare_household_arm.py --base <arm dir>/batch_000 --out <new arm dir>/batch_000 \
      --steps family_minor_child,move_persons --kreise 03101,03102,... \
      --cells-100m <zensus100m prepared parquet> --cells-1km <zensus1km prepared parquet> \
      --kreis-controls-dir <dir of kreis_erwerbsstatus.parquet> --data-path <pipeline data_path> \
      --employment-age-shape-source zensus2022_kreis_by_sex --commit <eqasim-bs sha> [--self-check]
"""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from braunschweig.popsim import cells as cellmod
from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import employment_grid, mid, prepared_cells, seed
from braunschweig.popsim import stage
from braunschweig.popsim.stage import config_keys

PART_A_SCRIPTS = Path(__file__).resolve().parents[2] / "popsim-ladder-inputs-a-2026-11-18" / "scripts"
if str(PART_A_SCRIPTS) not in sys.path:
    sys.path.append(str(PART_A_SCRIPTS))
import prepare_input_arm as part_a  # noqa: E402  (Part A's helpers, imported, not copied)

logger = logging.getLogger("prepare_household_arm")

STEPS = ("family_none", "family_minor_child", "move_persons")
CELL_ID = "ZENSUS100m"
PARENT_ID = "ZENSUS1km"
CONTROLS = "configs/controls.csv"
TABLE_100M = "data/control_totals_ZENSUS100m.csv"
TABLE_1KM = "data/control_totals_ZENSUS1km.csv"
CROSSWALK = "data/geo_cross_walk.csv"
SEED_HOUSEHOLDS = "data/seed_households.csv"
SEED_PERSONS = "data/seed_persons.csv"
SEED_HOUSEHOLD_ID = "H_ID"
SEED_AGE = "HP_ALTER"
MINOR_CLASS_COLUMN = "hh_minor_class"
GRID_TABLES = {TABLE_100M: (CELL_ID, mid.SUFFIX_100M), TABLE_1KM: (PARENT_ID, mid.SUFFIX_1KM)}
CELLS_CHECK_FILE = "household_cells_check.json"
EXIT_UNEXPECTED_CHANGE = 1
EXIT_BAD_ARGUMENTS = 2
# The stage's default bound of the move (config key household_less_cell_max_persons).
MOVE_PERSONS_MAX_DEFAULT = config_keys.HOUSEHOLD_LESS_CELL_MAX_PERSONS_DEFAULT


# --------------------------------------------------------------------------- text tables
def read_text_table(path: Path) -> pd.DataFrame:
    """A CSV as text (no number parsing, empty cells stay empty): unchanged cells stay byte-equal."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def line_terminator(path: Path) -> str:
    """The line ending of an existing CSV, so a rewrite does not change it."""
    return "\r\n" if b"\r\n" in path.read_bytes()[:65536] else "\n"


def write_text_table(frame: pd.DataFrame, path: Path, terminator: str) -> None:
    frame.to_csv(path, index=False, lineterminator=terminator)


def int_text(values) -> np.ndarray:
    """Integer counts as the text ``build_control_totals`` tables are written with."""
    return pd.Series(values).astype("int64").astype(str).to_numpy()


# --------------------------------------------------------------------------- controls.csv
def family_type_fields() -> set:
    """``control_field`` of the four census family-type rows (100 m)."""
    return {f"{base}_{cs.GEO_100M}" for base, _ in cs._TIER1_HH_TYPE_ENTRIES}


def split_family_rows(controls: pd.DataFrame) -> tuple:
    """``(kept, removed)``: ``ValueError`` unless exactly the four hh_type5 family rows are found."""
    is_family = controls["control_field"].isin(family_type_fields())
    removed = controls[is_family]
    if len(removed) != len(cs._TIER1_HH_TYPE_ENTRIES):
        raise ValueError(f"{CONTROLS}: {len(removed)} census family-type row(s) found, expected "
                         f"{len(cs._TIER1_HH_TYPE_ENTRIES)} ({sorted(family_type_fields())})")
    if not removed["expression"].str.contains("hh_type5").all():
        raise ValueError(f"{CONTROLS}: a family-type row does not read hh_type5: "
                         f"{removed['expression'].tolist()}")
    return controls[~is_family].reset_index(drop=True), removed


def minor_child_rows(importance: str) -> pd.DataFrame:
    """The two minor-child rows as the library renders them, with the batch's hhtype importance."""
    rows = cs.render_catalog_csv(cs.controls_for_seed(cs.minor_child_controls(), "mid"), "mid")
    groups = set(rows["control_field"].map(cs.importance_group_for_field))
    if groups != {"hhtype"}:
        raise ValueError(f"the minor-child controls are not in the hhtype importance group: {groups}")
    rows["importance"] = importance
    return rows.astype(str)


def hhtype_importance(removed: pd.DataFrame) -> str:
    values = sorted(set(removed["importance"]))
    if len(values) != 1:
        raise ValueError(f"the four family-type rows carry different importances {values}; the "
                         "minor-child rows cannot take one hhtype importance")
    return values[0]


# --------------------------------------------------------------------------- model cells
def load_kreis_cells(parquet_path: Path, kreise, columns) -> tuple:
    """The model cells (the cells of ``kreise``) in the parquet's row order, with ``ZENSUS1km``.

    Columns: ``ZENSUS100m``, the ARS, ``columns`` (cleaned names; absent ones raise, Part A's
    ``load_model_cells``) and ``ZENSUS1km`` (``cells.derive_1km_parent_id``, as
    ``mid.load_control_cells``). The Kreis filter is ``mid.filter_zgb_cells_with_report``.
    Returns ``(cells, filter_report)``.
    """
    raw_names = pq.ParquetFile(parquet_path).schema.names
    clean_to_raw = {}
    for raw in raw_names:
        clean_to_raw.setdefault(prepared_cells.clean_col_name(raw), raw)
    if mid._ARS_COLUMN not in clean_to_raw:
        raise ValueError(f"{parquet_path}: no ARS column {mid._ARS_COLUMN!r}")
    keys = pq.read_table(parquet_path, columns=[raw_names[0], clean_to_raw[mid._ARS_COLUMN]]).to_pandas()
    keys.columns = [CELL_ID, mid._ARS_COLUMN]
    keys[CELL_ID] = keys[CELL_ID].astype(str)
    keys, report = mid.filter_zgb_cells_with_report(keys, kreise)
    columns = [c for c in dict.fromkeys([mid._ARS_COLUMN, *columns])]
    cells = part_a.load_model_cells(parquet_path, keys[CELL_ID], columns)
    cells = cells.set_index(CELL_ID).loc[keys[CELL_ID].to_numpy()].reset_index()  # parquet order
    cells[PARENT_ID] = cells[CELL_ID].map(cellmod.derive_1km_parent_id)
    return cells, report


def batch_cells_in_loader_order(cells: pd.DataFrame, batch_ids) -> pd.DataFrame:
    """The batch's cells in the loader's order; ``ValueError`` when one is not a model cell."""
    wanted = set(map(str, batch_ids))
    out = cells[cells[CELL_ID].isin(wanted)].reset_index(drop=True)
    missing = sorted(wanted - set(out[CELL_ID]))
    if missing:
        raise ValueError(f"{len(missing)} cell(s) of the batch are not model cells of the given "
                         f"Kreise (first: {missing[0]})")
    return out


class StageConfig:
    """The three config values ``stage._inject_employment_grid_columns`` reads (one-argument config)."""

    def __init__(self, values: dict):
        self._values = values

    def config(self, key):
        return self._values[key]


# --------------------------------------------------------------------------- person columns
def person_columns(controls: pd.DataFrame, table_100m_columns) -> tuple:
    """``(age_cols, employed_cols, fine_teen)``: the columns the stage moves (base names).

    The bands are the 100 m person controls of the backbone variant ``controls.csv`` carries; the
    employed columns are ``control_spec.employment_grid_controls`` present in the 100 m table.
    """
    fine_teen = part_a.fine_teen_age_bands_of(controls)
    backbone = [c for c in cs.tier0_backbone_catalog(fine_teen_age_bands=fine_teen)
                if c.geography == cs.GEO_100M and c.seed_table == cs.SEED_TABLE_PERSONS]
    age_cols = [c.name for c in backbone]
    present = set(table_100m_columns)
    absent = [c for c in age_cols if f"{c}{mid.SUFFIX_100M}" not in present]
    if absent:
        raise ValueError(f"{TABLE_100M} lacks the band column(s) {absent}")
    employed_cols = [c.name for c in cs.employment_grid_controls()
                     if f"{c.name}{mid.SUFFIX_100M}" in present]
    return age_cols, employed_cols, fine_teen


def backbone_band_map(fine_teen: bool) -> tuple:
    """``(backbone person controls at 100 m, their aggregation map)`` as the stage builds the bands."""
    backbone = [c for c in cs.tier0_backbone_catalog(fine_teen_age_bands=fine_teen)
                if c.geography == cs.GEO_100M and c.seed_table == cs.SEED_TABLE_PERSONS]
    return backbone, cs.build_aggregation_map(backbone)


def employment_load_columns() -> list:
    """The single-year age columns ``employment_grid.per_cell_employment_targets`` sums (its defaults)."""
    params = inspect.signature(employment_grid.per_cell_employment_targets).parameters
    lo, hi = params["min_age"].default, params["single_year_max"].default
    return [f"{sex}_AGE_{year}" for sex in ("M", "F") for year in range(lo, hi + 1)]


def rebuild_person_tables(batch_cells: pd.DataFrame, crosswalk: pd.DataFrame, *, age_cols,
                          employed_cols, move_persons_max) -> tuple:
    """The pure core of ``move_persons``: ``(tables, move_report)`` of ``mid.build_control_totals``.

    ``move_persons_max`` None is the plain build (the self-check); else the stage's move arguments
    (``HouseholdUniverseSettings.run_popsim_mid_kwargs``) with the household total
    ``control_spec.HH_TOTAL_CENSUS_COLUMN`` in the base columns (its integers drive the move).
    """
    person_cols = [*age_cols, *employed_cols]
    if move_persons_max is None:
        return mid.build_control_totals(batch_cells, crosswalk, person_cols), None
    report: dict = {}
    tables = mid.build_control_totals(
        batch_cells, crosswalk, [cs.HH_TOTAL_CENSUS_COLUMN, *person_cols],
        household_total_col=cs.HH_TOTAL_CENSUS_COLUMN, person_cols=person_cols,
        threshold_cols=list(age_cols), move_persons_max=float(move_persons_max), report=report)
    return tables, report


def household_cells_check(batch_cells, crosswalk, tables, base_1km: pd.DataFrame, *, age_cols,
                          employed_cols, move_persons_max) -> dict:
    """The cell check of the move on the integer tables (module docstring).

    ``cells_left``: cells with 0 integer households, float persons over the bands in
    (0, ``move_persons_max``) before the move, a square with households, and integer persons left
    after the move (must be 0). ``cells_left_in_squares_without_households``: the same in squares
    without any household (the counted donors that cannot move). ``max_abs_parent_sum_change``: per
    person column, the largest change of its 1 km sum against the base table (must be 0).
    """
    t100 = tables[CELL_ID]
    parent = t100[CELL_ID].map(crosswalk.set_index(CELL_ID)[PARENT_ID])
    households = t100[f"{cs.HH_TOTAL_CENSUS_COLUMN}{mid.SUFFIX_100M}"].astype(float)
    parent_households = households.groupby(parent).transform("sum")
    before = batch_cells.set_index(CELL_ID).loc[t100[CELL_ID], list(age_cols)].fillna(0.0)
    before = before.sum(axis=1).to_numpy()
    after = t100[[f"{c}{mid.SUFFIX_100M}" for c in [*age_cols, *employed_cols]]].sum(axis=1)
    candidate = (households.to_numpy() == 0) & (before > 0) & (before < move_persons_max)
    left = candidate & (after.to_numpy() > 0)
    in_empty_square = parent_households.to_numpy() == 0
    new_1km = tables[PARENT_ID].set_index(PARENT_ID)
    old_1km = base_1km.set_index(PARENT_ID)
    changes = {}
    for col in [*age_cols, *employed_cols]:
        name = f"{col}{mid.SUFFIX_1KM}"
        old = pd.to_numeric(old_1km[name]).reindex(new_1km.index)
        changes[name] = float(np.max(np.abs(new_1km[name].to_numpy(dtype=float) - old.to_numpy(dtype=float))))
    return {
        "household_less_small_cells": int(candidate.sum()),
        "cells_left": int((left & ~in_empty_square).sum()),
        "cells_left_in_squares_without_households": int((left & in_empty_square).sum()),
        "max_abs_parent_sum_change": changes,
        "max_abs_parent_sum_change_all": max(changes.values()) if changes else 0.0,
    }


# --------------------------------------------------------------------------- seed
def add_minor_class(households: pd.DataFrame, persons: pd.DataFrame) -> tuple:
    """``(households with hh_minor_class, report)``; every household gets a class or is counted."""
    if MINOR_CLASS_COLUMN in households.columns:
        raise ValueError(f"{SEED_HOUSEHOLDS} already has {MINOR_CLASS_COLUMN}")
    person_ids = pd.to_numeric(persons[SEED_HOUSEHOLD_ID], errors="coerce")
    if person_ids.isna().any() or (person_ids != np.floor(person_ids)).any():
        raise ValueError(f"{SEED_PERSONS}: {SEED_HOUSEHOLD_ID} holds missing or non-integer ids")
    household_ids = pd.to_numeric(households[SEED_HOUSEHOLD_ID], errors="coerce")
    if household_ids.isna().any() or (household_ids != np.floor(household_ids)).any():
        raise ValueError(f"{SEED_HOUSEHOLDS}: {SEED_HOUSEHOLD_ID} holds missing or non-integer ids")
    classes = seed.derive_hh_minor_class(
        persons.assign(**{SEED_HOUSEHOLD_ID: person_ids.astype("int64")}),
        household_id_col=SEED_HOUSEHOLD_ID, age_col=SEED_AGE)
    household_ids = household_ids.astype("int64")
    labels = household_ids.map(classes)
    without_persons = int((~household_ids.isin(classes.index)).sum())
    out = households.copy()
    out[MINOR_CLASS_COLUMN] = labels.fillna("").to_numpy()
    counts = {str(k): int(v) for k, v in labels.value_counts(dropna=False).items() if pd.notna(k)}
    report = {"households": int(len(out)), "by_class": counts,
              "households_without_class": int(labels.isna().sum()),
              "of_which_without_persons": without_persons}
    logger.info("[prepare] hh_minor_class: %s", report)
    return out, report


# --------------------------------------------------------------------------- folder comparison
def _sha_or_none(path: Path):
    return part_a._sha256(path) if path.is_file() else None


def file_hashes(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): part_a._sha256(p)
            for sub in ("configs", "data") for p in sorted((root / sub).rglob("*")) if p.is_file()}


def compare_folders(base: Path, out: Path, allowed: dict) -> tuple:
    """Per-file change record of ``out`` against ``base`` and the unexpected changes.

    ``allowed``: ``{file: {"changed_columns": set, "added_columns": set, "removed_rows": set,
    "added_rows": set}}`` (rows as tuples of text, ``controls.csv`` only). A CSV in ``allowed`` is
    compared column by column (``controls.csv`` as a set of rows); every other file byte by byte.
    """
    base_hashes, out_hashes = file_hashes(base), file_hashes(out)
    files, unexpected = {}, []
    for name in sorted(set(base_hashes) | set(out_hashes)):
        entry = {"sha256_base": base_hashes.get(name), "sha256_out": out_hashes.get(name)}
        entry["changed"] = entry["sha256_base"] != entry["sha256_out"]
        files[name] = entry
        if not entry["changed"]:
            continue
        if entry["sha256_base"] is None or entry["sha256_out"] is None:
            unexpected.append(f"{name}: present on one side only")
            continue
        rule = allowed.get(name)
        if rule is None:
            unexpected.append(f"{name}: changed")
            continue
        before, after = read_text_table(base / name), read_text_table(out / name)
        if name == CONTROLS:
            old = set(map(tuple, before.to_numpy().tolist()))
            new = set(map(tuple, after.to_numpy().tolist()))
            entry["removed_rows"] = sorted(old - new)
            entry["added_rows"] = sorted(new - old)
            if list(before.columns) != list(after.columns):
                unexpected.append(f"{name}: columns changed")
            for row in set(entry["removed_rows"]) - rule.get("removed_rows", set()):
                unexpected.append(f"{name}: row removed {row}")
            for row in set(entry["added_rows"]) - rule.get("added_rows", set()):
                unexpected.append(f"{name}: row added {row}")
            if not entry["removed_rows"] and not entry["added_rows"]:
                unexpected.append(f"{name}: bytes changed but no row (format or order change)")
            continue
        removed = [c for c in before.columns if c not in after.columns]
        added = [c for c in after.columns if c not in before.columns]
        entry["columns_removed"], entry["columns_added"] = removed, added
        if removed or len(before) != len(after) or list(after.columns[:len(before.columns)]) != list(before.columns):
            unexpected.append(f"{name}: rows or columns removed / reordered")
            continue
        entry["columns_changed"] = [c for c in before.columns if not before[c].equals(after[c])]
        entry["max_abs_change"] = {}
        for column in entry["columns_changed"]:
            old_v = pd.to_numeric(before[column], errors="coerce").to_numpy(dtype=float)
            new_v = pd.to_numeric(after[column], errors="coerce").to_numpy(dtype=float)
            entry["max_abs_change"][column] = float(np.nanmax(np.abs(new_v - old_v))) if len(old_v) else 0.0
            if column not in rule.get("changed_columns", set()):
                unexpected.append(f"{name}: column {column} changed")
        for column in added:
            if column not in rule.get("added_columns", set()):
                unexpected.append(f"{name}: column {column} added")
        if not entry["columns_changed"] and not added:
            unexpected.append(f"{name}: bytes changed but no column value (format change)")
    return files, unexpected


def _allow(allowed: dict, name: str, key: str, values) -> None:
    allowed.setdefault(name, {}).setdefault(key, set()).update(values)


# --------------------------------------------------------------------------- steps
def step_family(state: dict, *, minor_child: bool, args, log: dict) -> None:
    controls = state["controls"]
    kept, removed = split_family_rows(controls)
    _allow(state["allowed"], CONTROLS, "removed_rows", map(tuple, removed.to_numpy().tolist()))
    record = {"removed_control_fields": removed["control_field"].tolist()}
    if minor_child:
        importance = hhtype_importance(removed)
        rows = minor_child_rows(importance)
        clash = sorted(set(rows["control_field"]) & set(kept["control_field"]))
        if clash:
            raise ValueError(f"{CONTROLS} already holds {clash}")
        kept = pd.concat([kept, rows[list(kept.columns)]], ignore_index=True)
        _allow(state["allowed"], CONTROLS, "added_rows", map(tuple, rows[list(kept.columns)].to_numpy().tolist()))
        record["added_rows"] = rows.to_dict(orient="records")
        record["hhtype_importance"] = importance
        record.update(minor_child_data(state, args))
    state["controls"] = kept
    log["steps_done"].append({"step": "family_minor_child" if minor_child else "family_none", **record})


def minor_child_data(state: dict, args) -> dict:
    """Targets, integer grid columns and the seed class of ``family_minor_child``; returns its report."""
    from braunschweig.popsim import minor_child_targets as mct

    cells, filter_report = load_kreis_cells(args.cells_100m, args.kreise, [cs.HH_TOTAL_CENSUS_COLUMN])
    cells, targets_report = stage._add_minor_child_targets(cells, args.cells_1km)
    batch = batch_cells_in_loader_order(cells, state["tables"][TABLE_100M][CELL_ID])
    columns = [mct.ONE_ADULT_TARGET, mct.MULTI_ADULT_TARGET]
    built = mid.build_control_totals(batch, state["crosswalk"], columns)
    for table, (key, suffix) in GRID_TABLES.items():
        frame = state["tables"][table]
        new = built[key].set_index(key)
        for column in columns:
            name = f"{column}{suffix}"
            if name in frame.columns:
                raise ValueError(f"{table} already has {name}")
            values = new[name].reindex(frame[key])
            if values.isna().any():
                raise ValueError(f"{table}: {int(values.isna().sum())} row(s) without a value for {name}")
            frame[name] = int_text(values.to_numpy())
            _allow(state["allowed"], table, "added_columns", [name])
    sums = {c: {"sum_100m": float(built[CELL_ID][f"{c}{mid.SUFFIX_100M}"].sum()),
                "sum_1km": float(built[PARENT_ID][f"{c}{mid.SUFFIX_1KM}"].sum()),
                "float_sum_batch": float(batch[c].sum())} for c in columns}
    persons = pd.read_csv(state["out"] / SEED_PERSONS, usecols=[SEED_HOUSEHOLD_ID, SEED_AGE])
    state["seed_households"], seed_report = add_minor_class(state["seed_households"], persons)
    _allow(state["allowed"], SEED_HOUSEHOLDS, "added_columns", [MINOR_CLASS_COLUMN])
    return {"zgb_cell_filter": filter_report, "targets_report": targets_report,
            "target_sums": sums, "seed_class": seed_report}


def step_move_persons(state: dict, args, log: dict) -> None:
    age_cols, employed_cols, fine_teen = person_columns(state["controls"], state["tables"][TABLE_100M].columns)
    backbone, band_map = backbone_band_map(fine_teen)
    sources = list(dict.fromkeys(src for c in backbone for src in c.census_source))
    load = [cs.HH_TOTAL_CENSUS_COLUMN, *sources, *(employment_load_columns() if employed_cols else [])]
    cells, filter_report = load_kreis_cells(args.cells_100m, args.kreise, list(dict.fromkeys(load)))
    cells = prepared_cells.add_aggregated_controls(cells, band_map)
    if employed_cols:
        context = StageConfig({config_keys.KEY_KREIS_CONTROLS: str(args.kreis_controls_dir),
                               "data_path": str(args.data_path),
                               config_keys.KEY_EMPLOYMENT_GRID_AGE_SHAPE_SOURCE: args.employment_age_shape_source})
        cells = stage._inject_employment_grid_columns(context, cells, True)
    batch = batch_cells_in_loader_order(cells, state["tables"][TABLE_100M][CELL_ID])
    move_max = None if args.self_check else args.move_persons_max
    tables, move_report = rebuild_person_tables(batch, state["crosswalk"], age_cols=age_cols,
                                                employed_cols=employed_cols, move_persons_max=move_max)
    record = {"step": "move_persons", "self_check": bool(args.self_check), "fine_teen_age_bands": fine_teen,
              "age_cols": age_cols, "employed_cols": employed_cols, "zgb_cell_filter": filter_report,
              "move_persons_max": move_max, "move_report": move_report}
    if move_max is not None:
        record["cells_check"] = household_cells_check(
            batch, state["crosswalk"], tables, state["tables"][TABLE_1KM], age_cols=age_cols,
            employed_cols=employed_cols, move_persons_max=move_max)
        (state["out"] / CELLS_CHECK_FILE).write_text(
            json.dumps({"batch": state["out"].name, **record["cells_check"],
                        "move_report": move_report}, indent=2), encoding="utf-8")
        if record["cells_check"]["cells_left"]:
            state["problems"].append(f"move_persons: {record['cells_check']['cells_left']} household-less "
                                     "cell(s) below the bound keep person targets")
        if record["cells_check"]["max_abs_parent_sum_change_all"] != 0:
            state["problems"].append("move_persons: a person column's 1 km sum changed")
    for table, (key, suffix) in GRID_TABLES.items():
        frame = state["tables"][table]
        new = tables[key].set_index(key)
        for column in [*age_cols, *employed_cols]:
            name = f"{column}{suffix}"
            values = new[name].reindex(frame[key])
            if values.isna().any():
                raise ValueError(f"{table}: {int(values.isna().sum())} row(s) without a value for {name}")
            frame[name] = int_text(values.to_numpy())
            if move_max is not None:
                _allow(state["allowed"], table, "changed_columns", [name])
    log["steps_done"].append(record)


# --------------------------------------------------------------------------- main
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, type=Path, help="base batch folder (previous arm)")
    parser.add_argument("--out", required=True, type=Path, help="new batch folder (must not exist)")
    parser.add_argument("--steps", required=True, help=f"comma list of {', '.join(STEPS)}, applied in order")
    parser.add_argument("--kreise", help="comma list of the model's Kreis codes (config "
                        f"{config_keys.KEY_KREISE}); the cells of the minor-child and the move steps")
    parser.add_argument("--cells-100m", type=Path, help="prepared 100 m cell parquet")
    parser.add_argument("--cells-1km", type=Path, help="prepared 1 km cell parquet (family_minor_child)")
    parser.add_argument("--kreis-controls-dir", type=Path,
                        help=f"config {config_keys.KEY_KREIS_CONTROLS} (employed grid of move_persons)")
    parser.add_argument("--data-path", type=Path, help="pipeline data_path (employed grid age shape)")
    parser.add_argument("--employment-age-shape-source", choices=config_keys.EMPLOYMENT_GRID_AGE_SHAPE_SOURCES,
                        help=f"config {config_keys.KEY_EMPLOYMENT_GRID_AGE_SHAPE_SOURCE} of the base run")
    parser.add_argument("--move-persons-max", type=float, default=MOVE_PERSONS_MAX_DEFAULT,
                        help="persons bound of a household-less donor cell (default: the stage's)")
    parser.add_argument("--commit", required=True, help="eqasim-bs commit of the library code")
    parser.add_argument("--self-check", action="store_true",
                        help="move_persons without the move: must reproduce the base folder exactly")
    return parser.parse_args(argv)


def check_arguments(args) -> str | None:
    """The reason the arguments are unusable, or None."""
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    if not steps or len(set(steps)) != len(steps) or any(s not in STEPS for s in steps):
        return f"--steps: give distinct names of {list(STEPS)}, got {steps}"
    if {"family_none", "family_minor_child"} <= set(steps):
        return "family_none and family_minor_child both remove the family rows; give one"
    if args.self_check and steps != ["move_persons"]:
        return "--self-check is the move_persons rebuild without the move; give --steps move_persons only"
    args.steps = steps
    needs = {"family_minor_child": ("kreise", "cells_100m", "cells_1km"),
             "move_persons": ("kreise", "cells_100m")}
    for step in steps:
        absent = [n for n in needs.get(step, ()) if getattr(args, n) is None]
        if absent:
            return f"{step} needs --{', --'.join(a.replace('_', '-') for a in absent)}"
    if args.kreise is not None:
        args.kreise = [k.strip() for k in args.kreise.split(",") if k.strip()]
    if args.out.exists():
        return f"{args.out} exists"
    for sub in ("configs", "data"):
        if not (args.base / sub).is_dir():
            return f"{args.base / sub} is not a directory"
    return None


def input_hashes(args) -> dict:
    paths = {"cells_100m": args.cells_100m, "cells_1km": args.cells_1km}
    if args.kreis_controls_dir is not None:
        paths["kreis_erwerbsstatus"] = Path(args.kreis_controls_dir) / "kreis_erwerbsstatus.parquet"
    return {k: {"path": str(p), "sha256": _sha_or_none(Path(p))} for k, p in paths.items() if p is not None}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    reason = check_arguments(args)
    if reason:
        print(f"refuse: {reason}", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    out = args.out
    log = {"arguments": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
           "steps": args.steps, "commit": args.commit, "steps_done": []}
    state = {"out": out, "allowed": {}, "problems": []}
    files = {}
    # Any failure ends in a log with the error text and exit 1; a half-built folder never stays
    # without a log.
    try:
        out.mkdir(parents=True)
        shutil.copytree(args.base / "configs", out / "configs")
        shutil.copytree(args.base / "data", out / "data")
        (out / "output").mkdir()
        log["inputs"] = input_hashes(args)
        terminators = {n: line_terminator(out / n) for n in (CONTROLS, TABLE_100M, TABLE_1KM, SEED_HOUSEHOLDS)}
        state["controls"] = read_text_table(out / CONTROLS)
        state["tables"] = {n: read_text_table(out / n) for n in GRID_TABLES}
        state["crosswalk"] = read_text_table(out / CROSSWALK)
        state["seed_households"] = read_text_table(out / SEED_HOUSEHOLDS)
        for step in args.steps:
            if step == "move_persons":
                step_move_persons(state, args, log)
            else:
                step_family(state, minor_child=step == "family_minor_child", args=args, log=log)
        written = {CONTROLS: state["controls"], SEED_HOUSEHOLDS: state["seed_households"], **state["tables"]}
        for name, frame in written.items():
            if not frame.equals(read_text_table(args.base / name)):
                write_text_table(frame, out / name, terminators[name])
        files, unexpected = compare_folders(args.base, out, state["allowed"])
        state["problems"].extend(unexpected)
        if args.self_check:
            state["problems"].extend(
                f"{n}: changed with --self-check (columns {e.get('columns_changed')})"
                for n, e in files.items() if e["changed"])
    except Exception as error:  # noqa: BLE001 - recorded in the log, then exit 1
        logger.exception("prepare failed")
        state["problems"].append(f"{type(error).__name__}: {error}")
    log.update({"files": files, "problems": state["problems"], "ok": not state["problems"]})
    log_path = out / "prepare_log.json" if out.is_dir() else out.with_name(out.name + ".prepare_log.json")
    log_path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    changed = {n: e.get("columns_changed") or e.get("columns_added") or "rows/file"
               for n, e in files.items() if e["changed"]}
    print(json.dumps({"out": str(out), "steps": args.steps, "changed": changed,
                      "problems": state["problems"]}, indent=2, default=str))
    return EXIT_UNEXPECTED_CHANGE if state["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
