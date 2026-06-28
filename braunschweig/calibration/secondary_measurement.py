"""Shared secondary-trip distance measurement helpers.

Single source of truth for the per-leg mode -> circuity-network dispatch and the
W12 9-band share computation used by both the validation script
(``scripts/validate_secondary_distances.py``) and the scorer calibration script
(``scripts/calibrate_secondary_scorer.py``). Keeping these helpers here prevents
the two scripts from diverging silently.
"""
from __future__ import annotations

import numpy as np

from braunschweig.calibration.targets import W12_BAND_EDGES_KM

# ---------------------------------------------------------------------------
# Per-leg mode -> circuity network dispatch
# ---------------------------------------------------------------------------

# Map a leg mode to the circuity network used for euclidean -> routed scaling.
# Unknown modes default to 'car' (the most common motorised network).
MODE_TO_NETWORK: dict[str, str] = {
    "car":           "car",
    "car_passenger": "car",
    "pt":            "pt",
    "walk":          "walk",
    "bike":          "walk",
}


def mode_to_network(mode: str) -> str:
    """Return the circuity network ('car'|'pt'|'walk') for a leg mode.

    Unknown modes default to 'car' (the most common motorised network).
    """
    return MODE_TO_NETWORK.get(str(mode), "car")


# ---------------------------------------------------------------------------
# W12 band-share helper
# ---------------------------------------------------------------------------

def w12_band_shares(distances_km) -> np.ndarray:
    """Normalised share per W12 band for an array of distances in km.

    W12_BAND_EDGES_KM = (0, 0.5, 1, 2, 5, 10, 20, 50, 100, inf) -> 9 bands.
    Returns a length-9 float array summing to 1.0 (or all-zero on empty input).
    """
    edges = np.asarray(W12_BAND_EDGES_KM[1:-1], dtype=float)  # inner edges only
    bands = np.digitize(np.asarray(distances_km, dtype=float), edges)
    n_bands = len(W12_BAND_EDGES_KM) - 1  # 9
    counts = np.bincount(bands, minlength=n_bands).astype(float)
    total = counts.sum()
    return counts / total if total > 0 else counts
