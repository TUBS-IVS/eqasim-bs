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
    """Minimal MiD Wege frame with the REQUIRED_COLUMNS the stage needs.

    ``kernwo`` / ``W_RBW`` put every leg INSIDE the weekday diary universe (a
    weekday reporting day, no rbW summary record -- see
    braunschweig.popsim.trips.weekday_diary_leg_mask), so this frame is
    unaffected by ``weekday_legs_only``; the out-of-universe legs live in
    :func:`_wege_with_weekend_and_rbw_legs` below.
    """
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
        "kernwo": 2,
        "W_RBW": 0,
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


# --- escort_passive_education layer (issue #256) ----------------------------
#
# Consumes braunschweig.popsim.trips.map_purpose(..., escort_passive_education=...)
# (Task 2): when escort_passive_education is also on, the passive escort leg
# (W_ZWECK 13) maps to "education" instead of "escort", so this stage's own
# map_purpose call (threaded with the same flag) must produce a dedicated
# "education" layer for it and stop counting it under "escort".

def _mini_wege_with_escort_second_leg():
    """Like ``_mini_wege_with_escort`` but each escort leg is the SECOND trip of
    a two-trip chain (first trip: W_ZWECK=7 leisure), not an isolated
    single-trip diary.

    This is required, not just stylistic: an ISOLATED single-trip diary starts
    at "home" (diary-starts-at-home convention), so once W_ZWECK=13 is
    relabelled to "education" it becomes a structurally PRIMARY home<->education
    trip -- run()'s Step 5 correctly excludes primary-only (home/work/education
    <-> home/work/education) chains from the SECONDARY distance distributions
    this stage builds (verified empirically: with ``_mini_wege_with_escort``'s
    isolated escort rows, the relabelled leg disappears from the output
    entirely, landing under neither "escort" nor "education", instead of
    demonstrating the split under test). Giving each escort leg a non-primary
    (leisure) preceding trip keeps it a genuine secondary trip regardless of
    which purpose it is mapped to, so the "education" vs "escort" split is
    actually observable in the output.
    """
    base = _synthetic_wege()
    n_escort = 3
    rng = np.random.default_rng(1)
    household_ids = np.arange(n_escort) + 10_000
    leisure_legs = pd.DataFrame({
        "H_ID": household_ids, "P_ID": 0, "W_ID": 0,
        "W_ZWECK": 7,  # leisure; makes the escort leg's preceding_purpose non-primary
        "hvm_imp": rng.choice([1, 2, 3, 4], size=n_escort),
        "wegkm_imp": rng.uniform(0.5, 30.0, size=n_escort),
        "W_SZS": rng.integers(6, 12, n_escort), "W_SZM": rng.integers(0, 60, n_escort),
        "W_AZS": rng.integers(6, 12, n_escort), "W_AZM": rng.integers(0, 60, n_escort),
        "W_GEW": rng.uniform(0.5, 2.0, size=n_escort),
        "W_ZWD": rng.choice([501, 502, 7704], size=n_escort),
    })
    escort_legs = pd.DataFrame({
        "H_ID": household_ids, "P_ID": 0, "W_ID": 1,
        "W_ZWECK": [6, 6, 13],
        "hvm_imp": rng.choice([1, 2, 3, 4], size=n_escort),
        "wegkm_imp": rng.uniform(0.5, 30.0, size=n_escort),
        "W_SZS": rng.integers(12, 20, n_escort), "W_SZM": rng.integers(0, 60, n_escort),
        "W_AZS": rng.integers(12, 20, n_escort), "W_AZM": rng.integers(0, 60, n_escort),
        "W_GEW": rng.uniform(0.5, 2.0, size=n_escort),
        "W_ZWD": rng.choice([501, 502, 7704], size=n_escort),
    })
    return pd.concat([base, leisure_legs, escort_legs], ignore_index=True)


def _count_legs(out: dict, key: str) -> int:
    """Total leg count under a purpose key, summed across modes and time bins."""
    if key not in out:
        return 0
    return sum(
        len(entry["values"])
        for mode_dict in out[key].values()
        for entry in mode_dict["distributions"]
    )


def test_escort_passive_education_relabels_w_zweck_13_to_education():
    """With escort_passive_education=True, the passive escort leg (W_ZWECK 13)
    must land under the 'education' purpose layer and must NOT be counted under
    'escort'; the two active W_ZWECK=6 legs stay under 'escort'.

    The OFF-path (escort_passive_education=False/default: both W_ZWECK 6 and 13
    land under 'escort', no 'education' key) is the pre-existing #201 behaviour
    already pinned by test_escort_layer_present_when_flag_on and
    test_escort_flag_off_output_unchanged above (same escort_purpose flag, same
    underlying map_purpose call); not duplicated here as a second test.
    """
    from braunschweig.popsim.distance_distributions import run

    df = _mini_wege_with_escort_second_leg()
    out = run(df, by_purpose=True, escort_purpose=True, escort_passive_education=True)

    assert "education" in out
    assert _count_legs(out, "education") == 1  # the single relabelled W_ZWECK=13 leg
    assert _count_legs(out, "escort") == 2      # only the two active W_ZWECK=6 legs


# ---------------------------------------------------------------------------
# Issue #373 fix round 1, Important finding 3c: w_zweck_10_as_leisure must reach
# the map_purpose() call inside run()'s purpose-harmonisation step.
# ---------------------------------------------------------------------------

def test_run_forwards_w_zweck_10_as_leisure_to_map_purpose(monkeypatch):
    from braunschweig.popsim import distance_distributions as dd

    captured = {}
    real_map_purpose = dd.map_purpose

    def spy(*args, **kwargs):
        captured["w_zweck_10_as_leisure"] = kwargs.get("w_zweck_10_as_leisure")
        return real_map_purpose(*args, **kwargs)

    monkeypatch.setattr(dd, "map_purpose", spy)
    dd.run(_synthetic_wege(), w_zweck_10_as_leisure=True)
    assert captured["w_zweck_10_as_leisure"] is True


def test_run_forwards_the_passive_escort_pairing_keywords_to_map_purpose(monkeypatch):
    """The distance layer must map the passive escort legs the way the plan does (issue #372
    task 4), or a leg the plan sends to "shop" draws its distance from the education layer."""
    from braunschweig.popsim import distance_distributions as dd

    captured = {}
    real_map_purpose = dd.map_purpose

    def capturing_map_purpose(wege, **kwargs):
        captured["escort_passive_from_adult"] = kwargs.get("escort_passive_from_adult")
        captured["passive_pair_max_gap_minutes"] = kwargs.get("passive_pair_max_gap_minutes")
        kwargs["escort_passive_from_adult"] = False
        return real_map_purpose(wege, **kwargs)

    monkeypatch.setattr(dd, "map_purpose", capturing_map_purpose)
    dd.run(_synthetic_wege(), escort_purpose=True, escort_passive_from_adult=True,
           passive_pair_max_gap_minutes=20.0)
    assert captured["escort_passive_from_adult"] is True
    assert captured["passive_pair_max_gap_minutes"] == 20.0


# ---------------------------------------------------------------------------
# Issue #373 task 2 (ruling C-R20/C-R21): the passive-escort pairing's candidate
# universe must follow the trip build's leg-drop flags (exclude_rbw_legs /
# drop_leading_arrive_home_leg), or a passive leg whose nearest-in-time adult leg
# is a leg the trip build actually drops resolves a purpose the plan never
# realises. The DISTANCE POOL itself must stay untouched by those flags -- only
# the pairing's candidate universe is restricted.
# ---------------------------------------------------------------------------

def _leg_drop_pairing_fixture():
    """One household: the adult's FIRST leg is a leading arrive-home leg (dropped by
    drop_leading_arrive_home_leg) departing the SAME minute as the child's passive leg;
    the adult's SECOND leg is the real active escort (Bringen/Holen) leg 5 minutes later.
    The child also has a leading LEISURE leg so its passive leg's preceding_purpose is
    non-primary regardless of which purpose the pairing gives it (otherwise a "home"
    <-> "home" or "home" <-> "education" trip would be excluded by run()'s Step 5
    primary-only filter before the bug/fix distinction is even observable)."""
    return pd.DataFrame({
        "H_ID": [1, 1, 1, 1], "P_ID": [1, 1, 2, 2], "W_ID": [1, 2, 1, 2],
        "W_ZWECK": [8, 6, 7, 13],
        "hvm_imp": [4, 4, 1, 1],
        "wegkm_imp": [2.0, 3.0, 1.5, 3.5],
        "W_SZS": [8, 8, 7, 8], "W_SZM": [0, 5, 0, 0],
        "W_AZS": [8, 8, 7, 8], "W_AZM": [10, 20, 30, 10],
        "W_SO1": [2, 809, 809, 809], "HP_ALTER": [35, 35, 5, 5], "W_GEW": [1.0] * 4,
    })


def test_run_pairs_on_the_unfiltered_frame_when_the_leg_drop_flags_are_off():
    """Code default (both leg-drop flags False, byte-identical to before this fix): the
    pairing considers the leading arrive-home leg (gap 0 min) as a candidate even though
    the trip build would drop it, so the child's passive leg is mis-paired to 'home'."""
    from braunschweig.popsim.distance_distributions import run

    out = run(_leg_drop_pairing_fixture(), by_purpose=True, escort_purpose=True,
             escort_passive_education=True, escort_passive_from_adult=True)
    assert _count_legs(out, "home") == 1
    assert _count_legs(out, "education") == 0


def test_run_restricts_the_pairing_to_the_trip_builds_candidate_universe_when_leg_drop_flags_are_on():
    """With drop_leading_arrive_home_leg=True (the production default, configs/base_bs.yml),
    the pairing's candidate universe excludes the dropped leading arrive-home leg, so the
    child's passive leg pairs with the active escort leg instead and lands under
    'education' -- matching what the trip build itself would realise. The distance POOL
    is unaffected: all 4 legs (2 primary-excluded, 2 secondary) are processed exactly as
    before; only the pairing's candidate universe changed."""
    from braunschweig.popsim.distance_distributions import run

    out = run(_leg_drop_pairing_fixture(), by_purpose=True, escort_purpose=True,
             escort_passive_education=True, escort_passive_from_adult=True,
             exclude_rbw_legs=False, drop_leading_arrive_home_leg=True)
    assert _count_legs(out, "home") == 0
    assert _count_legs(out, "education") == 1
    # The escort leg (adult, W_ZWECK 6) is still present under "escort".
    assert _count_legs(out, "escort") == 1


def test_run_forwards_the_leg_drop_flags_as_a_pairing_candidate_mask(monkeypatch):
    """The mask passed to map_purpose must be built from
    trips.legs_kept_by_the_trip_build with the SAME two flags, not re-derived."""
    from braunschweig.popsim import distance_distributions as dd

    captured = {}
    real_map_purpose = dd.map_purpose

    def capturing_map_purpose(wege, **kwargs):
        captured["pairing_candidate_mask"] = kwargs.get("pairing_candidate_mask")
        return real_map_purpose(wege, **kwargs)

    monkeypatch.setattr(dd, "map_purpose", capturing_map_purpose)
    df = _leg_drop_pairing_fixture()
    dd.run(df, escort_purpose=True, escort_passive_education=True,
          escort_passive_from_adult=True, exclude_rbw_legs=False,
          drop_leading_arrive_home_leg=True)
    mask = captured["pairing_candidate_mask"]
    assert mask is not None
    assert isinstance(mask, pd.Series)
    # The adult's leading arrive-home leg (row 0, H_ID=1/P_ID=1/W_ID=1) must be excluded;
    # every other leg stays a candidate.
    assert mask.loc[0] == False  # noqa: E712 (explicit bool compare reads clearer here)
    assert mask.drop(index=0).all()


def test_run_leg_drop_flags_off_by_default_leaves_map_purpose_mask_none(monkeypatch):
    """Code default False/False: run() must not build a mask at all (pairing_candidate_mask
    stays None), so map_purpose takes its byte-identical OFF path."""
    from braunschweig.popsim import distance_distributions as dd

    captured = {}
    real_map_purpose = dd.map_purpose

    def capturing_map_purpose(wege, **kwargs):
        captured["pairing_candidate_mask"] = kwargs.get("pairing_candidate_mask")
        return real_map_purpose(wege, **kwargs)

    monkeypatch.setattr(dd, "map_purpose", capturing_map_purpose)
    dd.run(_leg_drop_pairing_fixture(), escort_purpose=True, escort_passive_education=True,
          escort_passive_from_adult=True)
    assert captured["pairing_candidate_mask"] is None


class _RecordingConfigureContext:
    """Minimal synpp ConfigurationContext stand-in that records config() lookups.

    Same shape as the stand-in in tests/test_popsim_trips_stage.py
    (_RecordingConfigureContext) and tests/test_completed_donor_stage.py.
    """

    def __init__(self):
        self.calls = {}

    def config(self, key, default=None):
        self.calls[key] = default
        return default

    def stage(self, name, alias=None, **kwargs):
        pass


def test_configure_declares_the_shared_leg_drop_keys_with_the_production_defaults():
    """Must match braunschweig.popsim.trips_stage's own declaration of the SAME keys
    exactly (default True/True, configs/base_bs.yml), or a full pipeline run could
    resolve two different values for the SAME config key across the two stages."""
    from braunschweig.popsim import distance_distributions as dd
    from braunschweig.popsim.stage.config_keys import (
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_EXCLUDE_RBW_LEGS,
    )

    ctx = _RecordingConfigureContext()
    dd.configure(ctx)
    assert ctx.calls[KEY_EXCLUDE_RBW_LEGS] is True
    assert ctx.calls[KEY_DROP_LEADING_ARRIVE_HOME_LEG] is True


# ---------------------------------------------------------------------------
# The WEEKDAY DIARY universe (issue #373, ADR-0116) on the AGGREGATE and the
# per-purpose layer: the filter runs before the purpose mapping, so it reaches
# every layer this stage builds, not only the subtype layers (which
# tests/test_distance_distributions_subtypes.py covers).
# ---------------------------------------------------------------------------

_OUT_OF_UNIVERSE_KM = 66.0
_DETOUR_FACTOR = 1.3
_OUT_OF_UNIVERSE_M = _OUT_OF_UNIVERSE_KM * 1000.0 / _DETOUR_FACTOR


def _wege_with_weekend_and_rbw_legs():
    """The base frame plus 40 legs OUTSIDE the weekday diary universe: 20 weekend
    legs (kernwo 6) and 20 rbW summary records (W_RBW 1), all at a distance no
    in-universe leg carries, so their presence in a layer is unambiguous."""
    base = _synthetic_wege()
    extra = base.iloc[:40].copy()
    extra["W_ID"] = np.arange(1000, 1040)
    extra["wegkm_imp"] = _OUT_OF_UNIVERSE_KM
    extra["kernwo"] = [6] * 20 + [2] * 20
    extra["W_RBW"] = [0] * 20 + [1] * 20
    return pd.concat([base, extra], ignore_index=True)


def test_weekday_legs_only_drops_them_from_the_aggregate_and_purpose_layers():
    from braunschweig.popsim.distance_distributions import run

    w = _wege_with_weekend_and_rbw_legs()
    aggregate = run(w, by_purpose=False, weekday_legs_only=True)
    for mode_layer in aggregate.values():
        for distribution in mode_layer["distributions"]:
            assert _OUT_OF_UNIVERSE_M not in set(distribution["values"])

    by_purpose = run(w, by_purpose=True, weekday_legs_only=True)
    for purpose, layer in by_purpose.items():
        for mode_layer in layer.values():
            for distribution in mode_layer["distributions"]:
                assert _OUT_OF_UNIVERSE_M not in set(distribution["values"]), purpose


def test_weekday_legs_only_off_keeps_them_in_the_aggregate_layer():
    """The OFF path is today's behaviour; without this the test above could pass on
    a frame that never had an out-of-universe leg."""
    from braunschweig.popsim.distance_distributions import run

    w = _wege_with_weekend_and_rbw_legs()
    aggregate = run(w, by_purpose=False, weekday_legs_only=False)
    seen = {float(value)
            for mode_layer in aggregate.values()
            for distribution in mode_layer["distributions"]
            for value in distribution["values"]}
    assert _OUT_OF_UNIVERSE_M in seen


def test_weekday_legs_only_defaults_to_false():
    """The keyword's CODE default is False, so every existing direct caller keeps
    today's all-day behaviour (the production value is set in the config)."""
    import inspect

    from braunschweig.popsim.distance_distributions import run

    assert inspect.signature(run).parameters["weekday_legs_only"].default is False
