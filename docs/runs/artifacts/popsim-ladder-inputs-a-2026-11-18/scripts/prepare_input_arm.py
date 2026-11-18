"""Prepare one batch folder of an input-consistency ladder arm (issue #658, Part A, Task A7).

Copies ``configs/`` and ``data/`` of a base batch folder (a production batch, e.g. the arm-4 base)
to ``--out``, creates an empty ``output/`` and rewrites ONLY ``data/control_totals_KREIS.csv`` with
the branch's library functions, in the order ``mid.assemble_batch_folder`` applies them:

1. ``kreis_universe`` (A3): the KREIS person controls with an age universe are split by the
   batch's share of the persons of THEIR universe. The Kreis-wide targets are rebuilt as the sum
   of ``control_totals_KREIS.csv`` over all production batch folders of ``--prod-work-dir`` (the
   legacy population / household shares of one Kreis sum to 1 over its batches, so the sum is the
   Kreis marginal; the script checks the number of Kreise and that the ``trip_class`` parts sum to
   the persons of the Kreis). The active entries are the ``kreis_attribute_control.REGISTRY``
   entries whose KREIS control columns are all in the batch's ``configs/controls.csv``; the Tier-3
   names are the Tier-3 catalog controls present in the KREIS table. The universe weight columns
   come from ``controls_builder.apportion_universes`` / ``add_universe_weight_columns`` /
   ``apportion_weight_columns`` on the model cells (the cells of all production crosswalks) read
   from ``--cells-100m``; the region totals and the batch shares from
   ``mid._kreis_pop_from_crosswalk`` and ``mid.batch_folders.universe_apportion_weights``; the new values from
   ``folders.build_kreis_control_totals`` with ``controls_map = {name: (name,)}``. Only the
   controls of the apportionment map are rebuilt; every other KREIS column keeps its value
   (its legacy population or household share is what the pipeline would write too).
2. ``kreis_follow_grids`` (A4): ``folders.set_kreis_controls_from_fine_sums`` with the pairs of
   ``control_spec.kreis_controls_following_grids(controls.csv)`` and the batch's 100 m / 1 km tables.
3. ``employment_level`` (A5): ``employment_level.reconcile_kreis_employment_levels`` with the bridge
   of ``employment_level.employed_bridge_report`` computed from the batch's seed tables
   (``data/seed_persons.csv``, ``data/seed_households.csv``) as ``run_popsim_mid`` computes it.
   The step reads the batch's OWN seed; that equals the pipeline's whole seed only while
   ``stratify_regiostar`` is false (production), because the stratified path filters the seed per
   stratum. A batch folder does not record that key, so the step needs ``--prod-work-dir`` and
   fails unless every production batch carries the same seed files (sha256), which is what an
   unstratified run writes. Whether the base seed equals the production seed is logged.

Writes ``<out>/prepare_log.json``: the arguments, the steps, the library commit (``--commit``; the
archive has no git metadata on the run server), the bridge, the counts each library step returns
(``stats``), per file of ``configs/`` and ``data/`` whether it changed (sha256 of base and out) and
per KREIS column whether it changed and by how much at most. Exit 1 when any file other than
``data/control_totals_KREIS.csv`` changed (``unexpected_changed_files``) or ``--out`` exists.

Assertions for the production run (arguments, so the two-Kreis test fixture can pass its own):
``--expected-kreise`` (default 8), ``--expected-batches`` (default 60) and
``--trip-class-tolerance-persons`` (default 1.0). The persons of a Kreis are the pipeline's
definition, ``controls_builder.person_total_by_kreis`` over the age x sex band columns of its model
cells; the band set depends on ``fine_teen_age_bands``, which is read from the batch's
``controls.csv`` (the variant whose 100 m person backbone controls it carries) unless
``--fine-teen-age-bands on|off`` is given.

The cell parquet (Germany-wide on the server) is read with pyarrow, with only the grid id, the
single-year age columns the universes need and the band columns, filtered to the model cells.

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python prepare_input_arm.py --base <arm-4 dir>/batch_000 --out <arm dir>/batch_000 \
      --steps kreis_universe,kreis_follow_grids --prod-work-dir <production popsim work dir> \
      --cells-100m <zensus2022_grid_100m_de_prepared.parquet> --commit <eqasim-bs sha>
  (--commit is required)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from braunschweig.popsim import control_spec, employment_level, folders, mid, prepared_cells
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.mid import batch_folders
from braunschweig.popsim.stage import controls_builder

logger = logging.getLogger("prepare_input_arm")

STEPS = ("kreis_universe", "kreis_follow_grids", "employment_level")
KREIS_FILE = "data/control_totals_KREIS.csv"
ARS_KREIS = "ARS_kreis"
ID_COLUMNS = (folders.GEO_100M, folders.GEO_1KM, folders.GEO_KREIS)
TRIP_CLASS_ENTRY = "trip_class"
SEED_FILES = ("data/seed_persons.csv", "data/seed_households.csv")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_table(path: Path) -> pd.DataFrame:
    """A batch CSV with its zone and Kreis ids as text (zero-padded Kreis keys survive)."""
    header = pd.read_csv(path, nrows=0).columns
    return pd.read_csv(path, dtype={c: str for c in header if c in ID_COLUMNS})


def write_kreis_table(folder: Path, table: pd.DataFrame) -> None:
    """Write the KREIS control table of a batch folder the way ``folders.write_popsim_folder`` does."""
    table.to_csv(Path(folder) / KREIS_FILE, index=False)


def production_batches(prod_work_dir: Path, *, expected_batches: int) -> list[Path]:
    """The batch folders of the production work directory that carry a KREIS table."""
    batches = sorted(p for p in Path(prod_work_dir).glob("batch_*")
                     if (p / KREIS_FILE).is_file() and (p / "data" / "geo_cross_walk.csv").is_file())
    if len(batches) != expected_batches:
        raise ValueError(f"{prod_work_dir}: {len(batches)} batch folder(s) with a KREIS table and a "
                         f"crosswalk, expected {expected_batches}")
    return batches


def reconstruct_kreis_targets(batches: list[Path], *, expected_kreise: int) -> pd.DataFrame:
    """Kreis-wide targets: the sum of ``control_totals_KREIS.csv`` over the batches, keyed ``ARS_kreis``.

    Every batch's KREIS table holds its share of each Kreis it touches; the legacy shares of a
    Kreis sum to 1 over its batches, so the sum is the Kreis marginal. Raises ``ValueError`` when
    the batches disagree on the control columns or the number of Kreise is not ``expected_kreise``.
    """
    tables = [read_table(b / KREIS_FILE) for b in batches]
    columns = list(tables[0].columns)
    for batch, table in zip(batches, tables):
        if list(table.columns) != columns:
            raise ValueError(f"{batch}: KREIS columns differ from {batches[0]}")
    total = pd.concat(tables, ignore_index=True).groupby(folders.GEO_KREIS, sort=True).sum()
    if len(total) != expected_kreise:
        raise ValueError(f"the batches cover {len(total)} Kreis ({sorted(total.index)}), "
                         f"expected {expected_kreise}")
    return total.reset_index().rename(columns={folders.GEO_KREIS: ARS_KREIS})


def check_trip_class_partition(targets: pd.DataFrame, persons_by_kreis: dict, *,
                               tolerance_persons: float) -> dict:
    """The ``trip_class`` parts of each Kreis against its persons; ``ValueError`` above the tolerance."""
    entry = next(e for e in kac.REGISTRY if e.name == TRIP_CLASS_ENTRY)
    columns = [f"{c}_{folders.GEO_KREIS}" for c in kac.control_columns(entry)]
    if not all(c in targets.columns for c in columns):
        logger.info("trip_class is not an active KREIS control: partition check not applicable")
        return {"active": False}
    parts = targets.set_index(ARS_KREIS)[columns].sum(axis=1)
    deviation = {k: float(parts[k] - persons_by_kreis.get(k, 0.0)) for k in parts.index}
    worst = max(abs(v) for v in deviation.values())
    if worst > tolerance_persons:
        raise ValueError(f"trip_class parts differ from the persons of the Kreis by up to {worst:.3f} "
                         f"persons (tolerance {tolerance_persons}): {deviation}")
    return {"active": True, "deviation_persons_by_kreis": deviation, "max_abs_deviation_persons": worst}


def fine_teen_age_bands_of(controls_df: pd.DataFrame) -> bool:
    """``fine_teen_age_bands`` of the run that wrote ``controls.csv``.

    The variant whose 100 m person backbone controls are all in ``controls.csv``; ``ValueError``
    when both or neither match (then give ``--fine-teen-age-bands``).
    """
    fields = set(controls_df["control_field"])
    matches = []
    for fine in (True, False):
        names = {f"{c.name}_{c.geography}"
                 for c in control_spec.tier0_backbone_catalog(fine_teen_age_bands=fine)
                 if c.geography == control_spec.GEO_100M
                 and c.seed_table == control_spec.SEED_TABLE_PERSONS}
        if names <= fields:
            matches.append(fine)
    if len(matches) != 1:
        raise ValueError(f"controls.csv matches the person backbone of fine_teen_age_bands {matches}; "
                         "give --fine-teen-age-bands on|off")
    return matches[0]


def check_unstratified_seed(prod_batches: list[Path], base: Path) -> dict:
    """``ValueError`` unless all production batches carry the same seed files (module docstring)."""
    record = {}
    for name in SEED_FILES:
        digests = {b.name: _sha256(b / name) for b in prod_batches}
        if len(set(digests.values())) != 1:
            raise ValueError(f"{name} differs between the production batches: the run looks "
                             "stratified (stratify_regiostar on), so a batch seed is not the whole "
                             "seed and the employment bridge would not be the pipeline's")
        production = next(iter(digests.values()))
        base_digest = _sha256(base / name)
        if base_digest != production:
            logger.warning("%s of the base batch differs from the production seed; the bridge is "
                           "computed from the base batch's seed", name)
        record[name] = {"production_sha256": production, "base_sha256": base_digest,
                        "base_equals_production": base_digest == production}
    return record


def universe_years(universes) -> list[int]:
    """The single-year ages the universes need (the stage's parquet load rule)."""
    return sorted({year for lower, upper in universes
                   for year in range(int(lower), (controls_builder.SINGLE_YEAR_MAX_AGE
                                                  if upper is None else int(upper)) + 1)})


def load_model_cells(parquet_path: Path, cell_ids, columns) -> pd.DataFrame:
    """Grid id ``ZENSUS100m`` + ``columns`` (cleaned names) of the model cells, nothing else.

    Reads only the needed columns and the rows of ``cell_ids`` (pyarrow filter); the id column is
    the parquet's first column (as ``mid.load_control_cells``). Raises when a column or a cell is
    absent.
    """
    raw_names = pq.ParquetFile(parquet_path).schema.names
    clean_to_raw = {}
    for raw in raw_names:
        clean_to_raw.setdefault(prepared_cells.clean_col_name(raw), raw)
    absent = [c for c in columns if c not in clean_to_raw]
    if absent:
        raise ValueError(f"{parquet_path}: column(s) {absent[:5]} absent ({len(absent)} in total)")
    id_raw = raw_names[0]
    ids = sorted(set(map(str, cell_ids)))
    table = pq.read_table(parquet_path, columns=[id_raw, *[clean_to_raw[c] for c in columns]],
                          filters=[(id_raw, "in", ids)])
    cells = table.to_pandas()
    cells.columns = [folders.GEO_100M, *columns]
    cells[folders.GEO_100M] = cells[folders.GEO_100M].astype(str)
    missing = sorted(set(ids) - set(cells[folders.GEO_100M]))
    if missing or cells[folders.GEO_100M].duplicated().any():
        raise ValueError(f"{parquet_path}: {len(missing)} model cell(s) absent ({missing[:3]}) or "
                         "duplicate grid ids")
    return cells


def active_entries_of(controls_df: pd.DataFrame) -> list:
    """REGISTRY entries whose KREIS control columns are all in ``controls.csv``."""
    fields = set(controls_df["control_field"])
    return [e for e in kac.REGISTRY
            if all(f"{c}_{folders.GEO_KREIS}" in fields for c in kac.control_columns(e))]


def tier3_names_of(kreis_columns) -> list[str]:
    """The Tier-3 catalog KREIS controls present in the KREIS table (the stage's tier3 names)."""
    names = controls_builder._kreis_controls_map(control_spec.tier3_controls())
    return [n for n in names if n in set(kreis_columns)]


def universe_step(kreis: pd.DataFrame, crosswalk: pd.DataFrame, controls_df: pd.DataFrame,
                  args, log: dict) -> pd.DataFrame:
    """A3: rebuild the controls with an age universe from the Kreis targets (module docstring)."""
    batches = production_batches(args.prod_work_dir, expected_batches=args.expected_batches)
    targets = reconstruct_kreis_targets(batches, expected_kreise=args.expected_kreise)
    full_xwalk = pd.concat([read_table(b / "data" / "geo_cross_walk.csv") for b in batches],
                           ignore_index=True).drop_duplicates(folders.GEO_100M)
    active = active_entries_of(controls_df)
    tier3 = tier3_names_of(kreis.columns)
    universes = controls_builder.apportion_universes(active, tier3)
    fine_teen = (fine_teen_age_bands_of(controls_df) if args.fine_teen_age_bands is None
                 else args.fine_teen_age_bands == "on")
    band_columns = list(controls_builder.person_band_census_columns(fine_teen_age_bands=fine_teen))
    year_columns = [f"{sex}_AGE_{y}" for sex in ("M", "F") for y in universe_years(universes)]
    cells = load_model_cells(args.cells_100m, full_xwalk[folders.GEO_100M],
                             list(dict.fromkeys([*year_columns, *band_columns])))
    cells, column_of = controls_builder.add_universe_weight_columns(cells, universes)
    kreis_by_row = cells[folders.GEO_100M].map(
        full_xwalk.set_index(folders.GEO_100M)[folders.GEO_KREIS])
    persons = controls_builder.person_total_by_kreis(cells, kreis_by_row,
                                                     fine_teen_age_bands=fine_teen)
    log["kreis_targets"] = {
        "n_batches": len(batches), "kreise": targets[ARS_KREIS].tolist(),
        "fine_teen_age_bands": fine_teen,
        "trip_class_partition": check_trip_class_partition(
            targets, persons, tolerance_persons=args.trip_class_tolerance_persons)}
    apportion_map = controls_builder.apportion_weight_columns(active, tier3, column_of)
    by_col = {col: mid._kreis_pop_from_crosswalk(cells, full_xwalk, weight_col=col)
              for col in sorted(set(apportion_map.values()))}
    controls_map = {name: (name,) for name in apportion_map}
    dropped = batch_folders.log_empty_region_universes(by_col, apportion_map, targets, controls_map)
    batch_cells = cells[cells[folders.GEO_100M].isin(set(crosswalk[folders.GEO_100M]))]
    apportioned = batch_folders.universe_apportion_weights(batch_cells, crosswalk, by_col, apportion_map.values())
    rebuilt = folders.build_kreis_control_totals(
        targets, crosswalk, controls_map=controls_map,
        apportion_weights_by_control={c: apportioned.weights[col] for c, col in apportion_map.items()})
    out = kreis.copy()
    rebuilt = rebuilt.set_index(folders.GEO_KREIS).loc[out[folders.GEO_KREIS]]
    for name in apportion_map:
        out[name] = rebuilt[name].to_numpy()
    log["active_entries"] = [e.name for e in active]
    log["tier3_names"] = tier3
    log["apportion_map"] = apportion_map
    log["stats"]["kreis_universe"] = {
        "n_controls": len(apportion_map), "n_parts": apportioned.n_parts,
        "n_empty_part": apportioned.n_empty_part, "n_empty_region": apportioned.n_empty_region,
        "dropped_target_mass_by_universe_column": dropped}
    return out


def follow_grids_step(kreis, fine, crosswalk, controls_df, log) -> pd.DataFrame:
    """A4: the grid-following KREIS controls set to the batch sums of their grids."""
    mapping = control_spec.kreis_controls_following_grids(controls_df)
    fields = set(controls_df["control_field"])
    stats: dict = {}
    out = folders.set_kreis_controls_from_fine_sums(kreis, fine, crosswalk, mapping, stats=stats)
    stats["controls_without_grid"] = sorted(
        k for k in control_spec.kreis_grid_pairs() if k in fields and k not in mapping)
    log["stats"]["kreis_follow_grids"] = stats
    return out


def employment_step(kreis, fine, crosswalk, folder: Path, args, log) -> pd.DataFrame:
    """A5: the employed classes of the KREIS employment families at the Zensus level."""
    prod = production_batches(args.prod_work_dir, expected_batches=args.expected_batches)
    log["seed_check"] = check_unstratified_seed(prod, args.base)
    seed_persons = pd.read_csv(folder / "data" / "seed_persons.csv")
    seed_households = pd.read_csv(folder / "data" / "seed_households.csv")
    report = employment_level.employed_bridge_report(
        seed_persons, seed_households, grid_min_age=employment_level.employed_grid_min_age(),
        family_min_age=kac.WORK_BY_EMPLOYMENT_MIN_AGE_YEARS)
    log["bridge"] = report._asdict()
    stats: dict = {}
    out = employment_level.reconcile_kreis_employment_levels(
        kreis, fine, crosswalk, bridge=report.bridge, stats=stats)
    log["stats"]["employment_level"] = stats
    return out


def column_changes(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """Per KREIS column: changed (any value differs) and the largest absolute change."""
    if list(before.columns) != list(after.columns) or len(before) != len(after):
        raise RuntimeError("the rewritten KREIS table has other columns or rows than the base table")
    changes = {}
    for column in before.columns:
        if column == folders.GEO_KREIS:
            changes[column] = {"changed": bool((before[column] != after[column]).any())}
            continue
        old, new = before[column].to_numpy(dtype=float), after[column].to_numpy(dtype=float)
        changes[column] = {"changed": not np.array_equal(old, new),
                           "max_abs_change": float(np.max(np.abs(new - old))) if len(old) else 0.0}
    return changes


def file_changes(base: Path, out: Path) -> dict:
    """Per file of ``configs/`` and ``data/`` (either side): sha256 of base and out and ``changed``."""
    names = set()
    for root in (base, out):
        for sub in ("configs", "data"):
            names.update(p.relative_to(root).as_posix() for p in (root / sub).rglob("*") if p.is_file())
    record = {}
    for name in sorted(names):
        a, b = base / name, out / name
        sha_a = _sha256(a) if a.is_file() else None
        sha_b = _sha256(b) if b.is_file() else None
        record[name] = {"changed": sha_a != sha_b, "sha256_base": sha_a, "sha256_out": sha_b}
    return record


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, type=Path, help="base batch folder")
    parser.add_argument("--out", required=True, type=Path, help="new batch folder (must not exist)")
    parser.add_argument("--steps", required=True, help=f"comma list of {', '.join(STEPS)}")
    parser.add_argument("--prod-work-dir", type=Path, help="production work dir (kreis_universe)")
    parser.add_argument("--cells-100m", type=Path, help="prepared 100 m cell parquet (kreis_universe)")
    parser.add_argument("--commit", required=True, help="eqasim-bs commit of the library code")
    parser.add_argument("--fine-teen-age-bands", choices=("on", "off"),
                        help="band set of the trip_class person total (default: read from controls.csv)")
    parser.add_argument("--expected-kreise", type=int, default=8)
    parser.add_argument("--expected-batches", type=int, default=60)
    parser.add_argument("--trip-class-tolerance-persons", type=float, default=1.0)
    args = parser.parse_args(argv)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = [s for s in steps if s not in STEPS]
    if unknown or not steps or len(set(steps)) != len(steps):
        parser.error(f"--steps: give distinct names of {list(STEPS)}, got {steps}")
    if "kreis_universe" in steps and (args.prod_work_dir is None or args.cells_100m is None):
        parser.error("kreis_universe needs --prod-work-dir and --cells-100m")
    if "employment_level" in steps and args.prod_work_dir is None:
        parser.error("employment_level needs --prod-work-dir (the unstratified-seed check)")
    args.steps = [s for s in STEPS if s in steps]  # the library order, whatever the given order
    return args


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    base, out = args.base, args.out
    if out.exists():
        print(f"refuse: {out} exists", file=sys.stderr)
        return 1
    log = {"base": str(base), "out": str(out), "steps": args.steps, "commit": args.commit,
           "prod_work_dir": None if args.prod_work_dir is None else str(args.prod_work_dir),
           "cells_100m": None if args.cells_100m is None else str(args.cells_100m),
           "expected_kreise": args.expected_kreise, "expected_batches": args.expected_batches,
           "bridge": None, "stats": {}}
    out.mkdir(parents=True)
    shutil.copytree(base / "configs", out / "configs")
    shutil.copytree(base / "data", out / "data")
    (out / "output").mkdir()

    data = out / "data"
    controls_df = pd.read_csv(out / "configs" / "controls.csv")
    crosswalk = read_table(data / "geo_cross_walk.csv")
    fine = {geo: read_table(data / f"control_totals_{geo}.csv")
            for geo in (folders.GEO_100M, folders.GEO_1KM)}
    before = read_table(data / "control_totals_KREIS.csv")
    kreis = before.copy()
    if "kreis_universe" in args.steps:
        kreis = universe_step(kreis, crosswalk, controls_df, args, log)
    if "kreis_follow_grids" in args.steps:
        kreis = follow_grids_step(kreis, fine, crosswalk, controls_df, log)
    if "employment_level" in args.steps:
        kreis = employment_step(kreis, fine, crosswalk, out, args, log)
    write_kreis_table(out, kreis)

    log["kreis_columns"] = column_changes(before, read_table(data / "control_totals_KREIS.csv"))
    log["files"] = file_changes(base, out)
    log["unexpected_changed_files"] = [n for n, e in log["files"].items()
                                       if e["changed"] and n != KREIS_FILE]
    (out / "prepare_log.json").write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    changed_columns = [c for c, e in log["kreis_columns"].items() if e["changed"]]
    print(json.dumps({"out": str(out), "steps": args.steps, "changed_kreis_columns": changed_columns,
                      "unexpected_changed_files": log["unexpected_changed_files"]}, indent=2))
    if log["unexpected_changed_files"]:
        print(f"FAIL: files other than {KREIS_FILE} changed: {log['unexpected_changed_files']}",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
