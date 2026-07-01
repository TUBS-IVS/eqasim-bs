"""Tests for load_model_fuel() in braunschweig/data/kba/fleet_tables.py.

Uses tmp_path fixtures to write minimal kba_model_fuel.csv into the expected
derived directory and validates that the loader returns the DataFrame correctly
and raises RuntimeError on schema or label violations.
"""
import pandas as pd
import pytest
from braunschweig.data.kba import fleet_tables as ft


def _write_derived(tmp_path, name, df):
    """Write df as CSV into tmp_path/braunschweig/kba/derived/; return data_path str."""
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def _minimal_model_fuel_rows():
    """Minimal valid kba_model_fuel.csv rows for two models."""
    return [
        {
            "segment": "minis",
            "model": "ALPHA MINI",
            "stichtag": "2026-01-01",
            "petrol_share": 0.65,
            "diesel_share": 0.10,
            "hybrid_share": 0.12,
            "phev_share": 0.08,
            "bev_share": 0.05,
        },
        {
            "segment": "kleinwagen",
            "model": "BETA CITY",
            "stichtag": "2026-01-01",
            "petrol_share": 0.0,
            "diesel_share": 0.0,
            "hybrid_share": 0.0,
            "phev_share": 0.0,
            "bev_share": 1.0,
        },
    ]


def test_load_model_fuel_returns_dataframe(tmp_path):
    """load_model_fuel returns a DataFrame with the expected columns."""
    rows = _minimal_model_fuel_rows()
    dp = _write_derived(tmp_path, "kba_model_fuel.csv", pd.DataFrame(rows))
    df = ft.load_model_fuel(dp)
    assert isinstance(df, pd.DataFrame)
    required = {"segment", "model", "stichtag", "petrol_share", "diesel_share",
                "hybrid_share", "phev_share", "bev_share"}
    assert required.issubset(set(df.columns))


def test_load_model_fuel_row_count(tmp_path):
    """load_model_fuel returns all rows from the CSV."""
    rows = _minimal_model_fuel_rows()
    dp = _write_derived(tmp_path, "kba_model_fuel.csv", pd.DataFrame(rows))
    df = ft.load_model_fuel(dp)
    assert len(df) == 2


def test_load_model_fuel_missing_column_raises(tmp_path):
    """RuntimeError is raised when a required column is absent."""
    rows = _minimal_model_fuel_rows()
    df_raw = pd.DataFrame(rows).drop(columns=["bev_share"])
    dp = _write_derived(tmp_path, "kba_model_fuel.csv", df_raw)
    with pytest.raises(RuntimeError, match="bev_share"):
        ft.load_model_fuel(dp)


def test_load_model_fuel_invalid_segment_raises(tmp_path):
    """RuntimeError is raised when a segment label is not in SEGMENT_LABELS."""
    rows = _minimal_model_fuel_rows()
    rows[0]["segment"] = "unbekannt_segment"
    dp = _write_derived(tmp_path, "kba_model_fuel.csv", pd.DataFrame(rows))
    with pytest.raises(RuntimeError, match="segment"):
        ft.load_model_fuel(dp)


def test_load_model_fuel_all_valid_segment_labels(tmp_path):
    """All rows with valid SEGMENT_LABELS values load without error."""
    rows = [
        {
            "segment": seg,
            "model": f"BRAND {seg.upper()}",
            "stichtag": "2026-01-01",
            "petrol_share": 0.5,
            "diesel_share": 0.3,
            "hybrid_share": 0.1,
            "phev_share": 0.05,
            "bev_share": 0.05,
        }
        for seg in ft.SEGMENT_LABELS
    ]
    dp = _write_derived(tmp_path, "kba_model_fuel.csv", pd.DataFrame(rows))
    df = ft.load_model_fuel(dp)
    assert set(df["segment"]) == set(ft.SEGMENT_LABELS)


def test_load_model_fuel_file_not_found(tmp_path):
    """FileNotFoundError is raised when the CSV does not exist."""
    dp = str(tmp_path)
    with pytest.raises(FileNotFoundError):
        ft.load_model_fuel(dp)
