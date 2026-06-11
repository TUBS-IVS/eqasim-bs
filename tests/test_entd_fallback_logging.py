"""Fallback-rate logging for the ENTD prefix maps (no-silent-fallback rule).

``data.hts.entd.cleaned`` initialises ``following_purpose``/``preceding_purpose``
to "other" and ``mode`` to "pt" and then overwrites prefix matches. Codes that
match NO map prefix keep the default -- an (inherited) silent fallback. The
rate is now counted and logged via ``_report_default_fallback``; the mapped
values themselves are unchanged.
"""
from __future__ import annotations

import pandas as pd

from data.hts.entd import cleaned


def test_report_logs_fallback_rate_and_top_codes(capsys):
    raw = pd.Series(["1", "2", "Z9", "Z9", "X1"])
    matched = pd.Series([True, True, False, False, False])
    cleaned._report_default_fallback("mode (V2_MTP -> mode)", matched, raw, "pt")
    out = capsys.readouterr().out
    assert "primary 2/5 (40.0%)" in out
    assert "fallback to 'pt' 3 (60.0%)" in out
    assert "Z9" in out


def test_report_all_primary(capsys):
    raw = pd.Series(["1", "2"])
    matched = pd.Series([True, True])
    cleaned._report_default_fallback("purpose", matched, raw, "other")
    out = capsys.readouterr().out
    assert "primary 2/2 (100.0%)" in out
    assert "no default fallback" in out


def test_report_empty_is_silent(capsys):
    cleaned._report_default_fallback(
        "purpose", pd.Series([], dtype=bool), pd.Series([], dtype=str), "other")
    assert capsys.readouterr().out == ""


def test_purpose_and_mode_defaults_unchanged():
    # The explicit map entries still hold (values unchanged by the logging
    # refactor): code prefixes 3/4/6 are PRIMARY "other", not fallback.
    assert ("3", "other") in cleaned.PURPOSE_MAP
    assert ("9", "work") in cleaned.PURPOSE_MAP
    assert ("1", "walk") in cleaned.MODES_MAP
