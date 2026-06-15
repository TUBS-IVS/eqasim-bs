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
import os
import sys

# Running this file as a script puts scripts/ on sys.path[0] (NOT the repo root),
# so synpp could not import the stage packages (synthesis, matsim, braunschweig)
# and failed with "<stage> is not a supported object". Prepend the repo root (the
# parent of scripts/) so stage modules resolve exactly as with `python -m synpp`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import synpp


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python scripts/run_synpp.py <config.yml>", file=sys.stderr)
        return 1
    from braunschweig.logging_setup import setup_logging

    log_path = setup_logging(level="INFO")
    logging.getLogger("braunschweig").info("Run log: %s", log_path)
    synpp.run_from_yaml(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
