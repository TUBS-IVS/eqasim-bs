"""synpp stage: write the SimWrapper dashboard into <output_path>/simwrapper/ on
every run. Two modes, both automatic:
  * synthesis-only (default): all synthesis tabs (fleet, socio, ...); MATSim tabs skip.
  * with MATSim (simwrapper_include_matsim=True): additionally all MATSim tabs.
Flag-gated by simwrapper_export_enabled (default True); writes only into the new
simwrapper/ subfolder, so existing run outputs stay byte-identical.

When ``cordon_enabled`` (#140), this stage also pulls the LIVE
``braunschweig.synthesis.student_incommuters`` output and threads it through to
``export_all`` so the student-commuters tab (OD flows + distance) can be
produced -- see ``braunschweig.analysis.simwrapper.spatial_export
.emit_student_commuters`` for why this needs the live stage frames rather than
a disk artifact. When ``cordon_enabled`` is False (the default) this adds no
new stage dependency and no new behaviour, so the byte-identical baseline is
preserved.
"""
from __future__ import annotations

import logging

from braunschweig import synpp_tokens

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper_export")


#: Hashed by dotted NAME (see :func:`validate`): first the modules ``execute()`` imports, then
#: the rest of their import closure.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.analysis.simwrapper.export",
    "braunschweig.analysis.matsim_archive",
    # The rest of the import closure: modules the sub-analyses import, whose code this
    # stage runs without importing it itself. The static walk over-approximates (ADR-0136).
    "braunschweig.analysis.dashboard.build_dashboard",
    "braunschweig.analysis.dashboard.comparisons",
    "braunschweig.analysis.dashboard.html_template",
    "braunschweig.analysis.dashboard.mid_reference",
    "braunschweig.analysis.dashboard.paths",
    "braunschweig.analysis.dashboard.run_metrics",
    "braunschweig.analysis.dashboard.run_records",
    "braunschweig.analysis.dashboard.spatial_metrics",
    "braunschweig.analysis.fleet_filter",
    "braunschweig.analysis.freight_filter",
    "braunschweig.analysis.pipeline_trips_file",
    "braunschweig.analysis.population_validation.population_source",
    "braunschweig.analysis.simwrapper.behaviour",
    "braunschweig.analysis.simwrapper.commuter_tabs",
    "braunschweig.analysis.simwrapper.commuters",
    "braunschweig.analysis.simwrapper.fleet",
    "braunschweig.analysis.simwrapper.geo_layers",
    "braunschweig.analysis.simwrapper.socio",
    "braunschweig.analysis.simwrapper.spatial_export",
    "braunschweig.analysis.simwrapper.student_commuters",
    "braunschweig.analysis.simwrapper.trip_demand",
    "braunschweig.analysis.simwrapper.writers",
    "braunschweig.analysis.spatial",
    "braunschweig.data.cordon.plans",
    "braunschweig.synthesis.portal_trips.config_keys",
    "braunschweig.analysis.stuck_agents",
    "braunschweig.matsim.simulation.plan_check",
    "braunschweig.popsim.closure_dwell",
    "braunschweig.popsim.plan_validation",
    "data.hts.hts",
)

def validate(context):
    """synpp validation token: structured token over the sources of every module this stage runs (#454).

    synpp hashes only THIS module's source, and every sub-analysis here is imported lazily
    inside ``execute()``. Without this token an edit to a sub-analysis would leave the cached
    dashboard tabs in place, so the reports would silently describe old code. The modules are hashed by
    dotted NAME, which keeps the stage import light; the closure gate in
    tests/test_audit_synpp_helper_hash.py keeps the list complete (ADR-0136).

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it).
    """
    return synpp_tokens.source_token(
        (), _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def configure(context):
    context.config("simwrapper_export_enabled", True)
    context.config("simwrapper_include_matsim", False)
    context.config("output_path")
    context.config("sampling_rate")
    context.config("cordon_enabled", False)
    # Always need the synthesis output (persons/households/vehicles/homes CSVs+GPKG).
    context.stage("synthesis.output")
    # simwrapper_include_matsim is a pure SIGNAL ("this run has MATSim
    # outputs"), never a stage dependency: the sim outputs are read from the
    # <output_path>/matsim_output archive written by matsim.output, so an
    # analysis-only invocation never recomputes the simulation chain (#354).
    # Only depend on the student in-commuter stage when cordon is on (mirrors
    # braunschweig.matsim.scenario.population's conditional wiring of the same
    # stage), so an unrelated run's dependency graph is unaffected.
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.student_incommuters")


def execute(context):
    if not context.config("simwrapper_export_enabled"):
        LOGGER.info("[simwrapper_export] disabled (simwrapper_export_enabled=False)")
        return None
    from braunschweig.analysis.simwrapper.export import export_all

    output_path = context.config("output_path")
    # MATSim outputs come from the <output_path>/matsim_output archive written
    # by matsim.output (config-derived, #354). When the archive is absent,
    # export_all receives sim_cache=None and its MATSim tabs skip loudly.
    from braunschweig.analysis.matsim_archive import (
        archive_missing_reason, resolve_matsim_archive)
    sim_cache = None
    if context.config("simwrapper_include_matsim"):
        archive = resolve_matsim_archive(output_path)
        if archive is not None:
            sim_cache = str(archive)
        else:
            LOGGER.warning("[simwrapper_export] %s -- MATSim tabs will skip",
                           archive_missing_reason(output_path))

    student_frames = None
    if context.config("cordon_enabled"):
        student_frames = context.stage("braunschweig.synthesis.student_incommuters")

    written = export_all(
        output_path,
        sim_cache=sim_cache,
        sample_rate=float(context.config("sampling_rate")),
        student_frames=student_frames,
    )
    LOGGER.info("[simwrapper_export] wrote %d dashboard tab(s) into %s/simwrapper",
                len(written), output_path)
    return [str(p) for p in written]
