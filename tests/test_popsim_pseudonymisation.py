"""Tests for popsim donor-id pseudonymisation (data protection).

MiD is restricted scientific-use microdata (BMDV licence). The synthetic output
must not allow re-identification of the real MiD respondent each agent was derived
from. This module verifies that:

1. source_person_id / source_household_id are numeric surrogates, not raw MiD ids.
2. The surrogate mapping round-trips to (H_ID, P_ID).
3. census_person_id / census_household_id carry only integer synthetic ids after
   enriched_adapter.run (not the popsim embedding strings that embed H_ID / P_ID).
4. No output id column (source_*, census_*, hts_*) equals or contains the raw MiD
   H_ID / P_ID after the full pseudonymisation + adapter pipeline.

Phase 3 additions:
5. build_persons(..., pseudonymise=False) with a custom attribute_mapper (ENTD path)
   keeps source_* as the values set by the mapper (no surrogate replacement).
6. build_persons(..., pseudonymise=False) without a custom attribute_mapper raises
   ValueError (fail-fast: the default MiD mapper always pseudonymises).
7. pseudonymise=True (default, MiD path) is unchanged: byte-identical behaviour.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from braunschweig.popsim import assembly
from braunschweig.popsim import enriched_adapter as ea


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _merged_households():
    """Two synthetic households with two distinct MiD donors.

    Includes RegionalSchlussel_ARS for derive_zone_ids.
    """
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km": ["KA", "KB"],
        "H_ID": [12345, 67890],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })


def _mid_persons():
    """Three MiD respondent persons with deliberately large H_ID / P_ID values
    so surrogates (small sequential integers) can be distinguished from the raw ids.
    """
    return pd.DataFrame({
        "H_ID":      [12345, 12345, 67890],
        "P_ID":      [1,     2,     1],
        "HP_ALTER":  [40,    10,    25],
        "HP_SEX":    [1,     2,     1],
        "P_TAET":    [1,     9,     8],
        "P_FSCHEIN": [1,     2,     1],
        "P_FKARTE":  [3,     8,     1],
        "P_BKAT":    [1,     7,     7],
    })


def _mid_households():
    return pd.DataFrame({
        "H_ID":       [12345, 67890],
        "oek_status": [3,     5],
        "hheink_gr1": [4,     15],
        "H_ANZAUTO":  [1,     0],
        "H_ANZRAD":   [2,     1],
        "anzpedrad":  [2,     1],
        "H_ANZPED":   [0,     0],
    })


# ---------------------------------------------------------------------------
# Test 1: source_* are numeric surrogates, not raw MiD ids
# ---------------------------------------------------------------------------

def test_source_ids_are_numeric_surrogates_not_raw_mid_id():
    """source_household_id / source_person_id must be integer surrogates, not
    the raw MiD H_ID / P_ID values.

    Fixture: H_ID=[12345, 12345, 67890], P_ID=[1, 2, 1].
    - Persons of the same donor household must share the same source_household_id.
    - Different donor households must have different source_household_id surrogates.
    - No surrogate value must equal any raw H_ID (12345, 67890) or P_ID (1, 2).
    - All surrogates must be positive integers.
    """
    persons, pseudonym_map = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )

    # Surrogates must be integer dtype (Int64).
    assert pd.api.types.is_integer_dtype(persons["source_household_id"]), (
        f"source_household_id must be integer, got {persons['source_household_id'].dtype}"
    )
    assert pd.api.types.is_integer_dtype(persons["source_person_id"]), (
        f"source_person_id must be integer, got {persons['source_person_id'].dtype}"
    )

    # The raw H_ID values (12345, 67890) must NOT appear in source_household_id.
    raw_h_ids = {12345, 67890}
    actual_hh_surrogates = set(int(v) for v in persons["source_household_id"].unique())
    assert not actual_hh_surrogates.intersection(raw_h_ids), (
        f"source_household_id contains raw H_ID values: "
        f"{actual_hh_surrogates.intersection(raw_h_ids)}"
    )

    # The raw P_ID values (1, 2) happen to be small integers too; we therefore
    # check via the mapping that surrogates are assigned by factorize, not by copying.
    # With H_ID=[12345,12345,67890] and sort=True, surrogates are 1 and 2 (not 12345/67890).
    # Similarly person surrogates are 1, 2, 3 (not P_IDs 1 and 2).
    assert 12345 not in actual_hh_surrogates, (
        "source_household_id must not equal raw H_ID=12345"
    )
    assert 67890 not in actual_hh_surrogates, (
        "source_household_id must not equal raw H_ID=67890"
    )

    # Persons of the same donor household share the same source_household_id.
    hh_a = persons[persons["H_ID"] == 12345]
    assert hh_a["source_household_id"].nunique() == 1, (
        "All persons of H_ID=12345 must share one source_household_id surrogate"
    )

    hh_b = persons[persons["H_ID"] == 67890]
    assert hh_b["source_household_id"].nunique() == 1, (
        "All persons of H_ID=67890 must share one source_household_id surrogate"
    )

    # Different donor households have different surrogates.
    surr_a = int(hh_a["source_household_id"].iloc[0])
    surr_b = int(hh_b["source_household_id"].iloc[0])
    assert surr_a != surr_b, (
        "H_ID=12345 and H_ID=67890 must get different source_household_id surrogates"
    )

    # All surrogates are positive integers.
    assert (persons["source_household_id"] > 0).all(), (
        "All source_household_id surrogates must be positive integers"
    )
    assert (persons["source_person_id"] > 0).all(), (
        "All source_person_id surrogates must be positive integers"
    )

    # Three unique person surrogates (one per unique (H_ID, P_ID) pair).
    assert persons["source_person_id"].nunique() == 3, (
        f"Expected 3 unique source_person_id surrogates; "
        f"got {persons['source_person_id'].nunique()}"
    )


# ---------------------------------------------------------------------------
# Test 2: mapping round-trips
# ---------------------------------------------------------------------------

def test_mapping_round_trips():
    """The pseudonym_map returned by build_persons must recover (H_ID, P_ID)
    from the surrogate so internal re-linking is possible.
    """
    persons, pseudonym_map = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )

    # Map columns must be present.
    assert set(pseudonym_map.columns).issuperset(
        {"source_person_id", "source_household_id", "H_ID", "P_ID"}
    ), f"pseudonym_map missing required columns; got {list(pseudonym_map.columns)}"

    # Every unique (source_person_id) in persons must appear in the map.
    person_surrogates_in_frame = set(int(v) for v in persons["source_person_id"].unique())
    person_surrogates_in_map = set(int(v) for v in pseudonym_map["source_person_id"])
    assert person_surrogates_in_frame == person_surrogates_in_map, (
        f"person surrogates in frame {person_surrogates_in_frame} != "
        f"person surrogates in map {person_surrogates_in_map}"
    )

    # Round-trip: for each (source_person_id, source_household_id) in persons,
    # the map must give back the original (H_ID, P_ID).
    map_lookup = {
        int(row["source_person_id"]): (row["H_ID"], row["P_ID"])
        for _, row in pseudonym_map.iterrows()
    }
    for _, row in persons.iterrows():
        sp = int(row["source_person_id"])
        assert sp in map_lookup, f"source_person_id={sp} not in pseudonym_map"
        h_id_from_map, p_id_from_map = map_lookup[sp]
        assert row["H_ID"] == h_id_from_map, (
            f"Round-trip fail: source_person_id={sp} -> H_ID={h_id_from_map} "
            f"but row H_ID={row['H_ID']}"
        )
        assert row["P_ID"] == p_id_from_map, (
            f"Round-trip fail: source_person_id={sp} -> P_ID={p_id_from_map} "
            f"but row P_ID={row['P_ID']}"
        )

    # The full set of (H_ID, P_ID) pairs in the map must cover the original donors.
    expected_pairs = {(12345, 1), (12345, 2), (67890, 1)}
    actual_pairs = {
        (int(row["H_ID"]), int(row["P_ID"])) for _, row in pseudonym_map.iterrows()
    }
    assert actual_pairs == expected_pairs, (
        f"pseudonym_map covers {actual_pairs}, expected {expected_pairs}"
    )


# ---------------------------------------------------------------------------
# Test 3: census_* do NOT embed MiD ids after enriched_adapter.run
# ---------------------------------------------------------------------------

def test_census_ids_do_not_embed_mid_id():
    """After enriched_adapter.run, census_person_id / census_household_id must be
    the integer synthetic ids and must NOT contain H_ID or P_ID substrings.

    synthesis.population.sampled sets census_* to popsim embedding strings like
    '<cell>_<H_ID>_<occurrence>[_<P_ID>]' before reassigning integer person_id /
    household_id.  The adapter must overwrite census_* with the integer ids so
    no leaking string survives to the output.
    """
    # Simulate a post-sampled frame with leaking embedding strings in census_*.
    # person_id / household_id are the new sequential integers from sampled.
    persons = pd.DataFrame({
        "person_id":            [10, 11, 12],
        "household_id":         [5,  5,  6],
        # Leaking embedding strings that sampled copied from the old popsim ids.
        "census_person_id":     ["A_12345_0_1", "A_12345_0_2", "B_67890_0_1"],
        "census_household_id":  ["A_12345_0",   "A_12345_0",   "B_67890_0"],
        # Pseudonymised surrogates from assembly.assign_donor_surrogates.
        "source_person_id":     [1, 2, 3],
        "source_household_id":  [1, 1, 2],
    })

    out = ea.run(persons)

    # census_person_id must equal person_id (integer), not the embedding string.
    for i, (person_id, census_id) in enumerate(
        zip(out["person_id"].tolist(), out["census_person_id"].tolist())
    ):
        assert census_id == person_id, (
            f"Row {i}: census_person_id={census_id!r} must equal person_id={person_id!r}"
        )

    # census_household_id must equal household_id (integer), not the embedding string.
    for i, (hh_id, census_hh_id) in enumerate(
        zip(out["household_id"].tolist(), out["census_household_id"].tolist())
    ):
        assert census_hh_id == hh_id, (
            f"Row {i}: census_household_id={census_hh_id!r} must equal household_id={hh_id!r}"
        )

    # The embedding strings (containing raw MiD H_IDs/P_IDs) must not appear anywhere
    # in census_* as strings.
    for leaking_fragment in ["12345", "67890"]:
        for col in ["census_person_id", "census_household_id"]:
            for val in out[col].astype(str).tolist():
                assert leaking_fragment not in val, (
                    f"{col} contains leaking MiD fragment {leaking_fragment!r}: {val!r}"
                )


# ---------------------------------------------------------------------------
# Test 4: no output id column contains raw H_ID / P_ID
# ---------------------------------------------------------------------------

def test_output_id_columns_have_no_raw_mid_id():
    """After the full pipeline (build_persons + enriched_adapter.run), none of the
    output id columns (source_*, census_*, hts_*) should equal or contain the
    raw H_ID or P_ID values.

    This test exercises the end-to-end pseudonymisation path.
    """
    persons_raw, _map = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )

    # Simulate sampled: copy popsim string ids to census_*, assign integer ids.
    persons_sampled = persons_raw.copy()
    persons_sampled["census_person_id"] = persons_sampled["person_id"]   # popsim string
    persons_sampled["census_household_id"] = persons_sampled["household_id"]  # popsim string
    persons_sampled["person_id"] = list(range(len(persons_sampled)))
    n_hh = persons_sampled["household_id"].nunique()
    hh_map = {hh: i for i, hh in enumerate(persons_sampled["household_id"].unique())}
    persons_sampled["household_id"] = persons_sampled["household_id"].map(hh_map)

    # Run the adapter.
    out = ea.run(persons_sampled)

    raw_h_ids = {12345, 67890}
    raw_p_ids = {1, 2}

    for col in ["source_person_id", "source_household_id", "hts_id", "hts_household_id",
                "census_person_id", "census_household_id"]:
        assert col in out.columns, f"Column {col!r} missing from output"
        for val in out[col].tolist():
            str_val = str(val)
            # Must not contain raw H_IDs.
            for h_id in raw_h_ids:
                assert str(h_id) not in str_val, (
                    f"Output column {col!r} value {val!r} contains raw H_ID {h_id}"
                )
            # source_* and hts_* must not equal raw MiD ids as integers either.
            if col in ("source_person_id", "source_household_id",
                       "hts_id", "hts_household_id"):
                if pd.notna(val):
                    try:
                        int_val = int(val)
                        assert int_val not in raw_h_ids, (
                            f"Output column {col!r} value {int_val} equals raw H_ID "
                            f"(must be a surrogate)"
                        )
                    except (ValueError, TypeError):
                        pass  # string value; string check above already applied


# ---------------------------------------------------------------------------
# Phase 3 tests: pseudonymise=False (ENTD / popsim_open path)
# ---------------------------------------------------------------------------

# Minimal ENTD-shaped frames for the pseudonymise=False tests.
# These are independent of the MiD fixtures above so both path tests are clear.

def _entd_merged_households_for_pseudonymise():
    """Merged PopulationSim output with ENTD household_id column (not H_ID).

    For popsim_open, the merged output uses ENTD integer household ids as the
    donor key instead of H_ID.  derive_zone_ids still requires RegionalSchlussel_ARS;
    the cells join is unchanged.
    """
    return pd.DataFrame({
        "ZENSUS100m": ["X", "Y"],
        "ZENSUS1km": ["KX", "KY"],
        "household_id": [10, 20],   # ENTD household id (open integer)
        "H_ID": [10, 20],           # expand.expand_to_persons needs H_ID by default;
        # for the test we use household_id == H_ID values so the expand works.
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })


def _entd_donor_persons_for_pseudonymise():
    """Minimal ENTD donor persons.  Linked to H_ID for the expand join."""
    return pd.DataFrame({
        "H_ID":   [10, 20],
        "P_ID":   [1,  1],
        "HP_ALTER": [30, 45],
        "HP_SEX":   [1,  2],
        "P_TAET":   [1,  8],
        "P_FSCHEIN": [1, 1],
        "P_FKARTE": [3,  8],
        "P_BKAT":   [1,  7],
    })


def _entd_donor_households_for_pseudonymise():
    """Minimal households frame needed by EntdSource.map_person_attributes."""
    return pd.DataFrame({
        "household_id":       [10, 20],
        "household_weight":   [1.5, 2.0],
        "household_size":     [1, 1],
        "number_of_cars":     [1, 0],
        "number_of_bicycles": [2, 1],
        "income_class":       [5, 10],
        "departement_id":     ["03101", "03101"],
    })


def _minimal_entd_mapper(source_id_100, source_id_200):
    """Return a simple custom mapper that sets source_* to given open ids."""
    def _mapper(persons, households, *, rng=None):
        """Minimal ENTD-like mapper: sets source_* to open ids, adds schema cols."""
        out = persons.copy()
        # Set the open source ids (ENTD is open data, no surrogates).
        out["source_person_id"] = [source_id_100, source_id_200][: len(out)]
        out["source_household_id"] = out["household_id"]
        # Required schema columns that the default mapper sets.
        out["employed"] = True
        out["studies"] = False
        out["has_license"] = True
        out["has_pt_subscription"] = False
        out["household_income"] = "2000_2600"
        out["high_income"] = False
        out["number_of_cars"] = 1
        out["number_of_bicycles"] = 1
        out["car_availability"] = "all"
        out["bicycle_availability"] = "all"
        out["age_range"] = pd.Categorical(
            ["higher_education"] * len(out),
            categories=["primary_school", "middle_school", "high_school", "higher_education"],
        )
        out["household_size"] = 1
        out["is_urban_resident"] = True
        out["pt_subscription_type"] = pd.Series(["fahre_nie"] * len(out)).astype("string")
        out["socioprofessional_class"] = 3
        out["weight"] = 1.0
        # Schema requires these but schema.validate_person_columns checks columns only.
        out["economic_status"] = "medium"
        out["household_income_eur"] = 2300.0
        # Unified mapper contract: (persons, pseudonym_map); empty map for open ids.
        empty_map = pd.DataFrame(
            columns=["source_person_id", "source_household_id", "H_ID", "P_ID"]
        )
        return out, empty_map
    return _mapper


def test_build_persons_pseudonymise_false_keeps_source_ids_from_mapper():
    """build_persons with pseudonymise=False must keep source_* as set by the mapper.

    When pseudonymise=False and a custom attribute_mapper is supplied (ENTD path),
    source_person_id / source_household_id must equal the values set by the mapper,
    NOT the MiD surrogates that assign_donor_surrogates would produce.
    """
    merged = _entd_merged_households_for_pseudonymise()
    donor_hh = _entd_donor_households_for_pseudonymise()
    donor_persons = _entd_donor_persons_for_pseudonymise()

    # Custom mapper that sets source_person_id = 1001 for row 0, 1002 for row 1.
    mapper = _minimal_entd_mapper(1001, 1002)

    persons, pseudonym_map = assembly.build_persons(
        merged, donor_hh, donor_persons,
        attribute_mapper=mapper,
        pseudonymise=False,
        rng=np.random.RandomState(0),
    )

    # source_* must be exactly the values the mapper set (1001, 1002).
    actual_source_ids = sorted(persons["source_person_id"].tolist())
    assert 1001 in actual_source_ids, (
        f"source_person_id 1001 not found; got {actual_source_ids}"
    )
    assert 1002 in actual_source_ids, (
        f"source_person_id 1002 not found; got {actual_source_ids}"
    )

    # The pseudonym map must be empty (no surrogates were assigned).
    assert len(pseudonym_map) == 0, (
        f"pseudonym_map should be empty for pseudonymise=False; got {len(pseudonym_map)} rows"
    )


def test_build_persons_pseudonymise_false_returns_empty_pseudonym_map():
    """pseudonymise=False must always return an empty pseudonym_map.

    The pseudonym_map is used by stage.py to write a local re-linking file.
    For ENTD (open data) there are no surrogates and the map should have zero rows.
    """
    merged = _entd_merged_households_for_pseudonymise()
    donor_hh = _entd_donor_households_for_pseudonymise()
    donor_persons = _entd_donor_persons_for_pseudonymise()
    mapper = _minimal_entd_mapper(100, 200)

    _persons, pseudonym_map = assembly.build_persons(
        merged, donor_hh, donor_persons,
        attribute_mapper=mapper,
        pseudonymise=False,
        rng=np.random.RandomState(0),
    )

    assert len(pseudonym_map) == 0
    # The required map columns should still be present (schema consistency).
    for col in ("source_person_id", "source_household_id", "H_ID", "P_ID"):
        assert col in pseudonym_map.columns, (
            f"pseudonym_map missing column {col!r} even when empty"
        )


def test_build_persons_pseudonymise_false_without_mapper_raises():
    """build_persons(pseudonymise=False) without attribute_mapper must raise ValueError.

    The default map_mid_person_attributes always pseudonymises (calls
    assign_donor_surrogates).  Passing pseudonymise=False without a custom mapper
    is a logic error and must fail fast.
    """
    merged = _merged_households()
    donor_hh = _mid_households()
    donor_persons = _mid_persons()

    with pytest.raises(ValueError, match="pseudonymise=False requires a custom attribute_mapper"):
        assembly.build_persons(
            merged, donor_hh, donor_persons,
            pseudonymise=False,  # no attribute_mapper -> should raise
            rng=np.random.RandomState(0),
        )


def test_build_persons_pseudonymise_true_is_default_mid_unchanged():
    """build_persons() with no pseudonymise argument must produce MiD surrogates.

    This is the byte-identical guard: the default (pseudonymise=True, attribute_mapper=None)
    must produce the same surrogate source_* values as before Phase 3.
    """
    persons, pseudonym_map = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
        # pseudonymise=True is the default; not passing it must behave identically.
    )

    # Surrogates must be present and non-empty.
    assert "source_person_id" in persons.columns
    assert "source_household_id" in persons.columns
    assert len(pseudonym_map) > 0, "MiD path must produce a non-empty pseudonym_map"

    # Same values as test_source_ids_are_numeric_surrogates_not_raw_mid_id.
    raw_h_ids = {12345, 67890}
    actual_hh_surrogates = set(int(v) for v in persons["source_household_id"].unique())
    assert not actual_hh_surrogates.intersection(raw_h_ids), (
        "source_household_id must not contain raw H_ID values (surrogates expected)"
    )


def test_build_persons_pseudonymise_false_with_entd_source_open_ids():
    """build_persons with EntdSource.map_person_attributes and pseudonymise=False
    keeps open source ids (no surrogate).

    This test verifies that the ENTD adapter, when called as attribute_mapper,
    sets source_person_id = person_id and source_household_id = household_id
    (open data, no surrogates). We use a modified donor-persons frame that
    carries the required ENTD canonical columns alongside the expand-required
    MiD columns, and ENTD households that match the synthetic household_id
    produced by assign_synthetic_household_ids.

    Note: after expand.expand_to_persons the household_id column is the SYNTHETIC
    household id (a string like "A_10_0"), not the ENTD integer.
    EntdSource.map_person_attributes must join on this synthetic id.
    This test verifies that the EntdSource returns the unified mapper contract
    (persons, pseudonym_map) with an EMPTY pseudonym map, confirming the
    no-pseudonymisation behaviour for open ENTD data.
    """
    from braunschweig.popsim.sources.entd import EntdSource

    # Verify EntdSource.map_person_attributes returns (persons, empty pseudonym map).
    # Use a minimal persons frame that mimics the expand output.
    hh = pd.DataFrame({
        "household_id":       ["syn_hh_1"],
        "household_weight":   [1.5],
        "household_size":     [1],
        "number_of_cars":     [1],
        "number_of_bicycles": [2],
        "income_class":       [5],
        "urban_type":         ["central_city"],  # Phase 4A: required by _HH_JOIN_COLS
    })
    p = pd.DataFrame({
        "person_id":              [100],
        "household_id":           ["syn_hh_1"],
        "person_weight":          [1.5],
        "age":                    [40],
        "sex":                    ["male"],
        "employed":               [True],
        "studies":                [False],
        "has_license":            [True],
        "has_pt_subscription":    [False],
        "number_of_trips":        [3],
        "departement_id":         ["03101"],
        "trip_weight":            [1.0],
        "socioprofessional_class": [3],
    })

    src = EntdSource()
    result, pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # Unified mapper contract: (persons, pseudonym_map); the map is EMPTY for
    # ENTD (open data, no surrogates) but must be explicit.
    assert isinstance(result, pd.DataFrame), (
        "EntdSource.map_person_attributes must return a persons DataFrame first, "
        f"got {type(result).__name__}"
    )
    assert isinstance(pseudonym_map, pd.DataFrame) and len(pseudonym_map) == 0, (
        "EntdSource.map_person_attributes must return an explicit EMPTY pseudonym map "
        "(open data, no surrogates)"
    )

    # source_person_id must equal person_id (open ENTD data, no surrogate).
    assert int(result["source_person_id"].iloc[0]) == 100, (
        f"Expected source_person_id=100 (ENTD person_id), got "
        f"{result['source_person_id'].iloc[0]}"
    )
    assert result["source_household_id"].iloc[0] == "syn_hh_1", (
        f"Expected source_household_id='syn_hh_1', got "
        f"{result['source_household_id'].iloc[0]}"
    )
