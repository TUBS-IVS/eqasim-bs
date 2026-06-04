"""Tests for the joint age x household-size IPF margin (Zensus 1000A-3082).

Verify the scientifically critical properties: the raked joint matches both the
population age-group marginal and the size marginal exactly (so it is consistent
with the existing IPF margins and cannot make the IPF infeasible), and it
preserves the observed age x size correlation from the Zensus seed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.ipf import joint_age_size as jas


class TestAgeGroupLower:
    def test_maps_ages_to_group_lower_bounds(self) -> None:
        # Default bounds (15, 30, 40, 50, 60): the middle band [30,60) is split
        # at the native ALTKL2 edges 40 and 50 so family-size buckets receive
        # specifically young parents (see DEFAULT_AGE_GROUP_BOUNDS rationale).
        assert jas.age_group_lower(0) == 0
        assert jas.age_group_lower(8) == 0
        assert jas.age_group_lower(14) == 0
        assert jas.age_group_lower(15) == 15
        assert jas.age_group_lower(29) == 15
        assert jas.age_group_lower(30) == 30
        assert jas.age_group_lower(39) == 30
        assert jas.age_group_lower(40) == 40
        assert jas.age_group_lower(49) == 40
        assert jas.age_group_lower(50) == 50
        assert jas.age_group_lower(59) == 50
        assert jas.age_group_lower(60) == 60
        assert jas.age_group_lower(95) == 60

    def test_default_bounds_split_middle_band_at_native_edges(self) -> None:
        # The middle band is refined at the native 1000A-3082 ALTKL2 edges 40/50.
        assert jas.DEFAULT_AGE_GROUP_BOUNDS == (15, 30, 40, 50, 60)

    def test_group_lowers_prepends_zero(self) -> None:
        assert jas.group_lowers((15, 30, 60)) == [0, 15, 30, 60]
        assert jas.group_lowers() == [0, 15, 30, 40, 50, 60]


class TestRake2d:
    def test_matches_both_marginals(self) -> None:
        seed = np.array([[1.0, 2.0], [3.0, 4.0]])
        row = np.array([100.0, 200.0])
        col = np.array([120.0, 180.0])
        fitted = jas.rake_2d(seed, row, col)
        assert fitted.sum(axis=1) == pytest.approx(row, rel=1e-6)
        assert fitted.sum(axis=0) == pytest.approx(col, rel=1e-6)

    def test_preserves_correlation_structure(self) -> None:
        # Seed concentrates mass on the diagonal -> after raking to uniform
        # marginals the diagonal must still dominate the off-diagonal.
        seed = np.array([[9.0, 1.0], [1.0, 9.0]])
        row = np.array([100.0, 100.0])
        col = np.array([100.0, 100.0])
        fitted = jas.rake_2d(seed, row, col)
        assert fitted[0, 0] > fitted[0, 1]
        assert fitted[1, 1] > fitted[1, 0]

    def test_zero_seed_cell_does_not_block_positive_targets(self) -> None:
        seed = np.array([[0.0, 0.0], [0.0, 0.0]])
        row = np.array([50.0, 50.0])
        col = np.array([50.0, 50.0])
        fitted = jas.rake_2d(seed, row, col)
        assert fitted.sum(axis=1) == pytest.approx(row, rel=1e-6)
        assert fitted.sum(axis=0) == pytest.approx(col, rel=1e-6)


class TestBuildJointAgeSizeMargin:
    def _inputs(self):
        # One Kreis "03101", two communes. Children (age 6 -> group 0) live in
        # size-5; elderly (age 75 -> group 60) in size-1 and size-5. The size
        # margin keeps size-1 within the adult total so it is feasible under the
        # structural zero (no children in 1-person households).
        df_joint = pd.DataFrame([
            # commune, sex, lower_age, upper_age, hh_size, weight
            ["031010000001", "male", 5, 10, "5", 80.0],
            ["031010000001", "male", 75, 150, "1", 50.0],
            ["031010000001", "male", 75, 150, "5", 10.0],
            ["031010000002", "female", 5, 10, "5", 20.0],
            ["031010000002", "female", 75, 150, "1", 10.0],
            ["031010000002", "female", 75, 150, "5", 20.0],
        ], columns=["commune_id", "sex", "lower_age", "upper_age", "hh_size", "weight"])
        # Population: 100 children (age 6) + 90 elderly (age 75) = 190.
        df_population = pd.DataFrame([
            ["031010000001", 6, 70.0],
            ["031010000002", 6, 30.0],
            ["031010000001", 75, 60.0],
            ["031010000002", 75, 30.0],
        ], columns=["commune_id", "age_class", "weight"])
        # Size margin: size-1 total = 60 (<= 90 adults), size-5 total = 130; 190.
        df_size = pd.DataFrame([
            ["031010000001", "1", 40.0],
            ["031010000001", "5", 90.0],
            ["031010000002", "1", 20.0],
            ["031010000002", "5", 40.0],
        ], columns=["commune_id", "hh_size", "weight"])
        return df_joint, df_population, df_size

    def test_row_and_col_marginals_match_existing_margins(self) -> None:
        df_joint, df_population, df_size = self._inputs()
        out = jas.build_joint_age_size_margin(df_joint, df_population, df_size)

        children = out[out["age_group_lower"] == 0]["weight"].sum()
        elderly = out[out["age_group_lower"] == 60]["weight"].sum()
        assert children == pytest.approx(100.0, rel=1e-4)
        assert elderly == pytest.approx(90.0, rel=1e-4)

        size1 = out[out["hh_size"] == "1"]["weight"].sum()
        size5 = out[out["hh_size"] == "5"]["weight"].sum()
        assert size1 == pytest.approx(60.0, rel=1e-4)
        assert size5 == pytest.approx(130.0, rel=1e-4)

    def test_children_not_in_one_person_households(self) -> None:
        # Structural zero consistent with the IPF hard zero: the children group
        # x size-1 cell must be exactly zero.
        df_joint, df_population, df_size = self._inputs()
        out = jas.build_joint_age_size_margin(df_joint, df_population, df_size)
        cell = out[(out["age_group_lower"] == 0) & (out["hh_size"] == "1")]
        assert float(cell["weight"].iloc[0]) == pytest.approx(0.0, abs=1e-6)

    def test_preserves_observed_age_size_correlation(self) -> None:
        df_joint, df_population, df_size = self._inputs()
        out = jas.build_joint_age_size_margin(df_joint, df_population, df_size)

        def cell(group, size):
            m = out[(out["age_group_lower"] == group) & (out["hh_size"] == size)]
            return float(m["weight"].iloc[0])

        # Children are in size-5 (not size-1); among the elderly, size-1 carries
        # the whole 1-person column.
        assert cell(0, "5") > cell(0, "1")
        assert cell(60, "1") > 0.0

    def test_total_equals_population_total(self) -> None:
        df_joint, df_population, df_size = self._inputs()
        out = jas.build_joint_age_size_margin(df_joint, df_population, df_size)
        assert out["weight"].sum() == pytest.approx(190.0, rel=1e-4)
