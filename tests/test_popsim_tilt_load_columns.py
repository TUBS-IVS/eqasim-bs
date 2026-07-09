"""Issue #136: the income-spatial-tilt cell columns must come from the single
``load_control_cells`` read instead of a second national parquet scan.

The stage previously re-opened the prepared-cells parquet (all 3.1M national
rows, incl. two string columns) just to fetch the rent / Eigentuemerquote /
household-weight columns for the tilt. These tests pin the replacement:

- ``tilt_extra_load_columns`` extends the parquet load column list with the
  tilt columns ONLY when the tilt is enabled (OFF path byte-identical).
- ``extract_tilt_cells`` builds the tilt working frame from the already-loaded,
  already-ZGB-filtered cells frame (no second read), tolerating absent optional
  columns exactly like the old raw-parquet mapping did (missing -> column
  absent -> downstream neutral-index warn path).
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import stage


def test_tilt_extra_load_columns_appended_when_enabled() -> None:
    load_cols = ["CTRL_A", "CTRL_B"]
    out = stage.tilt_extra_load_columns(True, load_cols)
    # Originals preserved in order, tilt columns appended.
    assert out[:2] == ["CTRL_A", "CTRL_B"]
    assert stage._TILT_RENT_COL in out
    assert stage._TILT_QUOTE_COL in out
    assert stage._TILT_HH_COL in out
    # Input list is not mutated.
    assert load_cols == ["CTRL_A", "CTRL_B"]


def test_tilt_extra_load_columns_no_duplicates_when_already_present() -> None:
    load_cols = ["CTRL_A", stage._TILT_HH_COL]
    out = stage.tilt_extra_load_columns(True, load_cols)
    assert out.count(stage._TILT_HH_COL) == 1


def test_tilt_extra_load_columns_unchanged_when_disabled() -> None:
    load_cols = ["CTRL_A", "CTRL_B"]
    out = stage.tilt_extra_load_columns(False, load_cols)
    # Byte-identical OFF guarantee: the load set is exactly the input.
    assert out == load_cols


def _cells_frame(**extra_cols) -> pd.DataFrame:
    base = {
        "ZENSUS100m": ["c1", "c2"],
        "RegionalSchlussel_ARS": ["031010000000", "031530000000"],
        "SOME_CONTROL": [1.0, 2.0],
    }
    base.update(extra_cols)
    return pd.DataFrame(base)


def test_extract_tilt_cells_selects_available_columns() -> None:
    cells = _cells_frame(**{
        stage._TILT_RENT_COL: [6.0, 9.0],
        stage._TILT_QUOTE_COL: [0.3, 0.6],
        stage._TILT_HH_COL: [10.0, 20.0],
    })
    out = stage.extract_tilt_cells(cells)
    assert list(out["ZENSUS100m"]) == ["c1", "c2"]
    assert stage._TILT_RENT_COL in out.columns
    assert stage._TILT_QUOTE_COL in out.columns
    assert stage._TILT_HH_COL in out.columns
    assert "RegionalSchlussel_ARS" in out.columns
    # Control columns are NOT dragged into the tilt frame.
    assert "SOME_CONTROL" not in out.columns
    # The result is a copy: mutating it must not touch the source frame.
    out[stage._TILT_RENT_COL] = 0.0
    assert cells[stage._TILT_RENT_COL].tolist() == [6.0, 9.0]


def test_extract_tilt_cells_tolerates_missing_optional_columns() -> None:
    # Rent / quote / HH absent (older parquet): the columns are simply absent,
    # matching the old raw-parquet mapping (downstream code warns + neutral index).
    cells = _cells_frame()
    out = stage.extract_tilt_cells(cells)
    assert stage._TILT_RENT_COL not in out.columns
    assert stage._TILT_QUOTE_COL not in out.columns
    assert stage._TILT_HH_COL not in out.columns
    assert "RegionalSchlussel_ARS" in out.columns


def test_extract_tilt_cells_requires_cell_id() -> None:
    cells = pd.DataFrame({"RegionalSchlussel_ARS": ["031010000000"]})
    with pytest.raises(ValueError, match="ZENSUS100m"):
        stage.extract_tilt_cells(cells)
