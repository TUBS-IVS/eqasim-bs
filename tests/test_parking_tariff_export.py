"""The parking tariff JSON export (schema 3, spec 5.4 plus the v2 columns of issue #436, the permit flag per zone, the
list of resident districts of spec Amendment C3 and the garages of spec Amendment E) and its committed fixture model
(issue #249)."""
from __future__ import annotations

import csv
import dataclasses
import datetime
import hashlib
import io
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.parking import tariff_export as te
from braunschweig.parking import zones as pz
from braunschweig.parking.cost import ZoneTariff
from braunschweig.parking.golden_cases import GOLDEN_CASES, golden_case_mismatches

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "parking"
FIXTURE_JSON = FIXTURES / "parking_tariffs_fixture.json"
FIXTURE_CSV = FIXTURES / "parking_tariffs_fixture.csv"
SNAPSHOT_DATE = "2026-09-28"
V1_ZONE_IDS = {"fx_bs_ia", "fx_bs_ib", "fx_sz", "fx_wob", "fx_pe", "fx_res_a", "fx_campus", "fx_frac"}
V2_ZONE_IDS = {"fx_bs_ib_v2", "fx_bs_ia_v2", "fx_wob_v2", "fx_campus_v2", "fx_garage_window_v2", "fx_capped_street_v2",
               "fx_campus_tie_v2", "fx_res_garage_v2", "fx_bga_v2", "fx_res_nopermit_v2"}
# The zones whose table row states that resident permits are NOT valid (ASSUMPTION R2-a); every other street or resident
# zone takes the default (valid), every campus the campus default (not valid).
PERMITS_NOT_VALID_ZONE_IDS = {"fx_bga_v2", "fx_res_nopermit_v2"}
# The schema-6 rows (golden F, P and X, spec v3): a zone Ia-like without a long-stay product and three EV zones (in force, clipped
# by a maximum stay, expired before the snapshot date).
V3_ZONE_IDS = {"fx_ia_nolong_v3", "fx_ev_street_v3", "fx_ev_clip_v3", "fx_ev_expired_v3"}
FIXTURE_ZONE_IDS = V1_ZONE_IDS | V2_ZONE_IDS | V3_ZONE_IDS
EV_FIXTURE_ZONES = {"fx_ev_street_v3": 240, "fx_ev_clip_v3": 240}      # the exported exemptions (fx_ev_expired_v3 expired)
# Table column in euros -> JSON field in cents, written out here independently of the implementation.
EURO_FIELDS = {"hourly_rate_eur": "hourly_rate_cents", "first_period_eur": "first_period_cents",
               "daily_cap_eur": "daily_cap_cents", "long_stay_product_eur": "long_stay_product_cents",
               "member_day_eur": "member_day_cents", "guest_day_eur": "guest_day_cents",
               "garage_hourly_rate_eur": "garage_hourly_rate_cents",
               "garage_first_period_eur": "garage_first_period_cents",
               "garage_daily_cap_eur": "garage_daily_cap_cents", "commuter_day_eur": "commuter_day_cents"}
MINUTE_FIELDS = ("billing_unit_min", "free_if_stay_at_most_min", "first_period_min", "max_stay_min",
                 "garage_billing_unit_min", "garage_first_period_min", "search_time_min", "ev_free_minutes")
# The table columns schema 2 adds (all optional) and the JSON fields they become; null where absent or empty.
V2_COLUMNS = ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_first_period_min", "garage_first_period_eur",
              "garage_daily_cap_eur", "garage_fee_start_h", "garage_fee_end_h", "commuter_day_eur", "search_time_min")
V2_FIELDS = ("garage_hourly_rate_cents", "garage_billing_unit_min", "garage_first_period_min",
             "garage_first_period_cents", "garage_daily_cap_cents", "garage_fee_start_s", "garage_fee_end_s",
             "commuter_day_cents", "search_time_min")
# The permit column (Amendment C3) is optional too, but its JSON field is never null: an empty cell resolves to the
# default of the zone type.
PERMITS_COLUMN = "resident_permits_valid"
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


@pytest.fixture(scope="module")
def fixture_table_and_sources():
    # The fixture tariff table (spec 5.3 columns) and its provenance, as the golden exporter loads them.
    from scripts.export_parking_golden_cases import load_fixture_tariffs
    return load_fixture_tariffs()


@pytest.fixture(scope="module")
def table(fixture_table_and_sources):
    return fixture_table_and_sources[0]


@pytest.fixture(scope="module")
def sources(fixture_table_and_sources):
    return fixture_table_and_sources[1]


@pytest.fixture(scope="module")
def model(table, sources):
    return te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources)


def _is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or bool(pd.isna(value))


def _row(table, row_zone_id: str, /, **changes) -> dict:
    """The fixture-table row of ``row_zone_id`` with ``changes`` applied (which may replace the zone_id)."""
    row = next(row for row in table.to_dict(orient="records") if row["zone_id"] == row_zone_id)
    return {**row, **changes}


def test_the_model_has_the_schema_5_header_and_the_fixture_zones(model):
    assert model["schema_version"] == 5
    assert model["tariff_snapshot_date"] == SNAPSHOT_DATE
    assert model["currency"] == "EUR" and model["weekday_only"] is True
    assert model["terminal_stay_rule"] == "until_fee_end"
    assert set(model["zones"]) == FIXTURE_ZONE_IDS


def test_the_documented_zone_entries_are_reproduced_field_by_field(model):
    # The zone entry of the JSON fixture shape in the plan (schema of spec 5.4): a v1 row carries every schema-2
    # key as null.
    assert model["zones"]["fx_bs_ia"] == {
        "zone_type": "street_paid", "hourly_rate_cents": 180, "billing_unit_min": 1,
        "free_if_stay_at_most_min": None, "first_period_min": None, "first_period_cents": None,
        "daily_cap_cents": None, "max_stay_min": 180, "long_stay_product_cents": 900,
        "member_day_cents": None, "guest_day_cents": None,
        "fee_start_s": 32400, "fee_end_s": 72000, "resident_exempt": False, **dict.fromkeys(V2_FIELDS),
        "resident_permits_valid": True, "ev_free_minutes": None}
    # A schema-2 row of the golden fixture table (garage 1.20/h in started hours, first hour 1.20, cap 9.60, all day;
    # commuter 3.76 per working day; 5 min search time).
    assert model["zones"]["fx_bs_ib_v2"] == {
        "zone_type": "street_paid", "hourly_rate_cents": 180, "billing_unit_min": 1,
        "free_if_stay_at_most_min": None, "first_period_min": None, "first_period_cents": None,
        "daily_cap_cents": 900, "max_stay_min": None, "long_stay_product_cents": None,
        "member_day_cents": None, "guest_day_cents": None,
        "fee_start_s": 32400, "fee_end_s": 72000, "resident_exempt": False,
        "garage_hourly_rate_cents": 120, "garage_billing_unit_min": 60, "garage_first_period_min": 60,
        "garage_first_period_cents": 120, "garage_daily_cap_cents": 960, "garage_fee_start_s": 0,
        "garage_fee_end_s": 86400, "commuter_day_cents": 376, "search_time_min": 5, "resident_permits_valid": True,
        "ev_free_minutes": None}


def test_the_permit_flag_of_every_zone_is_a_resolved_bool_the_default_of_its_type_unless_the_row_states_it(model):
    # Hand-derived: street and resident zones honour resident permits unless their row says false (ASSUMPTION R2-a),
    # a campus never does; an empty cell is never exported as null.
    for zone_id, zone in model["zones"].items():
        expected = zone["zone_type"] != "campus" and zone_id not in PERMITS_NOT_VALID_ZONE_IDS
        assert zone[PERMITS_COLUMN] is expected, zone_id


def test_money_is_the_table_euros_in_whole_cents_and_empty_cells_are_null(model, table):
    for row in table.to_dict(orient="records"):
        zone = model["zones"][row["zone_id"]]
        for column, field in EURO_FIELDS.items():
            if _is_empty(row[column]):
                assert zone[field] is None, (row["zone_id"], field)
            else:
                assert type(zone[field]) is int, (row["zone_id"], field)
                assert zone[field] == round(float(row[column]) * 100), (row["zone_id"], field)
        for field in MINUTE_FIELDS:
            expected = None if _is_empty(row[field]) else int(float(row[field]))
            if field == "ev_free_minutes" and not _is_empty(row["ev_rule_valid_until"]) and (
                    row["ev_rule_valid_until"] < SNAPSHOT_DATE):
                expected = None       # an expired rule is exported as no exemption (spec v3 A5)
            assert zone[field] == expected, (row["zone_id"], field)
        assert zone["resident_exempt"] is (str(row["resident_exempt"]).lower() == "true")


def test_fee_windows_are_the_table_hours_in_seconds(model, table):
    for row in table.to_dict(orient="records"):
        zone = model["zones"][row["zone_id"]]
        for prefix in ("", "garage_"):
            for bound in ("start", "end"):
                hours = row[f"{prefix}fee_{bound}_h"]
                expected = None if _is_empty(hours) else int(round(float(hours) * 3600))
                assert zone[f"{prefix}fee_{bound}_s"] == expected, (row["zone_id"], prefix, bound)
    assert (model["zones"]["fx_sz"]["fee_start_s"], model["zones"]["fx_wob"]["fee_end_s"]) == (36000, 86400)
    assert (model["zones"]["fx_wob_v2"]["garage_fee_start_s"], model["zones"]["fx_wob_v2"]["garage_fee_end_s"]) == (
        0, 86400)


def test_the_assumptions_render_the_register_of_spec_section_7_the_product_minimum_and_the_district_rule(model):
    # Spec section 7 (v1) followed by P1 (the cheapest usable product) and P2 (the commuter product per working day)
    # of the v2 spec, lever 2, and R2 (the resident parking district rule) with its scope R2-a of its Amendment C3. Spec v3:
    # M3 (maximum-stay feasibility) follows M1 and G1-d (the price in the option weights) follows G1-c. This model is built
    # without a garage dataset, so it lists P9 (a rule of the pricing code) but none of the assumptions that a priced garage row
    # names; its fixture zones carry EV exemptions, so E1 and E2 end the register; P12 is withdrawn and never listed.
    assumptions = model["assumptions"]
    assert assumptions and all(re.fullmatch(r"ASSUMPTION [A-Z][0-9]+(-[a-z])?: .+", text) for text in assumptions)
    assert [text.split(":")[0] for text in assumptions] == [
        f"ASSUMPTION {assumption_id}"
        for assumption_id in ("Z1", "D1", "T1", "M1", "M3", "A1", "A1-b", "C1", "C2", "R1", "H1", "F1", "S1", "P1", "P2", "P9",
                              "G1", "G1-c", "G1-d", "G2", "G3", "R2", "R2-a", "E1", "E2")]
    # Amendment D5 and D6: the Wolfsburg proxy and the campus free share name their configuration key as the arm
    a1_b = next(text for text in assumptions if text.startswith("ASSUMPTION A1-b:"))
    c2 = next(text for text in assumptions if text.startswith("ASSUMPTION C2:"))
    assert "03103" in a1_b and "bs_zentrum" in a1_b and "Volkswagen" in a1_b
    assert "parking_free_share_proxy_classes" in a1_b and "parking_campus_free_share" in c2 and "owner estimate" in c2
    # R2 no longer claims every zone type: where the rule applies is R2-a, which names the BgA car parks and the campus
    r2 = next(text for text in assumptions if text.startswith("ASSUMPTION R2:"))
    r2_a = next(text for text in assumptions if text.startswith("ASSUMPTION R2-a:"))
    assert "every zone type" not in r2 and "R2-a" in r2
    assert "BgA" in r2_a and "campus" in r2_a and "resident_permits_valid" in r2_a
    # Task 4a review carry-over: the ZOB car park is named, as the tariff table's R2-a statement does
    assert "gs_parkplatz_klubgartenstrasse_zob" in r2_a
    p9 = next(text for text in assumptions if text.startswith("ASSUMPTION P9:"))
    assert "closed" in p9 and "1440" in p9 and "started 24 h" in p9
    # G1 to G3: the choice model, the maximum distance and the calibration (target, universe, transfer, no validation)
    g1, g2, g3 = (next(text for text in assumptions if text.startswith(f"ASSUMPTION G{number}:")) for number in (1, 2, 3))
    assert "exp(-d / lambda - beta * c / 100)" in g1 and "expected cost" in g1 and "garage_decay_m" in g1
    assert "does not enter the weights" not in g1, "the weights carry the price since spec v3 A2"
    assert "1000 m" in g2 and "garage_max_distance_m" in g2
    assert "bs_zone_ia" in g3 and "bs_zone_ib" in g3 and "srv2023_city_center_parking" in g3 and "no validation" in g3
    assert "same lambda applies in every town" in g3
    # Amendment H1: the commuter stays have a decay length of their own, calibrated on bs_zentrum, checked on Wolfsburg
    g1_c = next(text for text in assumptions if text.startswith("ASSUMPTION G1-c:"))
    assert "garage_decay_commute_m" in g1_c and "work" in g1_c and "education" in g1_c and "bs_zentrum" in g1_c
    assert "srv2023_commute_parking_by_workplace_class" in g1_c and "03103" in g1_c and "no validation" in g1_c
    assert "parking_garage_decay_commute_m" in g1_c
    assert "work and education" in g1 and "G1-c" in g1
    # Spec v3 A1 and A2: the feasibility rule and the price term, each with its key and its sensitivity
    m3 = next(text for text in assumptions if text.startswith("ASSUMPTION M3:"))
    g1_d = next(text for text in assumptions if text.startswith("ASSUMPTION G1-d:"))
    assert "NO_FEASIBLE_OPTION" in m3 and "max_stay_min" in m3 and "street" in m3
    assert "option_price_beta_u_per_eur" in g1_d and "parking_option_price_beta_u_per_eur" in g1_d
    assert "income" in g1_d and "no" in g1_d and "betaCost_u_MU" in g1_d


def test_the_dataset_assumptions_are_listed_exactly_when_a_priced_garage_row_names_them(garage_frame):
    # R-FW-1: P3 to P8, P10 and P11 are the assumptions of the garage DATASET, so the register lists one only when a PRICED row
    # names it (like P13 and P14); P9 is a rule of the pricing code and always listed; P12 is withdrawn and never listed.
    from braunschweig.parking import garages as pg

    ids = lambda assumptions: [text.split(":")[0].removeprefix("ASSUMPTION ") for text in assumptions]      # noqa: E731
    assert ids(te.assumption_texts()) == ["Z1", "D1", "T1", "M1", "M3", "A1", "A1-b", "C1", "C2", "R1", "H1", "F1", "S1",
                                          "P1", "P2", "P9", "G1", "G1-c", "G1-d", "G2", "G3", "R2", "R2-a"]
    named = te.assumption_texts(garage_assumption_ids=("P8", "P3", "P11", "P6"))
    assert [assumption_id for assumption_id in ids(named) if assumption_id.startswith("P")] == [
        "P1", "P2", "P3", "P6", "P8", "P9", "P11"]
    for assumption_id in ("P3", "P6", "P8", "P11"):
        text = next(text for text in named if text.startswith(f"ASSUMPTION {assumption_id}:"))
        assert pg.ASSUMPTIONS[assumption_id] in text
    # P13 and P14 follow the last dataset assumption listed (here P11) and precede G1
    both = ids(te.assumption_texts(monthly_imputed=True, lot_tariff_imputed=True, garage_assumption_ids=("P3",)))
    assert both[both.index("P9"):both.index("G1")] == ["P9", "P13", "P14"] and "P3" in both
    # P12 is never listed, even if a row names it; an id that is no dataset assumption raises
    assert "P12" not in ids(te.assumption_texts(garage_assumption_ids=("P12",)))
    with pytest.raises(ValueError, match="P99"):
        te.assumption_texts(garage_assumption_ids=("P99",))
    # the fixture dataset: the banded garages name P8 and P10, so exactly those two are listed besides P9
    named_by_fixture = te.garage_assumption_ids(garage_frame)
    assert named_by_fixture >= {"P8", "P10"} and "P12" not in named_by_fixture
    assert te.garage_assumption_ids(None) == frozenset()
    unpriced = garage_frame.copy()
    unpriced["priced"] = False
    assert te.garage_assumption_ids(unpriced) == frozenset()


def test_the_ev_assumptions_are_listed_exactly_when_a_zone_states_an_exemption():
    # ASSUMPTIONS E1 (every BEV/PHEV is an E-plate holder) and E2 (the exemption applies within the maximum stay) matter only to
    # a model that carries an exemption; they follow R2-a.
    ids = [text.split(":")[0].removeprefix("ASSUMPTION ") for text in te.assumption_texts(ev_exemption=True)]
    assert ids[-3:] == ["R2-a", "E1", "E2"]
    assert "E1" not in [text.split(":")[0].removeprefix("ASSUMPTION ") for text in te.assumption_texts()]
    e1, e2 = (next(text for text in te.assumption_texts(ev_exemption=True) if text.startswith(f"ASSUMPTION {key}:"))
              for key in ("E1", "E2"))
    assert "BEV" in e1 and "PHEV" in e1 and "ev_free_minutes" in e2 and "max_stay_min" in e2


def test_the_model_lists_the_resident_districts_sorted_by_id_for_the_plan_check(table, sources):
    # The Java plan check rejects a parkingDistrict or residentParkingDistrict that is not in this list; the list is an
    # additive top-level key.
    districts = pd.DataFrame({"district_id": ["gs_district_b", "bs_district_a", "bs_district_b"],
                              "name": ["B", "A", "B"], "municipality_ags": ["03153017", "03101000", "03101000"]})
    with_districts = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources,
                                           resident_districts=districts)
    assert with_districts["schema_version"] == 5
    assert with_districts["resident_districts"] == [
        {"district_id": "bs_district_a", "municipality_ags": "03101000"},
        {"district_id": "bs_district_b", "municipality_ags": "03101000"},
        {"district_id": "gs_district_b", "municipality_ags": "03153017"}]
    # no district layer given: an empty list, so a plan that carries a district fails the check instead of passing it
    assert te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources)["resident_districts"] == []


@pytest.mark.parametrize("districts, message", [
    (pd.DataFrame({"district_id": ["a", "a"], "municipality_ags": ["03101000"] * 2}), "duplicate district_id 'a'"),
    (pd.DataFrame({"district_id": ["a"]}), r"lack the columns \['municipality_ags'\]"),
    (pd.DataFrame({"district_id": [5], "municipality_ags": ["03101000"]}), "district_id must be a non-empty text"),
    (pd.DataFrame({"district_id": ["a"], "municipality_ags": ["3101000"]}), "municipality_ags must be an 8-digit"),
    (pd.DataFrame({"district_id": ["a"], "municipality_ags": [3101000]}), "municipality_ags must be an 8-digit"),
], ids=["duplicate_id", "no_ags_column", "numeric_id", "short_ags", "numeric_ags"])
def test_invalid_resident_districts_are_rejected(table, sources, districts, message):
    with pytest.raises(ValueError, match=message):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, resident_districts=districts)


def test_sources_carry_64_hex_content_hashes_and_posix_paths(model):
    assert model["sources"]
    for source in model["sources"]:
        assert source["source_id"] and SHA256_HEX.fullmatch(source["sha256"]) and "\\" not in source["path"]


@pytest.mark.parametrize("bad_sources, message", [
    ([], "at least one source"),
    ([{"source_id": "tariffs", "path": "a.csv", "sha256": "abc"}], "sha256"),
    ([{"source_id": "tariffs", "path": "a.csv"}], "sha256"),
    ([{"source_id": "tariffs", "path": "dir\\a.csv", "sha256": "0" * 64}], "POSIX"),
    ([{"source_id": "tariffs", "path": "a.csv", "sha256": "0" * 64}] * 2, "duplicate source_id"),
    # The Java ParkingTariffs reader accepts exactly these three keys per entry, so an extra key is refused here.
    ([{"source_id": "tariffs", "path": "a.csv", "sha256": "0" * 64, "retrieved": "2026-09-29"}],
     r"'retrieved'.*\['source_id', 'path', 'sha256'\]"),
])
def test_invalid_sources_are_rejected(table, bad_sources, message):
    with pytest.raises(ValueError, match=message):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=bad_sources)


def test_only_the_until_fee_end_terminal_stay_rule_is_supported(table, sources):
    with pytest.raises(ValueError, match="terminal_stay_rule"):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources,
                              terminal_stay_rule="until_departure")


def test_the_snapshot_date_must_be_an_iso_date(table, sources):
    with pytest.raises(ValueError, match="snapshot_date"):
        te.build_tariff_model(table, snapshot_date="28.09.2026", sources=sources)
    with pytest.raises(ValueError, match="snapshot_date"):
        te.tariff_model_file_name("bs_", "2026-9-28")


def test_check_snapshot_date_returns_iso_date_text_unchanged():
    assert te.check_snapshot_date("2026-09-28") == "2026-09-28"


@pytest.mark.parametrize("value", ["2026-9-28", "28.09.2026", "2026-02-30", "", None, 20260928,
                                   datetime.date(2026, 9, 28)],
                         ids=["unpadded", "german", "no_such_day", "empty", "none", "number", "date_object"])
def test_check_snapshot_date_accepts_only_iso_date_text(value):
    # The date names the tariff model file and is recorded in the model, so only its exact text form counts.
    with pytest.raises(ValueError, match="snapshot_date must be an ISO date text YYYY-MM-DD"):
        te.check_snapshot_date(value)


def test_a_tariff_row_with_an_unknown_zone_type_raises(table, sources):
    broken = table.copy()
    broken.loc[broken["zone_id"] == "fx_pe", "zone_type"] = "garage"
    with pytest.raises(ValueError, match="unknown zone_type 'garage'"):
        te.build_tariff_model(broken, snapshot_date=SNAPSHOT_DATE, sources=sources)


def test_duplicate_zone_ids_missing_columns_and_empty_tables_are_rejected(table, sources):
    with pytest.raises(ValueError, match="duplicate zone_id 'fx_bs_ia'"):
        te.build_tariff_model(pd.concat([table, table.head(1)]), snapshot_date=SNAPSHOT_DATE, sources=sources)
    with pytest.raises(ValueError, match="daily_cap_eur"):
        te.build_tariff_model(table.drop(columns=["daily_cap_eur"]), snapshot_date=SNAPSHOT_DATE, sources=sources)
    with pytest.raises(ValueError, match="no rows"):
        te.build_tariff_model(table.head(0), snapshot_date=SNAPSHOT_DATE, sources=sources)


def test_row_conversion_reads_text_cells_as_a_csv_reader_delivers_them(table):
    zone = te.tariff_row_to_zone(_row(table, "fx_sz", hourly_rate_eur="1.00", billing_unit_min="6",
                                      daily_cap_eur="", resident_exempt="FALSE"), snapshot_date=None)
    assert isinstance(zone, ZoneTariff)
    assert (zone.hourly_rate_cents, zone.billing_unit_min, zone.daily_cap_cents, zone.resident_exempt) == (
        100, 6, None, False)


def test_the_permit_cell_is_true_false_or_empty_and_empty_takes_the_default_of_the_zone_type(table):
    street, campus = _row(table, "fx_bs_ia"), _row(table, "fx_campus")
    for empty in (None, "", "  ", pd.NA, float("nan")):
        assert te.tariff_row_to_zone({**street, PERMITS_COLUMN: empty}, snapshot_date=None).resident_permits_valid is True
        assert te.tariff_row_to_zone({**campus, PERMITS_COLUMN: empty}, snapshot_date=None).resident_permits_valid is False
    for stated in (False, "false", "FALSE", " False "):
        assert te.tariff_row_to_zone({**street, PERMITS_COLUMN: stated}, snapshot_date=None).resident_permits_valid is False
    assert te.tariff_row_to_zone({**street, PERMITS_COLUMN: "TRUE"}, snapshot_date=None).resident_permits_valid is True
    # a row of a table that predates the column is a row with an empty cell
    older = {key: value for key, value in street.items() if key != PERMITS_COLUMN}
    assert te.tariff_row_to_zone(older, snapshot_date=None) == te.tariff_row_to_zone(street, snapshot_date=None)
    # a campus cannot honour permits: the row is refused with the column named
    with pytest.raises(ValueError, match=r"'fx_campus'.*resident_permits_valid must be false for zone_type 'campus'"):
        te.tariff_row_to_zone({**campus, PERMITS_COLUMN: "true"}, snapshot_date=None)


@pytest.mark.parametrize("changes, message", [
    ({"hourly_rate_eur": 1.805}, "whole number of cents"),
    ({"billing_unit_min": 1.5}, "whole number of minutes"),
    ({"resident_exempt": "yes"}, "resident_exempt must be true or false"),
    ({"resident_exempt": None}, "resident_exempt must be true or false"),
    ({"resident_permits_valid": "yes"}, "resident_permits_valid must be true or false"),
    ({"fee_start_h": None}, "fee_start_h is required"),
    ({"hourly_rate_eur": True}, "hourly_rate_eur must be a number"),
    ({"zone_id": " fx_bs_ia"}, "zone_id"),
])
def test_row_conversion_rejects_cells_it_cannot_convert_exactly(table, changes, message):
    with pytest.raises(ValueError, match=message):
        te.tariff_row_to_zone(_row(table, "fx_bs_ia", **changes), snapshot_date=None)


@pytest.mark.parametrize("prefix, zone_id", [("", "fx_bs_ia"), ("garage_", "fx_bs_ib_v2")], ids=["street", "garage"])
@pytest.mark.parametrize("fee_start_h, fee_end_h", [(20.0, 9.0), (9.0, 9.0), (9.0, 24.5)],
                         ids=["reversed", "zero_length", "past_midnight"])
def test_a_fee_window_error_names_the_hour_columns_of_the_table(table, prefix, zone_id, fee_start_h, fee_end_h):
    # The table holds decimal hours; the message must speak in those columns, not in the derived seconds.
    start, end = f"{prefix}fee_start_h", f"{prefix}fee_end_h"
    with pytest.raises(ValueError) as error:
        te.tariff_row_to_zone(_row(table, zone_id, **{start: fee_start_h, end: fee_end_h}), snapshot_date=None)
    message = str(error.value)
    assert message.startswith(f"tariff row {zone_id!r}: ")
    assert f"{start} = {fee_start_h:g} h" in message and f"{end} = {fee_end_h:g} h" in message
    assert f"0 <= {start} < {end} <= 24" in message
    assert "fee_start_s" not in message and "fee_end_s" not in message and "86400" not in message


@pytest.mark.parametrize("zone_id, changes, column", [
    ("fx_bs_ia", {"member_day_eur": 3.50}, "member_day_eur"),
    ("fx_res_a", {"daily_cap_eur": 9.00}, "daily_cap_eur"),
    ("fx_res_a", {"free_if_stay_at_most_min": 30}, "free_if_stay_at_most_min"),
    ("fx_campus", {"hourly_rate_eur": 1.80}, "hourly_rate_eur"),
    # Schema 2: no garage on campus, no commuter product in a resident zone (A4).
    ("fx_campus", {"garage_hourly_rate_eur": 1.20, "garage_billing_unit_min": 60, "garage_daily_cap_eur": 9.60,
                   "garage_fee_start_h": 0.0, "garage_fee_end_h": 24.0}, "garage_hourly_rate_eur"),
    ("fx_res_a", {"commuter_day_eur": 3.76}, "commuter_day_eur"),
])
def test_a_cell_the_zone_type_does_not_have_is_named_with_the_hint_to_leave_it_empty(table, zone_id, changes, column):
    with pytest.raises(ValueError) as error:
        te.tariff_row_to_zone(_row(table, zone_id, **changes), snapshot_date=None)
    message = str(error.value)
    assert message.startswith(f"tariff row {zone_id!r}: {column} does not apply to zone_type ")
    assert "leave the cell empty" in message


def test_a_daily_cap_below_the_first_period_price_is_rejected_for_the_row(table):
    # fx_wob charges a first period of 1.10 EUR; a cap of 1.00 EUR contradicts it.
    with pytest.raises(ValueError, match=r"'fx_wob'.*daily_cap_cents 100 is below first_period_cents 110"):
        te.tariff_row_to_zone(_row(table, "fx_wob", daily_cap_eur=1.00), snapshot_date=None)
    assert te.tariff_row_to_zone(_row(table, "fx_wob", daily_cap_eur=1.10), snapshot_date=None).daily_cap_cents == 110


def test_write_tariff_model_writes_sorted_keys_and_lf_line_endings(tmp_path, model):
    raw = te.write_tariff_model(tmp_path / "model.json", model).read_bytes()
    text = raw.decode("ascii")
    assert b"\r" not in raw and raw.endswith(b"}\n")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    assert json.loads(text) == model
    assert te.write_tariff_model(tmp_path / "again.json", model).read_bytes() == raw


def test_file_names_carry_the_prefix_and_the_snapshot_date():
    assert te.tariff_model_file_name("bs_", "2026-09-28") == "bs_parking_tariffs_2026-09-28.json"
    assert te.inputs_report_name("bs_") == "bs_parking_inputs_report.json"


def test_content_hash_ignores_the_checkout_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf.csv", tmp_path / "crlf.csv"
    lf.write_bytes(b"a,b\n1,2\n")
    crlf.write_bytes(b"a,b\r\n1,2\r\n")
    assert te.content_sha256(lf) == te.content_sha256(crlf) == hashlib.sha256(b"a,b\n1,2\n").hexdigest()


def _schema_1_csv(path: Path) -> Path:
    """The v1 rows of the fixture CSV in the schema-1 layout: the schema-2 columns and the permit column removed, every
    cell kept as text."""
    lines = FIXTURE_CSV.read_text(encoding="utf-8").splitlines()
    comments = [line for line in lines if line.startswith("#")]
    header, *rows = list(csv.reader(line for line in lines if not line.startswith("#")))
    keep = [index for index, column in enumerate(header) if column not in (*V2_COLUMNS, PERMITS_COLUMN, *pz.EV_COLUMNS)]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows([[header[index] for index in keep]] + [[row[index] for index in keep] for row in rows
                                                              if row[header.index("zone_id")] in V1_ZONE_IDS])
    path.write_text("\n".join(comments) + "\n" + buffer.getvalue(), encoding="utf-8")
    return path


def test_a_schema_1_table_loads_and_exports_as_schema_3_and_prices_the_v1_cases_unchanged(tmp_path, model, sources):
    """Review focus 1 (Python): a schema-1 CSV, without any of the nine schema-2 columns, still loads, validates and
    exports; every schema-2 field is null, every zone equals its schema-2 fixture entry (the permit flag takes the default
    of its zone type), and G01..G38, L01..L08, L01Z..L08Z and R01..R10 (the district rule R2 reads the permit flag, which
    the default resolves: valid on the street zones, not on the campus of R05) price on it exactly as their golden
    values say."""
    path = _schema_1_csv(tmp_path / "schema_1_tariffs.csv")
    header = next(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    assert header.split(",") == list(pz.SCHEMA_1_TARIFF_COLUMNS)
    table = pz.load_tariffs(path)
    assert list(table.columns) == list(pz.TARIFF_COLUMNS)
    assert table[[*V2_COLUMNS, PERMITS_COLUMN, *pz.EV_COLUMNS]].isna().all().all()
    pz.validate_tariffs(table, allow_fixture_marker=True)
    schema_1_model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources)
    assert schema_1_model["schema_version"] == 5
    assert schema_1_model["zones"] == {zone_id: model["zones"][zone_id] for zone_id in V1_ZONE_IDS}
    assert all(zone[field] is None for zone in schema_1_model["zones"].values() for field in V2_FIELDS)
    # A frame that lacks the schema-2 columns altogether (not read through the loader) exports the same zones.
    assert te.build_tariff_model(table.drop(columns=[*V2_COLUMNS, PERMITS_COLUMN, *pz.EV_COLUMNS]),
                                 snapshot_date=SNAPSHOT_DATE, sources=sources)["zones"] == schema_1_model["zones"]
    from scripts.export_parking_golden_cases import zones_from_model
    v1_cases = [case for case in GOLDEN_CASES if case["zone_id"] in V1_ZONE_IDS and case["destination_x_m"] is None
                and case["id"][0] in "GLR"]
    assert len(v1_cases) == 38 + 8 + 8 + 10 and not [case for case in v1_cases if case["id"].startswith("V")]
    assert golden_case_mismatches(zones_from_model(schema_1_model), v1_cases) == []


def test_a_pre_v2_tariff_model_without_the_schema_2_keys_round_trips_through_zones_from_model(model):
    """A tariff model written before schema 2 has no schema-2 keys at all (not null ones), and none written before the
    permit flag has that key: zones_from_model reads its zone entries into the same tariffs as the schema-2 entries with
    null keys and the resolved flag, which export back unchanged."""
    from scripts.export_parking_golden_cases import zones_from_model
    v1_zones = {zone_id: model["zones"][zone_id] for zone_id in V1_ZONE_IDS}
    pre_v2_zones = {zone_id: {key: value for key, value in zone.items() if key not in (*V2_FIELDS, PERMITS_COLUMN)}
                    for zone_id, zone in v1_zones.items()}
    assert all(not {*V2_FIELDS, PERMITS_COLUMN} & set(zone) for zone in pre_v2_zones.values())
    tariffs = zones_from_model({"zones": pre_v2_zones})
    assert tariffs == zones_from_model({"zones": v1_zones})
    assert {zone_id: te.zone_to_json(tariff) for zone_id, tariff in tariffs.items()} == v1_zones


def test_the_committed_fixture_tariff_model_is_in_sync(table, sources):
    from scripts.export_parking_golden_cases import build_fixture_tariff_model
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert committed == build_fixture_tariff_model(table, sources), (
        "regenerate with: python scripts/export_parking_golden_cases.py")


def test_the_committed_fixture_model_lists_the_fixture_districts_and_names_their_file_as_a_source():
    # Hand-written from tests/fixtures/parking/parking_resident_districts_fixture.geojson, for the Java reader test of
    # the plan check; the districts file is one of the model's sources, like the districts of the real release.
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert committed["resident_districts"] == [
        {"district_id": "fx_district_a", "municipality_ags": "03101000"},
        {"district_id": "fx_district_b", "municipality_ags": "03101000"},
        {"district_id": "fx_district_sz_a", "municipality_ags": "03102000"}]
    source = next(source for source in committed["sources"] if source["source_id"] == "parking_resident_districts_fixture")
    districts_file = FIXTURES / "parking_resident_districts_fixture.geojson"
    assert source["path"] == "tests/fixtures/parking/parking_resident_districts_fixture.geojson"
    assert source["sha256"] == te.content_sha256(districts_file)


# ------------------------------------------------------------------------------------------ garages (schema 3)

FIXTURE_GARAGES = FIXTURES / "parking_garages_fixture.geojson"
GARAGE_KEYS = ["bands", "billing_unit_min", "daily_cap_cents", "fee_end_s", "fee_start_s", "first_period_cents",
               "first_period_end_s", "first_period_min", "first_period_start_s", "garage_id", "hourly_rate_cents",
               "max_stay_min", "monthly_cents", "tiers", "x_m", "y_m"]


@pytest.fixture(scope="module")
def garage_frame():
    from braunschweig.parking import garages as pg
    frame = pg.load_garages(FIXTURE_GARAGES, allow_legacy_columns=True)
    pg.validate_garages(frame)
    return frame


def test_the_model_has_exactly_the_documented_top_level_keys_and_the_garage_parameters(model):
    assert sorted(model) == ["assumptions", "currency", "garage_decay_commute_m", "garage_decay_m", "garage_max_distance_m",
                             "garages", "option_price_beta_u_per_eur", "resident_districts", "schema_version", "sources",
                             "tariff_snapshot_date", "terminal_stay_rule", "weekday_only", "zones"]
    # built without a garage dataset: no garages, both decays 0 (the garage options off), the default maximum distance
    assert model["garages"] == [] and model["garage_decay_m"] == 0.0 and model["garage_max_distance_m"] == 1000.0
    assert model["garage_decay_commute_m"] == 0.0
    # built without the money coefficient: 0.0 = the price-independent weights of v2 (an explicit value, never absent)
    assert model["option_price_beta_u_per_eur"] == 0.0 and isinstance(model["option_price_beta_u_per_eur"], float)


def test_the_garages_are_the_priced_garages_of_the_dataset_sorted_by_id_in_integer_units(table, sources, garage_frame):
    shuffled = garage_frame.iloc[::-1].reset_index(drop=True)
    full = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=shuffled,
                                 garage_decay_m=400.0, garage_decay_commute_m=250.0, garage_max_distance_m=1000.0)
    assert full["garage_decay_m"] == 400.0 and full["garage_max_distance_m"] == 1000.0
    assert full["garage_decay_commute_m"] == 250.0
    ids = [entry["garage_id"] for entry in full["garages"]]
    assert ids == sorted(ids) and len(ids) == 38
    assert all(sorted(entry) == GARAGE_KEYS for entry in full["garages"])
    by_id = {entry["garage_id"]: entry for entry in full["garages"]}
    # hand-derived from the fixture rows: 2.00 EUR per started 60 min, all day (0 to 86400 s), monthly 63.00 EUR
    assert by_id["fx_g01_core"] == {
        "garage_id": "fx_g01_core", "x_m": 580300.0, "y_m": 5750000.0, "hourly_rate_cents": 200, "billing_unit_min": 60,
        "fee_start_s": 0, "fee_end_s": 86400, "first_period_min": None, "first_period_cents": None,
        "first_period_start_s": None, "first_period_end_s": None, "daily_cap_cents": None, "tiers": None, "bands": None,
        "monthly_cents": 6300, "max_stay_min": None}
    # tiers in seconds after midnight and cents, the first-period window 06:00-24:00 and the cap 6.00 EUR
    assert by_id["fx_g09_fp"]["tiers"] == [{"start_s": 0, "end_s": 21600, "unit_min": 60, "price_cents": 50},
                                           {"start_s": 21600, "end_s": 86400, "unit_min": 60, "price_cents": 120}]
    assert (by_id["fx_g09_fp"]["first_period_min"], by_id["fx_g09_fp"]["first_period_cents"],
            by_id["fx_g09_fp"]["first_period_start_s"], by_id["fx_g09_fp"]["first_period_end_s"],
            by_id["fx_g09_fp"]["daily_cap_cents"]) == (60, 110, 21600, 86400, 600)
    assert by_id["fx_g09_fp"]["fee_start_s"] is None and by_id["fx_g09_fp"]["hourly_rate_cents"] is None
    # the night tier of Rosenwall crosses midnight: 23:00-08:00 is 82800 to 28800
    assert {"start_s": 82800, "end_s": 28800, "unit_min": 30, "price_cents": 10} in by_id["fx_g07_tiers"]["tiers"]
    assert by_id["fx_g10_grace"]["bands"] == [
        {"from_min": 0, "to_min": 15, "kind": "free", "price_cents": 0, "unit_min": None},
        {"from_min": 15, "to_min": 60, "kind": "total", "price_cents": 150, "unit_min": None},
        {"from_min": 60, "to_min": None, "kind": "increment", "price_cents": 150, "unit_min": 60}]
    assert by_id["fx_g10_grace"]["fee_start_s"] == 0 and by_id["fx_g10_grace"]["daily_cap_cents"] == 1800
    assert by_id["fx_g06_window"]["fee_start_s"] == 36000 and by_id["fx_g06_window"]["fee_end_s"] == 43200
    # the model reads back into the same garages (the Java reader's contract)
    assert [g.to_json() for g in te.garages_from_model(full)] == full["garages"]
    assert te.garages_from_model({"zones": {}}) == []   # a schema 1 or 2 model has no garages


def test_an_unpriced_garage_is_listed_in_the_dataset_and_left_out_of_the_model(table, sources, garage_frame):
    unpriced = garage_frame.copy()
    unpriced.loc[0, ["priced", "not_priced_reason"]] = [False, "no_published_tariff"]
    for column in ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_fee_start_h", "garage_fee_end_h",
                   "monthly_eur"):
        unpriced.loc[0, column] = None
    entries = te.garage_entries(unpriced)
    assert len(entries) == 37 and unpriced.loc[0, "garage_id"] not in [entry["garage_id"] for entry in entries]
    with pytest.raises(ValueError, match="not priced"):
        te.garage_row_to_tariff(unpriced.iloc[0])


def test_a_tiered_garage_with_a_grace_period_and_a_free_car_park_export_without_a_new_key(garage_frame):
    # Task 4b3: the grace period of a tiered garage is the one closed free band next to its tiers, a car park that is free for
    # every stay the one open free band with the formal fee window; both use the documented keys of the garage entry only
    graced = garage_frame[garage_frame["garage_id"] == "fx_g07_tiers"].copy()
    graced["tariff_duration_bands"] = "0-30 free"
    [entry] = te.garage_entries(graced)
    assert sorted(entry) == GARAGE_KEYS and entry["tiers"] and entry["hourly_rate_cents"] is None
    assert entry["bands"] == [{"from_min": 0, "to_min": 30, "kind": "free", "price_cents": 0, "unit_min": None}]
    free = garage_frame[garage_frame["garage_id"] == "fx_g01_core"].copy()
    for column in ("garage_hourly_rate_eur", "garage_billing_unit_min", "monthly_eur"):
        free[column] = None
    free["tariff_duration_bands"] = "0- free"
    [entry] = te.garage_entries(free)
    assert sorted(entry) == GARAGE_KEYS and entry["hourly_rate_cents"] is None and entry["tiers"] is None
    assert entry["bands"] == [{"from_min": 0, "to_min": None, "kind": "free", "price_cents": 0, "unit_min": None}]
    assert (entry["fee_start_s"], entry["fee_end_s"]) == (0, 86400)
    assert [g.to_json() for g in te.garages_from_model({"garages": [entry]})] == [entry]


def test_the_garage_export_refuses_a_wrong_crs_a_missing_column_and_bad_parameters(table, sources, garage_frame):
    with pytest.raises(ValueError, match="EPSG:25832"):
        te.garage_entries(garage_frame.to_crs("EPSG:4326"))
    with pytest.raises(ValueError, match="lacks the columns"):
        te.garage_entries(garage_frame.drop(columns=["tariff_tiers"]))
    for decay, distance, message in ((-1.0, 1000.0, "garage_decay_m"), (float("nan"), 1000.0, "garage_decay_m"),
                                     (400.0, 0.0, "garage_max_distance_m"), (400.0, float("inf"), "garage_max_distance_m")):
        with pytest.raises(ValueError, match=message):
            te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garage_decay_m=decay,
                                  garage_max_distance_m=distance)


def test_the_commuter_decay_is_validated_like_the_visitor_decay_and_never_defaults_to_it(table, sources, garage_frame):
    for decay_commute, message in ((-1.0, "garage_decay_commute_m"), (float("nan"), "garage_decay_commute_m"),
                                   (float("inf"), "garage_decay_commute_m"), (True, "garage_decay_commute_m"),
                                   ("250", "garage_decay_commute_m")):
        with pytest.raises(ValueError, match=message):
            te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame,
                                  garage_decay_m=400.0, garage_decay_commute_m=decay_commute)
    # the garage options are on for the visitors but the commuter decay was not given: no silent default to lambda
    with pytest.raises(ValueError, match="garage_decay_commute_m is not given"):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame,
                              garage_decay_m=400.0)
    # each decay above 0 needs a priced garage
    for options in ({"garage_decay_m": 400.0, "garage_decay_commute_m": 0.0},
                    {"garage_decay_m": 0.0, "garage_decay_commute_m": 250.0},
                    {"garage_decay_m": 400.0, "garage_decay_commute_m": 250.0}):
        with pytest.raises(ValueError, match="lists no priced garage"):
            te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, **options)
    # 0 for either decay is valid with garages: it switches the options off for the purposes that decay governs
    for decay, decay_commute in ((400.0, 0.0), (0.0, 250.0), (0.0, 0.0)):
        model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame,
                                      garage_decay_m=decay, garage_decay_commute_m=decay_commute)
        assert (model["garage_decay_m"], model["garage_decay_commute_m"]) == (decay, decay_commute)


def test_a_schema_3_model_without_the_commuter_decay_prices_every_purpose_with_the_one_decay(table, sources, garage_frame):
    # H4: a schema-3 file has no garage_decay_commute_m; the Python reader of the model derives the pair as the Java reader
    # does: the visitor decay for every purpose, as schema 3 always did
    model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame,
                                  garage_decay_m=400.0, garage_decay_commute_m=250.0)
    assert te.garage_decays_from_model(model) == (400.0, 250.0)
    schema_3 = {key: value for key, value in model.items() if key != "garage_decay_commute_m"} | {"schema_version": 3}
    assert te.garage_decays_from_model(schema_3) == (400.0, 400.0)
    with pytest.raises(ValueError, match="has no garage_decay_commute_m"):
        te.garage_decays_from_model({**schema_3, "schema_version": 4})


def test_a_garage_price_that_is_no_whole_cent_is_refused(garage_frame):
    broken = garage_frame.copy()
    broken.loc[0, "garage_hourly_rate_eur"] = 2.005
    with pytest.raises(ValueError, match="whole number of cents"):
        te.garage_entries(broken)


def test_the_committed_fixture_model_lists_the_fixture_garages_and_names_their_file_as_a_source():
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert committed["schema_version"] == 5 and committed["garage_decay_m"] == 400.0
    assert committed["garage_max_distance_m"] == 1000.0 and len(committed["garages"]) == 38
    source = next(source for source in committed["sources"] if source["source_id"] == "parking_garages_fixture")
    assert source["path"] == "tests/fixtures/parking/parking_garages_fixture.geojson"
    assert source["sha256"] == te.content_sha256(FIXTURE_GARAGES)


def test_a_zone_row_with_garage_columns_is_warned_about_when_the_garage_options_are_on_e8(table, sources, garage_frame,
                                                                                         caplog):
    # E8: with garage options the zone-level garage family is superseded (the production table has none). The fixture table
    # has it on 6 of its 22 rows (the fx_*_v2 rows with a garage core); the export says so, with the count, only when the
    # decay is above 0.
    logger = "braunschweig.parking.tariff_export"
    with caplog.at_level("WARNING", logger=logger):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame, garage_decay_m=0.0)
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources)
    assert not [record for record in caplog.records if record.name == logger]
    with caplog.at_level("WARNING", logger=logger):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame, garage_decay_m=400.0,
                              garage_decay_commute_m=250.0)
    [message] = [record.getMessage() for record in caplog.records if record.name == logger]
    assert "6 of 22 zone rows" in message and "superseded" in message and "E8" in message
    for zone_id in ("fx_bs_ia_v2", "fx_bs_ib_v2", "fx_res_garage_v2"):
        assert zone_id in message
    # a table without zone-level garage columns (the production table) gives no warning
    no_family = table[table["zone_id"].isin(V1_ZONE_IDS)]
    caplog.clear()
    with caplog.at_level("WARNING", logger=logger):
        te.build_tariff_model(no_family, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame,
                              garage_decay_m=400.0, garage_decay_commute_m=250.0)
    assert not [record for record in caplog.records if record.name == logger]


# ------------------------------------------------------------------------------------------ monthly products (spec Amendment F)


def _with_imputed_monthly(frame, garage_id: str, eur: float = 107.48):
    """The fixture garage dataset with an imputed monthly product (ASSUMPTION P13) on one garage that has none."""
    changed = frame.copy()
    index = changed.index[changed["garage_id"] == garage_id][0]
    assert pd.isna(changed.loc[index, "monthly_eur"]) and changed.loc[index, "facility_kind"] == "garage"
    changed.loc[index, "monthly_imputed_eur"] = eur
    return changed


def _exported(frame, **options):
    return {entry["garage_id"]: entry for entry in te.garage_entries(frame, **options)}


def test_the_imputed_monthly_product_is_exported_as_monthly_cents_when_the_imputation_is_on(garage_frame):
    garage_id = "fx_g02_core_cap" if "fx_g02_core_cap" in set(garage_frame["garage_id"]) else next(
        row["garage_id"] for _, row in garage_frame.iterrows() if pd.isna(row["monthly_eur"]) and row["priced"]
        and row["facility_kind"] == "garage")
    frame = _with_imputed_monthly(garage_frame, garage_id)
    assert _exported(frame)[garage_id]["monthly_cents"] == 10748
    assert _exported(frame, monthly_imputation=True)[garage_id]["monthly_cents"] == 10748
    # the sensitivity arm 'published only': the garage has no monthly product
    assert _exported(frame, monthly_imputation=False)[garage_id]["monthly_cents"] is None
    # a published product is untouched by either setting, and the key set of an entry does not change (Java contract)
    for options in ({}, {"monthly_imputation": False}):
        entries = _exported(frame, **options)
        assert entries["fx_g01_core"]["monthly_cents"] == 6300
        assert all(sorted(entry) == GARAGE_KEYS for entry in entries.values())
    others = {key: value for key, value in _exported(garage_frame).items() if key != garage_id}
    assert {key: value for key, value in _exported(frame).items() if key != garage_id} == others


def test_an_imputed_product_next_to_a_published_one_or_at_a_surface_lot_is_refused_by_the_export(garage_frame):
    both = garage_frame.copy()
    both.loc[both["garage_id"] == "fx_g01_core", "monthly_imputed_eur"] = 50.0
    with pytest.raises(ValueError, match="monthly_imputed_eur is set next to a published monthly_eur"):
        te.garage_entries(both)
    lot = _with_imputed_monthly(garage_frame, next(row["garage_id"] for _, row in garage_frame.iterrows()
                                                    if pd.isna(row["monthly_eur"]) and row["priced"]))
    lot["facility_kind"] = "surface_lot"
    with pytest.raises(ValueError, match="never gets an imputed monthly product"):
        te.garage_entries(lot)


def test_the_model_lists_assumption_p13_exactly_when_an_imputed_product_is_used(table, sources, garage_frame, caplog):
    garage_id = next(row["garage_id"] for _, row in garage_frame.iterrows()
                     if pd.isna(row["monthly_eur"]) and row["priced"] and row["facility_kind"] == "garage")
    frame = _with_imputed_monthly(garage_frame, garage_id)
    plain = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame)
    with caplog.at_level("INFO", logger="braunschweig.parking.tariff_export"):
        on = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=frame)
    off = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=frame,
                                garage_monthly_imputation=False)
    ids = [text.split(":")[0] for text in on["assumptions"]]
    assert ids.index("ASSUMPTION P13") == ids.index("ASSUMPTION G1") - 1
    assert [text for text in on["assumptions"] if "P13" not in text.split(":")[0]] == plain["assumptions"]
    assert "median" in next(text for text in on["assumptions"] if text.startswith("ASSUMPTION P13:"))
    # the model without an imputed product (a dataset without one, or the sensitivity arm) has the register as before
    assert off["assumptions"] == plain["assumptions"] and "ASSUMPTION P13" not in " ".join(plain["assumptions"])
    # same schema, same keys: the Java reader and the golden fixture see no difference
    assert sorted(on) == sorted(plain) == sorted(off) and on["schema_version"] == plain["schema_version"] == 5
    assert on["zones"] == plain["zones"] and [entry["garage_id"] for entry in on["garages"]] == [
        entry["garage_id"] for entry in plain["garages"]]
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "parking_garage_monthly_imputation=True" in messages and "P13 median 107.48 EUR" in messages


def _with_lot_tariff_imputed(frame, garage_id: str):
    """The fixture garage dataset with ASSUMPTION P14 named on one priced garage that becomes a surface lot (the dataset
    validator allows P14 on a surface lot only; the export reads the assumptions column and the facility kind)."""
    changed = frame.copy()
    index = changed.index[changed["garage_id"] == garage_id][0]
    assert bool(changed.loc[index, "priced"]) and pd.isna(changed.loc[index, "monthly_eur"])
    existing = changed.loc[index, "assumptions"]
    changed.loc[index, "assumptions"] = f"{existing};P14" if isinstance(existing, str) and existing else "P14"
    changed.loc[index, "facility_kind"] = "surface_lot"
    return changed


def test_the_model_lists_assumption_p14_exactly_when_a_priced_row_names_it(table, sources, garage_frame):
    candidates = [row["garage_id"] for _, row in garage_frame.iterrows()
                  if pd.isna(row["monthly_eur"]) and row["priced"] and row["facility_kind"] == "garage"]
    garage_id, other_id = candidates[0], candidates[1]
    frame = _with_lot_tariff_imputed(garage_frame, garage_id)
    plain = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame)
    on = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=frame)
    ids = [text.split(":")[0] for text in on["assumptions"]]
    assert ids.index("ASSUMPTION P14") == ids.index("ASSUMPTION G1") - 1
    assert [text for text in on["assumptions"] if "P14" not in text.split(":")[0]] == plain["assumptions"]
    assert "ASSUMPTION P14" not in " ".join(plain["assumptions"])
    p14 = next(text for text in on["assumptions"] if text.startswith("ASSUMPTION P14:"))
    assert "MEDIAN" in p14 and "SAME ZONE" in p14 and "Surface lots without a stated general tariff" in p14
    # same schema, same keys: the Java reader and the golden fixture see no difference
    assert sorted(on) == sorted(plain) and on["schema_version"] == plain["schema_version"] == 5
    assert on["zones"] == plain["zones"] and [entry["garage_id"] for entry in on["garages"]] == [
        entry["garage_id"] for entry in plain["garages"]]
    # only a PRICED row lists it, and the count is of priced rows that name the id (not of a longer id that contains it)
    unpriced = frame.copy()
    unpriced.loc[unpriced["garage_id"] == garage_id, "priced"] = False
    assert te.garages_naming_assumption(frame, "P14") == 1 and te.garages_naming_assumption(unpriced, "P14") == 0
    assert te.garages_naming_assumption(None, "P14") == 0 and te.garages_naming_assumption(garage_frame, "P14") == 0
    assert te.garages_naming_assumption(frame, "P1") == 0
    # a model that uses an imputed monthly product as well lists P13 and P14 in that order, after the dataset assumptions
    # that its priced rows name and before G1
    both = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources,
                                 garages=_with_imputed_monthly(frame, other_id))
    both_ids = [text.split(":")[0] for text in both["assumptions"]]
    position = both_ids.index("ASSUMPTION P13")
    assert both_ids[position:position + 3] == ["ASSUMPTION P13", "ASSUMPTION P14", "ASSUMPTION G1"]
    assert te.assumption_texts(monthly_imputed=True, lot_tariff_imputed=True,
                               garage_assumption_ids=te.garage_assumption_ids(frame), ev_exemption=True) == both["assumptions"]


def test_the_register_never_lists_the_withdrawn_assumption_p12_and_a_row_that_names_it_is_warned_about(
        table, sources, garage_frame, caplog):
    # spec Amendment I5a (Task 4j) withdraws P12 in the dataset documentation (garages.assumption_status_text) and R-FW-1 drops
    # it from the exported register: the register of the model lists the dataset assumptions that PRICED rows name, and P12 is
    # none of them any more. A row of an older dataset that still names it is not silently ignored: the export warns.
    from braunschweig.parking import garages as pg

    assert "P12" in pg.WITHDRAWN_ASSUMPTIONS
    assert "ASSUMPTION P12" not in " ".join(te.assumption_texts(garage_assumption_ids=("P12", "P8")))
    frame = garage_frame.copy()
    index = frame.index[frame["garage_id"] == "fx_g01_core"][0]
    frame.loc[index, "assumptions"] = "P12"
    caplog.clear()
    with caplog.at_level("WARNING", logger="braunschweig.parking.tariff_export"):
        model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=frame)
    assert not any(text.startswith("ASSUMPTION P12:") for text in model["assumptions"])
    [message] = [record.getMessage() for record in caplog.records if "P12" in record.getMessage()]
    assert "withdrawn" in message and "1 priced garage" in message and "the it is" not in message
    assert "it is not listed in the register of the model" in message


def test_the_monthly_summary_counts_published_imputed_and_none_per_municipality(garage_frame):
    frame = _with_imputed_monthly(garage_frame, next(row["garage_id"] for _, row in garage_frame.iterrows()
                                                     if pd.isna(row["monthly_eur"]) and row["priced"]
                                                     and row["facility_kind"] == "garage"))
    on, off = te.monthly_product_summary(frame, True), te.monthly_product_summary(frame, False)
    assert sum(entry["imputed"] for entry in on.values()) == 1 and sum(entry["imputed"] for entry in off.values()) == 0
    assert sum(entry["published"] for entry in on.values()) == sum(entry["published"] for entry in off.values()) > 0
    assert te.monthly_product_summary(None) == {} and te.monthly_product_text({}) == "no garage dataset"
    assert "none" in te.monthly_product_text(on)


def test_a_fixture_dataset_without_imputed_products_gives_the_committed_golden_model(table, sources, garage_frame):
    # the fixture file has no monthly_imputed_eur column (the loader reads it as empty), so the committed fixture model is
    # unchanged by Amendment F: its register, its garages and its key sets
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert garage_frame["monthly_imputed_eur"].isna().all()
    assert not any("P13" in text.split(":")[0] for text in committed["assumptions"])
    assert te.assumption_texts(garage_assumption_ids=te.garage_assumption_ids(garage_frame),
                               ev_exemption=any(zone["ev_free_minutes"] is not None for zone in committed["zones"].values())
                               ) == committed["assumptions"]
    assert te.assumption_texts(monthly_imputed=True) != committed["assumptions"]


def test_the_imputation_switch_must_be_a_boolean(table, sources, garage_frame):
    with pytest.raises(ValueError, match="garage_monthly_imputation must be true or false"):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame,
                              garage_monthly_imputation="false")


def test_the_export_warns_per_municipality_when_the_imputed_products_are_a_majority(table, sources, garage_frame, caplog):
    logger = "braunschweig.parking.tariff_export"
    # one imputed garage among 19: no warning; every garage without a published product imputed: a warning that names the town
    one = _with_imputed_monthly(garage_frame, next(row["garage_id"] for _, row in garage_frame.iterrows()
                                                    if pd.isna(row["monthly_eur"]) and row["priced"]
                                                    and row["facility_kind"] == "garage"))
    many = garage_frame.copy()
    open_rows = many["monthly_eur"].isna() & many["priced"].astype(bool) & (many["facility_kind"] == "garage")
    many.loc[open_rows, "monthly_imputed_eur"] = 107.48
    with caplog.at_level("WARNING", logger=logger):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=one)
        assert not [record for record in caplog.records if record.name == logger]
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=many)
        messages = [record.getMessage() for record in caplog.records if record.name == logger]
        assert messages and all("carry an imputed monthly product (ASSUMPTION P13" in message for message in messages)
        caplog.clear()
        # the sensitivity arm uses no imputed product, so it has nothing to warn about
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=many,
                              garage_monthly_imputation=False)
        assert not [record for record in caplog.records if record.name == logger]


def _street_zone_fields(**changes):
    from braunschweig.parking import cost

    fields = {"zone_id": "z", "zone_type": "street_paid", "hourly_rate_cents": 180, "billing_unit_min": 1,
              "free_if_stay_at_most_min": None, "first_period_min": None, "first_period_cents": None,
              "daily_cap_cents": None, "max_stay_min": None, "long_stay_product_cents": None, "member_day_cents": None,
              "guest_day_cents": None, "fee_start_s": 32400, "fee_end_s": 72000, "resident_exempt": False, **changes}
    return cost.ZoneTariff(**fields)


def test_schema_5_zone_entries_carry_ev_free_minutes_as_an_integer_or_null():
    # Spec v3 A5: the key is always present (null = no exemption), so the Java reader never has to guess an absent key.
    assert te.zone_to_json(_street_zone_fields())["ev_free_minutes"] is None
    assert te.zone_to_json(_street_zone_fields(ev_free_minutes=240))["ev_free_minutes"] == 240
    assert list(te.zone_to_json(_street_zone_fields()))[-1] == "ev_free_minutes"
    # a zone entry reads back into the same tariff (the Java reader's contract)
    entry = te.zone_to_json(_street_zone_fields(ev_free_minutes=240))
    assert ZoneTariff(zone_id="z", **entry) == _street_zone_fields(ev_free_minutes=240)


def _ev_row(table, valid_until, minutes=240):
    return _row(table, "fx_bs_ia", ev_free_minutes=minutes, ev_rule_valid_until=valid_until)


def test_an_ev_rule_in_force_on_the_snapshot_date_is_exported_and_an_expired_one_is_switched_off_by_name(table, caplog):
    # Spec v3 A5: the exemption is exported when ev_rule_valid_until is empty or on or after the tariff snapshot date
    # (2026-09-28 here); an expired rule is exported as null with an info line that names the zone and the expiry, never
    # silently. The boundary day itself is in force.
    logger = "braunschweig.parking.tariff_export"
    for valid_until in (None, "", "2026-09-28", "2026-09-29", "2026-12-31", "2028-04-30"):
        assert te.tariff_row_to_zone(_ev_row(table, valid_until), snapshot_date=SNAPSHOT_DATE).ev_free_minutes == 240, valid_until
    caplog.clear()
    with caplog.at_level("INFO", logger=logger):
        for valid_until in ("2026-09-27", "2025-12-31"):
            assert te.tariff_row_to_zone(_ev_row(table, valid_until), snapshot_date=SNAPSHOT_DATE).ev_free_minutes is None
    messages = [record.getMessage() for record in caplog.records if record.name == logger]
    assert len(messages) == 2
    assert all("zone 'fx_bs_ia'" in message and "ev_free_minutes 240" in message and "2026-09-28" in message
               for message in messages)
    assert "2026-09-27" in messages[0] and "expired" in messages[0] and "no exemption" in messages[0]
    # a rule without any validity date states nothing to log
    caplog.clear()
    with caplog.at_level("INFO", logger=logger):
        te.tariff_row_to_zone(_ev_row(table, None), snapshot_date=SNAPSHOT_DATE)
    assert not [record for record in caplog.records if record.name == logger]


def test_an_ev_validity_date_without_the_snapshot_date_is_a_structural_check_and_a_wrong_form_is_refused(table):
    # Without the snapshot date (the zones stage and the validation script write no model) the date is checked for its form but
    # not compared: the minutes stay as stated, also for a date that would be expired. build_tariff_model always passes its
    # date, so no model carries such a conversion (test_the_table_export_writes_ev_free_minutes_per_zone_...).
    assert te.tariff_row_to_zone(_ev_row(table, "2026-12-31"), snapshot_date=None).ev_free_minutes == 240
    assert te.tariff_row_to_zone(_ev_row(table, "2020-01-01"), snapshot_date=None).ev_free_minutes == 240
    for bad in ("31.12.2026", "2026-12-32", "2026-9-1", "soon", 20261231):
        with pytest.raises(ValueError, match="'fx_bs_ia': ev_rule_valid_until must be an ISO date YYYY-MM-DD"):
            te.tariff_row_to_zone(_ev_row(table, bad), snapshot_date=None)
    for bad in ("31.12.2026", "2026-12-32", "2026-9-1", "soon", 20261231):
        with pytest.raises(ValueError, match="'fx_bs_ia': ev_rule_valid_until must be an ISO date YYYY-MM-DD"):
            te.tariff_row_to_zone(_ev_row(table, bad), snapshot_date=SNAPSHOT_DATE)
    with pytest.raises(ValueError, match="'fx_bs_ia': ev_rule_valid_until .* needs ev_free_minutes"):
        te.tariff_row_to_zone(_ev_row(table, "2026-12-31", minutes=None), snapshot_date=SNAPSHOT_DATE)
    # without a validity date the snapshot date is not needed (the rule is open-ended)
    assert te.tariff_row_to_zone(_ev_row(table, None), snapshot_date=None).ev_free_minutes == 240


def test_the_table_export_writes_ev_free_minutes_per_zone_and_applies_the_snapshot_date(table, sources, caplog):
    table = table[~table["zone_id"].isin(V3_ZONE_IDS)]       # without the fixture's own EV rows (their export: test below)
    stated = table.copy()
    stated.loc[stated["zone_id"] == "fx_bs_ia", ["ev_free_minutes", "ev_rule_valid_until"]] = [240, "2026-12-31"]
    stated.loc[stated["zone_id"] == "fx_sz", ["ev_free_minutes", "ev_rule_valid_until"]] = [180, "2026-09-27"]
    stated.loc[stated["zone_id"] == "fx_bs_ib", "ev_free_minutes"] = 90
    with caplog.at_level("INFO", logger="braunschweig.parking.tariff_export"):
        model = te.build_tariff_model(stated, snapshot_date=SNAPSHOT_DATE, sources=sources)
    assert {zone_id: zone["ev_free_minutes"] for zone_id, zone in model["zones"].items()
            if zone["ev_free_minutes"] is not None} == {"fx_bs_ia": 240, "fx_bs_ib": 90}
    assert model["zones"]["fx_sz"]["ev_free_minutes"] is None
    assert any("zone 'fx_sz'" in record.getMessage() and "expired" in record.getMessage() for record in caplog.records)
    # the model lists E1 and E2 because a zone carries an exemption; the table without any does not
    assert "ASSUMPTION E1" in " ".join(model["assumptions"]) and "ASSUMPTION E2" in " ".join(model["assumptions"])
    plain = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources)
    assert all(zone["ev_free_minutes"] is None for zone in plain["zones"].values())
    assert "ASSUMPTION E1" not in " ".join(plain["assumptions"])
    # a later snapshot date expires the same table row, an earlier one keeps it
    later = te.build_tariff_model(stated, snapshot_date="2027-01-01", sources=sources)
    assert later["zones"]["fx_bs_ia"]["ev_free_minutes"] is None and later["zones"]["fx_bs_ib"]["ev_free_minutes"] == 90
    assert "ASSUMPTION E1" in " ".join(later["assumptions"])      # fx_bs_ib still states one
    earlier = te.build_tariff_model(stated, snapshot_date="2026-01-01", sources=sources)
    assert earlier["zones"]["fx_sz"]["ev_free_minutes"] == 180


def test_the_fixture_table_exports_its_ev_rows_and_switches_the_expired_one_off(model):
    # fx_ev_street_v3 (valid until 2026-12-31) and fx_ev_clip_v3 (open-ended) are in force on the snapshot date 2026-09-28,
    # fx_ev_expired_v3 (valid until 2026-09-27) is not: its 180 min are exported as null
    assert {zone_id: zone["ev_free_minutes"] for zone_id, zone in model["zones"].items()
            if zone["ev_free_minutes"] is not None} == EV_FIXTURE_ZONES
    assert model["zones"]["fx_ev_expired_v3"]["ev_free_minutes"] is None
    assert model["zones"]["fx_ev_clip_v3"]["max_stay_min"] == 150 and model["zones"]["fx_ev_clip_v3"]["ev_free_minutes"] == 240
    assert model["zones"]["fx_ia_nolong_v3"]["max_stay_min"] == 180
    assert model["zones"]["fx_ia_nolong_v3"]["long_stay_product_cents"] is None


def test_the_ev_column_converts_into_the_tariff_field_and_a_campus_row_names_the_column(table):
    row = table.set_index("zone_id").loc["fx_bs_ia"].to_dict() | {"zone_id": "fx_bs_ia", "ev_free_minutes": 240}
    assert te.tariff_row_to_zone(row, snapshot_date=None).ev_free_minutes == 240
    assert te.tariff_row_to_zone({**row, "ev_free_minutes": None}, snapshot_date=None).ev_free_minutes is None
    campus = table.set_index("zone_id").loc["fx_campus"].to_dict() | {"zone_id": "fx_campus", "ev_free_minutes": 240}
    with pytest.raises(ValueError, match="ev_free_minutes does not apply to zone_type 'campus'"):
        te.tariff_row_to_zone(campus, snapshot_date=None)


# ------------------------------------------------------------- schema 5: option_price_beta_u_per_eur (spec v3 A2, R-A-7)


def test_the_model_carries_the_option_price_coefficient_as_a_float(table, sources):
    for value in (0.310998, 0.0, 1, 2.5):
        model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, option_price_beta_u_per_eur=value)
        assert model["option_price_beta_u_per_eur"] == value and isinstance(model["option_price_beta_u_per_eur"], float)
    # the document is plain JSON: the value survives the writer byte for byte
    model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, option_price_beta_u_per_eur=0.310998)
    assert json.loads(json.dumps(model))["option_price_beta_u_per_eur"] == 0.310998


@pytest.mark.parametrize("bad", [-0.1, float("nan"), float("inf"), -float("inf"), True, "0.31", None],
                         ids=["negative", "nan", "infinite", "negative_infinite", "boolean", "text", "none"])
def test_a_bad_option_price_coefficient_is_refused_by_name(table, sources, bad):
    with pytest.raises(ValueError, match="option_price_beta_u_per_eur must be a finite number >= 0"):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, option_price_beta_u_per_eur=bad)


def test_the_python_stand_in_reader_of_the_coefficient_follows_the_schema_rule(table, sources):
    # a schema-4 model has no coefficient and its weights carry no price (0.0, as schema 4 always did); from schema 5 on the key
    # is required and never defaulted
    model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, option_price_beta_u_per_eur=0.310998)
    assert te.option_price_beta_from_model(model) == 0.310998
    schema_4 = {key: value for key, value in model.items() if key != "option_price_beta_u_per_eur"} | {"schema_version": 4}
    assert te.option_price_beta_from_model(schema_4) == 0.0
    with pytest.raises(ValueError, match="has no option_price_beta_u_per_eur"):
        te.option_price_beta_from_model({**schema_4, "schema_version": 5})


def test_the_committed_fixture_model_carries_the_option_price_coefficient_of_the_mode_choice_cost():
    # 0.310998 is the magnitude of BraunschweigModeParameters.betaCost_u_MU (-0.310998, eqasim-java-bs); the Java startup check
    # of Task A7 refuses a tariff model whose value differs by more than 1e-9
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert committed["option_price_beta_u_per_eur"] == 0.310998


# --------------------------------------------------------------- schema 5: garage max_stay_min (spec v3 A1, R-A-5)


def test_schema_5_writes_the_garage_maximum_stay_and_refuses_schema_4_readers_silently_dropping_it(garage_frame):
    # The dataset has the max_stay_min column since spec v3 B (ruling R-B-2); the fixture file is a legacy layout without it, so the
    # loaded fixture frame carries it empty and an entry then carries null; where a dataset frame carries a value, it is written as an
    # integer number of minutes. The key set is exactly GARAGE_KEYS either way, so a schema-4 reader (which rejects an unknown
    # key, like the Java one) cannot silently drop a maximum.
    assert garage_frame["max_stay_min"].isna().all()
    assert all(entry["max_stay_min"] is None and sorted(entry) == GARAGE_KEYS for entry in te.garage_entries(garage_frame))
    with_maximum = garage_frame.copy()
    with_maximum["max_stay_min"] = pd.array([pd.NA] * len(with_maximum), dtype="Int64")
    with_maximum.loc[with_maximum["garage_id"] == "fx_g01_core", "max_stay_min"] = 120
    entries = {entry["garage_id"]: entry for entry in te.garage_entries(with_maximum)}
    assert entries["fx_g01_core"]["max_stay_min"] == 120 and type(entries["fx_g01_core"]["max_stay_min"]) is int
    assert entries["fx_g02_a"]["max_stay_min"] is None and all(sorted(entry) == GARAGE_KEYS for entry in entries.values())
    # a float column (a CSV read) converts the same way
    as_float = garage_frame.copy()
    as_float["max_stay_min"] = [120.0 if garage_id == "fx_g01_core" else float("nan") for garage_id in as_float["garage_id"]]
    assert {entry["garage_id"]: entry["max_stay_min"] for entry in te.garage_entries(as_float)}["fx_g01_core"] == 120
    # the entry reads back into the same garage (the Java reader's contract); a schema-4 entry (no key) is refused
    [entry] = [entry for entry in te.garage_entries(with_maximum) if entry["garage_id"] == "fx_g01_core"]
    from braunschweig.parking import cost
    assert cost.GarageTariff.from_json(entry).max_stay_min == 120 and cost.GarageTariff.from_json(entry).to_json() == entry
    schema_4_entry = {key: value for key, value in entry.items() if key != "max_stay_min"}
    with pytest.raises(ValueError, match="keys .* differ from the documented"):
        cost.GarageTariff.from_json(schema_4_entry)


def test_the_export_logs_how_many_garages_carry_a_maximum_stay(table, sources, garage_frame, caplog):
    # Fallback transparency: a dataset without the column exports every garage as unlimited and says so, with the count
    logger = "braunschweig.parking.tariff_export"
    with caplog.at_level("INFO", logger=logger):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=garage_frame)
    [line] = [record.getMessage() for record in caplog.records if "maximum stay (spec v3 A1" in record.getMessage()]
    assert "0 of 38 exported garages carry max_stay_min" in line
    with_maximum = garage_frame.copy()
    with_maximum["max_stay_min"] = [90 if garage_id in ("fx_g01_core", "fx_g02_a") else None
                                    for garage_id in with_maximum["garage_id"]]
    caplog.clear()
    with caplog.at_level("INFO", logger=logger):
        te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources, garages=with_maximum)
    [line] = [record.getMessage() for record in caplog.records if "maximum stay (spec v3 A1" in record.getMessage()]
    assert "2 of 38 exported garages carry max_stay_min (fx_g01_core, fx_g02_a)" in line


@pytest.mark.parametrize("bad", [0, -5, 1.5, True, "soon"], ids=["zero", "negative", "fractional", "boolean", "text"])
def test_a_garage_maximum_stay_that_is_no_positive_whole_number_of_minutes_is_refused(garage_frame, bad):
    broken = garage_frame.copy()
    broken["max_stay_min"] = [bad if garage_id == "fx_g01_core" else None for garage_id in broken["garage_id"]]
    with pytest.raises(ValueError, match="max_stay_min"):
        te.garage_entries(broken)


def test_the_committed_fixture_model_has_every_schema_5_key(model):
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert sorted(committed) == sorted(model)
    assert all(sorted(entry) == GARAGE_KEYS for entry in committed["garages"])
    assert all("ev_free_minutes" in zone for zone in committed["zones"].values())


def test_tariff_row_to_zone_requires_the_snapshot_date_so_an_expired_ev_rule_cannot_ship_by_omission(table):
    # keyword-only and without a default: omitting it is a TypeError, the structural callers pass None explicitly
    with pytest.raises(TypeError, match="snapshot_date"):
        te.tariff_row_to_zone(_ev_row(table, "2020-01-01"))
    with pytest.raises(TypeError):
        te.tariff_row_to_zone(_ev_row(table, "2020-01-01"), SNAPSHOT_DATE)
    assert te.tariff_row_to_zone(_ev_row(table, "2020-01-01"), snapshot_date=None).ev_free_minutes == 240
    assert te.tariff_row_to_zone(_ev_row(table, "2020-01-01"), snapshot_date=SNAPSHOT_DATE).ev_free_minutes is None
