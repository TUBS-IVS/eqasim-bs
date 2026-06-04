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


# ---------------------------------------------------------------------------
# Bucket orchestrator
# ---------------------------------------------------------------------------

# Ideal number of adults per hh_type (capped at the household size at call time).
_IDEAL_ADULTS: dict[str, int] = {
    "single": 1, "couple": 2, "couple_with_children": 2,
    "single_parent": 1, "other_multi": 1,
}


def normalize_type(hh_type: str, size: int) -> str:
    """Clamp a sampled hh_type to be size-compatible. By Zensus definition a
    ``couple`` is a 2-person household and ``single`` a 1-person household; if a
    sampled type cannot occur at the household's size (noise in the share data)
    it is treated as ``other_multi`` so composition and the realised label stay
    coherent."""
    size = int(size)
    if hh_type == "single" and size != 1:
        return "other_multi"
    if hh_type == "couple" and size != 2:
        return "other_multi"
    return hh_type


def required_adults(hh_type: str, size: int) -> int:
    return min(_IDEAL_ADULTS.get(hh_type, 1), int(size))


def required_children(hh_type: str, size: int) -> int:
    size = int(size)
    if hh_type == "couple_with_children":
        return max(size - 2, 0)
    if hh_type == "single_parent":
        return max(size - 1, 0)
    return 0


def _reduce_demand(req: list[int], available: int) -> int:
    """Reduce the per-household requirement list ``req`` (in place) until it sums
    to at most ``available``, always trimming the current largest entry first.
    Returns the number of slots relaxed (turned into free fill slots)."""
    reduced = 0
    while sum(req) > available:
        idx = max(range(len(req)), key=lambda i: req[i])
        if req[idx] <= 0:
            break
        req[idx] -= 1
        reduced += 1
    return reduced


def _realised_type(member_ages: list[float], min_adult: int) -> str:
    n = len(member_ages)
    a = sum(1 for x in member_ages if x >= min_adult)
    c = n - a
    if n == 1:
        return "single"
    if n == 2 and a == 2:           # a couple is exactly two adults
        return "couple"
    if a >= 2 and c >= 1:
        return "couple_with_children"
    if a == 1 and c >= 1:
        return "single_parent"
    return "other_multi"            # incl. multi-adult flatshares (a>=2, c==0, n>2)


def build_bucket_households(ages: np.ndarray, hh_types: list[str],
                           sizes: list[int], cfg: dict):
    """Assign the persons of one (commune, hh_size) bucket to households.

    ``hh_types`` and ``sizes`` describe the household shells (one per household).
    Returns ``(household_of_person, realised_hh_type)`` where
    ``household_of_person[i]`` is the household index of person ``i`` and
    ``realised_hh_type[h]`` is the household's type derived from its final adult/
    child composition (it may differ from the requested type if the bucket's
    adult/child pool forced a feasibility relaxation).

    The adult/child composition of each type is a HARD constraint; within that,
    couples are paired by ``optimal_adult_pairs`` (min within-pair age gap) and
    children placed by ``assign_children_to_households`` (min parent-child gap
    deviation). When the pool cannot meet the requested composition the demand
    is relaxed toward free fill slots (logged); no person is ever dropped.
    """
    ages = np.asarray(ages)
    n = len(ages)
    H = len(hh_types)
    min_adult = int(cfg.get("min_adult_age", 18))
    gap = float(cfg.get("parent_child_gap_years", DEFAULT_PARENT_CHILD_GAP_YEARS))
    pc_weight = float(cfg.get("parent_child_weight", 1.0))
    couple_weight = float(cfg.get("couple_age_weight", 1.0))

    adults, children = split_pools(ages, min_adult)
    adult_arr = np.asarray(adults, dtype=int)
    child_arr = np.asarray(children, dtype=int)

    # Clamp incompatible (type, size) shells (e.g. a 'couple' shell of size != 2)
    # to other_multi so composition and labels stay coherent.
    hh_types = [normalize_type(t, s) for t, s in zip(hh_types, sizes)]
    a_req = [required_adults(t, s) for t, s in zip(hh_types, sizes)]
    c_req = [required_children(t, s) for t, s in zip(hh_types, sizes)]

    relaxed = _reduce_demand(a_req, len(adult_arr))
    relaxed += _reduce_demand(c_req, len(child_arr))
    if relaxed:
        print(f"[household_composition] relaxed {relaxed} composition slot(s) "
              f"due to pool shortage in a {n}-person bucket")

    members: list[list[int]] = [[] for _ in range(H)]

    # --- Adults: couples paired optimally (min within-pair age gap), then
    # routed by age so the YOUNGER couples go to the child-rearing households
    # (couple_with_children) and the older couples to childless couple shells.
    # This is part of the parent-child objective: it avoids putting an
    # empty-nest-age couple with young children when a childless couple shell
    # exists in the same bucket. ---
    couple_child_hh = [h for h in range(H) if a_req[h] == 2 and c_req[h] > 0]
    couple_only_hh = [h for h in range(H) if a_req[h] == 2 and c_req[h] == 0]
    n_couple = len(couple_child_hh) + len(couple_only_hh)
    if len(adult_arr) == 0:
        pairs_idx, leftover_idx = [], np.empty(0, dtype=int)
    elif couple_weight > 0:
        # Age-optimal pairing (min within-pair age gap).
        pairs_idx, leftover_idx = optimal_adult_pairs(
            ages[adult_arr], n_couple, return_leftover=True)
    else:
        # couple_age_weight == 0: no couple-age optimisation -- pair adults in
        # their natural (stable) order and skip the age-based shell routing.
        pairs_idx = [(2 * k, 2 * k + 1) for k in range(n_couple)]
        leftover_idx = np.arange(2 * n_couple, len(adult_arr))
    if couple_weight > 0:
        # Route the formed pairs youngest-first to the child-rearing shells.
        pair_min_age = [min(ages[adult_arr[i]], ages[adult_arr[j]]) for i, j in pairs_idx]
        pair_order = np.argsort(pair_min_age, kind="mergesort")
        sorted_pairs = [pairs_idx[k] for k in pair_order]
    else:
        sorted_pairs = pairs_idx
    for k, h in enumerate(couple_child_hh + couple_only_hh):
        i, j = sorted_pairs[k]
        members[h].extend([int(adult_arr[i]), int(adult_arr[j])])
    remaining_adults = [int(adult_arr[t]) for t in leftover_idx]

    for h in range(H):
        if a_req[h] == 1 and remaining_adults:
            members[h].append(remaining_adults.pop(0))

    # --- Children: place the required children into parent households. ---
    need_child_hh = [h for h in range(H) if c_req[h] > 0]
    remaining_children = list(child_arr)
    if need_child_hh and remaining_children:
        slots = np.array([c_req[h] for h in need_child_hh])
        total_needed = int(slots.sum())
        assigned = remaining_children[:total_needed]
        remaining_children = remaining_children[total_needed:]
        if pc_weight > 0:
            parent_ages = np.array([
                min(ages[m] for m in members[h]) if members[h] else float(min_adult + gap)
                for h in need_child_hh
            ])
            who = assign_children_to_households(
                ages[np.asarray(assigned, dtype=int)], parent_ages, slots, target_gap=gap)
            for ci, person in enumerate(assigned):
                members[need_child_hh[who[ci]]].append(int(person))
        else:
            ptr = 0
            for hi, h in enumerate(need_child_hh):
                for _ in range(int(slots[hi])):
                    members[h].append(int(assigned[ptr]))
                    ptr += 1

    # --- Fill remaining slots (adults first, then children). ---
    fill_pool = remaining_adults + remaining_children
    fi = 0
    for h in range(H):
        while len(members[h]) < int(sizes[h]) and fi < len(fill_pool):
            members[h].append(fill_pool[fi])
            fi += 1

    household_of_person = np.full(n, -1, dtype=int)
    for h, mem in enumerate(members):
        for p in mem:
            household_of_person[p] = h
    if (household_of_person < 0).any():
        raise RuntimeError(
            "[household_composition] internal error: unplaced persons in bucket"
        )

    realised = [
        _realised_type([float(ages[p]) for p in mem], min_adult) for mem in members
    ]
    return household_of_person, realised
