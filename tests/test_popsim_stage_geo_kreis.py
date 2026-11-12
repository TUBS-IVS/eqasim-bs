"""Tests for stage.derive_geo_kreis_from_ars (employment-grid GEO_KREIS derivation).

The employment grid control derives the 5-digit Kreis ARS from the 12-digit cell
ARS column. An ARS that lost a leading zero (e.g. round-tripped through an
integer column, as often happens with parquet/CSV round-trips of numeric-looking
strings) must still zfill to 12 digits BEFORE slicing the first five characters,
mirroring mid.filter_zgb_cells / assembly.derive_zone_ids.
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import stage


def test_derive_geo_kreis_from_well_formed_12_digit_ars():
    ars = pd.Series(["031010000000", "031020000000"])
    out = stage.derive_geo_kreis_from_ars(ars)
    assert list(out) == ["03101", "03102"]


def test_derive_geo_kreis_from_ars_missing_leading_zero():
    # 11 digits (leading zero lost, e.g. via an int64 round-trip); zfill(12) restores
    # it BEFORE slicing, so the Kreis is still correctly "03101", not "03101" sliced
    # from the wrong offset ("31010000000"[:5] == "31010", the WRONG Kreis).
    ars = pd.Series(["31010000000"])
    out = stage.derive_geo_kreis_from_ars(ars)
    assert list(out) == ["03101"]


def test_derive_geo_kreis_from_ars_integer_dtype_input():
    ars = pd.Series([31010000000, 31020000000])
    out = stage.derive_geo_kreis_from_ars(ars)
    assert list(out) == ["03101", "03102"]


@pytest.mark.parametrize("ars, expected_prefix, expect_warning", [
    (pd.Series(["031010000000", "031020000000"] * 20), "[popsim.stage.geo_kreis] primary 40/40", False),
    # A numeric ARS column with a missing value is float64: the valid row stringifies as
    # "31010000000.0" (a WRONG Kreis) and the NaN as "nan"; both are counted, and any count warns.
    (pd.Series([31010000000, float("nan")]), "[popsim.stage.geo_kreis] primary 0/2", True),
])
def test_derive_geo_kreis_logs_placeholder_rate(caplog, ars, expected_prefix, expect_warning):
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.stage.cell_attributes"):
        out = stage.derive_geo_kreis_from_ars(ars)
    assert len(out) == len(ars)
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith(expected_prefix) for m in messages)
    assert bool([r for r in caplog.records if r.levelno >= logging.WARNING]) is expect_warning
