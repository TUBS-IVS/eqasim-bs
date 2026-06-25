"""Pure fit helpers for the detour/circuity calibration: exp-decay curve fit and
the convergence-driven sample-size stop rule (with a minimum-samples floor)."""
from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit


def _model(d, c_inf, a, tau):
    return c_inf + a * np.exp(-d / tau)


def fit_circuity_curve(euclidean_km, routed_km):
    """Fit c(d)=c_inf+a*exp(-d/tau) to the per-pair ratio routed/euclidean.

    Returns {"c_inf","a","tau","r2","n"}. Bounds enforce c_inf>=1, a>=0, tau>0.
    """
    d = np.asarray(euclidean_km, dtype=float)
    r = np.asarray(routed_km, dtype=float)
    keep = d > 1e-6
    d, r = d[keep], r[keep]
    ratio = r / d
    popt, _ = curve_fit(
        _model, d, ratio,
        p0=(1.2, 0.5, 2.0),
        bounds=((1.0, 0.0, 1e-3), (3.0, 5.0, 100.0)),
        maxfev=20000,
    )
    pred = _model(d, *popt)
    ss_res = float(np.sum((ratio - pred) ** 2))
    ss_tot = float(np.sum((ratio - ratio.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"c_inf": float(popt[0]), "a": float(popt[1]), "tau": float(popt[2]),
            "r2": r2, "n": int(d.size)}


def params_changed_within(prev, cur, tol):
    """True if every fitted parameter changed by < tol (relative) vs prev."""
    for k in ("c_inf", "a", "tau"):
        denom = max(abs(prev[k]), 1e-9)
        if abs(cur[k] - prev[k]) / denom >= tol:
            return False
    return True


class ConvergenceTracker:
    """Stop when params are stable for `patience` consecutive rounds, but only
    once the sample has reached `min_samples` (floor guards premature stop)."""

    def __init__(self, min_samples, tol, patience):
        self.min_samples = int(min_samples)
        self.tol = float(tol)
        self.patience = int(patience)
        self._prev = None
        self._stable_streak = 0
        self.history: list[dict] = []

    def update(self, n_samples, params):
        self.history.append({"n": int(n_samples), **params})
        converged = False
        if self._prev is not None and params_changed_within(self._prev, params, self.tol):
            self._stable_streak += 1
        else:
            self._stable_streak = 0
        self._prev = dict(params)
        if n_samples >= self.min_samples and self._stable_streak >= self.patience:
            converged = True
        return converged
