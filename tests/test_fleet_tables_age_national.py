"""Tests for load_age_national in braunschweig/data/kba/fleet_tables.py.

Writes minimal kba_age_national.csv fixtures (with and without the # comment
header line) into a temporary derived directory, validates that the loader
returns 6 bands, rejects missing columns, and rejects unexpected band labels.
"""
import io

import pandas as pd
import pytest
from braunschweig.data.kba import fleet_tables as ft


_AGE_NATIONAL_BANDS_EXPECTED = [
    "under_2", "2_to_4", "5_to_9", "10_to_14", "15_to_29", "30_plus",
]


def _write_derived_raw(tmp_path, name, content: str):
    """Write raw CSV text (possibly with a # comment line) into the derived dir."""
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(content, encoding="utf-8")
    return str(tmp_path)


def _minimal_csv(with_header: bool = True) -> str:
    """Return minimal kba_age_national.csv content covering all 6 bands."""
    rows = [
        {"year": 2026, "stichtag": "2026-01-01", "band": b, "share_pct": v}
        for b, v in zip(_AGE_NATIONAL_BANDS_EXPECTED, [10.4, 13.3, 27.5, 21.0, 24.6, 3.1])
    ]
    csv_body = pd.DataFrame(rows).to_csv(index=False)
    if with_header:
        return "# mean_age_years=10.9 source=KBA/Statista ID3438 stichtag=2026-01-01\n" + csv_body
    return csv_body


def test_load_age_national_returns_six_bands(tmp_path):
    dp = _write_derived_raw(tmp_path, "kba_age_national.csv", _minimal_csv(with_header=True))
    df = ft.load_age_national(dp)
    assert set(df["band"]) == set(_AGE_NATIONAL_BANDS_EXPECTED)
    assert len(df) == 6


def test_load_age_national_tolerates_comment_header(tmp_path):
    """Loader must work whether or not the # comment line is present."""
    dp_with = _write_derived_raw(tmp_path / "with", "kba_age_national.csv", _minimal_csv(True))
    dp_without = _write_derived_raw(tmp_path / "without", "kba_age_national.csv", _minimal_csv(False))
    df_with = ft.load_age_national(dp_with)
    df_without = ft.load_age_national(dp_without)
    assert set(df_with["band"]) == set(df_without["band"])


def test_load_age_national_validates_columns(tmp_path):
    """RuntimeError when a required column is missing (schema drift)."""
    rows = [{"year": 2026, "stichtag": "2026-01-01", "band": b}
            for b in _AGE_NATIONAL_BANDS_EXPECTED]
    csv_body = pd.DataFrame(rows).to_csv(index=False)
    dp = _write_derived_raw(tmp_path, "kba_age_national.csv", csv_body)
    with pytest.raises(RuntimeError, match="share_pct"):
        ft.load_age_national(dp)


def test_load_age_national_rejects_unknown_band(tmp_path):
    """RuntimeError when an unexpected band label appears."""
    rows = [
        {"year": 2026, "stichtag": "2026-01-01", "band": "unknown_band", "share_pct": 100.0},
    ]
    csv_body = pd.DataFrame(rows).to_csv(index=False)
    dp = _write_derived_raw(tmp_path, "kba_age_national.csv", csv_body)
    with pytest.raises(RuntimeError, match="band"):
        ft.load_age_national(dp)


def test_load_age_national_file_not_found_raises(tmp_path):
    """FileNotFoundError (via RuntimeError in _read) when the CSV is absent."""
    dp = str(tmp_path)  # no kba_age_national.csv written
    with pytest.raises(FileNotFoundError):
        ft.load_age_national(dp)
