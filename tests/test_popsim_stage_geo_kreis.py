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


def test_derive_geo_kreis_logs_full_primary_rate_on_valid_ars(caplog):
    ars = pd.Series(["031010000000", "031020000000"] * 20)
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.stage.cell_attributes"):
        stage.derive_geo_kreis_from_ars(ars)
    lines = [r for r in caplog.records if r.getMessage().startswith("[popsim.stage.geo_kreis] primary 40/40")]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO


@pytest.mark.parametrize("ars", [
    # A missing ARS used to become the string "nan" and the placeholder Kreis "00000" (audit P3-F5).
    pd.Series(["031010000000", None], dtype=object),
    # A numeric ARS column with a missing value is float64: the valid row stringified as
    # "31010000000.0" and derived the WRONG Kreis "31010" instead of "03101".
    pd.Series([31010000000, float("nan")]),
    # Not a Regionalschluessel at all.
    pd.Series(["03101000000X"]),
])
def test_derive_geo_kreis_rejects_missing_or_malformed_ars(ars):
    with pytest.raises(ValueError, match="ARS"):
        stage.derive_geo_kreis_from_ars(ars)


def test_join_cell_attributes_rejects_a_household_without_cell_ars():
    """Every PopulationSim cell comes from the loaded cells frame, so a household whose cell
    has no ARS after the join is a broken input, not a fallback (audit P3-F5)."""
    combined = pd.DataFrame({"ZENSUS100m": ["a", "b"], "H_ID": [1, 2]})
    cells = pd.DataFrame({"ZENSUS100m": ["a"], stage.mid._ARS_COLUMN: ["031010000000"]})
    with pytest.raises(ValueError, match="ARS"):
        stage.join_cell_attributes(combined, cells)
