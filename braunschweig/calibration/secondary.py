"""Tier 3A: per-purpose secondary chainsolvers scorer-weight calibration.

INFRASTRUCTURE ONLY -- not activated. Pinning/activating the weights is gated on
the deferred ON validation run actually showing a shop residual vs W12; until then
the weights stay at their current values. The chainsolvers re-run is injected by
the (server) caller as ``objective``; this module is the pure optimiser.
"""
from __future__ import annotations


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
