"""Fallback-transparency tests for ``braunschweig.data.census.employment``
``_coerce_int`` (issue #163, item 3).

GENESIS legitimately uses "." (not surveyed) and "-" (exactly zero) as
suppression markers -- both correctly coerce to 0. Any OTHER non-numeric,
non-empty cell that coerces to 0 via ``pd.to_numeric(errors="coerce")`` is a
genuine parse failure masquerading as a real zero and must be counted/warned
(CLAUDE.md "Fallback transparency"), never silently swallowed.
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.data.census import employment


class TestCoerceIntCleanPath:
    def test_all_numeric_cells_parse_with_no_warning(self, capsys) -> None:
        series = pd.Series([100, 200, 300])
        out = employment._coerce_int(series, column_name="all_male")
        capture = capsys.readouterr().out
        assert list(out) == [100, 200, 300]
        assert capture == ""

    def test_genesis_suppression_markers_coerce_to_zero_without_warning(self, capsys) -> None:
        # "." (not surveyed) and "-" (exactly zero) are the documented GENESIS
        # suppression markers and must remain silent (identical behavior).
        series = pd.Series(["100", ".", "-", "200"])
        out = employment._coerce_int(series, column_name="all_male")
        capture = capsys.readouterr().out
        assert list(out) == [100, 0, 0, 200]
        assert capture == ""


class TestCoerceIntDegradedPath:
    def test_unexplained_non_numeric_cell_is_counted_and_reported(self, capsys) -> None:
        # One cell is a stray text label, not a recognised suppression marker
        # -- this must be counted and printed, unlike "." / "-".
        series = pd.Series(["100", "N/A", "300", "400", "500"])
        out = employment._coerce_int(series, column_name="all_male")
        capture = capsys.readouterr().out
        assert list(out) == [100, 0, 300, 400, 500]
        assert "1/5" in capture
        assert "all_male" in capture

    def test_warn_threshold_fires_above_five_percent(self, capsys) -> None:
        # 2 unexplained cells out of 10 = 20% > the 5% WARN floor.
        series = pd.Series(["100"] * 8 + ["garbled", "corrupt"])
        out = employment._coerce_int(series, column_name="all_female")
        capture = capsys.readouterr().out
        assert (out == 0).sum() == 2
        assert "WARNING" in capture

    def test_raise_above_fifty_percent_unexplained(self) -> None:
        # 6 of 10 cells unexplained (60% > 50% raise limit) -> the parse is
        # implausibly broken and must fail loudly rather than run quietly.
        series = pd.Series(["bad"] * 6 + ["100"] * 4)
        with pytest.raises(RuntimeError, match="almost certainly"):
            employment._coerce_int(series, column_name="all_male")
