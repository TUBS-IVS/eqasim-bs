"""Distance-dependent detour/circuity factor c(d) for the calibration corner.

c(d_km) = c_inf + a * exp(-d_km / tau)  (per network: car, walk).
pt is not fitted: c_pt(d) = c_car(d) * uplift (cited multiplier, Huang & Levinson 2015).

Both directions are exposed:
  euclidean_to_routed(d) = d * c(d)                 (model euclidean -> MiD routed axis)
  routed_to_euclidean(r) = brentq root of d*c(d)=r  (MiD routed target -> straight-line)

mode="constant" reproduces the legacy single detour factor (1.3) exactly, for
reproducibility / regression. mode="curve" (default) uses the fitted curve.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
from scipy.optimize import brentq

LEGACY_DETOUR_FACTOR = 1.3

DEFAULT_PARAMS_PATH = os.path.join(
    "eqasim-data", "data", "braunschweig", "calibration", "detour_circuity_params.csv"
)

_CACHE: dict[str, dict] = {}


def load_circuity_params(path: str = DEFAULT_PARAMS_PATH) -> dict:
    """Load the committed circuity params CSV into a nested dict (cached per path)."""
    if path in _CACHE:
        return _CACHE[path]
    df = pd.read_csv(path, comment="#")
    params: dict = {}
    for _, row in df.iterrows():
        net = str(row["network"])
        if net == "pt":
            params[net] = {"uplift": float(row["uplift"]), "base": str(row["base"])}
        else:
            params[net] = {
                "c_inf": float(row["c_inf"]),
                "a": float(row["a"]),
                "tau": float(row["tau_km"]),
            }
    for required in ("car", "walk", "pt"):
        if required not in params:
            raise ValueError(
                f"detour_circuity_params.csv missing required network '{required}' "
                f"(path={path})."
            )
    if params["car"]["c_inf"] < 1.0:
        raise ValueError("car c_inf must be >= 1.0 (circuity cannot shorten a trip).")
    _CACHE[path] = params
    return params


def _resolve(params, mode):
    if mode not in ("curve", "constant"):
        raise ValueError(f"mode must be 'curve' or 'constant', got {mode!r}.")
    if mode == "curve" and params is None:
        params = load_circuity_params()
    return params


def circuity_factor(euclidean_km, network="car", params=None, mode="curve"):
    """Return c(d) per element of euclidean_km for the given network."""
    d = np.asarray(euclidean_km, dtype=float)
    if mode == "constant":
        return np.full_like(d, LEGACY_DETOUR_FACTOR)
    params = _resolve(params, mode)
    if network == "pt":
        base = params["pt"]["base"]
        return circuity_factor(d, base, params=params, mode=mode) * params["pt"]["uplift"]
    p = params[network]
    return p["c_inf"] + p["a"] * np.exp(-d / p["tau"])


def euclidean_to_routed(euclidean_km, network="car", params=None, mode="curve"):
    """Map straight-line km to routed-equivalent km: routed = d * c(d)."""
    d = np.asarray(euclidean_km, dtype=float)
    return d * circuity_factor(d, network, params=params, mode=mode)


def routed_to_euclidean(routed_km, network="car", params=None, mode="curve"):
    """Inverse map routed km -> straight-line km (unique root of d*c(d)=routed)."""
    r = np.asarray(routed_km, dtype=float)
    if mode == "constant":
        return r / LEGACY_DETOUR_FACTOR
    params = _resolve(params, mode)

    def _invert_scalar(rv):
        if rv <= 0.0:
            return 0.0
        # d*c(d) is strictly increasing; bracket [rv / c(0), rv] (c>=1 => d<=rv).
        c0 = float(circuity_factor(0.0, network, params=params, mode=mode))
        lo, hi = rv / max(c0, 1.0), rv
        return float(brentq(lambda d: d * float(
            circuity_factor(d, network, params=params, mode=mode)) - rv, lo, hi))

    if r.ndim == 0:
        return np.asarray(_invert_scalar(float(r)))
    return np.array([_invert_scalar(float(v)) for v in r])
