"""synpp stage: per-run cross-cordon commuter validation output.

Writes, into ``<output_path>/analysis/cordon/`` on every cordon run:
  - commuter_validation.csv  : in-commuter counts per (Kreis, direction, mode)
  - gates.csv / gates.gpkg   : per-entry-point flows (+ point geometry for QGIS)
  - summary.md               : short digest
  - gate_volumes.csv         : per-gate BA in/out SvB gravity expectation
  - gate_volumes.gpkg        : same as point geometry for QGIS (both directions)

So "how many in-commuters enter where" AND "how many out-commuters leave where"
(gravity expectation, not realized agents) are visible + mappable on every run.
Flag-gated on ``cordon_enabled`` (no-op otherwise). Uses the tested
``braunschweig.data.cordon.validation_output`` writers.
"""
from __future__ import annotations

import os

from braunschweig.data.cordon.validation_output import (
    write_cordon_validation,
    write_gate_volumes,
)


def configure(context):
    context.config("cordon_enabled", False)
    context.config("output_path")
    context.config("sampling_rate")
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.cordon_gates")


def execute(context):
    if not context.config("cordon_enabled"):
        return None

    incommuters = context.stage("braunschweig.synthesis.incommuters")
    agents = incommuters["validation"]
    # MiD/Mikrozensus target modal split for the "ein" direction, produced by
    # build_incommuter_frames.  Passed to write_cordon_validation so the summary
    # reports realized-vs-target for the in-commuter modal split (CLAUDE.md
    # no-invented-reference-values: the target is derived from the same Mikrozensus
    # reference the mode draw uses, labelled honestly as an aggregate proxy).
    mode_target = incommuters.get("mode_target", None)
    gate_volume = context.stage("braunschweig.synthesis.cordon_gates")
    out_dir = os.path.join(context.config("output_path"), "analysis", "cordon")

    paths = write_cordon_validation(
        out_dir, agents, sampling_rate=float(context.config("sampling_rate")),
        mode_target=mode_target, crs="EPSG:25832")

    gate_paths = write_gate_volumes(
        out_dir, gate_volume["gates"], gate_volume["assignment"], crs="EPSG:25832")
    paths.update(gate_paths)

    # Log per-gate totals so "ein + aus" is visible in the run log.
    total_inbound = int(gate_volume["assignment"]["inbound"].sum())
    total_outbound = int(gate_volume["assignment"]["outbound"].sum())
    print(f"[braunschweig.analysis.cordon_validation] {len(agents)} in-commuter records "
          f"-> {out_dir}")
    print(f"[braunschweig.analysis.cordon_validation] gate volumes (BA SvB gravity expectation): "
          f"inbound (ein) {total_inbound:,} | outbound (aus) {total_outbound:,}")

    return paths
