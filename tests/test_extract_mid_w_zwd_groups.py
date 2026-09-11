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
    """Two legs per group of all three purposes, so every block has a defined share.

    The two W_ZWECK 10 "anderer Zweck" legs carry only design sentinels (2202 "im PAPI nicht
    erhoben", 7704 "kein Einkaufs-, Erledigungs-, oder Freizeitweg"), which is what the
    ``leisure_unspecified`` group assumes; they are labelled by the W_ZWECK group in the
    "codeplan_unspecified" variant and are invisible to the other two, whose leisure spec has
    ``zweck_values == {7}``.
    """
    legs = [
        _leg(4, 501, 1.0), _leg(4, 501, 3.0),        # shop_daily
        _leg(4, 502, 5.0), _leg(4, 505, 7.0),        # shop_non_daily
        _leg(5, 601, 2.0), _leg(5, 602, 4.0),        # other_errand_short
        _leg(5, 603, 6.0), _leg(5, 699, 8.0),        # other_errand_long (699 only in "default")
        _leg(7, 706, 1.0), _leg(7, 710, 2.0),        # leisure_local
        _leg(7, 701, 3.0), _leg(7, 701, 4.0),        # leisure_visit
        _leg(7, 702, 5.0), _leg(7, 799, 6.0),        # leisure_activity (799 only in "default")
        _leg(7, 708, 40.0), _leg(7, 709, 60.0),      # leisure_excursion
        _leg(10, 2202, 3.0), _leg(10, 7704, 2.5),    # leisure_unspecified (third variant only)
    ]
    legs.extend(extra)
    return pd.DataFrame(legs)


def _wege_without_the_code_10_legs() -> pd.DataFrame:
    """The same fixture with the W_ZWECK 10 legs removed -- the pre-issue-#373 leg universe."""
    wege = _wege()
    return wege[wege["W_ZWECK"] != 10].reset_index(drop=True)


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


def test_spec_variants_names_the_three_measured_settings():
    """The third variant is the PRODUCTION composition: both flags on (issue #373, ADR-0115)."""
    assert M.SPEC_VARIANTS == ("default", "codeplan", "codeplan_unspecified")
    shop, errand, leisure = M.specs_for_variant("codeplan_unspecified")
    assert shop is M.SHOP_SPEC
    assert errand is P.OTHER_ERRAND_SPEC_CODEPLAN
    assert leisure is P.LEISURE_SPEC_CODEPLAN_UNSPECIFIED
    assert leisure is P.leisure_spec(True, True)
    # The fifth group is defined by the RAW purpose code, not by a W_ZWD detail code.
    assert leisure.zweck_groups == {P.LEISURE_UNSPECIFIED_GROUP: P.LEISURE_UNSPECIFIED_ZWECK}
    assert P.LEISURE_UNSPECIFIED_GROUP in leisure.group_names


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
    assert diagnostics["n_legs_raw"] == 20
    assert diagnostics["n_legs_after_weekday_rbw"] == 18 == len(filtered)
    assert diagnostics["n_legs_invalid_weight"] == 0
    assert diagnostics["n_values_coerced_to_nan"] == {c: 0 for c in M.REQUIRED_COLUMNS}


def test_filter_raises_on_a_non_positive_weight_and_names_the_rate():
    wege = _wege()
    wege.loc[0, "W_GEW"] = 0.0
    with pytest.raises(ValueError, match=r"1/18 legs \(5.56%\) have a missing or non-positive W_GEW"):
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
    assert any("1/18 values (5.56%) of column wegkm_imp" in message for message in messages), messages


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


# --------------------------------------------------------------- the third variant (issue #373)


def test_the_third_variant_adds_the_w_zweck_defined_leisure_group():
    """Five leisure rows summing to 1, the fifth measured on the two W_ZWECK 10 legs only.

    Labelled leisure legs under `codeplan_unspecified`: seven code-7 legs (799 is a sentinel
    here) plus the two code-10 legs, all weight 1.0, so every group's share is n/9. The
    percentiles come from the two code-10 distances 2.5 km and 3.0 km alone: with equal weights
    the Hazen midpoint CDF puts them at 0.25 and 0.75, so p25 = 2.5, p50 = 2.75, p75 = 3.0 --
    values no other leg of the fixture could produce.
    """
    table, diagnostics = M.build_group_reference(_wege())
    M.check_invariants(table)
    block = table[(table["spec_variant"] == "codeplan_unspecified") & (table["purpose"] == "leisure")]
    assert len(block) == 5
    assert sorted(block["group"]) == ["leisure_activity", "leisure_excursion", "leisure_local",
                                      "leisure_unspecified", "leisure_visit"]
    assert block["share_within_purpose"].sum() == pytest.approx(1.0)
    unspecified = _row(table, "codeplan_unspecified", "leisure_unspecified")
    assert int(unspecified["n_unweighted"]) == 2
    assert unspecified["share_within_purpose"] == pytest.approx(2.0 / 9.0)
    assert unspecified["km_p25"] == pytest.approx(2.5)
    assert unspecified["km_p50"] == pytest.approx(2.75)
    assert unspecified["km_p75"] == pytest.approx(3.0)
    assert int(unspecified["n_missing_distance"]) == 0
    assert diagnostics["codeplan_unspecified/leisure"]["n_labelled"] == 9
    assert diagnostics["codeplan_unspecified/leisure"]["n_purpose_legs"] == 10


def test_the_third_variant_shrinks_the_named_groups_raw_shares_by_one_common_factor():
    """The four W_ZWD groups keep exactly their legs; only the denominator grows (by the code-10
    legs), so every named group's share is scaled by the same factor -- which is why their
    comparable-universe shares in the SrV comparison are unchanged."""
    table, _ = M.build_group_reference(_wege())
    factor = 7.0 / 9.0
    for group in ("leisure_local", "leisure_visit", "leisure_activity", "leisure_excursion"):
        codeplan = _row(table, "codeplan", group)
        third = _row(table, "codeplan_unspecified", group)
        assert int(third["n_unweighted"]) == int(codeplan["n_unweighted"])
        assert third["share_within_purpose"] == pytest.approx(
            codeplan["share_within_purpose"] * factor)


def test_the_first_two_variant_blocks_are_unchanged_by_the_code_10_legs():
    """OFF-path identity: the `default` and `codeplan` specs have zweck_values == {7}, so a
    W_ZWECK 10 leg cannot enter their purpose universe at all. The two blocks must therefore be
    IDENTICAL to the ones the same fixture produces without any code-10 leg."""
    with_code_10, _ = M.build_group_reference(_wege())
    without_code_10, _ = M.build_group_reference(_wege_without_the_code_10_legs())
    old_blocks = with_code_10[with_code_10["spec_variant"].isin(("default", "codeplan"))]
    reference = without_code_10[without_code_10["spec_variant"].isin(("default", "codeplan"))]
    assert old_blocks.reset_index(drop=True).equals(reference.reset_index(drop=True))


def test_the_third_variant_sentinel_count_covers_only_the_code_7_legs():
    """A code-10 leg carries a design sentinel but IS labelled (by its W_ZWECK group), so it is
    not an exclusion and must not be counted as one -- otherwise the committed coverage header
    would report the group's own legs as sentinel legs. Only the code-7 sentinel (799 under this
    variant) remains."""
    _, diagnostics = M.build_group_reference(_wege())
    assert diagnostics["codeplan_unspecified/leisure"]["n_sentinel"] == 1
    assert diagnostics["codeplan/leisure"]["n_sentinel"] == 1


def test_a_code_10_leg_carrying_a_real_detail_code_is_counted_and_warned(caplog):
    """Fallback transparency (CLAUDE.md, MANDATORY): the W_ZWECK group OVERRIDES the detail code,
    and the spec ASSUMES code-10 legs carry only design sentinels. A leg where that does not hold
    is relabelled, so the count and the rate must be surfaced rather than happening silently.

    The extraction reports the counts per (purpose, spec variant) in its own INFO line; the
    WARNING comes from purpose_subtype.label_legs, the one place the override is computed."""
    caplog.set_level("INFO")
    table, _ = M.build_group_reference(_wege(extra=[_leg(10, 701, 12.0)]))
    unspecified = _row(table, "codeplan_unspecified", "leisure_unspecified")
    assert int(unspecified["n_unweighted"]) == 3          # the 701 leg is NOT a leisure_visit leg
    assert int(_row(table, "codeplan_unspecified", "leisure_visit")["n_unweighted"]) == 2
    messages = [record.getMessage() for record in caplog.records]
    assert any("codeplan_unspecified" in message and "3 by W_ZWECK group" in message
               and "1 of those overriding" in message for message in messages), messages
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("[purpose_subtype:leisure] 1/3 legs labelled by a W_ZWECK group (33.3%)" in message
               and "relabelled by the W_ZWECK group" in message for message in warnings), warnings


def test_no_override_warning_when_the_code_10_legs_carry_only_sentinels(caplog):
    caplog.set_level("INFO")
    M.build_group_reference(_wege())
    assert not [record for record in caplog.records if record.levelname == "WARNING"
                and "relabelled by the W_ZWECK group" in record.getMessage()]


def test_every_spec_variant_has_a_description_and_vice_versa():
    """Both the committed header and the comparison summary render EVERY variant from
    SPEC_VARIANT_DESCRIPTIONS, so an undescribed variant would print an unexplained block and a
    description without a variant would be dead text. The module raises at import time; this
    test states the invariant next to the data it protects."""
    assert set(M.SPEC_VARIANT_DESCRIPTIONS) == set(M.SPEC_VARIANTS)
    assert all(M.SPEC_VARIANT_DESCRIPTIONS[variant].strip() for variant in M.SPEC_VARIANTS)


def test_build_group_reference_labels_through_the_shared_helper(monkeypatch):
    """One labelling rule, one implementation (issue #373 review, ruling R10): the extraction
    must route every (variant, purpose) block through purpose_subtype.label_legs instead of
    re-deriving the zweck-first precedence, otherwise the committed reference could describe a
    mix the estimation never sees."""
    calls = []
    real = P.label_legs

    def recording(purpose_legs, spec):
        calls.append((spec.purpose_label, len(purpose_legs)))
        return real(purpose_legs, spec)

    monkeypatch.setattr(M, "label_legs", recording)
    table, _ = M.build_group_reference(_wege())
    assert len(calls) == 3 * len(M.SPEC_VARIANTS)          # three purposes per spec variant
    # The leisure universe widens from W_ZWECK {7} (8 legs) to {7, 10} (10 legs) in the third
    # variant -- the helper sees the widened frame, not a locally filtered one.
    assert calls.count(("leisure", 8)) == 2
    assert calls.count(("leisure", 10)) == 1
    # The table's group sizes are the helper's own labels, not a re-derived copy.
    labelled, _, _ = real(_wege()[_wege()["W_ZWECK"].isin({7, 10})],
                          P.LEISURE_SPEC_CODEPLAN_UNSPECIFIED)
    expected = labelled["_group"].value_counts().to_dict()
    block = table[(table["spec_variant"] == "codeplan_unspecified") & (table["purpose"] == "leisure")]
    assert dict(zip(block["group"], block["n_unweighted"].astype(int))) == expected


def test_filter_weekday_legs_uses_the_shared_universe_helper(monkeypatch):
    """One universe, one implementation (issue #373, ADR-0116): the committed reference and
    the model's own estimation (braunschweig.popsim.distance_distributions, the three MiD
    subtype deciders) must read the SAME leg universe, so this script's filter goes through
    braunschweig.popsim.trips.weekday_diary_leg_mask instead of re-deriving the kernwo/W_RBW
    rule. Pinned twice: the name this module holds IS the helper (identity), and the filter
    actually calls it (monkeypatch)."""
    from braunschweig.popsim import trips

    assert M.weekday_diary_leg_mask is trips.weekday_diary_leg_mask

    calls = []
    real = trips.weekday_diary_leg_mask

    def recording(frame, **kwargs):
        calls.append(len(frame))
        return real(frame, **kwargs)

    monkeypatch.setattr(M, "weekday_diary_leg_mask", recording)
    filtered, diagnostics = M.filter_weekday_legs(_wege())
    assert calls == [18]
    assert len(filtered) == 18 == diagnostics["n_legs_after_weekday_rbw"]


def test_filter_weekday_legs_result_is_unchanged_by_the_shared_helper():
    """The universe itself must not move: the same fixture, the same kept legs and the same
    diagnostics as before the helper was shared (the committed tables are regenerated and
    proven byte-identical for the same reason)."""
    wege = _wege(extra=[dict(_leg(4, 501, 1.0), kernwo=4),          # weekend leg
                        dict(_leg(4, 501, 1.0), W_RBW=1)])          # rbW summary leg
    filtered, diagnostics = M.filter_weekday_legs(wege)
    assert diagnostics["n_legs_raw"] == 20
    assert diagnostics["n_legs_after_weekday_rbw"] == 18 == len(filtered)
    assert set(filtered["kernwo"]) == {M.KERNWO_WEEKDAY_CODES[0]}
    assert set(filtered["W_RBW"]) == {0}


def test_kernwo_weekday_codes_is_the_shared_universe_definition():
    """The two extraction scripts' exported constant must BE the helper's value set, so a
    script importing KERNWO_WEEKDAY_CODES cannot describe a different weekday than the model
    (scripts/extract_mid_w_zwd_groups.py imports it from
    scripts/extract_mid_w_zweck_hwzweck1.py)."""
    from braunschweig.popsim import trips

    assert tuple(M.KERNWO_WEEKDAY_CODES) == trips.WEEKDAY_DIARY_KERNWO
    assert M.RBW_SUMMARY_LEG_CODE == trips.RBW_LEG_FLAG
