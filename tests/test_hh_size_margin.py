"""Tests for the Zensus 2022 1000A-2081 household-type loader helpers
(``braunschweig.data.census.households_type``).

The cache-level invariants that used to live here (household-type loader and
IPF ``hh_size`` margin, read from synpp pickles of the legacy local fixture run)
were removed on 2026-09-25: no checkout generates that cache any more, so they
had skipped on every machine since the popsim workflow became production.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ZGB_KREISE = ["03101", "03102", "03103", "03151",
              "03153", "03154", "03157", "03158"]
HH_SIZE_BINS = {"1", "2", "3", "4", "5", "6+"}


# ---------------------------------------------------------------------------
# Loader: braunschweig.data.census.households_type (Zensus 1000A-2081)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# IPF: braunschweig.ipf.model with hh_size margin
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pure-function unit tests (independent of caches)
# ---------------------------------------------------------------------------

class TestParseValueEFlag:
    """Guards the SafeMosaic ``e``-flag handling regression."""

    @staticmethod
    def _call(values, qs):
        from braunschweig.data.census.households_size_age import _parse_value
        return _parse_value(pd.Series(values), pd.Series(qs)).tolist()

    def test_e_flag_keeps_value(self):
        assert self._call(["123"], ["e"]) == [123.0]

    def test_dash_returns_zero(self):
        assert self._call(["-"], [""]) == [0.0]

    def test_dot_returns_zero(self):
        assert self._call(["."], [""]) == [0.0]

    def test_plain_numeric_keeps_value(self):
        assert self._call(["42"], [""]) == [42.0]

    def test_mixed_batch(self):
        assert self._call(
            ["100", "-", ".", "55"], ["", "", "", "e"]
        ) == [100.0, 0.0, 0.0, 55.0]
