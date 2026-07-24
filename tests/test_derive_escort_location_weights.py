"""Unit tests for the SrV escort-destination-weights derivation (issue #201)."""
import pandas as pd
import pytest

from scripts.derive_escort_location_weights import (
    BHOL_CATEGORY,
    CATEGORY_ORDER,
    derive_weights,
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
