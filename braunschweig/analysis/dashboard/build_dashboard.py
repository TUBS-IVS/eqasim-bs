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
(``_find_sim_output``, ``_detect_sample_rate``, and the VG250/per-Kreis
spatial cluster ``_ensure_vg250``/``_load_zgb_kreise``/``_classify_points``/
``metrics_time_of_day``/``metrics_per_kreis``/``metrics_od_matrix`` that
``metrics_matsim`` calls), along with the constants used exclusively by that
cluster (``ZGB_ARS5``, ``VG250_ZIP``, ``VG250_CACHE``), live in the sibling
module ``run_metrics.py``. All are re-exported below so existing callers of
``build_dashboard.<name>`` keep working unchanged.
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

from braunschweig.analysis.dashboard.html_template import HTML_TEMPLATE  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import KREIS_NAMES  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import MID_DIR  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import P13_BINS_KM  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import P13_LABELS  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import _earth_movers_distance  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import _safe_read_csv  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import _to_km_bands  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.mid_reference import load_mid_reference  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import VG250_CACHE  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import VG250_ZIP  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import ZGB_ARS5  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import _classify_points  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import _detect_sample_rate  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import _ensure_vg250  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import _find_sim_output  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import _load_zgb_kreise  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_eqasim  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_matsim  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_od_matrix  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_per_kreis  # noqa: F401  (re-exports)
from braunschweig.analysis.dashboard.run_metrics import metrics_time_of_day  # noqa: F401  (re-exports)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = Path(__file__).resolve().parent
RUNS_DIR = DASHBOARD_DIR / "runs"

# Mode mapping eqasim -> MiD.  MiD P12_1 reports any-mode used per commute
# (rows can sum >100 %).  We compare to the synth main mode.
MODE_LABEL = {
    "car": "Car",
    "car_passenger": "Car (passenger)",
    "pt": "PT",
    "bicycle": "Bicycle",
    "walk": "Walk",
}

# ---------------------------------------------------------------------------
# Comparisons against MiD
# ---------------------------------------------------------------------------


def build_comparisons(eqa: dict, ms: dict, mid: dict) -> dict[str, Any]:
    cmp: dict[str, Any] = {}
    if not mid.get("available"):
        return cmp

    # commute distance vs MiD P13 (ZGB-Gesamt)
    if ms.get("commute"):
        sim_mean = ms["commute"]["mean_km"]
        ref_mean = mid["p13_mean_km_zgb"]
        cmp["commute_mean_km"] = {
            "sim": sim_mean, "mid": ref_mean,
            "diff_km": round(sim_mean - ref_mean, 2),
            "diff_pct": round((sim_mean - ref_mean) / ref_mean * 100, 1),
        }

        # MiD distribution alignment (re-aggregate sim into MiD bands)
        # MiD bands: 0–5 / 5–10 / 10–20 / 20–30 / 30–50 / 50–100 / 100+
        sim_dist = ms["commute"]["dist_pct"]
        sim_aligned = {
            "0–5": sim_dist["0–0.5"] + sim_dist["0.5–5"],
            "5–10": sim_dist["5–10"],
            "10–20": sim_dist["10–20"],
            "20–30": sim_dist["20–30"],
            "30–50": sim_dist["30–50"],
            "50–100": sim_dist["50–100"],
            "100+": sim_dist["100+"],
        }
        emd = _earth_movers_distance(
            list(sim_aligned.values()),
            list(mid["p13_distance_pct_zgb"].values()),
        )
        cmp["distance_distribution"] = {
            "bands": list(sim_aligned.keys()),
            "sim_pct": list(sim_aligned.values()),
            "mid_pct": list(mid["p13_distance_pct_zgb"].values()),
            "emd": round(emd, 4),
            "tolerance": 0.08,  # from quality/QUALITY.md scenario 7
            "ok": bool(emd <= 0.08),
        }

    # mode share — work commute vs MiD P12_1 ZGB
    if ms.get("commute") and ms["commute"].get("mode_share_pct"):
        sim_ms = ms["commute"]["mode_share_pct"]
        sim_translated = {
            "Car": sim_ms.get("car", 0.0),
            "Car (passenger)": sim_ms.get("car_passenger", 0.0),
            "PT": sim_ms.get("pt", 0.0),
            "Bicycle": sim_ms.get("bicycle", 0.0),
            "Walk": sim_ms.get("walk", 0.0),
        }
        mid_ms = mid["p12_modal_split_zgb"]
        cmp["work_mode_share"] = {
            "modes": ["Car", "PT", "Bicycle", "Walk"],
            "sim_pct": [round(sim_translated[m], 1) for m in ["Car", "PT", "Bicycle", "Walk"]],
            "mid_pct": [round(mid_ms[m], 1) for m in ["Car", "PT", "Bicycle", "Walk"]],
            "note": "MiD P12_1 reports 'every mode used per commute' (rows can sum >100%); sim shows main mode only.",
        }
    return cmp


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
