"""Test that _status_given_segment uses the population status marginal for
data-absent segments (zero-row branch) instead of the uniform 1/5 fallback.

The relevant segment here is 'wohnmobile' (~2% of KBA fleet) which is absent
from the MiD bundesland table. Before this fix the zero-row branch returned
uniform status probabilities regardless of the population prior. After the fix
the caller can supply a `status_marginal` array and the absent segment inherits
that distribution.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

S = pytest.importorskip("braunschweig.synthesis.vehicles.segment")


def test_zero_row_uses_status_marginal_not_uniform():
    """An absent segment (zero row) must use status_marginal when provided."""
    df = pd.DataFrame({
        "region": ["NDS"] * 5,
        "segment": ["kompaktklasse"] * 5,
        "status": ["very_low", "low", "medium", "high", "very_high"],
        "share_pct": [10, 20, 40, 20, 10],
    })
    # 'wohnmobile' is absent from df -> zero row
    marg = np.array([0.1, 0.2, 0.4, 0.2, 0.1])
    out = S._status_given_segment(df, "NDS", ["kompaktklasse", "wohnmobile"],
                                  status_marginal=marg)
    # Row 0 (kompaktklasse) should be renormalised from share_pct, not the marginal.
    assert out.shape == (2, 5)
    # Row 1 (wohnmobile, absent) must equal the supplied marginal, not 1/5.
    assert np.allclose(out[1], marg), (
        f"Expected absent segment to use status_marginal {marg}, got {out[1]}"
    )
    assert not np.allclose(out[1], np.ones(5) / 5), (
        "Absent segment still got uniform; status_marginal was not applied"
    )


def test_zero_row_uniform_fallback_when_no_marginal():
    """Without status_marginal the zero-row branch still returns uniform (backward compat)."""
    df = pd.DataFrame({
        "region": ["NDS"] * 5,
        "segment": ["kompaktklasse"] * 5,
        "status": ["very_low", "low", "medium", "high", "very_high"],
        "share_pct": [10, 20, 40, 20, 10],
    })
    out = S._status_given_segment(df, "NDS", ["kompaktklasse", "wohnmobile"])
    assert np.allclose(out[1], np.ones(5) / 5), (
        "Without status_marginal the absent-segment row should still be uniform"
    )


def test_present_segment_unaffected_by_marginal():
    """A segment with data must use its own MiD share_pct regardless of status_marginal."""
    df = pd.DataFrame({
        "region": ["NDS"] * 5,
        "segment": ["kompaktklasse"] * 5,
        "status": ["very_low", "low", "medium", "high", "very_high"],
        "share_pct": [10, 20, 40, 20, 10],
    })
    marg = np.array([0.5, 0.1, 0.1, 0.1, 0.2])   # very different from share_pct
    out = S._status_given_segment(df, "NDS", ["kompaktklasse", "wohnmobile"],
                                  status_marginal=marg)
    expected = np.array([10, 20, 40, 20, 10], dtype=float)
    expected /= expected.sum()
    assert np.allclose(out[0], expected), (
        "A present segment must not be overridden by status_marginal"
    )
