"""Task B1 of parking cost zones v3 (issue #436, spec section 3 B1): the corrections of the committed tariff table.

Every assertion pins one changed cell of ``parking_tariffs_2026.csv`` and names the source id of the pinned 2026-10-14
packages that carries it (``Parkdaten_Nachrecherche_2026-10-14.zip``: ``sz_current``, ``pe_current``, ``wf_2026``,
``he_brochure``, ``seesen_reg``, ``seesen_parking``, ``bh_places``, ``gs_2025_gazette``, ``bs_parkgo_2025``;
``Parkdaten_Restluecken_2026-10-14.zip``: ``bh_brochure_calendar``; ``Grossparkplaetze_ZGB8_2026-10-12.zip``: ``db_hbf``)
or the ASSUMPTION id of the spec (M3, W-max, E2). Fee windows and maximum stays are separate columns. The unchanged rows
are pinned against the table of the base commit, so that a row nobody meant to touch cannot move.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.parking import cost
from braunschweig.parking import zones as pz
from braunschweig.parking.tariff_export import tariff_row_to_zone

REPO_ROOT = Path(__file__).resolve().parents[1]
PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
TARIFFS_PATH = PARKING_DIR / "parking_tariffs_2026.csv"
REGISTER_PATH = PARKING_DIR / "parking_coverage_register_2026.csv"
#: The table before Task B1 (Part A of v3 merged with the v2 tip); the unchanged rows must equal it.
BASE_COMMIT = "a9b640f2"
TABLE_RELATIVE = "eqasim-data/data/braunschweig/parking/parking_tariffs_2026.csv"

BAD_HARZBURG = ("bh_sole_therme", "bh_kurpark", "bh_grossparkplatz", "bh_burgberg", "bh_berliner_platz")
GOSLAR = ("gs_altstadt_zone1", "gs_parkplatz_baeringerstrasse", "gs_parkplatz_klubgartenstrasse_zob",
          "gs_parkplatz_glockengiesserstrasse")
STATIONS = ("bs_hbf_p1_nord", "bs_hbf_p2_sued", "bs_hbf_p3_west")
WOLFSBURG = ("wob_tarifzone_1", "wob_tarifzone_2", "wob_tarifzone_3")
HOUR_S = 3600


@pytest.fixture(scope="module")
def tariffs() -> pd.DataFrame:
    return pz.load_tariffs(TARIFFS_PATH).set_index("zone_id")


def _base_table() -> pd.DataFrame:
    result = subprocess.run(["git", "show", f"{BASE_COMMIT}:{TABLE_RELATIVE}"], cwd=REPO_ROOT, capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"the base commit {BASE_COMMIT} is not available in this clone")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "parking_tariffs_base.csv"
        path.write_bytes(result.stdout)
        return pz.load_tariffs(path).set_index("zone_id")


def _price(zone: pd.Series, zone_id: str, start_h: float, minutes: int, purpose: str = "shop") -> int:
    start_s = int(start_h * HOUR_S)
    row = {**zone.to_dict(), "zone_id": zone_id}
    return cost.parking_cost_cents(tariff_row_to_zone(row), start_s, start_s + minutes * 60, purpose=purpose,
                                   parking_free=False, resident_of_zone=False)[0]


# ------------------------------------------------------------------------------------------------ the unchanged rows
#: Zone -> the columns (other than ``notes``) whose value differs from the base table, with the new value. A changed
#: row may change nothing else, and every other row must equal the base row in every schema-2 column.
EXPECTED_CHANGES = {
    "bs_zone_ia": {"long_stay_product_eur": pd.NA},                                           # bs_parkgo_2025, M3
    "pe_innenstadt": {"daily_cap_eur": pd.NA, "max_stay_min": pd.NA, "long_stay_product_eur": pd.NA,
                      "source_url": None, "source_date": "2026-10-14", "valid_from": "2023-04-01",
                      "ev_free_minutes": 180, "ev_rule_valid_until": "2028-04-30"},         # pe_current
    "wf_innenstadt": {"hourly_rate_eur": 1.0, "billing_unit_min": 3, "first_period_min": 15, "first_period_eur": 0.25,
                      "source_url": None, "source_date": "2026-10-14", "valid_from": "2026-01-01"},   # wf_2026
    "sz_lebenstedt": {"max_stay_min": 150, "ev_free_minutes": 240, "ev_rule_valid_until": "2026-12-31",
                      "source_url": None, "source_date": "2026-10-14", "valid_from": "2026-06-25"},   # sz_current
    "he_innenstadt": {"max_stay_min": 180},                                                    # he_brochure
    "bh_sole_therme": {"max_stay_min": 360, "fee_window_source": "municipal_page"},            # bh_places
    "bh_kurpark": {"max_stay_min": 360, "fee_window_source": "municipal_page"},
    "bh_grossparkplatz": {"max_stay_min": 600, "fee_window_source": "municipal_page"},
    "bh_burgberg": {"max_stay_min": 600, "fee_window_source": "municipal_page"},
    "bh_berliner_platz": {"max_stay_min": 600, "fee_window_source": "municipal_page"},
    "se_am_markt": {"max_stay_min": 360, "ev_free_minutes": 360, "ev_rule_valid_until": "2026-12-31"},  # seesen_reg
    "gs_altstadt_zone1": {"ev_free_minutes": 240, "ev_rule_valid_until": "2026-12-31"},        # gs_2025_gazette
    "gs_parkplatz_baeringerstrasse": {"ev_free_minutes": 240, "ev_rule_valid_until": "2026-12-31"},
    "gs_parkplatz_klubgartenstrasse_zob": {"ev_free_minutes": 240, "ev_rule_valid_until": "2026-12-31"},
    "gs_parkplatz_glockengiesserstrasse": {"ev_free_minutes": 240, "ev_rule_valid_until": "2026-12-31"},
    "bs_hbf_p1_nord": {"fee_window_source": "municipal_page"},                                 # db_hbf
    "bs_hbf_p2_sued": {"fee_window_source": "municipal_page"},
    "bs_hbf_p3_west": {"fee_window_source": "municipal_page"},
    "wob_tarifzone_1": {},                                                                     # W-max: notes only
    "wob_tarifzone_2": {},
    "wob_tarifzone_3": {},
}


def _same(left, right) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    if pd.isna(left) or pd.isna(right):
        return False
    return left == right


def test_every_row_outside_the_listed_corrections_equals_the_base_table(tariffs):
    base = _base_table()
    assert set(base.index) <= set(tariffs.index) and set(tariffs.index) - set(base.index) == set()
    moved = []
    for zone_id in base.index:
        if zone_id in EXPECTED_CHANGES:
            continue
        for column in base.columns:
            if not _same(base.loc[zone_id, column], tariffs.loc[zone_id, column]):
                moved.append((zone_id, column))
        for column in pz.EV_COLUMNS:
            if pd.notna(tariffs.loc[zone_id, column]):
                moved.append((zone_id, column))
    assert moved == []


@pytest.mark.parametrize("zone_id", sorted(EXPECTED_CHANGES))
def test_a_corrected_row_changes_exactly_the_listed_cells(tariffs, zone_id):
    base = _base_table()
    differing = {}
    for column in pz.TARIFF_COLUMNS:
        if column in ("zone_id", "notes"):
            continue
        before = base.loc[zone_id, column] if column in base.columns else pd.NA
        after = tariffs.loc[zone_id, column]
        if not _same(before, after):
            differing[column] = after
    expected = EXPECTED_CHANGES[zone_id]
    assert sorted(differing) == sorted(expected), zone_id
    for column, value in expected.items():
        if value is None:
            continue    # a source URL: pinned in its own test
        assert _same(differing[column], value), (zone_id, column, differing[column], value)
    assert tariffs.loc[zone_id, "notes"] != base.loc[zone_id, "notes"], f"{zone_id}: the note must record the change"


# ------------------------------------------------------------------------------------------------ the EV columns
def test_the_ev_columns_are_whole_minutes_and_iso_dates_on_street_rows_only(tariffs):
    # Part A defines the columns (R-A-3, ruling R-B-1); B1 fills values only
    assert pz.EV_COLUMNS == ("ev_free_minutes", "ev_rule_valid_until")
    assert str(tariffs["ev_free_minutes"].dtype) == "Int64"
    stated = tariffs[tariffs["ev_free_minutes"].notna()]
    assert sorted(stated.index) == sorted(("sz_lebenstedt", "pe_innenstadt", "se_am_markt", *GOSLAR))
    assert (stated["ev_free_minutes"] >= 1).all() and (stated["zone_type"] == "street_paid").all()
    dated = tariffs[tariffs["ev_rule_valid_until"].notna()]
    assert sorted(dated.index) == sorted(stated.index) and all(pz._is_iso_date(value) for value in dated["ev_rule_valid_until"])
    pz.validate_tariffs(tariffs.reset_index(), allow_fixture_marker=False)


def test_the_loaded_columns_end_with_the_ev_columns_in_the_committed_header():
    header = next(line for line in TARIFFS_PATH.read_text(encoding="utf-8").splitlines() if line.startswith("zone_id,"))
    assert header.split(",")[-3:] == ["resident_permits_valid", "ev_free_minutes", "ev_rule_valid_until"]


# ------------------------------------------------------------------------------------------------ zone by zone
def test_zone_ia_keeps_its_maximum_stay_and_loses_the_long_stay_product(tariffs):
    # bs_parkgo_2025 (ParkGO 2025, sec. 1(2)): 180 min during the fee hours; the 9.00 EUR ticket is zone Ib's (was M1)
    row = tariffs.loc["bs_zone_ia"]
    assert row["max_stay_min"] == 180 and pd.isna(row["long_stay_product_eur"])
    assert "ASSUMPTION M3" in row["notes"] and "ASSUMPTION M1" not in row["notes"] and "no long-stay product" in row["notes"]
    assert row["hourly_rate_eur"] == 1.8 and (row["fee_start_h"], row["fee_end_h"]) == (9.0, 20.0)
    ib = tariffs.loc["bs_zone_ib"]
    assert ib["daily_cap_eur"] == 9.0 and pd.isna(ib["max_stay_min"]) and ib["commuter_day_eur"] == 3.76


def test_a_maximum_stay_without_a_long_stay_product_is_valid_on_a_street_zone_only(tariffs):
    frame = tariffs.reset_index()
    pz.validate_tariffs(frame, allow_fixture_marker=False)        # Ia, Salzgitter, Helmstedt, Bad Harzburg and Seesen
    streets = frame[(frame["zone_type"] == "street_paid") & frame["max_stay_min"].notna()]
    assert streets["long_stay_product_eur"].isna().sum() >= 8
    resident = frame[frame["zone_type"] == "resident_zone"].copy()
    resident["long_stay_product_eur"] = pd.NA
    with pytest.raises(ValueError, match="neither long_stay_product_eur nor the garage columns"):
        pz.validate_tariffs(resident, allow_fixture_marker=False)


def test_peine_is_half_a_euro_per_started_half_hour_without_cap_maximum_or_product(tariffs):
    # pe_current (ParkGO 2022-12-22, effective 2023-04-01, zone II): 0.50 EUR per started 30 min, the 4.20 EUR and 2.60
    # EUR tickets are restricted to three lots (lot rows), no zone-wide maximum; zone I (Echternplatz) is not zoned
    row = tariffs.loc["pe_innenstadt"]
    assert (row["hourly_rate_eur"], row["billing_unit_min"]) == (1.0, 30)
    assert row[["daily_cap_eur", "max_stay_min", "long_stay_product_eur", "first_period_min"]].isna().all()
    assert (row["fee_start_h"], row["fee_end_h"]) == (9.0, 17.0)
    assert row["ev_free_minutes"] == 180 and row["ev_rule_valid_until"] == "2028-04-30"     # pe_current sec. 1a
    assert row["source_url"].startswith("https://www.peine.de/") and "Parkgebuehrenordnung" in row["source_url"]
    notes = row["notes"]
    for phrase in ("pe_current", "no zone-wide maximum", "pe_echtern", "pe_luisen", "2028-04-30"):
        assert phrase in notes, phrase
    assert "ASSUMPTION M1" not in notes
    # an 8 h stay is 16 started half hours = 8.00 EUR, nothing caps it
    assert _price(row, "pe_innenstadt", 9.0, 480) == 800 and _price(row, "pe_innenstadt", 9.0, 31) == 100


def test_wolfenbuettel_charges_a_first_quarter_hour_and_then_per_started_three_minutes(tariffs):
    # wf_2026 (Amtsblatt 55/2025, 2nd amendment, sec. 1(2), 2(1), effective 2026-01-01): 0.25 EUR for the first 15 min,
    # then 0.05 EUR per started 3 min
    row = tariffs.loc["wf_innenstadt"]
    assert (row["first_period_min"], row["first_period_eur"]) == (15, 0.25)
    assert (row["hourly_rate_eur"], row["billing_unit_min"]) == (1.0, 3)
    assert row[["daily_cap_eur", "max_stay_min", "long_stay_product_eur"]].isna().all()
    assert "wf_2026" in row["notes"] and "Amtsblatt 55/2025" in row["notes"] and row["valid_from"] == "2026-01-01"
    assert row["source_url"].startswith("https://www.wolfenbuettel.de/media/custom/3413_10262_1.PDF")
    assert [_price(row, "wf_innenstadt", 10.5, minutes) for minutes in (1, 15, 16, 18, 19, 30, 60)] == [
        25, 25, 30, 30, 35, 50, 100]


def test_salzgitter_has_a_150_minute_maximum_and_an_ev_exemption_within_it(tariffs):
    # sz_current (Amtsblatt 16/2026, sec. 2(3) maximum 150 min, sec. 3 EV 240 min until 2026-12-31); ASSUMPTION E2
    row = tariffs.loc["sz_lebenstedt"]
    assert row["max_stay_min"] == 150 and pd.isna(row["long_stay_product_eur"])
    assert (row["ev_free_minutes"], row["ev_rule_valid_until"]) == (240, "2026-12-31")
    assert "ASSUMPTION E2" in row["notes"] and "sz_current" in row["notes"] and "marked" in row["notes"]
    assert row["source_url"].startswith("https://www.salzgitter.de/rathaus/downloads/amtsblatt-2026/")
    assert (row["billing_unit_min"], row["first_period_min"], row["first_period_eur"]) == (6, 60, 0.7)


def test_helmstedt_has_a_180_minute_maximum(tariffs):
    # he_brochure ('Parken in Helmstedt', 12/2024): 180 min at many city spaces; the fee window stays 9-16 h
    row = tariffs.loc["he_innenstadt"]
    assert row["max_stay_min"] == 180 and pd.isna(row["long_stay_product_eur"])
    assert (row["fee_start_h"], row["fee_end_h"]) == (9.0, 16.0) and "he_brochure" in row["notes"]
    assert "ASSUMPTION M3" in row["notes"] and "not modelled (the zone has no long-stay product" not in row["notes"]


@pytest.mark.parametrize("zone_id, maximum", [("bh_sole_therme", 360), ("bh_kurpark", 360), ("bh_grossparkplatz", 600),
                                              ("bh_burgberg", 600), ("bh_berliner_platz", 600)])
def test_bad_harzburg_maxima_come_from_the_tourism_page_and_the_window_from_the_dated_brochure(tariffs, zone_id, maximum):
    # bh_places (current tourism page: 6 h Sole-Therme and Kurpark, 10 h the other three); bh_brochure_calendar (2022-01
    # brochure, printed p. 41: Mo-Fr 8:00-18:00 for the machines, daily 8:00-18:00 at four named car parks)
    row = tariffs.loc[zone_id]
    assert row["max_stay_min"] == maximum and pd.isna(row["long_stay_product_eur"])
    assert (row["fee_start_h"], row["fee_end_h"], row["fee_window_source"]) == (8.0, 18.0, "municipal_page")
    notes = row["notes"]
    for phrase in ("bh_places", "bh_brochure_calendar", "ASSUMPTION M3", "ASSUMPTION D3-b", "ASSUMPTION C-a"):
        assert phrase in notes, phrase
    assert "ASSUMPTION F1" not in notes and "ASSUMPTION M2" not in notes
    assert row["ev_free_minutes"] is pd.NA or pd.isna(row["ev_free_minutes"])


def test_bad_harzburg_names_the_historical_four_hour_limit_of_the_sole_therme(tariffs):
    # the brochure says 4 h for the Sole-Therme, the current tourism page 6 h: the conflict is named, the current page applies
    notes = tariffs.loc["bh_sole_therme", "notes"]
    assert "4 h" in notes and "brochure" in notes and tariffs.loc["bh_sole_therme", "max_stay_min"] == 360


def test_seesen_applies_the_ordinance_maximum_and_names_the_city_page_conflict(tariffs):
    # seesen_reg sec. 2(4): 6 h; seesen_parking: 10 h at Am Markt, named as a conflict and not applied; sec. 2(3): E plate
    # free until 2026-12-31 with a parking disc and no stated duration, so the zone maximum bounds it (spec A5, ASSUMPTION E2)
    row = tariffs.loc["se_am_markt"]
    assert row["max_stay_min"] == 360 and pd.isna(row["long_stay_product_eur"])
    assert (row["ev_free_minutes"], row["ev_rule_valid_until"]) == (360, "2026-12-31")
    notes = row["notes"]
    for phrase in ("seesen_reg", "seesen_parking", "10 hours", "conflict", "ASSUMPTION M3", "ASSUMPTION E2"):
        assert phrase in notes, phrase
    assert "ASSUMPTION M2" not in notes and row["fee_window_source"] == "ordinance"


@pytest.mark.parametrize("zone_id", GOSLAR)
def test_goslar_street_rows_carry_the_240_minute_ev_exemption_and_name_the_marked_machines(tariffs, zone_id):
    # gs_2025_gazette (Amtsblatt 9/2025, ParkGO 2025-06-24): 240 min with a parking disc, only specifically marked
    # machines, until 2026-12-31; the machine marking is not modelled (named), so the exemption is an upper bound
    row = tariffs.loc[zone_id]
    assert (row["ev_free_minutes"], row["ev_rule_valid_until"]) == (240, "2026-12-31")
    assert "gs_2025_gazette" in row["notes"] and "marked" in row["notes"] and "not modelled" in row["notes"]
    assert pd.isna(row["max_stay_min"])


@pytest.mark.parametrize("zone_id", STATIONS)
def test_the_station_fee_window_is_sourced_from_the_db_bahnpark_page(tariffs, zone_id):
    # db_hbf (bahnhof.de/braunschweig-hbf/parkplaetze, package Grossparkplaetze_ZGB8_2026-10-12.zip): Mo-So incl. holidays
    # 00:00-00:00 for Nord, West and Ackerstrasse (= P2 Sued); the earlier ASSUMPTION F1 is gone
    row = tariffs.loc[zone_id]
    assert (row["fee_start_h"], row["fee_end_h"], row["fee_window_source"]) == (0.0, 24.0, "municipal_page")
    notes = row["notes"]
    assert "db_hbf" in notes and "Grossparkplaetze_ZGB8_2026-10-12.zip" in notes and "ASSUMPTION F1" not in notes
    assert "ASSUMPTION G-a" in notes and "ASSUMPTION C-a" in notes


@pytest.mark.parametrize("zone_id", WOLFSBURG)
def test_wolfsburg_has_no_zone_wide_maximum_and_names_assumption_w_max(tariffs, zone_id):
    row = tariffs.loc[zone_id]
    assert pd.isna(row["max_stay_min"]) and pd.isna(row["long_stay_product_eur"])
    assert "ASSUMPTION W-max" in row["notes"] and "Task B2" in row["notes"]


def test_no_row_carries_a_commuter_product_next_to_a_maximum_stay_without_naming_it(tariffs):
    # spec A6 stays true after the new maxima: the validator accepts the committed table (Ib has no maximum)
    pz.validate_tariffs(tariffs.reset_index(), allow_fixture_marker=False)
    assert tariffs[tariffs["max_stay_min"].notna() & tariffs["commuter_day_eur"].notna()].index.tolist() == []


def test_the_header_names_the_new_assumptions_and_no_longer_applies_m1_to_street_zones():
    text = TARIFFS_PATH.read_text(encoding="utf-8")
    header = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    for assumption in ("M3", "W-max", "E2"):
        assert f"# ASSUMPTION {assumption}:" in header
    m1 = next(line for line in header.splitlines() if line.startswith("# ASSUMPTION M1:"))
    assert "Ia" not in m1 and "Peine" not in m1
    assert "ev_free_minutes" in header and "ev_rule_valid_until" in header


# ------------------------------------------------------------------------------------------------ the coverage register
def test_peine_echternplatz_is_excluded_with_its_reason_in_the_coverage_register():
    register = pz.load_coverage_register(REGISTER_PATH)
    rows = register[register["name"].str.contains("Echternplatz")]
    assert len(rows) == 1
    row = rows.iloc[0]
    assert (row["ags"], row["status"]) == ("03157006", "excluded")
    for phrase in ("single lot", "1.70", "60 min", "pe_echtern"):
        assert phrase in row["note"] + row["source"], phrase
    tariffs = pz.load_tariffs(TARIFFS_PATH)
    pz.validate_coverage_register(register, tariffs)
