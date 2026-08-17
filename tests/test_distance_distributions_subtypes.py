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

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.distance_distributions import run
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


def _make_subtype_wege(*, include_w_zwd: bool = True) -> pd.DataFrame:
    """Synthetic Wege frame covering all leisure and other subtype groups.

    W_ZWECK codes: 7 = leisure, 5 = other/errand, 6 = other/escort (see
    braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK).
    """
    rows: list = []
    row_id = 0
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
