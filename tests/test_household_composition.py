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
