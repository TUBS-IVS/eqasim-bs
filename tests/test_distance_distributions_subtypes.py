"""Tests for the leisure/other W_ZWD subtype distance-distribution split (issue #127, Task 3).

TDD: written BEFORE the implementation. Mirrors the existing shop_daily_split tests in
tests/test_distance_distributions_by_purpose.py, but for the leisure and other subtype
groups defined in braunschweig.popsim.purpose_subtype.

Scenarios:
    (a) leisure_subtype_split=True -> 4 leisure_* group keys, aggregate "leisure" kept;
        the leisure_excursion distribution contains ONLY the excursion legs' distance.
    (b) other_subtype_split=True -> other_escort/other_errand_short/other_errand_long
        keys, aggregate "other" kept; other_escort is built even when the W_ZWD column
        is entirely absent (it only needs the raw W_ZWECK code).
    (c) both flags False -> output key set is IDENTICAL to a legacy by_purpose=True call
        (no new keys leak in when the flags are off).
    (d) W_ZWD absent + leisure_subtype_split=True -> a warning is logged and no
        leisure_* group keys are added (mirrors the shop_daily_split warning path).
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.distance_distributions import (
    _build_leisure_unspecified_layer, run)
from braunschweig.popsim.purpose_subtype import OTHER_ERRAND_GROUPS, LEISURE_GROUPS

DETOUR_FACTOR = 1.3

# Distinct wegkm_imp values per group so each group's distance is uniquely identifiable
# in the resulting CDF ("values" arrays).
_LEISURE_LOCAL_KM = 5.0
_LEISURE_VISIT_KM = 19.0
_LEISURE_ACTIVITY_KM = 15.0
_LEISURE_EXCURSION_KM = 80.0  # distinct from every other group's distance below
_OTHER_ERRAND_SHORT_KM = 6.0
_OTHER_ERRAND_LONG_KM = 12.0
_OTHER_ESCORT_KM = 3.0
# W_ZWECK 10 ("anderer Zweck") legs: the fifth leisure subtype (issue #373,
# ADR-0115). Distinct from every constant above so the layer's "values" array
# identifies the group unambiguously.
_LEISURE_UNSPECIFIED_KM = 27.0


def _add_rows(rows: list, row_id_start: int, *, w_zweck: int, w_zwd: int | None,
              wegkm: float, n: int = 15, include_w_zwd: bool = True) -> int:
    """Append n synthetic Wege rows with fixed purpose/detail/distance; return next row id."""
    row_id = row_id_start
    for _ in range(n):
        row = {
            "H_ID": row_id, "P_ID": 0, "W_ID": 0,
            "W_ZWECK": w_zweck,
            "hvm_imp": 4,  # car for all rows -> single mode, simpler assertions
            "wegkm_imp": wegkm,
            "W_SZS": 8, "W_SZM": 0, "W_AZS": 8, "W_AZM": 10,
            "W_GEW": 1.0,
        }
        if include_w_zwd:
            row["W_ZWD"] = w_zwd
        rows.append(row)
        row_id += 1
    return row_id


def _make_subtype_wege(*, include_w_zwd: bool = True,
                       include_code_10: bool = False) -> pd.DataFrame:
    """Synthetic Wege frame covering all leisure and other subtype groups.

    W_ZWECK codes: 7 = leisure, 5 = other/errand, 6 = other/escort (see
    braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK). With ``include_code_10``
    the frame additionally carries W_ZWECK 10 ("anderer Zweck") legs, which
    become leisure only under ``w_zweck_10_as_leisure`` and carry the design
    sentinel W_ZWD 2202 ("Zweck nicht zuordenbar") rather than a leisure
    detail code -- the fifth leisure subtype (issue #373, ADR-0115).
    """
    rows: list = []
    row_id = 0
    if include_code_10:
        row_id = _add_rows(rows, row_id, w_zweck=10, w_zwd=2202,
                           wegkm=_LEISURE_UNSPECIFIED_KM,
                           include_w_zwd=include_w_zwd)      # leisure_unspecified
    row_id = _add_rows(rows, row_id, w_zweck=7, w_zwd=706, wegkm=_LEISURE_LOCAL_KM,
                        include_w_zwd=include_w_zwd)       # leisure_local
    row_id = _add_rows(rows, row_id, w_zweck=7, w_zwd=701, wegkm=_LEISURE_VISIT_KM,
                        include_w_zwd=include_w_zwd)       # leisure_visit
    row_id = _add_rows(rows, row_id, w_zweck=7, w_zwd=702, wegkm=_LEISURE_ACTIVITY_KM,
                        include_w_zwd=include_w_zwd)       # leisure_activity
    row_id = _add_rows(rows, row_id, w_zweck=7, w_zwd=708, wegkm=_LEISURE_EXCURSION_KM,
                        include_w_zwd=include_w_zwd)       # leisure_excursion
    row_id = _add_rows(rows, row_id, w_zweck=5, w_zwd=601, wegkm=_OTHER_ERRAND_SHORT_KM,
                        include_w_zwd=include_w_zwd)       # other_errand_short
    row_id = _add_rows(rows, row_id, w_zweck=5, w_zwd=603, wegkm=_OTHER_ERRAND_LONG_KM,
                        include_w_zwd=include_w_zwd)       # other_errand_long
    row_id = _add_rows(rows, row_id, w_zweck=6, w_zwd=7704, wegkm=_OTHER_ESCORT_KM,
                        include_w_zwd=include_w_zwd)       # other_escort (W_ZWD irrelevant)
    return pd.DataFrame(rows)


def test_leisure_subtype_split_adds_four_group_keys_and_keeps_aggregate():
    w = _make_subtype_wege()
    out = run(w, by_purpose=True, leisure_subtype_split=True)

    for group_name in LEISURE_GROUPS:
        assert group_name in out, f"expected leisure group key {group_name!r} in output"
    assert "leisure" in out, "aggregate 'leisure' key must be kept as a fallback"


def test_leisure_excursion_distribution_contains_only_excursion_distance():
    w = _make_subtype_wege()
    out = run(w, by_purpose=True, leisure_subtype_split=True)

    expected_m = _LEISURE_EXCURSION_KM * 1000.0 / DETOUR_FACTOR
    excursion_values = np.concatenate([
        d["values"] for d in out["leisure_excursion"]["car"]["distributions"]
    ])
    assert len(excursion_values) > 0
    assert np.allclose(excursion_values, expected_m), (
        f"leisure_excursion distribution must contain ONLY the excursion distance "
        f"{expected_m:.1f} m, got {excursion_values}"
    )


def test_other_subtype_split_adds_group_keys_and_keeps_aggregate():
    w = _make_subtype_wege()
    out = run(w, by_purpose=True, other_subtype_split=True)

    for group_name in OTHER_ERRAND_GROUPS:
        assert group_name in out, f"expected other group key {group_name!r} in output"
    assert "other_escort" in out
    assert "other" in out, "aggregate 'other' key must be kept (serves other_rest)"


def test_other_escort_built_even_when_w_zwd_column_absent():
    """other_escort only needs W_ZWECK; it must still be built when W_ZWD is missing."""
    w = _make_subtype_wege(include_w_zwd=False)
    assert "W_ZWD" not in w.columns

    out = run(w, by_purpose=True, other_subtype_split=True)

    assert "other_escort" in out
    expected_m = _OTHER_ESCORT_KM * 1000.0 / DETOUR_FACTOR
    escort_values = np.concatenate([
        d["values"] for d in out["other_escort"]["car"]["distributions"]
    ])
    assert np.allclose(escort_values, expected_m)

    # But the errand short/long split (which DOES need W_ZWD) must be skipped.
    assert "other_errand_short" not in out
    assert "other_errand_long" not in out


def test_both_flags_off_key_set_identical_to_legacy_call():
    w = _make_subtype_wege()
    legacy = run(w, by_purpose=True)
    both_off = run(w, by_purpose=True, leisure_subtype_split=False, other_subtype_split=False)

    assert set(both_off) == set(legacy), (
        f"OFF path must not change the output key set: "
        f"legacy={set(legacy)}, both_off={set(both_off)}"
    )


def test_w_zwd_absent_plus_leisure_split_logs_warning_and_skips(caplog):
    w = _make_subtype_wege(include_w_zwd=False)
    assert "W_ZWD" not in w.columns

    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.distance_distributions"):
        out = run(w, by_purpose=True, leisure_subtype_split=True)

    assert any("leisure_subtype_split" in record.message or "leisure" in record.message
               for record in caplog.records), "expected a warning about the skipped leisure split"
    for group_name in LEISURE_GROUPS:
        assert group_name not in out, (
            f"leisure group key {group_name!r} must NOT appear when W_ZWD is absent"
        )
    assert "leisure" in out, "aggregate 'leisure' key must still be present"


def test_leisure_and_other_split_together_do_not_interfere():
    w = _make_subtype_wege()
    out = run(w, by_purpose=True, leisure_subtype_split=True, other_subtype_split=True)

    for group_name in LEISURE_GROUPS:
        assert group_name in out
    for group_name in OTHER_ERRAND_GROUPS:
        assert group_name in out
    assert "other_escort" in out
    assert "leisure" in out and "other" in out


def test_subtype_split_requires_by_purpose():
    import pytest
    with pytest.raises(ValueError, match="requires secondary_distance_by_purpose"):
        run(_make_subtype_wege(), by_purpose=False, leisure_subtype_split=True)
    with pytest.raises(ValueError, match="requires secondary_distance_by_purpose"):
        run(_make_subtype_wege(), by_purpose=False, other_subtype_split=True)


# ---------------------------------------------------------------------------
# Issue #242 Task 5: codeplan_sentinels excludes the NO-DETAIL codes (799, 699)
# from the leisure_activity / other_errand_long donor pool the SAME way
# secondary_chainsolvers.deciders excludes them from ESTIMATION -- the two
# consumers must agree, or a leg labelled "leisure_activity" would draw its
# distance from a donor pool that still includes the excluded 799 legs.
# ---------------------------------------------------------------------------

_LEISURE_ACTIVITY_NODETAIL_KM = 33.0    # W_ZWD 799 "Freizeit k.A."
_OTHER_ERRAND_LONG_NODETAIL_KM = 44.0   # W_ZWD 699 "Erledigung k.A."


def _make_codeplan_sentinel_wege() -> pd.DataFrame:
    """One ordinary group code plus the group's NO-DETAIL code, for both
    leisure_activity (702 vs. 799) and other_errand_long (603 vs. 699), each
    with a DISTINCT wegkm_imp so the NO-DETAIL leg's distance is uniquely
    identifiable in the resulting CDF "values" array."""
    rows: list = []
    row_id = 0
    row_id = _add_rows(rows, row_id, w_zweck=7, w_zwd=702, wegkm=_LEISURE_ACTIVITY_KM)
    row_id = _add_rows(rows, row_id, w_zweck=7, w_zwd=799, wegkm=_LEISURE_ACTIVITY_NODETAIL_KM)
    row_id = _add_rows(rows, row_id, w_zweck=5, w_zwd=603, wegkm=_OTHER_ERRAND_LONG_KM)
    row_id = _add_rows(rows, row_id, w_zweck=5, w_zwd=699, wegkm=_OTHER_ERRAND_LONG_NODETAIL_KM)
    return pd.DataFrame(rows)


def test_codeplan_sentinels_off_leisure_activity_includes_799_legs():
    w = _make_codeplan_sentinel_wege()
    out = run(w, by_purpose=True, leisure_subtype_split=True, codeplan_sentinels=False)
    values = np.concatenate([d["values"] for d in out["leisure_activity"]["car"]["distributions"]])
    expected_ordinary_m = _LEISURE_ACTIVITY_KM * 1000.0 / DETOUR_FACTOR
    expected_nodetail_m = _LEISURE_ACTIVITY_NODETAIL_KM * 1000.0 / DETOUR_FACTOR
    assert np.isclose(values, expected_ordinary_m).any()
    assert np.isclose(values, expected_nodetail_m).any(), (
        "codeplan_sentinels=False (the default, byte-identical to before issue "
        "#242 Task 5) must keep including 799 legs in leisure_activity"
    )


def test_codeplan_sentinels_on_leisure_activity_excludes_799_legs():
    w = _make_codeplan_sentinel_wege()
    out = run(w, by_purpose=True, leisure_subtype_split=True, codeplan_sentinels=True)
    values = np.concatenate([d["values"] for d in out["leisure_activity"]["car"]["distributions"]])
    expected_ordinary_m = _LEISURE_ACTIVITY_KM * 1000.0 / DETOUR_FACTOR
    expected_nodetail_m = _LEISURE_ACTIVITY_NODETAIL_KM * 1000.0 / DETOUR_FACTOR
    assert np.isclose(values, expected_ordinary_m).any(), (
        "the ordinary group code (702) must stay in leisure_activity"
    )
    assert not np.isclose(values, expected_nodetail_m).any(), (
        "codeplan_sentinels=True must exclude the 799 NO-DETAIL legs from the "
        "leisure_activity donor pool"
    )


def test_codeplan_sentinels_off_other_errand_long_includes_699_legs():
    w = _make_codeplan_sentinel_wege()
    out = run(w, by_purpose=True, other_subtype_split=True, codeplan_sentinels=False)
    values = np.concatenate([d["values"] for d in out["other_errand_long"]["car"]["distributions"]])
    expected_nodetail_m = _OTHER_ERRAND_LONG_NODETAIL_KM * 1000.0 / DETOUR_FACTOR
    assert np.isclose(values, expected_nodetail_m).any()


def test_codeplan_sentinels_on_other_errand_long_excludes_699_legs():
    w = _make_codeplan_sentinel_wege()
    out = run(w, by_purpose=True, other_subtype_split=True, codeplan_sentinels=True)
    values = np.concatenate([d["values"] for d in out["other_errand_long"]["car"]["distributions"]])
    expected_ordinary_m = _OTHER_ERRAND_LONG_KM * 1000.0 / DETOUR_FACTOR
    expected_nodetail_m = _OTHER_ERRAND_LONG_NODETAIL_KM * 1000.0 / DETOUR_FACTOR
    assert np.isclose(values, expected_ordinary_m).any()
    assert not np.isclose(values, expected_nodetail_m).any()


def test_codeplan_sentinels_default_is_off_byte_identical():
    # codeplan_sentinels' CODE-level default (False) must reproduce the
    # explicit OFF call exactly -- a direct caller/test that omits the keyword
    # keeps today's behaviour (the config-declared default of True lives only
    # in configure(), not here).
    w = _make_codeplan_sentinel_wege()
    default = run(w, by_purpose=True, leisure_subtype_split=True, other_subtype_split=True)
    explicit_off = run(w, by_purpose=True, leisure_subtype_split=True, other_subtype_split=True,
                        codeplan_sentinels=False)
    assert set(default) == set(explicit_off)
    for purpose in default:
        for mode in default[purpose]:
            np.testing.assert_array_equal(
                default[purpose][mode]["bounds"], explicit_off[purpose][mode]["bounds"])
            for d_default, d_off in zip(default[purpose][mode]["distributions"],
                                         explicit_off[purpose][mode]["distributions"]):
                np.testing.assert_array_equal(d_default["values"], d_off["values"])
                np.testing.assert_array_equal(d_default["weights"], d_off["weights"])


# ---------------------------------------------------------------------------
# Issue #373 / ADR-0115: leisure_unspecified, the fifth leisure subtype.
#
# MiD W_ZWECK 10 ("anderer Zweck") legs become leisure via w_zweck_10_as_leisure
# but never carry a leisure W_ZWD detail code, so under leisure_subtype_split
# alone they reach only the aggregate "leisure" fallback layer.
# leisure_unspecified_subtype gives them their own layer, defined by the RAW
# W_ZWECK code (purpose_subtype.LEISURE_UNSPECIFIED_ZWECK) rather than by W_ZWD.
# ---------------------------------------------------------------------------

_LEISURE_UNSPECIFIED_M = _LEISURE_UNSPECIFIED_KM * 1000.0 / DETOUR_FACTOR


def _run(*, include_code_10: bool = False, include_w_zwd: bool = True, **run_kwargs) -> dict:
    """Build the synthetic Wege frame and call run() on the purpose layer."""
    wege = _make_subtype_wege(include_w_zwd=include_w_zwd, include_code_10=include_code_10)
    return run(wege, by_purpose=True, **run_kwargs)


def _serialise(obj) -> str:
    """Stable text form of one distance layer, for the OFF-path identity check.

    numpy arrays and scalars are rendered via ``tolist()``; ``sort_keys`` makes
    the mode/bin ordering irrelevant, so a difference in the string is a
    difference in the numbers, not in dict iteration order.
    """
    return json.dumps(
        obj, sort_keys=True,
        default=lambda o: o.tolist() if hasattr(o, "tolist") else list(o),
    )


def test_leisure_unspecified_layer_contains_only_the_code_10_legs_distance():
    out = _run(leisure_subtype_split=True, leisure_unspecified_subtype=True,
               w_zweck_10_as_leisure=True, include_code_10=True)
    values = {v for d in out["leisure_unspecified"]["car"]["distributions"] for v in d["values"]}
    assert values == {_LEISURE_UNSPECIFIED_M}


def test_leisure_unspecified_flag_off_leaves_every_layer_byte_identical():
    on = _run(leisure_subtype_split=True, leisure_unspecified_subtype=True,
              w_zweck_10_as_leisure=True, include_code_10=True)
    off = _run(leisure_subtype_split=True, leisure_unspecified_subtype=False,
               w_zweck_10_as_leisure=True, include_code_10=True)
    assert "leisure_unspecified" in on and "leisure_unspecified" not in off
    for key in off:
        # four W_ZWD layers + the aggregate + every other purpose unchanged
        assert _serialise(on[key]) == _serialise(off[key]), (
            f"turning leisure_unspecified_subtype on changed the {key!r} layer"
        )


def test_leisure_unspecified_layer_requires_the_w_zweck_column():
    """The raw W_ZWECK column DEFINES the group, so its absence must raise.

    W_ZWECK is in REQUIRED_COLUMNS and is kept through the Step-5 column
    selection via _OPTIONAL_COLUMNS, so run() can never reach the layer builder
    without it -- the guard is defensive and is therefore exercised by calling
    the builder directly rather than through run().
    """
    df = pd.DataFrame({
        "following_purpose": ["leisure"],
        "W_ZWD": [2202],
        "mode": ["car"],
        "travel_time": [600.0],
        "distance": [_LEISURE_UNSPECIFIED_M],
        "weight": [1.0],
    })
    with pytest.raises(ValueError, match="W_ZWECK"):
        _build_leisure_unspecified_layer(df)


def test_leisure_unspecified_without_the_fold_warns_and_builds_no_layer(caplog):
    """With w_zweck_10_as_leisure off no code-10 leg is leisure, so the layer
    has zero legs: it must not be built, and the emptiness must be LOUD (the
    fallback-transparency rule) rather than silently absent."""
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.distance_distributions"):
        out = _run(leisure_subtype_split=True, leisure_unspecified_subtype=True,
                   w_zweck_10_as_leisure=False, include_code_10=True)
    assert "leisure_unspecified" not in out
    assert any("w_zweck_10_as_leisure" in record.message for record in caplog.records), (
        "a zero-leg leisure_unspecified layer must warn and name the fold flag"
    )


def test_leisure_unspecified_rate_is_logged_against_the_leisure_universe(caplog):
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.distance_distributions"):
        _run(leisure_subtype_split=True, leisure_unspecified_subtype=True,
             w_zweck_10_as_leisure=True, include_code_10=True)
    messages = [record.getMessage() for record in caplog.records]
    # 15 code-10 legs out of 15 + 4 x 15 = 75 leisure legs.
    assert any("leisure subtype leisure_unspecified: 15/75" in message for message in messages), (
        f"expected an explicit primary-vs-universe rate log line, got: {messages}"
    )


def test_leisure_unspecified_needs_no_w_zwd_column():
    """The group is defined by the raw W_ZWECK code, so -- like other_escort --
    it is still built when the W_ZWD detail column is entirely absent."""
    out = _run(leisure_subtype_split=True, leisure_unspecified_subtype=True,
               w_zweck_10_as_leisure=True, include_code_10=True, include_w_zwd=False)
    assert "leisure_unspecified" in out
    for group_name in LEISURE_GROUPS:
        assert group_name not in out


def test_leisure_unspecified_empty_leisure_universe_logs_no_nan_rate(caplog):
    """An empty leisure universe has no rate; the line must say so rather than
    print "nan%", which reads as a broken computation and hides the real finding
    (there were no leisure legs to split at all)."""
    df = pd.DataFrame({
        "following_purpose": ["shop"],
        "W_ZWECK": [4],
        "W_ZWD": [501],
        "mode": ["car"],
        "travel_time": [600.0],
        "distance": [1000.0],
        "weight": [1.0],
    })
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.distance_distributions"):
        assert _build_leisure_unspecified_layer(df) is None
    messages = [record.getMessage() for record in caplog.records]
    assert any("leisure subtype leisure_unspecified: 0/0 leisure legs (no leisure legs)" in message
               for message in messages), messages
    assert not any("nan" in message.lower() for message in messages), messages
