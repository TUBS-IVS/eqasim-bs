"""Deterministic weight-proportional choice from a candidate pool.

Used wherever the population pipeline must pick ONE donor from several equally
eligible candidates: the choice is drawn proportional to the MiD survey weight
(``H_GEW`` / ``P_GEW``) so more representative respondents are picked more often.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def weighted_choice(items, weights, *, rng):
    """Pick one of ``items`` with probability proportional to its weight.

    The (item, weight) pairs are sorted by ``item`` into a canonical order before
    drawing, so the result is deterministic for a given ``rng`` state and input
    set, independent of the caller's ordering. NaN/<=0 weights are treated as 0
    (excluded); if every weight is invalid (all NaN/<=0 or sum 0) the draw falls
    back to UNIFORM over the sorted items and a warning is logged (no silent
    fallback). ``items`` may be ints or tuples (anything sortable).
    """
    pairs = sorted(zip(list(items), np.asarray(weights, dtype=float)), key=lambda t: t[0])
    its = [p[0] for p in pairs]
    w = np.array([p[1] for p in pairs], dtype=float)
    w = np.where(np.isfinite(w) & (w > 0.0), w, 0.0)
    total = w.sum()
    if total <= 0.0:
        logger.warning(
            "[sampling.weighted_choice] all %d candidate weights are NaN/<=0; "
            "falling back to a uniform draw.", len(its))
        w = np.ones(len(its), dtype=float)
        total = w.sum()
    r = rng.uniform(0.0, total)
    idx = int(np.searchsorted(np.cumsum(w), r, side="right"))
    idx = min(idx, len(its) - 1)  # guard the float upper edge
    return its[idx]
