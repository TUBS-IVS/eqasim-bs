"""synpp stage: per-run cross-cordon commuter validation output.

Writes, into ``<output_path>/analysis/cordon/`` on every cordon run:
  - commuter_validation.csv : in-commuter counts per (Kreis, direction, mode)
  - gates.csv / gates.gpkg  : flows per gate (+ point geometry for QGIS)
  - summary.md              : short digest

So "how many in-commuters enter where" is visible + mappable on every run. Flag-gated
on ``cordon_enabled`` (no-op otherwise). Uses the tested
``braunschweig.data.cordon.validation_output.write_cordon_validation``.
"""
from __future__ import annotations

import os

from braunschweig.data.cordon.validation_output import write_cordon_validation


def configure(context):
    context.config("cordon_enabled", False)
    context.config("output_path")
    context.config("sampling_rate")
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")


def execute(context):
    if not context.config("cordon_enabled"):
        return None
    agents = context.stage("braunschweig.synthesis.incommuters")["validation"]
    out_dir = os.path.join(context.config("output_path"), "analysis", "cordon")
    paths = write_cordon_validation(
        out_dir, agents, sampling_rate=float(context.config("sampling_rate")),
        crs="EPSG:25832")
    print(f"[braunschweig.analysis.cordon_validation] {len(agents)} in-commuter records "
          f"-> {out_dir}")
    return paths
