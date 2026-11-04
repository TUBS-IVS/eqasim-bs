"""Initial mode choice before iteration 0 with eqasim's standalone mode choice (eqasim-bs#542, ADR-0177).

At the end of the prepare stage every agent chooses its modes once with eqasim's ``RunStandaloneModeChoice`` under free
flow travel times, so MATSim starts from a plan set in which each agent has chosen. This module holds the pure helpers
of that step: the runner arguments, the retired-key check, the person count and the before/after trip summary.

The ADR-0155 mechanism (eqasim-bs#465) added a second DiscreteModeChoice replanning strategy with a very high weight to
the MATSim config so that agents chose in iteration 1; it is gone, replaced by this upfront choice (ADR-0177 has the
reasons).
"""
from __future__ import annotations

import gzip
import logging

import pandas as pd

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
                         "runs eqasim's standalone mode choice before iteration 0 (ADR-0177); remove the key")


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

    Both frames need ``person_id``, ``person_trip_id`` and ``mode``. Raises ValueError if the trip sets differ.
    """
    keys = ["person_id", "person_trip_id"]
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
