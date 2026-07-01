"""Tests for load_kreis_fuel and load_kreis_euro in fleet_tables.py.

Uses tmp_path to write minimal kba_kreis_fuel.csv / kba_kreis_euro.csv with all
8 ZGB Kreise into a temporary derived directory, validates that the loaders
return them and raise RuntimeError when a Kreis is missing.
"""
import pandas as pd
import pytest
from braunschweig.data.kba import fleet_tables as ft


def _write_derived(tmp_path, name, df):
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def test_load_kreis_fuel_requires_all_zgb(tmp_path):
    rows = [{"kreis_ags5": k, "kreis_name": k, "stichtag": "2025-01-01",
             "petrol": 1, "diesel": 1, "gas": 0, "bev": 0, "phev": 0,
             "hybrid": 0, "other": 0, "total": 2, "petrol_share": .5,
             "diesel_share": .5, "gas_share": 0, "bev_share": 0,
             "phev_share": 0, "hybrid_share": 0, "other_share": 0}
            for k in ft.ZGB_KREISE_AGS5]
    dp = _write_derived(tmp_path, "kba_kreis_fuel.csv", pd.DataFrame(rows))
    out = ft.load_kreis_fuel(dp)
    assert set(out["kreis_ags5"]) == set(ft.ZGB_KREISE_AGS5)
    dp2 = _write_derived(tmp_path, "kba_kreis_fuel.csv", pd.DataFrame(rows[:-1]))
    with pytest.raises(RuntimeError):
        ft.load_kreis_fuel(dp2)


def test_load_kreis_euro_requires_all_zgb_in_all_teil(tmp_path):
    euro_cols = ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other", "total"]
    euro_shares = [f"{c}_share" for c in ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"]]
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel"):
            row = {"kreis_ags5": k, "kreis_name": k, "stichtag": "2025-01-01", "teil": teil}
            for c in euro_cols:
                row[c] = 1
            for c in euro_shares:
                row[c] = 0.0
            rows.append(row)
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    out = ft.load_kreis_euro(dp)
    assert set(out.loc[out["teil"] == "all", "kreis_ags5"]) == set(ft.ZGB_KREISE_AGS5)
    # Drop one Kreis from the 'all' rows -> should raise
    rows_missing = [r for r in rows if not (r["kreis_ags5"] == ft.ZGB_KREISE_AGS5[-1] and r["teil"] == "all")]
    dp2 = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows_missing))
    with pytest.raises(RuntimeError):
        ft.load_kreis_euro(dp2)


def test_load_kreis_euro_rejects_unexpected_teil(tmp_path):
    euro_cols = ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other", "total"]
    euro_shares = [f"{c}_share" for c in ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"]]
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel", "petrol"):  # 'petrol' is unexpected
            row = {"kreis_ags5": k, "kreis_name": k, "stichtag": "2025-01-01", "teil": teil}
            for c in euro_cols:
                row[c] = 1
            for c in euro_shares:
                row[c] = 0.0
            rows.append(row)
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    with pytest.raises(RuntimeError):
        ft.load_kreis_euro(dp)
