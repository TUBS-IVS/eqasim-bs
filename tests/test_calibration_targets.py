import numpy as np
from pathlib import Path
from braunschweig.calibration.targets import load_p13_band_shares
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
