"""Differential contracts for the array-backed typed-home matching kernels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.locations import building_typing as bt
from braunschweig.synthesis.locations import home_matcher as hm


def _rng_state(rng):
    algorithm, state, position, has_gauss, cached_gaussian = rng.get_state()
    return algorithm, state.tobytes(), position, has_gauss, cached_gaussian


def _assert_frame_equal_exact(actual, expected):
    assert list(actual.columns) == list(expected.columns)
    assert actual.dtypes.equals(expected.dtypes)
    pd.testing.assert_frame_equal(actual, expected)


def _cell_inputs():
    households = pd.DataFrame(
        {
            "household_id": ["e2", "e1", "m2", "m1", "s1", "extra"],
            "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh", "sonst", "sonst"],
            "household_size": [2.0, 4.0, np.nan, 3.0, 1.0, 1.0],
        }
    )
    slots = pd.DataFrame(
        {
            "slot_id": [0, 1, 2, 3, 4],
            "building_id": [11, 10, 20, 21, 30],
            "btype": ["efh_zfh", "efh_zfh", "mfh", "mfh", "sonst"],
            "size": [80.0, 120.0, np.nan, 110.0, 40.0],
        }
    )
    return households, slots


def test_array_matcher_matches_legacy_on_ties_nans_and_overcapacity():
    """The optimized matcher preserves each legacy assignment and report field."""
    households, slots = _cell_inputs()
    legacy_rng = np.random.RandomState(123)
    array_rng = np.random.RandomState(123)
    expected, expected_report = hm.match_cell(households, slots, legacy_rng)

    actual, actual_report = hm.match_cell_arrays(households, slots, array_rng)

    _assert_frame_equal_exact(actual, expected)
    assert actual_report == expected_report
    assert _rng_state(array_rng) == _rng_state(legacy_rng)


@pytest.mark.parametrize("nullable_dtype", ["object", "string"])
def test_array_matcher_matches_legacy_with_nullable_types(nullable_dtype):
    """Nullable household and slot types remain excluded like pandas groupby/filter."""
    households = pd.DataFrame(
        {
            "household_id": [1, 2],
            "btype": pd.Series(["efh_zfh", pd.NA], dtype=nullable_dtype),
            "household_size": [2.0, 1.0],
        }
    )
    slots = pd.DataFrame(
        {
            "slot_id": [0, 1],
            "building_id": [10, 11],
            "btype": pd.Series(["efh_zfh", pd.NA], dtype=nullable_dtype),
            "size": [80.0, 70.0],
        }
    )
    expected, expected_report = hm.match_cell(
        households, slots, np.random.RandomState(9)
    )

    actual, actual_report = hm.match_cell_arrays(
        households, slots, np.random.RandomState(9)
    )

    _assert_frame_equal_exact(actual, expected)
    assert actual_report == expected_report


@pytest.mark.parametrize(
    ("typed", "whg", "occupied", "size_hist"),
    [
        (
            pd.DataFrame({"building_id": [], "btype": [], "area_m2": [], "height_m": []}),
            {"efh_zfh": 0, "mfh": 0, "sonst": 0}, 0, [],
        ),
        (
            pd.DataFrame({
                "building_id": [1, 2, 3, 4],
                "btype": ["efh_zfh", "mfh", "sonst", "efh_zfh"],
                "area_m2": [100.0, 100.0, np.nan, 10.0],
                "height_m": [np.nan, 9.0, np.nan, np.nan],
            }),
            {"efh_zfh": 2.5, "mfh": 2.5, "sonst": 2.5}, 5.0,
            [(95.0, 2), (55.0, 1)],
        ),
        (
            pd.DataFrame({
                "building_id": [9, 9], "btype": ["efh_zfh", "efh_zfh"],
                "area_m2": [0.0, 0.0], "height_m": [np.nan, np.nan],
            }),
            {"efh_zfh": 4.0, "mfh": 0.0, "sonst": 0.0}, 3.0, [],
        ),
    ],
)
def test_array_slot_builder_matches_legacy_edge_cases(typed, whg, occupied, size_hist):
    """Hamilton allocation, ties, missing classes and empty histograms stay exact."""
    legacy_rng = np.random.RandomState(17)
    array_rng = np.random.RandomState(17)
    expected = bt.build_slots(typed, whg, occupied, size_hist, legacy_rng)

    actual = bt.build_slots_arrays(typed, whg, occupied, size_hist, array_rng)

    _assert_frame_equal_exact(actual, expected)
    assert _rng_state(array_rng) == _rng_state(legacy_rng)


def test_array_slot_builder_preserves_legacy_quicksort_tie_order():
    """Equal btype keys still retain pandas' observable quicksort permutation."""
    typed = pd.DataFrame(
        {
            "building_id": np.arange(8),
            "btype": ["efh_zfh"] * 8,
            "area_m2": [100.0] * 8,
            "height_m": [np.nan] * 8,
        }
    )
    whg = {"efh_zfh": 24.0, "mfh": 0.0, "sonst": 0.0}
    expected = bt.build_slots(typed, whg, 24.0, [], np.random.RandomState(17))

    actual = bt.build_slots_arrays(typed, whg, 24.0, [], np.random.RandomState(17))

    _assert_frame_equal_exact(actual, expected)


def test_array_kernels_match_legacy_for_fixed_seed_randomized_cells():
    """A deterministic corpus pins ordering across heterogeneous small cells."""
    source = np.random.RandomState(871)
    for cell in range(40):
        n_buildings = int(source.randint(0, 9))
        n_households = int(source.randint(0, 15))
        building_types = source.choice(hm.TYPES, size=n_buildings)
        typed = pd.DataFrame(
            {
                "building_id": source.randint(0, 5, size=n_buildings),
                "btype": building_types,
                "area_m2": source.choice([0.0, 25.0, 80.0, np.nan], size=n_buildings),
                "height_m": source.choice([np.nan, 3.0, 12.0], size=n_buildings),
            }
        )
        whg = dict(zip(hm.TYPES, source.uniform(-1.0, 6.0, size=3)))
        occupied = float(source.choice([0.0, 1.0, 3.0, 7.0]))
        size_hist = [(float(value), int(count)) for value, count in zip(
            source.choice([35.0, 65.0, 95.0], size=3), source.randint(0, 4, size=3)
        )]
        slots_legacy = bt.build_slots(typed, whg, occupied, size_hist, np.random.RandomState(cell))
        slots_array = bt.build_slots_arrays(typed, whg, occupied, size_hist, np.random.RandomState(cell))
        _assert_frame_equal_exact(slots_array, slots_legacy)

        households = pd.DataFrame(
            {
                "household_id": [f"{cell}-{index}" for index in range(n_households)],
                "btype": source.choice(hm.TYPES, size=n_households),
                "household_size": source.choice([1.0, 2.0, 4.0, np.nan], size=n_households),
            }
        )
        expected, expected_report = hm.match_cell(households, slots_legacy, np.random.RandomState(cell))
        actual, actual_report = hm.match_cell_arrays(households, slots_array, np.random.RandomState(cell))
        _assert_frame_equal_exact(actual, expected)
        assert actual_report == expected_report
