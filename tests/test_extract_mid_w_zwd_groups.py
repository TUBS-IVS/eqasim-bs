"""Unit tests for the MiD W_ZWD subtype-group reference extraction (issue #242 Task 6).

Synthetic MiD-Wege-shaped frames only -- the raw delivery is local-only and must never be read by
a test. The frames are deliberately tiny and use the REAL W_ZWD codes of the specs the model
estimates on, so the tests pin the actual wiring (spec identity, shop code sets) rather than a
re-typed copy of it.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import purpose_subtype as P
from braunschweig.popsim.shop_subtype import (
    SHOP_DAILY_W_ZWD, SHOP_DETAIL_MISSING, SHOP_NONDAILY_W_ZWD,
)
from scripts import extract_mid_w_zwd_groups as M


def _leg(zweck: int, zwd: int, km: float, weight: float = 1.0) -> dict:
    """One weekday, non-rbW leg."""
    return {"W_ZWECK": zweck, "W_ZWD": zwd, "W_GEW": weight, "W_RBW": 0,
            "kernwo": M.KERNWO_WEEKDAY_CODES[0], "wegkm_imp": km}


def _wege(extra=()) -> pd.DataFrame:
    """Two legs per group of all three purposes, so every block has a defined share."""
    legs = [
        _leg(4, 501, 1.0), _leg(4, 501, 3.0),        # shop_daily
        _leg(4, 502, 5.0), _leg(4, 505, 7.0),        # shop_non_daily
        _leg(5, 601, 2.0), _leg(5, 602, 4.0),        # other_errand_short
        _leg(5, 603, 6.0), _leg(5, 699, 8.0),        # other_errand_long (699 only in "default")
        _leg(7, 706, 1.0), _leg(7, 710, 2.0),        # leisure_local
        _leg(7, 701, 3.0), _leg(7, 701, 4.0),        # leisure_visit
        _leg(7, 702, 5.0), _leg(7, 799, 6.0),        # leisure_activity (799 only in "default")
        _leg(7, 708, 40.0), _leg(7, 709, 60.0),      # leisure_excursion
    ]
    legs.extend(extra)
    return pd.DataFrame(legs)


def _row(table: pd.DataFrame, variant: str, group: str) -> pd.Series:
    selected = table[(table["spec_variant"] == variant) & (table["group"] == group)]
    assert len(selected) == 1, (variant, group, table)
    return selected.iloc[0]


# --------------------------------------------------------------- spec wiring (never re-typed)


def test_specs_for_variant_returns_the_task_5_spec_objects_by_identity():
    """The variant must select the very spec objects the model uses, not an equivalent copy --
    identity is what guarantees the measured mix is the mix the estimation sees."""
    shop_default, errand_default, leisure_default = M.specs_for_variant("default")
    shop_codeplan, errand_codeplan, leisure_codeplan = M.specs_for_variant("codeplan")
    assert errand_default is P.OTHER_ERRAND_SPEC
    assert leisure_default is P.LEISURE_SPEC
    assert errand_codeplan is P.OTHER_ERRAND_SPEC_CODEPLAN
    assert leisure_codeplan is P.LEISURE_SPEC_CODEPLAN
    assert errand_default is P.other_errand_spec(False)
    assert leisure_default is P.leisure_spec(False)
    assert errand_codeplan is P.other_errand_spec(True)
    assert leisure_codeplan is P.leisure_spec(True)
    # The shop split has no codeplan variant: the very same object in both.
    assert shop_default is M.SHOP_SPEC and shop_codeplan is M.SHOP_SPEC


def test_specs_for_variant_rejects_an_unknown_variant():
    with pytest.raises(ValueError, match="banana"):
        M.specs_for_variant("banana")


def test_shop_spec_is_built_from_the_shop_subtype_constants():
    """The shop code sets must come from braunschweig.popsim.shop_subtype, never be re-typed."""
    assert M.SHOP_SPEC.groups["shop_daily"] == frozenset(SHOP_DAILY_W_ZWD)
    assert M.SHOP_SPEC.groups["shop_non_daily"] == frozenset(SHOP_NONDAILY_W_ZWD)
    assert M.SHOP_SPEC.sentinels == frozenset(SHOP_DETAIL_MISSING)
    assert M.SHOP_SPEC.group_codes == frozenset(SHOP_DAILY_W_ZWD | SHOP_NONDAILY_W_ZWD)
    assert M.SHOP_SPEC.group_col == "W_ZWD"


def test_the_missing_distance_bound_is_the_shared_repository_constant():
    from braunschweig.popsim.diary_facts import WEGKM_CODE_MIN

    assert M.DISTANCE_MISSING_CODE_MIN == WEGKM_CODE_MIN


def test_kernwo_weekday_codes_is_the_seed_day_filter_not_a_retyped_copy():
    """Issue #373 cleanup wave, item 4: KERNWO_WEEKDAY_CODES used to be re-typed as a literal
    ``(1, 2, 3)`` in scripts/extract_mid_w_zweck_hwzweck1.py (which this module imports it
    FROM), independently of braunschweig.popsim.seed.WEEKDAY_KERNWO / MID_SEED_COLUMNS.
    day_filter_values -- the single home scripts/derive_escort_w_zweck_split.py already
    imports it from. A re-typed copy can silently drift onto a different weekday-code
    universe than the PopulationSim seed actually keeps, which would make the two committed
    MiD Wege aggregates (this module's and derive_escort_w_zweck_split.py's) describe
    different leg universes without either script noticing. KERNWO_WEEKDAY_CODES is kept as
    an ALIAS (not renamed) for the callers that import this exact name."""
    from braunschweig.popsim.seed import MID_SEED_COLUMNS
    from scripts import extract_mid_w_zweck_hwzweck1

    assert extract_mid_w_zweck_hwzweck1.KERNWO_WEEKDAY_CODES is MID_SEED_COLUMNS.day_filter_values
    # M (this module) imports the alias FROM extract_mid_w_zweck_hwzweck1 -- same object, not a copy.
    assert M.KERNWO_WEEKDAY_CODES is extract_mid_w_zweck_hwzweck1.KERNWO_WEEKDAY_CODES


# --------------------------------------------------------------- universe and guards


def test_filter_keeps_weekday_non_rbw_legs_and_reports_the_universe_counts():
    wege = _wege(extra=[dict(_leg(4, 501, 1.0), kernwo=4),          # weekend leg
                        dict(_leg(4, 501, 1.0), W_RBW=1)])          # route-break summary leg
    filtered, diagnostics = M.filter_weekday_legs(wege)
    assert diagnostics["n_legs_raw"] == 18
    assert diagnostics["n_legs_after_weekday_rbw"] == 16 == len(filtered)
    assert diagnostics["n_legs_invalid_weight"] == 0
    assert diagnostics["n_values_coerced_to_nan"] == {c: 0 for c in M.REQUIRED_COLUMNS}


def test_filter_raises_on_a_non_positive_weight_and_names_the_rate():
    wege = _wege()
    wege.loc[0, "W_GEW"] = 0.0
    with pytest.raises(ValueError, match=r"1/16 legs \(6.25%\) have a missing or non-positive W_GEW"):
        M.filter_weekday_legs(wege)


def test_filter_logs_the_coercion_rate_of_a_non_numeric_column(caplog):
    """A mis-parsed column must not shrink the universe silently: the coercion is counted."""
    wege = _wege()
    wege["wegkm_imp"] = wege["wegkm_imp"].astype(object)
    wege.loc[0, "wegkm_imp"] = "not a number"
    caplog.set_level("WARNING")
    _, diagnostics = M.filter_weekday_legs(wege)
    assert diagnostics["n_values_coerced_to_nan"]["wegkm_imp"] == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("1/16 values (6.25%) of column wegkm_imp" in message for message in messages), messages


def test_build_raises_on_an_unmapped_w_zwd_code():
    """purpose_subtype.code_coverage_guard must run: an unmapped detail code is a spec gap."""
    wege = _wege(extra=[_leg(7, 777, 1.0)])
    with pytest.raises(ValueError, match="777"):
        M.build_group_reference(wege)


def test_build_raises_on_a_missing_required_column():
    with pytest.raises(ValueError, match="wegkm_imp"):
        M.build_group_reference(_wege().drop(columns=["wegkm_imp"]))


# --------------------------------------------------------------- shares and percentiles


def test_shares_are_weighted_and_sum_to_one_per_variant_and_purpose():
    wege = _wege()
    wege.loc[wege["W_ZWD"] == 501, "W_GEW"] = 3.0     # 2 legs x 3.0 vs 2 legs x 1.0
    table, _ = M.build_group_reference(wege)
    M.check_invariants(table)
    assert _row(table, "default", "shop_daily")["share_within_purpose"] == pytest.approx(0.75)
    assert _row(table, "default", "shop_non_daily")["share_within_purpose"] == pytest.approx(0.25)
    for (variant, purpose), block in table.groupby(["spec_variant", "purpose"]):
        assert block["share_within_purpose"].sum() == pytest.approx(1.0), (variant, purpose)


def test_the_codeplan_variant_drops_the_no_detail_codes_from_the_groups():
    """799 / 699 are group members under "default" and sentinels under "codeplan"."""
    table, diagnostics = M.build_group_reference(_wege())
    assert int(_row(table, "default", "leisure_activity")["n_unweighted"]) == 2      # 702 + 799
    assert int(_row(table, "codeplan", "leisure_activity")["n_unweighted"]) == 1     # 702 only
    assert int(_row(table, "default", "other_errand_long")["n_unweighted"]) == 2     # 603 + 699
    assert int(_row(table, "codeplan", "other_errand_long")["n_unweighted"]) == 1    # 603 only
    assert diagnostics["default/leisure"]["n_labelled"] == 8
    assert diagnostics["codeplan/leisure"]["n_labelled"] == 7
    assert diagnostics["codeplan/leisure"]["n_sentinel"] == 1


def test_a_missing_distance_code_is_excluded_from_the_percentiles_counted_and_reported(caplog):
    """A wegkm_imp 9994 leg still counts as a leg (it has a purpose and a weight) but must not
    enter the percentiles -- and the exclusion must be visible as a count and a rate."""
    caplog.set_level("INFO")
    wege = _wege(extra=[_leg(4, 501, 9994.0)])
    table, diagnostics = M.build_group_reference(wege)
    daily = _row(table, "default", "shop_daily")
    assert int(daily["n_unweighted"]) == 3           # the coded leg is still a shop_daily leg
    assert int(daily["n_missing_distance"]) == 1
    # Percentiles come from the two real legs (1 km, 3 km) only; a 9994 km value would blow p75 up.
    assert daily["km_p75"] <= 3.0
    assert diagnostics["default/shop"]["n_missing_distance"] == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("wegkm_imp usable for 4/5 labelled legs (80.00%)" in message
               for message in messages), messages
    assert any("1 carry a missing-value code (>= 9994)" in message for message in messages), messages


def test_a_group_whose_every_leg_carries_a_missing_distance_code_gets_nan_percentiles():
    wege = _wege()
    wege.loc[wege["W_ZWD"].isin([708, 709]), "wegkm_imp"] = M.DISTANCE_MISSING_CODE_MIN
    table, _ = M.build_group_reference(wege)
    excursion = _row(table, "default", "leisure_excursion")
    assert int(excursion["n_unweighted"]) == 2 and int(excursion["n_missing_distance"]) == 2
    assert np.isnan(excursion["km_p25"]) and np.isnan(excursion["km_p50"]) \
        and np.isnan(excursion["km_p75"])
    # The share is unaffected: only the distance is unknown, not the leg.
    assert excursion["share_within_purpose"] > 0


def test_check_invariants_rejects_a_block_whose_shares_do_not_sum_to_one():
    table, _ = M.build_group_reference(_wege())
    M.check_invariants(table)
    broken = table.copy()
    broken.loc[0, "share_within_purpose"] = broken.loc[0, "share_within_purpose"] + 0.1
    with pytest.raises(ValueError, match="share_within_purpose"):
        M.check_invariants(broken)
