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
    DETOUR_FACTOR, _build_leisure_unspecified_layer, run)
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
# Legs OUTSIDE the weekday diary universe (issue #373, ADR-0116): a WEEKEND leg
# (kernwo outside the seed's day filter) and an rbW SUMMARY record (W_RBW == 1).
# Both carry a distance no in-universe group uses, so a layer built under
# weekday_legs_only=True can be checked for their absence unambiguously.
_WEEKEND_KM = 66.0
_RBW_KM = 77.0


def _add_rows(rows: list, row_id_start: int, *, w_zweck: int, w_zwd: int | None,
              wegkm: float, n: int = 15, include_w_zwd: bool = True,
              kernwo: int = 2, w_rbw: int = 0) -> int:
    """Append n synthetic Wege rows with fixed purpose/detail/distance; return next row id.

    ``kernwo`` / ``w_rbw`` carry the two columns the weekday diary universe reads
    (``trips.weekday_diary_leg_mask``); the defaults put every row INSIDE that universe
    (a weekday reporting day, not an rbW summary record), so a fixture that does not
    mention them is unaffected by ``weekday_legs_only``.
    """
    row_id = row_id_start
    for _ in range(n):
        row = {
            "H_ID": row_id, "P_ID": 0, "W_ID": 0,
            "W_ZWECK": w_zweck,
            "hvm_imp": 4,  # car for all rows -> single mode, simpler assertions
            "wegkm_imp": wegkm,
            "W_SZS": 8, "W_SZM": 0, "W_AZS": 8, "W_AZM": 10,
            "W_GEW": 1.0,
            "kernwo": kernwo, "W_RBW": w_rbw,
        }
        if include_w_zwd:
            row["W_ZWD"] = w_zwd
        rows.append(row)
        row_id += 1
    return row_id


def _make_subtype_wege(*, include_w_zwd: bool = True,
                       include_code_10: bool = False,
                       include_weekend_and_rbw: bool = False) -> pd.DataFrame:
    """Synthetic Wege frame covering all leisure and other subtype groups.

    W_ZWECK codes: 7 = leisure, 5 = other/errand, 6 = other/escort (see
    braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK). With ``include_code_10``
    the frame additionally carries W_ZWECK 10 ("anderer Zweck") legs, which
    become leisure only under ``w_zweck_10_as_leisure`` and carry the design
    sentinel W_ZWD 2202 ("Zweck nicht zuordenbar") rather than a leisure
    detail code -- the fifth leisure subtype (issue #373, ADR-0115).

    With ``include_weekend_and_rbw`` the frame additionally carries legs OUTSIDE
    the weekday diary universe (issue #373, ADR-0116) -- weekend legs
    (``kernwo`` 6) at ``_WEEKEND_KM`` and rbW summary records (``W_RBW`` 1) at
    ``_RBW_KM``, spread over shop, leisure and other so EVERY layer the stage
    builds has an out-of-universe leg that ``weekday_legs_only=True`` must drop.
    """
    rows: list = []
    row_id = 0
    if include_weekend_and_rbw:
        # Shop legs exist ONLY in this branch (the base fixture has none): a weekday
        # daily leg and a WEEKEND non-daily leg, so the shop layers are covered too.
        row_id = _add_rows(rows, row_id, w_zweck=4, w_zwd=501, wegkm=2.0,
                           include_w_zwd=include_w_zwd)
        row_id = _add_rows(rows, row_id, w_zweck=4, w_zwd=502, wegkm=_WEEKEND_KM,
                           include_w_zwd=include_w_zwd, kernwo=6)
        # Weekend legs of every other purpose the fixture covers.
        for weekend_zweck, weekend_zwd in ((7, 708), (5, 601), (6, 7704)):
            row_id = _add_rows(rows, row_id, w_zweck=weekend_zweck, w_zwd=weekend_zwd,
                               wegkm=_WEEKEND_KM, include_w_zwd=include_w_zwd, kernwo=6)
        # rbW summary records: a weekday reporting day, but not an individually
        # reported diary leg.
        for rbw_zweck, rbw_zwd in ((7, 706), (5, 603)):
            row_id = _add_rows(rows, row_id, w_zweck=rbw_zweck, w_zwd=rbw_zwd,
                               wegkm=_RBW_KM, include_w_zwd=include_w_zwd, w_rbw=1)
        if include_code_10:
            row_id = _add_rows(rows, row_id, w_zweck=10, w_zwd=2202, wegkm=_WEEKEND_KM,
                               include_w_zwd=include_w_zwd, kernwo=6)
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


# ---------------------------------------------------------------------------
# The WEEKDAY DIARY universe (issue #373, ADR-0116): every layer this stage
# builds describes ONE synthetic weekday, so under weekday_legs_only the donor
# legs are the seed's weekday diaries without rbW summary records
# (trips.weekday_diary_leg_mask). The keyword defaults to False, which keeps the
# direct-call behaviour byte-identical to before.
# ---------------------------------------------------------------------------

_WEEKEND_M = _WEEKEND_KM * 1000.0 / DETOUR_FACTOR
_RBW_M = _RBW_KM * 1000.0 / DETOUR_FACTOR

_ALL_LAYER_FLAGS = dict(by_purpose=True, leisure_subtype_split=True,
                        other_subtype_split=True, shop_daily_split=True,
                        leisure_unspecified_subtype=True, w_zweck_10_as_leisure=True)


def _layer_values(layer: dict) -> set:
    """Every distance value in one {mode: {bounds, distributions}} layer."""
    return {float(value)
            for mode_layer in layer.values()
            for distribution in mode_layer["distributions"]
            for value in distribution["values"]}


def test_weekday_legs_only_drops_weekend_and_rbw_legs_from_every_layer():
    wege = _make_subtype_wege(include_code_10=True, include_weekend_and_rbw=True)
    out = run(wege, weekday_legs_only=True, **_ALL_LAYER_FLAGS)
    assert len(out) > 1
    for key, layer in out.items():
        assert not ({_WEEKEND_M, _RBW_M} & _layer_values(layer)), key


def test_weekday_legs_only_off_keeps_the_weekend_and_rbw_legs():
    """The OFF path is today's behaviour: an out-of-universe leg still contributes
    its distance, so the ON-path assertion above is a real difference and not a
    fixture that lacks those legs in the first place."""
    wege = _make_subtype_wege(include_code_10=True, include_weekend_and_rbw=True)
    out = run(wege, weekday_legs_only=False, **_ALL_LAYER_FLAGS)
    seen = set()
    for layer in out.values():
        seen |= _layer_values(layer)
    assert {_WEEKEND_M, _RBW_M} <= seen


def test_weekday_legs_only_default_false_is_byte_identical_to_today():
    wege = _make_subtype_wege(include_code_10=True, include_weekend_and_rbw=True)
    omitted = run(wege, **_ALL_LAYER_FLAGS)
    explicit_off = run(wege, weekday_legs_only=False, **_ALL_LAYER_FLAGS)
    assert _serialise(omitted) == _serialise(explicit_off)


def test_weekday_legs_only_logs_the_kept_rate(caplog):
    """No silent filter: the stage must report how many legs the universe kept and
    why the others went (CLAUDE.md "Fallback transparency")."""
    wege = _make_subtype_wege(include_code_10=True, include_weekend_and_rbw=True)
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        run(wege, weekday_legs_only=True, **_ALL_LAYER_FLAGS)
    assert "[popsim.distance_distributions] weekday diary universe" in caplog.text
    assert "non-weekday" in caplog.text and "rbW" in caplog.text


def test_weekday_legs_only_raises_when_the_universe_column_is_absent():
    """kernwo/W_RBW are required only when the keyword is ON; the failure must name
    the missing column instead of silently keeping every leg."""
    wege = _make_subtype_wege().drop(columns=["kernwo"])
    with pytest.raises(ValueError, match="kernwo"):
        run(wege, by_purpose=True, weekday_legs_only=True)


def test_weekday_legs_only_off_needs_no_universe_column():
    """The OFF path must not start requiring the two columns (every existing direct
    caller, e.g. the by-purpose fixtures, has neither)."""
    wege = _make_subtype_wege().drop(columns=["kernwo", "W_RBW"])
    out = run(wege, by_purpose=True, leisure_subtype_split=True)
    assert "leisure" in out


# ---------------------------------------------------------------------------
# exclude_no_answer_purpose: MiD W_ZWECK 99 "keine Angabe" legs are a NON-ANSWER,
# not a purpose, so they must not feed any donor pool (issue #373 follow-up, ADR-0117).
# ---------------------------------------------------------------------------

_NO_ANSWER_KM = 44.0   # distinct from every other fixture distance


def _wege_with_a_no_answer_purpose_leg():
    """The subtype fixture plus two W_ZWECK 99 legs carrying a unique distance."""
    rows: list = []
    row_id = _add_rows(rows, 0, w_zweck=7, w_zwd=706, wegkm=_LEISURE_LOCAL_KM)
    row_id = _add_rows(rows, row_id, w_zweck=5, w_zwd=601, wegkm=_OTHER_ERRAND_SHORT_KM)
    _add_rows(rows, row_id, w_zweck=99, w_zwd=7704, wegkm=_NO_ANSWER_KM)
    return pd.DataFrame(rows)


def test_no_answer_purpose_legs_are_excluded_from_every_donor_pool():
    out = run(_wege_with_a_no_answer_purpose_leg(), by_purpose=True,
              exclude_no_answer_purpose=True)
    for key, layer in out.items():
        values = {v for mode in layer.values() for d in mode["distributions"] for v in d["values"]}
        assert not any(abs(v * DETOUR_FACTOR / 1000.0 - _NO_ANSWER_KM) < 1e-9 for v in values), key


def test_no_answer_purpose_legs_are_kept_when_the_flag_is_off():
    """The OFF path is the pre-feature behaviour: the legs stay in the 'other' pool."""
    out = run(_wege_with_a_no_answer_purpose_leg(), by_purpose=True,
              exclude_no_answer_purpose=False)
    values = {v for mode in out["other"].values()
              for d in mode["distributions"] for v in d["values"]}
    assert any(abs(v * DETOUR_FACTOR / 1000.0 - _NO_ANSWER_KM) < 1e-9 for v in values)


def test_no_answer_purpose_exclusion_leaves_the_other_pools_untouched():
    on = run(_wege_with_a_no_answer_purpose_leg(), by_purpose=True, exclude_no_answer_purpose=True)
    without = run(_wege_with_a_no_answer_purpose_leg().query("W_ZWECK != 99"), by_purpose=True,
                  exclude_no_answer_purpose=False)
    assert set(on) == set(without)
    for key in without:
        assert _serialise(on[key]) == _serialise(without[key]), key


def test_no_answer_purpose_exclusion_logs_the_rate(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        run(_wege_with_a_no_answer_purpose_leg(), by_purpose=True, exclude_no_answer_purpose=True)
    assert "no-answer purpose" in caplog.text and "15/45" in caplog.text


# ---------------------------------------------------------------------------
# Issue #679: the leisure subtype layers without the loop legs. A stroll or dog walk on foot or
# by bicycle (W_ZWD 710/711) leaves the chain under secondary_loop_zero_displacement, so the
# leisure_local layer the remaining legs draw from must not pool the loops' path lengths.
# ---------------------------------------------------------------------------

_HVM_WALK, _HVM_BICYCLE, _HVM_CAR = 1, 2, 4   # trips.MODE_BY_HVM


def _loop_wege(*, walk_non_loop_local: bool = True) -> pd.DataFrame:
    """Leisure legs whose distance names their (mode, code) cell.

    walk: 706 at 3 km (non-loop local, optional), 710 at 4 km and 711 at 4.5 km (loops), 701 at
    2 km (visit); bicycle: 713 at 7 km (non-loop local), 711 at 6 km (loop); car: 710 at 9 km
    (a car stroll is no loop and stays), 706 at 8 km; plus an errand leg so a non-leisure layer
    exists.
    """
    cells = [(_HVM_WALK, 7, 710, 4.0), (_HVM_WALK, 7, 711, 4.5), (_HVM_WALK, 7, 701, 2.0),
             (_HVM_BICYCLE, 7, 713, 7.0), (_HVM_BICYCLE, 7, 711, 6.0),
             (_HVM_CAR, 7, 710, 9.0), (_HVM_CAR, 7, 706, 8.0), (_HVM_CAR, 5, 601, 6.0)]
    if walk_non_loop_local:
        cells.append((_HVM_WALK, 7, 706, 3.0))
    rows: list = []
    row_id = 0
    for hvm, zweck, zwd, km in cells:
        start = len(rows)
        row_id = _add_rows(rows, row_id, w_zweck=zweck, w_zwd=zwd, wegkm=km, n=5)
        for row in rows[start:]:
            row["hvm_imp"] = hvm
    return pd.DataFrame(rows)


def _metres(*km: float) -> set:
    return {value * 1000.0 / DETOUR_FACTOR for value in km}


def test_leisure_local_layer_leaves_out_the_walk_and_bicycle_loops():
    """Switch on: the walk and bicycle leisure_local layers hold only the non-loop legs, the car
    layer keeps its 710 legs; every other layer equals the switch-off output, which equals the
    output of a call that does not know the switch (today's)."""
    wege = _loop_wege()
    today = run(wege, by_purpose=True, leisure_subtype_split=True)
    off = run(wege, by_purpose=True, leisure_subtype_split=True,
              leisure_estimates_without_loops=False)
    on = run(wege, by_purpose=True, leisure_subtype_split=True,
             leisure_estimates_without_loops=True)

    assert set(off) == set(today) and all(
        _serialise(off[key]) == _serialise(today[key]) for key in today)
    local = on["leisure_local"]
    assert _layer_values({"walk": local["walk"]}) == _metres(3.0)
    assert _layer_values({"bicycle": local["bicycle"]}) == _metres(7.0)
    assert _serialise(local["car"]) == _serialise(off["leisure_local"]["car"])
    assert _layer_values({"walk": off["leisure_local"]["walk"]}) == _metres(3.0, 4.0, 4.5)
    assert set(on) == set(off)
    for key in set(off) - {"leisure_local"}:
        # the aggregate "leisure" layer and every other group or purpose keep the loops' legs
        assert _serialise(on[key]) == _serialise(off[key]), key


@pytest.mark.parametrize("switch", [True, False])
def test_execute_reports_the_legs_left_out_only_while_the_switch_is_on(monkeypatch, tmp_path,
                                                                        switch):
    """The stage report holds, per loop mode, the leisure legs the filter saw and left out, and
    per leisure group and mode the layer's legs with and without the loops; with the switch off
    the stage folder stays empty (today's state)."""
    from braunschweig.popsim import distance_distributions as dd
    from braunschweig.popsim.mid import donor
    from braunschweig.popsim.stage import config_keys as keys

    monkeypatch.setattr(donor, "load_mid_wege", lambda mid_dir: _loop_wege())
    values = {"braunschweig.population.popsim.mid_dir": "unused",
              "secondary_distance_by_purpose": True, "secondary_leisure_subtype_split": True,
              keys.KEY_SECONDARY_LEISURE_ESTIMATES_WITHOUT_LOOPS: switch,
              keys.KEY_PASSIVE_PAIR_MAX_GAP_MINUTES: 10.0,
              keys.KEY_PASSIVE_PAIR_ADULT_MIN_AGE_YEARS: 18}

    class _Context:
        def config(self, key):
            return values.get(key, False)

        def path(self):
            return str(tmp_path)

    dd.execute(_Context())
    if not switch:
        assert list(tmp_path.iterdir()) == []
        return
    report = json.loads((tmp_path / dd.REPORT_FILE).read_text(encoding="utf-8"))
    # walk: 710, 711, 701, 706 (5 legs each); bicycle: 713, 711; car legs are no loop mode.
    assert report["leisure_subtype_legs_left_out"] == {
        "bicycle": {"legs": 10, "left_out": 5, "kept": 5},
        "walk": {"legs": 20, "left_out": 10, "kept": 10}}
    layers = report["leisure_subtype_layer_legs"]
    assert layers["leisure_local"] == {
        "bicycle": {"legs_with_loops": 10, "legs_without_loops": 5},
        "car": {"legs_with_loops": 10, "legs_without_loops": 10},
        "walk": {"legs_with_loops": 15, "legs_without_loops": 5}}
    assert layers["leisure_visit"] == {"walk": {"legs_with_loops": 5, "legs_without_loops": 5}}
    assert layers["leisure_activity"] == layers["leisure_excursion"] == {}


def test_leisure_local_layer_raises_when_a_loop_mode_keeps_no_leg():
    """All walk leisure_local legs are loops: the walk layer cannot be estimated without them, and
    falling back to the with-loops layer (or to the aggregate) silently is not allowed."""
    with pytest.raises(ValueError, match="walk"):
        run(_loop_wege(walk_non_loop_local=False), by_purpose=True, leisure_subtype_split=True,
            leisure_estimates_without_loops=True)
