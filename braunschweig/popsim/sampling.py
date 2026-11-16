"""Deterministic weight-proportional choice from a candidate pool.

Used wherever the population pipeline must pick ONE donor from several equally
eligible candidates: the choice is drawn proportional to the MiD survey weight
(``H_GEW`` / ``P_GEW``) so more representative respondents are picked more often.

:func:`weighted_choice` is the one-shot form. A caller that draws many times from
the SAME candidate set (``weekend_plan_match.PreparedPersonPool``) splits it into
:func:`prepare_weighted_choice` (canonical sort and cumulative weights, once) and
:func:`draw_prepared_choice` (one ``rng.uniform`` per draw); the split form returns
bit-identical items and consumes the rng identically, because ``weighted_choice``
is literally the composition of the two.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class PreparedWeightedChoice:
    """Candidate set of :func:`weighted_choice`, canonically sorted, ready for repeated draws.

    ``items`` are sorted by item; ``cumulative_weights`` is the running sum of the
    sanitised weights (NaN/<=0 -> 0) in that order and ``total_weight`` their sum.
    ``uniform_fallback`` is True when every weight was invalid: the weights were then
    replaced by ones, and every draw logs the fallback warning again.
    """
    items: tuple
    cumulative_weights: np.ndarray
    total_weight: float
    uniform_fallback: bool


def prepare_weighted_choice(items, weights) -> PreparedWeightedChoice:
    """Sort the (item, weight) pairs by item and precompute the cumulative weights.

    The sort is stable, so duplicate items keep their input order (their weights are
    accumulated in that order, exactly as before the split).
    """
    pairs = sorted(zip(list(items), np.asarray(weights, dtype=float)), key=lambda t: t[0])
    its = [p[0] for p in pairs]
    w = np.array([p[1] for p in pairs], dtype=float)
    w = np.where(np.isfinite(w) & (w > 0.0), w, 0.0)
    total = w.sum()
    uniform_fallback = bool(total <= 0.0)
    if uniform_fallback:
        w = np.ones(len(its), dtype=float)
        total = w.sum()
    cumulative = np.cumsum(w)
    cumulative.flags.writeable = False  # shared by every later draw from this candidate set
    return PreparedWeightedChoice(items=tuple(its), cumulative_weights=cumulative,
                                  total_weight=total, uniform_fallback=uniform_fallback)


def draw_prepared_choice(prepared: PreparedWeightedChoice, *, rng):
    """Draw one item of ``prepared`` with exactly one ``rng.uniform`` call.

    Logs the uniform-fallback warning on EVERY draw from an all-invalid candidate set
    (no silent fallback; one record per draw, as :func:`weighted_choice` always did).
    """
    items = prepared.items
    if prepared.uniform_fallback:
        logger.warning(
            "[sampling.weighted_choice] all %d candidate weights are NaN/<=0; "
            "falling back to a uniform draw.", len(items))
    r = rng.uniform(0.0, prepared.total_weight)
    idx = int(np.searchsorted(prepared.cumulative_weights, r, side="right"))
    idx = min(idx, len(items) - 1)  # guard the float upper edge
    return items[idx]


def weighted_choice(items, weights, *, rng):
    """Pick one of ``items`` with probability proportional to its weight.

    The (item, weight) pairs are sorted by ``item`` into a canonical order before
    drawing, so the result is deterministic for a given ``rng`` state and input
    set, independent of the caller's ordering. NaN/<=0 weights are treated as 0
    (excluded); if every weight is invalid (all NaN/<=0 or sum 0) the draw falls
    back to UNIFORM over the sorted items and a warning is logged (no silent
    fallback). ``items`` may be ints or tuples (anything sortable).
    """
    return draw_prepared_choice(prepare_weighted_choice(items, weights), rng=rng)
