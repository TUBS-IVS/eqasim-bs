import os
import numpy as np
import pytest

from braunschweig.calibration import circuity as c


def test_load_params_has_three_networks():
    params = c.load_circuity_params()
    assert set(params) == {"car", "walk", "pt"}
    assert params["car"]["c_inf"] >= 1.0
    assert params["pt"]["base"] == "car"
    assert params["pt"]["uplift"] > 1.0


def test_curve_monotone_decreasing_toward_asymptote():
    p = {"car": {"c_inf": 1.15, "a": 0.6, "tau": 2.0}}
    d = np.array([0.5, 1.0, 2.0, 5.0, 20.0, 100.0])
    f = c.circuity_factor(d, "car", params=p)
    assert np.all(np.diff(f) < 0)            # strictly decreasing
    assert f[-1] >= 1.15 and f[-1] < 1.16    # approaches c_inf (>=: exp(-50) underflows to 0 in float64)
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
