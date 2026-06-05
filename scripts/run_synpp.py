"""Run the synpp pipeline with timestamped logging.

Equivalent to ``python -m synpp <config.yml>`` but installs a logging format that
prefixes every synpp log line with an ISO timestamp (asctime). This lets
``braunschweig.analysis.runtime`` compute per-stage wall-clock durations from the
run log, so the slowest stages (secondary locations / location choice, gravity,
...) are visible and settings can be tuned with evidence.

Used by ``scripts/run_pipeline.sh`` in place of ``python -m synpp``. MATSim's Java
stdout keeps its own timestamps; braunschweig ``print()`` diagnostics are
unchanged. ``force=True`` ensures this format wins even if synpp configures
logging itself.
"""
from __future__ import annotations

import logging
import sys

import synpp


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python scripts/run_synpp.py <config.yml>", file=sys.stderr)
        return 1
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=logging.INFO,
        force=True,
    )
    synpp.run_from_yaml(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
