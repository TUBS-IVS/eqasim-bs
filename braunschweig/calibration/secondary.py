"""Tier 3A: per-purpose secondary chainsolvers scorer-weight calibration.

INFRASTRUCTURE ONLY -- not activated. Pinning/activating the weights is gated on
the deferred ON validation run actually showing a shop residual vs W12; until then
the weights stay at their current values. The chainsolvers re-run is injected by
the (server) caller as ``objective``; this module is the pure optimiser.
"""
from __future__ import annotations

from braunschweig.calibration.metrics import emd_on_bands


def build_secondary_loss(per_purpose_realised_fn, w12_targets, *,
                         concentration_fn=None, conc_weight=0.0):
    """Return loss(weights) = sum_purpose EMD(realised_p, target_p) [+ conc_weight*concentration].

    per_purpose_realised_fn(weights) -> {purpose: realised_band_shares}; w12_targets
    is {purpose: target_band_shares}. Pure; deterministic given the realised fn.

    Parameters
    ----------
    per_purpose_realised_fn : callable
        weights -> {purpose: array-like band shares}. Called on each loss evaluation.
    w12_targets : dict
        {purpose: array-like band shares}. Must include the same purposes
        (shop, leisure, other) as used by the calibrator.
    concentration_fn : callable, optional
        weights -> float concentration penalty (e.g. mean excess_tv / top-1 share).
        Only applied when conc_weight > 0.
    conc_weight : float
        Weight on the concentration penalty term. Default 0.0 (no penalty).
    """
    def loss(weights):
        realised = per_purpose_realised_fn(weights)
        total = 0.0
        for purpose, target in w12_targets.items():
            if purpose in realised:
                total += emd_on_bands(realised[purpose], target)
        if concentration_fn is not None and conc_weight:
            total += conc_weight * float(concentration_fn(weights))
        return total
    return loss


def coordinate_descent(objective, init, grid, max_rounds=10):
    """Minimise ``objective(weights)`` by axis-wise grid coordinate descent.

    weights/init/grid are dicts keyed by parameter name. Returns
    {"weights","loss","history"}. Deterministic; ties keep the lower grid value.
    """
    current = dict(init)
    best_loss = objective(current)
    history = [{"weights": dict(current), "loss": best_loss}]
    for _ in range(max_rounds):
        improved = False
        for key, values in grid.items():
            for v in values:
                trial = dict(current)
                trial[key] = v
                loss = objective(trial)
                if loss < best_loss - 1e-12:
                    best_loss, current, improved = loss, trial, True
                    history.append({"weights": dict(current), "loss": loss})
        if not improved:
            break
    return {"weights": current, "loss": best_loss, "history": history}
