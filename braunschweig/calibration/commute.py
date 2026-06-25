"""Commute/work gravity friction distribution calibration (-> MiD P13).

Pure pieces: the Furness/Hyman multiplicative friction-factor update, the
sparse-(RS7, band)-cell shrinkage, and the end-of-calibration validation report.
The cache extraction and the per-iteration model re-run live in
scripts/calibrate_gravity_distribution.py.
"""
from __future__ import annotations

import numpy as np

from braunschweig.calibration.metrics import apply_detour, band_shares, emd_on_bands


def furness_update(factors, target_shares, model_shares, eps=1e-6):
    """One multiplicative friction-factor step toward the target distribution.

    ``f_b *= target_b / max(model_b, eps)``, then renormalised to mean 1 (the
    overall scale is irrelevant under the doubly-constrained balancing).
    """
    factors = np.asarray(factors, dtype=float)
    target = np.asarray(target_shares, dtype=float)
    model = np.asarray(model_shares, dtype=float)
    updated = factors * (target / np.maximum(model, eps))
    mean = updated.mean()
    return updated / mean if mean > 0 else updated


def shrink_sparse_factors(factors_by_rs7, counts_by_rs7, pooled, min_count,
                          weight=0.5):
    """Blend low-count (rs7, band) factors toward the pooled per-band factor.

    Returns (shrunk_factors_by_rs7, shrinkage_rate). A cell with
    ``count < min_count`` becomes ``(1-weight)*factor + weight*pooled``; dense cells
    are unchanged. The rate is logged by the caller (CLAUDE.md no-silent-fallback).
    """
    pooled = np.asarray(pooled, dtype=float)
    out = {}
    n_total = 0
    n_shrunk = 0
    for rs7, factors in factors_by_rs7.items():
        factors = np.asarray(factors, dtype=float)
        counts = np.asarray(counts_by_rs7[rs7], dtype=float)
        sparse = counts < min_count
        out[rs7] = np.where(sparse, (1.0 - weight) * factors + weight * pooled,
                            factors)
        n_total += factors.size
        n_shrunk += int(sparse.sum())
    rate = (n_shrunk / n_total) if n_total else 0.0
    return out, rate


def build_validation_report(realised_km_by_kreis, target_by_kreis,
                            jobs_by_gemeinde, svb_target_by_gemeinde):
    """Assemble the 'how well are the work locations hit' report.

    distance_fit: per Kreis the realised (detour-adjusted) band shares + EMD vs the
    committed P13 target. attraction_fill: per Gemeinde realised jobs vs the GENESIS
    SvB target (fill_ratio). The potential-respect block (mean chosen attraction vs
    a uniform baseline) is added by the caller, which has the candidate potentials.
    """
    distance_fit = {}
    for kreis, realised_km in realised_km_by_kreis.items():
        shares = band_shares(apply_detour(realised_km))
        target = np.asarray(target_by_kreis[kreis], dtype=float)
        distance_fit[kreis] = {
            "band_shares": shares.tolist(),
            "target": target.tolist(),
            "emd": emd_on_bands(shares, target),
        }
    attraction_fill = {}
    for gemeinde, jobs in jobs_by_gemeinde.items():
        target = float(svb_target_by_gemeinde.get(gemeinde, 0.0))
        attraction_fill[gemeinde] = {
            "assigned_jobs": float(jobs),
            "svb_target": target,
            "fill_ratio": (float(jobs) / target) if target > 0 else float("nan"),
        }
    return {"distance_fit": distance_fit, "attraction_fill": attraction_fill}
