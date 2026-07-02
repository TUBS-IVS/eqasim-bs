"""Tests for extract_kreis_fuel_46251 and extract_kreis_euro_46251.

Uses tmp_path fixtures with inline Destatis-shaped CSV strings (latin-1,
8 header rows before data) -- no real KBA raw files required.
"""
import textwrap
import pandas as pd
import scripts.extract_kba_fleet as ex

FUEL_FIXTURE = textwrap.dedent("""\
    Tabelle: 46251-02-01-4-B
    Personenkraftwagen nach Kraftstoffarten - Stichtag 01.01. -;;;;;;;;;;
    regionale Ebenen;;;;;;;;;;
    Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;
    ;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
    ;;;Art;Art;Art;Art;Art;Art;Art;Art
    ;;;Insgesamt;Benzin;Diesel;Gas;Hybrid;darunter PHEV;Elektro;sonstige
    ;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;143274;83528;40505;1078;10778;3089;7363;22
    01.01.2025;03152;Goettingen alt;-;-;-;-;-;-;-;-
    """)


def test_kreis_fuel_maps_powertrains_and_skips_dissolved(tmp_path):
    raw = tmp_path / "fuel.csv"
    raw.write_text(FUEL_FIXTURE, encoding="latin-1")
    df = ex.extract_kreis_fuel_46251(raw)
    row = df.set_index("kreis_ags5").loc["03101"]
    assert row["petrol"] == 83528
    assert row["bev"] == 7363
    assert row["phev"] == 3089
    assert row["hybrid"] == 10778 - 3089   # Hybrid minus darunter PHEV
    assert row["other"] == 22
    assert row["stichtag"] == "2025-01-01"
    assert "03152" not in set(df["kreis_ags5"])  # dissolved Kreis ('-') dropped


# Adds a non-ZGB Kreis (Region Hannover, 03241) alongside the ZGB row and the
# dissolved row, so the "keep every Kreis" behaviour (Task B3) can be tested
# independently of the ZGB-only fixture above.
FUEL_FIXTURE_WITH_NON_ZGB = textwrap.dedent("""\
    Tabelle: 46251-02-01-4-B
    Personenkraftwagen nach Kraftstoffarten - Stichtag 01.01. -;;;;;;;;;;
    regionale Ebenen;;;;;;;;;;
    Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;
    ;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
    ;;;Art;Art;Art;Art;Art;Art;Art;Art
    ;;;Insgesamt;Benzin;Diesel;Gas;Hybrid;darunter PHEV;Elektro;sonstige
    ;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;143274;83528;40505;1078;10778;3089;7363;22
    01.01.2025;03152;Goettingen alt;-;-;-;-;-;-;-;-
    01.01.2025;03241;Region Hannover;500000;300000;150000;5000;30000;10000;20000;100
    """)


def test_kreis_fuel_keeps_non_zgb_kreis_with_file_name(tmp_path):
    """Task B3: a non-ZGB Kreis with valid counts is kept, not filtered out.

    Its ``kreis_name`` comes from the file's own name column (there is no
    ZGB_KREISE entry for it), while the ZGB Kreis keeps the canonical
    ZGB_KREISE name for backward-compatible provenance.
    """
    raw = tmp_path / "fuel_all_kreise.csv"
    raw.write_text(FUEL_FIXTURE_WITH_NON_ZGB, encoding="latin-1")
    df = ex.extract_kreis_fuel_46251(raw)
    codes = set(df["kreis_ags5"])
    assert "03241" in codes, "non-ZGB Kreis (Region Hannover) must be kept"
    assert "03152" not in codes, "dissolved Kreis ('-') must still be dropped"

    non_zgb_row = df.set_index("kreis_ags5").loc["03241"]
    assert non_zgb_row["kreis_name"] == "Region Hannover", (
        "non-ZGB kreis_name must come from the file's own name column"
    )
    assert non_zgb_row["petrol"] == 300000
    assert non_zgb_row["bev"] == 20000

    zgb_row = df.set_index("kreis_ags5").loc["03101"]
    assert zgb_row["kreis_name"] == ex.ZGB_KREISE["03101"], (
        "ZGB kreis_name must keep the canonical ZGB_KREISE label"
    )


EURO_FIXTURE = textwrap.dedent("""\
    Tabelle: 46251-03-01-4-B
    Personenkraftwagen nach Emissionsgruppen - Stichtag 01.01. -;;;;;;;;;;;;;;
    regionale Ebenen;;;;;;;;;;;;;;
    Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;;;;;
    ;;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
    ;;;;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe
    ;;;;Insgesamt;Euro 1;Euro 2;Euro 3;Euro 4;Euro 5;Euro 6;darunter Euro-6d;darunter Euro-6d-temp;Sonstige
    ;;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;insgesamt;143274;234;1456;3210;22105;17432;98413;71203;8821;424
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;Dieselangetriebener Pkw;40505;98;543;1234;8765;6543;23098;18211;2210;224
    """)


def test_kreis_euro_two_rows_per_kreis_and_skips_subsets(tmp_path):
    raw = tmp_path / "euro.csv"
    raw.write_text(EURO_FIXTURE, encoding="latin-1")
    df = ex.extract_kreis_euro_46251(raw)
    assert set(df["teil"]) == {"all", "diesel"}
    all_row = df[(df["kreis_ags5"] == "03101") & (df["teil"] == "all")].iloc[0]
    assert all_row["euro6"] == 98413
    # darunter Euro-6d (71203) and Euro-6d-temp (8821) must NOT appear as columns
    # under their RAW column names (they now emerge as euro6d/euro6dtemp instead,
    # see test_kreis_euro_emits_euro6_substage_counts below -- Task B4).
    assert "e6d" not in df.columns
    assert "e6dtemp" not in df.columns
    diesel_row = df[(df["kreis_ags5"] == "03101") & (df["teil"] == "diesel")].iloc[0]
    assert diesel_row["euro1"] == 98


# ---------------------------------------------------------------------------
# Task B4: Euro-6 substage counts (euro6d / euro6dtemp / euro6ab) emerge as
# ADDITIVE count columns; the headline euro6 total and every other existing
# column stay UNCHANGED.
# ---------------------------------------------------------------------------
def test_kreis_euro_emits_euro6_substage_counts(tmp_path):
    """The 'darunter' columns (previously discarded) now emerge as euro6d /
    euro6dtemp counts, plus the derived residual euro6ab = euro6 - 6d - 6dtemp.
    The headline euro6 value is UNCHANGED (still the Destatis Euro-6 total)."""
    raw = tmp_path / "euro.csv"
    raw.write_text(EURO_FIXTURE, encoding="latin-1")
    df = ex.extract_kreis_euro_46251(raw)

    all_row = df[(df["kreis_ags5"] == "03101") & (df["teil"] == "all")].iloc[0]
    assert all_row["euro6"] == 98413  # headline UNCHANGED
    assert all_row["euro6d"] == 71203
    assert all_row["euro6dtemp"] == 8821
    assert all_row["euro6ab"] == 98413 - 71203 - 8821

    diesel_row = df[(df["kreis_ags5"] == "03101") & (df["teil"] == "diesel")].iloc[0]
    assert diesel_row["euro6"] == 23098  # headline UNCHANGED
    assert diesel_row["euro6d"] == 18211
    assert diesel_row["euro6dtemp"] == 2210
    assert diesel_row["euro6ab"] == 23098 - 18211 - 2210


def test_kreis_euro_euro6ab_clamped_at_zero(tmp_path):
    """euro6ab must never go negative, even if 6d + 6d-temp would exceed euro6
    (defensive clamp; not expected in real Destatis data, but must not crash
    or emit a negative count)."""
    fixture = textwrap.dedent("""\
        Tabelle: 46251-03-01-4-B
        Personenkraftwagen nach Emissionsgruppen - Stichtag 01.01. -;;;;;;;;;;;;;;
        regionale Ebenen;;;;;;;;;;;;;;
        Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;;;;;
        ;;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
        ;;;;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe
        ;;;;Insgesamt;Euro 1;Euro 2;Euro 3;Euro 4;Euro 5;Euro 6;darunter Euro-6d;darunter Euro-6d-temp;Sonstige
        ;;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
        01.01.2025;03101;Braunschweig, kreisfreie Stadt;insgesamt;1000;10;10;10;10;10;100;80;30;10
        """)
    raw = tmp_path / "euro_edge.csv"
    raw.write_text(fixture, encoding="latin-1")
    df = ex.extract_kreis_euro_46251(raw)
    row = df.iloc[0]
    assert row["euro6"] == 100  # headline UNCHANGED, not clamped
    assert row["euro6d"] == 80
    assert row["euro6dtemp"] == 30
    assert row["euro6ab"] == 0  # 100 - 80 - 30 = -10 -> clamped to 0


# Adds a non-ZGB Kreis (Region Hannover, 03241, both teil rows) to the ZGB
# fixture above, so the "keep every Kreis" behaviour (Task B3) can be tested.
EURO_FIXTURE_WITH_NON_ZGB = textwrap.dedent("""\
    Tabelle: 46251-03-01-4-B
    Personenkraftwagen nach Emissionsgruppen - Stichtag 01.01. -;;;;;;;;;;;;;;
    regionale Ebenen;;;;;;;;;;;;;;
    Statistik des Kraftfahrzeug- und Anhaengerbestandes;;;;;;;;;;;;;;
    ;;;;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw;Pkw
    ;;;;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe;Emissionsgruppe
    ;;;;Insgesamt;Euro 1;Euro 2;Euro 3;Euro 4;Euro 5;Euro 6;darunter Euro-6d;darunter Euro-6d-temp;Sonstige
    ;;;;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl;Anzahl
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;insgesamt;143274;234;1456;3210;22105;17432;98413;71203;8821;424
    01.01.2025;03101;Braunschweig, kreisfreie Stadt;Dieselangetriebener Pkw;40505;98;543;1234;8765;6543;23098;18211;2210;224
    01.01.2025;03241;Region Hannover;insgesamt;500000;1000;2000;5000;30000;50000;400000;300000;20000;12000
    01.01.2025;03241;Region Hannover;Dieselangetriebener Pkw;150000;300;600;1500;9000;15000;120000;90000;6000;3600
    01.01.2025;03152;Goettingen alt;insgesamt;-;-;-;-;-;-;-;-;-;-
    01.01.2025;03152;Goettingen alt;Dieselangetriebener Pkw;-;-;-;-;-;-;-;-;-;-
    """)


def test_kreis_euro_keeps_non_zgb_kreis_with_file_name(tmp_path):
    """Task B3: a non-ZGB Kreis is kept with the file's own name column; a
    dissolved non-ZGB Kreis ('-' counts) is still dropped."""
    raw = tmp_path / "euro_all_kreise.csv"
    raw.write_text(EURO_FIXTURE_WITH_NON_ZGB, encoding="latin-1")
    df = ex.extract_kreis_euro_46251(raw)
    codes = set(df["kreis_ags5"])
    assert "03241" in codes, "non-ZGB Kreis (Region Hannover) must be kept"
    assert set(df.loc[df["kreis_ags5"] == "03241", "teil"]) == {"all", "diesel"}
    assert "03152" not in codes, "dissolved non-ZGB Kreis ('-') must still be dropped"

    non_zgb_all = df[(df["kreis_ags5"] == "03241") & (df["teil"] == "all")].iloc[0]
    assert non_zgb_all["kreis_name"] == "Region Hannover", (
        "non-ZGB kreis_name must come from the file's own name column"
    )
    assert non_zgb_all["euro6"] == 400000

    zgb_row = df[(df["kreis_ags5"] == "03101") & (df["teil"] == "all")].iloc[0]
    assert zgb_row["kreis_name"] == ex.ZGB_KREISE["03101"], (
        "ZGB kreis_name must keep the canonical ZGB_KREISE label"
    )


def test_kreis_euro_non_zgb_substage_counts(tmp_path):
    """Task B3+B4: euro6d/euro6dtemp/euro6ab are also emitted for a non-ZGB
    Kreis row, since the 46251-03 file (and this derivation) covers every
    German Kreis, not only the 8 ZGB ones."""
    raw = tmp_path / "euro_all_kreise.csv"
    raw.write_text(EURO_FIXTURE_WITH_NON_ZGB, encoding="latin-1")
    df = ex.extract_kreis_euro_46251(raw)

    non_zgb_all = df[(df["kreis_ags5"] == "03241") & (df["teil"] == "all")].iloc[0]
    assert non_zgb_all["euro6"] == 400000
    assert non_zgb_all["euro6d"] == 300000
    assert non_zgb_all["euro6dtemp"] == 20000
    assert non_zgb_all["euro6ab"] == 400000 - 300000 - 20000

    non_zgb_diesel = df[(df["kreis_ags5"] == "03241") & (df["teil"] == "diesel")].iloc[0]
    assert non_zgb_diesel["euro6"] == 120000
    assert non_zgb_diesel["euro6d"] == 90000
    assert non_zgb_diesel["euro6dtemp"] == 6000
    assert non_zgb_diesel["euro6ab"] == 120000 - 90000 - 6000
