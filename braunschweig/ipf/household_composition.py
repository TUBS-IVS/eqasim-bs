"""Optimisation helpers for age-aware household composition (#3b).

Given a bucket of persons (ages) and the per-household ``hh_type`` composition
requirements, assign persons to households minimising age-implausibility
(within-couple age gap + parent-child age-gap deviation) subject to the hard
adult/child composition constraints. Pure numpy/scipy; deterministic (stable
sorts, no RNG inside).

The parent-child target gap default is Destatis-aligned: the mean age of the
mother at birth (Statistisches Bundesamt 2024) is 31.8 years (1st child 30.4)
and equals the mother-child age gap; the father is 34.7. The youngest adult in a
two-parent household is usually the mother, so 31.8 is the default. The same
Destatis basis (mothers 15-49, fathers 15-69) bounds the realistic gap, which is
why placement avoids leaving a child with a very old adult.
"""
from __future__ import annotations

import numpy as np

# Destatis-aligned default parent-child age gap in years (mean age of the mother
# at birth 2024 = 31.8; the youngest household adult is usually the mother).
DEFAULT_PARENT_CHILD_GAP_YEARS: float = 31.8

# Default share of couples formed same-sex. Source: Statistisches Bundesamt,
# Mikrozensus 2025 (Endergebnisse 2024 / Erstergebnisse 2025), Tabelle
# "Gleichgeschlechtliche Lebensgemeinschaften": 204 000 same-sex couples (102 000
# male, 102 000 female -- a ~50/50 split) against ~18.9 million couples in Germany
# => ~1.1 % of all couples. The 50/50 male/female split emerges automatically
# from a balanced adult pool, so only the aggregate share is parametrised. Applied
# uniformly across couple shells, so a small realistic minority of same-sex
# couples also have children (Regenbogenfamilien); most come out childless because
# childless couple shells dominate. Configurable via
# ``braunschweig.chunking.same_sex_couple_share``.
DEFAULT_SAME_SEX_COUPLE_SHARE: float = 0.011


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


def pair_adults_sex_aware(block: np.ndarray, match_key: np.ndarray,
                          is_female: np.ndarray, same_sex_share: float, rng):
    """Pair the adults in ``block`` into couples, opposite-sex by default with a
    small calibrated same-sex share.

    ``block`` is an even-length array of person indices. The number of same-sex
    couples is set to ``max(intended, forced)`` where ``intended`` is a
    ``Binomial(k, same_sex_share)`` draw (the genuine ~1.1 % same-sex couples) and
    ``forced = |#males - #females| / 2`` is the minimum imposed by the block's sex
    imbalance -- so the only same-sex couples beyond the intended share are those a
    sex-imbalanced pool makes unavoidable (never dropping anyone). This
    opposite-first allocation keeps the realised share close to the target even on
    small, locally-imbalanced buckets, where a per-seed greedy fallback would
    inflate it.

    Within each group, partners are paired adjacently in ``match_key`` (jittered)
    order, so the realistic within-couple age spread is preserved and opposite-sex
    couples are rank-aligned (small age gaps). ``match_key[i]`` and ``is_female[i]``
    are indexed by person id. Returns a list of ``(i, j)`` index pairs.
    Deterministic for a fixed ``rng``.
    """
    block = [int(x) for x in np.asarray(block, dtype=int)]
    k = len(block) // 2
    if k == 0:
        return []
    males = sorted((b for b in block if not bool(is_female[b])),
                   key=lambda b: float(match_key[b]))
    females = sorted((b for b in block if bool(is_female[b])),
                     key=lambda b: float(match_key[b]))
    m, f = len(males), len(females)

    # Split the same-sex couples into ``cm`` male and ``cf`` female ones. From m
    # males and f females, leaving ``n_opp`` of each for opposite pairs requires
    # m - 2*cm == f - 2*cf == n_opp, i.e. cm - cf == (m - f) / 2. With
    # cm + cf == n_same this fixes cm, cf; both must be non-negative integers,
    # which forces n_same >= forced_same AND n_same == forced_same (mod 2). A
    # single intended same-sex couple is therefore infeasible in a balanced block
    # (you cannot make one same-sex couple without stranding an opposite pair), so
    # the intended count is snapped to the nearest feasible parity-correct value.
    delta = (m - f) // 2                                # signed; |delta| = floor
    forced_same = abs(delta)
    intended_same = int(np.sum(rng.random_sample(k) < same_sex_share))
    n_same = max(forced_same, intended_same)
    if (n_same - forced_same) % 2 != 0:                 # snap to feasible parity
        n_same = n_same - 1 if n_same - 1 >= forced_same else n_same + 1
    max_same = k if (min(m, f) % 2 == 0) else k - 1     # pair-budget + parity cap
    while n_same > max_same:
        n_same -= 2
    n_same = max(n_same, forced_same)

    cm = (n_same + delta) // 2                           # same-sex male couples
    cf = (n_same - delta) // 2                           # same-sex female couples
    mm, ff = 2 * cm, 2 * cf
    pairs: list[tuple[int, int]] = []
    # Same-sex couples are drawn from the youngest of each sex (adjacent pairs);
    # opposite-sex couples use the remaining, rank-aligned, males and females.
    for i in range(0, mm, 2):
        pairs.append((males[i], males[i + 1]))
    for i in range(0, ff, 2):
        pairs.append((females[i], females[i + 1]))
    for a, b in zip(males[mm:], females[ff:]):
        pairs.append((a, b))
    return pairs


def assign_children_to_households(child_ages: np.ndarray, parent_ages: np.ndarray,
                                  child_slots: np.ndarray,
                                  target_gap: float = DEFAULT_PARENT_CHILD_GAP_YEARS):
    """Assign each child to a household child-slot minimising the total
    parent-child age-gap deviation ``Sum |child_age - (parent_age - target_gap)|``.

    ``child_slots[h]`` is the number of child slots in household ``h`` (with
    parent age ``parent_ages[h]``). Each household is expanded into its slots
    (target ``parent_age - target_gap``); the children are then matched to the
    slot targets by **sorted rank** -- the i-th youngest child to the slot with
    the i-th smallest target. For this 1-D cost (deviation on the age line) and
    the balanced call this function always receives (``sum(child_slots) ==
    len(child_ages)``, the caller slices exactly that many children) the sorted
    matching is provably the global minimum-total-deviation assignment, identical
    to a Hungarian ``linear_sum_assignment`` but ``O(n log n)`` instead of
    ``O(n^3)`` -- essential at full-population scale where a single urban bucket
    has tens of thousands of children (a dense LAP cost matrix is then quadratic
    in memory and cubic in time). Requires ``sum(child_slots) >=
    len(child_ages)``. Returns an int array of length ``len(child_ages)`` giving
    the household index per child.
    """
    child_ages = np.asarray(child_ages, dtype=float)
    parent_ages = np.asarray(parent_ages, dtype=float)
    child_slots = np.asarray(child_slots, dtype=int)
    n_children = len(child_ages)
    if n_children == 0:
        return np.empty(0, dtype=int)

    # target_gap may be a scalar or a per-household array (a realistic spread of
    # the parent-child age gap around the mean, rather than a single point).
    tg = np.asarray(target_gap, dtype=float)
    if tg.ndim == 0:
        tg = np.full(len(parent_ages), float(tg))
    slot_household = np.repeat(np.arange(len(parent_ages)), child_slots)
    slot_target = np.repeat(parent_ages - tg, child_slots)
    if len(slot_household) < n_children:
        raise ValueError(
            "assign_children_to_households: fewer child slots "
            f"({len(slot_household)}) than children ({n_children})"
        )

    # Sorted rank matching: the optimal 1-D assignment pairs the sorted children
    # with the sorted slot targets in rank order (stable sort -> deterministic).
    order_children = np.argsort(child_ages, kind="mergesort")
    order_slots = np.argsort(slot_target, kind="mergesort")
    assign = np.empty(n_children, dtype=int)
    assign[order_children] = slot_household[order_slots[:n_children]]
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


def _ensure_child_capacity(a_req: list[int], c_req: list[int], sizes: list[int],
                           n_children: int) -> int:
    """Grow the child-bearing capacity (``c_req``, in place) until it covers every
    child in the bucket, so no child spills into the fill phase.

    The IPF can place more children in a (commune, hh_size) cell than the Zensus
    hh_type shares provide child-bearing shells for (e.g. ~20 % of size-2 persons
    are children, far above the single_parent share). Without this, the surplus
    children fall through to the fill phase and land on the childless shells'
    adults -- which the priority routing fills with the OLDEST adults -- producing
    implausible households such as an 84-year-old "single parent" of a minor.

    Childless shells (``c_req == 0``) that still have room and at least one adult
    are promoted to child-bearing one slot at a time. 2-adult shells are promoted
    first (-> couple_with_children, two parents) before 1-adult shells
    (-> single_parent). The existing adult routing then hands these shells the
    youngest adults, so the surplus children get plausible parents. The hh_type
    label is re-derived from the realised composition, so promoting trades a small
    amount of hh_type-marginal fidelity (the Zensus single_parent/couple split) for
    age plausibility -- a deliberate trade-off, used only when the IPF's child
    count forces it. Returns the number of child slots added.
    """
    need = n_children - sum(c_req)
    if need <= 0:
        return 0
    candidates = sorted(
        (h for h in range(len(a_req))
         if a_req[h] >= 1 and a_req[h] + c_req[h] < sizes[h]),
        key=lambda h: (a_req[h] == 2, sizes[h] - a_req[h] - c_req[h]),
        reverse=True,
    )
    promoted = 0
    for h in candidates:
        if need <= 0:
            break
        room = sizes[h] - a_req[h] - c_req[h]
        add = min(room, need)
        c_req[h] += add
        promoted += add
        need -= add
    return promoted


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
                           sizes: list[int], cfg: dict, rng=None,
                           is_female: np.ndarray | None = None,
                           relaxation_stats: dict | None = None):
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
    is relaxed toward free fill slots; no person is ever dropped.

    That relaxation is a FALLBACK and must stay observable. Pass a dict as
    ``relaxation_stats`` to accumulate ``relaxed_slots`` / ``persons`` /
    ``buckets`` / ``buckets_relaxed`` across calls, so the caller can report one
    primary-vs-fallback rate for the whole run; the per-bucket line is then
    suppressed. Omit it (unit tests, direct calls) to keep the per-bucket print.
    """
    ages = np.asarray(ages)
    n = len(ages)
    H = len(hh_types)
    min_adult = int(cfg.get("min_adult_age", 18))
    gap = float(cfg.get("parent_child_gap_years", DEFAULT_PARENT_CHILD_GAP_YEARS))
    pc_weight = float(cfg.get("parent_child_weight", 1.0))
    couple_weight = float(cfg.get("couple_age_weight", 1.0))
    # Realistic spread of the parent-child gap around the mean (the std of the
    # mother's age at birth, Destatis ~5.5 y). Only applied when an ``rng`` is
    # given (the pipeline path); pure deterministic calls use the point target.
    gap_std = float(cfg.get("parent_child_gap_std", 0.0))
    # Realistic spread of the within-couple age gap. Strict age-sorted pairing in
    # a dense adult pool produces ~0-gap couples (everyone same age); jittering
    # the sort key by N(0, couple_age_std) before pairing reproduces the real
    # partner age-difference spread (German mean ~3-4 y). Only with an rng.
    couple_std = float(cfg.get("couple_age_std", 0.0))
    # Sex-aware couple formation: when enabled and a per-person sex vector is
    # provided, couples are paired opposite-sex with a small calibrated same-sex
    # share (Destatis Mikrozensus 2025, ~1.1 % of couples). Default off -> the
    # legacy age-adjacent (sex-blind) pairing, byte-identical to before.
    sex_aware = bool(cfg.get("sex_aware_couples", False)) and is_female is not None
    same_sex_share = float(cfg.get("same_sex_couple_share",
                                   DEFAULT_SAME_SEX_COUPLE_SHARE))

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
    if relaxation_stats is None:
        # Direct/unit-test call: keep the per-bucket diagnostic.
        if relaxed:
            print(f"[household_composition] relaxed {relaxed} composition slot(s) "
                  f"due to pool shortage in a {n}-person bucket")
    else:
        # Pipeline call: accumulate so the CALLER can report one primary-vs-
        # fallback rate over all buckets instead of one line per bucket (a
        # per-bucket count says nothing about how much of the run relaxed).
        relaxation_stats["relaxed_slots"] = (
            relaxation_stats.get("relaxed_slots", 0) + relaxed)
        relaxation_stats["persons"] = relaxation_stats.get("persons", 0) + n
        relaxation_stats["buckets"] = relaxation_stats.get("buckets", 0) + 1
        if relaxed:
            relaxation_stats["buckets_relaxed"] = (
                relaxation_stats.get("buckets_relaxed", 0) + 1)

    # Grow child-bearing capacity so every child gets a (young) adult instead of
    # spilling onto the oldest childless-shell adults (see _ensure_child_capacity).
    _ensure_child_capacity(a_req, c_req, list(sizes), len(child_arr))

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
    # Allocate adults to households in a priority order so the CHILD-REARING
    # households get the youngest adults (tight parent-child gap) and the
    # childless ones the older adults; within each household couples are formed
    # from adults that are adjacent in the age sort (tight within-couple gap):
    #   1. couple_with_children (2 youngest-available adults each)
    #   2. single_parent       (1 next-youngest adult each)
    #   3. childless couple    (2 older adults each)
    #   4. single / other_multi(1 oldest adult each)
    # The age sort is the whole optimisation here; with both weights 0 it falls
    # back to natural order (no age objective).
    cwc_hh = couple_child_hh                       # couple_with_children
    couple_only = couple_only_hh                   # childless couple
    sp_hh = [h for h in range(H) if a_req[h] == 1 and c_req[h] > 0]
    single_other_hh = [h for h in range(H) if a_req[h] == 1 and c_req[h] == 0]

    # match_key holds the (jittered) age per person; it drives both the age sort
    # and -- in the sex-aware path -- the nearest-partner selection, so the couple
    # age-spread is identical in both pairing modes.
    match_key = ages.astype(float)
    if couple_weight > 0 or pc_weight > 0:
        keys = ages[adult_arr].astype(float)
        if rng is not None and couple_weight > 0 and couple_std > 0:
            keys = keys + rng.normal(0.0, couple_std, size=len(keys))
        adults_sorted = adult_arr[np.argsort(keys, kind="mergesort")]
        match_key = ages.astype(float).copy()
        match_key[adult_arr] = keys
    else:
        adults_sorted = adult_arr

    def _pair_from(hh_list: list[int], source: np.ndarray, p: int) -> int:
        """Form a couple for each household in ``hh_list`` from the contiguous
        block ``source[p : p + 2*len(hh_list)]`` and return the new pointer.
        Sex-aware when enabled, otherwise the legacy age-adjacent pairing."""
        n_pairs = len(hh_list)
        block = source[p: p + 2 * n_pairs]
        if sex_aware:
            block_pairs = pair_adults_sex_aware(
                block, match_key, is_female, same_sex_share, rng)
        else:
            block_pairs = [(int(block[2 * k]), int(block[2 * k + 1]))
                           for k in range(n_pairs)]
        for h, (a, b) in zip(hh_list, block_pairs):
            members[h].extend([a, b])
        return p + 2 * n_pairs

    # --- Parent-age targeting. The child-bearing households (cwc + single_parent)
    # claim a contiguous window of the age-sorted adults centred on the desired
    # parent age (median child age + gap) instead of the absolute youngest, so the
    # realised parent-child gap is lifted toward the Destatis target: with the
    # youngest-first routing the 18-25-year-olds become parents of newborns and
    # collapse the gap (~26 y vs target 31.8). The very youngest and the oldest
    # adults are then left for childless young couples/singles and the elderly.
    # The window position blends from "youngest" (weight 0 -> legacy, byte-
    # identical) to "fully targeted" (weight 1). The childless segments stay
    # globally age-sorted (the pre-window adults are all younger than the
    # post-window ones), so childless couples keep tight within-pair gaps. ---
    target_w = float(cfg.get("child_parent_age_target_weight", 0.0))
    P = 2 * len(cwc_hh) + len(sp_hh)               # child-bearing adult slots
    A = len(adults_sorted)
    start = 0
    if target_w > 0 and 0 < P < A and len(child_arr) > 0:
        sorted_adult_ages = match_key[adults_sorted]
        desired = float(np.median(ages[child_arr])) + gap
        idx = int(np.searchsorted(sorted_adult_ages, desired))
        start_target = min(max(idx - P // 2, 0), A - P)
        start = min(max(int(round(target_w * start_target)), 0), A - P)
    parent_adults = adults_sorted[start: start + P]
    childless_adults = np.concatenate(
        [adults_sorted[:start], adults_sorted[start + P:]])

    pp = 0
    pp = _pair_from(cwc_hh, parent_adults, pp)
    for h in sp_hh:
        members[h].append(int(parent_adults[pp]))
        pp += 1
    cp = 0
    cp = _pair_from(couple_only, childless_adults, cp)
    for h in single_other_hh:
        members[h].append(int(childless_adults[cp]))
        cp += 1
    remaining_adults = [int(x) for x in childless_adults[cp:]]

    # --- Children: place the required children into the (young, child-rearing)
    # parent households, each with its own target gap drawn around the mean so
    # the realised gaps form a realistic distribution rather than a spike. ---
    gap_max = float(cfg.get("parent_child_gap_max", 50.0))

    def _slots_open(h):
        return int(sizes[h]) - len(members[h])

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
            if rng is not None and gap_std > 0:
                per_hh_gap = np.clip(
                    rng.normal(gap, gap_std, size=len(need_child_hh)), 16.0, gap_max)
            else:
                per_hh_gap = gap
            who = assign_children_to_households(
                ages[np.asarray(assigned, dtype=int)], parent_ages, slots,
                target_gap=per_hh_gap)
            for ci, person in enumerate(assigned):
                members[need_child_hh[who[ci]]].append(int(person))
        else:
            ptr = 0
            for hi, h in enumerate(need_child_hh):
                for _ in range(int(slots[hi])):
                    members[h].append(int(assigned[ptr]))
                    ptr += 1

    # --- Fill remaining slots: adults fill any open household; leftover children
    # go to the households with the YOUNGEST current adult first, so they land
    # with the most plausibly-aged adults available (bounds the parent-child gap
    # tail as far as the pool's young-adult capacity allows). ---
    ai = 0
    for h in range(H):
        while _slots_open(h) > 0 and ai < len(remaining_adults):
            members[h].append(remaining_adults[ai])
            ai += 1
    leftover_adults = remaining_adults[ai:]

    open_for_children = sorted(
        (h for h in range(H) if _slots_open(h) > 0),
        key=lambda h: min((ages[p] for p in members[h] if ages[p] >= min_adult),
                          default=10_000.0))
    ci = 0
    for h in open_for_children:
        while _slots_open(h) > 0 and ci < len(remaining_children):
            members[h].append(remaining_children[ci])
            ci += 1

    rest = list(leftover_adults) + remaining_children[ci:]
    ri = 0
    for h in range(H):
        while _slots_open(h) > 0 and ri < len(rest):
            members[h].append(rest[ri])
            ri += 1

    # Hard rule: NO all-children household. If the pool was too adult-poor for
    # every shell to be headed by an adult, move the orphaned children into the
    # adult-headed household whose youngest adult best fits the parent-child gap;
    # the emptied shell is dropped. (A child older than an adult is structurally
    # impossible given the >=18 adult split, so it needs no separate rule.)
    def _youngest_adult(mem):
        adult_ages = [ages[p] for p in mem if ages[p] >= min_adult]
        return min(adult_ages) if adult_ages else None

    adult_hh = [h for h in range(H) if _youngest_adult(members[h]) is not None]
    if adult_hh:
        for h in range(H):
            if members[h] and _youngest_adult(members[h]) is None:
                for p in members[h]:
                    best = min(adult_hh, key=lambda a:
                               abs(_youngest_adult(members[a]) - (ages[p] + gap)))
                    members[best].append(p)
                members[h] = []

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
