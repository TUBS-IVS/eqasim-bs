"""Tests for MiD CSV separator detection (braunschweig.popsim.mid).

The MiD 2023 scientific-use delivery occurs in both comma- and semicolon-
separated variants. A hard-coded separator silently mis-parses the other into a
single column, which then surfaces much later as a misleading "missing required
columns" error. These tests pin the header-based detection and that the Wege
reader handles a comma-separated delivery (the ZGB regional sample format).
"""
import pandas as pd
import pytest

from braunschweig.popsim import mid as mid_module


def _write_min_wege(path, sep):
    """Write a minimal MiD Wege file with all MID_WEGE_REQUIRED_COLS."""
    cols = list(mid_module.MID_WEGE_REQUIRED_COLS) + ["W_GEW", "hvm_imp"]
    row = {c: 1 for c in cols}
    pd.DataFrame([row, row]).to_csv(path, sep=sep, index=False)


def test_detect_csv_separator_comma(tmp_path):
    path = tmp_path / "comma.csv"
    pd.DataFrame({"a": [1], "b": [2], "c": [3]}).to_csv(path, sep=",", index=False)
    assert mid_module.detect_csv_separator(path) == ","


def test_detect_csv_separator_semicolon(tmp_path):
    path = tmp_path / "semi.csv"
    pd.DataFrame({"a": [1], "b": [2], "c": [3]}).to_csv(path, sep=";", index=False)
    assert mid_module.detect_csv_separator(path) == ";"


def test_detect_csv_separator_raises_when_no_separator(tmp_path):
    path = tmp_path / "single.csv"
    path.write_text("only_one_column\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="separator"):
        mid_module.detect_csv_separator(path)


def test_load_mid_wege_reads_comma_delivery(tmp_path):
    """The ZGB regional sample is comma-separated; the reader must parse it."""
    _write_min_wege(tmp_path / "MiD2023_Wege.csv", sep=",")
    df = mid_module.load_mid_wege(tmp_path)
    for col in mid_module.MID_WEGE_REQUIRED_COLS:
        assert col in df.columns
    assert len(df) == 2


def test_load_mid_wege_reads_semicolon_delivery(tmp_path):
    """A semicolon German-locale delivery must parse identically."""
    _write_min_wege(tmp_path / "MiD2023_Wege.csv", sep=";")
    df = mid_module.load_mid_wege(tmp_path)
    for col in mid_module.MID_WEGE_REQUIRED_COLS:
        assert col in df.columns
    assert len(df) == 2
