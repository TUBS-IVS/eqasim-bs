"""Capacity-constrained distance-decay assignment for school-age pupils.

The doubly-constrained Furness balancing is the rectangular generalisation of
``braunschweig.gravity.model.evaluate_gravity`` (square N x N): pupils are rows
(production target 1 each -> everyone is placed), schools are columns (attraction
target = capacity scaled to the pupil count -> schools fill in proportion to real
Schuelerplaetze). The distance-decay friction shapes who-goes-where within that.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.distance import cdist


def balance_doubly_constrained(production, attraction, friction,
                               max_iterations=50, tolerance=1e-3):
    """Rectangular doubly-constrained gravity balancing.

    production: (R,) row targets; attraction: (C,) column targets (sum should
    equal sum(production)); friction: (R, C) >= 0. Returns flow T (R, C) whose
    row sums approach ``production`` and column sums approach ``attraction``.
    """
    production = np.asarray(production, dtype=float)
    attraction = np.asarray(attraction, dtype=float)
    friction = np.asarray(friction, dtype=float)

    a = np.ones(friction.shape[0])
    b = np.ones(friction.shape[1])
    for _ in range(int(max_iterations)):
        denom_r = friction @ (b * attraction)
        a = np.divide(1.0, denom_r, out=np.zeros_like(denom_r),
                      where=denom_r > 0)
        denom_c = friction.T @ (a * production)
        b = np.divide(1.0, denom_c, out=np.zeros_like(denom_c),
                      where=denom_c > 0)
        T = (a * production)[:, None] * friction * (b * attraction)[None, :]
        row_err = np.max(np.abs(T.sum(axis=1) - production))
        col_err = np.max(np.abs(T.sum(axis=0) - attraction))
        if max(row_err, col_err) < tolerance:
            break
    return T


def _draw_from_weights(weights, rng):
    """One column index per row, drawn proportional to unnormalized row weights."""
    totals = weights.sum(axis=1, keepdims=True)
    totals[totals == 0] = 1.0
    cdf = np.cumsum(weights / totals, axis=1)
    u = rng.random_sample(size=weights.shape[0])
    return (u[:, None] > cdf).sum(axis=1).clip(max=weights.shape[1] - 1)


def assign_by_capacity_gravity(pupil_xy, school_xy, capacity, slope,
                               max_radius_km, max_iterations, tolerance, rng):
    """Assign each pupil to a school by the capacity-constrained gravity model.

    Each pupil draws a school proportional to ``capacity * exp(slope * dist_km)``,
    which is the standard gravity attraction weight (capacity-scaled distance decay).
    ``balance_doubly_constrained`` is called to verify aggregate margin convergence
    but the per-pupil draw uses the unnormalized gravity weights directly, so that
    both distance preference and capacity attractiveness are reflected in individual
    choices.

    pupil_xy: (R, 2) metric coords; school_xy: (C, 2); capacity: (C,) > 0;
    slope: decay (1/km, negative); max_radius_km: candidate cutoff. Returns
    (choice (R,) school index, fallback (R,) bool = no candidate in radius).
    """
    d_km = cdist(pupil_xy, school_xy) / 1000.0
    friction = np.where(d_km <= max_radius_km, np.exp(slope * d_km), 0.0)

    fallback = friction.sum(axis=1) == 0
    if fallback.any():
        nearest = np.argmin(d_km[fallback], axis=1)
        rows = np.where(fallback)[0]
        friction[rows, nearest] = np.exp(slope * d_km[rows, nearest])

    # Gravity attraction weight: capacity scales each school's pull.
    capacity = np.asarray(capacity, dtype=float)
    weights = friction * capacity[None, :]

    choice = _draw_from_weights(weights, rng)
    return choice, fallback


def assign_by_radius(pupil_xy, school_xy, weight, radius_m, rng):
    """Capacity-weighted draw within ``radius_m`` (nearest fallback). Mirrors the
    existing eqasim_common education sampler; used for kindergarten + university."""
    from sklearn.neighbors import KDTree

    tree = KDTree(school_xy)
    nearest = tree.query(pupil_xy, return_distance=False).flatten()
    candidates = tree.query_radius(pupil_xy, radius_m)
    weight = np.asarray(weight, dtype=float)

    u = rng.random_sample(size=len(candidates))
    choice = np.empty(len(candidates), dtype=int)
    for k in range(len(candidates)):
        idx = candidates[k]
        if len(idx) == 0:
            choice[k] = nearest[k]
            continue
        w = weight[idx]
        cdf = np.cumsum(w) / np.sum(w)
        choice[k] = idx[np.count_nonzero(u[k] > cdf)]
    return choice
