"""Kreis-Income-Control: real MiD net-income draw + max-entropy per-Kreis calibration.

Replaces the popsim income fudge (class_midpoint x INKAR_scale[Kreis]) with a real
continuous draw from P(bracket | hh_size, economic_status, raumtyp) (reusing the
data/mid income builders), reshaped per Kreis by an exponential tilt q*exp(lambda*e_b)
whose lambda is solved so the Kreis mean equals the construct-corrected INKAR target.

economic_status is left untouched (it is an INPUT: income is drawn conditioned on it);
household_income (label) + high_income are re-derived from the drawn EUR. The enriched/
IPF path is NOT touched. Pure module: no file I/O.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.data.mid.income_by_size import (
    INCOME_BRACKET_CATEGORIES,
    INCOME_BRACKET_BOUNDS_EUR,
    RS7_TO_RAUMTYP_KEY,
    SIZE_CATEGORIES,
    income_bracket_probabilities,
)
from braunschweig.data.mid.income_by_status import (
    income_bracket_probabilities_by_status,
    overall_bracket_pmf,
    combine_size_status_bracket_pmf,
)
from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR
# The popsim income-class vocabulary (label) + its EUR midpoints, so the re-derived
# household_income label stays in the SAME vocabulary the donor mappers use. No
# circular import: attributes imports only reference_tables / ipf.attributed / popsim.missing.
from braunschweig.popsim.attributes import (
    INCOME_CLASS_BY_GROUP,
    INCOME_GROUP_MIDPOINT_EUR,
)

logger = logging.getLogger(__name__)

# Constants mirrored from the enriched distribution draw (kept local; enriched untouched).
INCOME_MIN_EUR = 100.0
INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION = 0.4
INCOME_OPEN_TOP_MAX_EUR = 18000.0

# Dedicated RNG offset so this draw is reproducible and independent of other streams.
INCOME_KC_RNG_OFFSET = 91237
DEFAULT_DRAW_METHOD = "combined"


def bracket_expected_eur() -> np.ndarray:
    """Per-bracket expected income e_b over INCOME_BRACKET_CATEGORIES (the mean the
    within-bracket draw realizes): closed -> (max(low, INCOME_MIN_EUR)+high)/2,
    open-top -> low*(1 + INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION). Used as the support
    points of the max-ent tilt."""
    e = np.empty(len(INCOME_BRACKET_CATEGORIES), dtype=float)
    for i, b in enumerate(INCOME_BRACKET_CATEGORIES):
        low, high = INCOME_BRACKET_BOUNDS_EUR[b]
        if high is None:
            e[i] = low * (1.0 + INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION)
        else:
            e[i] = (max(low, INCOME_MIN_EUR) + high) / 2.0
    return e


def build_class_midpoint_eur() -> dict[str, float]:
    """Label -> midpoint EUR for the popsim income-class vocabulary, built from the
    attribute mappers so the re-derived label stays in the SAME vocabulary popsim uses.
    Key order is not significant; income_class_from_eur sorts by midpoint internally."""
    return {
        INCOME_CLASS_BY_GROUP[code]: float(INCOME_GROUP_MIDPOINT_EUR[code])
        for code in INCOME_CLASS_BY_GROUP
    }


def income_class_from_eur(eur_values, class_midpoint_eur: dict[str, float]) -> np.ndarray:
    """Nearest-midpoint classifier (1-D, monotone). Mirrors enriched._income_class_from_eur
    but kept local so enriched is not imported/touched."""
    labels = list(class_midpoint_eur.keys())
    midpoints = np.array([class_midpoint_eur[k] for k in labels], dtype=float)
    order = np.argsort(midpoints)
    labels_sorted = [labels[i] for i in order]
    mids_sorted = midpoints[order]
    edges = (mids_sorted[:-1] + mids_sorted[1:]) / 2.0
    idx = np.searchsorted(edges, np.asarray(eur_values, dtype=float), side="right")
    return np.asarray(labels_sorted, dtype=object)[idx]


def build_kreis_income_targets(
    inkar_df: pd.DataFrame,
    kreis_stats_df: pd.DataFrame,
    in_scope_ars5,
    *,
    hhsize_correct: bool = True,
) -> dict[str, float]:
    """Per-Kreis relative income factor rf_k, household-count-weighted, normalized to
    mean 1 over the in-scope Kreise.

    rf_k_raw = INKAR scale[k] * (mean_size[k] if hhsize_correct else 1)
    rf_k     = rf_k_raw / weighted_mean_k(rf_k_raw), weight = hh_count[k].

    Region-relative on purpose: imposing only the BETWEEN-Kreis relativity preserves
    the region-wide income level set by the MiD draw. Single Kreis -> rf_k == 1 (no-op).
    """
    scope = [str(a) for a in in_scope_ars5]
    scale = dict(zip(inkar_df["ars5"].astype(str), inkar_df["scale"].astype(float)))
    mean_size = dict(zip(kreis_stats_df["ars5"].astype(str),
                         kreis_stats_df["mean_size"].astype(float)))
    hh_count = dict(zip(kreis_stats_df["ars5"].astype(str),
                        kreis_stats_df["hh_count"].astype(float)))

    raw, weight = {}, {}
    for k in scope:
        s = scale.get(k, 1.0)
        sz = mean_size.get(k, 1.0) if hhsize_correct else 1.0
        raw[k] = s * sz
        weight[k] = hh_count.get(k, 1.0)

    wsum = sum(weight[k] for k in scope) or 1.0
    wmean = sum(raw[k] * weight[k] for k in scope) / wsum
    if wmean <= 0:
        logger.warning("[income_kreis_control] degenerate target mean <= 0; rf=1 for all.")
        return {k: 1.0 for k in scope}
    return {k: raw[k] / wmean for k in scope}


def _size_key(n) -> str:
    """Map an integer household size onto the income_by_size SIZE_CATEGORIES bins."""
    try:
        k = int(n)
    except (TypeError, ValueError):
        return ""
    return "5+" if k >= 5 else str(k)


def _raumtyp_key(rs7):
    if rs7 is None or (isinstance(rs7, float) and np.isnan(rs7)):
        return None
    try:
        return RS7_TO_RAUMTYP_KEY.get(int(rs7))
    except (TypeError, ValueError):
        return None


def household_base_pmf_matrix(
    households_df: pd.DataFrame,
    income_tables: dict,
    *,
    method: str = DEFAULT_DRAW_METHOD,
    size_col: str = "household_size",
    status_col: str = "economic_status",
    raumtyp_col: str = "RegioStaR7",
) -> tuple[np.ndarray, dict]:
    """Per-household base bracket pmf matrix (n_hh x 10), ordered like
    INCOME_BRACKET_CATEGORIES. Builds one pmf per UNIQUE (size, status, raumtyp) cell
    via combine_size_status_bracket_pmf and maps it back to households.

    method='combined' uses size x status; method='size_only' uses size alone.
    Households whose size-cell is absent fall back to a uniform pmf (counted)."""
    n = len(households_df)
    n_brackets = len(INCOME_BRACKET_CATEGORIES)
    uniform = np.full(n_brackets, 1.0 / n_brackets)
    size_bl = income_tables["size_bl"]
    size_rt = income_tables["size_rt"]
    status_bl = income_tables["status_bl"]
    status_rt = income_tables["status_rt"]

    sizes = households_df[size_col].map(_size_key).to_numpy()
    statuses = (households_df[status_col].astype(str).to_numpy()
                if status_col in households_df.columns else np.array([None] * n))
    rts = (households_df[raumtyp_col].map(_raumtyp_key).to_numpy()
           if raumtyp_col in households_df.columns else np.array([None] * n))

    cache: dict[tuple, np.ndarray] = {}
    n_fallback = 0
    mat = np.empty((n, n_brackets), dtype=float)
    for i in range(n):
        key = (sizes[i], statuses[i] if method == "combined" else None, rts[i])
        pmf = cache.get(key)
        if pmf is None:
            pmf = _cell_pmf(size_bl, size_rt, status_bl, status_rt, key[0], key[1], key[2], method)
            if pmf is None:
                pmf = uniform
                n_fallback += 1
            cache[key] = pmf
        mat[i] = pmf
    return mat, {"fallback_rate": (n_fallback / n) if n else 0.0,
                 "fallback_count": n_fallback}


def _cell_pmf(size_bl, size_rt, status_bl, status_rt, size_key, status_key, rt_key, method):
    p_size = income_bracket_probabilities(size_bl, size_rt, size_key, rt_key)
    if p_size is None:
        return None
    if method != "combined" or status_key is None:
        return p_size
    p_status = income_bracket_probabilities_by_status(status_bl, status_rt, status_key, rt_key)
    p_overall = overall_bracket_pmf(status_bl, status_rt, rt_key)
    if p_status is None or p_overall is None:
        return p_size
    return combine_size_status_bracket_pmf(p_size, p_status, p_overall)


def tilt_pmf_rows(pmf_rows: np.ndarray, e_b: np.ndarray, lam: float) -> np.ndarray:
    """Exponential tilt q*exp(lam*e_b) renormalized per row. Numerically stabilized by
    subtracting max(lam*e_b) before exp (cancels in the renormalization)."""
    z = lam * e_b
    z = z - z.max()
    w = pmf_rows * np.exp(z)[None, :]
    row = w.sum(axis=1, keepdims=True)
    row = np.where(row > 0, row, 1.0)
    return w / row


def _tilted_mean(pmf_rows, e_b, lam) -> float:
    tilted = tilt_pmf_rows(pmf_rows, e_b, lam)
    return float((tilted * e_b[None, :]).sum(axis=1).mean())


def solve_kreis_lambda(
    pmf_rows: np.ndarray,
    e_b: np.ndarray,
    target_mean: float,
    *,
    max_expand: int = 80,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> tuple[float, bool]:
    """Solve lambda so the household-averaged tilted mean equals target_mean. The mean
    is strictly increasing in lambda, bounded by [min(e_b), max(e_b)]; an unreachable
    target is clamped just inside the support and the clamp is flagged + logged."""
    emin, emax = float(e_b.min()), float(e_b.max())
    span = emax - emin
    lo_t = emin + 1e-9 * span
    hi_t = emax - 1e-9 * span
    target = min(max(target_mean, lo_t), hi_t)
    clamped = abs(target - target_mean) > 1e-9 * max(abs(target_mean), 1.0)

    base = _tilted_mean(pmf_rows, e_b, 0.0)
    if abs(base - target) <= tol * max(abs(target), 1.0):
        return 0.0, clamped

    if base < target:
        lo, hi = 0.0, 1e-9
        for _ in range(max_expand):
            if _tilted_mean(pmf_rows, e_b, hi) >= target:
                break
            lo, hi = hi, hi * 2.0
        else:
            logger.warning("[income_kreis_control] lambda upper bracket not found; clamping.")
            return hi, True
    else:
        lo, hi = -1e-9, 0.0
        for _ in range(max_expand):
            if _tilted_mean(pmf_rows, e_b, lo) <= target:
                break
            lo, hi = lo * 2.0, lo
        else:
            logger.warning("[income_kreis_control] lambda lower bracket not found; clamping.")
            return lo, True

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        m = _tilted_mean(pmf_rows, e_b, mid)
        if abs(m - target) <= tol * max(abs(target), 1.0):
            return mid, clamped
        if m < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi), clamped
