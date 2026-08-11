"""Unit tests for the SrV escort-destination-weights derivation (issue #201)."""
import numpy as np
import pandas as pd
import pytest

from scripts.derive_escort_location_weights import (
    BHOL_CATEGORY,
    CATEGORY_ORDER,
    derive_weights,
    derive_distance_factors,
    weighted_median,
)


def _mini_srv_wege():
    # 8 escort legs (V_ZWECK == 12): 4x Kita(3), 1x Grundschule(4), 1x priv. Besuch(15),
    # 1x Sportstaette(17), 1x invalid BHOL (-8, not erhoben); 1 non-escort leg.
    return pd.DataFrame({
        "V_ZWECK":       [12, 12, 12, 12, 12, 12, 12, 12, 19],
        "V_ZWECK_BHOL":  [3, 3, 3, 3, 4, 15, 17, -8, -8],
        "E_ZWECK_OBHOL": [3, 3, 3, 3, 4, 15, 17, -8, 19],
        "GEWICHT_W":     [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 5.0, 1.0],
    })


def test_category_map_covers_all_bhol_codes():
    # Every documented V_ZWECK_BHOL code (SrV2023 Datenkodierung) must be mapped.
    documented = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 70}
    assert set(BHOL_CATEGORY) == documented


def test_derive_weights_shares_and_coverage():
    table, stats = derive_weights(_mini_srv_wege())
    assert list(table["category"]) == list(CATEGORY_ORDER)
    shares = dict(zip(table["category"], table["weight_share"]))
    # valid weight total = 4*1 + 2 + 2 + 2 = 10
    assert shares["edu_kindergarten"] == pytest.approx(0.4)
    assert shares["edu_school"] == pytest.approx(0.2)
    assert shares["residential"] == pytest.approx(0.2)
    assert shares["leisure"] == pytest.approx(0.2)
    assert shares["shop"] == 0.0 and shares["other"] == 0.0
    assert table["weight_share"].sum() == pytest.approx(1.0)
    assert stats["n_escort_legs"] == 8
    assert stats["n_valid"] == 7
    # weighted coverage = 10 / 15 (sum of valid escort weights / sum of all escort weights)
    assert stats["coverage_weighted"] == pytest.approx(10.0 / 15.0)
    # all 7 valid rows have E_ZWECK_OBHOL == V_ZWECK_BHOL
    assert stats["obhol_consistency_share"] == pytest.approx(1.0)


def test_derive_weights_raises_on_unmapped_code():
    df = _mini_srv_wege()
    df.loc[0, "V_ZWECK_BHOL"] = 12  # 12 is not a documented BHOL code
    with pytest.raises(ValueError, match="unmapped V_ZWECK_BHOL"):
        derive_weights(df)


def test_derive_weights_raises_when_no_valid_bhol():
    df = _mini_srv_wege()
    df["V_ZWECK_BHOL"] = -8
    with pytest.raises(ValueError, match="zero valid observations"):
        derive_weights(df)


def _length_frame():
    # DATA CONTRACT (verified on the raw file 2026-08-11): GIS_LAENGE_GUELTIG is
    # NOT a 0/1 flag -- it is the valid-only COPY of the GIS route length in km
    # (decimal comma in the raw CSV), with sentinel -7 where invalid. Validity is
    # therefore "value > 0" and the length is taken from this very column.
    # Fixture: 8 kindergarten legs at 1 km (weight 1) + 8 leisure legs at 4 km
    # (weight 2): total weight 24, half = 12; the cumulative weight reaches 12
    # inside the 4 km cluster -> overall weighted median = 4.0 exactly.
    n = 8
    return pd.DataFrame({
        "V_ZWECK": [12] * (2 * n),
        "V_ZWECK_BHOL": [3] * n + [13] * n,     # 3 -> edu_kindergarten, 13 -> leisure
        "E_ZWECK_OBHOL": [3] * n + [13] * n,
        "GEWICHT_W": [1.0] * n + [2.0] * n,
        "GIS_LAENGE_GUELTIG": [1.0] * n + [4.0] * n,   # km values (already valid)
    })


def test_weighted_median_basic():
    assert weighted_median([1.0, 2.0, 10.0], [1.0, 1.0, 1.0]) == 2.0
    # weight mass pulls the median onto the heavy value
    assert weighted_median([1.0, 2.0, 10.0], [10.0, 1.0, 1.0]) == 1.0


def test_derive_distance_factors_ratios_and_min_obs():
    table, stats = derive_distance_factors(_length_frame(), min_obs=5)
    table = table.set_index("category")
    overall = stats["overall_weighted_median_km"]
    assert overall == pytest.approx(4.0)          # hand-computed above
    assert table.loc["edu_kindergarten", "factor"] == pytest.approx(0.25)   # 1.0 / 4.0
    assert table.loc["leisure", "factor"] == pytest.approx(1.0)             # 4.0 / 4.0
    # both n=8 >= min_obs=5 -> applied as-is
    assert table.loc["edu_kindergarten", "factor_applied"] == pytest.approx(0.25)
    # rows come out in CATEGORY_ORDER so a straight column copy into the
    # DEFAULT_ESCORT_DISTANCE_FACTORS constant is order-safe
    assert list(table.index) == list(CATEGORY_ORDER)


def test_derive_distance_factors_separates_thin_from_absent_categories():
    df = _length_frame()
    table, stats = derive_distance_factors(df, min_obs=10)   # both categories n=8 < 10
    assert (table["factor_applied"] == 1.0).all()
    # thin (0 < n < min_obs) vs absent (n == 0) are DIFFERENT signals:
    assert set(stats["neutralized_categories"]) == {"edu_kindergarten", "leisure"}
    assert set(stats["absent_categories"]) == {
        "edu_school", "edu_university", "other", "residential", "shop"}


def test_derive_distance_factors_raises_on_low_gis_coverage():
    df = _length_frame()
    df.loc[df.index[:12], "GIS_LAENGE_GUELTIG"] = -7.0       # sentinel: 4/16 valid = 25% < 50%
    with pytest.raises(ValueError, match="GIS length coverage"):
        derive_distance_factors(df, min_obs=5)


def test_derive_distance_factors_raises_on_implausible_units():
    df = _length_frame()
    df["GIS_LAENGE_GUELTIG"] = df["GIS_LAENGE_GUELTIG"] * 1000.0   # metres, not km
    with pytest.raises(ValueError, match="implausible"):
        derive_distance_factors(df, min_obs=5)


def test_derive_distance_factors_raises_on_unmapped_code():
    df = _length_frame()
    df.loc[0, "V_ZWECK_BHOL"] = 77  # 77 is not a documented BHOL code
    with pytest.raises(ValueError, match="unmapped V_ZWECK_BHOL"):
        derive_distance_factors(df, min_obs=5)


def test_compute_length_coherence_pass_and_fail(tmp_path):
    from scripts.derive_escort_location_weights import compute_length_coherence
    # identical band shapes -> L1 = 0, ratio = 1 -> PASS
    srv = _length_frame()
    mid = pd.DataFrame({
        "W_ZWECK": [6] * 16,
        "W_GEW": [1.0] * 8 + [2.0] * 8,
        "wegkm_imp": [1.0] * 8 + [4.0] * 8,
    })
    mid_path = tmp_path / "mid_wege.csv"
    mid.to_csv(mid_path, index=False)
    gate = compute_length_coherence(srv, mid_path)
    assert gate["passed"] is True
    assert gate["band_l1_pp"] == pytest.approx(0.0, abs=1e-9)
    assert gate["median_ratio"] == pytest.approx(1.0)
    # scale SrV lengths x10 -> median ratio 10 -> FAIL
    srv10 = srv.copy()
    srv10["GIS_LAENGE_GUELTIG"] = srv10["GIS_LAENGE_GUELTIG"] * 10.0
    gate_fail = compute_length_coherence(srv10, mid_path)
    assert gate_fail["passed"] is False
