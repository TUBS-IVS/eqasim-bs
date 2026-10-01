"""The parking tariff JSON export (schema 2, spec 5.4 plus the v2 columns of issue #436) and its committed fixture
model (issue #249)."""
from __future__ import annotations

import csv
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
               "fx_campus_tie_v2", "fx_res_garage_v2"}
FIXTURE_ZONE_IDS = V1_ZONE_IDS | V2_ZONE_IDS
# Table column in euros -> JSON field in cents, written out here independently of the implementation.
EURO_FIELDS = {"hourly_rate_eur": "hourly_rate_cents", "first_period_eur": "first_period_cents",
               "daily_cap_eur": "daily_cap_cents", "long_stay_product_eur": "long_stay_product_cents",
               "member_day_eur": "member_day_cents", "guest_day_eur": "guest_day_cents",
               "garage_hourly_rate_eur": "garage_hourly_rate_cents",
               "garage_first_period_eur": "garage_first_period_cents",
               "garage_daily_cap_eur": "garage_daily_cap_cents", "commuter_day_eur": "commuter_day_cents"}
MINUTE_FIELDS = ("billing_unit_min", "free_if_stay_at_most_min", "first_period_min", "max_stay_min",
                 "garage_billing_unit_min", "garage_first_period_min", "search_time_min")
# The table columns schema 2 adds (all optional) and the JSON fields they become; null where absent or empty.
V2_COLUMNS = ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_first_period_min", "garage_first_period_eur",
              "garage_daily_cap_eur", "garage_fee_start_h", "garage_fee_end_h", "commuter_day_eur", "search_time_min")
V2_FIELDS = ("garage_hourly_rate_cents", "garage_billing_unit_min", "garage_first_period_min",
             "garage_first_period_cents", "garage_daily_cap_cents", "garage_fee_start_s", "garage_fee_end_s",
             "commuter_day_cents", "search_time_min")
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


def test_the_model_has_the_schema_2_header_and_the_fixture_zones(model):
    assert model["schema_version"] == 2
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
        "fee_start_s": 32400, "fee_end_s": 72000, "resident_exempt": False, **dict.fromkeys(V2_FIELDS)}
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
        "garage_fee_end_s": 86400, "commuter_day_cents": 376, "search_time_min": 5}


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
            assert zone[field] == (None if _is_empty(row[field]) else int(float(row[field]))), (row["zone_id"], field)
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


def test_the_assumptions_render_the_register_of_spec_section_7_and_the_product_minimum(model):
    # Spec section 7 (v1) followed by P1 (the cheapest usable product) and P2 (the commuter product per working day)
    # of the v2 spec, lever 2.
    assumptions = model["assumptions"]
    assert assumptions and all(re.fullmatch(r"ASSUMPTION [A-Z][0-9]: .+", text) for text in assumptions)
    assert [text.split(":")[0] for text in assumptions] == [
        f"ASSUMPTION {assumption_id}"
        for assumption_id in ("Z1", "D1", "T1", "M1", "A1", "C1", "R1", "H1", "F1", "S1", "P1", "P2")]


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
                                      daily_cap_eur="", resident_exempt="FALSE"))
    assert isinstance(zone, ZoneTariff)
    assert (zone.hourly_rate_cents, zone.billing_unit_min, zone.daily_cap_cents, zone.resident_exempt) == (
        100, 6, None, False)


@pytest.mark.parametrize("changes, message", [
    ({"hourly_rate_eur": 1.805}, "whole number of cents"),
    ({"billing_unit_min": 1.5}, "whole number of minutes"),
    ({"resident_exempt": "yes"}, "resident_exempt must be true or false"),
    ({"resident_exempt": None}, "resident_exempt must be true or false"),
    ({"fee_start_h": None}, "fee_start_h is required"),
    ({"hourly_rate_eur": True}, "hourly_rate_eur must be a number"),
    ({"zone_id": " fx_bs_ia"}, "zone_id"),
])
def test_row_conversion_rejects_cells_it_cannot_convert_exactly(table, changes, message):
    with pytest.raises(ValueError, match=message):
        te.tariff_row_to_zone(_row(table, "fx_bs_ia", **changes))


@pytest.mark.parametrize("prefix, zone_id", [("", "fx_bs_ia"), ("garage_", "fx_bs_ib_v2")], ids=["street", "garage"])
@pytest.mark.parametrize("fee_start_h, fee_end_h", [(20.0, 9.0), (9.0, 9.0), (9.0, 24.5)],
                         ids=["reversed", "zero_length", "past_midnight"])
def test_a_fee_window_error_names_the_hour_columns_of_the_table(table, prefix, zone_id, fee_start_h, fee_end_h):
    # The table holds decimal hours; the message must speak in those columns, not in the derived seconds.
    start, end = f"{prefix}fee_start_h", f"{prefix}fee_end_h"
    with pytest.raises(ValueError) as error:
        te.tariff_row_to_zone(_row(table, zone_id, **{start: fee_start_h, end: fee_end_h}))
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
        te.tariff_row_to_zone(_row(table, zone_id, **changes))
    message = str(error.value)
    assert message.startswith(f"tariff row {zone_id!r}: {column} does not apply to zone_type ")
    assert "leave the cell empty" in message


def test_a_daily_cap_below_the_first_period_price_is_rejected_for_the_row(table):
    # fx_wob charges a first period of 1.10 EUR; a cap of 1.00 EUR contradicts it.
    with pytest.raises(ValueError, match=r"'fx_wob'.*daily_cap_cents 100 is below first_period_cents 110"):
        te.tariff_row_to_zone(_row(table, "fx_wob", daily_cap_eur=1.00))
    assert te.tariff_row_to_zone(_row(table, "fx_wob", daily_cap_eur=1.10)).daily_cap_cents == 110


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
    """The v1 rows of the fixture CSV in the schema-1 layout: the schema-2 columns removed, every cell kept as text."""
    lines = FIXTURE_CSV.read_text(encoding="utf-8").splitlines()
    comments = [line for line in lines if line.startswith("#")]
    header, *rows = list(csv.reader(line for line in lines if not line.startswith("#")))
    keep = [index for index, column in enumerate(header) if column not in V2_COLUMNS]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows([[header[index] for index in keep]] + [[row[index] for index in keep] for row in rows
                                                              if row[header.index("zone_id")] in V1_ZONE_IDS])
    path.write_text("\n".join(comments) + "\n" + buffer.getvalue(), encoding="utf-8")
    return path


def test_a_schema_1_table_loads_and_exports_as_schema_2_and_prices_the_v1_cases_unchanged(tmp_path, model, sources):
    """Review focus 1 (Python): a schema-1 CSV, without any of the nine schema-2 columns, still loads, validates and
    exports; every schema-2 field is null, every zone equals its schema-2 fixture entry, and G01..G38, L01..L08 and
    L01Z..L08Z price on it exactly as their golden values say."""
    path = _schema_1_csv(tmp_path / "schema_1_tariffs.csv")
    header = next(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#"))
    assert header.split(",") == list(pz.SCHEMA_1_TARIFF_COLUMNS)
    table = pz.load_tariffs(path)
    assert list(table.columns) == list(pz.TARIFF_COLUMNS) and table[list(V2_COLUMNS)].isna().all().all()
    pz.validate_tariffs(table, allow_fixture_marker=True)
    schema_1_model = te.build_tariff_model(table, snapshot_date=SNAPSHOT_DATE, sources=sources)
    assert schema_1_model["schema_version"] == 2
    assert schema_1_model["zones"] == {zone_id: model["zones"][zone_id] for zone_id in V1_ZONE_IDS}
    assert all(zone[field] is None for zone in schema_1_model["zones"].values() for field in V2_FIELDS)
    # A frame that lacks the schema-2 columns altogether (not read through the loader) exports the same zones.
    assert te.build_tariff_model(table.drop(columns=list(V2_COLUMNS)), snapshot_date=SNAPSHOT_DATE,
                                 sources=sources)["zones"] == schema_1_model["zones"]
    from scripts.export_parking_golden_cases import zones_from_model
    v1_cases = [case for case in GOLDEN_CASES if not case["id"].startswith("V")]
    assert len(v1_cases) == 38 + 8 + 8
    assert golden_case_mismatches(zones_from_model(schema_1_model), v1_cases) == []


def test_a_pre_v2_tariff_model_without_the_schema_2_keys_round_trips_through_zones_from_model(model):
    """A tariff model written before schema 2 has no schema-2 keys at all (not null ones): zones_from_model reads its
    zone entries into the same tariffs as the schema-2 entries with null keys, which export back unchanged."""
    from scripts.export_parking_golden_cases import zones_from_model
    v1_zones = {zone_id: model["zones"][zone_id] for zone_id in V1_ZONE_IDS}
    pre_v2_zones = {zone_id: {key: value for key, value in zone.items() if key not in V2_FIELDS}
                    for zone_id, zone in v1_zones.items()}
    assert all(not set(V2_FIELDS) & set(zone) for zone in pre_v2_zones.values())
    tariffs = zones_from_model({"zones": pre_v2_zones})
    assert tariffs == zones_from_model({"zones": v1_zones})
    assert {zone_id: te.zone_to_json(tariff) for zone_id, tariff in tariffs.items()} == v1_zones


def test_the_committed_fixture_tariff_model_is_in_sync(table, sources):
    from scripts.export_parking_golden_cases import build_fixture_tariff_model
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert committed == build_fixture_tariff_model(table, sources), (
        "regenerate with: python scripts/export_parking_golden_cases.py")
