"""Weighted, distance-matched and Kreis-tilted donor draw of the worker in-commuters (ADR-0191)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.data.cordon import donor_draw as dd

KM = [5.0, 10.0, 20.0, 40.0, 80.0]


def _pool():
    # one-person households, so income per person = household income
    return pd.DataFrame({
        "donor_weight": [1.0, 1.0, 1.0, 1.0, 1.0],
        "household_income_eur": [1000.0, 3000.0, 2000.0, 4000.0, 6000.0],
        "household_size": [1, 1, 1, 1, 1],
        "commute_distance_km": KM,
    })


def test_tilt_reaches_the_target_mean_and_leaves_the_weights_alone_without_a_shift():
    # two values: 1000 * p1 + 3000 * p2 = 2500 has exactly one solution
    tilted = dd.tilt_to_mean([1.0, 1.0], [1000.0, 3000.0], 2500.0)
    assert tilted.probabilities == pytest.approx([0.25, 0.75], abs=1e-6)
    assert tilted.reached and tilted.tilt > 0
    assert tilted.effective_size == pytest.approx(1.0 / (0.25 ** 2 + 0.75 ** 2), rel=1e-5)
    # target = weighted mean (3 * 1000 + 1 * 3000) / 4 = 1500: no tilt, the weights stay
    same = dd.tilt_to_mean([3.0, 1.0], [1000.0, 3000.0], 1500.0)
    assert same.probabilities == pytest.approx([0.75, 0.25], abs=1e-6)
    assert same.tilt == pytest.approx(0.0, abs=1e-6) and same.reached


@pytest.mark.parametrize("values, target", [
    ([1000.0, 3000.0], 3500.0),     # above the largest value
    ([2000.0, 2000.0], 2500.0),     # no spread to tilt
])
def test_tilt_reports_a_target_it_cannot_reach(values, target):
    tilted = dd.tilt_to_mean([1.0, 1.0], values, target)
    assert not tilted.reached
    assert tilted.probabilities.sum() == pytest.approx(1.0)


def test_tilt_treats_equal_values_with_inexact_weights_as_no_spread():
    # rounding leaves a weighted sd of about 1e-13 here; that must not become a tilt direction
    tilted = dd.tilt_to_mean([0.3, 0.7], [2000.0, 2000.0], 2000.0)
    assert tilted.probabilities == pytest.approx([0.3, 0.7])
    assert tilted.reached


@pytest.mark.parametrize("weights, target_km, min_size, expected", [
    ([1, 1, 1, 1, 1], 20.0, 1.0, [2]),              # the donor at 20 km alone is enough
    ([1, 1, 1, 1, 1], 20.0, 3.0, [1, 2, 3]),        # 10 and 40 km are equally near in log distance
    ([1, 1, 1, 1, 1], 80.0, 3.0, [2, 3, 4]),        # at the far end the window grows downwards
    ([1, 1, 4, 1, 1], 20.0, 3.0, [0, 1, 2, 3, 4]),  # a heavy donor adds little effective size
    ([1, 1, 1, 1, 1], 20.0, 10.0, [0, 1, 2, 3, 4]), # the minimum is out of reach: the whole pool
])
def test_window_takes_the_nearest_commutes_until_the_effective_size_is_reached(
        weights, target_km, min_size, expected):
    window = dd.nearest_distance_window(KM, np.array(weights, dtype=float), target_km, min_size)
    assert window.tolist() == expected


def test_cell_probabilities_follow_commute_window_kreis_scale_and_weights():
    sampler = dd.KreisDonorSampler(_pool(), {"A": 1.1}, min_effective_pool=2.0)
    # 5 km: window = donors at 5 and 10 km, base 2000, target 2200 -> P(donor 1) = 0.6
    assert sampler.cell_probabilities("A", 5.0).to_dict() == pytest.approx({0: 0.4, 1: 0.6}, abs=1e-6)
    # 80 km: window = donors at 40 and 80 km, base 5000, target 5500 -> P(donor 4) = 0.75
    assert sampler.cell_probabilities("A", 80.0).to_dict() == pytest.approx({3: 0.25, 4: 0.75}, abs=1e-6)
    # a Kreis without a scale: the weights of the window, untilted
    weighted = dd.KreisDonorSampler(_pool().assign(donor_weight=[3.0, 1.0, 1.0, 1.0, 1.0]), {"A": 1.1},
                                    min_effective_pool=1.5)
    assert weighted.cell_probabilities("Z", 5.0).to_dict() == pytest.approx({0: 0.75, 1: 0.25})


def test_an_int_keyed_scale_table_is_found_for_the_string_kreis_of_the_agent():
    keyed_by_int = dd.KreisDonorSampler(_pool(), {3101: 1.1}, min_effective_pool=2.0)
    keyed_by_str = dd.KreisDonorSampler(_pool(), {"3101": 1.1}, min_effective_pool=2.0)
    kreis, km = np.array(["3101"]), np.array([5.0])
    assert keyed_by_int.report(kreis, km)["n_without_kreis_scale"] == 0
    assert keyed_by_int.cell_probabilities("3101", 5.0).to_dict() == pytest.approx(
        keyed_by_str.cell_probabilities("3101", 5.0).to_dict())


def test_draw_stays_in_the_window_is_seeded_and_reports_every_fallback():
    sampler = dd.KreisDonorSampler(_pool(), {"A": 1.1, "X": 5.0}, min_effective_pool=2.0)
    kreis = np.array(["A", "A", "Z", "X"])
    km = np.array([5.0, 80.0, 5.0, 80.0])
    rows = sampler.draw(kreis, km, np.random.default_rng(7))
    assert set(rows[[0, 2]]) <= {0, 1} and set(rows[[1, 3]]) <= {3, 4}
    assert (rows == sampler.draw(kreis, km, np.random.default_rng(7))).all()
    report = sampler.report(kreis, km)
    assert report["n_agents"] == 4 and report["tilted"] is True and report["distance_matched"] is True
    assert report["n_without_kreis_scale"] == 1       # Kreis Z
    assert report["n_target_not_reached"] == 1        # Kreis X: 5000 * 5 is above every donor
    assert report["n_whole_pool_window"] == 0
    assert 1.59 <= report["min_effective_size"] <= 2.0   # (80 km, A): 1 / (0.25 ** 2 + 0.75 ** 2)
    # Kreis X cannot reach its target: it is drawn from the untilted window weights
    assert sampler.cell_probabilities("X", 80.0).to_dict() == pytest.approx({3: 0.5, 4: 0.5})
    assert report["max_window_ratio"] == pytest.approx(2.0)   # 5..10 km and 40..80 km


def test_without_a_minimum_pool_every_donor_is_drawn_from_and_a_too_large_minimum_is_counted():
    plain = dd.KreisDonorSampler(_pool().drop(columns="commute_distance_km"), None, min_effective_pool=None)
    assert plain.cell_probabilities("A", 30.0).to_dict() == pytest.approx({i: 0.2 for i in range(5)})
    report = plain.report(np.array(["A"]), np.array([30.0]))
    assert report["tilted"] is False and report["distance_matched"] is False
    starved = dd.KreisDonorSampler(_pool(), None, min_effective_pool=10.0)
    assert starved.report(np.array(["A", "B"]), np.array([5.0, 80.0]))["n_whole_pool_window"] == 2


def test_the_window_is_matched_on_the_named_distance_column_not_on_the_commute_column():
    # the commute column is a decoy at the opposite end of the pool; only education_distance_km may decide
    pool = _pool().rename(columns={"commute_distance_km": "education_distance_km"}).assign(
        commute_distance_km=KM[::-1])
    sampler = dd.KreisDonorSampler(pool, None, min_effective_pool=2.0, distance_column="education_distance_km")
    assert sampler.cell_probabilities("X", 5.0).to_dict() == pytest.approx({0: 0.5, 1: 0.5})
    with pytest.raises(ValueError, match="education_distance_km"):
        dd.KreisDonorSampler(_pool(), None, min_effective_pool=2.0, distance_column="education_distance_km")


def test_invalid_donor_weight_or_agent_commute_fails():
    with pytest.raises(ValueError, match="donor_weight"):
        dd.KreisDonorSampler(_pool().assign(donor_weight=[1.0, -1.0, 1.0, 1.0, 1.0]), {}, min_effective_pool=2.0)
    with pytest.raises(ValueError, match="commute_distance_km"):
        dd.KreisDonorSampler(_pool().drop(columns="commute_distance_km"), {}, min_effective_pool=2.0)
    sampler = dd.KreisDonorSampler(_pool(), {}, min_effective_pool=2.0)
    with pytest.raises(ValueError, match="commute"):
        sampler.draw(np.array(["A"]), np.array([0.0]), np.random.default_rng(0))
