"""Optimisation helpers for age-aware household composition (#3b).

Given a bucket of persons (ages) and the per-household ``hh_type`` composition
requirements, assign persons to households minimising age-implausibility
(within-couple age gap + parent-child age-gap deviation) subject to the hard
adult/child composition constraints. Pure numpy/scipy; deterministic (stable
sorts, no RNG inside).

The parent-child target gap default is Destatis-aligned: the mean age of the
mother at birth (Statistisches Bundesamt, GENESIS 12612) is ~30-31.5 years and
equals the mother-child age gap; ~31 years is a defensible blended (mother/
father) default. It is exposed as a config value so it can be refined to the
Niedersachsen figure.
"""
from __future__ import annotations

import numpy as np

# Destatis-aligned default parent-child age gap in years (GENESIS 12612, mean
# age of mother at birth ~30-31.5; blended mother/father ~31).
DEFAULT_PARENT_CHILD_GAP_YEARS: float = 31.0


def split_pools(ages: np.ndarray, min_adult_age: int = 18):
    """Return ``(adult_indices, child_indices)`` into ``ages``."""
    ages = np.asarray(ages)
    adults = np.nonzero(ages >= min_adult_age)[0]
    children = np.nonzero(ages < min_adult_age)[0]
    return adults, children


def optimal_adult_pairs(ages: np.ndarray, n_pairs: int,
                        return_leftover: bool = False):
    """Pick ``2 * n_pairs`` adults and pair them minimising the total within-pair
    age gap.

    Sorting the adults by age and pairing adjacent ranks is optimal for the
    sum of within-pair absolute age differences (a standard result: any pairing
    that crosses sorted order can be uncrossed without increasing the total
    gap). ``O(n log n)``.

    Returns a list of ``(idx_a, idx_b)`` index pairs into ``ages``; if
    ``return_leftover`` is set, also the sorted array of unused indices.
    """
    ages = np.asarray(ages)
    order = np.argsort(ages, kind="mergesort")  # stable -> deterministic ties
    take = order[: 2 * n_pairs]
    pairs = [(int(take[2 * k]), int(take[2 * k + 1])) for k in range(n_pairs)]
    if return_leftover:
        leftover = np.sort(order[2 * n_pairs:])
        return pairs, leftover
    return pairs
