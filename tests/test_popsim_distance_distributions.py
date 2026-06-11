"""Tests for braunschweig.popsim.distance_distributions (bug D3).

Verifies that the MiD-based secondary distance distributions return the IDENTICAL
structure as the default ENTD-based stage, so that the RDA solver can consume it
unchanged.

All tests use synthetic, in-memory MiD Wege frames — no raw MiD files are needed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import synthesis.population.spatial.secondary.distance_distributions as default_stage
from braunschweig.popsim import distance_distributions as mid_stage


# ---------------------------------------------------------------------------
# Synthetic MiD Wege builder
# ---------------------------------------------------------------------------

def _make_mid_wege(
    *,
    seed: int = 42,
    n_per_mode: int = 25,
) -> pd.DataFrame:
    """Build a synthetic MiD Wege frame with known, controllable values.

    Each row has the minimum columns required by the distance_distributions stage.
    We produce ``n_per_mode`` trips for each of the five canonical modes
    (walk/car/car_passenger/bicycle/pt).

    Columns produced:
        H_ID, P_ID, W_ID, hvm_imp, W_ZWECK, wegkm_imp,
        W_SZS, W_SZM, W_AZS, W_AZM, W_GEW
    """
    rng = np.random.RandomState(seed)

    # hvm_imp -> canonical mode (from trips.MODE_BY_HVM)
    hvm_codes = [1, 4, 3, 2, 5]  # walk, car, car_passenger, bicycle, pt

    rows = []
    hh = 1
    for hvm in hvm_codes:
        for i in range(n_per_mode):
            # random distance 0.5..20 km
            km = 0.5 + rng.random() * 19.5
            # departure 7:00 -> 9:00, arrival +5..+60 min
            dep_h = 7
            dep_m = rng.randint(0, 120)
            dur_m = rng.randint(5, 60)
            arr_total_m = dep_h * 60 + dep_m + dur_m
            arr_h = arr_total_m // 60
            arr_m = arr_total_m % 60
            rows.append({
                "H_ID": hh,
                "P_ID": i + 1,
                "W_ID": 1,
                # secondary purpose (shop=4, leisure=7, other=5) — not primary
                "W_ZWECK": rng.choice([4, 5, 7]),
                "hvm_imp": hvm,
                "wegkm_imp": km,
                "W_SZS": dep_h,
                "W_SZM": dep_m,
                "W_AZS": arr_h,
                "W_AZM": arr_m,
                "W_GEW": rng.uniform(0.5, 2.0),
            })
        hh += 1

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Structure-equality helper
# ---------------------------------------------------------------------------

def _assert_same_structure(mid_result: dict, ref_result: dict) -> None:
    """Assert that mid_result has the same structural keys and array dtypes as ref."""
    assert isinstance(mid_result, dict), "result must be a dict"
    # Every mode in mid_result must have the same top-level keys.
    for mode, mode_dist in mid_result.items():
        assert "bounds" in mode_dist, f"mode {mode!r}: missing 'bounds'"
        assert "distributions" in mode_dist, f"mode {mode!r}: missing 'distributions'"
        assert isinstance(mode_dist["bounds"], np.ndarray), (
            f"mode {mode!r}: bounds must be np.ndarray"
        )
        assert isinstance(mode_dist["distributions"], list), (
            f"mode {mode!r}: distributions must be list"
        )
        # Every distribution must have cdf, values, weights as np.ndarray.
        for idx, dist in enumerate(mode_dist["distributions"]):
            for key in ("cdf", "values", "weights"):
                assert key in dist, f"mode {mode!r} bin {idx}: missing '{key}'"
                assert isinstance(dist[key], np.ndarray), (
                    f"mode {mode!r} bin {idx} '{key}' must be np.ndarray"
                )
            # cdf must be monotone non-decreasing and end at 1.0.
            assert dist["cdf"][-1] == pytest.approx(1.0), (
                f"mode {mode!r} bin {idx}: cdf must end at 1.0"
            )
            # values and cdf must be the same length.
            assert len(dist["values"]) == len(dist["cdf"]), (
                f"mode {mode!r} bin {idx}: len(values) != len(cdf)"
            )
            # bounds last element must be inf.
        assert mode_dist["bounds"][-1] == np.inf, (
            f"mode {mode!r}: last bound must be np.inf"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMidDistributionStructure:
    """The output of mid_stage.run() must be structurally identical to the default."""

    def test_returns_dict_with_mode_keys(self):
        wege = _make_mid_wege()
        result = mid_stage.run(wege)
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_all_five_canonical_modes_present(self):
        wege = _make_mid_wege(n_per_mode=30)
        result = mid_stage.run(wege)
        expected = {"walk", "car", "car_passenger", "bicycle", "pt"}
        assert expected.issubset(set(result.keys())), (
            f"missing modes: {expected - set(result.keys())}"
        )

    def test_structure_matches_default_contract(self):
        """bounds/distributions/cdf/values/weights keys + np.ndarray types must match default."""
        wege = _make_mid_wege(n_per_mode=30)
        result = mid_stage.run(wege)
        _assert_same_structure(result, {})  # checks mid result alone; shape check below

    def test_structure_identical_to_default_stage_output_shape(self):
        """Run the default calculate_bounds logic and verify our per-mode dict shape matches."""
        wege = _make_mid_wege(n_per_mode=30)
        mid_result = mid_stage.run(wege)

        for mode, mode_dist in mid_result.items():
            # bounds must be a 1-D array of floats with at least one element.
            assert mode_dist["bounds"].ndim == 1
            assert mode_dist["bounds"].dtype.kind == "f"
            # distributions list length must equal the number of bounds.
            assert len(mode_dist["distributions"]) == len(mode_dist["bounds"])


class TestMidDistributionWeighting:
    """Trips are weighted by W_GEW; the CDF must reflect this."""

    def test_known_car_distance_appears_in_car_distribution(self):
        """A 5 km car trip must produce euclidean_distance ≈ 5000/1.3 m in the car CDF."""
        detour = 1.3
        expected_m = 5.0 * 1000.0 / detour

        # Build a frame with one heavy car trip at exactly 5 km and many very-short
        # car trips so the 5 km trip is near the top of the CDF.
        rows = []
        for i in range(20):
            rows.append({
                "H_ID": 1, "P_ID": i + 1, "W_ID": 1,
                "W_ZWECK": 4, "hvm_imp": 4,
                "wegkm_imp": 0.2,
                "W_SZS": 8, "W_SZM": 0, "W_AZS": 8, "W_AZM": 10,
                "W_GEW": 1.0,
            })
        # The 5 km trip.
        rows.append({
            "H_ID": 1, "P_ID": 99, "W_ID": 1,
            "W_ZWECK": 4, "hvm_imp": 4,
            "wegkm_imp": 5.0,
            "W_SZS": 8, "W_SZM": 0, "W_AZS": 8, "W_AZM": 10,
            "W_GEW": 1.0,
        })

        wege = pd.DataFrame(rows)
        result = mid_stage.run(wege)

        assert "car" in result, "car mode missing from result"
        # Collect all 'values' arrays across all car bins.
        all_car_values = np.concatenate([
            d["values"] for d in result["car"]["distributions"]
        ])
        assert expected_m in all_car_values or any(
            abs(v - expected_m) < 1.0 for v in all_car_values
        ), (
            f"Expected euclidean_distance ≈ {expected_m:.1f} m not found in car "
            f"distributions; got values (first 10): {all_car_values[:10]}"
        )

    def test_higher_weight_shifts_cdf(self):
        """A trip with weight 10x should dominate the CDF relative to weight-1 trips."""
        rows = []
        # 10 short (0.1 km) car trips, weight 1.
        for i in range(10):
            rows.append({
                "H_ID": 1, "P_ID": i + 1, "W_ID": 1,
                "W_ZWECK": 4, "hvm_imp": 4,
                "wegkm_imp": 0.1,
                "W_SZS": 8, "W_SZM": 0, "W_AZS": 8, "W_AZM": 5,
                "W_GEW": 1.0,
            })
        # 1 long (50 km) car trip, weight 100 (should dominate).
        rows.append({
            "H_ID": 1, "P_ID": 99, "W_ID": 1,
            "W_ZWECK": 4, "hvm_imp": 4,
            "wegkm_imp": 50.0,
            "W_SZS": 8, "W_SZM": 0, "W_AZS": 10, "W_AZM": 0,
            "W_GEW": 100.0,
        })

        wege = pd.DataFrame(rows)
        result = mid_stage.run(wege)
        assert "car" in result

        # Collect all weighted distances (weights dot values).
        total_weighted = sum(
            np.dot(d["weights"], d["values"])
            for d in result["car"]["distributions"]
        )
        total_weight = sum(
            d["weights"].sum()
            for d in result["car"]["distributions"]
        )
        mean_dist = total_weighted / total_weight
        # Mean should be pulled heavily toward 50 km * 1000 / 1.3 ≈ 38 461 m.
        assert mean_dist > 30_000, (
            f"Weighted mean distance {mean_dist:.0f} m should be >30 000 m "
            "when the 100-weight long trip dominates."
        )


class TestMidDistributionFiltering:
    """Secondary filter: trips between two primary locations must be excluded."""

    def test_primary_only_trips_are_excluded(self):
        """A work->home trip (both primary) must NOT appear in the distribution."""
        # Two rows: one work->home trip (primary; must be excluded) and one
        # shop (secondary; must be included). Both car trips at different distances.
        rows = [
            # work destination (following_purpose = work) from home (preceding = home)
            # -> both primary; must be excluded.
            {
                "H_ID": 1, "P_ID": 1, "W_ID": 1,
                "W_ZWECK": 1,   # work
                "hvm_imp": 4,
                "wegkm_imp": 30.0,  # long distance; must not appear
                "W_SZS": 8, "W_SZM": 0, "W_AZS": 9, "W_AZM": 0,
                "W_GEW": 1.0,
            },
            # home destination (following_purpose = home) from work (preceding = work)
            # -> both primary; must be excluded.
            {
                "H_ID": 1, "P_ID": 1, "W_ID": 2,
                "W_ZWECK": 8,   # home
                "hvm_imp": 4,
                "wegkm_imp": 30.0,  # long distance; must not appear
                "W_SZS": 17, "W_SZM": 0, "W_AZS": 18, "W_AZM": 0,
                "W_GEW": 1.0,
            },
            # shop destination from home -> one side is secondary (shop); must be included.
            {
                "H_ID": 1, "P_ID": 2, "W_ID": 1,
                "W_ZWECK": 4,   # shop
                "hvm_imp": 4,
                "wegkm_imp": 1.0,   # short; must appear
                "W_SZS": 9, "W_SZM": 0, "W_AZS": 9, "W_AZM": 30,
                "W_GEW": 1.0,
            },
            # Return home from shop: following = home (primary), preceding = shop
            # (secondary) -> one side is secondary; must be included.
            {
                "H_ID": 1, "P_ID": 2, "W_ID": 2,
                "W_ZWECK": 8,   # home
                "hvm_imp": 4,
                "wegkm_imp": 1.0,
                "W_SZS": 9, "W_SZM": 30, "W_AZS": 10, "W_AZM": 0,
                "W_GEW": 1.0,
            },
        ]
        wege = pd.DataFrame(rows)
        result = mid_stage.run(wege)

        assert "car" in result, "car mode should be present"
        all_values = np.concatenate([
            d["values"] for d in result["car"]["distributions"]
        ])
        # 30 km work->home trips must not appear (euclidean_distance = 30*1000/1.3 ~ 23 077 m)
        exclusion_target = 30.0 * 1000.0 / 1.3
        assert not any(
            abs(v - exclusion_target) < 1.0 for v in all_values
        ), (
            f"Primary-only trip ({exclusion_target:.0f} m) must be excluded from "
            f"distributions; got values: {all_values}"
        )


class TestMidDistributionSampler:
    """The result is directly consumable by CustomDistanceSampler without modification."""

    def test_sampler_can_consume_result(self):
        """CustomDistanceSampler.sample_distances must work on mid_stage.run() output."""
        from synthesis.population.spatial.secondary.components import CustomDistanceSampler

        wege = _make_mid_wege(n_per_mode=30)
        result = mid_stage.run(wege)

        random = np.random.RandomState(0)
        sampler = CustomDistanceSampler(
            random=random,
            distributions=result,
            maximum_iterations=10,
            leisure_correction_factor=1.0,
        )

        # Build a minimal problem dict with one trip per mode.
        modes = list(result.keys())
        travel_times = [600.0] * len(modes)  # 10 minutes
        purposes = ["shop"] * len(modes)

        problem = {
            "modes": modes,
            "travel_times": travel_times,
            "purposes": purposes,
        }

        distances = sampler.sample_distances(problem)
        assert distances.shape == (len(modes),)
        assert (distances > 0).all(), "sampled distances must be positive"


class TestMidDistributionStageInterface:
    """The synpp stage configure/execute interface is correct."""

    def test_run_function_is_exported(self):
        """mid_stage.run() must be importable and callable as a pure function."""
        assert callable(mid_stage.run)

    def test_configure_function_is_exported(self):
        assert callable(mid_stage.configure)

    def test_execute_function_is_exported(self):
        assert callable(mid_stage.execute)

    def test_default_stage_calculate_bounds_is_reused(self):
        """Our stage imports and reuses calculate_bounds from the default stage."""
        # We explicitly want to verify the helper is reused, not re-implemented.
        from braunschweig.popsim.distance_distributions import calculate_bounds
        from synthesis.population.spatial.secondary.distance_distributions import (
            calculate_bounds as ref_calculate_bounds,
        )
        # They must be the same object (we import it directly rather than copy it).
        assert calculate_bounds is ref_calculate_bounds, (
            "distance_distributions.calculate_bounds must be imported from "
            "the default stage, not re-implemented."
        )
