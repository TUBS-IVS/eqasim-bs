"""MiD 2023 reference-value loading and distance-band comparison helpers.

This module holds ``load_mid_reference`` and the small numeric helpers it
depends on (``_to_km_bands``, ``_earth_movers_distance``), moved verbatim from
``build_dashboard.py``.

It also owns ``MID_DIR``, ``KREIS_NAMES``, ``P13_BINS_KM`` and ``P13_LABELS``:
at the time of this extraction these constants were used exclusively by the
functions above (no other function in ``build_dashboard.py`` referenced
them), so they were moved here with them rather than left behind. A later
split step gave ``KREIS_NAMES`` a second user: the VG250/per-Kreis cluster
now in ``spatial_metrics.py`` imports it directly from this module (see that
module's docstring); ``MID_DIR``, ``P13_BINS_KM`` and ``P13_LABELS`` remain
used only by the functions above. The same applies to
``_safe_read_csv``: it is a generic CSV-reading helper (also used later by
``metrics_eqasim``/``metrics_matsim``), but at the time of this extraction it
had no sibling module to own it yet, and ``load_mid_reference`` needs it.
Leaving any of these in ``build_dashboard.py`` would force this module to
import back from the facade, creating an import cycle -- so they were moved
here instead. ``build_dashboard.py`` re-imports ``KREIS_NAMES`` only to
re-export it (see the ``# noqa: F401 (re-exports)`` marker there); the
VG250/per-Kreis helpers that actually use ``KREIS_NAMES`` internally now live
in ``spatial_metrics.py``, which imports it directly from this module (see
that module's docstring). Every name defined here is re-exported by
``build_dashboard.py`` so existing callers of ``build_dashboard.<name>`` keep
working unchanged.

``REPO_ROOT`` (needed to build ``MID_DIR``) is imported from the leaf module
``paths.py`` rather than recomputed privately here.

This module must not import ``build_dashboard`` -- that would create an
import cycle between the facade and this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from braunschweig.analysis.dashboard.paths import REPO_ROOT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MID_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "mid"

# MiD P13 distance bands (km).  Upper bound 250 km used for >=100 km bin.
P13_BINS_KM = [0.0, 0.5, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, 250.0]
P13_LABELS = [
    "0–0.5", "0.5–5", "5–10", "10–20",
    "20–30", "30–50", "50–100", "100+",
]
# MiD P13 also exposes "d_0" (=0 km, no commute) which we keep separate.

KREIS_NAMES = {
    "03101": "Braunschweig",
    "03102": "Salzgitter",
    "03103": "Wolfsburg",
    "03151": "Gifhorn",
    "03153": "Goslar",
    "03154": "Helmstedt",
    "03157": "Peine",
    "03158": "Wolfenbüttel",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_read_csv(path: Path, **kw: Any) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path, **kw)


def _to_km_bands(distances_km: np.ndarray) -> dict[str, float]:
    """Return percentage shares per P13 band."""
    if distances_km.size == 0:
        return {lbl: 0.0 for lbl in P13_LABELS}
    edges = np.array(P13_BINS_KM)
    idx = np.clip(np.searchsorted(edges, distances_km, side="right") - 1, 0, len(P13_LABELS) - 1)
    counts = np.bincount(idx, minlength=len(P13_LABELS))
    pct = counts / counts.sum() * 100.0
    return {lbl: float(round(pct[i], 2)) for i, lbl in enumerate(P13_LABELS)}


def _earth_movers_distance(p_pct: list[float], q_pct: list[float]) -> float:
    """1D EMD on equal-mass percentage histograms."""
    p = np.array(p_pct, dtype=float) / 100.0
    q = np.array(q_pct, dtype=float) / 100.0
    if p.sum() == 0 or q.sum() == 0:
        return float("nan")
    p /= p.sum()
    q /= q.sum()
    return float(np.abs(np.cumsum(p) - np.cumsum(q)).sum())


# ---------------------------------------------------------------------------
# MiD reference loader
# ---------------------------------------------------------------------------


def load_mid_reference() -> dict[str, Any]:
    # dtype=str at READ time: int64 inference would strip the ars5 leading
    # zero of a per-Kreis-only file irreversibly; do not rely on the "03ZGB"
    # row forcing object dtype (see data/mid/reference_tables._read_csv).
    p12 = _safe_read_csv(MID_DIR / "mid2023_P12_1.csv", dtype={"ars5": str})
    p13 = _safe_read_csv(MID_DIR / "mid2023_P13.csv", dtype={"ars5": str})

    ref: dict[str, Any] = {"available": False}
    if p12 is None or p13 is None:
        return ref

    p12_total = p12.loc[p12["ars5"] == "03ZGB"].iloc[0]
    p13_total = p13.loc[p13["ars5"] == "03ZGB"].iloc[0]

    # P13 distance distribution → renormalise (drop "keine_feste_arbeit"/"keine_angabe")
    d_cols = ["d_0_5", "d_5_10", "d_10_20", "d_20_30", "d_30_50", "d_50_100", "d_100p"]
    raw = {c: float(p13_total[c]) for c in d_cols}
    # Add "d_0" (=0 km) into 0-0.5 bucket so we have 8 bins matching synth.
    raw_first = float(p13_total["d_0"]) + raw["d_0_5"]
    pct_list = [raw_first, raw["d_5_10"], raw["d_10_20"], raw["d_20_30"],
                raw["d_30_50"], raw["d_50_100"], raw["d_100p"]]
    # Re-normalise to 100 (some kreise have rounding errors)
    s = sum(pct_list)
    if s > 0:
        pct_list = [round(x / s * 100, 2) for x in pct_list]
    # The synth bands have 8 entries (we split 0-0.5 vs 0.5-5);
    # for simplicity fold synth's "0-0.5" + "0.5-5" together when EMD is computed.
    p13_dist = {
        "0–5": pct_list[0],
        "5–10": pct_list[1],
        "10–20": pct_list[2],
        "20–30": pct_list[3],
        "30–50": pct_list[4],
        "50–100": pct_list[5],
        "100+": pct_list[6],
    }

    per_kreis = []
    for _, row in p13.iterrows():
        ars5 = str(row["ars5"])
        if ars5 == "03ZGB":
            continue
        per_kreis.append({
            "ars5": ars5,
            "name": KREIS_NAMES.get(ars5, str(row["kreis"])),
            "mean_km": float(row["mittel"]),
            "n_weighted": float(row["n_weighted"]),
        })

    p12_per_kreis = []
    for _, row in p12.iterrows():
        ars5 = str(row["ars5"])
        if ars5 == "03ZGB":
            continue
        p12_per_kreis.append({
            "ars5": ars5,
            "name": KREIS_NAMES.get(ars5, str(row["kreis"])),
            "auto": float(row["auto"]),
            "oeffentlich": float(row["oeffentlich"]),
            "fahrrad": float(row["fahrrad"]),
            "zu_fuss": float(row["zu_fuss"]),
        })

    ref.update({
        "available": True,
        "p12_modal_split_zgb": {
            # Note: MiD P12_1 = "any mode used"; rows can sum >100.
            "Car": float(p12_total["auto"]),
            "PT": float(p12_total["oeffentlich"]),
            "Bicycle": float(p12_total["fahrrad"]),
            "Walk": float(p12_total["zu_fuss"]),
        },
        "p13_mean_km_zgb": float(p13_total["mittel"]),
        "p13_distance_pct_zgb": p13_dist,
        "p13_per_kreis": per_kreis,
        "p12_per_kreis": p12_per_kreis,
        "n_weighted_zgb": float(p13_total["n_weighted"]),
    })
    return ref
