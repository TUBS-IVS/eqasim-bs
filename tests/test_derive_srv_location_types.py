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
        "V_ZWECK", "E_HVM_5", "GEWICHT_W_ZENSUS", "GIS_LAENGE_GUELTIG"])


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


def _write_raw_wege(path, zensus_weights):
    # Raw-file format (";"-separated, decimal comma). Both SrV trip weights are present
    # and DISAGREE, so the written shares reveal which one the script used:
    # culture leg GEWICHT_W 1 / GEWICHT_W_ZENSUS 3, gastronomy leg 1 / 1.
    header = "V_ZWECK;E_HVM_5;GEWICHT_W;GEWICHT_W_ZENSUS;GIS_LAENGE_GUELTIG"
    rows = [f"13;1;1,0;{zensus_weights[0]};1,3", f"14;1;1,0;{zensus_weights[1]};1,3"]
    path.write_text("\n".join([header, *rows]), encoding="cp1252")


def test_main_weights_by_zensus_expansion_weight(tmp_path):
    # ADR-0055: SrV aggregates use GEWICHT_W_ZENSUS; the stratum-internal GEWICHT_W
    # would give the culture leg a share of 0.5 instead of 3 / (3 + 1) = 0.75.
    from scripts.derive_srv_location_types import main
    wege = tmp_path / "wege.csv"
    _write_raw_wege(wege, ("3,0", "1,0"))
    probs_path, shares_path = tmp_path / "probs.csv", tmp_path / "shares.csv"
    assert main(["--wege", str(wege), "--out-probs", str(probs_path),
                 "--out-shares", str(shares_path), "--min-obs", "1"]) == 0

    shares = pd.read_csv(shares_path, comment="#").set_index("category")
    assert shares.loc["leisure_culture", "weight_share"] == pytest.approx(0.75)
    probs = pd.read_csv(probs_path, comment="#")
    marginal = probs[probs.is_marginal == 1].set_index("category")
    assert marginal.loc["leisure_culture", "probability"] == pytest.approx(0.75)


@pytest.mark.parametrize("bad_weight", ["0,0", ""])
def test_main_rejects_missing_or_non_positive_zensus_weight(tmp_path, bad_weight):
    from scripts.derive_srv_location_types import main
    wege = tmp_path / "wege.csv"
    _write_raw_wege(wege, (bad_weight, "1,0"))
    with pytest.raises(ValueError, match="GEWICHT_W_ZENSUS"):
        main(["--wege", str(wege), "--out-probs", str(tmp_path / "p.csv"),
              "--out-shares", str(tmp_path / "s.csv")])
