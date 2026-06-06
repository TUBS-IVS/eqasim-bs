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
        # Convergence residuals are computed from matrix-vector products instead
        # of the full R x C flow matrix (which used to be rebuilt every
        # iteration just to sum its rows/columns). For the flow matrix
        # T = (a*production)[:,None] * friction * (b*attraction)[None,:] formed
        # from the *current* a and b:
        #   T.sum(axis=1) = (a*production) * (friction @ (b*attraction))
        #   T.sum(axis=0) = (b*attraction) * (friction.T @ (a*production))
        #                 = (b*attraction) * denom_c
        # The column sum reuses denom_c (built from the current a). The row sum
        # needs a fresh matvec with the *updated* b -- note this is NOT
        # (a*production)*denom_r, because denom_r used the previous b, whereas T
        # is built with the new b; using denom_r would understate the residual
        # and change the iteration count. These two expressions are exactly
        # T.sum(axis=1) / T.sum(axis=0) of the old per-iteration T, so the
        # tolerance check is bit-identical without materialising T.
        row_sum = (a * production) * (friction @ (b * attraction))
        col_sum = (b * attraction) * denom_c
        row_err = np.max(np.abs(row_sum - production))
        col_err = np.max(np.abs(col_sum - attraction))
        if max(row_err, col_err) < tolerance:
            break
    # Build the dense flow matrix once, after convergence (or max_iterations),
    # from the final scaling factors -- identical to the old final-iteration T.
    T = (a * production)[:, None] * friction * (b * attraction)[None, :]
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
    slope: decay (1/km, negative) -- scalar OR per-pupil array of length R so
        pupils in different RegioStaR-7 home areas decay at their own rate;
    max_radius_km: candidate cutoff. Returns
    (choice (R,) school index, fallback (R,) bool = no candidate in radius).
    """
    d_km = cdist(pupil_xy, school_xy) / 1000.0
    n_pupils = int(pupil_xy.shape[0])

    # slope may be a scalar (same decay for all pupils) or a per-pupil vector
    # (length n_pupils) so pupils in different RegioStaR-7 home areas decay at
    # their own rate. Normalise to a column vector for broadcasting; a wrong-
    # length vector raises here, which is the desired guard.
    slope_vec = np.broadcast_to(np.asarray(slope, dtype=float),
                                (n_pupils,)).reshape(-1, 1)

    friction = np.where(d_km <= max_radius_km, np.exp(slope_vec * d_km), 0.0)

    fallback = friction.sum(axis=1) == 0
    if fallback.any():
        rows = np.where(fallback)[0]
        nearest = np.argmin(d_km[rows], axis=1)
        friction[rows, nearest] = np.exp(
            slope_vec[rows, 0] * d_km[rows, nearest])

    # Scale attraction so that column targets sum to the pupil count, preserving
    # capacity proportions: each school's target = n_pupils * cap_j / sum(cap).
    capacity = np.asarray(capacity, dtype=float)
    attraction = n_pupils * capacity / capacity.sum()
    production = np.ones(n_pupils)

    T = balance_doubly_constrained(production, attraction, friction,
                                   max_iterations=max_iterations,
                                   tolerance=tolerance)
    # Traceability: report how well the balancing met both margins (a large
    # col residual means some schools could not be filled to capacity within
    # the radius -- a slope/radius calibration signal), plus the fallback count.
    row_err = float(np.max(np.abs(T.sum(axis=1) - production)))
    col_err = float(np.max(np.abs(T.sum(axis=0) - attraction)))
    print("[education_gravity] %d pupils, %d schools: Furness residual "
          "row=%.3g col=%.3g; %d radius-fallback"
          % (n_pupils, friction.shape[1], row_err, col_err, int(fallback.sum())))
    choice = _draw_from_rows(T, rng)
    return choice, fallback


def assign_by_decay(pupil_xy, school_xy, weight, slope, max_radius_km, rng):
    """Singly-constrained gravity draw: each pupil picks a destination with
    probability proportional to ``weight_j * exp(slope * d_ij)`` among
    destinations within ``max_radius_km`` (nearest-destination fallback when
    none are in range). Unlike the doubly-constrained school model there is NO
    capacity balancing -- used for universities, where far institutions' large
    enrollment is mostly non-resident and only the distance decay should govern
    how far the local tail commutes.
    """
    d_km = cdist(pupil_xy, school_xy) / 1000.0
    weight = np.asarray(weight, dtype=float)
    attract = weight[None, :] * np.exp(slope * d_km)
    attract = np.where(d_km <= max_radius_km, attract, 0.0)

    none_in_radius = attract.sum(axis=1) == 0
    if none_in_radius.any():
        rows = np.where(none_in_radius)[0]
        nearest = np.argmin(d_km[rows], axis=1)
        attract[rows, :] = 0.0
        attract[rows, nearest] = 1.0

    totals = attract.sum(axis=1, keepdims=True)
    cdf = np.cumsum(attract / totals, axis=1)
    u = rng.random_sample(size=attract.shape[0])
    return (u[:, None] > cdf).sum(axis=1).clip(max=attract.shape[1] - 1)


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
