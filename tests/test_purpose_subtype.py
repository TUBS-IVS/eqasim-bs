"""Issue #127: generic multinomial W_ZWD subtype model.

Issue #242 Task 5 additions (below the original #127 tests): the W_ZWD codebook
labels are now verified, and the two NO-DETAIL ("keine Angabe") codes -- 799
"Freizeit k.A." and 699 "Erledigung k.A." -- are treated as sentinels behind the
``purpose_subtype_codeplan_sentinels`` flag via ``LEISURE_SPEC_CODEPLAN`` /
``OTHER_ERRAND_SPEC_CODEPLAN`` and the ``leisure_spec`` / ``other_errand_spec``
selectors.
"""
from __future__ import annotations

import inspect
import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import purpose_subtype as ps


SPEC = ps.SubtypeSpec(
    purpose_label="leisure",
    zweck_values=frozenset({7}),
    groups={"local": frozenset({711}), "visit": frozenset({701})},
    sentinels=frozenset({2202}),
)


def _wege(n_local=40, n_visit=40, n_sentinel=10):
    rows = ([(7, 711, "car", 200.0)] * n_local
            + [(7, 701, "car", 200.0)] * n_visit
            + [(7, 2202, "car", 200.0)] * n_sentinel)
    df = pd.DataFrame(rows, columns=["W_ZWECK", "W_ZWD", "mode", "travel_time"])
    df["W_GEW"] = 1.0
    return df


def test_estimate_cell_and_marginal_probabilities():
    cell_probs, marginal = ps.estimate_group_probabilities(_wege(), SPEC, min_obs=30)
    assert cell_probs[("car", 0)]["local"] == pytest.approx(0.5)
    assert marginal["visit"] == pytest.approx(0.5)


def test_thin_cells_fall_back_to_marginal():
    cell_probs, marginal = ps.estimate_group_probabilities(
        _wege(n_local=5, n_visit=5), SPEC, min_obs=30)
    assert ("car", 0) not in cell_probs
    assert marginal["local"] == pytest.approx(0.5)


def test_impute_is_deterministic_and_uses_marginal_for_unknown_cells():
    cell_probs = {("car", 0): {"local": 1.0, "visit": 0.0}}
    marginal = {"local": 0.0, "visit": 1.0}
    rng = np.random.RandomState(0)
    out = ps.impute_groups(np.array(["car", "walk"]), np.array([100.0, 100.0]),
                           cell_probs, marginal, rng)
    assert out.tolist() == ["local", "visit"]  # known cell -> local; unknown -> marginal


def test_code_coverage_guard_raises_on_unmapped_code():
    df = _wege()
    df.loc[len(df)] = (7, 777, "car", 200.0, 1.0)  # unmapped labelled code
    with pytest.raises(ValueError, match="777"):
        ps.code_coverage_guard(df, SPEC)


def _leisure_measured_wege() -> pd.DataFrame:
    """One row per W_ZWD code observed for W_ZWECK=7 (leisure) in the 2026-07-09
    measured code inventory (design spec's table).

    NOTE: these code lists are transcribed independently from the measurement, NOT
    derived from ps.LEISURE_SPEC/ps.LEISURE_GROUPS/ps.LEISURE_SENTINELS -- this is
    deliberate anti-circularity. A fixture built from `spec.group_codes | spec.sentinels`
    would trivially pass the coverage guard even if a module constant were miscopied,
    because it always equals the codes the guard is checking against. Hard-coding the
    measured codes here means a future miscopy of LEISURE_GROUPS/LEISURE_SENTINELS makes
    this test fail for real.
    """
    codes = [701, 702, 703, 704, 706, 707, 708, 709, 710, 711, 713, 716, 720, 721, 722,
             799, 2202, 4402, 503, 603, 605]
    df = pd.DataFrame({
        "W_ZWECK": [7] * len(codes),
        "W_ZWD": codes,
        "mode": ["car"] * len(codes),
        "travel_time": [200.0] * len(codes),
    })
    df["W_GEW"] = 1.0
    return df


def _other_errand_measured_wege() -> pd.DataFrame:
    """One row per W_ZWD code observed for W_ZWECK=5 (other errand) in the 2026-07-09
    measured code inventory (design spec's table).

    Independently transcribed, not derived from ps.OTHER_ERRAND_SPEC -- see the
    anti-circularity note on `_leisure_measured_wege`.
    """
    codes = [601, 602, 603, 604, 605, 699, 503, 504, 701, 706, 711, 713, 716, 721,
             2202, 4402]
    df = pd.DataFrame({
        "W_ZWECK": [5] * len(codes),
        "W_ZWD": codes,
        "mode": ["car"] * len(codes),
        "travel_time": [200.0] * len(codes),
    })
    df["W_GEW"] = 1.0
    return df


def test_code_coverage_guard_passes_for_leisure_and_other_errand_specs():
    # Exercises the guard against an INDEPENDENTLY hard-coded transcription of the
    # 2026-07-09 measured W_ZWD code inventory (see _leisure_measured_wege /
    # _other_errand_measured_wege), not a fixture derived from LEISURE_SPEC /
    # OTHER_ERRAND_SPEC -- this is a real cross-check: every measured code must be
    # classified (group or sentinel) by the module-level constants under test.
    ps.code_coverage_guard(_leisure_measured_wege(), ps.LEISURE_SPEC)
    ps.code_coverage_guard(_other_errand_measured_wege(), ps.OTHER_ERRAND_SPEC)


def test_code_coverage_guard_raises_on_unmapped_code_in_measured_inventory():
    # Same independently hard-coded inventory as above, plus one code the module-level
    # LEISURE_SPEC does not classify; confirms the guard still fails loudly when a real
    # future MiD delivery introduces an unmapped code.
    df = _leisure_measured_wege()
    df.loc[len(df)] = (7, 777, "car", 200.0, 1.0)
    with pytest.raises(ValueError, match="777"):
        ps.code_coverage_guard(df, ps.LEISURE_SPEC)


def test_impute_groups_fractional_draw_is_deterministic():
    # Task-1 review flagged that the vectorised cumsum/searchsorted path in
    # impute_groups was validated for fractional probabilities only out-of-band; this
    # puts a genuinely fractional (non-degenerate) split under CI.
    cell_probs = {("car", 0): {"a": 0.3, "b": 0.7}}
    marginal = {"a": 0.3, "b": 0.7}
    modes = np.array(["car"] * 200)
    tt_values = np.array([100.0] * 200)

    rng_first = np.random.RandomState(42)
    out_first = ps.impute_groups(modes, tt_values, cell_probs, marginal, rng_first)

    rng_second = np.random.RandomState(42)
    out_second = ps.impute_groups(modes, tt_values, cell_probs, marginal, rng_second)

    # Same seed, same inputs -> bit-identical output (determinism).
    assert out_first.tolist() == out_second.tolist()

    # Sanity, not exactness: the drawn shares should be roughly in line with the
    # requested probabilities.
    share_a = float((out_first == "a").mean())
    assert abs(share_a - 0.3) < 0.1


# ---------------------------------------------------------------------------
# Issue #242 Task 5: codebook labels verified; 799 / 699 become sentinels
# behind purpose_subtype_codeplan_sentinels (LEISURE_SPEC_CODEPLAN /
# OTHER_ERRAND_SPEC_CODEPLAN, ADR-0113).
# ---------------------------------------------------------------------------


def test_every_group_code_has_a_codebook_label_comment():
    # The module source must no longer carry the Task-2 placeholder now that
    # every group's W_ZWD codes have been checked against
    # MiD2023_Codeplaene_B1_Standard_v1.1.xlsx (issue #242 Task 5).
    source = inspect.getsource(ps)
    assert "label to verify" not in source


def test_default_specs_are_unchanged():
    # Pins today's LEISURE_GROUPS / OTHER_ERRAND_GROUPS / *_SENTINELS sets
    # literally, so a future edit to the codeplan-sentinel wiring cannot
    # silently also change the unflagged (OFF-path) groups.
    assert ps.LEISURE_GROUPS == {
        "leisure_local":     frozenset({706, 710, 711, 713, 716}),
        "leisure_visit":     frozenset({701}),
        "leisure_activity":  frozenset({702, 703, 704, 707, 720, 721, 799}),
        "leisure_excursion": frozenset({708, 709, 722}),
    }
    assert ps.LEISURE_SENTINELS == frozenset({2202, 4402, 599, 999, 503, 603, 605, 7704, 7705})
    assert ps.OTHER_ERRAND_GROUPS == {
        "other_errand_short": frozenset({601, 602}),
        "other_errand_long":  frozenset({603, 604, 605, 699}),
    }
    assert ps.OTHER_ERRAND_SENTINELS == frozenset({2202, 4402, 7704, 7705, 599, 999,
                                                    503, 504, 701, 706, 711, 713, 716, 721})


def test_codeplan_specs_move_799_and_699_to_sentinels():
    # 799 "Freizeit k.A." leaves leisure_activity and becomes a sentinel.
    assert 799 not in ps.LEISURE_SPEC_CODEPLAN.groups["leisure_activity"]
    assert ps.LEISURE_SPEC_CODEPLAN.groups["leisure_activity"] == \
        ps.LEISURE_GROUPS["leisure_activity"] - {799}
    assert 799 in ps.LEISURE_SPEC_CODEPLAN.sentinels
    assert ps.LEISURE_SPEC_CODEPLAN.sentinels == ps.LEISURE_SENTINELS | {799}
    # Every other leisure group is untouched.
    for name in ("leisure_local", "leisure_visit", "leisure_excursion"):
        assert ps.LEISURE_SPEC_CODEPLAN.groups[name] == ps.LEISURE_GROUPS[name]

    # 699 "Erledigung k.A." leaves other_errand_long and becomes a sentinel.
    assert 699 not in ps.OTHER_ERRAND_SPEC_CODEPLAN.groups["other_errand_long"]
    assert ps.OTHER_ERRAND_SPEC_CODEPLAN.groups["other_errand_long"] == \
        ps.OTHER_ERRAND_GROUPS["other_errand_long"] - {699}
    assert 699 in ps.OTHER_ERRAND_SPEC_CODEPLAN.sentinels
    assert ps.OTHER_ERRAND_SPEC_CODEPLAN.sentinels == ps.OTHER_ERRAND_SENTINELS | {699}
    # other_errand_short is untouched.
    assert ps.OTHER_ERRAND_SPEC_CODEPLAN.groups["other_errand_short"] == \
        ps.OTHER_ERRAND_GROUPS["other_errand_short"]


def test_codeplan_specs_are_valid_subtype_specs():
    # SubtypeSpec.__post_init__ raises on an overlap between groups and
    # sentinels or an empty groups dict; simply constructing these objects
    # (done at module import time) already proves the move did not create an
    # overlap, but assert the partition explicitly here too.
    for spec in (ps.LEISURE_SPEC_CODEPLAN, ps.OTHER_ERRAND_SPEC_CODEPLAN):
        assert not (spec.group_codes & spec.sentinels)


def test_leisure_spec_selector_off_path_is_identity():
    # Identity, not a re-derived equivalent object -- this is what makes the
    # OFF path byte-identical "by construction".
    assert ps.leisure_spec(False) is ps.LEISURE_SPEC


def test_leisure_spec_selector_on_path_returns_codeplan_variant():
    assert ps.leisure_spec(True) is ps.LEISURE_SPEC_CODEPLAN


def test_other_errand_spec_selector_off_path_is_identity():
    assert ps.other_errand_spec(False) is ps.OTHER_ERRAND_SPEC


def test_other_errand_spec_selector_on_path_returns_codeplan_variant():
    assert ps.other_errand_spec(True) is ps.OTHER_ERRAND_SPEC_CODEPLAN


def test_code_coverage_guard_passes_for_codeplan_specs_on_measured_inventory():
    # The same INDEPENDENTLY hard-coded 2026-07-09 measured code inventory used
    # by test_code_coverage_guard_passes_for_leisure_and_other_errand_specs
    # above must also be fully covered by the CODEPLAN variant: moving 799/699
    # from a group to a sentinel must not leave either code unmapped.
    ps.code_coverage_guard(_leisure_measured_wege(), ps.LEISURE_SPEC_CODEPLAN)
    ps.code_coverage_guard(_other_errand_measured_wege(), ps.OTHER_ERRAND_SPEC_CODEPLAN)


# --- Estimation goldens (issue #242 Task 5) ---------------------------------
#
# Both goldens below are computed from the SAME synthetic MiD Wege fixture; only
# the spec passed to estimate_group_probabilities differs (LEISURE_SPEC vs.
# LEISURE_SPEC_CODEPLAN, OTHER_ERRAND_SPEC vs. OTHER_ERRAND_SPEC_CODEPLAN). Every
# row shares one mode ("car") and one travel_time (200.0s, tt_band 0), so all
# labelled legs fall into a SINGLE (mode, band) cell and the cell probability is
# numerically identical to the marginal -- this isolates the effect of moving
# 799 / 699 to a sentinel from any cell-vs-marginal fallback behaviour.
# min_obs=15 is used (rather than the estimate_group_probabilities default of
# 30) so the smallest golden cell (the ON-spec other_errand denominator of 20
# legs) still qualifies for its own cell-level estimate rather than falling
# back to the marginal.

_GOLDEN_MIN_OBS = 15


def _leisure_codeplan_golden_wege() -> pd.DataFrame:
    """Ten legs per leisure group code (one group code per group, except
    leisure_activity which gets TWO distinct codes: 702, a genuine activity
    code that stays a group member under both specs, and 799, the NO-DETAIL
    code that only LEISURE_SPEC_CODEPLAN excludes) -- so moving 799 to the
    sentinel set changes the "leisure_activity" share specifically, not merely
    the denominator uniformly."""
    rows = []
    for w_zwd, n in ((706, 10), (701, 10), (702, 10), (799, 10), (708, 10)):
        rows.extend([(7, w_zwd, "car", 200.0)] * n)
    df = pd.DataFrame(rows, columns=["W_ZWECK", "W_ZWD", "mode", "travel_time"])
    df["W_GEW"] = 1.0
    return df


def test_leisure_estimation_golden_off_spec_kept_unchanged():
    # OFF (LEISURE_SPEC): 799 counts toward leisure_activity, exactly as it
    # always has -- this is the golden the OFF path must keep reproducing.
    wege = _leisure_codeplan_golden_wege()
    cell_probs, marginal = ps.estimate_group_probabilities(
        wege, ps.LEISURE_SPEC, min_obs=_GOLDEN_MIN_OBS)
    expected = {
        "leisure_local": 0.2, "leisure_visit": 0.2,
        "leisure_activity": 0.4, "leisure_excursion": 0.2,
    }
    assert marginal == pytest.approx(expected)
    band = ps.tt_band(200.0)
    assert cell_probs[("car", band)] == pytest.approx(expected)


def test_leisure_estimation_golden_on_spec_codeplan_sentinels():
    # ON (LEISURE_SPEC_CODEPLAN): the ten 799 rows leave the labelled
    # denominator entirely (50 -> 40 labelled legs), so leisure_activity drops
    # from 0.4 to 10/40 = 0.25 and the other three groups rise from 0.2 to
    # 10/40 = 0.25 (the 799 mass no longer dilutes them). Same fixture as the
    # OFF golden above -- only the spec differs.
    wege = _leisure_codeplan_golden_wege()
    cell_probs, marginal = ps.estimate_group_probabilities(
        wege, ps.LEISURE_SPEC_CODEPLAN, min_obs=_GOLDEN_MIN_OBS)
    expected = {
        "leisure_local": 0.25, "leisure_visit": 0.25,
        "leisure_activity": 0.25, "leisure_excursion": 0.25,
    }
    assert marginal == pytest.approx(expected)
    band = ps.tt_band(200.0)
    assert cell_probs[("car", band)] == pytest.approx(expected)


def _other_errand_codeplan_golden_wege() -> pd.DataFrame:
    """Ten legs per other_errand group code: 601 (other_errand_short), 603 (a
    genuine other_errand_long code under both specs) and 699 (the NO-DETAIL
    code that only OTHER_ERRAND_SPEC_CODEPLAN excludes)."""
    rows = []
    for w_zwd, n in ((601, 10), (603, 10), (699, 10)):
        rows.extend([(5, w_zwd, "car", 200.0)] * n)
    df = pd.DataFrame(rows, columns=["W_ZWECK", "W_ZWD", "mode", "travel_time"])
    df["W_GEW"] = 1.0
    return df


def test_other_errand_estimation_golden_off_spec_kept_unchanged():
    # OFF (OTHER_ERRAND_SPEC): 699 counts toward other_errand_long, exactly as
    # it always has.
    wege = _other_errand_codeplan_golden_wege()
    cell_probs, marginal = ps.estimate_group_probabilities(
        wege, ps.OTHER_ERRAND_SPEC, min_obs=_GOLDEN_MIN_OBS)
    expected = {"other_errand_short": 10 / 30, "other_errand_long": 20 / 30}
    assert marginal == pytest.approx(expected)
    band = ps.tt_band(200.0)
    assert cell_probs[("car", band)] == pytest.approx(expected)


def test_other_errand_estimation_golden_on_spec_codeplan_sentinels():
    # ON (OTHER_ERRAND_SPEC_CODEPLAN): the ten 699 rows leave the labelled
    # denominator entirely (30 -> 20 labelled legs), so other_errand_long
    # drops from 2/3 to 10/20 = 0.5 and other_errand_short rises from 1/3 to
    # 10/20 = 0.5. Same fixture as the OFF golden above -- only the spec
    # differs.
    wege = _other_errand_codeplan_golden_wege()
    cell_probs, marginal = ps.estimate_group_probabilities(
        wege, ps.OTHER_ERRAND_SPEC_CODEPLAN, min_obs=_GOLDEN_MIN_OBS)
    expected = {"other_errand_short": 0.5, "other_errand_long": 0.5}
    assert marginal == pytest.approx(expected)
    band = ps.tt_band(200.0)
    assert cell_probs[("car", band)] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Issue #373 (ADR-0115): W_ZWECK-defined subtype groups and the
# "leisure_unspecified" fifth leisure subtype. MiD W_ZWECK 10 legs are folded to
# leisure by w_zweck_10_as_leisure but never carry a W_ZWD detail code (only the
# design sentinels), so they are labelled by their RAW purpose code instead of by
# W_ZWD (SubtypeSpec.zweck_groups / LEISURE_SPEC_UNSPECIFIED).
# ---------------------------------------------------------------------------


def test_zweck_group_validation():
    with pytest.raises(ValueError, match="zweck group"):
        ps.SubtypeSpec("x", frozenset({7, 10}), {"a": frozenset({701})}, frozenset(),
                       zweck_groups={"a": frozenset({10})})          # name clash
    with pytest.raises(ValueError, match="zweck_values"):
        ps.SubtypeSpec("x", frozenset({7}), {"a": frozenset({701})}, frozenset(),
                       zweck_groups={"u": frozenset({10})})          # code outside zweck_values
    with pytest.raises(ValueError, match="more than one zweck group"):
        ps.SubtypeSpec("x", frozenset({7, 10, 11}), {"a": frozenset({701})}, frozenset(),
                       zweck_groups={"u": frozenset({10}), "v": frozenset({10, 11})})


def test_leisure_spec_selector_returns_the_four_constants_by_identity():
    assert ps.leisure_spec(False) is ps.LEISURE_SPEC
    assert ps.leisure_spec(False, False) is ps.LEISURE_SPEC
    assert ps.leisure_spec(True) is ps.LEISURE_SPEC_CODEPLAN
    assert ps.leisure_spec(False, True) is ps.LEISURE_SPEC_UNSPECIFIED
    assert ps.leisure_spec(True, True) is ps.LEISURE_SPEC_CODEPLAN_UNSPECIFIED


def test_unspecified_specs_add_only_the_zweck_group():
    for base, spec in ((ps.LEISURE_SPEC, ps.LEISURE_SPEC_UNSPECIFIED),
                       (ps.LEISURE_SPEC_CODEPLAN, ps.LEISURE_SPEC_CODEPLAN_UNSPECIFIED)):
        assert spec.groups == base.groups and spec.sentinels == base.sentinels
        assert spec.zweck_values == base.zweck_values | ps.LEISURE_UNSPECIFIED_ZWECK
        assert spec.zweck_groups == {ps.LEISURE_UNSPECIFIED_GROUP: ps.LEISURE_UNSPECIFIED_ZWECK}
        assert spec.group_names == sorted([*base.groups, ps.LEISURE_UNSPECIFIED_GROUP])
    assert ps.LEISURE_SPEC.zweck_groups == {} and ps.LEISURE_SPEC.group_names == sorted(ps.LEISURE_GROUPS)


def _frame_with_code_10_legs():
    # 4 labelled code-7 legs (2 visit, 2 local) and 3 code-10 legs carrying design sentinels.
    return pd.DataFrame({
        "W_ZWECK": [7, 7, 7, 7, 10, 10, 10],
        "W_ZWD": [701, 701, 710, 706, 2202, 7704, 4402],
        "mode": ["car"] * 7, "travel_time": [600.0] * 7, "W_GEW": [1.0] * 7,
    })


def test_estimation_labels_code_10_legs_by_w_zweck_and_ignores_their_w_zwd():
    cell_probs, marginal = ps.estimate_group_probabilities(
        _frame_with_code_10_legs(), ps.LEISURE_SPEC_UNSPECIFIED, min_obs=1)
    assert marginal[ps.LEISURE_UNSPECIFIED_GROUP] == pytest.approx(3 / 7)
    assert marginal["leisure_visit"] == pytest.approx(2 / 7)
    assert marginal["leisure_local"] == pytest.approx(2 / 7)
    assert sum(marginal.values()) == pytest.approx(1.0)
    assert set(cell_probs[("car", ps.tt_band(600.0))]) == set(ps.LEISURE_SPEC_UNSPECIFIED.group_names)


def test_estimation_without_the_zweck_group_is_unchanged_by_code_10_legs():
    frame = _frame_with_code_10_legs()
    with_10, marginal_with_10 = ps.estimate_group_probabilities(frame, ps.LEISURE_SPEC, min_obs=1)
    without_10, marginal_without = ps.estimate_group_probabilities(
        frame[frame["W_ZWECK"] == 7], ps.LEISURE_SPEC, min_obs=1)
    assert marginal_with_10 == marginal_without and with_10 == without_10
    assert ps.LEISURE_UNSPECIFIED_GROUP not in marginal_with_10


def test_code_coverage_guard_accepts_sentinel_only_code_10_legs():
    # Under LEISURE_SPEC_UNSPECIFIED the guard reads W_ZWECK 10 legs too; their W_ZWD is
    # always a design sentinel (2202 / 4402 / 7704), all already in LEISURE_SENTINELS, so
    # widening zweck_values must not trip the guard.
    ps.code_coverage_guard(_frame_with_code_10_legs(), ps.LEISURE_SPEC_UNSPECIFIED)


def test_code_coverage_guard_raises_on_unknown_w_zwd_of_a_code_10_leg():
    # The guard must stay loud for a code-10 leg carrying a W_ZWD the spec does not
    # classify -- widening zweck_values must not open a silent NaN bucket.
    frame = _frame_with_code_10_legs()
    frame.loc[len(frame)] = (10, 123, "car", 600.0, 1.0)
    with pytest.raises(ValueError, match="123"):
        ps.code_coverage_guard(frame, ps.LEISURE_SPEC_UNSPECIFIED)


# A zweck group labels its legs whatever their W_ZWD says, which is only defensible while those
# legs really carry no usable detail code (the ASSUMPTION stated in the module docstring). The
# two tests below pin that estimation MEASURES the assumption instead of relying on it: a
# W_ZWECK-group leg carrying a real group code is counted as an override and warned about.
_OVERRIDE_WARNING_MARKER = "relabelled by the W_ZWECK group"


def _warning_messages(caplog) -> list:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


def test_zweck_group_overriding_a_valid_detail_code_is_counted_and_warned(caplog):
    # One of the three code-10 legs carries 708 (a leisure_excursion code) instead of a design
    # sentinel -- the case the spec assumes does not occur.
    frame = _frame_with_code_10_legs()
    frame.loc[frame.index[-1], "W_ZWD"] = 708

    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.purpose_subtype"):
        _, marginal = ps.estimate_group_probabilities(
            frame, ps.LEISURE_SPEC_UNSPECIFIED, min_obs=1)

    # The W_ZWECK group still wins: the 708 leg is leisure_unspecified, not leisure_excursion.
    assert marginal[ps.LEISURE_UNSPECIFIED_GROUP] == pytest.approx(3 / 7)
    assert marginal["leisure_excursion"] == pytest.approx(0.0)

    # ... but the override is reported loudly, with its count and rate (1 of 3 zweck-group legs).
    messages = _warning_messages(caplog)
    assert any(_OVERRIDE_WARNING_MARKER in message for message in messages), messages
    assert any("1/3" in message and "33.3%" in message for message in messages), messages


def test_no_override_warning_when_code_10_legs_carry_only_sentinels(caplog):
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.purpose_subtype"):
        ps.estimate_group_probabilities(
            _frame_with_code_10_legs(), ps.LEISURE_SPEC_UNSPECIFIED, min_obs=1)

    overrides = [message for message in _warning_messages(caplog)
                 if _OVERRIDE_WARNING_MARKER in message]
    assert not overrides, overrides


# --------------------------------------------------------------- label_legs (issue #373, R10)
# The labelling rule has exactly ONE implementation, shared by estimate_group_probabilities and
# scripts/extract_mid_w_zwd_groups.py. These tests exercise it directly, so a change to the
# precedence or to the override counting is caught here rather than only through its two callers.


def _leisure_purpose_legs(spec):
    """The code-10 fixture restricted to the spec's own W_ZWECK universe, as callers pass it."""
    frame = _frame_with_code_10_legs()
    return frame[frame["W_ZWECK"].isin(spec.zweck_values)]


def test_label_legs_prefers_the_zweck_group_and_keeps_row_order():
    spec = ps.LEISURE_SPEC_UNSPECIFIED
    purpose_legs = _leisure_purpose_legs(spec)
    labelled, n_by_zweck, n_override = ps.label_legs(purpose_legs, spec)

    # All seven legs are labelled: four by their W_ZWD group, three by the W_ZWECK group whose
    # design sentinels (2202 / 7704 / 4402) no W_ZWD group would have labelled.
    assert list(labelled["_group"]) == ["leisure_visit", "leisure_visit", "leisure_local",
                                        "leisure_local", ps.LEISURE_UNSPECIFIED_GROUP,
                                        ps.LEISURE_UNSPECIFIED_GROUP,
                                        ps.LEISURE_UNSPECIFIED_GROUP]
    # Row order preserved and the labels aligned with the rows they describe.
    assert list(labelled["W_ZWD"]) == [701, 701, 710, 706, 2202, 7704, 4402]
    assert n_by_zweck == 3 and n_override == 0
    # A copy, never a view: the caller's frame keeps its columns.
    assert "_group" not in purpose_legs.columns


def test_label_legs_counts_a_zweck_group_overriding_a_valid_detail_code(caplog):
    spec = ps.LEISURE_SPEC_UNSPECIFIED
    frame = _frame_with_code_10_legs()
    frame.loc[len(frame)] = (10, 708, "car", 600.0, 1.0)     # 708 IS a leisure_excursion code
    purpose_legs = frame[frame["W_ZWECK"].isin(spec.zweck_values)]
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.purpose_subtype"):
        labelled, n_by_zweck, n_override = ps.label_legs(purpose_legs, spec)

    assert n_by_zweck == 4 and n_override == 1
    assert list(labelled["_group"]).count(ps.LEISURE_UNSPECIFIED_GROUP) == 4
    assert "leisure_excursion" not in set(labelled["_group"])
    messages = _warning_messages(caplog)
    assert any(_OVERRIDE_WARNING_MARKER in message and "1/4" in message and "25.0%" in message
               for message in messages), messages


def test_label_legs_without_zweck_groups_is_the_plain_detail_code_rule(caplog):
    """OFF path: with an empty zweck_groups the helper must reduce exactly to "label by the
    detail code", report zero W_ZWECK-group legs and emit no override warning."""
    spec = ps.LEISURE_SPEC
    purpose_legs = _leisure_purpose_legs(spec)          # W_ZWECK 7 only
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.purpose_subtype"):
        labelled, n_by_zweck, n_override = ps.label_legs(purpose_legs, spec)

    assert n_by_zweck == 0 and n_override == 0
    assert list(labelled["_group"]) == ["leisure_visit", "leisure_visit", "leisure_local",
                                        "leisure_local"]
    assert not [message for message in _warning_messages(caplog)
                if _OVERRIDE_WARNING_MARKER in message]


def test_label_legs_drops_unlabelled_legs_and_requires_its_columns():
    spec = ps.LEISURE_SPEC
    frame = pd.DataFrame({"W_ZWECK": [7, 7], "W_ZWD": [701, 2202],
                          "mode": ["car"] * 2, "travel_time": [600.0] * 2, "W_GEW": [1.0] * 2})
    labelled, n_by_zweck, n_override = ps.label_legs(frame, spec)
    assert list(labelled["_group"]) == ["leisure_visit"]     # the sentinel leg is unlabelled
    assert n_by_zweck == 0 and n_override == 0
    with pytest.raises(ValueError, match="W_ZWD"):
        ps.label_legs(frame.drop(columns=["W_ZWD"]), spec)


def test_zweck_group_codes_is_the_union_of_the_zweck_groups():
    assert ps.LEISURE_SPEC.zweck_group_codes == frozenset()
    assert ps.LEISURE_SPEC_UNSPECIFIED.zweck_group_codes == ps.LEISURE_UNSPECIFIED_ZWECK
    assert ps.LEISURE_SPEC_CODEPLAN_UNSPECIFIED.zweck_group_codes == ps.LEISURE_UNSPECIFIED_ZWECK


def test_estimation_labels_through_the_shared_helper(monkeypatch):
    """estimate_group_probabilities must not re-derive the precedence rule locally."""
    calls = []
    real = ps.label_legs

    def recording(purpose_legs, spec):
        calls.append((spec.purpose_label, len(purpose_legs)))
        return real(purpose_legs, spec)

    monkeypatch.setattr(ps, "label_legs", recording)
    ps.estimate_group_probabilities(_frame_with_code_10_legs(), ps.LEISURE_SPEC_UNSPECIFIED,
                                    min_obs=1)
    assert calls == [("leisure", 7)]
