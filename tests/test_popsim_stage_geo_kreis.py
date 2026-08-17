"""Tests for stage.derive_geo_kreis_from_ars (employment-grid GEO_KREIS derivation).

The employment grid control derives the 5-digit Kreis ARS from the 12-digit cell
ARS column. An ARS that lost a leading zero (e.g. round-tripped through an
integer column, as often happens with parquet/CSV round-trips of numeric-looking
strings) must still zfill to 12 digits BEFORE slicing the first five characters,
mirroring mid.filter_zgb_cells / assembly.derive_zone_ids.
"""
from __future__ import annotations

import pandas as pd

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
