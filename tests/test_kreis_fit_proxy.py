import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.analysis.population_validation.kreis_fit_proxy import (  # noqa: E402
    rake, weighted_category_shares, srmse, kreis_fit_proxy,
)


def test_rake_matches_a_single_marginal():
    # 4 donor rows, attribute a in {x,y}; base weight 1 each; target 30 x / 10 y.
    seed = pd.DataFrame({"a": ["x", "x", "y", "y"], "weight": [1.0, 1.0, 1.0, 1.0]})
    w = rake(seed, [("a", {"x": 30.0, "y": 10.0})], weight_col="weight")
    assert w[seed.a == "x"].sum() == pytest.approx(30.0)
    assert w[seed.a == "y"].sum() == pytest.approx(10.0)


def test_rake_converges_on_two_marginals():
    # a in {x,y}, b in {p,q}; rake to both marginals (classic IPF).
    seed = pd.DataFrame({
        "a": ["x", "x", "y", "y"],
        "b": ["p", "q", "p", "q"],
        "weight": [1.0, 1.0, 1.0, 1.0],
    })
    w = rake(seed, [("a", {"x": 60.0, "y": 40.0}), ("b", {"p": 50.0, "q": 50.0})], weight_col="weight")
    # both marginals satisfied simultaneously
    assert w[seed.a == "x"].sum() == pytest.approx(60.0, rel=1e-4)
    assert w[seed.b == "p"].sum() == pytest.approx(50.0, rel=1e-4)


def test_weighted_category_shares_sum_to_one():
    seed = pd.DataFrame({"c": ["0", "1", "1", "2"]})
    w = np.array([10.0, 20.0, 20.0, 50.0])
    sh = weighted_category_shares(seed, w, "c", ["0", "1", "2"])
    assert sh.sum() == pytest.approx(1.0)
    assert sh[2] == pytest.approx(0.5)  # category "2" has weight 50/100


def test_srmse_zero_and_scale():
    t = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
    assert srmse(t.copy(), t.copy()) == pytest.approx(0.0)
    r = np.array([0.3, 0.1, 0.2, 0.2, 0.2])
    # SRMSE = RMSE / mean(target); mean(target)=0.2
    assert srmse(r, t) == pytest.approx(np.sqrt(np.mean((r - t) ** 2)) / 0.2)


def test_kreis_fit_proxy_reads_candidate_deviation_after_raking():
    # Two Kreise share ONE national donor pool. The controlled attribute "econ" is raked to each
    # Kreis's marginal; the candidate "cars" deviation is then measured vs its per-Kreis target.
    seed = pd.DataFrame({
        "econ": ["low", "low", "high", "high"],
        "cars": ["0", "1", "1", "2"],
        "weight": [1.0, 1.0, 1.0, 1.0],
    })
    control_marginals = {
        "03102": [("econ", {"low": 20.0, "high": 80.0})],   # rich Kreis
        "03153": [("econ", {"low": 80.0, "high": 20.0})],   # poor Kreis
    }
    cars_cats = ["0", "1", "2"]
    # SAME (region-average-like) cars target for both Kreise -> the two Kreise's SRMSE-vs-the-common-
    # target must DIFFER because raking econ differently reweights the cars-correlated donors.
    # 03102 (econ mostly high) rakes to cars ~[0.1,0.5,0.4]; 03153 (econ mostly low) to ~[0.4,0.5,0.1].
    common_target = np.array([0.5, 0.3, 0.2])  # asymmetric so the two distances are not symmetric
    cars_target = {"03102": common_target, "03153": common_target}
    out = kreis_fit_proxy(seed, control_marginals, "cars", cars_cats, cars_target, weight_col="weight")
    assert set(out) == {"03102", "03153"}
    assert all(np.isfinite(v) for v in out.values())
    # 03102's raked cars distribution is further from the common target than 03153's.
    assert out["03102"] > out["03153"]
