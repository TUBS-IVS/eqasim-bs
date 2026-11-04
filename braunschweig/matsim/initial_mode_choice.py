"""Initial mode choice before iteration 0 with eqasim's standalone mode choice (eqasim-bs#542, ADR-0178).

At the end of the prepare stage every agent chooses its modes once with eqasim's ``RunStandaloneModeChoice`` under free
flow travel times, so MATSim starts from a plan set in which each agent has chosen. This module holds the pure helpers
of that step: the runner arguments, the retired-key check, the person count and the before/after trip summary, and
``run``, which calls the Java runner, checks that no person disappeared and replaces the population with the result.

The ADR-0155 mechanism (eqasim-bs#465) added a second DiscreteModeChoice replanning strategy with a very high weight to
the MATSim config so that agents chose in iteration 1; it is gone, replaced by this upfront choice (ADR-0178 has the
reasons).
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import time

import matsim.runtime.eqasim as eqasim
import pandas as pd

from braunschweig.matsim.simulation.plan_check import resolve_population_path

logger = logging.getLogger(__name__)

_LOG_TAG = "[initial mode choice]"

KEY_ENABLED = "braunschweig.initial_mode_choice.enabled"
DEFAULT_ENABLED = True
#: Key of the ADR-0155 strategy weight; the mechanism is gone, so a configured value is refused (no silent ignore).
KEY_WEIGHT_RETIRED = "braunschweig.initial_mode_choice.weight"

RUNNER = "org.eqasim.core.standalone_mode_choice.RunStandaloneModeChoice"
CONFIGURATOR = "org.eqasim.braunschweig.BraunschweigConfigurator"
OUTPUT_DIRECTORY = "mode_choice"
OUTPUT_PLANS = "output_plans.xml.gz"
INPUT_TRIPS = "input_trips.csv.gz"
OUTPUT_TRIPS = "output_trips.csv.gz"
CSV_DELIMITER = ";"
SUMMARY_FILE_NAME = "initial_mode_choice.json"

_PERSON_TOKEN = b"<person "


def refuse_retired_weight(value) -> None:
    """Raise ValueError if the retired ADR-0155 weight key is configured (``value`` is not None)."""
    if value is not None:
        raise ValueError(f"{_LOG_TAG} {KEY_WEIGHT_RETIRED} is retired (value {value!r}): the initial mode choice now "
                         "runs eqasim's standalone mode choice before iteration 0 (ADR-0178); remove the key")


def arguments(config_name: str, population_file: str, threads: int) -> list[str]:
    """Return the command line arguments of the standalone mode choice (free flow: no travel time option)."""
    return [
        "--config-path", config_name,
        "--config:plans.inputPlansFile", population_file,
        "--config:standaloneModeChoice.outputDirectory", OUTPUT_DIRECTORY,
        "--config:standaloneModeChoice.removePersonsWithNoValidAlternatives", "true",
        "--config:global.numberOfThreads", str(threads),
        "--write-input-csv-trips", "true",
        "--write-output-csv-trips", "true",
        "--compression", "gzip",
        "--skip-scenario-check", "true",
        "--eqasim-configurator", CONFIGURATOR,
    ]


def count_persons(path, chunk_size: int = 1 << 20) -> int:
    """Count the ``<person `` elements of a gzipped plans file, streaming in chunks of ``chunk_size`` bytes."""
    overlap = len(_PERSON_TOKEN) - 1
    count, tail = 0, b""
    with gzip.open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            data = tail + chunk
            count += data.count(_PERSON_TOKEN)
            # The kept tail is shorter than the token, so no token is counted twice.
            tail = data[-overlap:]
    return count


def summarise(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """Summarise the mode changes between the trips before and after the choice (JSON-serialisable).

    Both frames need ``person_id``, ``person_trip_id`` and ``mode``. Raises ValueError if a frame has duplicate trip
    keys or a missing mode, or if the trip sets differ.
    """
    keys = ["person_id", "person_trip_id"]
    for name, frame in (("before", before), ("after", after)):
        if frame.duplicated(subset=keys).any():
            raise ValueError(f"{_LOG_TAG} the trips {name} the choice have duplicate {keys} keys")
        if frame["mode"].isna().any():
            raise ValueError(f"{_LOG_TAG} the trips {name} the choice have a missing mode")
    joined = before.merge(after, on=keys, how="inner", suffixes=("_before", "_after"))
    if not len(before) == len(after) == len(joined):
        raise ValueError(f"{_LOG_TAG} the trips before ({len(before)}) and after ({len(after)}) the choice differ "
                         f"({len(joined)} matched on {keys})")
    n_trips = int(len(joined))
    changed = int((joined["mode_before"] != joined["mode_after"]).sum())
    modes = sorted(set(before["mode"]) | set(after["mode"]))

    def shares(column: pd.Series) -> dict[str, float]:
        values = column.value_counts(normalize=True).reindex(modes).fillna(0.0)
        return {mode: float(values[mode]) for mode in modes}

    return {
        "n_trips": n_trips,
        "n_trips_mode_changed": changed,
        "share_mode_changed": changed / n_trips if n_trips else 0.0,
        "mode_shares_before": shares(before["mode"]),
        "mode_shares_after": shares(after["mode"]),
    }


def run(context, config_name: str) -> dict:
    """Run eqasim's standalone mode choice on the final population and replace the population with the result.

    ``config_name`` is the MATSim config in the stage directory; the population is the file its
    ``plans.inputPlansFile`` names. Travel times are free flow (no travel time option is passed). Raises ValueError if
    the population is outside the stage directory, RuntimeError if it is empty or the choice removed persons (no
    feasible alternative) and FileNotFoundError if the runner wrote no output; the population is replaced only after
    all checks passed. Logs a warning if the choice changed no trip or the trips CSVs are empty. Writes
    ``initial_mode_choice.json`` to the stage directory, leaves the before/after trips CSVs in ``mode_choice/`` and
    returns the summary.
    """
    stage_path = context.path()
    population_path = resolve_population_path(os.path.join(stage_path, config_name))
    # The population is replaced in place below, so it must live inside the stage directory.
    absolute_stage, absolute_population = os.path.abspath(stage_path), os.path.abspath(population_path)
    try:
        inside = os.path.commonpath([absolute_stage, absolute_population]) == absolute_stage
    except ValueError:  # different drives on Windows
        inside = False
    if not inside:
        raise ValueError(f"{_LOG_TAG} the population {population_path} named by {config_name} is not inside the "
                         f"stage directory {stage_path}; refusing to replace a file outside the stage")
    persons_before = count_persons(population_path)
    if persons_before == 0:
        raise RuntimeError(f"{_LOG_TAG} the population {population_path} has no persons; an empty population is "
                           "never a valid input")
    threads = int(context.config("matsim_threads") or context.config("processes"))

    started = time.monotonic()
    # Like every other Java call of the repo, pass the plans file relative to the config (the stage directory).
    relative_population = os.path.relpath(population_path, stage_path).replace(os.sep, "/")
    eqasim.run(context, RUNNER, arguments(config_name, relative_population, threads))
    java_wall_time_s = time.monotonic() - started

    output_directory = os.path.join(stage_path, OUTPUT_DIRECTORY)
    paths = {name: os.path.join(output_directory, name) for name in (OUTPUT_PLANS, INPUT_TRIPS, OUTPUT_TRIPS)}
    for path in paths.values():
        if not os.path.exists(path):
            raise FileNotFoundError(f"{_LOG_TAG} the standalone mode choice did not write {path}; "
                                    f"see the logs in {output_directory}")

    persons_after = count_persons(paths[OUTPUT_PLANS])
    if persons_after != persons_before:
        raise RuntimeError(f"{_LOG_TAG} the population had {persons_before} persons before and {persons_after} after "
                           f"the choice: eqasim removed {persons_before - persons_after} persons with no feasible "
                           f"mode choice alternative; see {OUTPUT_DIRECTORY}/ logs")

    summary = summarise(pd.read_csv(paths[INPUT_TRIPS], sep=CSV_DELIMITER, dtype=str),
                        pd.read_csv(paths[OUTPUT_TRIPS], sep=CSV_DELIMITER, dtype=str))
    if summary["n_trips"] == 0:
        logger.warning("%s no trips in the trips CSVs although the population has %d persons; the summary is empty "
                       "(check the logs in %s/)", _LOG_TAG, persons_before, OUTPUT_DIRECTORY)
    elif summary["n_trips_mode_changed"] == 0:
        logger.warning("%s the choice changed no trip of %d: an ignored configurator or a filter freezing every tour "
                       "is the likely cause (check the logs in %s/)", _LOG_TAG, summary["n_trips"], OUTPUT_DIRECTORY)
    summary.update({
        "persons_before": persons_before,
        "persons_after": persons_after,
        "population_file": os.path.basename(population_path),
        "java_wall_time_s": java_wall_time_s,
        "travel_times": "free_flow",
        "runner": RUNNER,
        "configurator": CONFIGURATOR,
    })

    os.replace(paths[OUTPUT_PLANS], population_path)
    with open(os.path.join(stage_path, SUMMARY_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)

    def percent(shares: dict[str, float]) -> str:
        return ", ".join(f"{mode} {100.0 * share:.1f}%" for mode, share in shares.items())

    logger.info("%s persons %d before, %d after; trips %d; share with changed mode %.1f%%; mode shares before: %s; "
                "after: %s; free-flow travel times; Java wall time %.0f s", _LOG_TAG, persons_before, persons_after,
                summary["n_trips"], 100.0 * summary["share_mode_changed"], percent(summary["mode_shares_before"]),
                percent(summary["mode_shares_after"]), java_wall_time_s)
    return summary
