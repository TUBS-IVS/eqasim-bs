"""Tests for extract_gemeinde_ev in scripts/extract_kba_fleet.py.

Uses a tmp_path fixture with an inline CSV string mimicking the KBA per-Gemeinde
EV timeseries export (utf-8-sig encoding).  No real KBA raw file is required.

Fixture design:
- Two periods ("2025.10" and "2026.04") to prove only the latest is kept.
- A non-ZGB AGS row ("04012" = Bremen suburb) to prove the ZGB filter drops it.
- A German decimal comma in one share column to prove comma -> dot + /100.
- Both a Braunschweig and a Wolfsburg row.
"""
import io
import textwrap

import pandas as pd
import pytest

import scripts.extract_kba_fleet as ex
from braunschweig.synthesis.vehicles.fleet_sampling_de import normalize_gemeinde


# Inline fixture CSV (utf-8-sig encoded in the tmp_path write below).
# Columns: AGS, Gemeinde, Berichtszeitpunkt, Pkw Elektro Anteil,
#          Pkw_BEV_Anteil, Pkw Plug In Hybrid Anteil, Pkw Brennstoffzelle Anteil
_CSV_FIXTURE = textwrap.dedent("""\
    AGS,Gemeinde,Berichtszeitpunkt,Pkw Elektro Anteil,Pkw_BEV_Anteil,Pkw Plug In Hybrid Anteil,Pkw Brennstoffzelle Anteil
    03101000,Braunschweig,2025.10,3.2,2.5,0.7,0.0
    03103000,Wolfsburg,2025.10,20.1,18.3,1.8,0.1
    04012000,Bremerhaven,2025.10,5.0,4.0,1.0,0.0
    03101000,Braunschweig,2026.04,4,1,3,0.0
    03103000,Wolfsburg,2026.04,"21,5",19.0,2.5,0.1
    04012000,Bremerhaven,2026.04,6.0,5.0,1.0,0.0
""")


@pytest.fixture()
def kba_gemeinde_ev_csv(tmp_path):
    """Write the fixture CSV as utf-8-sig to a tmp_path file."""
    p = tmp_path / "kba_ev_gemeinde_timeseries_2023_2026.csv"
    p.write_text(_CSV_FIXTURE, encoding="utf-8-sig")
    return p


def test_only_latest_period_kept(kba_gemeinde_ev_csv):
    """Rows for older periods must be dropped; only 2026.04 (stichtag=2026-04-01) survives."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    assert set(df["stichtag"]) == {"2026-04-01"}


def test_non_zgb_rows_filtered_out(kba_gemeinde_ev_csv):
    """Rows whose AGS5 prefix is not in ZGB_KREISE must be dropped."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    # Bremerhaven (04012) is not a ZGB Kreis -> must not appear
    assert "04012" not in set(df["ags8"].str[:5])
    assert all(k in ex.ZGB_KREISE for k in df["kreis_ags5"])


def test_shares_are_fractions(kba_gemeinde_ev_csv):
    """Share values must be divided by 100 (fractions, not percentages)."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    bs = df.set_index("ags8").loc["03101000"]
    # 4% -> 0.04, 1% -> 0.01, 3% -> 0.03
    assert abs(bs["ev_share"] - 0.04) < 1e-9
    assert abs(bs["bev_share"] - 0.01) < 1e-9
    assert abs(bs["phev_share"] - 0.03) < 1e-9
    assert abs(bs["fuelcell_share"] - 0.0) < 1e-9


def test_german_decimal_comma_converted(kba_gemeinde_ev_csv):
    """A German decimal comma in a share cell (e.g. '21,5') must parse correctly."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    wob = df.set_index("ags8").loc["03103000"]
    # "21,5" -> 21.5 / 100 = 0.215
    assert abs(wob["ev_share"] - 0.215) < 1e-9


def test_phev_and_fuelcell_columns_present(kba_gemeinde_ev_csv):
    """Output must contain phev_share and fuelcell_share columns."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    for col in ("phev_share", "fuelcell_share"):
        assert col in df.columns, f"Column {col!r} missing from output"


def test_required_columns_present(kba_gemeinde_ev_csv):
    """All nine output columns must be present."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    expected = {
        "kreis_ags5", "ags8", "gemeinde", "gemeinde_norm",
        "stichtag", "ev_share", "bev_share", "phev_share", "fuelcell_share",
    }
    assert expected.issubset(set(df.columns))


def test_gemeinde_norm_matches_normalize_gemeinde(kba_gemeinde_ev_csv):
    """gemeinde_norm must equal normalize_gemeinde(gemeinde) for every row."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    for _, row in df.iterrows():
        expected_norm = normalize_gemeinde(row["gemeinde"])
        assert row["gemeinde_norm"] == expected_norm, (
            f"gemeinde_norm mismatch for {row['gemeinde']!r}: "
            f"got {row['gemeinde_norm']!r}, expected {expected_norm!r}"
        )


def test_kreis_ags5_is_ags8_prefix(kba_gemeinde_ev_csv):
    """kreis_ags5 must equal the first 5 characters of ags8 for every row."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    for _, row in df.iterrows():
        assert row["kreis_ags5"] == row["ags8"][:5]


def test_sorted_output(kba_gemeinde_ev_csv):
    """Output must be sorted by (kreis_ags5, gemeinde)."""
    df = ex.extract_gemeinde_ev(kba_gemeinde_ev_csv)
    expected = df.sort_values(["kreis_ags5", "gemeinde"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(df, expected)
