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
from pathlib import Path

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper_export")


def configure(context):
    context.config("simwrapper_export_enabled", True)
    context.config("simwrapper_include_matsim", False)
    context.config("output_path")
    context.config("sampling_rate")
    context.config("cordon_enabled", False)
    # Always need the synthesis output (persons/households/vehicles/homes CSVs+GPKG).
    context.stage("synthesis.output")
    # Only depend on the MATSim run when this run includes MATSim. This is an
    # explicit flag (NOT the global default-True run_matsim) so a synthesis-only
    # pipeline never accidentally pulls in / forces a MATSim run.
    if context.config("simwrapper_include_matsim"):
        context.stage("matsim.simulation.run")
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
    sim_cache = None
    if context.config("simwrapper_include_matsim"):
        # context.path("matsim.simulation.run") is the run stage's cache dir
        # (matsim.simulation.run__<hash>.cache); its PARENT is the synpp cache
        # root that export_all/_find_sim_output globs for simulation_output.
        sim_cache = str(Path(context.path("matsim.simulation.run")).parent)

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
