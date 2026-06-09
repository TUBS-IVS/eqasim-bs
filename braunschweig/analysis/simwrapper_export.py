"""synpp stage: write the SimWrapper dashboard into <output_path>/simwrapper/ on
every run. Two modes, both automatic:
  * synthesis-only (default): all synthesis tabs (fleet, socio, ...); MATSim tabs skip.
  * with MATSim (simwrapper_include_matsim=True): additionally all MATSim tabs.
Flag-gated by simwrapper_export_enabled (default True); writes only into the new
simwrapper/ subfolder, so existing run outputs stay byte-identical.
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
    # Always need the synthesis output (persons/households/vehicles/homes CSVs+GPKG).
    context.stage("synthesis.output")
    # Only depend on the MATSim run when this run includes MATSim. This is an
    # explicit flag (NOT the global default-True run_matsim) so a synthesis-only
    # pipeline never accidentally pulls in / forces a MATSim run.
    if context.config("simwrapper_include_matsim"):
        context.stage("matsim.simulation.run")


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

    written = export_all(
        output_path,
        sim_cache=sim_cache,
        sample_rate=float(context.config("sampling_rate")),
    )
    LOGGER.info("[simwrapper_export] wrote %d dashboard tab(s) into %s/simwrapper",
                len(written), output_path)
    return [str(p) for p in written]
