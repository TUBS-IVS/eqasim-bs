"""Count which path PopulationSim's simultaneous integerizer took, per batch and level.

Parses ``output/populationsim.log`` of PopulationSim 0.10.0
(``populationsim/integerizing/wrappers.py::do_simul_integerizing``).  A parent zone is
labelled ``<PARENT_GEOGRAPHY>_<zone id>``; the integerized level is the geography that
follows the parent in ``configs/settings.yaml`` ``geographies``.

Each failed first attempt ends in exactly one outcome: a successful retry, a failed retry
followed by the sequential fallback, "no infeasible sub zones" (sequential fallback, or,
without a fallback line, a single feasible sub zone that is sequentially integerized and
rounded), or "all sub zones infeasible" (smart rounding).  This module only reads.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Sequence

import pandas as pd
import yaml

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import list_batch_folders

logger = logging.getLogger(__name__)

_LOG = Path("output") / "populationsim.log"
_SETTINGS = Path("configs") / "settings.yaml"
_PREFIX = "do_simul_integerizing"
_KNOWN_STATUSES = {"OPTIMAL": "optimal", "FEASIBLE": "feasible",
                   "INFEASIBLE": "infeasible", "NOT_SOLVED": "not_solved"}
_STATUS = r"status (\S+?)\.?\s*$"
_FIRST_OK = re.compile(rf"{_PREFIX} succeeded for (\S+) {_STATUS}")
_FIRST_FAIL = re.compile(rf"{_PREFIX} failed for (\S+) {_STATUS}")
_RETRY_OK = re.compile(rf"{_PREFIX} retry succeeded for (\S+) {_STATUS}")
_RETRY_FAIL = re.compile(rf"{_PREFIX} retry failed for (\S+) {_STATUS}")
_FALLBACK = re.compile(rf"{_PREFIX} falling back to sequential integerizing for (\S+?)\.?\s*$")
_NO_INFEASIBLE = re.compile(rf"{_PREFIX} failed but found no infeasible sub zones (\S+?)\.?\s*$")
_SMART_ROUNDED = re.compile(rf"{_PREFIX} returning smart rounded weights for (\S+?)\.?\s*$")
_INFEASIBLE_COUNT = re.compile(rf"{_PREFIX} (\d+) infeasable subzones for (\S+?)\.?\s*$")

_COUNTERS = ["n_parents", "first_optimal", "first_feasible", "first_infeasible",
             "first_not_solved", "retry_optimal", "retry_feasible", "retry_infeasible",
             "retry_not_solved", "sequential_fallback", "no_infeasible_subzones",
             "single_feasible_subzone", "all_subzones_infeasible", "infeasible_subzones",
             "non_simultaneous"]
COLUMNS = ["batch", "level"] + _COUNTERS


def _geographies(batch_dir: Path) -> list:
    settings_file = batch_dir / _SETTINGS
    if not settings_file.is_file():
        raise FileNotFoundError(f"batch {batch_dir.name}: {settings_file} is missing")
    settings = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    if "geographies" not in settings:
        raise ValueError(f"batch {batch_dir.name}: {_SETTINGS.as_posix()} lacks 'geographies'")
    return list(settings["geographies"])


def _level_of(label: str, geographies: Sequence[str], where: str) -> str:
    """Level integerized under ``label``: the geography after the longest matching prefix."""
    parents = [g for g in geographies if label.startswith(g + "_")]
    if not parents:
        raise ValueError(f"{where}: label {label!r} starts with no geography of {list(geographies)}")
    parent = max(parents, key=len)
    position = list(geographies).index(parent)
    if position + 1 >= len(geographies):
        raise ValueError(f"{where}: label {label!r} belongs to the last geography {parent!r}, "
                         "which has no level below it")
    return geographies[position + 1]


def _status(word: str, where: str) -> str:
    if word not in _KNOWN_STATUSES:
        logger.warning("[popsim_batch_diagnostics] unknown solver status %r: %s", word, where)
        raise ValueError(f"{where}: unknown solver status {word!r}; "
                         f"known: {sorted(_KNOWN_STATUSES)}")
    return _KNOWN_STATUSES[word]


def _batch_counts(batch_dir: Path) -> dict:
    """Return ``{level: {counter: int}}`` for one batch."""
    log_file = batch_dir / _LOG
    if not log_file.is_file():
        raise FileNotFoundError(f"batch {batch_dir.name}: solver log missing: {log_file}")
    geographies = _geographies(batch_dir)
    counts: dict = {}
    no_infeasible_labels: list = []   # one (level, label) per occurrence
    fallback_labels: set = set()
    failed_first = {}      # level -> failed first attempts
    unknown_lines = 0
    for number, line in enumerate(log_file.read_text(encoding="utf-8").splitlines(), 1):
        if _PREFIX not in line:
            continue
        text = line[line.index(_PREFIX):]
        where = f"batch {batch_dir.name} {_LOG.as_posix()} line {number}"
        for pattern, kind in ((_FIRST_OK, "first"), (_FIRST_FAIL, "first_fail"),
                              (_RETRY_OK, "retry"), (_RETRY_FAIL, "retry_fail"),
                              (_FALLBACK, "fallback"), (_NO_INFEASIBLE, "no_infeasible"),
                              (_SMART_ROUNDED, "smart"), (_INFEASIBLE_COUNT, "count")):
            match = pattern.search(text)
            if match:
                break
        else:
            unknown_lines += 1
            logger.warning("[popsim_batch_diagnostics] unrecognised solver line, not counted "
                           "(%s): %s", where, line.strip())
            continue
        label = match.group(2) if kind == "count" else match.group(1)
        level = _level_of(label, geographies, where)
        row = counts.setdefault(level, dict.fromkeys(_COUNTERS, 0))
        if kind in ("first", "first_fail"):
            row["n_parents"] += 1
            row["first_" + _status(match.group(2), where)] += 1
            if kind == "first_fail":
                failed_first[level] = failed_first.get(level, 0) + 1
        elif kind in ("retry", "retry_fail"):
            row["retry_" + _status(match.group(2), where)] += 1
        elif kind == "fallback":
            row["sequential_fallback"] += 1
            fallback_labels.add(label)
        elif kind == "no_infeasible":
            row["no_infeasible_subzones"] += 1
            no_infeasible_labels.append((level, label))
        elif kind == "smart":
            row["all_subzones_infeasible"] += 1
        else:
            row["infeasible_subzones"] += int(match.group(1))
    seen_labels: set = set()
    for level, label in no_infeasible_labels:
        if (level, label) in seen_labels:
            logger.warning("[popsim_batch_diagnostics] %s: label %s has more than one 'found no "
                           "infeasible sub zones' line (duplicate parent in one log)",
                           batch_dir.name, label)
        seen_labels.add((level, label))
        if label not in fallback_labels:
            counts[level]["single_feasible_subzone"] += 1
    for level, row in counts.items():
        if row["n_parents"] == 0 and any(row[c] for c in _COUNTERS):
            logger.warning("[popsim_batch_diagnostics] %s level %s: outcome lines but no first "
                           "attempt (truncated or appended log?)", batch_dir.name, level)
        row["non_simultaneous"] = (row["sequential_fallback"] + row["single_feasible_subzone"]
                                   + row["all_subzones_infeasible"])
        ended = (sum(row[f"retry_{s}"] for s in _KNOWN_STATUSES.values())
                 + row["no_infeasible_subzones"] + row["all_subzones_infeasible"])
        failed = failed_first.get(level, 0)
        if failed != ended:
            logger.warning("[popsim_batch_diagnostics] %s level %s: %d failed first attempts "
                           "but %d outcomes (retries, no-infeasible, smart-rounded)",
                           batch_dir.name, level, failed, ended)
    if unknown_lines:
        logger.warning("[popsim_batch_diagnostics] %s: %d unrecognised solver line(s)",
                       batch_dir.name, unknown_lines)
    return counts


def solver_path_table(work_dir, names: Sequence[str] | None = None) -> pd.DataFrame:
    """One row per batch and integerized level with the counts of solver paths taken.

    ``n_parents`` counts first attempts; ``non_simultaneous`` counts parents whose final
    weights did not come from a successful simultaneous solve (sequential fallback, single
    feasible sub zone, all sub zones infeasible).  A batch without a log raises
    ``FileNotFoundError``; an unknown status, a label matching no geography, or a batch whose
    log yields no first attempt at any level raises ``ValueError`` (naming the batches).
    """
    rows, folders, unparsed = [], list_batch_folders(work_dir, names), []
    for batch_dir in folders:
        counts = _batch_counts(batch_dir)
        if not any(row["n_parents"] for row in counts.values()):
            unparsed.append(batch_dir.name)
        for level, row in counts.items():
            rows.append({"batch": batch_dir.name, "level": level, **row})
            logger.info(
                "[popsim_batch_diagnostics] solver paths %s %s: non-simultaneous %d/%d (%.1f%%), "
                "sequential fallback %d, smart-rounded subzones %d", batch_dir.name, level,
                row["non_simultaneous"], row["n_parents"],
                100.0 * row["non_simultaneous"] / row["n_parents"] if row["n_parents"] else 0.0,
                row["sequential_fallback"], row["infeasible_subzones"])
    logger.info("[popsim_batch_diagnostics] solver paths: parsed %d of %d batches (a first "
                "attempt found)", len(folders) - len(unparsed), len(folders))
    if unparsed:
        raise ValueError(
            f"{len(unparsed)} of {len(folders)} batch(es) have no first attempt in "
            f"{_LOG.as_posix()} (no '{_PREFIX} succeeded/failed for' line at any level): "
            f"{unparsed}; an unparsed log would read as '0 fallbacks'.  Check the log "
            "(truncated, empty, other PopulationSim version?)")
    return pd.DataFrame(rows, columns=COLUMNS)
