# tests/test_popsim_cells_nan_sum.py
"""Tests for cells.sum_columns_logging_nan (issue #150).

The helper row-sums a set of columns with the same skipna semantics as
``DataFrame.sum(axis=1)`` but first counts and logs NaN cells, so a
Zensus-suppressed (NaN) component is observable instead of silently becoming 0.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import cells as _cells


def test_sum_columns_no_nan_matches_plain_sum_and_is_quiet(caplog):
    """With no NaN the result equals a plain row-sum and nothing is logged."""
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})
    with caplog.at_level(logging.INFO):
        out = _cells.sum_columns_logging_nan(df, ["a", "b"], "test_label")
    pd.testing.assert_series_equal(out, df[["a", "b"]].sum(axis=1), check_names=False)
    assert not [r for r in caplog.records if "NaN" in r.message]


def test_sum_columns_empty_columns_yields_zero_series():
    """An empty column list yields an all-zero Series aligned to the index."""
    df = pd.DataFrame({"a": [1.0, 2.0]}, index=[5, 6])
    out = _cells.sum_columns_logging_nan(df, [], "empty")
    assert list(out.index) == [5, 6]
    assert (out == 0.0).all()


def test_sum_columns_nan_is_counted_and_logged_treated_as_zero(caplog):
    """A NaN cell is counted/logged (observable) and treated as 0 in the sum."""
    df = pd.DataFrame({"a": [1.0, np.nan], "b": [10.0, 20.0]})
    with caplog.at_level(logging.INFO):
        out = _cells.sum_columns_logging_nan(df, ["a", "b"], "mycontrol")
    # Behaviour preserved (skipna): row 1 = 0(nan)+20 = 20.
    assert out.iloc[0] == pytest.approx(11.0)
    assert out.iloc[1] == pytest.approx(20.0)
    logs = [r for r in caplog.records if "NaN" in r.message and "mycontrol" in r.message]
    assert logs, "NaN suppression was not logged"


def test_sum_columns_high_nan_rate_warns(caplog):
    """A NaN rate above the warn fraction is logged at WARNING (loud signal)."""
    # 3/4 cells NaN -> 75% >> warn fraction.
    df = pd.DataFrame({"a": [np.nan, np.nan], "b": [np.nan, 1.0]})
    with caplog.at_level(logging.INFO):
        _cells.sum_columns_logging_nan(df, ["a", "b"], "loud")
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "loud" in r.message]
    assert warns, "High NaN rate did not escalate to WARNING"
