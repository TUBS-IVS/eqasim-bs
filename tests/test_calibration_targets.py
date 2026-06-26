import numpy as np
import pytest
from pathlib import Path
from braunschweig.calibration.targets import load_p13_band_shares, load_w12_band_shares, W12_BAND_EDGES_KM
from braunschweig.gravity.friction import BAND_EDGES_KM

MID = Path(__file__).resolve().parents[1] / "eqasim-data" / "data" / "braunschweig" / "mid"


def test_p13_band_shares_sum_to_one_seven_bands():
    shares = load_p13_band_shares(str(MID))
    assert "03101" in shares and "03ZGB" in shares
    for v in shares.values():
        assert len(v) == len(BAND_EDGES_KM) - 1 == 7
        np.testing.assert_allclose(v.sum(), 1.0, atol=1e-9)


def test_p13_first_band_combines_d0_and_d0_5():
    shares = load_p13_band_shares(str(MID))
    bs = shares["03101"]
    assert bs[0] > bs[5]  # short band heavier than the 50-100 band (sanity)


def test_p13_rs7_band_shares_all_six_types_sum_to_one():
    from braunschweig.calibration.targets import load_p13_band_shares_by_rs7
    shares = load_p13_band_shares_by_rs7(str(MID))
    assert set(shares) == {72, 73, 74, 75, 76, 77}   # 71 Metropole absent in ZGB
    for v in shares.values():
        assert len(v) == 7
        np.testing.assert_allclose(v.sum(), 1.0, atol=1e-9)


def test_p13_rs7_rural_dorflich_has_heaviest_long_tail():
    # RS7 77 (laendlich kleinstaedtisch/doerflich) has the longest commutes
    # (d_100p = 21% in the committed data) -> band 6 share is highest of all RS7.
    from braunschweig.calibration.targets import load_p13_band_shares_by_rs7
    shares = load_p13_band_shares_by_rs7(str(MID))
    band6 = {k: v[6] for k, v in shares.items()}
    assert max(band6, key=band6.get) == 77


def test_w12_band_shares_sum_to_one_for_all_secondary_purposes():
    """W12 band shares must sum to 1 for all three secondary purposes."""
    w12 = load_w12_band_shares(str(MID))
    for purpose in ("shop", "leisure", "other"):
        shares = w12[purpose]
        assert len(shares) == len(W12_BAND_EDGES_KM) - 1 == 9, (
            f"Expected 9 bands for purpose '{purpose}', got {len(shares)}"
        )
        np.testing.assert_allclose(
            shares.sum(), 1.0, atol=1e-9,
            err_msg=f"W12 band shares for '{purpose}' do not sum to 1",
        )


def test_w12_mean_km_values_are_positive():
    """W12 arithmetic mean km must be positive for each purpose."""
    w12 = load_w12_band_shares(str(MID))
    for purpose in ("shop", "leisure", "other"):
        mean_key = f"{purpose}_mean_km"
        assert mean_key in w12, f"Missing mean_km key '{mean_key}'"
        assert w12[mean_key] > 0, f"Expected positive mean_km for '{purpose}'"


def test_w12_shop_shorter_than_leisure():
    """Einkauf (shop) has a shorter mean trip length than Freizeit (leisure) in W12."""
    w12 = load_w12_band_shares(str(MID))
    assert w12["shop_mean_km"] < w12["leisure_mean_km"], (
        f"Expected shop mean ({w12['shop_mean_km']} km) < leisure mean ({w12['leisure_mean_km']} km)"
    )


def test_w12_missing_file_raises():
    """load_w12_band_shares raises FileNotFoundError for a non-existent directory."""
    with pytest.raises(FileNotFoundError):
        load_w12_band_shares("/nonexistent/path/to/mid")
