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

from braunschweig import synpp_tokens

LOGGER = logging.getLogger("braunschweig.analysis.analysis_suite")

KEY_ENABLED = "analysis_suite_enabled"
# Pure signal ("this run has MATSim outputs"), NEVER a stage dependency: the
# sim outputs are read from the <output_path>/matsim_output archive written by
# matsim.output, so an analysis-only invocation never recomputes the simulation
# chain (issue #354; see braunschweig.analysis.matsim_archive).
KEY_INCLUDE_MATSIM = "simwrapper_include_matsim"
KEY_POPULATION_VALIDATION = "analysis_population_validation"
KEY_MID_VALIDATION = "analysis_mid_validation"
KEY_POPSIM_VALIDATION = "analysis_popsim_validation"
KEY_INTEGERIZER_QUALITY = "analysis_integerizer_quality"
KEY_EDUCATION_VALIDATION = "analysis_education_validation"
KEY_HOUSEHOLD_COMPOSITION = "analysis_household_composition"
KEY_DASHBOARD = "analysis_dashboard"
KEY_STUCK_AGENTS = "analysis_stuck_agents"
# Share of stuck residents (fraction, 0-1) above which the stuck analysis warns; default single-sourced from
# stuck_agents.STUCK_WARN_SHARE (ADR-0146).
KEY_STUCK_WARN_SHARE = "analysis_stuck_warn_share"
KEY_WORK_DIR = "braunschweig.population.popsim.work_dir"
KEY_METHOD = "braunschweig.population.method"
# Issue #96 minor-employment plausibility guard (population_validation). The
# default bound is single-sourced from controls.DEFAULT_MINOR_EMPLOYMENT_MAX_RATE
# (imported lazily where needed to keep stage import light).
KEY_MINOR_EMP_MAX_RATE = "analysis_minor_employment_max_rate"  # fraction, under-15 employed share
KEY_MINOR_EMP_RAISE = "analysis_minor_employment_raise"        # bool: raise vs warn on exceed


#: Hashed by dotted NAME (see :func:`validate`): first the sub-analyses ``configure()`` and
#: ``execute()`` import, then the rest of their import closure.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.analysis.population_validation.controls",
    "braunschweig.analysis.population_validation.population_source",
    "braunschweig.analysis.matsim_archive",
    "braunschweig.analysis.population_validation.run_population_validation",
    "braunschweig.analysis.run_mid_validation",
    "braunschweig.analysis.run_household_composition",
    "braunschweig.analysis.popsim_validation.run_popsim_control_validation",
    "braunschweig.analysis.run_integerizer_quality",
    "braunschweig.analysis.run_education_validation",
    "braunschweig.analysis.dashboard.build_dashboard",
    "braunschweig.analysis.stuck_agents",
    # The rest of the import closure: modules the sub-analyses import, whose code this
    # stage runs without importing it itself. The static walk over-approximates (ADR-0136).
    "braunschweig.analysis.dashboard.comparisons",
    "braunschweig.analysis.dashboard.html_template",
    "braunschweig.analysis.dashboard.mid_reference",
    "braunschweig.analysis.dashboard.paths",
    "braunschweig.analysis.dashboard.run_metrics",
    "braunschweig.analysis.dashboard.run_records",
    "braunschweig.analysis.dashboard.spatial_metrics",
    "braunschweig.analysis.fleet_filter",
    "braunschweig.analysis.freight_filter",
    "braunschweig.analysis.integerizer_quality.cell_error",
    "braunschweig.analysis.integerizer_quality.cell_geometry",
    "braunschweig.analysis.integerizer_quality.report",
    "braunschweig.analysis.integerizer_quality.zone_feasibility",
    "braunschweig.analysis.noise_bands",
    "braunschweig.analysis.pipeline_trips_file",
    "braunschweig.analysis.popsim_validation.controls",
    "braunschweig.analysis.population_validation.control_validation",
    "braunschweig.analysis.population_validation.fleet_age_status",
    "braunschweig.analysis.population_validation.fleet_evaluation",
    "braunschweig.analysis.population_validation.geo_export",
    "braunschweig.analysis.population_validation.participation_fit",
    "braunschweig.analysis.population_validation.quality_assessment",
    "braunschweig.analysis.population_validation.trip_coherence",
    "braunschweig.analysis.population_validation.validation_chart",
    "braunschweig.analysis.spatial",
    "braunschweig.calibration.circuity",
    "braunschweig.calibration.metrics",
    "braunschweig.constants",
    "braunschweig.data.census.households_size_age",
    "braunschweig.data.census.households_type",
    "braunschweig.data.census.population",
    "braunschweig.data.cordon.network",
    "braunschweig.data.kba.fleet_tables",
    "braunschweig.data.mid.reference_tables",
    "braunschweig.data.mid.school_distance",
    "braunschweig.data.mid.status_by_hhtype",
    "braunschweig.data.mid.status_by_kreis",
    "braunschweig.gravity.friction",
    "braunschweig.matsim.simulation.plan_check",
    "braunschweig.popsim.attributes",
    "braunschweig.popsim.cells",
    "braunschweig.popsim.chain_matching",
    "braunschweig.popsim.closure_dwell",
    "braunschweig.popsim.control_spec",
    "braunschweig.popsim.controls",
    "braunschweig.popsim.day_type",
    "braunschweig.popsim.diary_plan_match",
    "braunschweig.popsim.escort_pairing",
    "braunschweig.popsim.folders",
    "braunschweig.popsim.kreis_attribute_control",
    "braunschweig.popsim.member_completion",
    "braunschweig.popsim.mid.control_cells",
    "braunschweig.popsim.mid.csv_format",
    "braunschweig.popsim.mid.donor",
    "braunschweig.popsim.mid.kreis_controls",
    "braunschweig.popsim.mid.participation",
    "braunschweig.popsim.mid_p9",
    "braunschweig.popsim.missing",
    "braunschweig.popsim.ownership_grid",
    "braunschweig.popsim.passenger_availability",
    "braunschweig.popsim.plan_validation",
    "braunschweig.popsim.prepared_cells",
    "braunschweig.popsim.sampling",
    "braunschweig.popsim.seed",
    "braunschweig.popsim.time_imputation",
    "braunschweig.popsim.trips",
    "braunschweig.popsim.weekend_plan_match",
    "braunschweig.popsim.zensus_employment_age",
    "braunschweig.population.socioprofessional_class",
    "braunschweig.synthesis.portal_trips.config_keys",
    "data.hts.egt.cleaned",
    "data.hts.entd.cleaned",
    "data.hts.hts",
    "synthesis.population.matched",
)

def validate(context):
    """synpp validation token: structured token over the sources of every module this stage runs (#454).

    synpp hashes only THIS module's source, and every sub-analysis here is imported lazily
    inside ``execute()``. Without this token an edit to a sub-analysis would leave the cached
    suite result in place, so the reports would silently describe old code. The modules are hashed by
    dotted NAME, which keeps the stage import light; the closure gate in
    tests/test_audit_synpp_helper_hash.py keeps the list complete (ADR-0136).

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it).
    """
    return synpp_tokens.source_token(
        (), _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def configure(context):
    context.config(KEY_ENABLED, True)
    context.config(KEY_POPULATION_VALIDATION, True)
    context.config(KEY_MID_VALIDATION, True)
    context.config(KEY_POPSIM_VALIDATION, True)
    context.config(KEY_INTEGERIZER_QUALITY, True)
    context.config(KEY_EDUCATION_VALIDATION, True)
    context.config(KEY_HOUSEHOLD_COMPOSITION, True)
    context.config(KEY_DASHBOARD, True)
    context.config(KEY_STUCK_AGENTS, True)
    context.config("output_path")
    context.config("sampling_rate")
    context.config("data_path")
    context.config(KEY_INCLUDE_MATSIM, False)
    context.config(KEY_WORK_DIR, None)
    context.config(KEY_METHOD, None)
    context.config("analysis_working_directory", None)
    from braunschweig.analysis.population_validation.controls import (
        DEFAULT_MINOR_EMPLOYMENT_MAX_RATE,
    )
    context.config(KEY_MINOR_EMP_MAX_RATE, DEFAULT_MINOR_EMPLOYMENT_MAX_RATE)
    context.config(KEY_MINOR_EMP_RAISE, False)
    from braunschweig.analysis.stuck_agents import STUCK_WARN_SHARE
    context.config(KEY_STUCK_WARN_SHARE, STUCK_WARN_SHARE)
    if not context.config(KEY_ENABLED):
        return
    context.stage("synthesis.output")


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
    if not list(output_path.glob("*_persons.csv")):
        raise FileNotFoundError(
            f"[analysis_suite] no *_persons.csv in {output_path}; run output malformed")
    prefix = PS._detect_prefix(output_path)

    # MATSim outputs come from the <output_path>/matsim_output archive written
    # by matsim.output (config-derived), never from a stage edge (#354). When
    # the archive is absent, the MATSim-consuming sub-analyses skip LOUDLY with
    # this named reason via the _run readiness contract below.
    from braunschweig.analysis.matsim_archive import (
        archive_missing_reason, resolve_matsim_archive)
    sim_cache = None
    sim_missing_reason = "MATSim not included (simwrapper_include_matsim=False)"
    if context.config(KEY_INCLUDE_MATSIM):
        archive = resolve_matsim_archive(output_path)
        if archive is not None:
            sim_cache = str(archive)
        else:
            sim_missing_reason = archive_missing_reason(output_path)
            LOGGER.warning("[analysis_suite] %s", sim_missing_reason)

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

    # ExecuteContext.config() takes the key alone; all defaults are declared in
    # configure() (see tests/test_execute_context_config_contract.py).
    def _pop():
        from braunschweig.analysis.population_validation import run_population_validation as R
        argv = ["--run-output-dir", str(output_path), "--prefix", prefix,
                "--minor-employment-max-rate",
                str(context.config(KEY_MINOR_EMP_MAX_RATE))]
        if context.config(KEY_MINOR_EMP_RAISE):
            argv.append("--minor-employment-raise")
        R.run(R._parse_args(argv))
    _run(summary, "population_validation",
         context.config(KEY_POPULATION_VALIDATION), True, "", _pop)

    def _mid():
        from braunschweig.analysis import run_mid_validation as R
        argv = ["--output-dir", str(output_path), "--prefix", prefix]
        if sim_cache:
            argv += ["--sim-cache", sim_cache]
        R.main(argv)
    _run(summary, "mid_validation",
         context.config(KEY_MID_VALIDATION), True, "", _mid)

    def _hh():
        from braunschweig.analysis import run_household_composition as R
        R.main(["--output-dir", str(output_path), "--prefix", prefix])
    _run(summary, "household_composition",
         context.config(KEY_HOUSEHOLD_COMPOSITION), True, "", _hh)

    def _popsim():
        from braunschweig.analysis.popsim_validation import run_popsim_control_validation as R
        R.run(R._parse_args(["--run-output-dir", str(output_path), "--prefix", prefix]))
    _run(summary, "popsim_validation",
         context.config(KEY_POPSIM_VALIDATION), is_popsim, "not a popsim run", _popsim)

    mid_dir = (str(Path(data_path) / "braunschweig" / "popsim" / "mid2023_raw")
               if data_path else None)
    iq_ready = (is_popsim and bool(work_dir) and Path(work_dir).is_dir()
                and mid_dir is not None and Path(mid_dir).is_dir())
    def _iq():
        from braunschweig.analysis import run_integerizer_quality as R
        R.main(["--work-dir", work_dir, "--mid-dir", mid_dir, "--output-dir", str(output_path)])
    _run(summary, "integerizer_quality",
         context.config(KEY_INTEGERIZER_QUALITY), iq_ready,
         "popsim work_dir / mid_dir not resolvable", _iq)

    # education_validation uniquely reads synpp CACHE stage pickles (named
    # "<stage>__<hash>.p"), not the output dir. Gate on the cache dir existing AND
    # carrying the stages education loads, so an unset/stale/incomplete cache SKIPS
    # cleanly (loud, with the missing stage named) instead of failing red.
    edu_required_stages = (
        "braunschweig.synthesis.locations.education_gravity",
        "braunschweig.data.schools.facilities",
        "synthesis.population.spatial.home.locations",
    )
    if not (bool(working_directory) and Path(working_directory).is_dir()):
        edu_ready = False
        edu_reason = "working_directory not set (analysis_working_directory)"
    else:
        edu_missing = [s for s in edu_required_stages
                       if not list(Path(working_directory).glob(f"{s}__*.p"))]
        edu_ready = not edu_missing
        edu_reason = ("" if edu_ready
                      else f"education cache incomplete (missing {edu_missing[0]} pickle)")
    def _edu():
        from braunschweig.analysis import run_education_validation as R
        R.main(["--working-directory", str(working_directory),
                "--sampling-rate", str(sampling_rate),
                "--output-dir", str(output_path / "analysis" / "education_validation")])
    _run(summary, "education_validation",
         context.config(KEY_EDUCATION_VALIDATION), edu_ready, edu_reason, _edu)

    # The summary this writes under analysis/stuck/ is reused by the dashboard (run_records.assemble_run_record),
    # so the multi-GB events file is streamed once per suite run; keep this sub-analysis BEFORE the dashboard.
    def _stuck():
        from braunschweig.analysis import stuck_agents as _stuck_module
        _stuck_module.write_stuck_outputs(Path(sim_cache), output_path / "analysis" / "stuck",
                                          warn_share=float(context.config(KEY_STUCK_WARN_SHARE)))
    _run(summary, "stuck_agents",
         context.config(KEY_STUCK_AGENTS), bool(sim_cache), sim_missing_reason, _stuck)

    def _dash():
        import sys as _sys
        from braunschweig.analysis.dashboard import build_dashboard as R
        argv = ["build_dashboard", "--output-dir", str(output_path),
                "--sim-cache", sim_cache, "--sample-rate", str(sampling_rate)]
        old = _sys.argv[:]
        _sys.argv = argv
        try:
            R.main()
        finally:
            _sys.argv = old
    _run(summary, "dashboard",
         context.config(KEY_DASHBOARD), bool(sim_cache), sim_missing_reason, _dash)

    out_summary = output_path / "analysis" / "analysis_suite_summary.json"
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2))
    LOGGER.info("[analysis_suite] ran=%s skipped=%d failed=%d -> %s",
                summary["ran"], len(summary["skipped"]), len(summary["failed"]), out_summary)
    return str(out_summary)
