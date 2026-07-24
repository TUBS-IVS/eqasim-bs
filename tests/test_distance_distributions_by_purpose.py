"""Tests for the per-(purpose x mode) secondary distance distributions refactor.

TDD: tests written BEFORE the implementation.

Three scenarios:
1. OFF path (by_purpose=False) returns the legacy mode-keyed structure.
2. by_purpose=True adds a purpose layer: {purpose: {mode: {bounds, distributions}}}.
3. OFF path is byte-identical to calling _build_mode_distributions on the whole frame
   (the extracted helper must produce the same result as the inlined Step-6 code).
"""

import numpy as np
import pandas as pd


def _synthetic_wege():
    """Minimal MiD Wege frame with the REQUIRED_COLUMNS the stage needs."""
    n = 400
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "H_ID": np.arange(n) // 4,
        "P_ID": np.arange(n) % 4,
        "W_ID": np.arange(n),
        "W_ZWECK": rng.choice([4, 7, 5], size=n),          # shop / leisure / other
        "hvm_imp": rng.choice([1, 2, 3, 4], size=n),       # walk/bike/car_passenger/car
        "wegkm_imp": rng.uniform(0.5, 30.0, size=n),
        "W_SZS": rng.integers(6, 20, n),
        "W_SZM": rng.integers(0, 60, n),
        "W_AZS": rng.integers(6, 20, n),
        "W_AZM": rng.integers(0, 60, n),
        "W_GEW": rng.uniform(0.5, 2.0, size=n),
        "W_ZWD": rng.choice([501, 502, 7704], size=n),
    })


def test_off_returns_legacy_mode_keyed_structure():
    """by_purpose=False: top-level keys are modes, each has bounds + distributions."""
    from braunschweig.popsim.distance_distributions import run

    w = _synthetic_wege()
    legacy = run(w, by_purpose=False)

    # Top-level keys are MODES, not purposes
    assert all(k in ("walk", "bicycle", "car", "pt", "car_passenger") for k in legacy)
    any_mode = next(iter(legacy))
    assert set(legacy[any_mode]) == {"bounds", "distributions"}


def test_by_purpose_adds_a_purpose_layer():
    """by_purpose=True: top-level keys are purposes, each maps to a mode-keyed dict."""
    from braunschweig.popsim.distance_distributions import run

    w = _synthetic_wege()
    bp = run(w, by_purpose=True)

    # Top-level keys are PURPOSES; each maps to a mode-keyed dict like the legacy one
    assert "shop" in bp and "leisure" in bp and "other" in bp
    mode_dict = bp["shop"]
    any_mode = next(iter(mode_dict))
    assert set(mode_dict[any_mode]) == {"bounds", "distributions"}


def test_off_is_byte_identical_to_pre_refactor():
    """run(by_purpose=False) == _build_mode_distributions on the whole prepared frame.

    This verifies that extracting _build_mode_distributions did NOT change the OFF path:
    same mode keys, same bounds arrays, same number of bins, same cdf/values for a
    spot-checked bin.
    """
    from braunschweig.popsim.distance_distributions import (
        run,
        _build_mode_distributions,
        _build_preceding_purpose,
        REQUIRED_COLUMNS,
        PRIMARY_ACTIVITIES,
        DETOUR_FACTOR,
    )
    from braunschweig.popsim.trips import map_mode, map_purpose, mid_time_seconds

    w = _synthetic_wege()

    # Build the prepared frame the same way run() does internally
    df = w.copy()
    df = map_mode(map_purpose(df))
    df["following_purpose"] = df["purpose"]
    df["preceding_purpose"] = _build_preceding_purpose(df)
    df["departure_time"] = mid_time_seconds(df, "W_SZS", "W_SZM")
    df["arrival_time"] = mid_time_seconds(df, "W_AZS", "W_AZM")
    df["travel_time"] = df["arrival_time"] - df["departure_time"]
    midnight_cross = df["travel_time"] < 0
    df.loc[midnight_cross, "travel_time"] += 24 * 3600
    df = df[df["travel_time"] >= 0].copy()
    df["distance"] = df["wegkm_imp"].astype(float) * 1000.0 / DETOUR_FACTOR

    keep_cols = ["mode", "travel_time", "distance", "W_GEW",
                 "preceding_purpose", "following_purpose"]
    if "W_ZWD" in df.columns:
        keep_cols.append("W_ZWD")
    df = df[keep_cols].rename(columns={"W_GEW": "weight"})

    is_primary_both = (
        df["preceding_purpose"].isin(PRIMARY_ACTIVITIES) &
        df["following_purpose"].isin(PRIMARY_ACTIVITIES)
    )
    df = df[~is_primary_both]

    # Build via the helper directly
    expected = _build_mode_distributions(df)

    # Build via run()
    actual = run(w, by_purpose=False)

    # Same mode keys
    assert set(actual) == set(expected), (
        f"Mode key mismatch: actual={set(actual)}, expected={set(expected)}"
    )

    # Mode dict structure keys must be identical
    for mode in actual:
        assert set(actual[mode]) == {"bounds", "distributions"}, (
            f"Actual mode {mode} dict has wrong keys: {set(actual[mode])}"
        )

    for mode in expected:
        # Same bounds array
        np.testing.assert_array_equal(
            actual[mode]["bounds"], expected[mode]["bounds"],
            err_msg=f"bounds mismatch for mode={mode}"
        )
        # Same number of bins
        assert len(actual[mode]["distributions"]) == len(expected[mode]["distributions"]), (
            f"Number of bins mismatch for mode={mode}"
        )
        # True byte-identical check: iterate ALL bins, check both values and cdf exactly
        for i, (a_bin, e_bin) in enumerate(
            zip(actual[mode]["distributions"], expected[mode]["distributions"])
        ):
            np.testing.assert_array_equal(
                a_bin["values"], e_bin["values"],
                err_msg=f"values mismatch for mode={mode}, bin={i}"
            )
            np.testing.assert_array_equal(
                a_bin["cdf"], e_bin["cdf"],
                err_msg=f"cdf mismatch for mode={mode}, bin={i}"
            )


def test_shop_daily_split_adds_subtype_keys():
    from braunschweig.popsim.distance_distributions import run
    w = _synthetic_wege()
    bp = run(w, by_purpose=True, shop_daily_split=True)
    assert "shop_daily" in bp and "shop_non_daily" in bp and "shop" in bp


def test_shop_split_without_by_purpose_raises():
    import pytest
    from braunschweig.popsim.distance_distributions import run
    w = _synthetic_wege()
    with pytest.raises(ValueError, match="requires secondary_distance_by_purpose"):
        run(w, by_purpose=False, shop_daily_split=True)


# --- escort purpose layer (issue #201, Task 4) ------------------------------
#
# Consumes braunschweig.popsim.trips.map_purpose(..., escort_purpose=...) (Task 2):
# W_ZWECK codes in trips.ESCORT_W_ZWECK ({6, 13}) map to the dedicated "escort"
# purpose instead of "other" when the flag is on. These tests verify that the
# escort override, once threaded into this stage's own map_purpose call, produces
# a fully-formed "escort" layer in the by_purpose output (mode -> bounds/distributions),
# leaves the OFF path untouched, and interacts correctly with other_subtype_split
# (whose internal other_escort group is fed by the SAME W_ZWECK=6 legs and therefore
# becomes empty once those legs are reclassified away from following_purpose=="other").

def _mini_wege_with_escort():
    """``_synthetic_wege()`` extended with explicit escort (W_ZWECK 6/13) legs.

    Two legs carry W_ZWECK=6 (Bringen/Holen) and one leg carries W_ZWECK=13 (the
    second MiD-derived escort code; see braunschweig.popsim.trips.ESCORT_W_ZWECK).
    The base fixture's W_ZWECK=5 legs are kept as-is so the aggregate "other" key
    stays populated once escort_purpose reclassifies the new legs away from "other".
    """
    base = _synthetic_wege()
    n_escort = 3
    rng = np.random.default_rng(1)
    escort_rows = pd.DataFrame({
        "H_ID": np.arange(n_escort) + 10_000,
        "P_ID": 0,
        "W_ID": 0,
        "W_ZWECK": [6, 6, 13],
        "hvm_imp": rng.choice([1, 2, 3, 4], size=n_escort),
        "wegkm_imp": rng.uniform(0.5, 30.0, size=n_escort),
        "W_SZS": rng.integers(6, 20, n_escort),
        "W_SZM": rng.integers(0, 60, n_escort),
        "W_AZS": rng.integers(6, 20, n_escort),
        "W_AZM": rng.integers(0, 60, n_escort),
        "W_GEW": rng.uniform(0.5, 2.0, size=n_escort),
        "W_ZWD": rng.choice([501, 502, 7704], size=n_escort),
    })
    return pd.concat([base, escort_rows], ignore_index=True)


def test_escort_layer_present_when_flag_on():
    from braunschweig.popsim.distance_distributions import run

    df = _mini_wege_with_escort()
    out = run(df, by_purpose=True, escort_purpose=True)
    assert "escort" in out
    assert "other" in out  # aggregate stays
    mode = next(iter(out["escort"]))
    assert "bounds" in out["escort"][mode] and "distributions" in out["escort"][mode]


def test_escort_flag_off_output_unchanged():
    from braunschweig.popsim.distance_distributions import run

    df = _mini_wege_with_escort()
    off_default = run(df, by_purpose=True)
    off_explicit = run(df, by_purpose=True, escort_purpose=False)
    assert sorted(off_default.keys()) == sorted(off_explicit.keys())
    assert "escort" not in off_default


def test_escort_on_with_other_subtype_split_skips_other_escort_key():
    from braunschweig.popsim.distance_distributions import run

    df = _mini_wege_with_escort()
    out = run(df, by_purpose=True, escort_purpose=True, other_subtype_split=True)
    # W_ZWECK 6 legs are 'escort' now, so the internal other_escort layer is empty/absent.
    assert "other_escort" not in out
    assert "escort" in out
