"""Comparisons of simulated run metrics against the MiD 2023 reference.

This module holds ``build_comparisons`` -- the function that turns a run's
eqasim/MATSim metric dictionaries and the loaded MiD reference into the
``comparisons`` block assembled into each run record -- plus the
``MODE_LABEL`` constant it is organised around (the eqasim-mode -> MiD-label
mapping used when describing mode-share comparisons), moved verbatim from
``build_dashboard.py``.

``build_comparisons`` uses ``_earth_movers_distance``, which is imported from
the sibling module ``mid_reference.py`` (its owner after an earlier split
step); this module never imports ``build_dashboard`` back.

``build_dashboard.py`` re-exports ``MODE_LABEL`` and ``build_comparisons`` so
existing callers of ``build_dashboard.<name>`` keep working unchanged.

This module must not import ``build_dashboard`` -- that would create an
import cycle between the facade and this module.
"""

from __future__ import annotations

from typing import Any

from braunschweig.analysis.dashboard.mid_reference import _earth_movers_distance

# Mode mapping eqasim -> MiD.  MiD P12_1 reports any-mode used per commute
# (rows can sum >100 %).  We compare to the synth main mode.
MODE_LABEL = {
    "car": "Car",
    "car_passenger": "Car (passenger)",
    "pt": "PT",
    "bicycle": "Bicycle",
    "walk": "Walk",
}


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
