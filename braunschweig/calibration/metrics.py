"""Distance-band share + EMD metrics for distribution calibration (shared).

All calibrators in the corner compare a realised STRAIGHT-LINE distance
distribution to a committed MiD distribution target. MiD reports routed Weglaengen;
the model output is euclidean. They are put on the same (routed) axis by scaling
the MODEL output with a documented detour factor -- the committed reference shares
are never transformed (no invented reshaping of a reference).
"""
from __future__ import annotations

import numpy as np

from braunschweig.gravity.friction import BAND_EDGES_KM, band_index

# ASSUMPTION: euclidean->routed detour factor. Same value and rationale as
# braunschweig.data.mid.school_distance (T43). Single source of truth.
DETOUR_FACTOR = 1.3


def apply_detour(euclidean_km, factor=DETOUR_FACTOR):
    """Scale euclidean (straight-line) distances to routed-equivalent km, so the
    model output lies on the same axis as the MiD routed band edges."""
    return np.asarray(euclidean_km, dtype=float) * factor


def band_shares(distances_km, edges=BAND_EDGES_KM, weights=None):
    """Normalised share of mass per distance band."""
    bands = band_index(distances_km, edges)
    n_bands = len(edges) - 1
    if weights is None:
        counts = np.bincount(bands, minlength=n_bands).astype(float)
    else:
        counts = np.bincount(bands, weights=np.asarray(weights, dtype=float),
                             minlength=n_bands).astype(float)
    total = counts.sum()
    return counts / total if total > 0 else counts


def emd_on_bands(p, q):
    """Earth Mover's Distance between two ordered band distributions.

    Equals the mean absolute difference of the cumulative distributions divided by
    (n_bands - 1) so a unit-mass move across the whole range is 1.0 and a one-band
    move is 1/(n_bands-1). Both inputs must sum to 1.
    """
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    cdf_diff = np.cumsum(p) - np.cumsum(q)
    return float(np.abs(cdf_diff[:-1]).sum() / (len(p) - 1))
