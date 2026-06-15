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

# Per-bracket lower / upper EUR bounds, ordered like INCOME_BRACKET_CATEGORIES.
# Upper is NaN for the open-top bracket (high is None in INCOME_BRACKET_BOUNDS_EUR).
_BRACKET_LOW = np.array(
    [INCOME_BRACKET_BOUNDS_EUR[b][0] for b in INCOME_BRACKET_CATEGORIES], dtype=float
)
_BRACKET_HIGH = np.array(
    [np.nan if INCOME_BRACKET_BOUNDS_EUR[b][1] is None else INCOME_BRACKET_BOUNDS_EUR[b][1]
     for b in INCOME_BRACKET_CATEGORIES], dtype=float
)


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
    Households whose size-cell is absent fall back to a uniform pmf. fallback_count /
    fallback_rate are counted PER HOUSEHOLD (not per unique cell), so a single missing
    cell shared by many households is reported at its true household share -- the
    no-silent-fallback rule needs the real fraction, not the unique-cell count."""
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

    # Cache one (pmf, is_fallback) per unique cell; count fallback PER HOUSEHOLD below.
    cache: dict[tuple, tuple[np.ndarray, bool]] = {}
    n_fallback = 0
    mat = np.empty((n, n_brackets), dtype=float)
    for i in range(n):
        key = (sizes[i], statuses[i] if method == "combined" else None, rts[i])
        entry = cache.get(key)
        if entry is None:
            pmf = _cell_pmf(size_bl, size_rt, status_bl, status_rt, key[0], key[1], key[2], method)
            is_fallback = pmf is None
            if is_fallback:
                pmf = uniform
            entry = (pmf, is_fallback)
            cache[key] = entry
        pmf, is_fallback = entry
        if is_fallback:
            n_fallback += 1
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


def draw_brackets(pmf_rows: np.ndarray, uniforms: np.ndarray) -> np.ndarray:
    """Inverse-CDF bracket sample per row: one uniform per household."""
    cdf = np.cumsum(pmf_rows, axis=1)
    cdf[:, -1] = 1.0  # guard rounding
    idx = (uniforms[:, None] > cdf).sum(axis=1)
    return np.clip(idx, 0, pmf_rows.shape[1] - 1)


def draw_income_within_bracket(bracket_idx: np.ndarray, rng) -> np.ndarray:
    """Continuous EUR within each sampled bracket: uniform [max(low, INCOME_MIN_EUR), high)
    for closed brackets, low*(1 + Exp(INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION)) capped at
    INCOME_OPEN_TOP_MAX_EUR for the open top."""
    low = _BRACKET_LOW[bracket_idx]
    high = _BRACKET_HIGH[bracket_idx]
    eur = np.empty(len(bracket_idx), dtype=float)
    is_open = np.isnan(high)
    closed = ~is_open
    if closed.any():
        u = rng.random_sample(int(closed.sum()))
        low_draw = np.maximum(low[closed], INCOME_MIN_EUR)
        eur[closed] = low_draw + u * (high[closed] - low_draw)
    if is_open.any():
        exp_draw = rng.exponential(scale=INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION, size=int(is_open.sum()))
        eur[is_open] = np.minimum(low[is_open] * (1.0 + exp_draw), INCOME_OPEN_TOP_MAX_EUR)
    return np.maximum(eur, INCOME_MIN_EUR)


def apply_kreis_income_control(
    persons: pd.DataFrame,
    *,
    inkar_df: pd.DataFrame,
    kreis_stats_df: pd.DataFrame,
    income_tables: dict,
    enabled: bool,
    random_seed: int,
    method: str = DEFAULT_DRAW_METHOD,
    hhsize_correct: bool = True,
    kreis_col: str = "departement_id",
    hh_col: str = "household_id",
    size_col: str = "household_size",
    status_col: str = "economic_status",
    raumtyp_col: str = "RegioStaR7",
    income_col: str = "household_income_eur",
    class_col: str = "household_income",
) -> tuple[pd.DataFrame, dict]:
    """Draw real income + apply the max-entropy per-Kreis calibration.

    OFF (enabled=False): returns (persons, {}) UNCHANGED (byte-identical).
    ON: per household, build the base bracket pmf, solve per-Kreis lambda to the target,
    tilt + sample a bracket, draw the continuous EUR, broadcast to persons; re-derive
    household_income (label) + high_income from the EUR. economic_status is untouched."""
    if not enabled:
        return persons, {}

    e_b = bracket_expected_eur()
    # Household-level frame (income is a household quantity: one draw per household).
    hh = persons.sort_values(hh_col).groupby(hh_col, sort=True).first().reset_index()
    in_scope = sorted(hh[kreis_col].astype(str).unique())
    rf = build_kreis_income_targets(inkar_df, kreis_stats_df, in_scope,
                                    hhsize_correct=hhsize_correct)

    base_mat, pmf_diag = household_base_pmf_matrix(
        hh, income_tables, method=method,
        size_col=size_col, status_col=status_col, raumtyp_col=raumtyp_col)

    # Region-wide base mean (household-weighted over THIS synthetic population).
    # Targets are region_mean * rf_k with rf household-count-weighted mean-1, so the
    # INKAR between-Kreis relativity is imposed exactly. The region-wide realized mean
    # is preserved EXACTLY only when the synthetic per-Kreis household shares match the
    # census hh_count shares used to normalize rf; when they differ it can drift by the
    # share mismatch. This is intended: we anchor relativity, not INKAR's absolute level.
    region_mean = float((base_mat * e_b[None, :]).sum(axis=1).mean())

    rng = np.random.RandomState(int(random_seed) + INCOME_KC_RNG_OFFSET)
    n_hh = len(hh)
    tilted = np.empty_like(base_mat)
    kreis_lambda, kreis_clamped = {}, {}
    kreis_vals = hh[kreis_col].astype(str).to_numpy()
    for k in in_scope:
        mask = kreis_vals == k
        target = region_mean * rf.get(k, 1.0)
        lam, clamped = solve_kreis_lambda(base_mat[mask], e_b, target)
        tilted[mask] = tilt_pmf_rows(base_mat[mask], e_b, lam)
        kreis_lambda[k] = lam
        kreis_clamped[k] = clamped

    uniforms = rng.random_sample(n_hh)
    brackets = draw_brackets(tilted, uniforms)
    eur = np.round(draw_income_within_bracket(brackets, rng), 0)

    eur_by_hh = dict(zip(hh[hh_col].to_numpy(), eur))
    out = persons.copy()
    out[income_col] = out[hh_col].map(eur_by_hh).astype(float)

    class_midpoint = build_class_midpoint_eur()
    out[class_col] = income_class_from_eur(out[income_col].to_numpy(), class_midpoint)
    out["high_income"] = (out[income_col].fillna(0.0) >= HIGH_INCOME_THRESHOLD_EUR).astype(bool)

    realized = {}
    for k in in_scope:
        m = kreis_vals == k
        realized[k] = float(eur[m].mean()) if m.any() else float("nan")

    diag = {
        "region_mean": region_mean,
        "kreis_target_factor": rf,
        "kreis_lambda": kreis_lambda,
        "kreis_clamped": kreis_clamped,
        "kreis_realized_mean": realized,
        "pmf_fallback_rate": pmf_diag["fallback_rate"],
        "pmf_fallback_count": pmf_diag["fallback_count"],
        "method": method,
        "hhsize_correct": hhsize_correct,
    }
    logger.info("[income_kreis_control] applied: region_mean=%.0f, kreise=%d, "
                "pmf_fallback=%.1f%%, clamped=%s",
                region_mean, len(in_scope), 100 * pmf_diag["fallback_rate"],
                {k: v for k, v in kreis_clamped.items() if v})
    return out, diag
