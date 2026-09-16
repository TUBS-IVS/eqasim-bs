"""
Test for SrV 2023 per-Kreis trip-participation aggregate builder.
"""

import pandas as pd
import pytest

from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS
from scripts.build_srv_participation_aggregate import compute_participation


def test_participation_shares_weighted():
    """Test that participation shares are correctly computed and weighted."""
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2],
        "PNR": [1, 2, 1],
        "kreis": ["03101"] * 3,  # real ARS Kreis, derived from household AGS
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 2.0],
    })
    wege = pd.DataFrame({
        "HHNR": [1, 2],
        "PNR": [1, 1],
        "E_ZWECK_9": [1, 7],  # (1,1) has work trip, (2,1) has leisure trip
    })
    out = compute_participation(persons, wege)
    row = out[out["code"] == "03101"].iloc[0]
    # Person (1,1): weight 1.0, has work trip -> contributes to work
    # Person (1,2): weight 1.0, no trip -> contributes to neither
    # Person (2,1): weight 2.0, has leisure trip -> contributes to leisure
    # Total weight: 4.0
    # work: 1.0/4.0 = 0.25
    # leisure: 2.0/4.0 = 0.5
    # education: 0.0
    assert abs(row["work"] - 1.0 / 4.0) < 1e-9
    assert abs(row["leisure"] - 2.0 / 4.0) < 1e-9
    # A MEASURED zero: this Kreis has persons, none of them made an education trip. It must stay
    # 0.0 -- only an EMPTY weight base becomes NaN (see the unsurveyed-Kreis test below).
    assert row["education"] == 0.0


def _one_kreis():
    """One Kreis (03101) with three persons; 03101 work 0.25, leisure 0.5, education 0."""
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2], "PNR": [1, 2, 1], "kreis": ["03101"] * 3,
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 2.0],
    })
    wege = pd.DataFrame({"HHNR": [1, 2], "PNR": [1, 1], "E_ZWECK_9": [1, 7]})
    return persons, wege


def test_a_kreis_without_persons_is_a_zero_row_with_nan_shares():
    """A Kreis the delivery does not cover is a ZERO row, never an absent one (issue #405).

    The row set comes from the expected geography, so a reader never has to know how many Kreise
    to expect. The shares must be NaN, not 0.0: a 0.0 here would read as a measured participation
    rate of zero and would flow into a control target as one -- the builder's old
    ``if tot > 0 else 0.0`` would have produced exactly that fabricated zero.
    """
    persons, wege = _one_kreis()
    out = compute_participation(persons, wege, expected_kreise=("03101", WOLFSBURG_KREIS))
    row = out[out["code"] == WOLFSBURG_KREIS].iloc[0]
    assert row["level"] == "kreis"
    assert row["n_unweighted"] == 0
    for purpose in ("work", "education", "leisure", "escort"):
        assert pd.isna(row[purpose]), f"{purpose} must be NaN for an empty Kreis, got {row[purpose]!r}"


def test_the_row_set_is_the_expected_geography_and_the_region_row_is_unchanged():
    """Zero rows add no weight, so the region total stays exactly what it was."""
    persons, wege = _one_kreis()
    out = compute_participation(persons, wege, expected_kreise=("03101", WOLFSBURG_KREIS))
    assert list(out[out["level"] == "kreis"]["code"]) == ["03101", WOLFSBURG_KREIS]
    total = out[out["level"] == "total"].iloc[0]
    assert total["code"] == "03ZGB" and total["n_unweighted"] == 3
    assert total["work"] == pytest.approx(1.0 / 4.0)


def test_a_kreis_outside_the_expected_set_raises_instead_of_vanishing():
    """With a contract-driven row set, a person in an unexpected Kreis would land in the region
    row but in no Kreis row -- silently breaking sum(kreis) == total. Named, not absorbed."""
    persons, wege = _one_kreis()
    persons.loc[2, "kreis"] = "09999"
    with pytest.raises(ValueError, match="09999"):
        compute_participation(persons, wege, expected_kreise=("03101", WOLFSBURG_KREIS))
