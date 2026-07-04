"""Fast IPU/raking proxy for the per-Kreis control fit (S2-A of the automated regional
control-selection design).

Because ``braunschweig.popsim.stage`` is sampling-independent (it integerizes the full ~558k
household population regardless of sampling_rate, ~1-2 h), the forward-selection control
optimizer cannot re-run the full popsim per iteration. This module provides the fast signal:
rake (iterative proportional fitting) the national donor seed's weights to a Kreis's control
marginals, then read the weighted per-Kreis distribution of ANY candidate attribute and its
SRMSE vs the committed MiD target. No integerization, no downstream — seconds, and
representative of ALL Kreise (it uses every Kreis's marginals).

The proxy measures the FRACTIONAL reweighted distribution; at Kreis level (thousands of
households) it tracks the integerized realised shares closely. The proxy MUST be validated
against a full popsim run before it is trusted for selection (S2 validation step); this module
is the pure computation, unit-tested on synthetic frames.
"""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd


def rake(
    seed: pd.DataFrame,
    marginals: Sequence[tuple],
    *,
    weight_col: str = "weight",
    max_iter: int = 200,
    tol: float = 1e-8,
) -> np.ndarray:
    """Iterative proportional fitting of per-row weights to a set of categorical marginals.

    ``marginals`` is a sequence of ``(column, {category: target_total})``. Each IPF pass scales
    the rows of every category so its weighted total matches the target; iterate until the
    largest relative adjustment falls below ``tol`` (or ``max_iter``). A target of 0 forces the
    category's weights to 0. Categories absent from the seed contribute nothing (their target
    cannot be met by this seed -- a coverage gap the caller should surface, not silently absorb).
    Returns the fitted weight array (does not mutate ``seed``).
    """
    w = seed[weight_col].to_numpy(dtype=float).copy()
    col_values = {col: seed[col].to_numpy() for col, _ in marginals}
    for _ in range(max_iter):
        max_rel = 0.0
        for col, targets in marginals:
            vals = col_values[col]
            for cat, target in targets.items():
                mask = vals == cat
                cur = w[mask].sum()
                if target <= 0:
                    if cur > 0:
                        w[mask] = 0.0
                    continue
                if cur > 0:
                    factor = target / cur
                    w[mask] *= factor
                    max_rel = max(max_rel, abs(factor - 1.0))
        if max_rel < tol:
            break
    return w


def weighted_category_shares(
    seed: pd.DataFrame, weights: np.ndarray, column: str, categories: Sequence[str]
) -> np.ndarray:
    """Weighted shares of ``column``'s ``categories`` (in the given order; sums to 1 if total>0)."""
    vals = seed[column].to_numpy()
    counts = np.array([weights[vals == c].sum() for c in categories], dtype=float)
    total = counts.sum()
    return counts / total if total > 0 else counts


def srmse(realised: np.ndarray, target: np.ndarray) -> float:
    """Standardised RMSE = RMSE(realised, target) / mean(target). For a k-share vector with
    mean 1/k this equals k*RMSE. Both inputs are share vectors (same order/length)."""
    realised = np.asarray(realised, dtype=float)
    target = np.asarray(target, dtype=float)
    mean_t = target.mean()
    if mean_t <= 0:
        return float("nan")
    return float(np.sqrt(np.mean((realised - target) ** 2)) / mean_t)


def kreis_fit_proxy(
    seed: pd.DataFrame,
    control_marginals_by_kreis: Mapping[str, Sequence[tuple]],
    candidate_column: str,
    candidate_categories: Sequence[str],
    candidate_target_shares_by_kreis: Mapping[str, np.ndarray],
    *,
    weight_col: str = "weight",
) -> dict:
    """Per-Kreis SRMSE of a CANDIDATE attribute after raking the seed to the CURRENT controls.

    For each Kreis: rake the shared national ``seed`` to that Kreis's ``control_marginals``
    (the attributes already controlled), then measure the reweighted distribution of
    ``candidate_column`` vs its per-Kreis target shares. A large SRMSE means the current controls
    do NOT reproduce the candidate per Kreis -> the candidate is a good forward-selection pick.
    Returns ``{ars5: srmse}``. Kreise without a candidate target are skipped.
    """
    out = {}
    for ars5, marginals in control_marginals_by_kreis.items():
        if ars5 not in candidate_target_shares_by_kreis:
            continue
        w = rake(seed, marginals, weight_col=weight_col)
        realised = weighted_category_shares(seed, w, candidate_column, candidate_categories)
        out[str(ars5)] = srmse(realised, candidate_target_shares_by_kreis[ars5])
    return out
