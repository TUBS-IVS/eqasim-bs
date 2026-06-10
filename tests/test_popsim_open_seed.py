"""Tests for EntdSource.build_seed: ENTD -> MiD control schema transformation.

These tests verify that:

- build_seed produces H_ID/H_GEW/STAAT on households and H_ID/P_ID/HP_ID/
  P_GEW/HP_ALTER/HP_SEX/STAAT on persons from a small ENTD fixture.
- HP_SEX values are 1 (male) or 2 (female) -- never NaN, never string.
- The seed satisfies the PopulationSim control-spec column expectations.
- ENTD attribute columns (employed, has_license, …) are retained on persons.
- HP_ID is unique across the full seed persons frame.
- Completeness report has completeness_rate=1.0 (no day filter for ENTD).
- Unmapped sex values raise ValueError (fail-fast guard).
- built_seed_columns() returns ENTD_BUILT_SEED_COLUMNS (H_ID schema).
- An end-to-end-ish unit test: build_seed -> expand -> build_persons with a tiny
  fake PopulationSim combined frame; verifies the unified schema output and that
  source_* equal the real ENTD ids (no pseudonymisation).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import assembly, expand
from braunschweig.popsim.seed import CompletenessReport, SeedColumns
from braunschweig.popsim.sources.entd import (
    ENTD_BUILT_SEED_COLUMNS,
    ENTD_SEED_COLUMNS,
    EntdSource,
)
from braunschweig.population import schema


# ---------------------------------------------------------------------------
# Minimal ENTD fixtures (same style as test_popsim_open_entd.py)
# ---------------------------------------------------------------------------

def _entd_households():
    """Three ENTD households in canonical schema (household_id, household_weight, …)."""
    return pd.DataFrame({
        "household_id":       [10, 20, 30],
        "household_weight":   [1.5, 2.0, 1.0],
        "household_size":     [2, 3, 1],
        "number_of_cars":     [1, 2, 0],
        "number_of_bicycles": [2, 1, 0],
        "income_class":       [5, 10, 13],
        "urban_type":         ["central_city", "suburb", "none"],
    })


def _entd_persons():
    """Five ENTD persons linked to the three households."""
    return pd.DataFrame({
        "person_id":               [100, 101, 200, 201, 202],
        "household_id":            [10,  10,  20,  20,  30],
        "person_weight":           [1.5, 1.5, 2.0, 2.0, 1.0],
        "age":                     [40,  10,  25,  55,  70],
        "sex":                     ["male", "female", "male", "female", "male"],
        "employed":                [True, False, True, False, False],
        "studies":                 [False, True, False, False, False],
        "has_license":             [True, False, True, True, False],
        "has_pt_subscription":     [True, False, False, True, False],
        "socioprofessional_class": [3, 8, 5, 7, 7],
    })


# ---------------------------------------------------------------------------
# build_seed: output column schema
# ---------------------------------------------------------------------------

class TestBuildSeedOutputColumns:
    """build_seed must produce MiD-schema columns for both households and persons."""

    def setup_method(self):
        self.src = EntdSource()
        self.hh, self.p, self.report = self.src.build_seed(
            _entd_households(), _entd_persons()
        )

    def test_households_have_H_ID(self):
        assert "H_ID" in self.hh.columns, "seed households must have H_ID"

    def test_households_have_H_GEW(self):
        assert "H_GEW" in self.hh.columns, "seed households must have H_GEW"

    def test_households_have_STAAT(self):
        assert "STAAT" in self.hh.columns, "seed households must have STAAT"
        assert (self.hh["STAAT"] == 1).all()

    def test_persons_have_H_ID(self):
        assert "H_ID" in self.p.columns, "seed persons must have H_ID"

    def test_persons_have_P_ID(self):
        assert "P_ID" in self.p.columns, "seed persons must have P_ID"

    def test_persons_have_HP_ID(self):
        assert "HP_ID" in self.p.columns, "seed persons must have HP_ID"

    def test_persons_have_P_GEW(self):
        assert "P_GEW" in self.p.columns, "seed persons must have P_GEW"

    def test_persons_have_HP_ALTER(self):
        assert "HP_ALTER" in self.p.columns, "seed persons must have HP_ALTER"

    def test_persons_have_HP_SEX(self):
        assert "HP_SEX" in self.p.columns, "seed persons must have HP_SEX"

    def test_persons_have_STAAT(self):
        assert "STAAT" in self.p.columns, "seed persons must have STAAT"
        assert (self.p["STAAT"] == 1).all()


class TestBuildSeedValues:
    """build_seed must correctly map/rename ENTD values to MiD names."""

    def setup_method(self):
        self.src = EntdSource()
        self.hh, self.p, self.report = self.src.build_seed(
            _entd_households(), _entd_persons()
        )

    def test_H_ID_values_are_original_household_ids(self):
        """H_ID on households must equal the original ENTD household_id values."""
        expected = sorted(_entd_households()["household_id"].tolist())
        actual = sorted(self.hh["H_ID"].tolist())
        assert actual == expected, f"H_ID expected {expected}, got {actual}"

    def test_H_GEW_values_are_original_weights(self):
        """H_GEW on households must equal household_weight."""
        original = _entd_households().set_index("household_id")["household_weight"]
        for _, row in self.hh.iterrows():
            expected_w = original[row["H_ID"]]
            assert abs(row["H_GEW"] - expected_w) < 1e-9, (
                f"H_ID={row['H_ID']}: H_GEW={row['H_GEW']} expected {expected_w}"
            )

    def test_HP_SEX_is_1_or_2_only(self):
        """HP_SEX must be exactly 1 (male) or 2 (female), never NaN, never string."""
        assert self.p["HP_SEX"].notna().all(), "HP_SEX must not contain NaN"
        invalid = ~self.p["HP_SEX"].isin([1, 2])
        assert not invalid.any(), (
            f"HP_SEX values must be 1 or 2; got: {self.p.loc[invalid, 'HP_SEX'].unique()}"
        )

    def test_HP_SEX_male_maps_to_1(self):
        """ENTD sex='male' must map to HP_SEX=1."""
        original_p = _entd_persons()
        hid_map = dict(zip(original_p["person_id"], original_p["sex"]))
        for _, row in self.p.iterrows():
            original_sex = hid_map[row["P_ID"]]
            if original_sex == "male":
                assert row["HP_SEX"] == 1, (
                    f"P_ID={row['P_ID']}: sex=male must map to HP_SEX=1, got {row['HP_SEX']}"
                )

    def test_HP_SEX_female_maps_to_2(self):
        """ENTD sex='female' must map to HP_SEX=2."""
        original_p = _entd_persons()
        hid_map = dict(zip(original_p["person_id"], original_p["sex"]))
        for _, row in self.p.iterrows():
            original_sex = hid_map[row["P_ID"]]
            if original_sex == "female":
                assert row["HP_SEX"] == 2, (
                    f"P_ID={row['P_ID']}: sex=female must map to HP_SEX=2, got {row['HP_SEX']}"
                )

    def test_HP_ALTER_values_are_original_ages(self):
        """HP_ALTER on persons must equal the original ENTD age values."""
        original_p = _entd_persons().set_index("person_id")["age"]
        for _, row in self.p.iterrows():
            expected_age = original_p[row["P_ID"]]
            assert row["HP_ALTER"] == expected_age, (
                f"P_ID={row['P_ID']}: HP_ALTER={row['HP_ALTER']} expected {expected_age}"
            )

    def test_P_GEW_values_are_original_person_weights(self):
        """P_GEW on persons must equal person_weight."""
        original_p = _entd_persons().set_index("person_id")["person_weight"]
        for _, row in self.p.iterrows():
            expected_w = original_p[row["P_ID"]]
            assert abs(row["P_GEW"] - expected_w) < 1e-9, (
                f"P_ID={row['P_ID']}: P_GEW={row['P_GEW']} expected {expected_w}"
            )

    def test_P_ID_values_are_original_person_ids(self):
        """P_ID on persons must equal the original ENTD person_id values."""
        expected = sorted(_entd_persons()["person_id"].tolist())
        actual = sorted(self.p["P_ID"].tolist())
        assert actual == expected

    def test_HP_ID_is_unique(self):
        """HP_ID must be unique across the full seed persons frame."""
        assert not self.p["HP_ID"].duplicated().any(), "HP_ID must be globally unique"

    def test_HP_ID_is_integer(self):
        """HP_ID must be an integer type."""
        assert np.issubdtype(self.p["HP_ID"].dtype, np.integer), (
            f"HP_ID must be integer, got {self.p['HP_ID'].dtype}"
        )


class TestBuildSeedControlSpecCompatibility:
    """The seed must satisfy PopulationSim control-spec column expectations."""

    def setup_method(self):
        src = EntdSource()
        self.hh, self.p, _ = src.build_seed(_entd_households(), _entd_persons())

    def test_persons_have_HP_ALTER_for_age_controls(self):
        """HP_ALTER is required by the control-spec age bands (M_AGE_*, F_AGE_*)."""
        assert "HP_ALTER" in self.p.columns
        assert self.p["HP_ALTER"].notna().all()
        assert (self.p["HP_ALTER"] >= 0).all()

    def test_persons_have_HP_SEX_for_sex_controls(self):
        """HP_SEX is required by the control-spec sex expression (HP_SEX==1 / ==2)."""
        assert "HP_SEX" in self.p.columns
        assert self.p["HP_SEX"].isin([1, 2]).all()

    def test_persons_have_P_GEW_for_total_person_control(self):
        """P_GEW is required for the PopulationSim person weight control."""
        assert "P_GEW" in self.p.columns
        assert (self.p["P_GEW"] > 0).all()

    def test_households_have_H_GEW_for_household_weight_control(self):
        """H_GEW is required for the PopulationSim household weight control."""
        assert "H_GEW" in self.p.columns or "H_GEW" in self.hh.columns
        assert (self.hh["H_GEW"] > 0).all()


class TestBuildSeedAttributeRetention:
    """ENTD attribute columns must be retained on the seed persons frame."""

    def setup_method(self):
        src = EntdSource()
        _, self.p, _ = src.build_seed(_entd_households(), _entd_persons())

    def test_employed_retained(self):
        assert "employed" in self.p.columns

    def test_studies_retained(self):
        assert "studies" in self.p.columns

    def test_has_license_retained(self):
        assert "has_license" in self.p.columns

    def test_has_pt_subscription_retained(self):
        assert "has_pt_subscription" in self.p.columns

    def test_socioprofessional_class_retained(self):
        assert "socioprofessional_class" in self.p.columns

    def test_original_sex_string_retained(self):
        """The original ENTD sex string ('male'/'female') must also be present.

        expand.map_demographics derives sex from HP_SEX, but the original string
        column can be carried as an extra for traceability.
        """
        assert "sex" in self.p.columns
        assert self.p["sex"].isin(["male", "female"]).all()


class TestBuildSeedCompletenessReport:
    """build_seed returns a CompletenessReport with completeness_rate=1.0."""

    def test_report_is_completeness_report_type(self):
        src = EntdSource()
        _, _, report = src.build_seed(_entd_households(), _entd_persons())
        assert isinstance(report, CompletenessReport)

    def test_report_completeness_rate_is_1(self):
        """ENTD has no day-of-week filter; every household is complete."""
        src = EntdSource()
        _, _, report = src.build_seed(_entd_households(), _entd_persons())
        assert report.completeness_rate == 1.0, (
            f"Expected completeness_rate=1.0, got {report.completeness_rate}"
        )

    def test_report_no_households_dropped(self):
        src = EntdSource()
        hh = _entd_households()
        _, _, report = src.build_seed(hh, _entd_persons())
        assert report.n_households_dropped == 0
        assert report.n_households_complete == len(hh)


# ---------------------------------------------------------------------------
# build_seed: fail-fast on unmapped sex value
# ---------------------------------------------------------------------------

class TestBuildSeedSexFailFast:
    """build_seed must raise ValueError immediately when sex contains unmapped values."""

    def test_unmapped_sex_raises_value_error(self):
        hh = _entd_households()
        p = _entd_persons().copy()
        p.loc[0, "sex"] = "unknown"  # introduce an unmapped value
        src = EntdSource()
        with pytest.raises(ValueError, match="unmapped"):
            src.build_seed(hh, p)

    def test_diverse_sex_raises(self):
        """'diverse' is not in the accepted set."""
        hh = _entd_households()
        p = _entd_persons().copy()
        p.loc[2, "sex"] = "diverse"
        src = EntdSource()
        with pytest.raises(ValueError, match="diverse"):
            src.build_seed(hh, p)

    def test_valid_seed_does_not_raise(self):
        """Fixture with only male/female must not raise."""
        src = EntdSource()
        src.build_seed(_entd_households(), _entd_persons())  # must not raise


# ---------------------------------------------------------------------------
# built_seed_columns: returns ENTD_BUILT_SEED_COLUMNS (H_ID schema)
# ---------------------------------------------------------------------------

class TestBuiltSeedColumns:
    """EntdSource.built_seed_columns() returns the MiD-schema SeedColumns."""

    def test_built_seed_columns_returns_seed_columns_instance(self):
        src = EntdSource()
        assert isinstance(src.built_seed_columns(), SeedColumns)

    def test_built_seed_columns_household_id_is_H_ID(self):
        src = EntdSource()
        assert src.built_seed_columns().household_id == "H_ID"

    def test_built_seed_columns_person_household_id_is_H_ID(self):
        src = EntdSource()
        assert src.built_seed_columns().person_household_id == "H_ID"

    def test_built_seed_columns_household_weight_is_H_GEW(self):
        src = EntdSource()
        assert src.built_seed_columns().household_weight == "H_GEW"

    def test_built_seed_columns_person_id_is_P_ID(self):
        src = EntdSource()
        assert src.built_seed_columns().person_id == "P_ID"

    def test_built_seed_columns_age_is_HP_ALTER(self):
        src = EntdSource()
        assert src.built_seed_columns().age == "HP_ALTER"

    def test_built_seed_columns_sex_is_HP_SEX(self):
        src = EntdSource()
        assert src.built_seed_columns().sex == "HP_SEX"

    def test_built_seed_columns_no_day_filter(self):
        src = EntdSource()
        assert src.built_seed_columns().day_filter_col is None

    def test_built_seed_columns_equals_constant(self):
        src = EntdSource()
        assert src.built_seed_columns() == ENTD_BUILT_SEED_COLUMNS

    def test_seed_columns_still_returns_entd_canonical(self):
        """seed_columns() must still return the canonical ENTD column names."""
        src = EntdSource()
        assert src.seed_columns().household_id == "household_id"
        assert src.seed_columns().age == "age"
        assert src.seed_columns() == ENTD_SEED_COLUMNS


# ---------------------------------------------------------------------------
# End-to-end unit test: build_seed -> simulate PopulationSim output ->
# expand_to_persons -> map_demographics -> build_persons with EntdSource mapper
# ---------------------------------------------------------------------------

def _make_combined_popsim_output(seed_households: pd.DataFrame) -> pd.DataFrame:
    """Simulate a tiny PopulationSim combined output frame.

    PopulationSim writes one row per synthetic household with:
    - ZENSUS100m: the 100 m cell id the household was placed in
    - H_ID: the donor household id (from the seed)
    - ZENSUS1km: the 1 km parent cell
    - RegionalSchlussel_ARS: the 12-digit ARS (from stage.py's post-merge join)

    We place each seed household once in a single cell (cell "C1") in BS city
    (ARS "031010000000" -> commune_id "03101000" -> departement_id "03101").
    """
    rows = []
    for _, row in seed_households.iterrows():
        rows.append({
            "ZENSUS100m": "C1",
            "ZENSUS1km": "K1",
            "H_ID": row["H_ID"],
            "RegionalSchlussel_ARS": "031010000000",
        })
    return pd.DataFrame(rows)


class TestBuildSeedExpandBuildPersonsIntegration:
    """End-to-end unit test: ENTD seed -> expand -> assembly.build_persons.

    No PopulationSim subprocess is invoked.  We manually construct the
    merged PopulationSim output frame (as stage.py would produce it) and
    run the expand + build_persons path with EntdSource.map_person_attributes.
    """

    def setup_method(self):
        self.src = EntdSource()
        self.entd_hh = _entd_households()
        self.entd_p = _entd_persons()

        # Build the seed (MiD schema)
        self.seed_hh, self.seed_p, self.report = self.src.build_seed(
            self.entd_hh, self.entd_p
        )

        # Simulate a tiny PopulationSim output: one synthetic household per donor
        self.combined = _make_combined_popsim_output(self.seed_hh)

        # Run assembly.build_persons with EntdSource mapper and ENTD donor frames
        self.persons, self.pseudonym_map = assembly.build_persons(
            self.combined,
            self.entd_hh,    # donor households (original ENTD canonical schema)
            self.seed_p,     # donor persons (post-build_seed: has H_ID, P_ID, HP_ALTER, HP_SEX + attrs)
            attribute_mapper=self.src.map_person_attributes,
            pseudonymise=False,
            inkar_scale=None,  # no INKAR scaling in unit test
        )

    def test_persons_output_has_expected_row_count(self):
        """One row per (synthetic household, donor person); 3 hh * persons each."""
        # ENTD fixture: hh 10 -> 2 persons, hh 20 -> 2 persons, hh 30 -> 1 person
        # Each hh placed once -> 2 + 2 + 1 = 5 persons total.
        assert len(self.persons) == 5, (
            f"Expected 5 persons (one per donor person), got {len(self.persons)}"
        )

    def test_persons_have_age_column(self):
        assert "age" in self.persons.columns
        assert self.persons["age"].notna().all()

    def test_persons_have_sex_column(self):
        assert "sex" in self.persons.columns
        assert self.persons["sex"].isin(["male", "female", "unknown"]).all()

    def test_persons_have_employed(self):
        assert "employed" in self.persons.columns

    def test_persons_have_has_pt_subscription(self):
        assert "has_pt_subscription" in self.persons.columns

    def test_persons_have_socioprofessional_class(self):
        assert "socioprofessional_class" in self.persons.columns

    def test_persons_have_household_income_eur(self):
        assert "household_income_eur" in self.persons.columns

    def test_source_person_id_is_entd_p_id(self):
        """source_person_id must equal the ENTD donor P_ID (no pseudonymisation)."""
        expected_pids = set(self.entd_p["person_id"])
        actual_src_pids = set(self.persons["source_person_id"].unique())
        assert actual_src_pids == expected_pids, (
            f"source_person_id should be ENTD person_id values {expected_pids}, "
            f"got {actual_src_pids}"
        )

    def test_source_household_id_is_entd_hh_id(self):
        """source_household_id must equal the ENTD donor H_ID (no pseudonymisation)."""
        expected_hhids = set(self.entd_hh["household_id"])
        actual_src_hhids = set(self.persons["source_household_id"].unique())
        assert actual_src_hhids == expected_hhids, (
            f"source_household_id should be ENTD household_id values {expected_hhids}, "
            f"got {actual_src_hhids}"
        )

    def test_pseudonym_map_is_empty(self):
        """No pseudonymisation for ENTD (open data); map must be empty."""
        assert len(self.pseudonym_map) == 0, (
            f"pseudonym_map should be empty for ENTD (pseudonymise=False), "
            f"got {len(self.pseudonym_map)} rows"
        )

    def test_persons_schema_validates(self):
        """assembly.build_persons must produce a frame that passes schema validation."""
        # schema.validate_person_columns raises on missing required columns.
        # It is called inside build_persons itself; if we got here without an
        # exception the schema is valid.  This test documents that expectation.
        schema.validate_person_columns(self.persons.columns)

    def test_commune_id_derived_from_ars(self):
        """commune_id must be derived from the ARS (BS city = '03101000')."""
        assert "commune_id" in self.persons.columns
        assert (self.persons["commune_id"] == "03101000").all(), (
            f"commune_id expected '03101000' (from ARS '031010000000'), "
            f"got: {self.persons['commune_id'].unique()}"
        )

    def test_is_urban_resident_is_true_for_bs_city(self):
        """is_urban_resident must be True for BS city (departement_id='03101')."""
        assert "is_urban_resident" in self.persons.columns
        assert (self.persons["is_urban_resident"] == True).all()

    def test_age_values_match_entd_donor_ages(self):
        """All age values must come from the ENTD donor (no invented ages)."""
        expected_ages = set(self.entd_p["age"])
        actual_ages = set(self.persons["age"])
        assert actual_ages.issubset(expected_ages), (
            f"Output ages {actual_ages} contain values not in donor {expected_ages}"
        )

    def test_weight_is_1_0(self):
        """weight must be 1.0 (already expanded population)."""
        assert "weight" in self.persons.columns
        assert (self.persons["weight"] == 1.0).all()
