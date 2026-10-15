"""Stuck agents of the final iteration by category (eqasim-bs#443, ADR-0146).

eqasim's ``stuck_analysis.csv`` counts stuck events per iteration without categories. This module streams
``output_events.xml.gz`` once for the ``stuckAndAbort`` events (a line filter, then the attributes), the
selected plans of ``output_plans.xml.gz`` for the ``frozen`` flag (the plan touches an ``outside`` activity:
a tour the discrete mode choice never re-chooses) and ``output_persons.csv.gz`` for the subpopulation, and
reports the stuck agents by subpopulation x at the simulation end / during the day x mode x frozen. The
dashboard block (``run_metrics.metrics_matsim``) and the analysis-suite outputs come from the same function.

The simulation end that separates "stuck at the end" from "stuck during the day" is read from the run's own
``output_config.xml`` (fallback ``output_config_reduced.xml``); the warn share is a parameter
(``analysis_stuck_warn_share`` of the analysis suite, ``STUCK_WARN_SHARE`` is its default).

Freight agents are a category of their own here (they are not dropped as in ``freight_filter``). Person ids
are strings throughout because freight ids are not numeric.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from braunschweig.matsim.simulation.plan_check import read_qsim_end_time_s
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

logger = logging.getLogger(__name__)

_LOG_TAG = "[stuck_agents]"
RESIDENTS = "residents"
UNKNOWN_SUBPOPULATION = "unknown"
OUTSIDE_ACTIVITY_TYPE = "outside"
STUCK_TYPE = 'type="stuckAndAbort"'
#: ASSUMPTION (ADR-0146): WARN when more than this share of the residents is stuck in the final iteration.
STUCK_WARN_SHARE = 0.01
#: Files of a MATSim output directory that carry the run's qsim.endTime, in the order they are tried.
OUTPUT_CONFIG_NAMES = ("output_config.xml", "output_config_reduced.xml")
INPUT_FILE_NAMES = ("output_events.xml.gz", "output_persons.csv.gz", "output_plans.xml.gz")
EVENT_COLUMNS = ["person", "time", "leg_mode", "link"]
OUTPUT_COLUMNS = EVENT_COLUMNS + ["subpopulation", "frozen", "at_end"]
_ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')


def _open_text(path):
    """Open a plain or gzip-compressed file as UTF-8 text."""
    path = str(path)
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def _open_binary(path):
    """Open a plain or gzip-compressed file as bytes."""
    path = str(path)
    return gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")


def scan_stuck_events(events_path) -> pd.DataFrame:
    """Stream an events file and return its ``stuckAndAbort`` events as ``person, time, leg_mode, link``.

    ``time`` is in seconds. ``leg_mode`` is ``none`` where MATSim wrote no ``legMode`` (agents still stuck at
    the simulation end). Only lines containing the event type are parsed, so a multi-GB file stays cheap.
    """
    rows = []
    with _open_text(events_path) as handle:
        for line in handle:
            if STUCK_TYPE not in line:
                continue
            attributes = dict(_ATTRIBUTE.findall(line))
            rows.append((attributes.get("person"), float(attributes.get("time", "nan")),
                         attributes.get("legMode") or "none", attributes.get("link")))
    frame = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    logger.info("%s %d stuckAndAbort events in %s", _LOG_TAG, len(frame), events_path)
    return frame


def frozen_persons(plans_path) -> set:
    """Persons whose SELECTED plan contains an ``outside`` activity (streamed, one person in memory at a time)."""
    frozen = set()
    with _open_binary(plans_path) as handle:
        for _, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "person":
                continue
            for plan in element.findall("plan"):
                if plan.get("selected") == "yes" and any(
                        activity.get("type") == OUTSIDE_ACTIVITY_TYPE for activity in plan.findall("activity")):
                    frozen.add(element.get("id"))
            element.clear()
    logger.info("%s %d persons with an outside activity in the selected plan (%s)", _LOG_TAG, len(frozen), plans_path)
    return frozen


def person_subpopulations(persons_csv_path) -> pd.Series:
    """Subpopulation per person id (str); the empty label of ``output_persons.csv.gz`` is named ``residents``."""
    try:
        persons = pd.read_csv(persons_csv_path, sep=";", usecols=["person", "subpopulation"], dtype=str,
                              keep_default_na=False)
    except ValueError as exc:
        raise ValueError(f"{persons_csv_path} must carry the columns person and subpopulation "
                         f"(a run without subpopulations cannot be categorised): {exc}") from exc
    labels = persons["subpopulation"].where(persons["subpopulation"].str.strip() != "", RESIDENTS)
    return pd.Series(labels.to_numpy(), index=pd.Index(persons["person"].astype(str), name="person"))


def _categorised(events, subpopulations, frozen, simulation_end_s) -> pd.DataFrame:
    """Add ``subpopulation``, ``frozen`` and ``at_end`` (time >= simulation end) to the stuck events."""
    table = events.copy()
    table["subpopulation"] = table["person"].map(subpopulations).fillna(UNKNOWN_SUBPOPULATION)
    table["frozen"] = table["person"].isin(frozen)
    table["at_end"] = table["time"] >= simulation_end_s
    return table


def summarise_stuck(events: pd.DataFrame, subpopulations: pd.Series, frozen: set, *, simulation_end_s: float,
                    warn_share: float = STUCK_WARN_SHARE) -> dict:
    """Summarise the stuck events by subpopulation x time class x mode x frozen (layout: see ADR-0146).

    ``warning`` is set (and logged at WARNING) when the share of stuck residents exceeds ``warn_share``.
    """
    table = _categorised(events, subpopulations, frozen, simulation_end_s)
    n_persons_by_subpopulation = subpopulations.value_counts().to_dict()
    for name in table["subpopulation"].unique():
        n_persons_by_subpopulation.setdefault(name, 0)
    by_subpopulation = {}
    for name, n_persons in n_persons_by_subpopulation.items():
        group = table[table["subpopulation"] == name]
        n_stuck = int(group["person"].nunique())
        by_subpopulation[name] = {
            "n_persons": int(n_persons),
            "n_stuck": n_stuck,
            "share_stuck": n_stuck / int(n_persons) if n_persons else 0.0,
            "at_end": {"frozen": int((group["at_end"] & group["frozen"]).sum()),
                       "in_region": int((group["at_end"] & ~group["frozen"]).sum())},
            "during_day": {"frozen": int((~group["at_end"] & group["frozen"]).sum()),
                           "in_region": int((~group["at_end"] & ~group["frozen"]).sum())},
            "by_mode": {str(mode): int(count) for mode, count in sorted(group["leg_mode"].value_counts().items())},
        }
    residents = by_subpopulation.get(RESIDENTS)
    warning = residents is not None and residents["share_stuck"] > warn_share
    summary = {"available": True, "simulation_end_s": float(simulation_end_s), "n_stuck": int(len(table)),
               "by_subpopulation": by_subpopulation, "warn_share": float(warn_share), "warning": bool(warning)}
    if residents is None:
        logger.warning("%s no residents among the %d persons; the stuck share cannot be judged", _LOG_TAG,
                       len(subpopulations))
        return summary
    log = logger.warning if warning else logger.info
    log("%s stuck agents %d; residents %d/%d (%.2f%%; warn above %.2f%%): at end frozen %d / in-region %d, during "
        "the day frozen %d / in-region %d", _LOG_TAG, len(table), residents["n_stuck"], residents["n_persons"],
        100.0 * residents["share_stuck"], 100.0 * warn_share,
        residents["at_end"]["frozen"], residents["at_end"]["in_region"],
        residents["during_day"]["frozen"], residents["during_day"]["in_region"])
    return summary


def simulation_end_seconds(sim_output) -> float:
    """Simulation end (``qsim.endTime``, seconds) of the run that wrote ``sim_output``.

    Read from ``output_config.xml``, else ``output_config_reduced.xml``. A config that is absent, unreadable or
    carries no usable ``endTime`` is skipped and named in the log; when none yields a value the plan-time bound
    ``MAX_PLAN_TIME_SECONDS`` is used (the simulated day equals it, ADR-0146) and a WARNING names the files.
    """
    sim_output = Path(sim_output)
    problems = []
    for name in OUTPUT_CONFIG_NAMES:
        path = sim_output / name
        if not path.is_file():
            problems.append(f"{name}: not found")
            continue
        try:
            end_s = read_qsim_end_time_s(path)
        except (ValueError, ET.ParseError) as exc:
            # A config that exists but cannot be used is a finding of its own, not just a missing file.
            logger.warning("%s %s has no usable qsim.endTime (%s); trying the next config", _LOG_TAG, path, exc)
            problems.append(f"{name}: {exc}")
            continue
        logger.info("%s simulation end %.2f h read from %s", _LOG_TAG, end_s / 3600.0, path)
        return end_s
    logger.warning("%s no qsim.endTime in the output config of %s (%s); using the plan-time bound %.2f h", _LOG_TAG,
                   sim_output, "; ".join(problems), MAX_PLAN_TIME_SECONDS / 3600.0)
    return float(MAX_PLAN_TIME_SECONDS)


def _read_inputs(sim_output):
    """Read the three simulation outputs; fail early with the missing path named."""
    sim_output = Path(sim_output)
    paths = {name: sim_output / name for name in INPUT_FILE_NAMES}
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(f"Stuck-agent analysis needs {path}; run the simulation to its final iteration first.")
    return (scan_stuck_events(paths["output_events.xml.gz"]),
            person_subpopulations(paths["output_persons.csv.gz"]),
            frozen_persons(paths["output_plans.xml.gz"]))


def stuck_summary(sim_output, *, simulation_end_s: float | None = None, warn_share: float = STUCK_WARN_SHARE) -> dict:
    """Stuck-agent summary of a simulation output directory.

    ``simulation_end_s`` (seconds) defaults to the ``qsim.endTime`` of the run's output config
    (:func:`simulation_end_seconds`).
    """
    events, subpopulations, frozen = _read_inputs(sim_output)
    if simulation_end_s is None:
        simulation_end_s = simulation_end_seconds(sim_output)
    return summarise_stuck(events, subpopulations, frozen, simulation_end_s=simulation_end_s, warn_share=warn_share)


def write_stuck_outputs(sim_output, out_dir, *, simulation_end_s: float | None = None,
                        warn_share: float = STUCK_WARN_SHARE) -> tuple:
    """Write ``stuck_agents.csv`` (one row per stuck event) and ``stuck_summary.json`` into ``out_dir``."""
    events, subpopulations, frozen = _read_inputs(sim_output)
    if simulation_end_s is None:
        simulation_end_s = simulation_end_seconds(sim_output)
    summary = summarise_stuck(events, subpopulations, frozen, simulation_end_s=simulation_end_s,
                              warn_share=warn_share)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table = _categorised(events, subpopulations, frozen, simulation_end_s)
    csv_path, json_path = out_dir / "stuck_agents.csv", out_dir / "stuck_summary.json"
    table[OUTPUT_COLUMNS].to_csv(csv_path, sep=";", index=False)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
    logger.info("%s wrote %s and %s", _LOG_TAG, csv_path, json_path)
    return csv_path, json_path


def reusable_stuck_summary(sim_output, summary_path) -> dict | None:
    """Return the summary an earlier run of the analysis suite wrote, or ``None`` when it must be recomputed.

    The file is reused only when it exists, parses, and is NEWER than ``output_events.xml.gz`` (a stale summary of
    an earlier simulation must never describe this one). Every reason for not reusing it is logged.
    """
    if summary_path is None:
        return None
    summary_path = Path(summary_path)
    events_path = Path(sim_output) / "output_events.xml.gz"
    if not summary_path.is_file():
        logger.info("%s no precomputed summary at %s", _LOG_TAG, summary_path)
        return None
    if not events_path.is_file():
        return None
    if summary_path.stat().st_mtime <= events_path.stat().st_mtime:
        logger.warning("%s %s is not newer than %s (stale); recomputing", _LOG_TAG, summary_path, events_path)
        return None
    try:
        with open(summary_path, encoding="utf-8") as handle:
            summary = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("%s %s is unreadable (%s); recomputing", _LOG_TAG, summary_path, exc)
        return None
    if not isinstance(summary, dict) or "by_subpopulation" not in summary:
        logger.warning("%s %s does not look like a stuck summary; recomputing", _LOG_TAG, summary_path)
        return None
    return summary
