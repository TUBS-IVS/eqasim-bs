# tests/test_extract_mid_ownership_by_rs7_haustyp.py
"""Unit tests for the RS7 x haustyp ownership conditional extraction (issue #240)."""
import numpy as np
import pandas as pd
import pytest

from scripts.extract_mid_ownership_by_rs7_haustyp import build_ownership_conditional_tables


def _households(rows):
    return pd.DataFrame(rows, columns=["RegioStaR7", "haustyp", "H_ANZAUTO", "anzpedrad", "H_GEW"])


def test_weighted_shares_and_caps():
    # rs7=71/ht=1: two households, weights 1 and 3 -> car-free share 0.25; 5 cars caps to 3plus.
    rows = [(71, 1, 0, 2, 1.0), (71, 1, 5, 10, 3.0)]
    # Fill every other (rs7, ht) cell with one dummy household so the completeness
    # guard (test below) is not what fails here.
    rows += [(r, h, 1, 1, 1.0) for r in range(71, 78) for h in (1, 2, 3, 4) if not (r == 71 and h == 1)]
    cars, bikes = build_ownership_conditional_tables(_households(rows))
    row = cars[(cars["rs7"] == 71) & (cars["ht"] == 1)].iloc[0]
    assert row["cars_0"] == pytest.approx(0.25)
    assert row["cars_3plus"] == pytest.approx(0.75)
    assert row["n_unweighted"] == 2
    brow = bikes[(bikes["rs7"] == 71) & (bikes["ht"] == 1)].iloc[0]
    assert brow["bikes_2"] == pytest.approx(0.25)
    assert brow["bikes_4plus"] == pytest.approx(0.75)


def test_invalid_codes_are_excluded_and_counted():
    # H_ANZAUTO=99 and haustyp=95 rows must be excluded from the universe.
    rows = [(71, 1, 0, 1, 1.0), (71, 1, 99, 1, 1.0), (71, 95, 1, 1, 1.0)]
    rows += [(r, h, 1, 1, 1.0) for r in range(71, 78) for h in (1, 2, 3, 4) if not (r == 71 and h == 1)]
    cars, _ = build_ownership_conditional_tables(_households(rows))
    row = cars[(cars["rs7"] == 71) & (cars["ht"] == 1)].iloc[0]
    assert row["n_unweighted"] == 1
    assert row["cars_0"] == pytest.approx(1.0)


def test_rows_sum_to_one_and_grid_complete():
    rows = [(r, h, c, b, 1.0 + 0.1 * h) for r in range(71, 78) for h in (1, 2, 3, 4)
            for c, b in ((0, 0), (1, 2), (2, 4))]
    cars, bikes = build_ownership_conditional_tables(_households(rows))
    assert len(cars) == 28 and len(bikes) == 28
    for df, cats in ((cars, ["cars_0", "cars_1", "cars_2", "cars_3plus"]),
                     (bikes, ["bikes_0", "bikes_1", "bikes_2", "bikes_3", "bikes_4plus"])):
        np.testing.assert_allclose(df[cats].sum(axis=1).to_numpy(), 1.0, atol=1e-9)


def test_missing_grid_cell_raises():
    # No (77, 4) households at all -> the extraction must fail loudly, not emit 27 rows.
    rows = [(r, h, 1, 1, 1.0) for r in range(71, 78) for h in (1, 2, 3, 4) if not (r == 77 and h == 4)]
    with pytest.raises(ValueError, match="77.*4|incomplete"):
        build_ownership_conditional_tables(_households(rows))


def test_committed_conditionals_match_the_2026_08_19_spike_pins():
    """Pins from the independent 2026-08-19 spike aggregation (throwaway script,
    same raw B1 source, separate implementation) -- a mismatch means THIS script's
    universe/weighting differs from the verified one, not that reality changed."""
    cars = pd.read_csv("eqasim-data/data/braunschweig/mid/mid2023_cars_by_rs7_haustyp.csv", comment="#")
    bikes = pd.read_csv("eqasim-data/data/braunschweig/mid/mid2023_bikes_by_rs7_haustyp.csv", comment="#")
    assert len(cars) == 28 and len(bikes) == 28  # non-empty: gitignored-data trap guard
    c = cars[(cars["rs7"] == 71) & (cars["ht"] == 2)].iloc[0]
    assert c["cars_0"] == pytest.approx(0.449166, abs=5e-6)
    assert c["n_unweighted"] == 14554
    b = bikes[(bikes["rs7"] == 72) & (bikes["ht"] == 3)].iloc[0]
    assert b["bikes_0"] == pytest.approx(0.328313, abs=5e-6)
    assert b["n_unweighted"] == 1538
