"""Tests for the age-aware household-composition optimisation (#3b).

Pure numpy/scipy helpers: adult/child pools, optimal couple pairing (sorted
adjacency minimises the within-pair age gap), optimal child->parent age
matching, and the bucket orchestrator with feasibility fallback.
"""
from __future__ import annotations

import numpy as np
import pytest

from braunschweig.ipf import household_composition as hc


class TestPools:
    def test_split_pools_by_adult_age(self):
        ages = np.array([5, 17, 18, 40, 70])
        adults, children = hc.split_pools(ages, min_adult_age=18)
        assert sorted(adults.tolist()) == [2, 3, 4]   # ages 18, 40, 70
        assert sorted(children.tolist()) == [0, 1]     # ages 5, 17


class TestCouplePairing:
    def test_sorted_adjacent_pairing_minimises_age_gap(self):
        # Ages 20, 22, 50, 53 -> optimal pairs (20,22) and (50,53), gaps 2 and 3.
        ages = np.array([50, 20, 53, 22])
        pairs = hc.optimal_adult_pairs(ages, n_pairs=2)
        gaps = sorted(abs(ages[i] - ages[j]) for i, j in pairs)
        assert gaps == [2, 3]
        used = sorted([k for p in pairs for k in p])
        assert used == [0, 1, 2, 3]

    def test_returns_requested_pair_count_and_leftover(self):
        ages = np.array([20, 21, 60])
        pairs, leftover = hc.optimal_adult_pairs(ages, n_pairs=1, return_leftover=True)
        assert len(pairs) == 1
        assert abs(ages[pairs[0][0]] - ages[pairs[0][1]]) == 1
        assert leftover.tolist() == [2]


class TestChildMatching:
    def test_children_matched_to_age_plausible_parents(self):
        # Two parent households (parent ages 30 and 60), one child slot each.
        # target_gap=30 -> parent-30 expects child ~0, parent-60 expects ~30.
        child_ages = np.array([5, 35])
        parent_ages = np.array([30, 60])
        slots = np.array([1, 1])
        assign = hc.assign_children_to_households(
            child_ages, parent_ages, slots, target_gap=30)
        assert assign[0] == 0   # child 5 -> household with parent 30
        assert assign[1] == 1   # child 35 -> household with parent 60

    def test_respects_slot_capacity(self):
        child_ages = np.array([3, 6, 9])
        parent_ages = np.array([35])
        slots = np.array([3])
        assign = hc.assign_children_to_households(
            child_ages, parent_ages, slots, target_gap=30)
        assert set(assign.tolist()) == {0}
        assert len(assign) == 3

    def test_deterministic(self):
        child_ages = np.array([4, 9, 2])
        parent_ages = np.array([34, 40])
        slots = np.array([2, 1])
        a = hc.assign_children_to_households(child_ages, parent_ages, slots, target_gap=30)
        b = hc.assign_children_to_households(child_ages, parent_ages, slots, target_gap=30)
        assert a.tolist() == b.tolist()


class TestSexAwarePairing:
    """Sex-aware couple pairing: couples are opposite-sex by default with a small
    calibrated same-sex share (Destatis Mikrozensus 2025 ~1.1% of couples)."""

    def test_opposite_sex_when_share_zero(self):
        # 3 males (ages 20,40,60) + 3 females (21,41,61); share 0 -> every couple
        # must be opposite-sex, and partners nearest in age.
        ages = np.array([20, 40, 60, 21, 41, 61], dtype=float)
        is_female = np.array([False, False, False, True, True, True])
        block = np.array([0, 3, 1, 4, 2, 5])  # interleaved age order
        rng = np.random.RandomState(0)
        pairs = hc.pair_adults_sex_aware(block, ages, is_female,
                                         same_sex_share=0.0, rng=rng)
        assert len(pairs) == 3
        for i, j in pairs:
            assert is_female[i] != is_female[j]      # opposite-sex
        # nearest-age opposite-sex pairing -> (20,21),(40,41),(60,61)
        gaps = sorted(abs(ages[i] - ages[j]) for i, j in pairs)
        assert gaps == [1.0, 1.0, 1.0]

    def test_all_same_sex_when_share_one(self):
        ages = np.array([20, 22, 60, 62], dtype=float)
        is_female = np.array([False, False, True, True])
        block = np.array([0, 1, 2, 3])
        rng = np.random.RandomState(0)
        pairs = hc.pair_adults_sex_aware(block, ages, is_female,
                                         same_sex_share=1.0, rng=rng)
        for i, j in pairs:
            assert is_female[i] == is_female[j]      # same-sex

    def test_no_drop_under_sex_imbalance(self):
        # 3 males, 1 female, share 0 (want opposite): only one opposite-sex pair
        # is possible; the leftover two males must still be paired (no drop).
        ages = np.array([20, 30, 40, 25], dtype=float)
        is_female = np.array([False, False, False, True])
        block = np.array([0, 1, 2, 3])
        rng = np.random.RandomState(1)
        pairs = hc.pair_adults_sex_aware(block, ages, is_female,
                                         same_sex_share=0.0, rng=rng)
        assert len(pairs) == 2
        used = sorted([k for p in pairs for k in p])
        assert used == [0, 1, 2, 3]                  # everyone placed

    def test_deterministic(self):
        ages = np.array([20, 40, 60, 21, 41, 61], dtype=float)
        is_female = np.array([False, False, False, True, True, True])
        block = np.array([0, 3, 1, 4, 2, 5])
        a = hc.pair_adults_sex_aware(block, ages, is_female, 0.3,
                                     np.random.RandomState(7))
        b = hc.pair_adults_sex_aware(block, ages, is_female, 0.3,
                                     np.random.RandomState(7))
        assert a == b

    def test_balanced_single_couple_intended_same_sex_falls_back(self):
        # One balanced couple (1 male + 1 female) with share 1.0: a same-sex couple
        # is structurally impossible here (would strand an opposite pair), so the
        # only valid output is the opposite-sex pair -- and it must not crash.
        ages = np.array([30, 32], dtype=float)
        is_female = np.array([False, True])
        block = np.array([0, 1])
        pairs = hc.pair_adults_sex_aware(block, ages, is_female,
                                         same_sex_share=1.0,
                                         rng=np.random.RandomState(0))
        assert len(pairs) == 1
        i, j = pairs[0]
        assert is_female[i] != is_female[j]
        assert sorted([i, j]) == [0, 1]

    def test_every_adult_paired_once_random_blocks(self):
        # Fuzz: random sex mixes and shares must always pair everyone exactly once
        # and never raise (covers all parity/imbalance branches).
        rng = np.random.RandomState(3)
        for _ in range(200):
            k = int(rng.randint(1, 8))
            block = np.arange(2 * k)
            ages = rng.randint(18, 80, size=2 * k).astype(float)
            is_female = rng.rand(2 * k) < rng.uniform(0.1, 0.9)
            share = float(rng.uniform(0.0, 0.5))
            pairs = hc.pair_adults_sex_aware(block, ages, is_female, share, rng)
            used = sorted([x for p in pairs for x in p])
            assert used == list(range(2 * k))
            assert len(pairs) == k

    def _same_sex_share(self, hoh, ages, is_female):
        same = total = 0
        for h in set(hoh.tolist()):
            m = np.nonzero(hoh == h)[0]
            if len(m) == 2 and (ages[m] >= 18).all():
                total += 1
                same += int(is_female[m[0]] == is_female[m[1]])
        return same / total if total else 0.0

    def test_share_is_small_minority_not_sex_blind(self):
        # Many childless couple shells: sex-aware keeps same-sex a small minority,
        # vs the sex-blind pairing which yields ~50% (random adjacency).
        n = 400
        ages = np.array([30 + (i % 40) for i in range(2 * n)], dtype=float)
        is_female = np.array([i % 2 == 0 for i in range(2 * n)])
        types, sizes = ["couple"] * n, [2] * n
        base = dict(min_adult_age=18, couple_age_weight=1.0, parent_child_weight=1.0,
                    parent_child_gap_years=31)
        aware = hc.build_bucket_households(
            ages, types, sizes,
            cfg={**base, "sex_aware_couples": True, "same_sex_couple_share": 0.011},
            rng=np.random.RandomState(0), is_female=is_female)[0]
        blind = hc.build_bucket_households(
            ages, types, sizes, cfg=base, rng=np.random.RandomState(0))[0]
        assert self._same_sex_share(aware, ages, is_female) < 0.05
        assert self._same_sex_share(blind, ages, is_female) > 0.30

    def test_imbalanced_pool_forces_minimum_same_sex(self):
        # 3 males + 1 female over two couple shells: exactly one same-sex (male)
        # couple is unavoidable; opposite-first yields exactly that minimum.
        ages = np.array([30, 32, 34, 36], dtype=float)
        is_female = np.array([False, False, False, True])
        cfg = dict(min_adult_age=18, couple_age_weight=1.0, parent_child_weight=1.0,
                   parent_child_gap_years=31, sex_aware_couples=True,
                   same_sex_couple_share=0.0)
        hoh = hc.build_bucket_households(
            ages, ["couple", "couple"], [2, 2], cfg=cfg,
            rng=np.random.RandomState(0), is_female=is_female)[0]
        assert self._same_sex_share(hoh, ages, is_female) == 0.5  # exactly 1 of 2

    def test_sexes_none_is_byte_identical_to_legacy_pairing(self):
        # Without is_female the pairing is the legacy age-adjacent one.
        ages = np.array([29, 64, 31, 66], dtype=float)
        cfg = dict(min_adult_age=18, couple_age_weight=1.0, parent_child_weight=1.0,
                   parent_child_gap_years=31)
        legacy = hc.build_bucket_households(
            ages, ["couple", "couple"], [2, 2], cfg=cfg)[0]
        with_none = hc.build_bucket_households(
            ages, ["couple", "couple"], [2, 2], cfg=cfg, is_female=None)[0]
        assert legacy.tolist() == with_none.tolist()


class TestBuildBucket:
    def _cfg(self, **kw):
        base = dict(min_adult_age=18, couple_age_weight=1.0,
                    parent_child_weight=1.0, parent_child_gap_years=31)
        base.update(kw)
        return base

    def _members(self, ages, hoh, h):
        return ages[hoh == h]

    def test_couple_with_children_gets_two_adults_and_children(self):
        ages = np.array([40, 38, 8, 5])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["couple_with_children"], sizes=[4], cfg=self._cfg())
        assert set(hoh.tolist()) == {0}
        m = self._members(ages, hoh, 0)
        assert (m >= 18).sum() == 2 and (m < 18).sum() == 2
        assert types[0] == "couple_with_children"

    def test_single_parent_one_adult_rest_children(self):
        ages = np.array([35, 7, 4])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["single_parent"], sizes=[3], cfg=self._cfg())
        m = self._members(ages, hoh, 0)
        assert (m >= 18).sum() == 1 and (m < 18).sum() == 2
        assert types[0] == "single_parent"

    def test_couple_two_similar_age_adults(self):
        ages = np.array([29, 64, 31, 66])  # two couples (29,31) and (64,66)
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["couple", "couple"], sizes=[2, 2], cfg=self._cfg())
        for h in (0, 1):
            m = self._members(ages, hoh, h)
            assert (m >= 18).sum() == 2
            assert abs(int(m[0]) - int(m[1])) <= 3  # similar age within couple

    def test_no_all_children_household_when_feasible(self):
        ages = np.array([30, 32, 6, 8])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["other_multi", "other_multi"], sizes=[2, 2],
            cfg=self._cfg())
        for h in (0, 1):
            m = self._members(ages, hoh, h)
            assert (m >= 18).sum() >= 1

    def test_feasibility_fallback_drops_nobody_and_no_all_children(self, capsys):
        # 1 adult but two couple households requested -> infeasible. Nobody is
        # dropped AND no all-children household survives (the orphan children are
        # merged into the adult-headed household).
        ages = np.array([40, 6, 8, 10])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["couple", "couple"], sizes=[2, 2], cfg=self._cfg())
        assert len(hoh) == 4                       # nobody dropped
        for h in set(hoh.tolist()):
            members = ages[hoh == h]
            assert (members >= 18).sum() >= 1      # every household has an adult
        out = capsys.readouterr().out
        assert "relaxed" in out.lower()

    def test_no_all_children_household_under_adult_shortage(self):
        # 1 adult, 3 children, two size-2 shells -> would-be all-children shell
        # eliminated; the single adult heads the (merged) household.
        ages = np.array([30, 4, 6, 8])
        hoh, _ = hc.build_bucket_households(
            ages, hh_types=["other_multi", "other_multi"], sizes=[2, 2],
            cfg=self._cfg())
        for h in set(hoh.tolist()):
            assert (ages[hoh == h] >= 18).sum() >= 1

    def test_parent_older_than_child(self):
        ages = np.array([34, 4, 60, 30])  # 2 single_parent size-2
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["single_parent", "single_parent"], sizes=[2, 2],
            cfg=self._cfg())
        for h in (0, 1):
            m = sorted(self._members(ages, hoh, h).tolist())
            assert m[-1] - m[0] > 0  # an adult older than the child present

    def test_every_person_assigned_once(self):
        ages = np.array([40, 38, 8, 5, 30, 6])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["couple_with_children", "single_parent"],
            sizes=[4, 2], cfg=self._cfg())
        assert sorted(hoh.tolist()) == sorted([0, 0, 0, 0, 1, 1])

    def test_deterministic(self):
        ages = np.array([40, 38, 8, 5, 30, 6])
        a = hc.build_bucket_households(ages, ["couple_with_children", "single_parent"],
                                      [4, 2], cfg=self._cfg())[0]
        b = hc.build_bucket_households(ages, ["couple_with_children", "single_parent"],
                                      [4, 2], cfg=self._cfg())[0]
        assert a.tolist() == b.tolist()

    def test_age_weights_zero_disables_age_pairing(self):
        # 2 childless couple shells; adults in input order [60,20,62,22].
        # any age weight on -> the adult sort makes age-optimal pairs (20,22),
        # (60,62) (gaps 2,2). Both weights 0 -> natural-order pairing (60,20),
        # (62,22) with a ~40-year gap.
        ages = np.array([60, 20, 62, 22])
        on = hc.build_bucket_households(
            ages, ["couple", "couple"], [2, 2], cfg=self._cfg(couple_age_weight=1.0))[0]
        off = hc.build_bucket_households(
            ages, ["couple", "couple"], [2, 2],
            cfg=self._cfg(couple_age_weight=0.0, parent_child_weight=0.0))[0]
        # With optimisation on, the two within-couple gaps are both 2.
        on_gaps = sorted(abs(int(ages[i]) - int(ages[j]))
                         for h in (0, 1)
                         for i, j in [tuple(np.nonzero(on == h)[0])])
        assert on_gaps == [2, 2]
        # With optimisation off, natural-order pairing -> a 40-year gap appears.
        off_gaps = sorted(abs(int(ages[i]) - int(ages[j]))
                          for h in (0, 1)
                          for i, j in [tuple(np.nonzero(off == h)[0])])
        assert max(off_gaps) >= 38

    def test_gap_std_spreads_parent_child_gaps(self):
        # Many single_parent size-2 households; with an rng + gap_std the realised
        # parent-child gaps spread around the mean instead of collapsing to a
        # single value. Adults 30-49, children 0-19.
        ages = np.array([30 + i for i in range(20)] + [i for i in range(20)])
        types = ["single_parent"] * 20
        sizes = [2] * 20
        rng = np.random.RandomState(0)
        hoh, _ = hc.build_bucket_households(
            ages, types, sizes, cfg=self._cfg(parent_child_gap_std=6.0), rng=rng)
        gaps = []
        for h in set(hoh.tolist()):
            m = ages[hoh == h]
            ad = m[m >= 18]; ch = m[m < 18]
            if len(ad) and len(ch):
                gaps.append(int(ad.min()) - int(ch.min()))
        assert np.std(gaps) > 2.0      # a real spread, not a single spike

    def test_couple_age_std_spreads_couple_gaps(self):
        # Many couple shells over a dense adult pool (two adults per integer age):
        # without a couple jitter the sorted pairing gives ~0-gap couples; with
        # couple_age_std the gaps spread.
        ages = np.array([30 + (i // 2) for i in range(40)])  # 20 ages, 2 each
        types = ["couple"] * 20
        sizes = [2] * 20
        tight = hc.build_bucket_households(
            ages, types, sizes, cfg=self._cfg(couple_age_std=0.0),
            rng=np.random.RandomState(0))[0]
        spread = hc.build_bucket_households(
            ages, types, sizes, cfg=self._cfg(couple_age_std=5.0),
            rng=np.random.RandomState(0))[0]

        def gaps(hoh):
            return [abs(int(ages[hoh == h][0]) - int(ages[hoh == h][1]))
                    for h in set(hoh.tolist())]
        assert np.mean(gaps(tight)) < 1.0       # no jitter -> ~0-gap couples
        assert np.mean(gaps(spread)) > 2.0      # jitter -> realistic spread

    def test_age_aware_bucket_deterministic_with_rng(self):
        ages = np.array([35, 7, 4, 33, 6, 2])
        a = hc.build_bucket_households(ages, ["single_parent", "single_parent"],
                                      [3, 3], cfg=self._cfg(parent_child_gap_std=5.0),
                                      rng=np.random.RandomState(1))[0]
        b = hc.build_bucket_households(ages, ["single_parent", "single_parent"],
                                      [3, 3], cfg=self._cfg(parent_child_gap_std=5.0),
                                      rng=np.random.RandomState(1))[0]
        assert a.tolist() == b.tolist()

    def test_weight_zero_disables_child_age_matching(self):
        # parent_child_weight 0 -> children still placed (composition holds),
        # just no age optimisation. Composition must still be correct.
        ages = np.array([35, 7, 4])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["single_parent"], sizes=[3],
            cfg=self._cfg(parent_child_weight=0.0))
        m = self._members(ages, hoh, 0)
        assert (m >= 18).sum() == 1 and (m < 18).sum() == 2
