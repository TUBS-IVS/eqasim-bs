"""Tests for the popsim donor-source adapter interface + MidSource (Phase 1).

Verifies:
- Registry returns a MidSource for "mid"; unknown names raise ValueError.
- MidSource.seed_columns() matches the existing mid.MID_SEED_COLUMNS constant.
- MidSource.map_person_attributes() produces the same columns as assembly.build_persons.
- MidSource.build_trips() delegates to trips_stage.run() and returns the 11-col contract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import sources
from braunschweig.popsim.sources.mid import MidSource


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_registry_returns_mid_source():
    s = sources.get_source("mid")
    assert s.name == "mid"
    assert isinstance(s, MidSource)


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        sources.get_source("nope")


def test_mid_source_seed_columns_matches_existing():
    from braunschweig.popsim import mid
    assert MidSource().seed_columns() == mid.MID_SEED_COLUMNS


# ---------------------------------------------------------------------------
# Fixtures shared between attribute-mapping and trips tests
# ---------------------------------------------------------------------------

def _merged_households():
    """Two synthetic households with the full ARS column required by derive_zone_ids."""
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km": ["KA", "KB"],
        "H_ID": [1, 2],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })


def _mid_persons():
    return pd.DataFrame({
        "H_ID":      [1,  1,  2],
        "P_ID":      [1,  2,  1],
        "HP_ALTER":  [40, 10, 25],
        "HP_SEX":    [1,  2,  1],
        "P_TAET":    [1,  9,  8],      # employed; pupil; unemployed
        "P_FSCHEIN": [1,  2,  1],      # licence yes/no/yes
        "P_FKARTE":  [3,  8,  1],      # Deutschlandticket; never; single ticket
        "P_BKAT":    [1,  99, 7],      # Angestellte; k.A.; not employed
    })


def _mid_households():
    return pd.DataFrame({
        "H_ID":       [1, 2],
        "oek_status": [3, 5],
        "hheink_gr1": [4, 15],
        "H_ANZAUTO":  [1, 0],
        "H_ANZRAD":   [2, 1],
    })


# ---------------------------------------------------------------------------
# MidSource.map_person_attributes tests
# ---------------------------------------------------------------------------

def test_mid_source_map_person_attributes_yields_same_columns_as_build_persons():
    """MidSource.map_person_attributes must produce the same attribute columns as
    assembly.build_persons on an identical persons+households fixture.

    The two code paths must be byte-identical because MidSource delegates to the
    same assembly.map_mid_person_attributes function that build_persons now calls.
    """
    from braunschweig.popsim import assembly
    from braunschweig.popsim import expand

    # Build the persons frame that build_persons would receive internally, by
    # replicating the pre-attribute-mapping steps from build_persons:
    #   assign_synthetic_household_ids -> expand_to_persons -> map_demographics
    #                                  -> derive_zone_ids
    merged = _merged_households()
    mid_hh = _mid_households()
    mid_p = _mid_persons()
    rng = np.random.RandomState(42)

    # Baseline: full build_persons path.
    baseline_persons, _map = assembly.build_persons(
        merged, mid_hh, mid_p, rng=np.random.RandomState(42)
    )

    # MidSource path: call map_person_attributes directly.
    # We need to supply the same pre-mapped persons frame that build_persons hands to
    # map_mid_person_attributes. Reproduce the pre-mapping state.
    households_with_ids = expand.assign_synthetic_household_ids(merged, donor_col="H_ID")
    persons_expanded = expand.expand_to_persons(households_with_ids, mid_p, donor_col="H_ID")
    persons_demog = expand.map_demographics(persons_expanded)
    persons_zoned = assembly.derive_zone_ids(persons_demog)

    result, _pseudonym_map = MidSource().map_person_attributes(
        persons_zoned, mid_hh, rng=np.random.RandomState(42)
    )

    # The attribute columns that map_mid_person_attributes adds must all be present.
    expected_attr_cols = [
        "employed", "studies", "has_license", "has_pt_subscription",
        "economic_status", "household_income", "household_income_eur",
        "number_of_cars", "number_of_bicycles",
        "car_availability", "bicycle_availability",
        "age_range", "high_income", "household_size", "is_urban_resident",
        "source_person_id", "source_household_id",
        "pt_subscription_type", "socioprofessional_class",
        "weight",
    ]
    for col in expected_attr_cols:
        assert col in result.columns, (
            f"MidSource.map_person_attributes is missing column: {col}"
        )

    # The same columns must be present in the baseline build_persons output.
    for col in expected_attr_cols:
        assert col in baseline_persons.columns, (
            f"assembly.build_persons is missing expected column: {col}"
        )


def test_mid_source_map_person_attributes_produces_same_result_as_build_persons():
    """MidSource.map_person_attributes must be byte-identical to build_persons for
    the attribute columns (same rng seed -> same stochastic assignments).
    """
    from braunschweig.popsim import assembly
    from braunschweig.popsim import expand

    merged = _merged_households()
    mid_hh = _mid_households()
    mid_p = _mid_persons()

    # Baseline: assembly.build_persons.
    baseline_persons, _map = assembly.build_persons(
        merged.copy(), mid_hh.copy(), mid_p.copy(),
        rng=np.random.RandomState(7),
    )

    # MidSource path: reproduce pre-mapping state, then call map_person_attributes.
    households_with_ids = expand.assign_synthetic_household_ids(
        merged.copy(), donor_col="H_ID"
    )
    persons_expanded = expand.expand_to_persons(
        households_with_ids, mid_p.copy(), donor_col="H_ID"
    )
    persons_demog = expand.map_demographics(persons_expanded)
    persons_zoned = assembly.derive_zone_ids(persons_demog)

    result, _pseudonym_map = MidSource().map_person_attributes(
        persons_zoned, mid_hh.copy(), rng=np.random.RandomState(7)
    )

    # Sort by source_person_id (surrogate) for a stable comparison.
    baseline_sorted = baseline_persons.sort_values("source_person_id").reset_index(drop=True)
    result_sorted = result.sort_values("source_person_id").reset_index(drop=True)

    # Deterministic columns (not RNG-dependent) must be identical.
    for col in ["age", "sex", "commune_id", "departement_id", "studies",
                "economic_status", "household_income_eur",
                "car_availability", "bicycle_availability",
                "age_range", "high_income", "household_size", "is_urban_resident",
                "source_person_id", "source_household_id", "weight"]:
        assert col in baseline_sorted.columns, f"baseline missing {col}"
        assert col in result_sorted.columns, f"result missing {col}"
        pd.testing.assert_series_equal(
            baseline_sorted[col].reset_index(drop=True),
            result_sorted[col].reset_index(drop=True),
            check_names=False,
            obj=f"column '{col}' differs between build_persons and MidSource.map_person_attributes",
        )


def test_mid_source_mapper_via_build_persons_keeps_pseudonym_map_populated():
    """build_persons with attribute_mapper=MidSource().map_person_attributes
    (the exact wiring of stage.py for source='mid') must return the POPULATED
    pseudonym map -- not a silently substituted empty one.

    This pins the empty-pseudonym_map.csv bug: MidSource.map_person_attributes
    used to discard the map, build_persons sniffed the non-tuple return and
    substituted an empty map, and stage.py wrote an empty (useless) re-linking
    file for the pseudonymisation-REQUIRED MiD source.
    """
    from braunschweig.popsim import assembly

    persons, pseudonym_map = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(7),
        attribute_mapper=MidSource().map_person_attributes,
        pseudonymise=True,
    )
    assert len(pseudonym_map) > 0, (
        "pseudonym map is empty: the MidSource mapper return was silently "
        "replaced by an empty map (re-linking lost)"
    )
    # The map must re-link every surrogate on the persons frame to a raw donor id.
    assert set(persons["source_person_id"]) <= set(pseudonym_map["source_person_id"])
    assert {"source_person_id", "source_household_id", "H_ID", "P_ID"} <= set(
        pseudonym_map.columns
    )


# ---------------------------------------------------------------------------
# MidSource.build_trips tests
# ---------------------------------------------------------------------------

def _minimal_persons_for_trips():
    """Minimal persons frame with the keys that trips_stage.run requires."""
    return pd.DataFrame({
        "person_id": ["A_1_0_1"],
        "H_ID": [1],
        "P_ID": [1],
    })


def _minimal_wege():
    """Two MiD trips for person (H_ID=1, P_ID=1)."""
    return pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0],
        "W_AZS": [8, 17], "W_AZM": [30, 20],
        "wegkm_imp": [12.0, 12.0],
    })


def test_mid_source_build_trips_returns_contract_columns():
    """MidSource.build_trips must return the 11-column synthesis.population.trips contract."""
    from braunschweig.popsim.trips_stage import CONTRACT

    out = MidSource().build_trips(
        _minimal_persons_for_trips(), _minimal_wege(), random_seed=0
    )
    for col in CONTRACT:
        assert col in out.columns, f"build_trips output missing contract column: {col}"


def test_mid_source_build_trips_returns_euclidean_distance():
    """build_trips must include euclidean_distance derived from wegkm_imp / detour factor."""
    import numpy as np
    from braunschweig.popsim.trips_stage import DETOUR_FACTOR

    out = MidSource().build_trips(
        _minimal_persons_for_trips(), _minimal_wege(), random_seed=0
    )
    assert "euclidean_distance" in out.columns
    expected = 12.0 * 1000.0 / DETOUR_FACTOR
    assert np.allclose(out["euclidean_distance"], expected)


def test_mid_source_build_trips_delegates_to_trips_stage():
    """MidSource.build_trips must produce the exact same output as trips_stage.run
    (it is a thin wrapper, not a re-implementation)."""
    from braunschweig.popsim import trips_stage

    persons = _minimal_persons_for_trips()
    wege = _minimal_wege()

    expected = trips_stage.run(persons.copy(), wege.copy(), random_seed=99)
    result = MidSource().build_trips(persons.copy(), wege.copy(), random_seed=99)

    pd.testing.assert_frame_equal(
        expected.reset_index(drop=True),
        result.reset_index(drop=True),
    )
