"""Run the PopulationSim batch diagnostics over a work directory.

Aggregates the per-batch tables of ``batch_folder``, ``reuse``, ``coverage``, ``fit`` and
``solver_paths`` into one set of CSV tables, applies warning thresholds, and writes a Markdown
summary and a provenance file.  The batches of one seed group (same ``seed_households.csv``
and ``seed_persons.csv`` fingerprint) share one seed, so seed-level tables (reuse, weight shares, attribute shares,
coverage) are computed once per group with the group's summed usage; the seed is never counted
once per batch.  Ratios are always recomputed from sums, never averaged.

Command line::

    python -m braunschweig.analysis.popsim_batch_diagnostics.run --work-dir <dir> \\
        --out-dir <dir> [--batches batch_000 batch_032] [--mid-households <csv>] [--misfit]

Nothing here assumes a reference population; every number is derived from the batch folders.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (
    list_batch_folders, load_batch_folder)
from braunschweig.analysis.popsim_batch_diagnostics.coverage import (
    coverage_table, resolved_record_shares, universe_table)
from braunschweig.analysis.popsim_batch_diagnostics.fit import (
    control_fit, misfit_split, rounding_shift)
from braunschweig.analysis.popsim_batch_diagnostics.reuse import (
    reuse_by_zone, reuse_summary, shares_by_attribute, usage_counts, weight_class_shares)
from braunschweig.analysis.popsim_batch_diagnostics.solver_paths import solver_path_table
from braunschweig.popsim.control_groups import UNIVERSE_RATIO_TOLERANCE, unmatched_controls

logger = logging.getLogger(__name__)
_TAG = "[popsim_batch_diagnostics]"

SEED_ATTRIBUTES = ("RegioStaR7", "H_GR", "hh_type5", "oek_status", "number_of_cars")
MID_COLUMNS = ("H_ID", "BASISAUF", "BLAND", "MODE", "TEILSTP")
MID_JOIN_WARN_RATE = 0.99
MID_JOIN_FAIL_RATE = 0.50
_KREIS_COLUMN = "KREIS"
_SEED_GROUP_CHARS = 12


@dataclass(frozen=True)
class Thresholds:
    """Warning thresholds; every breach is logged at WARNING and returned, none aborts a run."""

    weight_class_gap_pp: float = 5.0          # |synthetic - seed weight share| in percentage points
    coverage_ratio_low: float = 0.5           # uncovered-record share, synthetic / seed
    coverage_ratio_high: float = 2.0
    universe_ratio_tolerance: float = UNIVERSE_RATIO_TOLERANCE    # |group target sum / household total - 1|
    sequential_fallback_rate: float = 0.05    # non_simultaneous / n_parents per level
    coverage_multi_hit_max: float = 0.0       # records hit by 2+ categories of an exhaustive group
    rounding_shift_bound: float = 0.10        # |integer - float| / target per control
    rounding_min_target: float = 40.0         # controls with a smaller target sum are not judged


class _Warnings(list):
    """Warning collector that also logs every breach with its value and threshold."""

    def add(self, check: str, subject: str, value: float, threshold: float) -> None:
        self.append({"check": check, "subject": subject, "value": value, "threshold": threshold})
        logger.warning("%s WARNING %s: %s value %.4g, threshold %.4g", _TAG, check, subject,
                       value, threshold)


@dataclass
class _SeedGroup:
    """Accumulator of the batches that share one seed."""

    fingerprint: str               # sha256 over the households and the persons file
    first_batch: object            # BatchFolder without its expanded output
    usage: pd.Series
    kreis_ids: list
    names: list

    @property
    def label(self) -> str:
        return self.fingerprint[:_SEED_GROUP_CHARS]


def _ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.where(denominator != 0)


def _check_empty_out_dir(out_dir: Path) -> None:
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise FileExistsError(f"out_dir {out_dir} exists and is not empty; choose a new "
                              "directory (no output is overwritten)")


def _join_mid_households(seed: pd.DataFrame, mid: pd.DataFrame, id_col: str,
                         label: str) -> tuple:
    """Left-join the MiD household columns to the seed households; return (frame, columns)."""
    if id_col not in mid.columns:
        raise ValueError(f"mid_households lacks the household id column '{id_col}'")
    if mid[id_col].duplicated().any():
        raise ValueError(f"mid_households has duplicate '{id_col}' values")
    value_columns = [c for c in mid.columns if c != id_col]
    rename = {c: f"mid_{c}" if c in seed.columns else c for c in value_columns}
    collided = [c for c in value_columns if rename[c] != c]
    if collided:
        logger.warning("%s mid_households columns %s exist in the seed households of %s; "
                       "renamed with the prefix 'mid_'", _TAG, collided, label)
    joined = seed.merge(mid.rename(columns=rename), on=id_col, how="left", indicator=True)
    found = int((joined.pop("_merge") == "both").sum())
    rate = found / len(seed)
    message = (f"{_TAG} mid_households join {label}: {found}/{len(seed)} seed households "
               f"found ({100 * rate:.1f}%)")
    if rate < MID_JOIN_FAIL_RATE:
        raise ValueError(f"{message}; below {100 * MID_JOIN_FAIL_RATE:.0f}%, the household ids "
                         "of mid_households and the seed do not match")
    (logger.warning if rate < MID_JOIN_WARN_RATE else logger.info)(message)
    return joined, list(rename.values())


def _md_value(value) -> str:
    if isinstance(value, (float, np.floating)):
        return "" if np.isnan(value) else f"{value:.4g}"
    return str(value)


def _md_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "(no rows)\n"
    lines = ["| " + " | ".join(frame.columns) + " |", "|" + "---|" * len(frame.columns)]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(_md_value(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def _repository_commit() -> str:
    """Commit of the repository that contains this package, else ``EQASIM_BS_COMMIT``, else unknown.

    Git is used only when its top-level directory is the package's own repository root (the code
    may run from an unpacked archive inside some other repository).  A non-empty
    ``git status --porcelain`` gives ``<sha>-dirty`` and a WARNING.
    """
    root = Path(__file__).resolve().parents[3]

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                              check=True, timeout=30).stdout.strip()

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
            raise OSError(f"git top level is not the package repository {root}")
        commit = git("rev-parse", "HEAD")
        if git("status", "--porcelain"):
            logger.warning("%s the working tree of %s has uncommitted changes; the commit is "
                           "recorded as '-dirty'", _TAG, root)
            commit += "-dirty"
        return commit
    except (OSError, subprocess.SubprocessError) as error:
        fallback = os.environ.get("EQASIM_BS_COMMIT")
        if fallback:
            logger.info("%s git commit unavailable (%s); using EQASIM_BS_COMMIT", _TAG, error)
            return fallback
        logger.warning("%s repository commit unknown: git failed (%s) and EQASIM_BS_COMMIT is "
                       "not set", _TAG, error)
        return "unknown"


def _mid_provenance(mid: pd.DataFrame | None, path) -> dict | None:
    if path is not None:
        return {"path": str(path), "sha256": _sha256(Path(path))}
    if mid is not None:
        return {"path": None, "source": "dataframe", "rows": len(mid),
                "columns": list(mid.columns)}
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _seed_fingerprint(batch) -> str:
    """Fingerprint of the seed: the households hash combined with the sha256 of the persons file.

    Two batches with equal households but different persons are different seeds.
    """
    persons_hash = _sha256(batch.path / "data" / "seed_persons.csv")
    return hashlib.sha256(f"{batch.seed_fingerprint}:{persons_hash}".encode()).hexdigest()


def _check_group_member(group: "_SeedGroup", batch) -> None:
    """Raise unless ``batch`` agrees with the group's first batch on everything a group shares."""
    first = group.first_batch
    differences = []
    if not batch.control_spec.equals(first.control_spec):
        differences.append("control specifications")
    for field in ("total_hh_control", "weight_col", "household_id_col", "geographies"):
        if getattr(batch, field) != getattr(first, field):
            differences.append(field)
    if differences:
        raise ValueError(
            f"batches {group.names[0]} and {batch.name} share one seed but their "
            f"{', '.join(differences)} differ; they cannot be aggregated as one group")


def _region_fit(per_batch: pd.DataFrame) -> pd.DataFrame:
    """Per control sums over batches, then per geography sums over controls with a target.

    For a control whose geography is coarser than a batch (e.g. KREIS), every batch holds its
    own apportioned target, so the ``region`` and ``region_geography`` rows sum per-batch
    absolute errors against per-batch targets.  They are an UPPER BOUND of the Kreis-level
    WAPE: errors of opposite sign in different batches of one Kreis do not cancel.
    """
    sums = per_batch.groupby("control", sort=False).agg(
        geography=("geography", "first"), importance=("importance", "first"),
        target_sum=("target_sum", "sum"), synthetic_sum=("synthetic_sum", "sum"),
        abs_error=("abs_error", "sum")).reset_index()
    sums["bias"] = _ratio(sums["synthetic_sum"], sums["target_sum"]) - 1.0
    sums["wape"] = _ratio(sums["abs_error"], sums["target_sum"])
    sums.insert(0, "scope", "region")
    kept = sums[sums["target_sum"] > 0]
    logger.info("%s control_fit region_geography: %d of %d controls excluded (target_sum 0)",
                _TAG, len(sums) - len(kept), len(sums))
    geography = kept.groupby("geography", sort=False)[
        ["target_sum", "synthetic_sum", "abs_error"]].sum().reset_index()
    geography["bias"] = _ratio(geography["synthetic_sum"], geography["target_sum"]) - 1.0
    geography["wape"] = _ratio(geography["abs_error"], geography["target_sum"])
    geography.insert(0, "scope", "region_geography")
    geography["control"] = "ALL"
    return pd.concat([sums, geography], ignore_index=True)


def _solver_level_table(solver: pd.DataFrame, batch_names: Sequence[str]) -> pd.DataFrame:
    """Solver counters summed over batches per level; every batch must have a first attempt."""
    if solver.empty:
        raise ValueError("no solver path rows: no batch log has a first integerizer attempt")
    parents = solver.groupby("batch")["n_parents"].sum().reindex(list(batch_names)).fillna(0)
    silent = parents[parents == 0].index.tolist()
    logger.info("%s solver paths: parsed %d of %d batches", _TAG, len(batch_names) - len(silent),
                len(batch_names))
    if silent:
        raise ValueError(f"batch(es) without any first attempt in the solver log: {silent}")
    counters = [c for c in solver.select_dtypes(include="number").columns]
    levels = solver.groupby("level", sort=False)[counters].sum().reset_index()
    levels["non_simultaneous_rate"] = _ratio(levels["non_simultaneous"], levels["n_parents"])
    return levels


def run_diagnostics(work_dir, out_dir, *, batches: Sequence[str] | None = None,
                    mid_households: pd.DataFrame | None = None, misfit: bool = False,
                    thresholds: Thresholds = Thresholds(), chunk_rows: int = 5_000_000,
                    max_unmatched_zone_share: float = 0.01,
                    mid_households_path=None) -> dict:
    """Compute, write and return all diagnostics tables and warnings of a work directory.

    Returns ``{"tables": {name: DataFrame}, "warnings": [{"check", "subject", "value",
    "threshold"}]}``.  ``out_dir`` must not exist or be empty (``FileExistsError``).  Batches with
    the same seed fingerprint must have identical control specifications (``ValueError``).
    The sequential-fallback rate uses ``non_simultaneous / n_parents`` summed over batches per
    level: ``sequential_fallback`` alone would miss parents that ended in sequential+rounded
    or all-smart-rounded weights.  A batch whose log has no first integerizer attempt, or a
    batch that differs from its seed group in control specification, household-total control,
    weight or household id column or geographies, raises ``ValueError``.  ``mid_households_path`` is recorded in the provenance only.
    """
    work_dir, out_dir = Path(work_dir), Path(out_dir)
    _check_empty_out_dir(out_dir)
    folders = list_batch_folders(work_dir, batches)
    if not folders:
        raise ValueError(f"no batch_* folders in {work_dir}")
    warnings = _Warnings()
    groups: dict = {}
    reuse_rows, fit_parts, universe_parts, split_parts, shift_parts = [], [], [], [], []

    for position, folder in enumerate(folders, start=1):
        logger.info("%s batch %d/%d: %s", _TAG, position, len(folders), folder.name)
        batch = load_batch_folder(folder)
        id_col = batch.household_id_col
        usage = usage_counts(batch.expanded, id_col)
        fingerprint = _seed_fingerprint(batch)
        group = groups.get(fingerprint)
        if group is None:
            group = groups[fingerprint] = _SeedGroup(
                fingerprint, replace(batch, expanded=batch.expanded.iloc[0:0]),
                pd.Series(dtype="int64"), [], [])
        else:
            _check_group_member(group, batch)
        group.names.append(batch.name)
        group.usage = group.usage.add(usage, fill_value=0)
        if _KREIS_COLUMN in batch.expanded.columns:
            group.kreis_ids.append(batch.expanded[[id_col, _KREIS_COLUMN]])
        reuse_rows.append(pd.DataFrame([{
            "scope": "batch", "seed_group": group.label, "subject": batch.name,
            **reuse_summary(usage, len(batch.seed_households), id_col)}]))

        fit = control_fit(batch, max_unmatched_zone_share=max_unmatched_zone_share)
        fit.insert(0, "batch", batch.name)
        fit_parts.append(fit)
        universe_parts.append(universe_table(batch))
        if misfit:
            split = misfit_split(batch, levels=batch.geographies, chunk_rows=chunk_rows,
                                 max_unmatched_zone_share=max_unmatched_zone_share)
            judged_level = batch.geographies[-1]
            if judged_level not in set(split["level"]):
                raise ValueError(
                    f"batch {batch.name}: the misfit split has no rows for the judged level "
                    f"{judged_level!r} (levels present: {sorted(set(split['level']))}); the "
                    "rounding shift cannot be judged")
            shift = rounding_shift(
                split, batch.total_hh_control, level=judged_level,
                min_target=thresholds.rounding_min_target, bound=thresholds.rounding_shift_bound)
            split.insert(0, "batch", batch.name)
            shift.insert(0, "batch", batch.name)
            split_parts.append(split)
            shift_parts.append(shift)
            for row in shift.itertuples():
                if row.shift > thresholds.rounding_shift_bound:
                    warnings.add("rounding_shift", f"{batch.name}/{row.control}", row.shift,
                                 thresholds.rounding_shift_bound)
        del batch

    many = len(groups) > 1
    weight_parts, attribute_parts, coverage_parts, resolved_parts = [], [], [], []
    for group in groups.values():
        batch = group.first_batch
        id_col = batch.household_id_col
        usage = group.usage.astype("int64")
        usage.index.name = id_col
        label = group.label
        prefix = f"{label}/" if many else ""
        row = {"scope": "region", "seed_group": label, "subject": label,
               **reuse_summary(usage, len(batch.seed_households), id_col)}
        reuse_rows.append(pd.DataFrame([row]))
        if len(group.kreis_ids) == len(group.names):
            expanded = pd.concat(group.kreis_ids, ignore_index=True)
            kreis = reuse_by_zone(expanded, _KREIS_COLUMN, id_col).rename(
                columns={"zone": "subject"})
            kreis.insert(0, "scope", "kreis")
            kreis.insert(1, "seed_group", label)
            kreis["subject"] = kreis["subject"].astype(str)
            reuse_rows.append(kreis)
        else:
            logger.warning("%s seed group %s: not every batch has a '%s' column in the "
                           "expanded output; kreis reuse rows skipped", _TAG, label,
                           _KREIS_COLUMN)

        weights = weight_class_shares(batch.seed_households, usage, batch.weight_col, id_col)
        weights.insert(0, "seed_group", label)
        weight_parts.append(weights)
        for item in weights.itertuples():
            if abs(item.difference_pp) > thresholds.weight_class_gap_pp:
                warnings.add("weight_class_gap", f"{prefix}{item.weight_class}",
                             item.difference_pp, thresholds.weight_class_gap_pp)

        seed = batch.seed_households
        columns = [c for c in SEED_ATTRIBUTES if c in seed.columns]
        absent = [c for c in SEED_ATTRIBUTES if c not in seed.columns]
        if absent:
            logger.info("%s seed group %s: attribute column(s) %s absent, skipped", _TAG,
                        label, absent)
        if mid_households is not None:
            seed, mid_columns = _join_mid_households(seed, mid_households, id_col, label)
            columns += mid_columns
        for column in columns:
            shares = shares_by_attribute(seed, usage, column, batch.weight_col, id_col)
            shares.insert(0, "seed_group", label)
            attribute_parts.append(shares)

        coverage = coverage_table(batch, usage)
        coverage.insert(0, "seed_group", label)
        coverage_parts.append(coverage)
        resolved = resolved_record_shares(batch, usage)
        resolved.insert(0, "seed_group", label)
        resolved_parts.append(resolved)
        for item in coverage.itertuples():
            if item.exhaustive and item.n_multi_hit > thresholds.coverage_multi_hit_max:
                warnings.add("coverage_multi_hit", f"{prefix}{item.group}", item.n_multi_hit,
                             thresholds.coverage_multi_hit_max)
        for item in coverage.itertuples():
            if pd.isna(item.ratio):
                logger.info("%s coverage ratio of group %s%s is undefined (NaN)", _TAG, prefix,
                            item.group)
            elif item.ratio < thresholds.coverage_ratio_low:
                warnings.add("coverage_ratio", f"{prefix}{item.group}", item.ratio,
                             thresholds.coverage_ratio_low)
            elif item.ratio > thresholds.coverage_ratio_high:
                warnings.add("coverage_ratio", f"{prefix}{item.group}", item.ratio,
                             thresholds.coverage_ratio_high)

    seen_unmatched: set = set()
    for group in groups.values():
        spec_batch = group.first_batch
        for control in unmatched_controls(spec_batch.control_spec, spec_batch.total_hh_control):
            if control not in seen_unmatched:
                seen_unmatched.add(control)
                warnings.add("unmatched_control", control, float("nan"), float("nan"))

    universe = pd.concat(universe_parts, ignore_index=True).groupby(
        ["group", "geography"], sort=False)[["target_sum", "household_total"]].sum().reset_index()
    universe["ratio"] = _ratio(universe["target_sum"], universe["household_total"])
    for item in universe.itertuples():
        if pd.isna(item.ratio):
            logger.info("%s universe ratio of group %s in %s is undefined (NaN)", _TAG,
                        item.group, item.geography)
        elif abs(item.ratio - 1.0) > thresholds.universe_ratio_tolerance:
            warnings.add("universe_ratio", f"{item.group}/{item.geography}", item.ratio,
                         thresholds.universe_ratio_tolerance)

    per_batch_fit = pd.concat(fit_parts, ignore_index=True)
    per_batch_fit.insert(0, "scope", "batch")
    fit_table = pd.concat([per_batch_fit, _region_fit(per_batch_fit)], ignore_index=True)

    solver = solver_path_table(work_dir, [f.name for f in folders])
    solver_levels = _solver_level_table(solver, [f.name for f in folders])
    for item in solver_levels.itertuples():
        if item.n_parents == 0:
            logger.info("%s solver level %s has no parents; fallback rate undefined", _TAG,
                        item.level)
        elif item.non_simultaneous_rate > thresholds.sequential_fallback_rate:
            warnings.add("sequential_fallback_rate", item.level, item.non_simultaneous_rate,
                         thresholds.sequential_fallback_rate)

    tables = {
        "reuse": pd.concat(reuse_rows, ignore_index=True),
        "weight_class_shares": pd.concat(weight_parts, ignore_index=True),
        "attribute_shares": pd.concat(attribute_parts, ignore_index=True)
        if attribute_parts else pd.DataFrame(),
        "control_coverage": pd.concat(coverage_parts, ignore_index=True),
        "resolved_record_shares": pd.concat(resolved_parts, ignore_index=True),
        "control_universe": universe,
        "control_fit": fit_table,
        "solver_paths": solver,
    }
    if misfit:
        tables["misfit_split"] = pd.concat(split_parts, ignore_index=True)
        tables["rounding_shift"] = pd.concat(shift_parts, ignore_index=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(out_dir / f"{name}.csv", index=False)
    commit = _repository_commit()
    _write_summary(out_dir, work_dir, folders, groups, commit, tables, solver_levels, warnings)
    provenance = {
        "work_dir": str(work_dir), "batches": [f.name for f in folders],
        "seed_groups": {g.fingerprint: g.names for g in groups.values()},
        "repository_commit": commit, "thresholds": dataclasses.asdict(thresholds),
        "misfit": misfit, "chunk_rows": chunk_rows,
        "mid_households": _mid_provenance(mid_households, mid_households_path),
        "utc_timestamp": datetime.now(timezone.utc).isoformat()}
    (out_dir / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    logger.info("%s wrote %d tables and %d warning(s) to %s", _TAG, len(tables), len(warnings),
                out_dir)
    return {"tables": tables, "warnings": list(warnings)}


def _write_summary(out_dir: Path, work_dir: Path, folders, groups, commit: str, tables: dict,
                   solver_levels: pd.DataFrame, warnings: list) -> None:
    reuse = tables["reuse"]
    fit = tables["control_fit"]
    lines = [
        "# PopulationSim batch diagnostics", "",
        f"- work directory: `{work_dir}`", f"- batches: {len(folders)}",
        f"- seed groups: {len(groups)} ("
        + ", ".join(f"{g.label}: {len(g.names)} batch(es)" for g in groups.values()) + ")",
        f"- repository commit: `{commit}`", "",
        "## Reuse (region rows)", "", _md_table(reuse[reuse["scope"] == "region"]),
        "## Weight classes", "", _md_table(tables["weight_class_shares"]),
        "## Control coverage", "", _md_table(tables["control_coverage"]),
        "## Resolved seed records by raw code", "",
        _md_table(tables["resolved_record_shares"]),
        "## Control universe", "", _md_table(tables["control_universe"]),
        "## Control fit per geography (region)", "",
        _md_table(fit[fit["scope"] == "region_geography"].drop(columns=["batch"])),
        "## Solver paths per level (summed over batches)", "",
        "`non_simultaneous_rate` = `non_simultaneous / n_parents`: parents whose final weights did "
        "not come from the simultaneous solution (sequential, single feasible sub zone, or "
        "all-smart-rounded).", "", _md_table(solver_levels),
        "## Warnings", "", _md_table(pd.DataFrame(
            warnings, columns=["check", "subject", "value", "threshold"]))]
    (out_dir / "popsim_batch_diagnostics.md").write_text("\n".join(lines), encoding="utf-8")


def _parse_thresholds(parser: argparse.ArgumentParser, items: Sequence[str]) -> Thresholds:
    fields = {f.name for f in dataclasses.fields(Thresholds)}
    values = {}
    for item in items:
        name, separator, text = item.partition("=")
        if not separator or name not in fields:
            parser.error(f"--threshold {item!r}: expected NAME=VALUE with NAME one of "
                         f"{sorted(fields)}")
        try:
            values[name] = float(text)
        except ValueError:
            parser.error(f"--threshold {item!r}: value is not a number")
    return Thresholds(**values)


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry point; returns 0 on success (warnings do not change the exit code)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--batches", nargs="+", default=None)
    parser.add_argument("--mid-households", default=None,
                        help="MiD2023_Haushalte.csv; columns " + ", ".join(MID_COLUMNS))
    parser.add_argument("--mid-separator", default=",", help="field separator of that file")
    parser.add_argument("--misfit", action="store_true")
    parser.add_argument("--chunk-rows", type=int, default=5_000_000)
    parser.add_argument("--threshold", action="append", default=[], metavar="NAME=VALUE",
                        help="override a Thresholds field (repeatable)")
    parser.add_argument("--max-unmatched-zone-share", type=float, default=0.01)
    args = parser.parse_args(argv)
    thresholds = _parse_thresholds(parser, args.threshold)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mid = None
    if args.mid_households:
        mid = pd.read_csv(args.mid_households, usecols=list(MID_COLUMNS), sep=args.mid_separator)
    run_diagnostics(args.work_dir, args.out_dir, batches=args.batches, mid_households=mid,
                    misfit=args.misfit, thresholds=thresholds, chunk_rows=args.chunk_rows,
                    max_unmatched_zone_share=args.max_unmatched_zone_share,
                    mid_households_path=args.mid_households)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
