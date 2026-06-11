"""Tests for the attribute-matched chain replacement of unfixable persons
(cascade stage B, replaces the same-cell resample; braunschweig.popsim.trips).

Persons whose chains are still unfixable after stage A (time imputation) are
matched to a VALID-chain donor by attributes (sex, age_class, employed,
socioprofessional_class[, RegioStaR7]) via the reusable hierarchical-relaxation
matching (synthesis.population.matched.match_donors) -- behavioural similarity
beats 100 m proximity (the same-cell pool was 1-3 donors at 1 % sampling).
Home-only remains ONLY as the loudly-logged catch for match failures.

Tiny synthetic data only.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim import trips as popsim_trips


def _coded_wege(h_id, p_id, zwecke, hvm):
    """Wege rows for one donor person with ALL time fields carrying code 701."""
    n = len(zwecke)
    return pd.DataFrame({
        "H_ID": [h_id] * n, "P_ID": [p_id] * n, "W_ID": list(range(1, n + 1)),
        "W_ZWECK": zwecke, "hvm_imp": hvm,
        "W_SZS": [701] * n, "W_SZM": [701] * n,
        "W_AZS": [701] * n, "W_AZM": [701] * n,
    })


def _valid_wege(h_id, p_id, zwecke, hvm, times):
    """Wege rows for one donor person with valid (dep_h, dep_m, arr_h, arr_m)."""
    n = len(zwecke)
    return pd.DataFrame({
        "H_ID": [h_id] * n, "P_ID": [p_id] * n, "W_ID": list(range(1, n + 1)),
        "W_ZWECK": zwecke, "hvm_imp": hvm,
        "W_SZS": [t[0] for t in times], "W_SZM": [t[1] for t in times],
        "W_AZS": [t[2] for t in times], "W_AZM": [t[3] for t in times],
    })


def test_unfixable_person_gets_attribute_matched_chain_not_same_cell():
    """The unfixable person pA must inherit the chain of the ATTRIBUTE-matching
    donor pC (other cell), NOT the same-cell donor pB with different attributes,
    and the trip rows must carry chain_donor_id == pC for traceability."""
    persons = pd.DataFrame({
        "person_id": ["pA", "pB", "pC"],
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1],
        # pB shares pA's ZENSUS100m cell; pC lives in ANOTHER cell.
        "ZENSUS100m": ["c1", "c1", "c2"],
        # pC matches pA on every key; pB differs on every key.
        "sex": ["male", "female", "male"],
        "age": [35, 70, 36],
        "employed": [True, False, True],
        "socioprofessional_class": [3, 6, 3],
    })
    wege = pd.concat([
        # pA: structurally broken chain (all four time fields coded 701; no
        # wegmin_imp1 column -> stage A unavailable -> straight to stage B).
        _coded_wege(1, 1, [1, 8], [4, 4]).assign(wegkm_imp=[5.0, 6.0]),
        # pB (same-cell donor, different attributes): shop -> home.
        _valid_wege(2, 1, [4, 8], [5, 5],
                    [(9, 0, 9, 10), (17, 0, 17, 15)]).assign(wegkm_imp=[1.0, 2.0]),
        # pC (attribute-matching donor, other cell): leisure -> home.
        _valid_wege(3, 1, [7, 8], [1, 1],
                    [(10, 0, 10, 20), (18, 0, 18, 30)]).assign(wegkm_imp=[3.0, 4.0]),
    ], ignore_index=True)

    table, report = popsim_trips.build_validated_trip_table(
        persons, wege, resample=True, resample_cell_col="ZENSUS100m",
        random_seed=0,
    )

    assert "pA" in set(table["person_id"]), "pA must not fall back to home-only"
    chain = table[table["person_id"] == "pA"].sort_values("trip_index")
    # pA carries pC's chain (purposes + distances), not pB's.
    assert chain["following_purpose"].tolist() == ["leisure", "home"]
    assert chain["wegkm_imp"].tolist() == [3.0, 4.0]
    # Traceability: the matched donor is recorded per trip row.
    assert (chain["chain_donor_id"] == "pC").all()
    # Donor rows of pB / pC themselves are unchanged (no chain_donor_id).
    assert table.loc[table["person_id"] == "pC", "chain_donor_id"].isna().all()
    # No NaN time survives; the final report is valid.
    assert table["departure_time"].notna().all()
    assert report.is_valid


def test_unfixable_person_without_first_key_donor_becomes_home_only(caplog):
    """When NO donor shares the FIRST matching-key value (sex), the person is
    left trip-less (home-only drop convention) and the fallback is logged."""
    persons = pd.DataFrame({
        "person_id": ["pA", "pB", "pC"],
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1],
        # pB even shares pA's cell: the OLD same-cell draw would have replaced
        # pA's chain -- the matched path must NOT, because no male donor exists.
        "ZENSUS100m": ["c1", "c1", "c2"],
        "sex": ["male", "female", "female"],
        "age": [35, 36, 40],
        "employed": [True, True, True],
        "socioprofessional_class": [3, 3, 3],
    })
    wege = pd.concat([
        _coded_wege(1, 1, [1, 8], [4, 4]),
        _valid_wege(2, 1, [4, 8], [5, 5], [(9, 0, 9, 10), (17, 0, 17, 15)]),
        _valid_wege(3, 1, [7, 8], [1, 1], [(10, 0, 10, 20), (18, 0, 18, 30)]),
    ], ignore_index=True)

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        table, report = popsim_trips.build_validated_trip_table(
            persons, wege, resample=True, resample_cell_col="ZENSUS100m",
            random_seed=0,
        )

    # pA is trip-less (no rows): the eqasim stay-home convention.
    assert "pA" not in set(table["person_id"])
    assert set(table["person_id"]) == {"pB", "pC"}
    assert table["departure_time"].notna().all()
    # The home-only fallback is loudly logged with the coverage line.
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("home-only" in m for m in messages)
    assert any("stage B" in m for m in messages)


def test_minimal_persons_frame_falls_back_to_same_cell_resample(caplog):
    """Persons frames without the 'sex' attribute (minimal unit-test fixtures)
    must fall back to the OLD same-cell resample path with a loud warning."""
    persons = pd.DataFrame({
        "person_id": ["pA", "pB"], "H_ID": [1, 2], "P_ID": [1, 1],
        "ZENSUS100m": ["c1", "c1"],
    })
    wege = pd.concat([
        _coded_wege(1, 1, [1], [4]),
        _valid_wege(2, 1, [1, 8], [4, 4], [(8, 0, 8, 30), (17, 0, 17, 20)]),
    ], ignore_index=True)

    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.trips"):
        table, report = popsim_trips.build_validated_trip_table(
            persons, wege, resample=True, resample_cell_col="ZENSUS100m",
            random_seed=0,
        )

    # Same-cell semantics: pA inherits the only same-cell donor chain (pB's).
    assert set(table["person_id"]) == {"pA", "pB"}
    assert table["departure_time"].notna().all()
    assert any("same-cell" in rec.getMessage() for rec in caplog.records)


def test_matched_replacement_is_deterministic():
    """Identical inputs + seed must yield byte-identical replacement chains."""
    persons = pd.DataFrame({
        "person_id": ["pA", "pB", "pC"],
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1],
        "ZENSUS100m": ["c1", "c2", "c2"],
        "sex": ["male", "male", "male"],
        "age": [35, 36, 37],
        "employed": [True, True, True],
        "socioprofessional_class": [3, 3, 3],
    })
    wege = pd.concat([
        _coded_wege(1, 1, [1, 8], [4, 4]),
        _valid_wege(2, 1, [4, 8], [5, 5], [(9, 0, 9, 10), (17, 0, 17, 15)]),
        _valid_wege(3, 1, [7, 8], [1, 1], [(10, 0, 10, 20), (18, 0, 18, 30)]),
    ], ignore_index=True)

    t1, _ = popsim_trips.build_validated_trip_table(
        persons, wege, resample=True, resample_cell_col="ZENSUS100m", random_seed=7)
    t2, _ = popsim_trips.build_validated_trip_table(
        persons, wege, resample=True, resample_cell_col="ZENSUS100m", random_seed=7)
    pd.testing.assert_frame_equal(
        t1.reset_index(drop=True), t2.reset_index(drop=True)
    )


def test_shared_chain_matching_constants_are_single_sourced():
    """entd.py and the stage B matched replacement must share ONE definition of
    the age-class bins and the minimum-observations rule (no copied constants)."""
    from braunschweig.popsim import chain_matching
    from braunschweig.popsim.sources import entd

    assert entd.CHAIN_MATCHING_AGE_BOUNDARIES is chain_matching.CHAIN_MATCHING_AGE_BOUNDARIES
    assert entd.CHAIN_MATCHING_MINIMUM_OBSERVATIONS == chain_matching.CHAIN_MATCHING_MINIMUM_OBSERVATIONS
    # The legacy bins: digitize(35) == digitize(36) (same 30-44 band).
    assert (chain_matching.derive_age_class(pd.Series([35]))
            == chain_matching.derive_age_class(pd.Series([36]))).all()
    # The pool-capped minimum-observations rule from entd.py.
    assert chain_matching.effective_minimum_observations(2) == 1
    assert chain_matching.effective_minimum_observations(1000) == 20
    assert chain_matching.effective_minimum_observations(0) == 1
