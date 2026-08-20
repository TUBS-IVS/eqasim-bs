"""Tests for the wohnmobile holder-age tilt (issue #315, ADR-0093).

Covers: (a) the schema-validated loader; (b) the Bayes ratio r(a) and the
E_pop[r] = 1 identity; (c) the covariance case -- plain Bayes drifts the
wohnmobile mass, the calibration scalar restores it exactly; (d) composition
invariance to c; (e) pmf integrity + fallback counting; (f) sampler wiring
(flag OFF never consults the tilt; absent CSV with flag ON raises; missing
owner_age column is a loud 100% fallback); (g) the acceptance bands on the
committed tables.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402

DATA_PATH = str(DATA)


def _reference_df(vehicles=None) -> pd.DataFrame:
    """Synthetic reference table with the canonical 8 + residual rows."""
    base = {
        "up_to_20": 700, "21_29": 16000, "30_39": 79000, "40_49": 132000,
        "50_59": 265000, "60_69": 318000, "70_79": 124000, "80_plus": 27000,
    }
    if vehicles:
        base.update(vehicles)
    rows = []
    for label, count in base.items():
        rows.append({"age_class": label, "vehicles": count})
    rows.append({"age_class": ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED, "vehicles": 39000})
    df = pd.DataFrame(rows)
    bounds = {"up_to_20": (np.nan, 20), "21_29": (21, 29), "30_39": (30, 39),
              "40_49": (40, 49), "50_59": (50, 59), "60_69": (60, 69),
              "70_79": (70, 79), "80_plus": (80, np.nan),
              ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED: (np.nan, np.nan)}
    df["age_min_years"] = [bounds[a][0] for a in df["age_class"]]
    df["age_max_years"] = [bounds[a][1] for a in df["age_class"]]
    df["published_share_pct"] = np.nan
    att = df["age_class"] != ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED
    att_total = df.loc[att, "vehicles"].sum()
    df["share_of_attributed"] = np.where(att, df["vehicles"] / att_total, np.nan)
    df["total_stock"] = int(df["vehicles"].sum())
    df["stichtag"] = "2025-04-01"
    return df


def _write_reference_csv(tmp_path: Path, df: pd.DataFrame) -> str:
    derived = tmp_path / "braunschweig" / "kba" / "derived"
    derived.mkdir(parents=True)
    (derived / "kba_wohnmobile_holder_age.csv").write_text(
        df.to_csv(index=False), encoding="utf-8")
    return str(tmp_path)


def test_loader_accepts_valid_table(tmp_path):
    data_path = _write_reference_csv(tmp_path, _reference_df())
    df = ft.load_wohnmobile_holder_age(data_path)
    att = df[df["age_class"] != ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED]
    assert set(att["age_class"]) == set(ft.WOHNMOBILE_AGE_CLASS_LABELS)
    assert float(att["share_of_attributed"].sum()) == pytest.approx(1.0)


def test_loader_rejects_missing_age_class(tmp_path):
    df = _reference_df()
    df = df[df["age_class"] != "60_69"]
    data_path = _write_reference_csv(tmp_path, df)
    with pytest.raises(RuntimeError, match="60_69"):
        ft.load_wohnmobile_holder_age(data_path)


def test_loader_rejects_share_drift(tmp_path):
    df = _reference_df()
    df.loc[df["age_class"] == "60_69", "share_of_attributed"] += 0.05
    data_path = _write_reference_csv(tmp_path, df)
    with pytest.raises(RuntimeError, match="share_of_attributed"):
        ft.load_wohnmobile_holder_age(data_path)


def test_loader_rejects_count_total_mismatch(tmp_path):
    df = _reference_df()
    df.loc[df["age_class"] == "50_59", "vehicles"] += 1  # breaks the sum check
    data_path = _write_reference_csv(tmp_path, df)
    with pytest.raises(RuntimeError, match="total_stock"):
        ft.load_wohnmobile_holder_age(data_path)
