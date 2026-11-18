"""Prepare one batch folder of ladder arm 4 of issue #657 (the tenure rule) from an arm-3 batch folder.

Purpose: arm 4 is arm 3 with ONLY the two tenure control columns replaced. This driver copies the
arm-3 batch folder byte for byte (``configs/`` and ``data/``, an empty ``output/``) and rewrites

* ``EigentuemerHH_Tenure_100m_Gitter_ZENSUS100m`` / ``MieterHH_Tenure_100m_Gitter_ZENSUS100m`` in
  ``data/control_totals_ZENSUS100m.csv``, and
* ``EigentuemerHH_Tenure_100m_Gitter_ZENSUS1km`` / ``MieterHH_Tenure_100m_Gitter_ZENSUS1km`` in
  ``data/control_totals_ZENSUS1km.csv`` (the sums of the 100 m integers per 1 km parent),

from the real prepared cell grid. The steps use the library functions the pipeline uses:
``braunschweig.popsim.mid.load_control_cells`` (loader), ``tenure_targets.limit_owner_households_to_dwellings``
(the rule; skipped with ``--rule off``) and ``braunschweig.popsim.mid.build_control_totals`` (fills NaN
with 0 and integerises each column within its 1 km parent with ``controls.integerize_within_parents``).
The cells go into the integerisation in the loader's row order (the pipeline's order, restricted to the
batch's cells); only the written columns are reindexed to the row order of the folder's tables.

``--rule off`` is the self-check: the tenure columns are rebuilt without the rule and must reproduce the
arm-3 folder exactly (no changed file). If they do not, the driver does not rebuild the columns the way
the pipeline did, and the run is not evidence for the rule.

Inputs: ``--base`` an arm-3 batch folder, ``--cells-100m`` the prepared 100 m cell parquet.
Outputs: ``--out`` the new batch folder (must not exist) and the log ``--log`` (default
``<out>/prepare_log.json``): arguments, rule on/off, the rule's report dict, the library commit, the
sha256 of every file of base and out, and per file (and per column of the two control tables) whether it
changed. Exit code 0: only the two tenure columns of the two tables changed (with ``--rule off``: nothing
changed). Exit code 1: anything else changed, or a cell of the batch is not in the grid. Exit code 2: the
arguments are unusable (``--out`` exists, no library commit).

Pipeline difference: none known in the rebuild itself; the cells of the batch are loaded from the grid
and not filtered by the pipeline's zone filter (the batch's cell list is the filter).

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python prepare_tenure_arm.py --base <arm-3 batch folder> --out <new batch folder> \
      --cells-100m <prepared grid parquet> [--rule off] [--min-households-outside 10.0] \
      [--library-commit <sha>] [--log <file>]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

from braunschweig.popsim import control_spec, mid, tenure_targets

CELL_ID = "ZENSUS100m"
PARENT_ID = "ZENSUS1km"
OWNER_BASE = next(name for name, _ in control_spec._TIER2_TENURE_ENTRIES if name.startswith("Eigentuemer"))
RENTER_BASE = next(name for name, _ in control_spec._TIER2_TENURE_ENTRIES if name.startswith("Mieter"))
TENURE_BASES = (OWNER_BASE, RENTER_BASE)
# The two control tables of a batch folder, their key column and the suffix of their tenure columns.
CONTROL_TABLES = {
    "data/control_totals_ZENSUS100m.csv": (CELL_ID, mid.SUFFIX_100M),
    "data/control_totals_ZENSUS1km.csv": (PARENT_ID, mid.SUFFIX_1KM),
}
CROSSWALK = "data/geo_cross_walk.csv"
EXIT_UNEXPECTED_CHANGE = 1
EXIT_BAD_ARGUMENTS = 2


def tenure_columns(table: str) -> tuple:
    """The two tenure column names of one control table."""
    suffix = CONTROL_TABLES[table][1]
    return tuple(f"{base}{suffix}" for base in TENURE_BASES)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_hashes(root: Path) -> dict:
    """sha256 of every file under ``configs/`` and ``data/`` of ``root``, keyed by the relative POSIX path.

    ``output/`` is left out: the arm-3 folder holds its run outputs there, the new folder must hold none.
    """
    return {p.relative_to(root).as_posix(): sha256(p)
            for sub in ("configs", "data") for p in sorted((root / sub).rglob("*")) if p.is_file()}


def read_text_table(path: Path) -> pd.DataFrame:
    """A CSV as text (no number parsing, empty cells stay empty), so unchanged cells stay byte-equal."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def line_terminator(path: Path) -> str:
    """The line ending of an existing CSV, so a rewrite does not change it."""
    return "\r\n" if b"\r\n" in path.read_bytes()[:65536] else "\n"


def compute_tenure_tables(base: Path, cells_path: Path, *, rule_on: bool, min_households_outside: float):
    """The new 100 m and 1 km tenure columns for the cells of the batch folder ``base``.

    Returns ``(new_100m, new_1km, report)``: two frames indexed by the cell and parent ids holding the two
    suffixed tenure columns of ``mid.build_control_totals`` (int64), and the rule's report dict (``None``
    with the rule off). Raises ``ValueError`` when a cell of the batch is not in the grid.
    """
    batch_table = read_text_table(base / "data/control_totals_ZENSUS100m.csv")
    batch_ids = batch_table[CELL_ID]
    crosswalk = pd.read_csv(base / CROSSWALK, dtype=str, keep_default_na=False,
                            usecols=[CELL_ID, PARENT_ID])
    columns = [control_spec.HH_TOTAL_CENSUS_COLUMN, *TENURE_BASES, control_spec.OWNER_QUOTE_DWELLING_COLUMN]
    cells = mid.load_control_cells(cells_path, columns)
    absent = [c for c in columns if c not in cells.columns]
    if absent:
        raise ValueError(f"the prepared grid {cells_path} has no column(s) {absent}")
    if cells[CELL_ID].duplicated().any():
        raise ValueError(f"the prepared grid {cells_path} holds a cell id more than once")
    # Loader order = the pipeline's order; only the batch's cells are kept.
    cells = cells[cells[CELL_ID].isin(set(batch_ids))].reset_index(drop=True)
    missing = sorted(set(batch_ids) - set(cells[CELL_ID]))
    if missing:
        raise ValueError(f"{len(missing)} of {len(batch_ids)} cells of the batch are not in the prepared "
                         f"grid {cells_path} (first: {missing[0]})")
    if crosswalk[CELL_ID].duplicated().any() or not set(cells[CELL_ID]) <= set(crosswalk[CELL_ID]):
        raise ValueError(f"{base / CROSSWALK} does not hold every cell of the batch exactly once")

    report = None
    if rule_on:
        cells, report = tenure_targets.limit_owner_households_to_dwellings(
            cells, owner_col=OWNER_BASE, renter_col=RENTER_BASE,
            household_total_col=control_spec.HH_TOTAL_CENSUS_COLUMN,
            dwelling_col=control_spec.OWNER_QUOTE_DWELLING_COLUMN,
            min_households_outside=min_households_outside)
    tables = mid.build_control_totals(cells, crosswalk, list(TENURE_BASES))
    return tables[CELL_ID].set_index(CELL_ID), tables[PARENT_ID].set_index(PARENT_ID), report


def rewrite_control_tables(out: Path, new_100m: pd.DataFrame, new_1km: pd.DataFrame) -> None:
    """Overwrite the two tenure columns in the two control tables of ``out``; keep rows, order, format."""
    new_columns = {"data/control_totals_ZENSUS100m.csv": new_100m, "data/control_totals_ZENSUS1km.csv": new_1km}
    for table, new in new_columns.items():
        key = CONTROL_TABLES[table][0]
        path = out / table
        terminator = line_terminator(path)
        frame = read_text_table(path)
        for column in tenure_columns(table):
            if column not in frame.columns:
                raise ValueError(f"{path} has no column {column!r}")
            values = new[column].reindex(frame[key])
            if values.isna().any():
                raise ValueError(f"{path}: {int(values.isna().sum())} row(s) have no new value for {column!r}")
            frame[column] = values.astype("int64").astype(str).to_numpy()
        frame.to_csv(path, index=False, lineterminator=terminator)


def compare_folders(base: Path, out: Path) -> tuple:
    """Per-file change record of ``out`` against ``base`` and the list of unexpected changes.

    A change is expected only in the two tenure columns of the two control tables, at unchanged row and
    column structure, and a byte change of such a table must come with a changed tenure column (a pure
    format change is unexpected). Every other file must be byte-identical.
    """
    base_hashes, out_hashes = file_hashes(base), file_hashes(out)
    files, unexpected = {}, []
    for name in sorted(set(base_hashes) | set(out_hashes)):
        entry = {"sha256_base": base_hashes.get(name), "sha256_out": out_hashes.get(name)}
        entry["changed"] = entry["sha256_base"] != entry["sha256_out"]
        if name in CONTROL_TABLES and name in base_hashes and name in out_hashes:
            before, after = read_text_table(base / name), read_text_table(out / name)
            entry["structure_changed"] = (list(before.columns) != list(after.columns)
                                          or len(before) != len(after))
            entry["columns_changed"] = [] if entry["structure_changed"] else [
                c for c in before.columns if not before[c].equals(after[c])]
            allowed = set(tenure_columns(name))
            if entry["structure_changed"]:
                unexpected.append(f"{name}: rows or columns changed")
            for column in entry["columns_changed"]:
                if column not in allowed:
                    unexpected.append(f"{name}: column {column} changed")
            if entry["changed"] and not entry["structure_changed"] and not entry["columns_changed"]:
                unexpected.append(f"{name}: bytes changed but no column value (format change)")
        elif entry["changed"]:
            unexpected.append(f"{name}: changed" if entry["sha256_base"] and entry["sha256_out"]
                              else f"{name}: present on one side only")
        files[name] = entry
    return files, unexpected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", required=True, type=Path, help="arm-3 batch folder")
    parser.add_argument("--out", required=True, type=Path, help="new batch folder (must not exist)")
    parser.add_argument("--cells-100m", required=True, type=Path, help="prepared 100 m cell parquet")
    parser.add_argument("--rule", choices=("on", "off"), default="on")
    parser.add_argument("--min-households-outside", type=float,
                        default=tenure_targets.DEFAULT_MIN_HOUSEHOLDS_OUTSIDE)
    parser.add_argument("--library-commit", default=os.environ.get("EQASIM_BS_COMMIT"))
    parser.add_argument("--log", type=Path, help="default: <out>/prepare_log.json")
    args = parser.parse_args(argv)

    if not args.library_commit:
        print("refuse: no library commit (--library-commit or EQASIM_BS_COMMIT)", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    if args.out.exists():
        print(f"refuse: {args.out} exists", file=sys.stderr)
        return EXIT_BAD_ARGUMENTS
    for sub in ("configs", "data"):
        if not (args.base / sub).is_dir():
            print(f"refuse: {args.base / sub} is not a directory", file=sys.stderr)
            return EXIT_BAD_ARGUMENTS
    rule_on = args.rule == "on"

    problems = []
    report = None
    files = {}
    # Any failure (a library KeyError, a full disk, a missing grid column) ends in a log with the error
    # text and exit 1; a half-built folder never stays without a log.
    try:
        args.out.mkdir(parents=True)
        shutil.copytree(args.base / "configs", args.out / "configs")
        shutil.copytree(args.base / "data", args.out / "data")
        (args.out / "output").mkdir()
        new_100m, new_1km, report = compute_tenure_tables(
            args.base, args.cells_100m, rule_on=rule_on,
            min_households_outside=args.min_households_outside)
        rewrite_control_tables(args.out, new_100m, new_1km)
        files, unexpected = compare_folders(args.base, args.out)
        problems.extend(unexpected)
        if any((args.out / "output").iterdir()):
            problems.append("output/: not empty")
        if not rule_on:
            problems.extend(f"{name}: changed with --rule off (the self-check needs an exact reproduction)"
                            for name, entry in files.items() if entry["changed"])
    except Exception as error:  # noqa: BLE001 - recorded in the log, then exit 1
        problems.append(f"{type(error).__name__}: {error}")
    # Inside --out when it exists, else next to it.
    log_path = args.log or (args.out / "prepare_log.json" if args.out.is_dir()
                            else args.out.with_name(args.out.name + ".prepare_log.json"))
    record = {
        "arguments": {"base": str(args.base), "out": str(args.out), "cells_100m": str(args.cells_100m),
                      "rule": args.rule, "min_households_outside": args.min_households_outside},
        "rule_on": rule_on, "rule_report": report, "library_commit": args.library_commit,
        "files": files, "problems": problems, "ok": not problems,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    changed = {n: e.get("columns_changed", "file") for n, e in files.items() if e["changed"]}
    print(json.dumps({"out": str(args.out), "rule": args.rule, "rule_report": report,
                      "changed": changed, "problems": problems}, indent=2))
    return EXIT_UNEXPECTED_CHANGE if problems else 0


if __name__ == "__main__":
    sys.exit(main())
