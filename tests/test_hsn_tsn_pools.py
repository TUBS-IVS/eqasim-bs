"""Tests for exact/model tier variant-pool drawing (Task 4).

Verifies that the exact and model match tiers draw per-vehicle engine attributes
from a variant pool rather than returning the identical deterministic median
record for every vehicle. Within-group engine variance is thereby restored while
the pool median is preserved as the central value.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

H = pytest.importorskip("braunschweig.data.kba.hsn_tsn")


def _frame():
    # One brand+family+fuel group with 3 distinct power values -> median 100 kW.
    return pd.DataFrame({
        "brand": ["VW"] * 3,
        "hsn": ["0603"] * 3,
        "tsn": ["A", "B", "C"],
        "model": ["VW GOLF"] * 3,
        "power_ps": [110, 150, 190],
        "power_kw": [80, 100, 120],
        "displacement_ccm": [1400, 1600, 1800],
        "fuel": ["petrol"] * 3,
    })


def test_exact_model_tiers_are_pooled_not_median():
    lk = H.HsnTsnLookup.from_frame(_frame())
    assert "exact" in H._POOLED_TIERS and "model" in H._POOLED_TIERS
    pool = lk.get_model_pool("VW", "golf", "petrol")
    assert pool is not None and len(pool.power_kw) == 3
    # Pool median preserved as the old deterministic value.
    assert float(np.median(pool.power_kw)) == 100.0


def test_pool_draw_varies(monkeypatch):
    lk = H.HsnTsnLookup.from_frame(_frame())
    rng = np.random.default_rng(0)
    vals = {H._draw_record(lk.get_model_pool("VW", "golf", "petrol"), rng).power_kw
            for _ in range(50)}
    assert len(vals) > 1   # not a single clone
