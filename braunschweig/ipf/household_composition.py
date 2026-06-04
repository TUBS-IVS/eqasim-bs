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
from scipy.optimize import linear_sum_assignment

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


def assign_children_to_households(child_ages: np.ndarray, parent_ages: np.ndarray,
                                  child_slots: np.ndarray,
                                  target_gap: float = DEFAULT_PARENT_CHILD_GAP_YEARS):
    """Assign each child to a household child-slot minimising the total
    parent-child age-gap deviation ``Sum |child_age - (parent_age - target_gap)|``.

    ``child_slots[h]`` is the number of child slots in household ``h`` (with
    parent age ``parent_ages[h]``). Each household is expanded into its slots,
    a children x slots cost matrix is built, and ``scipy.optimize.
    linear_sum_assignment`` finds the globally optimal (minimum total
    deviation) child -> slot assignment. Requires ``sum(child_slots) >=
    len(child_ages)``. Returns an int array of length ``len(child_ages)`` giving
    the household index per child.
    """
    child_ages = np.asarray(child_ages, dtype=float)
    parent_ages = np.asarray(parent_ages, dtype=float)
    child_slots = np.asarray(child_slots, dtype=int)
    n_children = len(child_ages)
    if n_children == 0:
        return np.empty(0, dtype=int)

    slot_household = np.repeat(np.arange(len(parent_ages)), child_slots)
    slot_target = np.repeat(parent_ages - float(target_gap), child_slots)
    if len(slot_household) < n_children:
        raise ValueError(
            "assign_children_to_households: fewer child slots "
            f"({len(slot_household)}) than children ({n_children})"
        )

    # children x slots cost of placing child c in slot s.
    cost = np.abs(child_ages[:, None] - slot_target[None, :])
    row, col = linear_sum_assignment(cost)
    assign = np.empty(n_children, dtype=int)
    assign[row] = slot_household[col]
    return assign
