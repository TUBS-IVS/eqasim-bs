"""Tests for the KBA / MiD fleet table readers (``braunschweig.data.kba``).

Covers, for every derived-CSV loader:
  * the loader returns a non-empty, schema-validated frame;
  * the canonical label sets (segment / powertrain / status / euro class) match
    the sets the extraction script (``scripts/extract_kba_fleet.py``) produced;
  * the per-Kreis tables carry all 8 ZGB Kreise as ``"03" + Kreis3`` AGS-5 codes;
  * schema drift (a missing/renamed column, or an unexpected label) raises a
    clear ``RuntimeError`` rather than silently producing a malformed frame.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402

DATA_PATH = str(DATA)

# The 8 ZGB Kreise as AGS-5 ("03" + Kreis3).
ZGB_KREISE_AGS5 = {
    "03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158",
}


# ---------------------------------------------------------------------------
# Per-loader: returns a validated, non-empty frame with the expected schema
# ---------------------------------------------------------------------------
def test_load_segment_powertrain():
    df = ft.load_segment_powertrain(DATA_PATH)
    assert not df.empty
    assert {"segment", "total", "bev", "phev", "hybrid", "gas", "hydrogen",
            "segment_share"} <= set(df.columns)
    # Every segment label is canonical.
    assert set(df["segment"]) <= set(ft.SEGMENT_LABELS)
    # The KBA segment marginal is a valid pmf.
    assert df["segment_share"].sum() == pytest.approx(1.0, abs=1e-6)


def test_load_kreis_powertrain_has_8_zgb_kreise():
    df = ft.load_kreis_powertrain(DATA_PATH)
    assert not df.empty
    assert {"kreis_ags5", "total", "bev", "phev", "bev_share"} <= set(df.columns)
    codes = set(df["kreis_ags5"])
    assert codes == ZGB_KREISE_AGS5
    # Codes are strings of the form "03" + 3 digits.
    assert all(c.startswith("03") and len(c) == 5 and c.isdigit() for c in codes)


def test_load_gemeinde_private_bev_kreis_codes():
    df = ft.load_gemeinde_private_bev(DATA_PATH)
    assert not df.empty
    assert {"kreis_ags5", "gemeinde", "private_total", "private_bev",
            "private_phev"} <= set(df.columns)
    # Every Gemeinde row maps to one of the 8 ZGB Kreise.
    assert set(df["kreis_ags5"]) <= ZGB_KREISE_AGS5
    assert set(df["kreis_ags5"]) == ZGB_KREISE_AGS5


def test_load_fuel_euro_nds():
    df = ft.load_fuel_euro_nds(DATA_PATH)
    assert not df.empty
    assert {"fuel", "euro_class", "count", "share"} <= set(df.columns)
    assert set(df["fuel"]) <= set(ft.POWERTRAIN_LABELS)
    assert set(df["euro_class"]) <= set(ft.EURO_CLASS_LABELS)


def test_load_age_fuel():
    df = ft.load_age_fuel(DATA_PATH)
    assert not df.empty
    assert {"age_band", "fuel", "pkw_count", "share"} <= set(df.columns)
    assert set(df["fuel"]) <= set(ft.POWERTRAIN_LABELS)
    assert set(df["age_band"]) <= set(ft.AGE_BAND_LABELS)


def test_load_brand_powertrain():
    df = ft.load_brand_powertrain(DATA_PATH)
    assert not df.empty
    assert {"brand", "total", "bev", "phev", "hybrid", "gas",
            "brand_share"} <= set(df.columns)
    assert df["brand_share"].sum() == pytest.approx(1.0, abs=1e-6)


def test_load_segment_model():
    df = ft.load_segment_model(DATA_PATH)
    assert not df.empty
    assert {"segment", "model", "count", "share"} <= set(df.columns)
    assert set(df["segment"]) <= set(ft.SEGMENT_LABELS)


def test_load_mid_segment_by_status_bundesland():
    df = ft.load_mid_segment_by_status_bundesland(DATA_PATH)
    assert not df.empty
    assert {"region", "segment", "status", "share_pct",
            "base_weighted"} <= set(df.columns)
    assert set(df["segment"]) <= set(ft.SEGMENT_LABELS)
    assert set(df["status"]) == set(ft.STATUS_LABELS)
    # Niedersachsen is the base region and must be present.
    assert ft.BUNDESLAND_NIEDERSACHSEN in set(df["region"])


def test_load_mid_segment_by_status_raumtyp():
    df = ft.load_mid_segment_by_status_raumtyp(DATA_PATH)
    assert not df.empty
    assert {"region", "segment", "status", "share_pct",
            "base_weighted"} <= set(df.columns)
    assert set(df["status"]) == set(ft.STATUS_LABELS)
    # All 7 RegioStaR-7 raumtyp regions are present.
    assert set(df["region"]) == set(ft.RS7_TO_RAUMTYP_REGION.values())


# ---------------------------------------------------------------------------
# Schema-drift detection: validation raises a clear error
# ---------------------------------------------------------------------------
def test_missing_column_raises(tmp_path):
    # A CSV missing a required column must raise (not silently load).
    df = ft.load_segment_powertrain(DATA_PATH).drop(columns=["segment_share"])
    bad = tmp_path / "kba_segment_powertrain.csv"
    (tmp_path / "braunschweig" / "kba" / "derived").mkdir(parents=True)
    target = tmp_path / "braunschweig" / "kba" / "derived" / "kba_segment_powertrain.csv"
    df.to_csv(target, index=False)
    with pytest.raises(RuntimeError, match="missing columns"):
        ft.load_segment_powertrain(str(tmp_path))


def test_unexpected_segment_label_raises(tmp_path):
    df = ft.load_segment_powertrain(DATA_PATH)
    df.loc[df.index[0], "segment"] = "not_a_real_segment"
    (tmp_path / "braunschweig" / "kba" / "derived").mkdir(parents=True)
    target = tmp_path / "braunschweig" / "kba" / "derived" / "kba_segment_powertrain.csv"
    df.to_csv(target, index=False)
    with pytest.raises(RuntimeError, match="unexpected segment"):
        ft.load_segment_powertrain(str(tmp_path))


def test_kreis_table_missing_zgb_kreis_raises(tmp_path):
    df = ft.load_kreis_powertrain(DATA_PATH)
    df = df[df["kreis_ags5"] != "03101"]  # drop Braunschweig
    (tmp_path / "braunschweig" / "kba" / "derived").mkdir(parents=True)
    target = tmp_path / "braunschweig" / "kba" / "derived" / "kba_kreis_powertrain.csv"
    df.to_csv(target, index=False)
    with pytest.raises(RuntimeError, match="ZGB Kreise"):
        ft.load_kreis_powertrain(str(tmp_path))
