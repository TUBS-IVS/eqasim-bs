"""Issue #127: generic multinomial W_ZWD subtype model."""
from __future__ import annotations

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
