"""Unit tests for the SrV leisure/other location-type derivation (issue #262)."""
import numpy as np
import pandas as pd
import pytest

from scripts.derive_srv_location_types import (
    BAND_EDGES_EUCLID_KM, DETOUR_FACTOR,
    derive_type_probabilities, derive_type_shares,
)


def _wege(rows):
    return pd.DataFrame(rows, columns=[
        "V_ZWECK", "E_HVM_5", "GEWICHT_W", "GIS_LAENGE_GUELTIG"])


def test_probabilities_sum_to_one_per_cell_and_marginal():
    rows = [(13, 1, 1.0, 1.3)] * 20 + [(14, 1, 1.0, 1.3)] * 40
    cells, stats = derive_type_probabilities(_wege(rows), min_obs=30)
    marg = cells[(cells.purpose == "leisure") & (cells.is_marginal == 1)]
    assert marg.probability.sum() == pytest.approx(1.0)
    assert marg.set_index("category").probability["leisure_gastronomy"] == pytest.approx(2 / 3)
    cell = cells[(cells.is_marginal == 0)]
    # 60 legs, all walk, all routed 1.3 km -> euclid 1.0 km -> band [1.0, 1.5)
    # NOTE: bracket access, not `cell.mode` -- DataFrame.mode() is a built-in
    # method that always shadows attribute access to a column literally named
    # "mode" (a known pandas pitfall, unrelated to the derivation logic).
    assert set(cell["mode"]) == {"walk"}
    assert cell.probability.sum() == pytest.approx(1.0)
    assert (cell.band_lower_km == 1.0).all() and (cell.band_upper_km == 1.5).all()


def test_thin_cells_are_omitted_not_fabricated():
    rows = [(13, 1, 1.0, 1.3)] * 10  # below min_obs=30
    cells, stats = derive_type_probabilities(_wege(rows), min_obs=30)
    assert (cells.is_marginal == 1).all()  # only the marginal survives
    assert stats["n_thin_cells"] == 1


def test_invalid_mode_and_length_are_excluded_and_counted():
    rows = [(13, -7, 1.0, 1.3), (13, 1, 1.0, -7.0), (13, 1, 1.0, 1.3)]
    cells, stats = derive_type_probabilities(_wege(rows), min_obs=1)
    assert stats["n_excluded_invalid_mode"] == 1
    assert stats["n_excluded_invalid_length"] == 1


def test_out_of_scope_codes_are_ignored():
    rows = [(12, 1, 1.0, 1.0), (13, 1, 1.0, 1.3)]
    cells, stats = derive_type_probabilities(_wege(rows), min_obs=1)
    assert stats["n_in_scope"] == 1


def test_shares_include_shop_and_weighted_medians():
    rows = [(8, 1, 2.0, 1.3), (9, 1, 1.0, 2.6), (16, 1, 1.0, 1.3)]
    shares = derive_type_shares(_wege(rows))
    shop = shares[shares.purpose == "shop"].set_index("category")
    assert shop.loc["shop_daily", "weight_share"] == pytest.approx(2 / 3)
    assert shop.loc["shop_daily", "weighted_median_euclid_km"] == pytest.approx(1.0)
