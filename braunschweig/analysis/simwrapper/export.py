"""Convert a build_dashboard ``record`` dict into a self-contained SimWrapper
dashboard folder (CSV data + dashboard-*.yaml tabs).

The ``record`` is produced by
``braunschweig.analysis.dashboard.build_dashboard.assemble_run_record`` -- the
same structure that already drives the interactive HTML dashboard. This module
adds no scientific logic; it only reshapes that data into SimWrapper files so
the dashboard can be opened in simwrapper.app inside the MATSim ecosystem.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Callable

from braunschweig.analysis.dashboard.build_dashboard import (
    REPO_ROOT,
    assemble_run_record,
)
from braunschweig.analysis.simwrapper import writers as w

LOGGER = logging.getLogger("braunschweig.analysis.simwrapper")

# Ordered registry of (filename, emit-fn). Each emit-fn writes its CSV(s) into
# the target folder and returns a dashboard dict (or None if data is absent).
EmitFn = Callable[[dict, Path], "dict[str, Any] | None"]


def export_run(record: dict, target_dir: Path) -> list[Path]:
    """Write all CSVs + dashboard-*.yaml for one run. Returns YAML paths."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for idx, (name, fn) in enumerate(_REGISTRY, start=1):
        try:
            board = fn(record, target_dir)
        except Exception as exc:  # pragma: no cover - defensive, logged loudly
            LOGGER.warning("[simwrapper] tab '%s' skipped: %s", name, exc)
            board = None
        if board is None:
            LOGGER.info("[simwrapper] tab '%s' has no data, skipped", name)
            continue
        path = w.write_yaml(target_dir, f"dashboard-{idx}-{name}.yaml", board)
        written.append(path)
    LOGGER.info("[simwrapper] wrote %d dashboard tab(s) to %s",
                len(written), target_dir)
    return written


# Filled in by Tasks 4-10. Declared here so the orchestrator imports cleanly.
_REGISTRY: list[tuple[str, EmitFn]] = []
