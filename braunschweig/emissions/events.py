"""synpp stage: run MATSim's ``AirPollutionAnalysis`` on the emissions view (post-run emissions, #688).

The tool comes from the pipeline jar (``matsim.runtime.eqasim``); no Java code of this project
is involved. It reads the view of ``braunschweig.emissions.inputs`` and writes into this stage's
directory (the Java call runs with the stage directory as working directory, so the output names
are given relative).

What the tool does, measured on the pinned jar (wiring test of 2026-11-26, issue #688):

* every output is already multiplied by ``1 / sample size``; nothing may be scaled again. The
  stage passes ``sampling_rate`` and stops when the archive was simulated with another rate (or
  does not name its rate): the tool would scale by whatever it is given;
* ``emissions_total.csv`` (kg), the per-link files and the grids drop every emission from 24:00
  on; ``emissions_per_vehicle_type.csv`` and ``emissions_per_network_mode.csv`` (g) do not;
* the raster cannot be switched off, only coarsened. With the tool's own default of 100 m an
  8 GB heap ran out on a 1 % archive, 1000 m finished; the grid size is therefore a config key
  whose default is the size that is proven to run. The heap is ``java_memory``.

Returns ``{"files": {name: file name}, "report_path", "sample_size", "grid_size_meters"}``; all
file names are relative to the stage directory.
"""
from __future__ import annotations

import json
import logging
import os
import time

from braunschweig import synpp_tokens
import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java

logger = logging.getLogger(__name__)

_LOG_TAG = "[emissions.events]"

INPUTS_STAGE = "braunschweig.emissions.inputs"
TOOL_CLASS = "org.matsim.application.analysis.emissions.AirPollutionAnalysis"
REPORT_FILE_NAME = "emissions_events_report.json"

#: Edge length of the tool's raster cells in metres (``--grid-size``).
KEY_GRID_SIZE = "emissions_grid_size_meters"
DEFAULT_GRID_SIZE_METERS = 1000.0

#: Largest difference between ``sampling_rate`` and the archive's sample size that counts as equal
#: (both are read from text; this only absorbs float formatting).
_SAMPLE_SIZE_TOLERANCE = 1e-9

#: Outputs the summary reads; the tool writes them (and its per-link files and grids) by these names.
TOTAL_FILE_NAME = "emissions_total.csv"
PER_VEHICLE_TYPE_FILE_NAME = "emissions_per_vehicle_type.csv"
PER_NETWORK_MODE_FILE_NAME = "emissions_per_network_mode.csv"
REQUIRED_OUTPUTS = {
    "total": TOTAL_FILE_NAME,
    "per_vehicle_type": PER_VEHICLE_TYPE_FILE_NAME,
    "per_network_mode": PER_NETWORK_MODE_FILE_NAME,
}

#: ``eqasim`` and ``java`` are declared stages this stage runs as libraries (``eqasim.run``,
#: ``java.run``): the DAG edge covers their output, not this code (ADR-0194).
_HELPER_MODULES = (eqasim, java)
_DEFERRED_HELPER_MODULE_NAMES = ("matsim.runtime.process_watchdog",)
#: Stage modules the closure walk reaches through the hashed ``matsim.runtime.eqasim`` helper but
#: whose code this stage never runs (read by scripts/audit_synpp_helper_hash.py, ADR-0136).
_TOKEN_CLOSURE_BOUNDARIES = {
    "matsim.runtime.git": "only matsim.runtime.eqasim.execute/configure call git.run; this stage "
                          "calls eqasim.run, which calls java.run",
    "matsim.runtime.maven": "only matsim.runtime.eqasim.execute/configure call maven.run; this stage "
                            "calls eqasim.run, which calls java.run",
}


def validate(context):
    """synpp validation token over the helper sources."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES, stage_label=__name__ + ".validate()")


def configure(context):
    context.stage(INPUTS_STAGE)
    context.stage("matsim.runtime.eqasim")
    context.stage("matsim.runtime.java")
    # eqasim.run -> java.run reads java_binary, java_memory and the hang-watchdog keys from this stage's context.
    java.configure(context)
    context.config("sampling_rate")
    context.config(KEY_GRID_SIZE, DEFAULT_GRID_SIZE_METERS)


def execute(context):
    sample_size = context.config("sampling_rate")
    if isinstance(sample_size, bool) or not isinstance(sample_size, (int, float)) or not 0.0 < sample_size <= 1.0:
        raise ValueError(f"{_LOG_TAG} sampling_rate={sample_size!r} must lie in (0, 1]")
    grid_size = context.config(KEY_GRID_SIZE)
    if isinstance(grid_size, bool) or not isinstance(grid_size, (int, float)) or grid_size <= 0:
        raise ValueError(f"{_LOG_TAG} {KEY_GRID_SIZE}={grid_size!r} must be a positive number of metres")
    inputs = context.stage(INPUTS_STAGE)
    # The tool multiplies every output by 1 / sample size and cannot know the rate of the archive.
    simulated = inputs["archive_sample_size"]
    if simulated is None:
        raise ValueError(
            f"{_LOG_TAG} the config of the archive {inputs['archive']} does not name the sample size of its "
            "simulation (module eqasim, parameter sampleSize), so sampling_rate cannot be confirmed; "
            "the outputs would be scaled by an unchecked factor"
        )
    if abs(simulated - sample_size) > _SAMPLE_SIZE_TOLERANCE:
        raise ValueError(
            f"{_LOG_TAG} sampling_rate={sample_size!r} of this configuration, but the archive "
            f"{inputs['archive']} was simulated with {simulated!r}; every output would be scaled by the "
            "wrong factor. Run the emissions stages with the configuration of the archived run."
        )
    # Absolute: the Java call runs with this stage's directory as working directory.
    view = os.path.join(os.path.abspath(context.path(INPUTS_STAGE)), inputs["view_path"])
    arguments = [
        "--input", view,
        "--config", os.path.join(view, "output_config.xml"),
        "--sample-size", str(sample_size),
        "--grid-size", str(grid_size),
        "--output", TOTAL_FILE_NAME,
    ]
    logger.info("%s running %s %s", _LOG_TAG, TOOL_CLASS, " ".join(arguments))
    started = time.monotonic()
    eqasim.run(context, TOOL_CLASS, arguments)
    runtime_seconds = time.monotonic() - started

    stage_dir = context.path()
    sizes = {}
    for name, file_name in REQUIRED_OUTPUTS.items():
        path = os.path.join(stage_dir, file_name)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError(f"{_LOG_TAG} the tool finished without writing {file_name} into {stage_dir}")
        sizes[file_name] = os.path.getsize(path)
    report = {
        "stage": __name__,
        "tool": TOOL_CLASS,
        "arguments": arguments,
        "sample_size": sample_size,
        "grid_size_meters": grid_size,
        "runtime_seconds": runtime_seconds,
        "output_size_bytes": sizes,
        "note": "Every output is already multiplied by 1 / sample size. emissions_total.csv, the "
                "per-link files and the grids end at 24:00; the per-vehicle-type file does not.",
    }
    with open(os.path.join(stage_dir, REPORT_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    logger.info("%s tool finished in %.0f s; outputs %s", _LOG_TAG, runtime_seconds, sizes)
    return {
        "files": dict(REQUIRED_OUTPUTS),
        "report_path": REPORT_FILE_NAME,
        "sample_size": sample_size,
        "grid_size_meters": grid_size,
    }
