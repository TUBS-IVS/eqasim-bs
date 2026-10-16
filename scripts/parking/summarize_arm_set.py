"""Pooled mode shares, zone-group parking tables and free-share tables of an arm set of the parking cost zones v3 runs (issue #436).

An analysis aid, never a pipeline stage and never a model input. ``compare_parking_targets.py`` writes the metrics against the
committed SrV references; this script writes the three tables that script does not: the pooled mode shares of every arm, the
parking outcomes per zone group and the free share of the work and education stays next to the configured shift. A comparison
is a comparison: no row of these tables validates the model, the convergence of a run is not validation and a run of a
sample is not validation.

Inputs per arm (read only; ``LABEL=ARM_DIRECTORY``, the directory of the downloaded comparison subset of one run)
    * ``<arm>/matsim_output/modestats.csv`` (``;``-separated, one row per iteration with the mode share of every mode);
    * ``<arm>/matsim_output/ITERS/it.N/N.parking_outcomes.csv`` of the selected iterations (outcome CSV v4 of the Java parking
      module, read with the strict reader of ``compare_parking_targets.py`` and pooled cell by cell); zone arms only;
    * ``<arm>/*.log`` (exactly one: the pipeline log) with the person-level free-parking draw line of the population stage
      (``attach`` logs it once per population build); zone arms only;
    * ``<arm>/*_resolved.yml`` (exactly one: the resolved config) with ``parking_workplace_free_share_shift``; zone arms only;
    * the committed tariff table (zone type of every zone), ``--data-path``.
    Arms that price no zones (OFF, LEGACY) are given with ``--arm-without-outcomes`` and appear in the mode share table only.

Tables (``<prefix>_<name>.csv`` in ``--out-dir``; an existing file is replaced only with ``--overwrite``)
    * ``modestats_pooled``: per arm and mode the mean and the SAMPLE standard deviation (n - 1) of the mode share over the
      selected iterations, and the difference of the mean to the arm ``--baseline-arm`` (default ``v3``) and to the arm
      ``--off-arm`` (default ``off``) in percentage points. The SD is the iteration-to-iteration spread WITHIN one run (the
      iterations of one run are serially correlated: the same agents keep their plans), NOT the noise between independent
      runs and not a standard error; no difference of two arms is tested for significance.
    * ``zone_groups``: per zone arm, zone group and purpose class the pooled pricing calls (``calls``: every call with a
      zone id, HOME included, the universe of the comparison script), the paid share (PAID_* outcomes and NO_FEASIBLE_OPTION,
      the ``PAID_OUTCOMES`` of the comparison script), the expected garage share split into the paid and the free off-street
      options (``sum(garage_probability_sum_paid) / calls`` and ``sum(garage_probability_sum_free) / calls``), the mean
      expected parking cost per call in UNSCALED euro cents (the tariff cost, not the cost that enters the utility),
      ``ev_exempt_count``, ``street_infeasible_count``, ``no_feasible_option_count`` and ``employer_free_count``. A count is a
      number of pricing calls (car alternatives that mode choice evaluated, chosen or not), not of trips or persons. The zone
      groups are listed in ``ZONE_GROUPS`` (first match wins, ``fnmatch``; every other zone is ``other``) and the column
      ``zone_ids`` names the zones of the report that make up a group.
    * ``zone_group_deltas``: every zone arm against ``--baseline-arm`` on the same rows (difference of the paid share, the
      garage shares and the EV exempt share in percentage points, of the mean cost in euro cents and of the counts), the table
      the arm ``v3_ev_off`` is judged on (it is compared on these rows, not on mode shares).
    * ``free_share``: per zone arm the configured shift, the shift the population stage LOGGED, the person-level free draw
      (persons with a work or education activity in a street or resident zone, those drawn free, the percentage, the campus
      draw) and the call-level EMPLOYER_FREE rate pooled over the selected iterations (``employer_free_count`` over the work
      and education calls in the zones of the free-draw zone types, and in all zones). The two rates are different measures:
      the first is a share of persons, the second a share of pricing calls.
    * ``<prefix>_provenance.json``: the path and the SHA-256 of every input, the iterations, the arms.

Usage (from the repository root)::

    python scripts/parking/summarize_arm_set.py --data-path eqasim-data/data --iteration 51-60 \\
        --arm-without-outcomes off=<arm off> --arm-without-outcomes legacy=<arm legacy> --arm v3=<arm v3> ... \\
        --out-dir docs/runs/artifacts/<folder> --prefix <prefix>
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import logging
import re
import sys
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.parking import attach, cost, zones as parking_zones  # noqa: E402

log = logging.getLogger("summarize_arm_set")


def _load_compare_script():
    """Load ``compare_parking_targets.py`` (the scripts are no package): its strict report reader, its pooling and its outcome
    classes are reused, not copied."""
    name = "compare_parking_targets_for_arm_set_summary"
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / "compare_parking_targets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare = _load_compare_script()

MODESTATS_FILE_NAME = "modestats.csv"
MATSIM_OUTPUT_DIRECTORY = "matsim_output"
CONFIG_SHIFT_KEY = "parking_workplace_free_share_shift"
ARM_LABEL_PATTERN = compare.ARM_LABEL_PATTERN
#: Zone groups of the zone table: (fnmatch pattern, group), the first match wins, every other zone is ``OTHER_GROUP``.
ZONE_GROUPS = (("bs_zone_ia", "bs_zone_ia"), ("bs_zone_ib", "bs_zone_ib"), ("bs_zone_ia_sued", "bs_zone_ia_sued"),
               ("bs_zone_ii", "bs_zone_ii"), ("wob_*", "wob"), ("bh_*", "bh"), ("se_am_markt", "se_am_markt"))
OTHER_GROUP = "other"
PURPOSE_CLASS_ALL, PURPOSE_CLASS_WORK_EDUCATION, PURPOSE_CLASS_OTHER = "all", "work_education", "other_purposes"
#: The universe of the "other purposes" class is the one of the comparison script: not home, work and education.
PURPOSE_CLASSES = (PURPOSE_CLASS_ALL, PURPOSE_CLASS_WORK_EDUCATION, PURPOSE_CLASS_OTHER)
SHARE_DECIMALS = 6
PERCENTAGE_POINT_DECIMALS = 4
COST_DECIMALS = 4
#: Shares of the modes of one iteration sum to 1 up to the rounding of the writer.
MODESTATS_SUM_TOLERANCE = 1e-6

FREE_DRAW_LINE = re.compile(
    r"\[parking\] free-parking draw \(RandomState\(random_seed (?P<seed>\d+) \+ (?P<offset>\d+)\), shift (?P<shift>[+-][\d.]+)\): "
    r"(?P<paid_persons>\d+)/(?P<persons>\d+) persons \((?P<paid_person_percent>[\d.]+) %\) have a work/education activity in a "
    r"street_paid or resident_zone zone, (?P<free_persons>\d+) of them \((?P<free_percent>[\d.]+) %\) park free; "
    r"(?P<campus_persons>\d+)/\d+ persons \([\d.]+ %\) have one in a campus zone, (?P<campus_free_persons>\d+) of them "
    r"\((?P<campus_free_percent>[\d.]+) %\) park free")


class ArmInputs(NamedTuple):
    """One arm as given on the command line: its label, the directory of its downloaded subset and whether it has a report."""
    label: str
    directory: Path
    with_outcomes: bool


class InputError(ValueError):
    """An input is missing or does not have the expected content: the message names the file and the fix."""


# ------------------------------------------------------------------------------------------------------------ mode shares


def read_modestats(path) -> pd.DataFrame:
    """The ``modestats.csv`` of a run: one row per iteration (column ``iteration``), one share column per mode. Raises
    ``InputError`` for a missing file, a missing ``iteration`` column, a duplicate iteration, a share that is no number in
    [0, 1] and an iteration whose shares do not sum to 1."""
    path = Path(path)
    if not path.is_file():
        raise InputError(f"modestats file missing: {path}")
    frame = pd.read_csv(path, sep=";")
    if "iteration" not in frame.columns or len(frame.columns) < 2:
        raise InputError(f"{path}: expected a ';'-separated table with the column 'iteration' and one column per mode, "
                         f"found {list(frame.columns)}")
    if frame["iteration"].duplicated().any():
        raise InputError(f"{path}: an iteration occurs twice")
    modes = [column for column in frame.columns if column != "iteration"]
    shares = frame[modes].apply(pd.to_numeric, errors="coerce")
    if shares.isna().any().any() or (shares < 0).any().any() or (shares > 1).any().any():
        raise InputError(f"{path}: every mode share must be a number in [0, 1]")
    if ((shares.sum(axis=1) - 1.0).abs() > MODESTATS_SUM_TOLERANCE).any():
        raise InputError(f"{path}: the mode shares of an iteration do not sum to 1 (rows {list(frame['iteration'])}); the file "
                         "is not a modestats table of MATSim")
    return pd.concat([frame[["iteration"]].astype("int64"), shares], axis=1)


def pooled_mode_shares(frames: "dict[str, pd.DataFrame]", iterations, *, baseline_arm: str, off_arm: str) -> pd.DataFrame:
    """Per arm and mode: the mean and the sample SD (n - 1) of the share over ``iterations`` and the differences of the mean to
    ``baseline_arm`` and ``off_arm`` in percentage points (the arm itself: 0 against itself, empty when the arm is absent).

    The SD is the iteration-to-iteration spread within ONE run, not the noise between independent runs. Raises ``InputError``
    when an arm has no row for a selected iteration or the arms do not all have the same modes, and ``ValueError`` for fewer
    than two iterations (no SD).
    """
    iterations = sorted(set(iterations))
    if len(iterations) < 2:
        raise ValueError("the iteration-to-iteration SD needs at least two iterations")
    modes = None
    stats = {}
    for label, frame in frames.items():
        selected = frame[frame["iteration"].isin(iterations)]
        missing = sorted(set(iterations) - set(selected["iteration"]))
        if missing:
            raise InputError(f"arm {label}: modestats has no row for iteration(s) {missing}")
        arm_modes = [column for column in frame.columns if column != "iteration"]
        if modes is None:
            modes = arm_modes
        elif sorted(modes) != sorted(arm_modes):
            raise InputError(f"arm {label}: modes {sorted(arm_modes)} differ from those of the first arm {sorted(modes)}")
        stats[label] = pd.DataFrame({"mean": selected[arm_modes].mean(), "sd": selected[arm_modes].std(ddof=1)})
    rows = []
    for label, arm_stats in stats.items():
        for mode in modes:
            row = {"arm": label, "mode": mode, "iterations": f"{iterations[0]}-{iterations[-1]}",
                   "n_iterations": len(iterations), "mean_share": arm_stats.loc[mode, "mean"],
                   "sd_share_within_run": arm_stats.loc[mode, "sd"]}
            for column, other in (("diff_to_baseline_pp", baseline_arm), ("diff_to_off_pp", off_arm)):
                row[column] = (100.0 * (arm_stats.loc[mode, "mean"] - stats[other].loc[mode, "mean"])
                               if other in stats else np.nan)
            rows.append(row)
    table = pd.DataFrame(rows)
    table.insert(2, "baseline_arm", baseline_arm)
    table.insert(3, "off_arm", off_arm)
    return table


# ------------------------------------------------------------------------------------------------------------- zone groups


def zone_group(zone_id: str) -> str:
    """The group of the first pattern of ``ZONE_GROUPS`` that matches ``zone_id`` (``fnmatch``, case-sensitive), else ``other``."""
    for pattern, group in ZONE_GROUPS:
        if fnmatch.fnmatchcase(zone_id, pattern):
            return group
    return OTHER_GROUP


def group_order() -> list[str]:
    """The groups in the order of the tables: ``ZONE_GROUPS`` as listed, then ``other``."""
    ordered = []
    for _, group in ZONE_GROUPS:
        if group not in ordered:
            ordered.append(group)
    return [*ordered, OTHER_GROUP]


def _purpose_mask(frame: pd.DataFrame, purpose_class: str) -> np.ndarray:
    commuter = frame["purpose"].isin(compare.COMMUTER_PURPOSES).to_numpy()
    if purpose_class == PURPOSE_CLASS_ALL:
        return np.ones(len(frame), dtype=bool)
    if purpose_class == PURPOSE_CLASS_WORK_EDUCATION:
        return commuter
    return ~frame["purpose"].isin({compare.HOME_PURPOSE, *compare.COMMUTER_PURPOSES}).to_numpy()


def _ratio(numerator, denominator) -> float:
    return float(numerator) / float(denominator) if denominator else np.nan


def zone_group_table(arm: str, frame: pd.DataFrame, iterations) -> pd.DataFrame:
    """The zone-group rows of one zone arm from its pooled v4 outcome report ``frame`` (the calls without a zone id, NO_ZONE,
    are not part of any group). A group or purpose class without a call has ``calls`` 0 and empty ratios."""
    zoned = frame[frame["outcome"] != cost.NO_ZONE].copy()
    zoned["zone_group"] = zoned["zone_id"].map(zone_group)
    iterations = sorted(set(iterations))
    rows = []
    for group in group_order():
        in_group = zoned[zoned["zone_group"] == group]
        zone_ids = ", ".join(sorted(in_group["zone_id"].unique()))
        for purpose_class in PURPOSE_CLASSES:
            part = in_group[_purpose_mask(in_group, purpose_class)]
            calls = int(part["count"].sum())
            paid = int(part.loc[part["outcome"].isin(compare.PAID_OUTCOMES), "count"].sum())
            employer_free = int(part.loc[part["outcome"] == cost.EMPLOYER_FREE, "count"].sum())
            rows.append({
                "arm": arm, "zone_group": group, "purpose_class": purpose_class,
                "iterations": f"{iterations[0]}-{iterations[-1]}", "zone_ids": zone_ids,
                "priced_calls": calls, "paid_calls": paid, "paid_share": _ratio(paid, calls),
                "garage_share_paid_options": _ratio(part["garage_probability_sum_paid"].sum(), calls),
                "garage_share_free_options": _ratio(part["garage_probability_sum_free"].sum(), calls),
                "mean_expected_cost_cents": _ratio(part["expected_cost_cents_sum"].sum(), calls),
                "ev_exempt_count": int(part["ev_exempt_count"].sum()),
                "street_infeasible_count": int(part["street_infeasible_count"].sum()),
                "no_feasible_option_count": int(part["no_feasible_option_count"].sum()),
                "employer_free_count": employer_free})
    return pd.DataFrame(rows)


def zone_group_deltas(table: pd.DataFrame, baseline_arm: str) -> pd.DataFrame:
    """Every zone arm of ``table`` against ``baseline_arm`` on the same (zone group, purpose class) rows: the baseline's call
    count, the difference of the ratios (shares in percentage points, the mean cost in euro cents) and of the counts. A
    difference of two stochastic runs, descriptive only. An empty frame without the baseline arm."""
    base = table[table["arm"] == baseline_arm].set_index(["zone_group", "purpose_class"])
    if base.empty:
        return pd.DataFrame()
    shares = ("paid_share", "garage_share_paid_options", "garage_share_free_options")
    counts = ("ev_exempt_count", "street_infeasible_count", "no_feasible_option_count", "employer_free_count")
    rows = []
    for arm, arm_rows in table[table["arm"] != baseline_arm].groupby("arm", sort=False):
        for _, row in arm_rows.iterrows():
            reference = base.loc[(row["zone_group"], row["purpose_class"])]
            out = {"arm": arm, "baseline_arm": baseline_arm, "zone_group": row["zone_group"],
                   "purpose_class": row["purpose_class"], "priced_calls": row["priced_calls"],
                   "baseline_priced_calls": reference["priced_calls"]}
            for column in shares:
                out[f"{column}_diff_pp"] = 100.0 * (row[column] - reference[column])
            out["mean_expected_cost_cents_diff"] = row["mean_expected_cost_cents"] - reference["mean_expected_cost_cents"]
            for column in counts:
                out[f"{column}_diff"] = int(row[column]) - int(reference[column])
            rows.append(out)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------------- free share


def parse_free_draw(lines, source: str) -> dict:
    """The person-level free-parking draw of a pipeline log: the fields of THE summary line ``attach`` writes once per population
    build. Raises ``InputError`` for no such line (a cached population stage leaves none: pass the log of the arm that built the
    population) and for more than one (two builds in one log would be ambiguous)."""
    matches = [match for match in (FREE_DRAW_LINE.search(line) for line in lines) if match]
    if not matches:
        raise InputError(f"{source}: no '[parking] free-parking draw (RandomState(...), shift ...)' summary line; the population "
                         "stage was cached or the log is not the pipeline log of a zone arm")
    if len(matches) > 1:
        raise InputError(f"{source}: {len(matches)} free-parking draw summary lines; the log holds more than one population build")
    return matches[0].groupdict()


def read_configured_shift(path) -> float:
    """``parking_workplace_free_share_shift`` of a resolved config (``config:`` section); ``InputError`` when absent or not a number."""
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    section = data.get("config") if isinstance(data, dict) else None
    if not isinstance(section, dict) or CONFIG_SHIFT_KEY not in section:
        raise InputError(f"{path}: no 'config: {CONFIG_SHIFT_KEY}' entry in the resolved config")
    value = section[CONFIG_SHIFT_KEY]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{path}: {CONFIG_SHIFT_KEY} must be a number, found {value!r}")
    return float(value)


def free_share_row(arm: str, frame: pd.DataFrame, zone_types: "dict[str, str]", draw: dict, configured_shift: float,
                   iterations) -> dict:
    """The free-share row of one zone arm: the configured shift, the logged person-level draw and the pooled call-level
    EMPLOYER_FREE rate of the work and education calls in the zones of the free-draw zone types and in all zones.

    ``zone_types`` maps zone id -> zone type; a zone of the report without a tariff row raises ``InputError``."""
    zoned = frame[(frame["outcome"] != cost.NO_ZONE) & _purpose_mask(frame, PURPOSE_CLASS_WORK_EDUCATION)]
    unknown = sorted(set(zoned["zone_id"]) - set(zone_types))
    if unknown:
        raise InputError(f"arm {arm}: report zones without a tariff row: {unknown}")
    in_draw_zones = zoned["zone_id"].map(zone_types).isin(attach.FREE_DRAW_ZONE_TYPES).to_numpy()
    draw_calls = zoned[in_draw_zones]
    employer_free_all = int(zoned.loc[zoned["outcome"] == cost.EMPLOYER_FREE, "count"].sum())
    employer_free_draw = int(draw_calls.loc[draw_calls["outcome"] == cost.EMPLOYER_FREE, "count"].sum())
    n_paid_persons, n_free_persons = int(draw["paid_persons"]), int(draw["free_persons"])
    iterations = sorted(set(iterations))
    return {
        "arm": arm, "configured_shift": configured_shift, "logged_shift": float(draw["shift"]),
        "random_seed_and_offset": f"{draw['seed']} + {draw['offset']}", "persons": int(draw["persons"]),
        "persons_in_paid_zones": n_paid_persons, "persons_drawn_free": n_free_persons,
        "person_free_share": _ratio(n_free_persons, n_paid_persons), "person_free_percent_logged": float(draw["free_percent"]),
        "campus_persons": int(draw["campus_persons"]), "campus_persons_drawn_free": int(draw["campus_free_persons"]),
        "iterations": f"{iterations[0]}-{iterations[-1]}",
        "work_education_calls_in_draw_zones": int(draw_calls["count"].sum()),
        "employer_free_calls_in_draw_zones": employer_free_draw,
        "call_employer_free_share_draw_zones": _ratio(employer_free_draw, draw_calls["count"].sum()),
        "work_education_calls_in_all_zones": int(zoned["count"].sum()),
        "employer_free_calls_in_all_zones": employer_free_all,
        "call_employer_free_share_all_zones": _ratio(employer_free_all, zoned["count"].sum()),
    }


# --------------------------------------------------------------------------------------------------------------- the arm set


def _exactly_one(directory: Path, pattern: str, what: str) -> Path:
    found = sorted(directory.glob(pattern))
    if len(found) != 1:
        raise InputError(f"{directory}: expected exactly one {what} ({pattern}), found {[path.name for path in found]}")
    return found[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _input_record(path: Path, paths) -> dict:
    """Path (as ``paths`` records it) and the SHA-256 of the file's bytes (not line-ending normalised)."""
    return {"path": paths(path), "sha256": _sha256(path)}


def summarize(arms, *, iterations, tariffs_path, baseline_arm: str = "v3", off_arm: str = "off", path_roots=()) -> dict:
    """Build the four tables and the provenance of an arm set: ``{"modestats_pooled", "zone_groups", "zone_group_deltas",
    "free_share", "provenance"}``. Raises ``InputError`` (and ``FileNotFoundError``/``ValueError`` of the strict report reader)
    for any missing or malformed input; nothing is estimated or defaulted.

    The provenance records every input path relative to the repository root of this script or, for an input of another checkout
    (the run outputs of the main checkout while the script runs in a worktree), to the first of ``path_roots`` that contains it
    (``compare.PathRecorder``), and the ``code_state`` of the working tree (the commit, marked when it has uncommitted changes)."""
    arms = list(arms)
    labels = [arm.label for arm in arms]
    if not arms:
        raise ValueError("summarize needs at least one arm")
    for label in labels:
        if not ARM_LABEL_PATTERN.fullmatch(label):
            raise ValueError(f"arm label {label!r} must consist of letters, digits, underscore, dot and hyphen")
    if len(set(labels)) != len(labels):
        raise ValueError(f"duplicate arm label in {labels}")
    iterations = sorted(set(iterations))
    tariffs = parking_zones.load_tariffs(Path(tariffs_path))
    zone_types = dict(zip(tariffs["zone_id"], tariffs["zone_type"]))
    paths = compare.PathRecorder(path_roots)
    provenance = {"script": "scripts/parking/summarize_arm_set.py", "code_state": compare.calibrate.git_state(),
                  "path_note": "paths are relative to the repository root of the checkout that holds the file",
                  "iterations": iterations, "tariffs": _input_record(Path(tariffs_path), paths), "arms": {}}
    modestats, zone_rows, free_rows = {}, [], []
    for arm in arms:
        directory = Path(arm.directory)
        run_output = directory / MATSIM_OUTPUT_DIRECTORY
        if not run_output.is_dir():
            raise InputError(f"arm {arm.label}: {run_output} not found (the downloaded comparison subset of the run)")
        record = {"directory": paths(directory)}
        modestats_path = run_output / MODESTATS_FILE_NAME
        modestats[arm.label] = read_modestats(modestats_path)
        record["modestats"] = _input_record(modestats_path, paths)
        if arm.with_outcomes:
            reports = [compare.outcome_report_path(run_output, number) for number in iterations]
            frames = []
            for path in reports:
                frames.append(compare.read_parking_outcomes(path))
            frame, version = compare.pool_outcome_reports(frames)
            if version != compare.LATEST_REPORT_VERSION:
                raise InputError(f"arm {arm.label}: the outcome reports are version {version}; this summary needs v4")
            record["outcome_reports"] = [_input_record(path, paths) for path in reports]
            zone_rows.append(zone_group_table(arm.label, frame, iterations))
            log_path = _exactly_one(directory, "*.log", "pipeline log")
            config_path = _exactly_one(directory, "*_resolved.yml", "resolved config")
            draw = parse_free_draw(log_path.read_text(encoding="utf-8", errors="replace").splitlines(), str(log_path))
            free_rows.append(free_share_row(arm.label, frame, zone_types, draw, read_configured_shift(config_path), iterations))
            record["pipeline_log"] = _input_record(log_path, paths)
            record["resolved_config"] = _input_record(config_path, paths)
        provenance["arms"][arm.label] = record
    paths.warn_if_outside("arm-set-summary")
    zone_groups = pd.concat(zone_rows, ignore_index=True) if zone_rows else pd.DataFrame()
    return {"modestats_pooled": pooled_mode_shares(modestats, iterations, baseline_arm=baseline_arm, off_arm=off_arm),
            "zone_groups": zone_groups,
            "zone_group_deltas": zone_group_deltas(zone_groups, baseline_arm) if not zone_groups.empty else pd.DataFrame(),
            "free_share": pd.DataFrame(free_rows), "provenance": provenance}


def _text(frame: pd.DataFrame) -> pd.DataFrame:
    """Fixed decimals for the written tables (determinism): shares and means with six, differences with four decimals."""
    out = frame.copy()
    for column in out.columns:
        if out[column].dtype.kind != "f":
            continue
        if column.endswith("_pp"):
            decimals = PERCENTAGE_POINT_DECIMALS
        elif "cost" in column:
            decimals = COST_DECIMALS
        else:
            decimals = SHARE_DECIMALS
        out[column] = out[column].map(lambda value, decimals=decimals: "" if pd.isna(value) else f"{value:.{decimals}f}")
    return out


def write_tables(result: dict, out_dir, prefix: str, *, overwrite: bool = False) -> list[Path]:
    """Write the non-empty tables and the provenance as ``<prefix>_<name>.csv`` / ``<prefix>_provenance.json`` below ``out_dir``.
    Refuses to replace an existing file without ``overwrite`` (nothing is written then)."""
    out_dir = Path(out_dir)
    targets = {name: out_dir / f"{prefix}_{name}.csv" for name in ("modestats_pooled", "zone_groups", "zone_group_deltas",
                                                                   "free_share") if not result[name].empty}
    targets["provenance"] = out_dir / f"{prefix}_provenance.json"
    existing = [path for path in targets.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(f"{existing[0]} exists; pass --overwrite to replace the output files")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, path in targets.items():
        if name == "provenance":
            path.write_text(json.dumps(result["provenance"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            _text(result[name]).to_csv(path, index=False, lineterminator="\n")
    return list(targets.values())


class _ArmAction(argparse.Action):
    """Collect ``--arm`` and ``--arm-without-outcomes`` into ONE list in command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):
        arms = list(getattr(namespace, self.dest) or [])
        label, separator, directory = values.partition("=")
        if not separator or not label or not directory:
            parser.error(f"{option_string} expects LABEL=ARM_DIRECTORY, got {values!r}")
        arms.append(ArmInputs(label, Path(directory), option_string == "--arm"))
        setattr(namespace, self.dest, arms)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", dest="arms", action=_ArmAction, metavar="LABEL=ARM_DIRECTORY",
                        help="a zone arm: the downloaded subset of its run (matsim_output/, pipeline log, resolved config)")
    parser.add_argument("--arm-without-outcomes", dest="arms", action=_ArmAction, metavar="LABEL=ARM_DIRECTORY",
                        help="an arm that prices no zones (OFF, LEGACY): mode shares only")
    parser.add_argument("--data-path", type=Path, required=True, help="data directory with the committed tariff table")
    parser.add_argument("--iteration", type=compare.parse_iterations, required=True, metavar="FIRST-LAST",
                        help="inclusive range of iterations that is pooled (at least two)")
    parser.add_argument("--baseline-arm", default="v3", help="arm of the differences of the mode share and zone group tables")
    parser.add_argument("--off-arm", default="off", help="arm of the mode share differences to the unpriced reference")
    parser.add_argument("--path-root", dest="path_roots", type=Path, action="append", default=[], metavar="REPOSITORY_ROOT",
                        help="repository root of another checkout that holds inputs (repeatable); provenance paths are "
                             "recorded relative to the first root that contains them")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True, help="file name prefix of every output (descriptive: scenario, set, date)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files")
    args = parser.parse_args(argv)
    if not args.arms:
        parser.error("at least one --arm or --arm-without-outcomes is required")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    tariffs_path = args.data_path / compare.TARIFFS_RELATIVE
    if not tariffs_path.is_file():
        raise SystemExit(f"tariff table not found: {tariffs_path}")
    try:
        result = summarize(args.arms, iterations=args.iteration, tariffs_path=tariffs_path, baseline_arm=args.baseline_arm,
                           off_arm=args.off_arm, path_roots=args.path_roots)
        paths = write_tables(result, args.out_dir, args.prefix, overwrite=args.overwrite)
    except (InputError, FileNotFoundError, FileExistsError, ValueError) as error:
        raise SystemExit(str(error)) from error
    for path in paths:
        log.info("[arm-set-summary] wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
