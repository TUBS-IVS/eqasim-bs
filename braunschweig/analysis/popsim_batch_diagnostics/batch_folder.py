"""Read one PopulationSim batch folder and rebuild its household incidence.

A batch folder holds the inputs (``data/``, ``configs/``) and outputs (``output/``)
of one PopulationSim run.  This module only reads; it never writes into a batch.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd
import yaml

from braunschweig.analysis.integerizer_quality.cell_error import evaluate_expression

logger = logging.getLogger(__name__)

_SEED_HOUSEHOLDS = Path("data") / "seed_households.csv"
_SEED_PERSONS = Path("data") / "seed_persons.csv"
_CONTROLS = Path("configs") / "controls.csv"
_SETTINGS = Path("configs") / "settings.yaml"
_EXPANDED = Path("output") / "final_expanded_household_ids.csv"
_STORE = Path("output") / "pipeline.h5"


@dataclass(frozen=True)
class BatchFolder:
    """One loaded batch folder (inputs, control specification, expanded output)."""

    name: str
    path: Path
    seed_households: pd.DataFrame
    seed_persons: pd.DataFrame
    control_spec: pd.DataFrame
    expanded: pd.DataFrame
    total_hh_control: str
    household_id_col: str
    weight_col: str
    geographies: tuple
    has_store: bool
    seed_fingerprint: str  # sha256 of data/seed_households.csv


def list_batch_folders(work_dir, names: Sequence[str] | None = None) -> list:
    """Return the sorted ``batch_*`` directories of ``work_dir``.

    A requested name that is not an existing directory raises ``FileNotFoundError``.
    """
    work_dir = Path(work_dir)
    if names is not None:
        missing = [n for n in names if not (work_dir / n).is_dir()]
        if missing:
            raise FileNotFoundError(
                f"batch folder(s) {missing} not found in {work_dir}")
        found = sorted(work_dir / n for n in names)
    else:
        found = sorted(p for p in work_dir.glob("batch_*") if p.is_dir())
    logger.info("[popsim_batch_diagnostics] %d batch folder(s) in %s", len(found), work_dir)
    return found


def _require(path: Path, batch_name: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"batch {batch_name}: required input file missing: {path}")
    return path


def load_batch_folder(path) -> BatchFolder:
    """Load a complete batch folder; refuse an incomplete one (no expanded output)."""
    path = Path(path)
    name = path.name
    if not (path / _EXPANDED).is_file():
        raise ValueError(
            f"batch {name} is incomplete: {_EXPANDED.as_posix()} is missing "
            "(the run did not finish); exclude it or complete the run")
    households_file = _require(path / _SEED_HOUSEHOLDS, name)
    persons_file = _require(path / _SEED_PERSONS, name)
    controls_file = _require(path / _CONTROLS, name)
    settings_file = _require(path / _SETTINGS, name)

    settings = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    for key in ("total_hh_control", "household_id_col", "household_weight_col", "geographies"):
        if key not in settings:
            raise ValueError(f"batch {name}: {_SETTINGS.as_posix()} lacks '{key}'")
    id_col = settings["household_id_col"]

    households = pd.read_csv(households_file)
    persons = pd.read_csv(persons_file)
    # Persons carry the household id as float text ("2.0"); cast to the households' dtype.
    duplicated = households.loc[households[id_col].duplicated(), id_col].unique()
    if len(duplicated):
        raise ValueError(f"batch {name}: duplicate household id(s) "
                         f"{sorted(duplicated.tolist())[:10]} in {_SEED_HOUSEHOLDS.as_posix()}")
    numeric_ids = pd.to_numeric(persons[id_col], errors="coerce")
    if numeric_ids.isna().any():
        raise ValueError(
            f"batch {name}: {int(numeric_ids.isna().sum())} seed person(s) have a missing or "
            f"non-numeric household id '{id_col}' in {_SEED_PERSONS.as_posix()}")
    persons[id_col] = numeric_ids.astype(households[id_col].dtype)
    control_spec = pd.read_csv(controls_file, comment="#")
    expanded = pd.read_csv(path / _EXPANDED)

    fingerprint = hashlib.sha256(households_file.read_bytes()).hexdigest()
    batch = BatchFolder(
        name=name, path=path, seed_households=households, seed_persons=persons,
        control_spec=control_spec, expanded=expanded,
        total_hh_control=settings["total_hh_control"], household_id_col=id_col,
        weight_col=settings["household_weight_col"],
        geographies=tuple(settings["geographies"]),
        has_store=(path / _STORE).is_file(), seed_fingerprint=fingerprint)
    logger.info("[popsim_batch_diagnostics] loaded %s: %d households, %d persons, "
                "%d controls, %d expanded rows, store=%s", name, len(households),
                len(persons), len(control_spec), len(expanded), batch.has_store)
    return batch


def household_incidence(batch: BatchFolder) -> pd.DataFrame:
    """Household incidence of every control, indexed by seed household id.

    Household controls give 0/1; person controls are summed per household
    (PopulationSim's own rule).
    """
    id_col = batch.household_id_col
    households, persons = batch.seed_households, batch.seed_persons
    orphans = sorted(set(persons[id_col]) - set(households[id_col]))
    if orphans:
        raise ValueError(
            f"batch {batch.name}: seed persons reference household id(s) {orphans[:10]} "
            "that are not in the seed households")
    index = pd.Index(households[id_col], name=id_col)
    out = pd.DataFrame(index=index)
    for control in batch.control_spec.itertuples():
        if control.seed_table == "households":
            mask = evaluate_expression(control.expression, households)
            out[control.target] = mask.astype(int)
        elif control.seed_table == "persons":
            mask = evaluate_expression(control.expression, persons)
            per_household = pd.Series(mask.astype(int), index=persons[id_col]).groupby(
                level=0).sum()
            out[control.target] = per_household.reindex(index, fill_value=0).to_numpy()
        else:
            raise ValueError(f"batch {batch.name}: control {control.target} has unknown "
                             f"seed_table {control.seed_table!r}")
    return out


def control_totals(batch: BatchFolder, geography: str) -> pd.DataFrame:
    """Control totals of one geography, indexed by zone id, columns named by ``target``."""
    file = _require(batch.path / "data" / f"control_totals_{geography}.csv", batch.name)
    totals = pd.read_csv(file, index_col=geography)
    spec = batch.control_spec[batch.control_spec["geography"] == geography]
    rename = dict(zip(spec["control_field"], spec["target"]))
    missing = [f for f in rename if f not in totals.columns]
    if missing:
        raise ValueError(f"batch {batch.name}: {file.name} lacks control field(s) {missing}")
    return totals[list(rename)].rename(columns=rename)
