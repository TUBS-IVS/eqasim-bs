"""Tests for load_gemeinde_ev in braunschweig/data/kba/fleet_tables.py.

Writes minimal kba_gemeinde_ev.csv fixtures into a tmp derived directory to
validate column requirements and ZGB-membership enforcement.
"""
import pandas as pd
import pytest
from braunschweig.data.kba import fleet_tables as ft


def _write_derived(tmp_path, name, df):
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def _minimal_row(kreis_ags5):
    return {
        "kreis_ags5": kreis_ags5,
        "ags8": kreis_ags5 + "000",
        "gemeinde": "Testgemeinde",
        "gemeinde_norm": "TESTGEMEINDE",
        "stichtag": "2026-04-01",
        "ev_share": 0.05,
        "bev_share": 0.04,
        "phev_share": 0.01,
        "fuelcell_share": 0.0,
    }


def test_load_gemeinde_ev_all_zgb_kreise_accepted(tmp_path):
    """A file with at least one row per ZGB Kreis must load without error."""
    rows = [_minimal_row(k) for k in ft.ZGB_KREISE_AGS5]
    dp = _write_derived(tmp_path, "kba_gemeinde_ev.csv", pd.DataFrame(rows))
    df = ft.load_gemeinde_ev(dp)
    assert set(df["kreis_ags5"]) == set(ft.ZGB_KREISE_AGS5)


def test_load_gemeinde_ev_raises_on_non_zgb_kreis(tmp_path):
    """A file that contains a non-ZGB Kreis code must raise RuntimeError."""
    rows = [_minimal_row(k) for k in ft.ZGB_KREISE_AGS5]
    rows.append(_minimal_row("04012"))  # not a ZGB Kreis
    dp = _write_derived(tmp_path, "kba_gemeinde_ev.csv", pd.DataFrame(rows))
    with pytest.raises(RuntimeError):
        ft.load_gemeinde_ev(dp)


def test_load_gemeinde_ev_raises_on_missing_column(tmp_path):
    """A file with a missing required column must raise RuntimeError."""
    rows = [_minimal_row(k) for k in ft.ZGB_KREISE_AGS5]
    df = pd.DataFrame(rows).drop(columns=["phev_share"])
    dp = _write_derived(tmp_path, "kba_gemeinde_ev.csv", df)
    with pytest.raises(RuntimeError):
        ft.load_gemeinde_ev(dp)


def test_load_gemeinde_ev_file_not_found(tmp_path):
    """When the CSV is absent, load_gemeinde_ev must raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        ft.load_gemeinde_ev(str(tmp_path))


def test_load_gemeinde_ev_returns_correct_dtypes(tmp_path):
    """kreis_ags5 must be read as string (leading zero preserved)."""
    rows = [_minimal_row(k) for k in ft.ZGB_KREISE_AGS5]
    dp = _write_derived(tmp_path, "kba_gemeinde_ev.csv", pd.DataFrame(rows))
    df = ft.load_gemeinde_ev(dp)
    # AGS codes start with "03"; if read as int they lose the leading zero
    assert all(str(v).startswith("03") for v in df["kreis_ags5"])
