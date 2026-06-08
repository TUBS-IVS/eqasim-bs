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

import pandas as pd

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


def emit_overview(record: dict, folder: Path) -> "dict[str, Any] | None":
    """Emit a SimWrapper Overview tab with headline KPI tiles and a full KPI table.

    Columns written to ``overview_kpis.csv``: ``metric``, ``value``.
    Returns ``None`` when the eqasim sub-record is absent or unavailable.
    """
    eqa = record.get("eqasim", {})
    ms = record.get("matsim", {})
    cmp = record.get("comparisons", {})
    if not eqa.get("available"):
        return None
    cm = cmp.get("commute_mean_km", {})
    dd = cmp.get("distance_distribution", {})
    rows = [
        ("Persons", eqa.get("n_persons")),
        ("Households", eqa.get("n_households")),
        ("Trips", eqa.get("n_trips")),
        ("Trips / person", eqa.get("trips_per_person")),
        ("Mean trip (km)", ms.get("mean_trip_km")),
        ("Mean commute (km)", cm.get("sim")),
        ("Commute vs MiD (%)", cm.get("diff_pct")),
        ("Distance EMD vs MiD", dd.get("emd")),
        ("Final score", ms.get("score_final")),
        ("Iterations", (ms.get("last_iteration") + 1) if ms.get("last_iteration") is not None else None),
        ("Employed (%)", eqa.get("share_employed_pct")),
        ("Driving licence (%)", eqa.get("share_license_pct")),
        ("PT subscription (%)", eqa.get("share_pt_sub_pct")),
    ]
    df = pd.DataFrame(rows, columns=["metric", "value"])
    name = w.write_csv(folder, "overview_kpis.csv", df)
    return w.dashboard(
        "Overview", f"Braunschweig run - {record.get('label', '')}",
        {"kpis": [w.card_tile("Headline KPIs", name, width=2)],
         "table": [w.card_table("All KPIs", name, width=2)]},
        description="MiD 2023 ZGB as reference. Generated from the eqasim run outputs.",
    )


_REGISTRY.append(("overview", emit_overview))
