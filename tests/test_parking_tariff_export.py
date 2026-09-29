"""The parking tariff JSON export (schema 1, spec 5.4) and its committed fixture model (issue #249)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.parking import tariff_export as te
from braunschweig.parking.cost import ZoneTariff

FIXTURE_JSON = Path(__file__).resolve().parent / "fixtures" / "parking" / "parking_tariffs_fixture.json"
SNAPSHOT_DATE = "2026-09-28"
FIXTURE_ZONE_IDS = {"fx_bs_ia", "fx_bs_ib", "fx_sz", "fx_wob", "fx_pe", "fx_res_a", "fx_campus"}
# Table column in euros -> JSON field in cents, written out here independently of the implementation.
EURO_FIELDS = {"hourly_rate_eur": "hourly_rate_cents", "first_period_eur": "first_period_cents",
               "daily_cap_eur": "daily_cap_cents", "long_stay_product_eur": "long_stay_product_cents",
               "member_day_eur": "member_day_cents", "guest_day_eur": "guest_day_cents"}
MINUTE_FIELDS = ("billing_unit_min", "free_if_stay_at_most_min", "first_period_min", "max_stay_min")
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


def test_the_model_has_the_schema_1_header_and_the_seven_fixture_zones(model):
    assert model["schema_version"] == 1
    assert model["tariff_snapshot_date"] == SNAPSHOT_DATE
    assert model["currency"] == "EUR" and model["weekday_only"] is True
    assert model["terminal_stay_rule"] == "until_fee_end"
    assert set(model["zones"]) == FIXTURE_ZONE_IDS


def test_the_documented_zone_entry_is_reproduced_field_by_field(model):
    # The zone entry of the JSON fixture shape in the plan (schema of spec 5.4).
    assert model["zones"]["fx_bs_ia"] == {
        "zone_type": "street_paid", "hourly_rate_cents": 180, "billing_unit_min": 1,
        "free_if_stay_at_most_min": None, "first_period_min": None, "first_period_cents": None,
        "daily_cap_cents": None, "max_stay_min": 180, "long_stay_product_cents": 900,
        "member_day_cents": None, "guest_day_cents": None,
        "fee_start_s": 32400, "fee_end_s": 72000, "resident_exempt": False}


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
        assert zone["fee_start_s"] == int(round(float(row["fee_start_h"]) * 3600)), row["zone_id"]
        assert zone["fee_end_s"] == int(round(float(row["fee_end_h"]) * 3600)), row["zone_id"]
    assert (model["zones"]["fx_sz"]["fee_start_s"], model["zones"]["fx_wob"]["fee_end_s"]) == (36000, 86400)


def test_the_assumptions_render_the_register_of_spec_section_7(model):
    assumptions = model["assumptions"]
    assert assumptions and all(re.fullmatch(r"ASSUMPTION [A-Z][0-9]: .+", text) for text in assumptions)
    assert [text.split(":")[0] for text in assumptions] == [
        f"ASSUMPTION {assumption_id}" for assumption_id in ("Z1", "D1", "T1", "M1", "A1", "C1", "R1", "H1", "F1", "S1")]


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


def test_the_committed_fixture_tariff_model_is_in_sync(table, sources):
    from scripts.export_parking_golden_cases import build_fixture_tariff_model
    committed = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    assert committed == build_fixture_tariff_model(table, sources), (
        "regenerate with: python scripts/export_parking_golden_cases.py")
