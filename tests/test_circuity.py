import os
import numpy as np
import pytest

from braunschweig.calibration import circuity as c


def test_load_params_has_three_networks():
    params = c.load_circuity_params()
    assert set(params) == {"car", "walk", "pt"}
    assert params["car"]["c_inf"] >= 1.0
    assert params["pt"]["base"] == "car"
    assert params["pt"]["uplift"] >= 1.0


def test_curve_monotone_decreasing_toward_asymptote():
    p = {"car": {"c_inf": 1.15, "a": 0.6, "tau": 2.0}}
    # d=10 km: exp(-10/2)=exp(-5)≈0.0067, so c≈1.154 — strictly above 1.15 in float64.
    # d=100 would give c=1.15+0.6*1.9e-22 which rounds to 1.15 exactly (below machine eps
    # relative to 1.15), so 100.0 is replaced with 10.0 to keep the strict > assertion.
    d = np.array([0.5, 1.0, 2.0, 5.0, 10.0])
    f = c.circuity_factor(d, "car", params=p)
    assert np.all(np.diff(f) < 0)            # strictly decreasing
    assert f[-1] > 1.15 and f[-1] < 1.16    # approaches c_inf from above
    assert f[0] > f[-1]


def test_pt_is_car_times_uplift():
    p = {"car": {"c_inf": 1.15, "a": 0.6, "tau": 2.0},
         "pt": {"uplift": 1.3, "base": "car"}}
    d = np.array([1.0, 10.0])
    np.testing.assert_allclose(
        c.circuity_factor(d, "pt", params=p),
        c.circuity_factor(d, "car", params=p) * 1.3,
    )


def test_euclidean_routed_roundtrip():
    p = {"car": {"c_inf": 1.15, "a": 0.6, "tau": 2.0}}
    d = np.array([0.3, 1.0, 3.0, 25.0, 80.0])
    routed = c.euclidean_to_routed(d, "car", params=p)
    back = c.routed_to_euclidean(routed, "car", params=p)
    np.testing.assert_allclose(back, d, rtol=1e-4)


def test_routed_increases_with_euclidean():
    p = {"car": {"c_inf": 1.15, "a": 0.6, "tau": 2.0}}
    d = np.linspace(0.1, 50, 50)
    routed = c.euclidean_to_routed(d, "car", params=p)
    assert np.all(np.diff(routed) > 0)


def test_constant_mode_reproduces_legacy_factor():
    d = np.array([0.5, 10.0])
    np.testing.assert_allclose(
        c.euclidean_to_routed(d, "car", mode="constant"), d * c.LEGACY_DETOUR_FACTOR)
    np.testing.assert_allclose(
        c.routed_to_euclidean(d, "car", mode="constant"), d / c.LEGACY_DETOUR_FACTOR)


def test_load_circuity_params_raises_on_invalid_tau(tmp_path):
    """load_circuity_params must raise ValueError for tau_km <= 0."""
    csv_content = (
        "network,c_inf,a,tau_km,uplift,base\n"
        "car,1.15,0.6,2.0,,\n"
        "walk,1.1,0.5,-1.0,,\n"   # invalid tau
        "pt,,,,1.2,car\n"
    )
    p = tmp_path / "bad_circuity_params.csv"
    p.write_text(csv_content)
    with pytest.raises(ValueError):
        c.load_circuity_params(path=str(p))


def test_walk_circuity_factor_spot_check():
    """c(1.0, walk) = c_inf + a * exp(-1.0 / tau) for a known params dict."""
    p = {"walk": {"c_inf": 1.2, "a": 0.4, "tau": 1.0}}
    result = c.circuity_factor(1.0, "walk", params=p)
    expected = 1.2 + 0.4 * np.exp(-1.0)
    np.testing.assert_allclose(result, expected)


def test_invalid_mode_raises():
    """euclidean_to_routed raises ValueError for an unknown mode string."""
    with pytest.raises(ValueError):
        c.euclidean_to_routed([1.0], "car", mode="bad")
