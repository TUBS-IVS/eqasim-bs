# braunschweig/analysis/analysis_suite.py
"""Collector synpp stage: write all analysis/validation artifacts on every run.

Default-ON. Reuses the existing analysis orchestrators (no reinvented logic).
MATSim-dependent and popsim-dependent sub-analyses are skipped with a loud log
when their inputs are absent; a failing sub-analysis is caught and recorded and
never aborts the (expensive) run. See
docs/superpowers/specs/2026-07-01-auto-analysis-suite-design.md.
"""
from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

LOGGER = logging.getLogger("braunschweig.analysis.analysis_suite")

KEY_ENABLED = "analysis_suite_enabled"
KEY_INCLUDE_MATSIM = "simwrapper_include_matsim"  # reuse: signals the run has MATSim
KEY_POPULATION_VALIDATION = "analysis_population_validation"
KEY_MID_VALIDATION = "analysis_mid_validation"
KEY_POPSIM_VALIDATION = "analysis_popsim_validation"
KEY_INTEGERIZER_QUALITY = "analysis_integerizer_quality"
KEY_EDUCATION_VALIDATION = "analysis_education_validation"
KEY_HOUSEHOLD_COMPOSITION = "analysis_household_composition"
KEY_DASHBOARD = "analysis_dashboard"
KEY_WORK_DIR = "braunschweig.population.popsim.work_dir"
KEY_METHOD = "braunschweig.population.method"


def configure(context):
    context.config(KEY_ENABLED, True)
    context.config(KEY_POPULATION_VALIDATION, True)
    context.config(KEY_MID_VALIDATION, True)
    context.config(KEY_POPSIM_VALIDATION, True)
    context.config(KEY_INTEGERIZER_QUALITY, True)
    context.config(KEY_EDUCATION_VALIDATION, True)
    context.config(KEY_HOUSEHOLD_COMPOSITION, True)
    context.config(KEY_DASHBOARD, True)
    context.config("output_path")
    context.config("sampling_rate")
    context.config("data_path")
    context.config(KEY_INCLUDE_MATSIM, False)
    context.config(KEY_WORK_DIR, None)
    context.config(KEY_METHOD, None)
    context.config("analysis_working_directory", None)
    if not context.config(KEY_ENABLED):
        return
    context.stage("synthesis.output")
    if context.config(KEY_INCLUDE_MATSIM):
        context.stage("matsim.simulation.run")


def _run(summary, name, enabled, ready, reason_if_not_ready, fn):
    """Invoke sub-analysis `fn` with RAN/SKIPPED/FAILED logging + summary record."""
    if not enabled:
        LOGGER.info("[analysis_suite] %s: SKIPPED (flag off)", name)
        summary["skipped"].append({"analysis": name, "reason": "flag off"})
        return
    if not ready:
        LOGGER.warning("[analysis_suite] %s: SKIPPED (%s)", name, reason_if_not_ready)
        summary["skipped"].append({"analysis": name, "reason": reason_if_not_ready})
        return
    try:
        fn()
        LOGGER.info("[analysis_suite] %s: RAN", name)
        summary["ran"].append(name)
    except Exception as exc:  # noqa: BLE001 - one broken analysis must not lose the run
        LOGGER.error("[analysis_suite] %s: FAILED: %s\n%s", name, exc, traceback.format_exc())
        summary["failed"].append({"analysis": name, "error": str(exc)})


def execute(context):
    if not context.config(KEY_ENABLED):
        LOGGER.info("[analysis_suite] disabled (%s=False)", KEY_ENABLED)
        return None

    from braunschweig.analysis.population_validation import population_source as PS

    output_path = Path(context.config("output_path"))
    if not sorted(output_path.glob("*_persons.csv")):
        raise FileNotFoundError(
            f"[analysis_suite] no *_persons.csv in {output_path}; run output malformed")
    prefix = PS._detect_prefix(output_path)

    sim_cache = None
    if context.config(KEY_INCLUDE_MATSIM):
        sim_cache = str(Path(context.path("matsim.simulation.run")).parent)

    method = context.config(KEY_METHOD)
    is_popsim = bool(method) and "popsim" in str(method)
    work_dir = context.config(KEY_WORK_DIR)
    data_path = context.config("data_path")
    sampling_rate = float(context.config("sampling_rate"))
    working_directory = context.config("analysis_working_directory")

    summary = {
        "output_path": str(output_path), "prefix": prefix,
        "ran": [], "skipped": [], "failed": [],
    }

    # Sub-analyses are wired in Tasks 2-5 (each appends _run(...) calls here).

    out_summary = output_path / "analysis" / "analysis_suite_summary.json"
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2))
    LOGGER.info("[analysis_suite] ran=%s skipped=%d failed=%d -> %s",
                summary["ran"], len(summary["skipped"]), len(summary["failed"]), out_summary)
    return str(out_summary)
