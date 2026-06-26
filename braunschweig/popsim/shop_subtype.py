"""Daily vs non-daily shopping subtype: conditional probability from MiD W_ZWD,
imputed onto synthetic shop legs. CATI/CAWI detail is estimated on labelled legs
(no synthetic leg is dropped); see reference-mid-detail-purposes."""
from __future__ import annotations

import numpy as np

# MiD W_ZWD codes for shopping sub-purpose detail (Tabelle: Wegezweck-Detail)
SHOP_DAILY_W_ZWD = {501}                         # Einkauf taeglich
SHOP_NONDAILY_W_ZWD = {502, 503, 504, 505}       # Einkauf nicht-taeglich (clothing, electronics, etc.)
# Sentinels: PAPI interview (2202), child reported (4402), no-info codes (7704, 7705, 599, 999)
# These carry no valid label and must be excluded from estimation.
SHOP_DETAIL_MISSING = {2202, 4402, 7704, 7705, 599, 999}

# Travel-time band edges in seconds: [0, 300, 600, 1200, inf)
TT_BANDS = [0.0, 300.0, 600.0, 1200.0, float("inf")]


def tt_band(travel_time: float) -> int:
    """Return the 0-based travel-time band index for a travel time in seconds.

    Bands: [0, 300) -> 0, [300, 600) -> 1, [600, 1200) -> 2, [1200, inf) -> 3.
    Uses np.digitize on the interior edges TT_BANDS[1:-1].
    """
    return int(np.digitize(float(travel_time), TT_BANDS[1:-1]))


def estimate_daily_probability(mid_wege, *, min_obs: int = 30) -> dict:
    """P(daily | mode, tt_band), W_GEW-weighted, over labelled shop legs only.

    Parameters
    ----------
    mid_wege : DataFrame
        MiD Wege table. Required columns: W_ZWECK, mode, travel_time, W_ZWD, W_GEW.
        Only rows with W_ZWECK == 4 (shopping) and W_ZWD in the labelled sets
        (SHOP_DAILY_W_ZWD | SHOP_NONDAILY_W_ZWD) are used; sentinel rows are
        excluded.
    min_obs : int
        Minimum row count for a (mode, tt_band) cell to receive its own estimate.
        Cells below this threshold are not returned; the caller uses the marginal
        (stored under key ("__marginal__", -1)).

    Returns
    -------
    dict[(mode, int), float]
        Keys are (mode_string, band_int); "(__marginal__, -1)" holds the overall
        W_GEW-weighted daily share across all labelled legs (used as fallback).
    """
    # Filter to shopping legs only.
    df = mid_wege[mid_wege["W_ZWECK"] == 4].copy()

    # Keep only legs with a labelled W_ZWD; sentinels are excluded here.
    labelled = df[df["W_ZWD"].isin(SHOP_DAILY_W_ZWD | SHOP_NONDAILY_W_ZWD)].copy()

    labelled["is_daily"] = labelled["W_ZWD"].isin(SHOP_DAILY_W_ZWD)
    labelled["band"] = labelled["travel_time"].map(tt_band)

    w = labelled["W_GEW"].astype(float)

    # Marginal W_GEW-weighted daily share (fallback for sparse cells).
    marginal = float((labelled["is_daily"] * w).sum() / w.sum())
    prob: dict = {("__marginal__", -1): marginal}

    for (mode, band), grp in labelled.groupby(["mode", "band"]):
        gw = grp["W_GEW"].astype(float)
        if gw.size >= min_obs:
            prob[(mode, int(band))] = float((grp["is_daily"] * gw).sum() / gw.sum())

    return prob


def impute_subtype(modes, tt_values, prob: dict, marginal: float, rng) -> "np.ndarray":
    """Per-leg daily flag ~ Bernoulli(P(daily | mode, tt_band)); marginal where cell absent.

    Parameters
    ----------
    modes : array-like of str
        Mode label per synthetic leg.
    tt_values : array-like of float
        Travel time in seconds per synthetic leg.
    prob : dict
        Output of estimate_daily_probability; keys are (mode, band) pairs.
    marginal : float
        Fallback probability used when the (mode, band) cell is absent from prob.
        Typically prob[("__marginal__", -1)].
    rng : numpy RandomState
        Seeded random state for reproducibility.

    Returns
    -------
    np.ndarray of bool
        True = daily shopping leg, False = non-daily.
    """
    modes = np.asarray(modes)
    tt_values = np.asarray(tt_values)
    p = np.array([prob.get((m, tt_band(t)), marginal) for m, t in zip(modes, tt_values)])
    return rng.random_sample(len(p)) < p
