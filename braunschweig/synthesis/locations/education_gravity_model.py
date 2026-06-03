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


def _draw_from_rows(T, rng):
    """One column index per row, drawn proportional to that row of the balanced
    flow matrix T (doubly-constrained probabilities)."""
    totals = T.sum(axis=1, keepdims=True)
    totals[totals == 0] = 1.0
    cdf = np.cumsum(T / totals, axis=1)
    u = rng.random_sample(size=T.shape[0])
    return (u[:, None] > cdf).sum(axis=1).clip(max=T.shape[1] - 1)


def assign_by_capacity_gravity(pupil_xy, school_xy, capacity, slope,
                               max_radius_km, max_iterations, tolerance, rng):
    """Assign each pupil to a school by the doubly-constrained gravity model.

    Builds a friction matrix from distance-decay, scales school attraction
    targets to the pupil count (so column sums match capacity proportions),
    runs Furness balancing (``balance_doubly_constrained``) to satisfy both
    the per-pupil row constraint (everyone is placed exactly once) and the
    per-school column constraint (schools fill in proportion to their real
    Schuelerplaetze), then draws each pupil's school proportional to the
    balanced flow row. This is the doubly-constrained guarantee that prevents
    a tiny-capacity school from absorbing all nearby pupils ("no 2-vs-10000").

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

    # Scale attraction so that column targets sum to the pupil count, preserving
    # capacity proportions: each school's target = n_pupils * cap_j / sum(cap).
    capacity = np.asarray(capacity, dtype=float)
    n_pupils = int(pupil_xy.shape[0])
    attraction = n_pupils * capacity / capacity.sum()
    production = np.ones(n_pupils)

    T = balance_doubly_constrained(production, attraction, friction,
                                   max_iterations=max_iterations,
                                   tolerance=tolerance)
    choice = _draw_from_rows(T, rng)
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
