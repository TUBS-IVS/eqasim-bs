"""Side-by-side table of the headline numbers of two arm pairs at different sample sizes (issue #436, parking cost zones v3).

An analysis aid, never a pipeline stage and never a model input. Reads the tables that ``compare_parking_targets.py`` and
``summarize_arm_set.py`` wrote for the small sample (e.g. 1 %, arms ``v3`` and ``off``) and the large sample (e.g. 25 %,
arms ``v3_25pct`` and ``off_25pct``) and puts the headline numbers next to each other, so the reader sees how much a number of
the small sample moves at the large one. Nothing is computed from the run outputs and nothing is typed: every cell is a value
of one of the eight input tables, and the provenance lists them with SHA-256.

This is a comparison of two runs per sample, never a validation. The change of a number between the samples mixes the sample
size with the stochastic noise of one run each (and, for the 25 % pair, a different population build); the within-run SD of
``summarize_arm_set.py`` is the spread over iterations of one run, not a between-run noise.

Output ``<out-prefix>_side_by_side.csv`` (snake_case columns): ``metric``, ``source`` (the table the value comes from),
``unit`` (``share``, ``cents`` or ``count``), ``universe`` (what the value is a share or mean of), ``off_small``,
``v3_small``, ``off_large``, ``v3_large``, ``n_v3_small`` and ``n_v3_large`` (the size of the universe of the v3 value: trips,
pricing calls or persons, empty where the source has none), ``v3_large_minus_small`` (large minus small for the v3 arm: percentage
points for a share, euro cents for a mean cost, empty for a count: a count scales with the sample),
``v3_minus_off_small`` and ``v3_minus_off_large`` (the v3 arm minus the off arm within one sample, same units: the contrast of
each sample; the row ``car_trip_share_ending_outside_zones`` is the trips that end outside every parking zone, which the zone
cost does not price directly, so it is the contrast row for the rows of the zones Ia and Ib). An arm without the value
(the ``off`` arms price nothing) has an empty cell, never a zero.

Usage (from the repository root)::

    python scripts/parking/side_by_side_sample_sizes.py --folder docs/runs/artifacts/<folder> \\
        --set-small 1pct_pv3_2026-10-15 --set-large 25pct_pv3_2026-10-15 --arms-small v3,off \\
        --arms-large v3_25pct,off_25pct --out-prefix arm_set_summary_pv3_1pct_vs_25pct_2026-10-15
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent

log = logging.getLogger("side_by_side_sample_sizes")

#: Metrics of the comparison table (``parking_targets_comparison_<set>.csv``): (metric, unit).
COMPARISON_METRICS = (
    ("car_trip_share_all_trips", "share"), ("car_trip_share_ending_in_zones", "share"),
    ("car_trip_share_ending_outside_zones", "share"), ("car_trip_share_ending_in_ia_ib", "share"), ("paid_share_ia_ib_all_purposes", "share"),
    ("paid_share_ia_ib_other_purposes", "share"), ("garage_share_ia_ib_other_purposes", "share"),
    ("garage_share_ia_ib_work_education_paid_calls", "share"), ("mean_expected_cost_cents_ia_ib_work_education", "cents"),
    ("mean_expected_cost_cents_ia_ib_other_purposes", "cents"), ("mean_expected_cost_cents_all_zones_work_education", "cents"),
    ("mean_expected_cost_cents_all_zones_other_purposes", "cents"), ("ev_exempt_share_all_zones", "share"))
#: Zone groups and their columns taken from the zone group table (purpose class ``all``): (column, unit).
ZONE_GROUPS = ("bs_zone_ia", "bs_zone_ib", "wob", "other")
ZONE_COLUMNS = (("paid_share", "share"), ("garage_share_paid_options", "share"), ("garage_share_free_options", "share"),
                ("mean_expected_cost_cents", "cents"))
#: Counts summed over the zone groups of the purpose class ``all`` (a count scales with the sample size).
ZONE_COUNT_COLUMNS = ("ev_exempt_count", "no_feasible_option_count")
FREE_SHARE_COLUMNS = (("person_free_share", "person_free_share_draw", "persons_in_paid_zones"),
                      ("call_employer_free_share_draw_zones", "call_employer_free_share_draw_zones",
                       "work_education_calls_in_draw_zones"))
MODESTATS_MODE = "car"
PURPOSE_CLASS_ALL = "all"
SHARE_DECIMALS = 6
COST_DECIMALS = 4
PERCENTAGE_POINT_DECIMALS = 4
#: Columns that hold a difference (percentage points for a share, euro cents for a mean cost, empty for a count).
DIFFERENCE_COLUMNS = ("v3_large_minus_small", "v3_minus_off_small", "v3_minus_off_large")
OUTPUT_COLUMNS = ["metric", "source", "unit", "universe", "off_small", "v3_small", "off_large", "v3_large", "n_v3_small",
                  "n_v3_large", "v3_large_minus_small", "v3_minus_off_small", "v3_minus_off_large"]


class InputError(ValueError):
    """An input table or an arm is missing or does not have the expected content: the message names the file and the fix."""


def _load_compare_script():
    """Load ``compare_parking_targets.py`` (the scripts are no package) for ``PathRecorder`` and the code state."""
    name = "compare_parking_targets_for_side_by_side"
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / "compare_parking_targets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise InputError(f"input table missing: {path}")
    return pd.read_csv(path, keep_default_na=False)


def _number(value):
    """A table cell as a float; an empty cell is NaN (the arm has no such value)."""
    return np.nan if value == "" or value is None else float(value)


def _pick(frame: pd.DataFrame, arm: str, key_column: str, key: str, value_column: str, source: Path):
    """The single value of ``value_column`` for the row of ``arm`` and ``key``; ``InputError`` names a missing row."""
    rows = frame[(frame["arm"] == arm) & (frame[key_column] == key)]
    if len(rows) != 1:
        raise InputError(f"{source.name}: expected exactly one row for arm {arm!r} and {key_column} {key!r}, found {len(rows)}")
    return rows.iloc[0][value_column]


def _check_arms(frame: pd.DataFrame, arms, source: Path) -> None:
    for arm in arms:
        if arm not in set(frame["arm"]):
            raise InputError(f"{source.name}: arm {arm!r} not in the table (arms {sorted(set(frame['arm']))})")


def _row(metric, source, unit, universe, values, sizes):
    """One output row; ``values`` = (off_small, v3_small, off_large, v3_large), ``sizes`` = (n_v3_small, n_v3_large)."""
    small, large = values[1], values[3]
    if unit == "share":
        change = 100.0 * (large - small)
        contrast_small, contrast_large = 100.0 * (values[1] - values[0]), 100.0 * (values[3] - values[2])
    elif unit == "cents":
        change = large - small
        contrast_small, contrast_large = values[1] - values[0], values[3] - values[2]
    else:
        change = contrast_small = contrast_large = np.nan
    return {"metric": metric, "source": source, "unit": unit, "universe": universe, "off_small": values[0], "v3_small": values[1],
            "off_large": values[2], "v3_large": values[3], "n_v3_small": sizes[0], "n_v3_large": sizes[1],
            "v3_large_minus_small": change, "v3_minus_off_small": contrast_small, "v3_minus_off_large": contrast_large}


def build_side_by_side(folder, set_small: str, set_large: str, *, arms_small, arms_large) -> pd.DataFrame:
    """The side-by-side table (columns ``OUTPUT_COLUMNS``) of the sample ``set_small`` and ``set_large`` in ``folder``.

    ``arms_small`` and ``arms_large`` are ``(v3 arm, off arm)`` of each sample. Raises ``InputError`` for a missing input table, a
    missing arm or metric, or a metric that is not exactly one row of the table. Reads the comparison table, the pooled modestats,
    the zone group table and the free share table of each sample (see ``input_paths``).
    """
    folder = Path(folder)
    v3_small, off_small = arms_small
    v3_large, off_large = arms_large
    paths = input_paths(folder, set_small, set_large)
    compare_small, compare_large = _read(paths["comparison_small"]), _read(paths["comparison_large"])
    mode_small, mode_large = _read(paths["modestats_small"]), _read(paths["modestats_large"])
    zone_small, zone_large = _read(paths["zone_groups_small"]), _read(paths["zone_groups_large"])
    free_small, free_large = _read(paths["free_share_small"]), _read(paths["free_share_large"])
    for frame, arms, key in ((compare_small, arms_small, "comparison_small"), (compare_large, arms_large, "comparison_large"),
                             (mode_small, arms_small, "modestats_small"), (mode_large, arms_large, "modestats_large")):
        _check_arms(frame, arms, paths[key])
    for frame, arm, key in ((zone_small, v3_small, "zone_groups_small"), (zone_large, v3_large, "zone_groups_large"),
                            (free_small, v3_small, "free_share_small"), (free_large, v3_large, "free_share_large")):
        _check_arms(frame, [arm], paths[key])

    rows = []
    for metric, unit in COMPARISON_METRICS:
        def value(frame, arm, key):
            return _number(_pick(frame, arm, "metric", metric, "model", paths[key]))

        def size(frame, arm, key):
            return _number(_pick(frame, arm, "metric", metric, "universe_size", paths[key]))

        universe = _pick(compare_large, v3_large, "metric", metric, "universe", paths["comparison_large"])
        rows.append(_row(metric, "parking_targets_comparison", unit, universe,
                         (value(compare_small, off_small, "comparison_small"), value(compare_small, v3_small, "comparison_small"),
                          value(compare_large, off_large, "comparison_large"), value(compare_large, v3_large, "comparison_large")),
                         (size(compare_small, v3_small, "comparison_small"), size(compare_large, v3_large, "comparison_large"))))
    # the modestats are the MATSim mode statistics pooled over the selected iterations (a different universe from the trips)
    car = {key: {arm: _number(_pick(frame[frame["mode"] == MODESTATS_MODE], arm, "mode", MODESTATS_MODE, "mean_share", paths[key]))
                 for arm in arms} for key, frame, arms in (("modestats_small", mode_small, arms_small),
                                                           ("modestats_large", mode_large, arms_large))}
    rows.append(_row("car_share_modestats_mean", "modestats_pooled", "share", "modestats_all_legs_pooled_iterations",
                     (car["modestats_small"][off_small], car["modestats_small"][v3_small], car["modestats_large"][off_large],
                      car["modestats_large"][v3_large]), (np.nan, np.nan)))
    for group in ZONE_GROUPS:
        for column, unit in ZONE_COLUMNS:
            def zone_value(frame, arm, key):
                rows_ = frame[(frame["arm"] == arm) & (frame["zone_group"] == group) & (frame["purpose_class"] == PURPOSE_CLASS_ALL)]
                if len(rows_) != 1:
                    raise InputError(f"{paths[key].name}: expected one row for arm {arm!r}, zone group {group!r}, purpose class "
                                     f"{PURPOSE_CLASS_ALL!r}, found {len(rows_)}")
                return rows_.iloc[0]

            small, large = zone_value(zone_small, v3_small, "zone_groups_small"), zone_value(zone_large, v3_large, "zone_groups_large")
            rows.append(_row(f"zone_{group}_{column}", "zone_groups", unit, f"pricing_calls_zone_group_{group}_all_purposes",
                             (np.nan, _number(small[column]), np.nan, _number(large[column])),
                             (_number(small["priced_calls"]), _number(large["priced_calls"]))))
    for column in ZONE_COUNT_COLUMNS:
        totals = []
        for frame, arm in ((zone_small, v3_small), (zone_large, v3_large)):
            selected = frame[(frame["arm"] == arm) & (frame["purpose_class"] == PURPOSE_CLASS_ALL)]
            totals.append((int(selected[column].sum()), int(selected["priced_calls"].sum())))
        rows.append(_row(f"{column}_all_zone_groups", "zone_groups", "count", "pricing_calls_all_zone_groups_all_purposes",
                         (np.nan, totals[0][0], np.nan, totals[1][0]), (totals[0][1], totals[1][1])))
    for column, metric, size_column in FREE_SHARE_COLUMNS:
        row_small = free_small[free_small["arm"] == v3_small].iloc[0]
        row_large = free_large[free_large["arm"] == v3_large].iloc[0]
        small, large = _number(row_small[column]), _number(row_large[column])
        sizes = (_number(row_small[size_column]), _number(row_large[size_column]))
        universe = "persons_in_street_and_resident_zones" if size_column == "persons_in_paid_zones" \
            else "work_education_calls_in_free_draw_zones"
        rows.append(_row(metric, "free_share", "share", universe, (np.nan, small, np.nan, large), sizes))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def input_paths(folder: Path, set_small: str, set_large: str) -> dict:
    """The eight input tables of the two samples (names as written by the two scripts)."""
    paths = {}
    for suffix, token in (("small", set_small), ("large", set_large)):
        paths[f"comparison_{suffix}"] = folder / f"parking_targets_comparison_{token}.csv"
        for name in ("modestats_pooled", "zone_groups", "free_share"):
            paths[f"{name.replace('_pooled', '')}_{suffix}"] = folder / f"arm_set_summary_{token}_{name}.csv"
    return paths


def _text(frame: pd.DataFrame) -> pd.DataFrame:
    """Fixed decimals for the written table (determinism); counts as integers, empty for a missing value."""
    out = frame.copy().astype(object)
    for index, row in frame.iterrows():
        for column in ("off_small", "v3_small", "off_large", "v3_large", "n_v3_small", "n_v3_large", "v3_large_minus_small",
                       "v3_minus_off_small", "v3_minus_off_large"):
            value = row[column]
            difference = column in DIFFERENCE_COLUMNS
            if pd.isna(value):
                out.at[index, column] = ""
            elif column.startswith("n_") or row["unit"] == "count" and not difference:
                out.at[index, column] = f"{int(value)}"
            elif difference:
                out.at[index, column] = f"{value:.{PERCENTAGE_POINT_DECIMALS}f}"
            else:
                out.at[index, column] = f"{value:.{COST_DECIMALS if row['unit'] == 'cents' else SHARE_DECIMALS}f}"
    return out


def write_outputs(table: pd.DataFrame, paths: dict, folder: Path, out_prefix: str, *, compare, overwrite: bool = False,
                  path_roots=(), sets=None) -> list[Path]:
    """Write ``<out-prefix>_side_by_side.csv`` and ``_provenance.json`` in ``folder``; ``FileExistsError`` unless ``overwrite``."""
    table_path = folder / f"{out_prefix}_side_by_side.csv"
    provenance_path = folder / f"{out_prefix}_side_by_side_provenance.json"
    existing = [path for path in (table_path, provenance_path) if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"{existing[0]} exists; pass --overwrite to replace the output files")
    recorder = compare.PathRecorder(path_roots)
    provenance = {"generated_on": datetime.date.today().isoformat(), "script": "scripts/parking/side_by_side_sample_sizes.py",
                  "code_state": compare.calibrate.git_state(), "sets": sets or {},
                  "path_note": "paths are relative to the repository root of the checkout that holds the file",
                  "inputs": {key: {"path": recorder(path), "sha256": _sha256(path)} for key, path in paths.items()},
                  "note": "every value of the table is a value of one of the input tables; a comparison of two runs per sample, "
                          "not a validation"}
    recorder.warn_if_outside("side-by-side")
    folder.mkdir(parents=True, exist_ok=True)
    _text(table).to_csv(table_path, index=False, lineterminator="\n")
    provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [table_path, provenance_path]


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--folder", type=Path, required=True, help="folder with the tables of both samples (also the output)")
    parser.add_argument("--set-small", required=True, help="token of the small sample's tables, e.g. 1pct_pv3_2026-10-15")
    parser.add_argument("--set-large", required=True, help="token of the large sample's tables, e.g. 25pct_pv3_2026-10-15")
    parser.add_argument("--arms-small", required=True, help="V3_ARM,OFF_ARM of the small sample")
    parser.add_argument("--arms-large", required=True, help="V3_ARM,OFF_ARM of the large sample")
    parser.add_argument("--out-prefix", required=True, help="file name prefix of both outputs")
    parser.add_argument("--path-root", dest="path_roots", type=Path, action="append", default=[], metavar="REPOSITORY_ROOT",
                        help="repository root of another checkout (repeatable) for the provenance paths")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    arms = {}
    for name, text in (("--arms-small", args.arms_small), ("--arms-large", args.arms_large)):
        parts = text.split(",")
        if len(parts) != 2 or not all(parts):
            parser.error(f"{name} expects V3_ARM,OFF_ARM, got {text!r}")
        arms[name] = tuple(parts)
    compare = _load_compare_script()
    try:
        table = build_side_by_side(args.folder, args.set_small, args.set_large, arms_small=arms["--arms-small"],
                                   arms_large=arms["--arms-large"])
        written = write_outputs(table, input_paths(args.folder, args.set_small, args.set_large), args.folder, args.out_prefix,
                                compare=compare, overwrite=args.overwrite, path_roots=args.path_roots,
                                sets={"small": {"token": args.set_small, "arms": arms["--arms-small"]},
                                      "large": {"token": args.set_large, "arms": arms["--arms-large"]}})
    except (InputError, FileExistsError) as error:
        raise SystemExit(str(error)) from error
    for path in written:
        log.info("[side-by-side] wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
