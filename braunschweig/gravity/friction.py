"""Per-distance-band friction for the gravity models.

The legacy friction is a scalar exponential decay ``exp(slope * d)``. This module
generalises it to per-band friction factors ``f_b`` (one factor per distance band)
so the gravity model's trip-length DISTRIBUTION can be calibrated to an empirical
target, not just its mean. ``factors=None`` reproduces the legacy exponential
friction byte-identically (OFF path).
"""
from __future__ import annotations

import numpy as np

# Distance-band edges in km (single source of truth; aligned to MiD P13 bands,
# with the exactly-0 "same place" P13 column folded into the first band -- the
# intra-Gemeinde diagonal term carries the ~0 mass).
BAND_EDGES_KM = (0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, float("inf"))


def band_index(distances_km, edges=BAND_EDGES_KM):
    """Return the band index (0..len(edges)-2) for each distance in km."""
    inner = np.asarray(edges[1:-1], dtype=float)
    return np.digitize(np.asarray(distances_km, dtype=float), inner)


def build_friction_matrix(distances_km, slope_vec, constant, diagonal,
                          factors=None, edges=BAND_EDGES_KM, rs7_vec=None):
    """Assemble the gravity friction matrix.

    ``distances_km`` must be a 2-D matrix of shape ``(n_origins, n_destinations)``.

    ``factors=None`` -> legacy ``exp(slope_vec[:, None] * d + constant)``.
    ``factors={band: f}`` -> global per-band friction.
    ``factors={rs7: {band: f}}`` with ``rs7_vec`` -> per-origin-RS7 per-band
    friction. In all cases the intra-Gemeinde ``diagonal`` is added on the main
    diagonal exactly as in the legacy model.
    """
    distances_km = np.asarray(distances_km, dtype=float)
    n = distances_km.shape[0]

    if factors is None:
        base = np.exp(np.asarray(slope_vec)[:, None] * distances_km + constant)
        return base + np.eye(n) * diagonal

    bands = band_index(distances_km, edges)
    if rs7_vec is None:
        # Global per-band: use vectorised look-up table
        # Build LUT for all possible bands (0..len(edges)-2), expecting all to be in factors
        lut = np.array([float(factors[b]) for b in range(len(edges) - 1)])
        base = lut[bands]
    else:
        # Per-RS7 per-band: vectorise the inner loop (origin rows stay separate)
        rs7_vec = np.asarray(rs7_vec)
        base = np.empty_like(distances_km)
        for i in range(n):
            row_factors = factors[int(rs7_vec[i])]
            lut = np.array([float(row_factors[b]) for b in range(len(edges) - 1)])
            base[i, :] = lut[bands[i, :]]

    return base + np.eye(n) * diagonal
