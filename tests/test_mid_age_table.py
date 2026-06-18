"""Tests for the MiD 2023 age-by-segment-status derived table and loader.

Schema:  columns segment, status, age_band, share, base_weighted
         share = P(age_band | segment, status), sums to 1.0 per (segment, status).
Gradient: P(age < 5yr) rises monotonically from very_low -> very_high.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATA_PATH = str(REPO / "eqasim-data" / "data")
sys.path.insert(0, str(REPO))

import braunschweig.data.kba.fleet_tables as ft  # noqa: E402


def test_age_table_schema_and_gradient():
    df = ft.load_mid_age_by_segment_status(DATA_PATH)

    # --- schema ---
    assert set(df.columns) >= {"segment", "status", "age_band", "share", "base_weighted"}

    # --- label validation ---
    assert set(df["age_band"]) <= set(ft.AGE_BAND_LABELS)
    assert set(df["status"]) <= set(ft.STATUS_LABELS)
    assert set(df["segment"]) <= set(ft.SEGMENT_LABELS)

    # --- share sums to 1.0 per (segment, status) ---
    totals = df.groupby(["segment", "status"])["share"].sum()
    assert (abs(totals - 1.0) < 1e-6).all(), \
        f"share does not sum to 1.0 per (segment,status): {totals[abs(totals - 1.0) >= 1e-6]}"

    # --- monotone gradient: P(<5yr) rises with status (pool over segments, base-weighted) ---
    piv = (
        df[df["age_band"] == "under_5"]
        .groupby("status")
        .apply(lambda g: (g["share"] * g["base_weighted"]).sum() / g["base_weighted"].sum())
    )
    assert piv["very_low"] < piv["very_high"], (
        f"P(under_5 | very_low)={piv['very_low']:.4f} should be < "
        f"P(under_5 | very_high)={piv['very_high']:.4f}"
    )
