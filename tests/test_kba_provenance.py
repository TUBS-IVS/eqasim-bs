"""Provenance tests: every new extractor in scripts/extract_kba_fleet.py that emits
a ``stichtag`` column is tested here against a tiny in-memory fixture.

One test per extractor; no real raw files required.  Each test builds a minimal
fixture (CSV string written to ``tmp_path``, or an openpyxl workbook, or a small
GeoDataFrame) and asserts:

1. The returned DataFrame has a ``stichtag`` column.
2. Every value in that column equals the expected ISO date string.

Expected stichtag values by extractor:

- ``extract_kreis_fuel_46251``   -> ``"2025-01-01"``  (Destatis 46251-02, Stichtag 01.01.2025)
- ``extract_kreis_euro_46251``   -> ``"2025-01-01"``  (Destatis 46251-03, Stichtag 01.01.2025)
- ``extract_fuel_euro6_substage_nds`` -> ``"2025-01-01"``  (FZ 27.4 Euro-6 substage, Stichtag 01.01.2025)
- ``extract_age_national``       -> ``"2026-01-01"``  (KBA/Statista ID3438, Stichtag 01.01.2026)
- ``extract_model_fuel``         -> ``"2026-01-01"``  (KBA Modellreihen, Stichtag 01.01.2026)
- ``extract_gemeinde_ev``        -> ``"2026-04-01"``  (KBA per-Gemeinde EV, April 2026)
- ``extract_ev_grid``            -> ``"2026-04-01"``  (KBA 5 km EV grid, April 2026)
- ``extract_ev_regiostar7``      -> latest-period-derived (``"2026-04-01"`` for a "2026.04" fixture)

Note: ``build_mid_antrieb_by_status.py`` (Task B1) deliberately emits NO ``stichtag``
column -- it is a MiD-2023 household-survey distribution, not a register snapshot,
mirroring its sibling ``mid2023_age_by_segment_status.csv`` -- so it is out of scope
for this file's "extractor that emits a stichtag" contract and is not tested here.

Fixture-construction patterns match those in the sibling test files
``test_extract_kba_46251.py``, ``test_extract_kba_age_national.py``,
``test_extract_kba_gemeinde_ev.py``, ``test_extract_kba_grid.py``, and
``test_extract_kba_modellreihen.py``.
"""
import math
import textwrap

import geopandas as gpd
import openpyxl
import pandas as pd
import pytest
from shapely.geometry import box

import scripts.extract_kba_fleet as ex

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# ---- 46251-02 fuel fixture (latin-1, semicolon-separated, 8-row header) ---
_FUEL_CSV = textwrap.dedent("""\
    Tabelle: 46251-02-01-4-B
    Personenkraftwagen nach Kraftstoffarten - Stichtag 01.01. -;;;;;;;;;;
    regionale Ebenen;;;;;;;;;;
    Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;
    ;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
    ;;;Art;Art;Art;Art;Art;Art;Art;Art
    ;;;Insgesamt;Benzin;Diesel;Gas;Hybrid;darunter PHEV;Elektro;sonstige
    ;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;143274;83528;40505;1078;10778;3089;7363;22
    """)

# ---- 46251-03 euro fixture (latin-1, semicolon-separated, 8-row header) ---
_EURO_CSV = textwrap.dedent("""\
    Tabelle: 46251-03-01-4-B
    Personenkraftwagen nach Emissionsgruppen - Stichtag 01.01. -;;;;;;;;;;;;;;
    regionale Ebenen;;;;;;;;;;;;;;
    Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;;;;;
    ;;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
    ;;;;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe
    ;;;;Insgesamt;Euro 1;Euro 2;Euro 3;Euro 4;Euro 5;Euro 6;darunter Euro-6d;darunter Euro-6d-temp;Sonstige
    ;;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;insgesamt;143274;234;1456;3210;22105;17432;98413;71203;8821;424
    """)

# ---- Gemeinde EV timeseries fixture (utf-8-sig, comma-separated) ----------
_GEMEINDE_EV_CSV = textwrap.dedent("""\
    AGS,Gemeinde,Berichtszeitpunkt,Pkw Elektro Anteil,Pkw_BEV_Anteil,Pkw Plug In Hybrid Anteil,Pkw Brennstoffzelle Anteil
    03101000,Braunschweig,2026.04,4.0,3.0,1.0,0.0
    03103000,Wolfsburg,2026.04,20.5,18.5,2.0,0.1
    """)

# ---- Modellreihen fixture (utf-8-sig, semicolon-separated) ----------------
_MODELLREIHEN_CSV = (
    "﻿"  # utf-8-sig BOM
    "Berichtszeitpunkt;Segment;Marke;Modellreihe;Anzahl;Diesel;Hybrid;Hybrid_Plugin;BEV;gewerblich\n"
    "01.01.2026;Minis;ALPHA;MINI;1000;100;200;80;50;120\n"
)


def _make_grid_gpkg(tmp_path):
    """Build a 2-cell 5 km EV-grid .gpkg with both cells inside the ZGB bbox."""
    earth_c = 20037508.342789244

    def _cell(lon, lat, size=5000.0):
        x = lon * earth_c / 180.0
        lat_rad = math.radians(lat)
        y = math.log(math.tan(math.pi / 4.0 + lat_rad / 2.0)) * earth_c / math.pi
        h = size / 2.0
        return box(x - h, y - h, x + h, y + h)

    gdf = gpd.GeoDataFrame(
        {
            "id_5km": ["5kmN2695E4340", "5kmN2700E4360"],
            "elektro_an": [5.2, 3.8],
            "ZS_Anteil_": ["ok", "ok"],
            "berichtsj": [2026, 2026],
        },
        geometry=[_cell(10.53, 52.27), _cell(10.79, 52.43)],
        crs="EPSG:3857",
    )
    path = tmp_path / "kba_ev_grid_5km_2026.gpkg"
    gdf.to_file(str(path), driver="GPKG")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_kreis_fuel_46251_stichtag(tmp_path):
    """extract_kreis_fuel_46251: stichtag column must equal '2025-01-01'."""
    raw = tmp_path / "fuel.csv"
    raw.write_text(_FUEL_CSV, encoding="latin-1")
    df = ex.extract_kreis_fuel_46251(raw)
    assert "stichtag" in df.columns, "stichtag column missing from kreis_fuel output"
    assert (df["stichtag"] == "2025-01-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )


def test_extract_kreis_euro_46251_stichtag(tmp_path):
    """extract_kreis_euro_46251: stichtag column must equal '2025-01-01'."""
    raw = tmp_path / "euro.csv"
    raw.write_text(_EURO_CSV, encoding="latin-1")
    df = ex.extract_kreis_euro_46251(raw)
    assert "stichtag" in df.columns, "stichtag column missing from kreis_euro output"
    assert (df["stichtag"] == "2025-01-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )


def test_extract_fuel_euro6_substage_nds_stichtag_and_shares(tmp_path, monkeypatch):
    """extract_fuel_euro6_substage_nds (Task B4): stichtag == '2025-01-01' and
    per-fuel substage shares (P(substage | euro6, fuel)) sum to 1.

    FZ 27.4 sheet layout (see extract_fuel_euro_nds / extract_fuel_euro6_substage_nds
    docstrings): col1=Land (filled once per block), col2=fuel, col3..7=Euro1..5,
    col8=Euro6 total, col9=darunter Euro-6d-temp, col10=darunter Euro-6d,
    col11=darunter Euro-6e (folded into euro6d -- Euro-6e is the newest
    sub-class and has no separate downstream bucket), col12=Sonstige,
    col13=row total.
    """
    xlsx = tmp_path / "fz27_euro6_substage.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "FZ 27.4"
    # Benzin (petrol): euro6=1000, 6d-temp=200, 6d=500, 6e=100
    #   -> euro6d (folded) = 600, euro6ab = max(1000 - 200 - 600, 0) = 200
    ws.append([None, "Niedersachsen", "Benzin", 10, 20, 30, 40, 50, 1000, 200, 500, 100, 5, 1855])
    # Diesel: euro6=2000, 6d-temp=300, 6d=700, 6e absent (no separate 6e reported)
    #   -> euro6d (folded) = 700, euro6ab = max(2000 - 300 - 700, 0) = 1000
    ws.append([None, None, "Diesel", 15, 25, 35, 45, 55, 2000, 300, 700, None, 8, 2483])
    ws.append([None, "Niedersachsen zusammen", None])
    wb.save(xlsx)
    monkeypatch.setattr(ex, "FZ27_PATH", xlsx)

    df = ex.extract_fuel_euro6_substage_nds()

    assert "stichtag" in df.columns, "stichtag column missing from euro6_substage output"
    assert (df["stichtag"] == "2025-01-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )
    assert set(df["substage"]) == {"euro6ab", "euro6dtemp", "euro6d"}

    petrol = df[df["fuel"] == "petrol"].set_index("substage")["count"]
    assert petrol["euro6dtemp"] == 200
    assert petrol["euro6d"] == 600  # 500 (6d) + 100 (6e, folded in)
    assert petrol["euro6ab"] == 200  # 1000 - 200 - 600

    diesel = df[df["fuel"] == "diesel"].set_index("substage")["count"]
    assert diesel["euro6dtemp"] == 300
    assert diesel["euro6d"] == 700  # no 6e reported -> unchanged
    assert diesel["euro6ab"] == 1000  # 2000 - 300 - 700

    # P(substage | euro6, fuel) sums to 1 within each fuel.
    per_fuel = df.groupby("fuel")["share"].sum()
    for fuel, total in per_fuel.items():
        assert total == pytest.approx(1.0, abs=1e-6), f"fuel {fuel} shares sum to {total}"


def test_extract_age_national_stichtag(tmp_path):
    """extract_age_national: stichtag column must equal '2026-01-01' for year=2026."""
    xlsx = tmp_path / "age.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Daten"
    for _ in range(4):
        ws.append([None])
    ws.append([None, None, "unter 2 Jahre", "2 bis 4 Jahre", "5 bis 9 Jahre",
               "10 bis 14 Jahre", "15 bis 29 Jahre", "30 und mehr Jahre", None])
    ws.append([None, 2026, 10.4, 13.3, 27.5, 21.0, 24.6, 3.1, "in %"])
    wb.save(xlsx)
    df = ex.extract_age_national(xlsx, year=2026)
    assert "stichtag" in df.columns, "stichtag column missing from age_national output"
    assert (df["stichtag"] == "2026-01-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )


def test_extract_model_fuel_stichtag(tmp_path):
    """extract_model_fuel: stichtag column must equal '2026-01-01'."""
    csv_path = tmp_path / "kba_modellreihen_bestand_2020_2026.csv"
    csv_path.write_text(_MODELLREIHEN_CSV, encoding="utf-8-sig")
    df = ex.extract_model_fuel(str(csv_path))
    assert "stichtag" in df.columns, "stichtag column missing from model_fuel output"
    assert (df["stichtag"] == "2026-01-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )


def test_extract_gemeinde_ev_stichtag(tmp_path):
    """extract_gemeinde_ev: stichtag column must equal '2026-04-01' for 2026.04 data."""
    csv_path = tmp_path / "kba_ev_gemeinde_timeseries_2023_2026.csv"
    csv_path.write_text(_GEMEINDE_EV_CSV, encoding="utf-8-sig")
    df = ex.extract_gemeinde_ev(csv_path)
    assert "stichtag" in df.columns, "stichtag column missing from gemeinde_ev output"
    assert (df["stichtag"] == "2026-04-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )


def test_extract_ev_grid_stichtag(tmp_path):
    """extract_ev_grid: stichtag column must equal '2026-04-01'."""
    gpkg_path = _make_grid_gpkg(tmp_path)
    df = ex.extract_ev_grid(gpkg_path)
    assert "stichtag" in df.columns, "stichtag column missing from ev_grid output"
    assert (df["stichtag"] == "2026-04-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )


def test_extract_ev_regiostar7_stichtag(tmp_path):
    """extract_ev_regiostar7 (Task B6): stichtag is derived from the LATEST
    reporting period ('2026.04' -> '2026-04-01'); only the latest period is
    kept and the residual RegioStaR7 code 99 ('keine Zuordnung') is dropped.
    """
    csv_path = tmp_path / "kba_ev_regiostar7_timeseries_2023_2026.csv"
    # utf-8-sig, comma-separated (matches the raw file + extract_gemeinde_ev
    # fixture style): field separator is a comma, so the "Pkw Elektro Anteil"
    # decimal uses a dot -- the extractor's defensive str.replace(",", ".") is a
    # no-op here. Includes an older period, the latest period, and a code-99 row
    # ("keine Zuordnung") that the extractor must drop.
    csv_path.write_text(
        "Berichtszeitpunkt,Regiostar7 Nummer,Pkw Elektro Anteil\n"
        "2025.04,71,3.0\n"   # older period -> dropped by latest-period filter
        "2026.04,71,4.5\n"
        "2026.04,77,2.1\n"
        "2026.04,99,9.9\n",  # 'keine Zuordnung' -> dropped
        encoding="utf-8-sig",
    )
    df = ex.extract_ev_regiostar7(csv_path)
    assert "stichtag" in df.columns, "stichtag column missing from ev_regiostar7 output"
    assert (df["stichtag"] == "2026-04-01").all(), (
        f"Unexpected stichtag values: {df['stichtag'].unique().tolist()}"
    )
    # Only the latest period is kept and code 99 is dropped -> codes {71, 77}.
    assert sorted(df["rs7"].tolist()) == [71, 77], (
        f"Unexpected rs7 codes: {df['rs7'].tolist()}"
    )
    # Percent -> fraction: '4.5' -> 0.045.
    rs71 = df.loc[df["rs7"] == 71, "ev_share"].iloc[0]
    assert rs71 == pytest.approx(0.045, abs=1e-9), f"rs7=71 ev_share={rs71}"
