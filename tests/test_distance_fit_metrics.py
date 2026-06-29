import numpy as np
import pandas as pd
from braunschweig.calibration.distance_fit import fit_metrics as M

BAND_EDGES = [0, 5, 10, 20, 30, 50, 100, np.inf]


def test_band_share_fit_perfect_match_has_zero_emd():
    df = pd.DataFrame({"distance_km": [1.0, 2.0, 3.0], "kreis": ["A", "A", "A"]})
    target = np.zeros(len(BAND_EDGES) - 1); target[0] = 1.0
    out = M.band_share_fit(df, "kreis", {"A": target}, BAND_EDGES, reference_tag="out_of_sample")
    assert out["emd"].iloc[0] == 0.0
    assert out["reference_tag"].iloc[0] == "out_of_sample"
    assert abs(out[out.band == 0]["model_share"].iloc[0] - 1.0) < 1e-9


def test_mean_distance_fit_reports_abs_and_rel_error():
    df = pd.DataFrame({"distance_km": [4.0, 6.0], "rs7": [72, 72]})
    out = M.mean_distance_fit(df, ["rs7"], {"72": 4.0}, reference_tag="in_sample")
    row = out.iloc[0]
    assert abs(row["model_mean_km"] - 5.0) < 1e-9
    assert abs(row["abs_err_km"] - 1.0) < 1e-9
    assert abs(row["rel_err"] - 0.25) < 1e-9


def test_honesty_summary_only_validation_when_all_out_of_sample():
    df = pd.DataFrame({
        "key": ["A", "B"], "emd": [0.02, 0.10], "n": [100, 50],
        "reference_tag": ["out_of_sample", "out_of_sample"],
    })
    s = M.honesty_summary(df, metric="emd")
    assert s["worst_value"] == 0.10
    assert s["is_validation"] is True
    assert abs(s["subpop_weighted_mean"] - (0.02 * 100 + 0.10 * 50) / 150) < 1e-9
