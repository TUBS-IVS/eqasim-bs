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
