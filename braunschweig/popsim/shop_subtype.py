"""Daily vs non-daily shopping subtype: conditional probability from MiD W_ZWD,
imputed onto synthetic shop legs. CATI/CAWI detail is estimated on labelled legs
(no synthetic leg is dropped); see reference-mid-detail-purposes."""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

# MiD W_ZWD codes for shopping sub-purpose detail (Tabelle: Wegezweck-Detail)
SHOP_DAILY_W_ZWD = {501}                         # Einkauf taeglich
SHOP_NONDAILY_W_ZWD = {502, 503, 504, 505}       # Einkauf nicht-taeglich (clothing, electronics, etc.)
# Sentinels: PAPI interview (2202), child reported (4402), no-info codes (7704, 7705, 599, 999)
# These carry no valid label and must be excluded from estimation.
SHOP_DETAIL_MISSING = {2202, 4402, 7704, 7705, 599, 999}

# Travel-time band edges in seconds: [0, 300, 600, 1200, inf)
TT_BANDS = [0.0, 300.0, 600.0, 1200.0, float("inf")]

# Warn threshold for the share of labelled estimation legs left out because their duration
# is unknown (coded clock time AND invalid wegmin_imp1, see
# braunschweig.popsim.trips.mid_travel_time_seconds). Heuristic, not a measured value: the
# same bound the distance stage applies to the same quantity (trips.CODED_TIME_DROP_WARN_RATE);
# above it the duration source is more likely broken than the legs unrecoverable.
UNKNOWN_DURATION_WARN_SHARE = 0.02


def tt_band(travel_time: float) -> int:
    """Return the 0-based travel-time band index for a travel time in seconds.

    Bands: [0, 300) -> 0, [300, 600) -> 1, [600, 1200) -> 2, [1200, inf) -> 3.
    Uses np.digitize on the interior edges TT_BANDS[1:-1]. A non-finite travel time raises:
    np.digitize would sort NaN into the longest band and so count a leg of unknown duration
    as a long one (audit B1).
    """
    value = float(travel_time)
    if not np.isfinite(value):
        raise ValueError(f"tt_band needs a finite travel time in seconds, got {travel_time!r}")
    return int(np.digitize(value, TT_BANDS[1:-1]))


def drop_legs_of_unknown_duration(labelled, *, label: str):
    """Return the labelled estimation legs that have a finite ``travel_time``.

    Legs without a duration cannot be banded; they are left out of the estimate instead of
    being counted in one band. The kept/dropped counts are logged, a dropped share above
    ``UNKNOWN_DURATION_WARN_SHARE`` is a warning, and an empty result raises: an estimate
    from zero legs would silently become a 0/0 marginal.
    """
    finite = np.isfinite(labelled["travel_time"].astype(float).to_numpy())
    n_total = len(labelled)
    n_dropped = int((~finite).sum())
    share = n_dropped / n_total if n_total else 0.0
    log = logger.warning if share > UNKNOWN_DURATION_WARN_SHARE else logger.info
    log("[%s] labelled legs with a known duration %d/%d; left out (unknown duration) %d (%.1f%%)",
        label, n_total - n_dropped, n_total, n_dropped, 100.0 * share)
    if n_dropped == n_total:
        raise ValueError(
            f"[{label}] none of the {n_total} labelled legs has a known travel time; "
            "cannot estimate probabilities from zero legs.")
    return labelled[finite]


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

    labelled = drop_legs_of_unknown_duration(labelled, label="shop_subtype").copy()
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
