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
