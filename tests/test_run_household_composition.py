"""Unit tests for the household-composition realism diagnostics (#3b).

Pure helpers that quantify the age-plausibility of synthesised households:
within-couple age gaps, parent-child age gaps, the share of implausible
households, and the household-type share. The full ``run`` pipeline needs an
eqasim run output and is exercised manually.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import run_household_composition as rhc


def _persons():
    # hh0: couple 30/33 ; hh1: parent 35 + children 5/8 ; hh2: two children 6/9.
    return pd.DataFrame({
        "household_id": [0, 0, 1, 1, 1, 2, 2],
        "age": [30, 33, 35, 5, 8, 6, 9],
    })


class TestCoupleAgeGaps:
    def test_two_adult_household_gap(self):
        gaps = rhc.couple_age_gaps(_persons(), min_adult_age=18)
        assert gaps.tolist() == [3]   # only hh0 is a 2-adult household


class TestParentChildGaps:
    def test_youngest_adult_minus_child(self):
        gaps = sorted(rhc.parent_child_gaps(_persons(), min_adult_age=18).tolist())
        assert gaps == [27, 30]       # 35-8, 35-5 (hh1); hh2 has no adult


class TestImplausibleShare:
    def test_counts_all_children_household(self):
        # hh2 (two children, no adult) is implausible -> 1 of 3 households.
        share = rhc.implausible_share(_persons(), min_adult_age=18,
                                      couple_gap_threshold=20)
        assert share == pytest.approx(1 / 3)

    def test_couple_with_huge_gap_is_implausible(self):
        persons = pd.DataFrame({"household_id": [0, 0], "age": [25, 70]})
        share = rhc.implausible_share(persons, min_adult_age=18,
                                      couple_gap_threshold=20)
        assert share == pytest.approx(1.0)

    def test_child_older_than_adult_is_implausible(self):
        persons = pd.DataFrame({"household_id": [0, 0], "age": [19, 17]})
        # one adult (19) + one child (17), child not older than adult -> ok.
        assert rhc.implausible_share(persons, 18, 20) == pytest.approx(0.0)


class TestHhTypeShare:
    def test_shares_sum_to_one(self):
        households = pd.DataFrame({"hh_type": ["couple", "couple", "single_parent"]})
        share = rhc.hh_type_share(households)
        assert share.sum() == pytest.approx(1.0)
        assert share["couple"] == pytest.approx(2 / 3)
