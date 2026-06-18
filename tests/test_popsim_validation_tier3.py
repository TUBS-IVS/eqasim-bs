"""Tests for popsim_validation/controls.py — Tier-3 employment definition."""
import pandas as pd
import pytest


def test_employed_25_64_uses_erwerb_definition():
    from braunschweig.analysis.popsim_validation import controls as vc
    persons = pd.DataFrame({
        "RegionalSchlussel_ARS": ["03102000000"] * 3,
        "HP_ALTER": [30, 30, 40],
        "P_TAET": [8, 5, 1],
    })
    # 8 Azubi=employed, 5 Elternzeit=not_employed, 1=employed → 2/3 employed
    result = vc.employed_25_64_rate(persons)
    assert round(result["03102"], 3) == round(2 / 3, 3)
