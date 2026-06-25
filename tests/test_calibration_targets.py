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
