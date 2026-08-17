"""Tests for load_ev_grid() in braunschweig/data/kba/fleet_tables.py.

Writes minimal kba_ev_grid.csv fixtures into a tmp derived directory and
validates column requirements, correct loading, and schema-drift errors.
"""
import pandas as pd
import pytest
from braunschweig.data.kba import fleet_tables as ft


def _write_derived(tmp_path, name, df):
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def _minimal_row(cell_id="5kmN2695E4340", ev_share=0.04, suppressed=False):
    return {
        "cell_id": cell_id,
        "stichtag": "2026-04-01",
        "ev_share": ev_share,
        "minx": 1170000.0,
        "miny": 6830000.0,
        "maxx": 1175000.0,
        "maxy": 6835000.0,
        "suppressed": suppressed,
    }


def test_load_ev_grid_happy_path(tmp_path):
    """A valid CSV with all required columns must load without error."""
    rows = [_minimal_row("5kmN2695E4340"), _minimal_row("5kmN2700E4360", suppressed=True)]
    dp = _write_derived(tmp_path, "kba_ev_grid.csv", pd.DataFrame(rows))
    df = ft.load_ev_grid(dp)
    assert len(df) == 2
    assert set(df["cell_id"]) == {"5kmN2695E4340", "5kmN2700E4360"}


def test_load_ev_grid_raises_on_missing_column(tmp_path):
    """A CSV with a missing required column must raise RuntimeError."""
    rows = [_minimal_row()]
    df = pd.DataFrame(rows).drop(columns=["minx"])
    dp = _write_derived(tmp_path, "kba_ev_grid.csv", df)
    with pytest.raises(RuntimeError):
        ft.load_ev_grid(dp)


def test_load_ev_grid_file_not_found(tmp_path):
    """When kba_ev_grid.csv is absent, load_ev_grid must raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        ft.load_ev_grid(str(tmp_path))


def test_load_ev_grid_ev_share_nan_preserved(tmp_path):
    """NaN ev_share must survive the round-trip (suppressed cells have NaN)."""
    import math
    rows = [_minimal_row("5kmN2695E4340", ev_share=float("nan"), suppressed=True)]
    dp = _write_derived(tmp_path, "kba_ev_grid.csv", pd.DataFrame(rows))
    df = ft.load_ev_grid(dp)
    assert math.isnan(df.iloc[0]["ev_share"]), "NaN ev_share must be preserved"


def test_load_ev_grid_required_columns_all_present(tmp_path):
    """All 8 required columns must be present in the returned DataFrame."""
    rows = [_minimal_row()]
    dp = _write_derived(tmp_path, "kba_ev_grid.csv", pd.DataFrame(rows))
    df = ft.load_ev_grid(dp)
    required = {"cell_id", "stichtag", "ev_share", "minx", "miny", "maxx", "maxy", "suppressed"}
    assert required.issubset(set(df.columns))
