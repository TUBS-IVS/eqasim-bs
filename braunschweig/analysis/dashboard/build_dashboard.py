"""Build / refresh the Braunschweig simulation dashboard.

Usage (PowerShell, conda env `eqasim` activated):

    python -m braunschweig.analysis.dashboard.build_dashboard `
        --output-dir eqasim-data/output_bs_25pct `
        --sim-cache eqasim-data/cache_bs_25pct `
        --label "25pct_v1"

The script
  1. reads the eqasim CSV outputs and MATSim simulation_output/ for one run,
  2. computes a battery of KPIs (mode share, distance bands, commute means,
     iteration evolution, ...),
  3. compares them against MiD 2023 Braunschweig reference values
     (`eqasim-data/data/braunschweig/mid/mid2023_*.csv`),
  4. writes a `metrics.json` into `braunschweig/analysis/dashboard/runs/<run_id>/`,
  5. regenerates `braunschweig/analysis/dashboard/index.html` with all runs
     embedded as JSON (no web-server required, just open the HTML file).

Re-run the script after every new simulation to add a new version.

Module layout: this file is the facade for the ``dashboard`` package. The
large ``HTML_TEMPLATE`` string literal used by ``render_dashboard`` lives in
the sibling module ``html_template.py``. The MiD 2023 reference loader
(``load_mid_reference``) and the distance-band helpers it depends on
(``_to_km_bands``, ``_earth_movers_distance``), along with the constants used
exclusively by them (``MID_DIR``, ``KREIS_NAMES``, ``P13_BINS_KM``,
``P13_LABELS``) and the generic ``_safe_read_csv`` helper, live in the
sibling module ``mid_reference.py``. The per-run metric computation
(``metrics_eqasim``, ``metrics_matsim``) and its supporting helpers
(``_find_sim_output``, ``_detect_sample_rate``) live in the sibling module
``run_metrics.py``. The VG250/per-Kreis spatial cluster that
``metrics_matsim`` calls (``_ensure_vg250``/``_load_zgb_kreise``/
``_classify_points``/``metrics_time_of_day``/``metrics_per_kreis``/
``metrics_od_matrix``), along with the constants used exclusively by that
cluster (``ZGB_ARS5``, ``VG250_ZIP``, ``VG250_CACHE``), live in the sibling
module ``spatial_metrics.py``. The mode-share comparison against the MiD
reference (``build_comparisons``) and the ``MODE_LABEL`` constant it is
organised around live in the sibling module ``comparisons.py``. All are
re-exported below so existing callers of ``build_dashboard.<name>`` keep
working unchanged.

The canonical ``REPO_ROOT``/``DASHBOARD_DIR``/``RUNS_DIR`` path constants live
in the leaf module ``paths.py`` (imported, not recomputed, by this facade and
every sibling that needs them) to avoid duplicated path derivation drifting
apart across the package.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np  # noqa: F401  (namespace parity: baseline import, no longer used directly here)
import pandas as pd  # noqa: F401  (namespace parity: baseline import, no longer used directly here)

from braunschweig.analysis.dashboard.comparisons import MODE_LABEL  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.comparisons import build_comparisons  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.html_template import HTML_TEMPLATE  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import KREIS_NAMES  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import MID_DIR  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import P13_BINS_KM  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import P13_LABELS  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import _earth_movers_distance  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import _safe_read_csv  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import _to_km_bands  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import load_mid_reference  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.paths import DASHBOARD_DIR
from braunschweig.analysis.dashboard.paths import REPO_ROOT
from braunschweig.analysis.dashboard.paths import RUNS_DIR
from braunschweig.analysis.dashboard.run_metrics import _detect_sample_rate  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import _find_sim_output  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_eqasim  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_matsim  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import VG250_CACHE  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import VG250_ZIP  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import ZGB_ARS5  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import _classify_points  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import _ensure_vg250  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import _load_zgb_kreise  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import metrics_od_matrix  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import metrics_per_kreis  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.spatial_metrics import metrics_time_of_day  # noqa: F401  (re-exports)

# ---------------------------------------------------------------------------
# Run record + dashboard rendering
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def render_dashboard(runs: list[dict]) -> Path:
    runs_json = json.dumps(runs, ensure_ascii=False, default=str)
    html = HTML_TEMPLATE.replace("__RUNS_JSON__", runs_json)
    out = DASHBOARD_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Braunschweig simulation dashboard.")
    ap.add_argument("--output-dir", required=False, default="eqasim-data/output_bs_25pct",
                    help="eqasim CSV output folder for the run.")
    ap.add_argument("--sim-cache", required=False, default="eqasim-data/cache_bs_25pct",
                    help="Synpp cache folder containing matsim.simulation.run__*.cache/.")
    ap.add_argument("--label", required=False, default=None,
                    help="Friendly label for this run (defaults to <output_dir name>).")
    ap.add_argument("--notes", required=False, default="", help="Free-form notes.")
    ap.add_argument("--sample-rate", required=False, type=float, default=None,
                    help="Sampling rate (0.01 / 0.1 / 0.25). Detected from folder name if omitted.")
    ap.add_argument("--rebuild-only", action="store_true",
                    help="Only re-render index.html from existing runs/ — no new run added.")
    args = ap.parse_args()

    if not args.rebuild_only:
        out_dir = (REPO_ROOT / args.output_dir).resolve()
        sim_cache = (REPO_ROOT / args.sim_cache).resolve()
        rate = args.sample_rate if args.sample_rate else _detect_sample_rate(out_dir)
        label = args.label or out_dir.name.replace("output_bs_", "").replace("output_bs", "1pct")
        rec = assemble_run_record(label, out_dir, sim_cache, rate, args.notes)
        f = write_run(rec)
        print(f"[dashboard] wrote {f.relative_to(REPO_ROOT)}")

    runs = collect_all_runs()
    html = render_dashboard(runs)
    print(f"[dashboard] {len(runs)} run(s) embedded")
    print(f"[dashboard] open {html.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
