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

    def test_feasibility_fallback_drops_nobody(self, capsys):
        # 1 adult but two couple households requested -> infeasible -> relax.
        ages = np.array([40, 6, 8, 10])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["couple", "couple"], sizes=[2, 2], cfg=self._cfg())
        assert len(hoh) == 4
        assert set(hoh.tolist()) == {0, 1}
        out = capsys.readouterr().out
        assert "relaxed" in out.lower()

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

    def test_weight_zero_disables_child_age_matching(self):
        # parent_child_weight 0 -> children still placed (composition holds),
        # just no age optimisation. Composition must still be correct.
        ages = np.array([35, 7, 4])
        hoh, types = hc.build_bucket_households(
            ages, hh_types=["single_parent"], sizes=[3],
            cfg=self._cfg(parent_child_weight=0.0))
        m = self._members(ages, hoh, 0)
        assert (m >= 18).sum() == 1 and (m < 18).sum() == 2
