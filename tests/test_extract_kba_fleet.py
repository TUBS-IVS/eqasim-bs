"""Tests for ``scripts/extract_kba_fleet.py`` and its committed derived CSVs.

These tests read the *real* committed derived CSVs under
``eqasim-data/data/braunschweig/kba/derived/`` and assert their schema,
dtypes, non-emptiness, canonical labels, ZGB coverage and share
normalisation.  The raw xlsx are local-only; tests that need to re-run the
extraction (coercion-count logging) are guarded with skip-if-missing on the
raw inputs.  The pure-helper tests (symbol coercion, label maps) run
unconditionally.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import extract_kba_fleet as ekf

DERIVED = Path("eqasim-data/data/braunschweig/kba/derived")

RAW_INPUTS_PRESENT = all(
    path.exists() for path in (
        ekf.FZ27_PATH, ekf.FZ12_PATH,
        ekf.MID_BUNDESLAND_PATH, ekf.MID_RAUMTYP_PATH,
    )
)


def _read(name: str) -> pd.DataFrame:
    path = DERIVED / name
    assert path.exists(), f"derived CSV missing (run scripts/extract_kba_fleet.py): {path}"
    frame = pd.read_csv(path, dtype={"kreis_ags5": str})
    assert len(frame) > 0, f"derived CSV is empty: {path}"
    return frame


# --------------------------------------------------------------------------- #
# Symbol coercion (pure helper, no xlsx needed)
# --------------------------------------------------------------------------- #
def test_coerce_count_maps_placeholders_explicitly():
    counter = ekf.CoercionCounter("unit")
    assert ekf._coerce_count("-", counter) == 0.0
    assert ekf._coerce_count("0", counter) == 0.0
    assert np.isnan(ekf._coerce_count(".", counter))
    assert np.isnan(ekf._coerce_count("/", counter))
    assert np.isnan(ekf._coerce_count("()", counter))
    assert np.isnan(ekf._coerce_count("", counter))
    assert np.isnan(ekf._coerce_count(None, counter))
    assert ekf._coerce_count(1234, counter) == 1234.0
    assert ekf._coerce_count("1.234", counter) == 1234.0  # German thousands sep
    # Two "-> 0" ("-", "0") and five "-> NaN" (".", "/", "()", "", None).
    assert counter.to_zero == 2
    assert counter.to_nan == 5


def test_coerce_count_genuine_zero_is_not_a_placeholder():
    counter = ekf.CoercionCounter("unit")
    assert ekf._coerce_count(0, counter) == 0.0
    assert counter.to_zero == 0  # a real numeric 0 must not be counted as coerced


def test_coerce_percent_parses_mid_percent_cells():
    counter = ekf.CoercionCounter("unit")
    assert ekf._coerce_percent("7 %", counter) == 7.0
    assert ekf._coerce_percent("100 %", counter) == 100.0
    assert np.isnan(ekf._coerce_percent("-", counter))


def test_coercion_counter_logs_count(caplog):
    counter = ekf.CoercionCounter("file_x")
    ekf._coerce_count("-", counter)
    ekf._coerce_count(".", counter)
    with caplog.at_level(logging.INFO, logger="extract_kba_fleet"):
        counter.log()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "file_x" in messages
    assert "coerced placeholders=2" in messages


# --------------------------------------------------------------------------- #
# kba_segment_powertrain.csv (FZ 27.10)
# --------------------------------------------------------------------------- #
def test_segment_powertrain_schema_and_canonical_labels():
    frame = _read("kba_segment_powertrain.csv")
    expected = {"segment", "total", "bev", "phev", "hybrid", "gas", "hydrogen",
                "alt_total", "segment_share", "bev_share"}
    assert expected.issubset(frame.columns)
    assert set(frame["segment"]).issubset(set(ekf.SEGMENT_LABELS))
    assert len(frame) == len(ekf.SEGMENT_LABELS)
    for column in ("total", "bev", "phev", "hybrid", "gas", "segment_share"):
        assert pd.api.types.is_numeric_dtype(frame[column])
    assert frame["total"].gt(0).all()


def test_segment_powertrain_segment_share_sums_to_one():
    frame = _read("kba_segment_powertrain.csv")
    assert frame["segment_share"].sum() == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# kba_kreis_powertrain.csv (FZ 27.15)
# --------------------------------------------------------------------------- #
def test_kreis_powertrain_covers_all_zgb_kreise():
    frame = _read("kba_kreis_powertrain.csv")
    expected = {"kreis_ags5", "total", "bev", "phev", "hybrid", "gas",
                "alt_total", "bev_share"}
    assert expected.issubset(frame.columns)
    assert set(frame["kreis_ags5"]) == set(ekf.ZGB_KREISE)
    assert frame["kreis_ags5"].str.startswith("03").all()  # "03" + Kreis3
    assert frame["total"].gt(0).all()
    # BEV share is a plausible registration share (0..30 %).
    assert frame["bev_share"].between(0, 0.30).all()


# --------------------------------------------------------------------------- #
# kba_gemeinde_private_bev.csv (FZ 27.17)
# --------------------------------------------------------------------------- #
def test_gemeinde_private_bev_covers_zgb_kreise():
    frame = _read("kba_gemeinde_private_bev.csv")
    expected = {"kreis_ags5", "gemeinde", "private_total", "private_bev",
                "private_phev"}
    assert expected.issubset(frame.columns)
    assert set(frame["kreis_ags5"]) == set(ekf.ZGB_KREISE)
    assert frame["private_total"].gt(0).all()
    # Private BEV must not exceed the private total (NaN = KBA-suppressed cell
    # "." coerced to NaN; those are legitimately absent and skipped here).
    observed = frame.dropna(subset=["private_bev"])
    assert (observed["private_bev"] <= observed["private_total"]).all()
    # The suppression rate must stay low (no-silent-fallback sanity bound).
    assert frame["private_bev"].isna().mean() < 0.2


# --------------------------------------------------------------------------- #
# kba_fuel_euro_nds.csv (FZ 27.4)
# --------------------------------------------------------------------------- #
def test_fuel_euro_nds_schema_and_shares():
    frame = _read("kba_fuel_euro_nds.csv")
    assert {"fuel", "euro_class", "count", "share"}.issubset(frame.columns)
    assert set(frame["fuel"]).issubset(set(ekf.POWERTRAIN_LABELS))
    # Per-fuel Euro shares sum to ~1 (for fuels with a positive total).
    per_fuel = frame.groupby("fuel")["share"].sum()
    for fuel, total in per_fuel.items():
        assert total == pytest.approx(1.0, abs=1e-6), f"fuel {fuel} shares sum to {total}"


# --------------------------------------------------------------------------- #
# kba_age_fuel.csv (FZ 27.7)
# --------------------------------------------------------------------------- #
def test_age_fuel_schema_and_shares():
    frame = _read("kba_age_fuel.csv")
    assert {"age_band", "fuel", "pkw_count", "share"}.issubset(frame.columns)
    assert set(frame["fuel"]).issubset(set(ekf.POWERTRAIN_LABELS))
    per_fuel = frame.groupby("fuel")["share"].sum()
    for fuel, total in per_fuel.items():
        assert total == pytest.approx(1.0, abs=1e-6), f"fuel {fuel} shares sum to {total}"


# --------------------------------------------------------------------------- #
# kba_brand_powertrain.csv (FZ 27.11)
# --------------------------------------------------------------------------- #
def test_brand_powertrain_schema():
    frame = _read("kba_brand_powertrain.csv")
    assert {"brand", "total", "bev", "phev", "hybrid", "gas", "brand_share"}.issubset(frame.columns)
    assert frame["total"].gt(0).all()
    assert frame["brand_share"].sum() == pytest.approx(1.0, abs=1e-6)
    # A few well-known German brands must be present.
    brands = set(frame["brand"].str.upper())
    assert {"VW", "AUDI", "BMW", "MERCEDES"}.issubset(brands)


# --------------------------------------------------------------------------- #
# kba_segment_model.csv (FZ 12.1)
# --------------------------------------------------------------------------- #
def test_segment_model_schema_and_within_segment_shares():
    frame = _read("kba_segment_model.csv")
    assert {"segment", "model", "count", "share"}.issubset(frame.columns)
    assert set(frame["segment"]).issubset(set(ekf.SEGMENT_LABELS))
    per_segment = frame.groupby("segment")["share"].sum()
    for segment, total in per_segment.items():
        assert total == pytest.approx(1.0, abs=1e-6), f"segment {segment} shares sum to {total}"


# --------------------------------------------------------------------------- #
# MiD segment x status (Bundesland / Raumtyp)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "mid2023_segment_by_status_bundesland.csv",
    "mid2023_segment_by_status_raumtyp.csv",
])
def test_mid_segment_by_status_schema_and_canonical_labels(name):
    frame = _read(name)
    assert {"region", "segment", "status", "share_pct", "base_weighted"}.issubset(frame.columns)
    assert set(frame["segment"]).issubset(set(ekf.SEGMENT_LABELS))
    assert set(frame["status"]).issubset(set(ekf.STATUS_LABELS))
    # Each (region, segment) block is a column-% over the 5 status classes and
    # sums to ~100 % (ignoring rounding / the rare suppressed cell).
    grouped = frame.groupby(["region", "segment"])["share_pct"].sum()
    valid = grouped.dropna()
    assert (valid.between(95, 105)).mean() > 0.9, "most status blocks should sum to ~100 %"


def test_mid_bundesland_includes_niedersachsen():
    frame = _read("mid2023_segment_by_status_bundesland.csv")
    assert "Niedersachsen" in set(frame["region"])
    # 16 Bundeslaender x 13 MiD segments x 5 status classes.
    assert frame["region"].nunique() == 16
    assert frame["status"].nunique() == 5


def test_mid_raumtyp_has_seven_raumtypen():
    frame = _read("mid2023_segment_by_status_raumtyp.csv")
    assert frame["region"].nunique() == 7


# --------------------------------------------------------------------------- #
# Re-extraction logs the coercion count (primary-path / no-silent-fallback)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not RAW_INPUTS_PRESENT,
                    reason="raw KBA/MiD xlsx are local-only; not present here")
def test_segment_powertrain_extraction_logs_coercion_count(caplog):
    with caplog.at_level(logging.INFO, logger="extract_kba_fleet"):
        frame = ekf.extract_segment_powertrain()
    assert len(frame) == len(ekf.SEGMENT_LABELS)
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "coerced placeholders=" in messages
    assert "numeric cells=" in messages


@pytest.mark.skipif(not RAW_INPUTS_PRESENT,
                    reason="raw KBA/MiD xlsx are local-only; not present here")
def test_extraction_is_idempotent_for_kreis_powertrain():
    # Re-running the parser on the unchanged xlsx reproduces the committed CSV.
    fresh = ekf.extract_kreis_powertrain()
    committed = pd.read_csv(DERIVED / "kba_kreis_powertrain.csv", dtype={"kreis_ags5": str})
    fresh_sorted = fresh.sort_values("kreis_ags5").reset_index(drop=True)
    committed_sorted = committed.sort_values("kreis_ags5").reset_index(drop=True)
    pd.testing.assert_series_equal(
        fresh_sorted["total"].astype(float),
        committed_sorted["total"].astype(float),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        fresh_sorted["bev"].astype(float),
        committed_sorted["bev"].astype(float),
        check_names=False,
    )
