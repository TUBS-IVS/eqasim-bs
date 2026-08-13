"""Per-run record assembly, persistence, and collection.

This module holds ``assemble_run_record`` -- the function that runs the
eqasim/MATSim metric computation and MiD comparison for one run and packages
the result into the JSON-serialisable run-record dict -- plus ``write_run``
(persists a record to ``runs/<run_id>/metrics.json``) and
``collect_all_runs`` (reads all persisted records back for rendering), moved
verbatim from ``build_dashboard.py``.

``assemble_run_record`` calls ``_find_sim_output``/``metrics_eqasim``/
``metrics_matsim`` from the sibling module ``run_metrics.py``,
``load_mid_reference`` from the sibling module ``mid_reference.py``, and
``build_comparisons`` from the sibling module ``comparisons.py``.
``write_run``/``collect_all_runs`` use ``RUNS_DIR``, imported from the leaf
module ``paths.py`` (its owner) rather than recomputed here.

``build_dashboard.py`` re-exports ``assemble_run_record``, ``write_run``, and
``collect_all_runs`` so existing callers of ``build_dashboard.<name>`` keep
working unchanged.

This module must not import ``build_dashboard`` -- that would create an
import cycle between the facade and this module.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

from braunschweig.analysis.dashboard.comparisons import build_comparisons
from braunschweig.analysis.dashboard.mid_reference import load_mid_reference
from braunschweig.analysis.dashboard.paths import RUNS_DIR
from braunschweig.analysis.dashboard.run_metrics import _find_sim_output
from braunschweig.analysis.dashboard.run_metrics import metrics_eqasim
from braunschweig.analysis.dashboard.run_metrics import metrics_matsim


def assemble_run_record(
    label: str,
    output_dir: Path,
    sim_cache: Path | None,
    sample_rate: float | None,
    notes: str = "",
) -> dict[str, Any]:
    # sim_cache may be None for a synthesis-only run (no MATSim). In that case
    # there is no simulation_output and the MATSim metrics stay "available: False",
    # so the MATSim-dependent dashboard tabs skip (no silent failure).
    sim_output = _find_sim_output(sim_cache) if sim_cache is not None else None
    eqa = metrics_eqasim(output_dir, sample_rate)
    ms = metrics_matsim(sim_output) if sim_output else {"available": False}
    mid = load_mid_reference()
    cmp = build_comparisons(eqa, ms, mid)

    ts = _dt.datetime.now().isoformat(timespec="seconds")
    run_id = (
        _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_"
        + re.sub(r"[^A-Za-z0-9_-]", "_", label or "run")
    )

    return {
        "run_id": run_id,
        "label": label,
        "created_at": ts,
        "notes": notes,
        "sample_rate": sample_rate,
        "paths": {
            "output_dir": str(output_dir),
            "sim_output": str(sim_output) if sim_output else None,
        },
        "eqasim": eqa,
        "matsim": ms,
        "mid_reference": mid,
        "comparisons": cmp,
    }


def write_run(record: dict) -> Path:
    run_dir = RUNS_DIR / record["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    f = run_dir / "metrics.json"
    f.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return f


def collect_all_runs() -> list[dict]:
    runs: list[dict] = []
    if not RUNS_DIR.exists():
        return runs
    for d in sorted(RUNS_DIR.iterdir()):
        f = d / "metrics.json"
        if f.exists():
            try:
                runs.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    runs.sort(key=lambda r: r.get("created_at", ""))
    return runs
