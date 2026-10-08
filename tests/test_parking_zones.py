"""Loader, validators and zone assignment of the parking cost zones (issue #249, design spec 3.1, 5.3).

The fixture tariff set under ``tests/fixtures/parking`` pins arithmetic, not truth (plan section
"Fixture tariff set and golden cases"); the committed real data is checked by
``test_committed_parking_data_is_valid`` and ``scripts/validate_parking_zones.py``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon, box

from braunschweig.parking import zones as pz

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "parking"
TARIFF_FIXTURE = FIXTURES / "parking_tariffs_fixture.csv"
ZONE_FIXTURE = FIXTURES / "parking_zones_fixture.geojson"
OVERPASS_FIXTURE = FIXTURES / "overpass_fee_tags_fixture.json"


def _write_tariffs(path: Path, frame: pd.DataFrame, header_lines=("# test tariffs",)) -> Path:
    """Write a tariff frame back to CSV the way the committed file is laid out."""
    text = frame.copy()
    for column in pz.MINUTE_COLUMNS:
        if column in text.columns:
            text[column] = text[column].astype("object").where(text[column].notna(), "")
    text["resident_exempt"] = text["resident_exempt"].map({True: "true", False: "false"})
    if "resident_permits_valid" in text.columns:
        text["resident_permits_valid"] = text["resident_permits_valid"].map({True: "true", False: "false"}).fillna("")
    body = text.to_csv(index=False, lineterminator="\n")
    path.write_text("\n".join(header_lines) + "\n" + body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- tariff table


def test_fixture_tariffs_load_typed():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    assert list(tariffs["zone_id"]) == ["fx_bs_ia", "fx_bs_ib", "fx_sz", "fx_wob", "fx_pe", "fx_res_a", "fx_campus",
                                        "fx_frac", "fx_bs_ib_v2", "fx_bs_ia_v2", "fx_wob_v2", "fx_campus_v2",
                                        "fx_garage_window_v2", "fx_capped_street_v2", "fx_campus_tie_v2",
                                        "fx_res_garage_v2", "fx_bga_v2", "fx_res_nopermit_v2"]
    row = tariffs.set_index("zone_id").loc["fx_sz"]
    assert row["first_period_min"] == 60 and row["first_period_eur"] == pytest.approx(0.70)
    assert pd.isna(row["max_stay_min"])
    assert bool(row["resident_exempt"]) is False
    assert list(tariffs.columns) == list(pz.TARIFF_COLUMNS)
    assert str(tariffs["billing_unit_min"].dtype) == "Int64"
    assert tariffs["fee_start_h"].dtype == float and tariffs["hourly_rate_eur"].dtype == float
    assert tariffs.set_index("zone_id").loc["fx_res_a", "resident_exempt"] == True  # noqa: E712
    assert tariffs.set_index("zone_id").loc["fx_sz", "workplace_class"] == "03102"
    assert tariffs.set_index("zone_id").loc["fx_pe", "municipality_ags"] == "03157006"
    # Schema 2: money and hours as float, minutes (including the search time) as Int64; empty on every v1 row.
    assert all(str(tariffs[column].dtype) == "Int64"
               for column in ("garage_billing_unit_min", "garage_first_period_min", "search_time_min"))
    assert all(tariffs[column].dtype == float for column in ("garage_hourly_rate_eur", "garage_first_period_eur",
                                                             "garage_daily_cap_eur", "garage_fee_start_h",
                                                             "garage_fee_end_h", "commuter_day_eur"))
    v2 = tariffs.set_index("zone_id").loc["fx_bs_ib_v2"]
    assert (v2["garage_hourly_rate_eur"], v2["garage_first_period_min"], v2["garage_fee_end_h"]) == (1.20, 60, 24.0)
    assert (v2["commuter_day_eur"], v2["search_time_min"]) == (3.76, 5)
    assert tariffs.set_index("zone_id").loc["fx_campus_v2", "search_time_min"] == 0
    assert tariffs.loc[~tariffs["zone_id"].str.endswith("_v2"), list(pz.OPTIONAL_TARIFF_COLUMNS)].isna().all().all()
    # Spec amendment A6: a garage without a day cap (empty cell) is a valid garage, priced without a cap.
    uncapped = tariffs.set_index("zone_id").loc["fx_garage_window_v2"]
    assert pd.isna(uncapped["garage_daily_cap_eur"]) and uncapped[list(pz.GARAGE_CORE_COLUMNS)].notna().all()


def test_fixture_tariffs_pass_validation():
    pz.validate_tariffs(pz.load_tariffs(TARIFF_FIXTURE))


def test_resident_permits_valid_is_an_optional_nullable_boolean_column(tmp_path):
    # Spec Amendment C3, ASSUMPTION R2-a: empty = the default of the zone type, so the loader keeps "not stated" apart
    # from false instead of turning the empty cell into either.
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    assert "resident_permits_valid" in pz.OPTIONAL_TARIFF_COLUMNS and list(tariffs.columns) == list(pz.TARIFF_COLUMNS)
    assert str(tariffs["resident_permits_valid"].dtype) == "boolean"
    stated = tariffs.set_index("zone_id")["resident_permits_valid"].dropna()
    assert {zone_id: bool(value) for zone_id, value in stated.items()} == {"fx_bga_v2": False,
                                                                           "fx_res_nopermit_v2": False}
    # true and false survive a round trip through the CSV; a table that predates the column reads it as empty
    stated_true = tariffs.copy()
    stated_true.loc[stated_true["zone_id"] == "fx_bs_ia", "resident_permits_valid"] = True
    loaded = pz.load_tariffs(_write_tariffs(tmp_path / "stated.csv", stated_true)).set_index("zone_id")
    assert loaded.loc["fx_bs_ia", "resident_permits_valid"] == True  # noqa: E712
    assert loaded.loc["fx_bga_v2", "resident_permits_valid"] == False  # noqa: E712
    assert loaded.loc["fx_sz", "resident_permits_valid"] is pd.NA
    older = pz.load_tariffs(_write_tariffs(tmp_path / "older.csv", tariffs.drop(columns=["resident_permits_valid"])))
    assert older["resident_permits_valid"].isna().all() and list(older.columns) == list(pz.TARIFF_COLUMNS)


def test_resident_permits_valid_accepts_only_the_literals_true_false_or_empty(tmp_path):
    text = TARIFF_FIXTURE.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("fx_bga_v2,"))
    assert row.endswith(",false")
    path = tmp_path / "t.csv"
    path.write_text(text.replace(row, row[:-len("false")] + "no"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"zone 'fx_bga_v2': resident_permits_valid = 'no'; use the literal 'true' or "
                                         r"'false', or leave the cell empty"):
        pz.load_tariffs(path)


_GARAGE_EMPTY = dict.fromkeys(("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_first_period_min",
                               "garage_first_period_eur", "garage_daily_cap_eur", "garage_fee_start_h",
                               "garage_fee_end_h"), float("nan"))


@pytest.mark.parametrize("zone_id, changes, field", [
    # Spec amendment A6: the garage core (rate, unit, fee window) is all-or-none; the day cap and the first-period
    # pair are optional, but only with the core.
    ("fx_bs_ib_v2", {"garage_billing_unit_min": float("nan")}, "garage_billing_unit_min"),
    ("fx_bs_ib_v2", {"garage_fee_end_h": float("nan")}, "garage_fee_end_h"),
    ("fx_bs_ib", {"garage_daily_cap_eur": 9.60}, "garage_daily_cap_eur"),
    ("fx_bs_ib_v2", {"garage_first_period_eur": float("nan")}, "garage_first_period_eur"),
    ("fx_bs_ib", {"garage_first_period_min": 60, "garage_first_period_eur": 1.20}, "garage_first_period_min"),
    ("fx_bs_ib_v2", {"garage_fee_start_h": 20.0, "garage_fee_end_h": 9.0}, "garage_fee_start_h"),
    ("fx_bs_ib_v2", {"garage_billing_unit_min": 0}, "garage_billing_unit_min"),
    ("fx_bs_ib_v2", {"garage_daily_cap_eur": 0.0}, "garage_daily_cap_eur"),
    # A maximum stay without a long-stay product needs the garage: otherwise a longer stay would have no product.
    ("fx_bs_ia_v2", _GARAGE_EMPTY, "long_stay_product_eur"),
    # The commuter product exists on street_paid and campus rows only (A4) and is never negative.
    ("fx_res_a", {"commuter_day_eur": 3.76}, "commuter_day_eur"),
    ("fx_bs_ib_v2", {"commuter_day_eur": -1.0}, "commuter_day_eur"),
    # A campus stay pays a day product, never a garage.
    ("fx_campus", {"garage_hourly_rate_eur": 1.20, "garage_billing_unit_min": 60, "garage_daily_cap_eur": 9.60,
                   "garage_fee_start_h": 0.0, "garage_fee_end_h": 24.0}, "garage_hourly_rate_eur"),
    # The search time is a whole number of minutes >= 0 (0 is valid, fx_campus_v2).
    ("fx_bs_ib_v2", {"search_time_min": -1}, "search_time_min"),
    # Spec Amendment C3 (R2-a): a campus car park honours no resident permit, so only empty or false is valid there.
    ("fx_campus", {"resident_permits_valid": True}, "resident_permits_valid"),
], ids=["garage_core_without_unit", "garage_core_without_window_end", "garage_cap_without_core",
        "garage_first_period_unpaired", "garage_first_period_without_core", "garage_window_reversed",
        "garage_unit_zero", "garage_cap_zero", "max_stay_without_any_long_stay_product", "commuter_in_resident_zone",
        "commuter_negative", "garage_on_campus", "search_time_negative", "permits_valid_on_campus"])
def test_the_schema_2_columns_are_validated_per_row(zone_id, changes, field):
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    for column, value in changes.items():
        broken.loc[broken["zone_id"] == zone_id, column] = value
    with pytest.raises(ValueError, match=rf"zone '{zone_id}': {field}: "):
        pz.validate_tariffs(broken)


def test_hash_inside_a_field_is_not_a_comment(tmp_path):
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    tariffs.loc[0, "source_url"] = "https://www.braunschweig.de/plan/#parken"
    loaded = pz.load_tariffs(_write_tariffs(tmp_path / "t.csv", tariffs))
    assert loaded.loc[0, "source_url"] == "https://www.braunschweig.de/plan/#parken"


def test_literal_booleans_only(tmp_path):
    text = TARIFF_FIXTURE.read_text(encoding="utf-8").replace(",false,fixture,2026-09-28", ",no,fixture,2026-09-28", 1)
    path = tmp_path / "t.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="resident_exempt"):
        pz.load_tariffs(path)


def test_missing_or_unexpected_columns_raise(tmp_path):
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    with pytest.raises(ValueError, match="notes"):
        pz.load_tariffs(_write_tariffs(tmp_path / "a.csv", tariffs.drop(columns=["notes"])))
    with pytest.raises(ValueError, match="parking_colour"):
        pz.load_tariffs(_write_tariffs(tmp_path / "b.csv", tariffs.assign(parking_colour="red")))


def test_cross_validate_rejects_missing_and_orphan_rows(tmp_path):
    zones = pz.load_zone_polygons(ZONE_FIXTURE)
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    pz.cross_validate(zones, tariffs)
    with pytest.raises(ValueError, match="fx_campus"):
        pz.cross_validate(zones[zones["zone_id"] != "fx_campus"], tariffs)
    with pytest.raises(ValueError, match="fx_bs_ia"):
        pz.cross_validate(zones, tariffs[tariffs["zone_id"] != "fx_bs_ia"])


def test_required_fields_per_type_are_enforced(tmp_path):
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_bs_ia", "long_stay_product_eur"] = float("nan")
    with pytest.raises(ValueError, match="long_stay_product_eur"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_campus", "member_day_eur"] = float("nan")
    with pytest.raises(ValueError, match="member_day_eur"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_bs_ib", "fee_start_h"] = 21.0
    with pytest.raises(ValueError, match="fee_start_h"):
        pz.validate_tariffs(broken)


def test_paired_fields_money_and_ids_are_enforced():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_sz", "first_period_eur"] = float("nan")
    with pytest.raises(ValueError, match="first_period_eur"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_bs_ib", "long_stay_product_eur"] = 9.0
    with pytest.raises(ValueError, match="max_stay_min"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_wob", "daily_cap_eur"] = -1.0
    with pytest.raises(ValueError, match="daily_cap_eur"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_pe", "zone_id"] = "fx_sz"
    with pytest.raises(ValueError, match="duplicate"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_bs_ia", "billing_unit_min"] = 0
    with pytest.raises(ValueError, match="billing_unit_min"):
        pz.validate_tariffs(broken)


def test_resident_zone_needs_exemption_and_zero_rate():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_res_a", "resident_exempt"] = False
    with pytest.raises(ValueError, match="resident_exempt"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_res_a", "hourly_rate_eur"] = 1.0
    with pytest.raises(ValueError, match="hourly_rate_eur"):
        pz.validate_tariffs(broken)


def test_resident_zone_rejects_threshold_and_day_cap():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    for field, value in (("free_if_stay_at_most_min", 30), ("daily_cap_eur", 9.0)):
        broken = tariffs.copy()
        broken.loc[broken["zone_id"] == "fx_res_a", field] = value
        with pytest.raises(ValueError, match=field):
            pz.validate_tariffs(broken)


def test_zero_day_cap_is_rejected():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_wob", "daily_cap_eur"] = 0.0
    with pytest.raises(ValueError, match="daily_cap_eur"):
        pz.validate_tariffs(broken)


def test_campus_rows_carry_no_metering_fields():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_campus", "hourly_rate_eur"] = 1.0
    with pytest.raises(ValueError, match="hourly_rate_eur"):
        pz.validate_tariffs(broken)


def test_unknown_workplace_class_and_zone_type_raise():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy(); broken.loc[0, "workplace_class"] = "mars"
    with pytest.raises(ValueError, match="workplace_class"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy(); broken.loc[0, "zone_type"] = "garage"
    with pytest.raises(ValueError, match="zone_type"):
        pz.validate_tariffs(broken)


def test_workplace_class_must_match_the_county_of_the_municipality():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_sz", "workplace_class"] = "03103"
    with pytest.raises(ValueError, match="workplace_class"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_wob", "municipality_ags"] = "09162000"
    with pytest.raises(ValueError, match="municipality_ags"):
        pz.validate_tariffs(broken)


def test_provenance_fields_and_fee_window_source_are_required():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_sz", "source_url"] = pd.NA
    with pytest.raises(ValueError, match="source_url"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_sz", "fee_window_source"] = "rumour"
    with pytest.raises(ValueError, match="fee_window_source"):
        pz.validate_tariffs(broken)
    broken = tariffs.copy()
    broken.loc[broken["zone_id"] == "fx_sz", "valid_from"] = "01.01.2026"
    with pytest.raises(ValueError, match="valid_from"):
        pz.validate_tariffs(broken)


def test_fixture_marker_can_be_rejected_for_committed_data():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    with pytest.raises(ValueError, match="fixture"):
        pz.validate_tariffs(tariffs, allow_fixture_marker=False)


# --------------------------------------------------------------------------- polygons


def test_fixture_zones_load_in_metric_crs():
    zones = pz.load_zone_polygons(ZONE_FIXTURE)
    assert zones.crs.to_epsg() == 25832 and len(zones) == 18 and zones["zone_id"].is_unique
    for column in pz.ZONE_PROVENANCE_COLUMNS:
        assert zones[column].notna().all(), column


def test_overlapping_zones_are_rejected():
    zones = gpd.GeoDataFrame({"zone_id": ["alpha", "beta"], "geometry": [box(0, 0, 10, 10), box(5, 0, 15, 10)]},
                             crs="EPSG:25832")
    with pytest.raises(ValueError, match="overlap") as raised:
        pz.validate_zone_polygons(zones)
    assert "alpha" in str(raised.value) and "beta" in str(raised.value)


def test_touching_zones_within_tolerance_are_accepted():
    zones = gpd.GeoDataFrame({"zone_id": ["a", "b"], "geometry": [box(0, 0, 10, 10), box(10, 0, 20, 10)]}, crs="EPSG:25832")
    pz.validate_zone_polygons(zones)
    sliver = gpd.GeoDataFrame({"zone_id": ["a", "b"], "geometry": [box(0, 0, 10, 10), box(9.95, 0, 20, 10)]}, crs="EPSG:25832")
    pz.validate_zone_polygons(sliver)  # 0.5 m2 < OVERLAP_TOLERANCE_M2


def test_zone_polygons_need_metric_crs_and_unique_ids():
    wgs84 = gpd.GeoDataFrame({"zone_id": ["a"], "geometry": [box(10.5, 52.2, 10.6, 52.3)]}, crs="EPSG:4326")
    with pytest.raises(ValueError, match="EPSG:25832"):
        pz.validate_zone_polygons(wgs84)
    twins = gpd.GeoDataFrame({"zone_id": ["a", "a"], "geometry": [box(0, 0, 1, 1), box(5, 5, 6, 6)]}, crs="EPSG:25832")
    with pytest.raises(ValueError, match="duplicate"):
        pz.validate_zone_polygons(twins)


def test_load_zone_polygons_requires_provenance(tmp_path):
    zones = gpd.read_file(ZONE_FIXTURE).drop(columns=["digitising_note"])
    path = tmp_path / "zones.geojson"
    zones.to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError, match="digitising_note"):
        pz.load_zone_polygons(path)


def test_load_zone_polygons_tolerates_licence_members(tmp_path):
    document = json.loads(ZONE_FIXTURE.read_text(encoding="utf-8"))
    document["license"] = "ODbL-1.0"
    document["attribution"] = "(c) OpenStreetMap contributors"
    path = tmp_path / "zones.geojson"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert len(pz.load_zone_polygons(path)) == len(document["features"])


def test_load_zone_polygons_repairs_a_self_intersecting_ring(tmp_path, caplog):
    zones = gpd.read_file(ZONE_FIXTURE).to_crs("EPSG:25832")
    x0, y0, x1, y1 = zones.geometry.iloc[0].bounds
    bow_tie = Polygon([(x0, y0), (x1, y1), (x1, y0), (x0, y1)])
    zones.loc[0, "geometry"] = bow_tie
    path = tmp_path / "zones.geojson"
    zones.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
    with caplog.at_level("INFO", logger=pz.__name__):
        repaired = pz.load_zone_polygons(path)
    assert repaired.geometry.is_valid.all()
    assert repaired.geometry.iloc[0].area > 0
    assert "repaired 1" in caplog.text
    # the committed release must be valid as stored (the validator and the assembly pass max_repairs=0)
    with pytest.raises(ValueError, match="1 invalid polygon\\(s\\) would need a repair, at most 0 allowed"):
        pz.load_zone_polygons(path, max_repairs=0)


ERODED_ZONE = "fx_bs_ib"
SNAPSHOT = "2026-09-30T12:00:00Z"


def _erosion_release(tmp_path, *, walk=250.0, timestamp=SNAPSHOT, other_walk=None) -> Path:
    """The fixture polygons with ERODED_ZONE digitised by the lever-1 construction (v2, osm_fee_erosion)."""
    zones = gpd.read_file(ZONE_FIXTURE)
    eroded = zones["zone_id"] == ERODED_ZONE
    zones.loc[eroded, "geometry_source"] = pz.EROSION_GEOMETRY_SOURCE
    zones["unavoidable_walk_m"] = [walk if flag else other_walk for flag in eroded]
    zones["osm_timestamp"] = [timestamp if flag else None for flag in eroded]
    path = tmp_path / "zones.geojson"
    zones.to_file(path, driver="GeoJSON")
    return path


@pytest.mark.parametrize("walk, timestamp, other_walk, message", [
    (250.0, SNAPSHOT, None, None),
    (None, SNAPSHOT, None, "unavoidable_walk_m required"),
    (250.0, None, None, "osm_timestamp required"),
    (-5.0, SNAPSHOT, None, "unavoidable_walk_m must be a positive number"),
    (250.0, "2026-09-30", None, "osm_timestamp must be"),
    (250.0, SNAPSHOT, 250.0, "only applies to geometry_source osm_fee_erosion"),
], ids=["complete", "no_walk", "no_timestamp", "negative_walk", "date_only", "walk_on_other_source"])
def test_osm_fee_erosion_zones_need_walk_and_snapshot_provenance(tmp_path, walk, timestamp, other_walk, message):
    path = _erosion_release(tmp_path, walk=walk, timestamp=timestamp, other_walk=other_walk)
    if message is None:
        zones = pz.load_zone_polygons(path).set_index("zone_id")
        assert zones.loc[ERODED_ZONE, "unavoidable_walk_m"] == 250.0
        assert zones.loc[ERODED_ZONE, "osm_timestamp"] == SNAPSHOT
    else:
        with pytest.raises(ValueError, match=message):
            pz.load_zone_polygons(path)


@pytest.mark.parametrize("buffer_m, reconstructed_m2, message", [
    (50.0, 1000.0, None),
    (None, None, "section_buffer_m required for geometry_source municipal_street_sections_buffered"),
    (0.0, None, "section_buffer_m must be a positive number"),
    (50.0, -1.0, "reconstructed_section_m2 of zone"),
    (50.0, 1e12, "reconstructed_section_m2 of zone"),
], ids=["complete", "no_buffer", "zero_buffer", "negative_reconstruction", "reconstruction_above_the_polygon"])
def test_municipal_zone_provenance_is_validated(tmp_path, buffer_m, reconstructed_m2, message):
    # spec Amendment C: buffered municipal street sections carry their buffer (ASSUMPTION C-a); a polygon digitised
    # from a municipal map may flag the area that rests on a reconstruction (Braunschweig 1a, the 2024 annex)
    zones = gpd.read_file(ZONE_FIXTURE)
    municipal = zones["zone_id"] == "fx_wob"
    zones.loc[municipal, "geometry_source"] = pz.MUNICIPAL_SECTIONS_GEOMETRY_SOURCE
    zones["section_buffer_m"] = [buffer_m if flag else None for flag in municipal]
    zones["reconstructed_section_m2"] = [reconstructed_m2 if zone_id == "fx_bs_ia" else None
                                         for zone_id in zones["zone_id"]]
    path = tmp_path / "zones.geojson"
    zones.to_file(path, driver="GeoJSON")
    if message is None:
        loaded = pz.load_zone_polygons(path).set_index("zone_id")
        assert loaded.loc["fx_wob", "section_buffer_m"] == 50.0
        assert loaded.loc["fx_bs_ia", "reconstructed_section_m2"] == 1000.0
    else:
        with pytest.raises(ValueError, match=message):
            pz.load_zone_polygons(path)


@pytest.mark.parametrize("buffer_m, other_buffer_m, message", [
    (50.0, None, None),
    (None, None, "site_buffer_m required for geometry_source single_site_buffered"),
    (0.0, None, "site_buffer_m must be a positive number"),
    (-50.0, None, "site_buffer_m must be a positive number"),
    (50.0, 50.0, "site_buffer_m only applies to geometry_source single_site_buffered"),
], ids=["complete", "no_buffer", "zero_buffer", "negative_buffer", "buffer_on_other_source"])
def test_single_site_zone_provenance_is_validated(tmp_path, buffer_m, other_buffer_m, message):
    # spec Amendment D3: a paid car park or street section that has no zone map becomes a zone of its own, the area
    # within site_buffer_m of it (ASSUMPTION C-a); the buffer is recorded on that source's polygons only
    zones = gpd.read_file(ZONE_FIXTURE)
    site = zones["zone_id"] == "fx_sz"
    zones.loc[site, "geometry_source"] = pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE
    zones["site_buffer_m"] = [buffer_m if flag else other_buffer_m for flag in site]
    path = tmp_path / "zones.geojson"
    zones.to_file(path, driver="GeoJSON")
    if message is None:
        loaded = pz.load_zone_polygons(path).set_index("zone_id")
        assert loaded.loc["fx_sz", "geometry_source"] == "single_site_buffered"
        assert loaded.loc["fx_sz", "site_buffer_m"] == 50.0 and loaded["site_buffer_m"].drop("fx_sz").isna().all()
    else:
        with pytest.raises(ValueError, match=message):
            pz.load_zone_polygons(path)


@pytest.mark.parametrize("source_name, source", [
    ("CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE", "campus_detection_zones"),
    ("CAMPUS_OUTLINE_AND_DETECTION_ZONES_GEOMETRY_SOURCE", "campus_outline_and_detection_zones"),
], ids=["detection_zones_only", "outline_and_detection_zones"])
def test_campus_sources_are_allowed_geometry_sources_without_a_provenance_column(tmp_path, source_name, source):
    # spec Amendment D1 and ruling R-4a-8: a TU campus zone is the union of the camera detection zones of its campus map
    # (the paid car parks) and, where a v1 outline of the campus grounds exists, of that outline (the destination area);
    # the method is a digitisation, so it takes no rule parameter (the method, the uncertainty and the feature ids are in
    # the note)
    assert getattr(pz, source_name) == source
    assert {source, "single_site_buffered"} <= set(pz.GEOMETRY_SOURCES)
    assert getattr(pz, source_name) not in pz.RULE_PROVENANCE_COLUMNS
    assert pz.RULE_PROVENANCE_COLUMNS[pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE] == ("site_buffer_m",)
    zones = gpd.read_file(ZONE_FIXTURE)
    zones.loc[zones["zone_id"] == "fx_campus", "geometry_source"] = source
    path = tmp_path / "zones.geojson"
    zones.to_file(path, driver="GeoJSON")
    assert pz.load_zone_polygons(path).set_index("zone_id").loc["fx_campus", "geometry_source"] == source
    zones.loc[zones["zone_id"] == "fx_campus", "geometry_source"] = "tu_detection_polygons"  # not a known source
    zones.to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError, match="geometry_source not one of"):
        pz.load_zone_polygons(path)


@pytest.mark.parametrize("zone_id, source, message", [
    ("fx_campus", pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE, None),
    ("fx_campus", pz.CAMPUS_OUTLINE_AND_DETECTION_ZONES_GEOMETRY_SOURCE, None),
    ("fx_sz", pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE, None),
    ("fx_sz", pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE, "campus_detection_zones .* zone_type campus .*fx_sz"),
    ("fx_sz", pz.CAMPUS_OUTLINE_AND_DETECTION_ZONES_GEOMETRY_SOURCE,
     "campus_outline_and_detection_zones .* zone_type campus .*fx_sz"),
    ("fx_campus", pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE, "single_site_buffered .* zone_type street_paid .*fx_campus"),
    ("fx_res_a", pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE, "single_site_buffered .* zone_type street_paid .*fx_res_a"),
], ids=["campus_union_on_a_campus", "campus_outline_union_on_a_campus", "site_buffer_on_a_street_zone",
        "campus_union_on_a_street_zone", "campus_outline_union_on_a_street_zone", "site_buffer_on_a_campus",
        "site_buffer_on_a_resident_zone"])
def test_the_new_geometry_sources_fit_the_zone_types_they_describe(zone_id, source, message):
    # A campus zone (detection zones, or campus grounds united with them) is a campus; a single paid site (car park or
    # street section) is a street_paid zone: the tariff row and the polygon must agree on what the zone is.
    zones = pz.load_zone_polygons(ZONE_FIXTURE)
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    zones.loc[zones["zone_id"] == zone_id, "geometry_source"] = source
    if message is None:
        pz.validate_geometry_source_zone_types(zones, tariffs)
    else:
        with pytest.raises(ValueError, match=message):
            pz.validate_geometry_source_zone_types(zones, tariffs)


def _qa_row(**changes) -> pd.DataFrame:
    row = {"ags": "03101000", "name": "Braunschweig, Stadt", "role": "zones_from_core",
           "raw_response": "03101000_regulation_overpass_2026-09-30.json", "osm_timestamp": SNAPSHOT,
           "walk_m": "250.0", "maximum_filled_hole_m2": "20000.0", "minimum_island_m2": "10000.0", "segments": "40",
           "regulated_segments": "30", "free_segments": "4", "free_segments_without_fee_tag": "3",
           "mixed_segments": "1", "separate_segments": "0", "lots": "5", "free_street_side_areas": "2",
           "free_street_side_areas_without_fee_tag": "2", "free_offstreet_lots": "2", "free_offstreet_lots_in_core": "0",
           "free_offstreet_lot_area_in_core_m2": "0.0", "tagging_completeness": "0.812",
           "regulated_area_m2": "150000.0", "filled_area_m2": "260000.0", "eroded_filled_area_m2": "52000.0",
           "core_area_m2": "40000.0", "core_parts": "1", "reference": "fixture outline",
           "core_share_inside_reference": "0.95", "reference_share_covered_by_core": "0.20",
           "reference_tagging_completeness": "0.41", "largest_outline_distance_m": "310.0",
           "reference_paid_ways": "3", "reference_paid_street_side_areas": "2", "reference_free_side_ways": "1",
           "reference_free_side_length_m": "120.0",
           "reference_free_street_side_areas": "0", "reference_conflicts": "fixture_zone: 1 way with a free side",
           "piece_assignment": "", "q4_decision": "accepted", "applied": "true", "zone_ids": ERODED_ZONE,
           "note": "fixture row"}
    row.update(changes)
    return pd.DataFrame([row], columns=list(pz.ZONE_QA_COLUMNS))


@pytest.mark.parametrize("changes, message", [
    ({}, None),
    ({"walk_m": "150.0"}, "unavoidable_walk_m"),
    ({"osm_timestamp": "2026-09-29T05:10:00Z"}, "osm_timestamp"),
    ({"q4_decision": "rejected"}, "applied rows need q4_decision 'accepted'"),
    ({"applied": "false", "zone_ids": ""}, "not listed in the zone_ids of an applied QA row"),
    ({"zone_ids": "fx_bs_ia"}, "is not an osm_fee_erosion polygon"),
    ({"ags": "03102000", "name": "Salzgitter"}, "municipality_ags"),
    ({"role": "qa_only"}, "role 'zones_from_core'"),
    ({"tagging_completeness": "1.4"}, "tagging_completeness"),
    ({"q4_decision": "request_failed", "applied": "false", "zone_ids": ""}, "request_failed"),
], ids=["consistent", "walk_mismatch", "snapshot_mismatch", "applied_but_rejected", "polygon_not_listed",
        "listed_zone_not_eroded", "wrong_municipality", "qa_only_applied", "completeness_out_of_range",
        "failed_request_with_numbers"])
def test_zone_qa_table_is_cross_checked_against_the_polygons(tmp_path, changes, message):
    zones = pz.load_zone_polygons(_erosion_release(tmp_path))
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    qa = _qa_row(**changes)
    if message is None:
        pz.validate_zone_qa(qa, zones, tariffs)
    else:
        with pytest.raises(ValueError, match=message):
            pz.validate_zone_qa(qa, zones, tariffs)


def test_load_zone_qa_reads_the_documented_layout(tmp_path):
    path = tmp_path / "qa.csv"
    path.write_text("# QA table\n" + _qa_row().to_csv(index=False, lineterminator="\n"), encoding="utf-8")
    qa = pz.load_zone_qa(path)
    assert list(qa.columns) == list(pz.ZONE_QA_COLUMNS) and qa.loc[0, "zone_ids"] == ERODED_ZONE
    path.write_text(_qa_row().drop(columns=["note"]).to_csv(index=False), encoding="utf-8")
    with pytest.raises(ValueError, match="note"):
        pz.load_zone_qa(path)


# --------------------------------------------------------------------------- assignment


def test_assign_zones_returns_id_inside_and_nan_outside():
    zones = gpd.GeoDataFrame({"zone_id": ["a", "b"], "geometry": [box(0, 0, 10, 10), box(20, 0, 30, 10)]}, crs="EPSG:25832")
    points = gpd.GeoDataFrame({"person_id": [1, 1, 2], "activity_index": [0, 1, 0]},
                              geometry=[Point(5, 5), Point(50, 50), Point(25, 5)], crs="EPSG:25832")
    assigned = pz.assign_zones(points, zones)
    assert list(assigned.fillna("-")) == ["a", "-", "b"]


def test_assign_zones_keeps_the_index_of_the_points_even_when_duplicated():
    zones = gpd.GeoDataFrame({"zone_id": ["a"], "geometry": [box(0, 0, 10, 10)]}, crs="EPSG:25832")
    points = gpd.GeoDataFrame({"person_id": [7, 7]}, geometry=[Point(5, 5), Point(50, 50)], crs="EPSG:25832",
                              index=[3, 3])
    assigned = pz.assign_zones(points, zones)
    assert list(assigned.index) == [3, 3]
    assert list(assigned.fillna("-")) == ["a", "-"]


def test_assign_zones_raises_for_a_point_in_two_zones():
    zones = gpd.GeoDataFrame({"zone_id": ["a", "b"], "geometry": [box(0, 0, 10, 10), box(5, 0, 15, 10)]}, crs="EPSG:25832")
    points = gpd.GeoDataFrame({"person_id": [1]}, geometry=[Point(7, 5)], crs="EPSG:25832")
    with pytest.raises(ValueError, match="more than one zone"):
        pz.assign_zones(points, zones)


def test_assign_zones_rejects_points_without_coordinates():
    zones = gpd.GeoDataFrame({"zone_id": ["a"], "geometry": [box(0, 0, 10, 10)]}, crs="EPSG:25832")
    points = gpd.GeoDataFrame({"person_id": [1, 2, 3]}, geometry=[Point(5, 5), None, Point()], crs="EPSG:25832",
                              index=["p1", "p2", "p3"])
    with pytest.raises(ValueError, match="2 point") as raised:
        pz.assign_zones(points, zones)
    assert "p2" in str(raised.value) and "p3" in str(raised.value)


def test_assign_zones_requires_the_same_crs():
    zones = gpd.GeoDataFrame({"zone_id": ["a"], "geometry": [box(0, 0, 10, 10)]}, crs="EPSG:25832")
    points = gpd.GeoDataFrame({"person_id": [1]}, geometry=[Point(10.5, 52.2)], crs="EPSG:4326")
    with pytest.raises(ValueError, match="CRS"):
        pz.assign_zones(points, zones)


def test_fixture_zone_centroids_are_assigned_to_their_own_zone():
    zones = pz.load_zone_polygons(ZONE_FIXTURE)
    points = gpd.GeoDataFrame({"zone_id_expected": zones["zone_id"].values},
                              geometry=zones.geometry.representative_point().values, crs=zones.crs)
    assert list(pz.assign_zones(points, zones)) == list(zones["zone_id"])


# --------------------------------------------------------------------------- coverage register


def _register(rows):
    return pd.DataFrame(rows, columns=list(pz.REGISTER_COLUMNS))


def test_register_accepts_one_status_row_per_municipality_plus_excluded_areas():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    register = _register([
        ("03101000", "Braunschweig, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03101000", "Braunschweig: hospital lots", "excluded", "", "customer/visitor regimes, no exposure measured yet"),
        ("03102000", "Salzgitter, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03103000", "Wolfsburg, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03157006", "Peine, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03158037", "Wolfenbuettel, Stadt", "not_audited", "", ""),
        ("03151009", "Gifhorn, Stadt", "no_paid_parking_known", "https://example.org/source", "checked"),
    ])
    pz.validate_coverage_register(register, tariffs)
    pz.validate_coverage_register(register, tariffs, expected_ags={"03101000", "03102000", "03103000", "03157006",
                                                                   "03158037", "03151009"})
    with pytest.raises(ValueError, match="03154028"):
        pz.validate_coverage_register(register, tariffs, expected_ags={"03101000", "03102000", "03103000",
                                                                       "03157006", "03158037", "03151009", "03154028"})


def test_register_rejects_contradictions():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    base = [
        ("03101000", "Braunschweig, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03102000", "Salzgitter, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03103000", "Wolfsburg, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
        ("03157006", "Peine, Stadt", "zoned", "parking_tariffs_2026.csv", ""),
    ]
    with pytest.raises(ValueError, match="03157006"):  # a tariff row in a municipality that is not 'zoned'
        pz.validate_coverage_register(_register(base[:3] + [("03157006", "Peine, Stadt", "not_audited", "", "")]), tariffs)
    with pytest.raises(ValueError, match="03158037"):  # 'zoned' without any zone
        pz.validate_coverage_register(_register(base + [("03158037", "Wolfenbuettel", "zoned", "x", "")]), tariffs)
    with pytest.raises(ValueError, match="source"):
        pz.validate_coverage_register(_register(base + [("03158037", "Wolfenbuettel", "no_paid_parking_known", "", "")]),
                                      tariffs)
    with pytest.raises(ValueError, match="reason"):
        pz.validate_coverage_register(_register(base + [("03101000", "BS hospital", "excluded", "", "")]), tariffs)
    with pytest.raises(ValueError, match="duplicate"):
        pz.validate_coverage_register(_register(base + [("03101000", "Braunschweig again", "not_audited", "", "")]),
                                      tariffs)
    with pytest.raises(ValueError, match="status"):
        pz.validate_coverage_register(_register(base + [("03158037", "Wolfenbuettel", "maybe", "", "")]), tariffs)
    with pytest.raises(ValueError, match="ags"):
        pz.validate_coverage_register(_register(base + [("0315803", "Wolfenbuettel", "not_audited", "", "")]), tariffs)


def test_load_coverage_register_skips_header_lines(tmp_path):
    path = tmp_path / "register.csv"
    path.write_text("# header line\nags,name,status,source,note\n03101000,\"Braunschweig, Stadt\",zoned,"
                    "https://www.braunschweig.de/plan/#parken,\n", encoding="utf-8")
    register = pz.load_coverage_register(path)
    assert list(register.columns) == list(pz.REGISTER_COLUMNS)
    assert register.loc[0, "ags"] == "03101000" and register.loc[0, "source"].endswith("#parken")


# --------------------------------------------------------------------------- curation helper (Overpass)


def test_overpass_query_covers_every_fee_statement_and_escapes_names():
    from scripts.build_parking_zones_from_osm import build_overpass_query

    query = build_overpass_query((52.2, 10.4, 52.35, 10.65), street_names=["Bohlweg", "Am Wendentor (Nord)"],
                                 timeout_s=180)
    assert query.startswith("[out:json][timeout:180];")
    for statement in ('nwr["amenity"="parking"]["fee"="yes"]', 'way["highway"]["parking:both:fee"~"yes"]',
                      'way["highway"]["parking:left:fee"~"yes"]', 'way["highway"]["parking:right:fee"~"yes"]',
                      'way["highway"]["parking:condition:both"~"ticket"]',
                      'way["highway"]["parking:condition:left"~"ticket"]',
                      'way["highway"]["parking:condition:right"~"ticket"]'):
        assert statement + "(52.2,10.4,52.35,10.65);" in query
    assert "Am Wendentor \\\\(Nord\\\\)" in query
    assert query.rstrip().endswith("out tags geom;")


def test_overpass_remark_refuses_the_response():
    from scripts.build_parking_zones_from_osm import check_remark

    check_remark({"elements": []})
    with pytest.raises(ValueError, match="runtime error"):
        check_remark({"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 3"})


def test_failed_overpass_request_is_logged(tmp_path):
    import datetime as dt
    import urllib.error

    from scripts.build_parking_zones_from_osm import FAILURE_LOG_NAME, log_failed_request

    when = dt.datetime(2026, 9, 29, 5, 14, tzinfo=dt.timezone.utc)
    error = urllib.error.HTTPError("https://overpass-api.de/api/interpreter", 504, "Gateway Timeout", {}, None)
    log_failed_request(tmp_path, "03153017", error, now=when)
    log_failed_request(tmp_path, "03158037", TimeoutError("timed out"), now=when)
    lines = (tmp_path / FAILURE_LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert lines == ["2026-09-29T05:14:00Z\t03153017\tHTTP 504", "2026-09-29T05:14:00Z\t03158037\tTimeoutError: timed out"]


def test_overpass_fixture_dissolves_into_the_expected_candidates():
    from scripts.build_parking_zones_from_osm import build_candidates, elements_to_features

    payload = json.loads(OVERPASS_FIXTURE.read_text(encoding="utf-8"))
    features = elements_to_features(payload)
    assert features.crs.to_epsg() == 25832
    assert sorted(features["kind"].unique()) == ["parking", "street_fee", "street_named"]
    candidates = build_candidates(features)
    # ways 101 and 102 touch and dissolve; the lot 201, the node 301 and the legacy ticket way 103 stay apart;
    # the named street 401 without fee tags is not fee evidence.
    assert len(candidates) == 4
    merged = candidates[candidates["osm_ids"].str.contains("way/101")].iloc[0]
    assert "way/102" in merged["osm_ids"] and "parking:both:fee=yes" in merged["evidence"]
    assert not candidates["osm_ids"].str.contains("way/401").any()
    assert candidates.crs.to_epsg() == 25832 and (candidates.geometry.area > 0).all()


# --------------------------------------------------------------------------- committed data (validator CLI)

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
COMMITTED_PARKING_FILES = ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv",
                           "parking_zones_2026_qa.csv", "parking_zones_2026_supply_share_qa.csv",
                           "parking_zones_2026_municipal_qa.csv", "parking_resident_districts_2026.geojson",
                           "parking_garages_2026.geojson", "parking_garages_2026_qa.csv")
PARKSCHEININSELN = ("bs_parkscheininsel_marthastrasse_koernerstrasse",
                    "bs_parkscheininsel_gerstaeckerstrasse_kleine_campestrasse", "bs_parkscheininsel_mentestrasse")


def test_committed_parking_data_is_valid(capsys):
    from scripts.validate_parking_zones import main

    assert (COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").is_file(), "committed tariff table missing"
    assert main(["--data-path", str(REPO_ROOT / "eqasim-data" / "data")]) == 0
    out = capsys.readouterr().out
    assert "[parking-validate] OK" in out
    assert "register status" in out
    assert "geometry_source mix" in out
    # spec Amendment D (2026-10-07): 37 zones (26 before: 11 single paid sites of Bad Harzburg, Seesen, Braunlage and
    # Goslar came in, Braunschweig stays at 17 with Willy-Brandt-Platz and Volkmaroder Strasse for Kannengiesserstrasse
    # and the International House); the register has 11 zoned municipalities and the audited Schoeningen
    # spec Amendment G1 (2026-10-08): 40 zones (the three station car parks of Braunschweig Hbf came in, Braunschweig has 20)
    # and 130 register rows (the excluded row of the Braunschweig station car parks is gone, they are zones)
    assert "[parking-validate] 40 zones, 40 tariff rows, 130 register rows (123 municipalities)" in out
    assert "register status: zoned 11, no_paid_parking_known 1, not_audited 111, excluded 7" in out
    # ruling R-4a-8: the five campuses with a v1 outline are the union of the outline and the detection zones, Volkmaroder
    # Strasse (no outline exists) its detection zone alone
    assert "campus_outline_and_detection_zones 5" in out and "campus_detection_zones 1," in out
    assert "single_site_buffered 14" in out
    # spec Amendment C3: Braunschweig A, B, C and Goslar A, B, C, F, G, H, J, a second layer next to the fee zones
    assert "resident districts: 10 districts" in out and "03101000 3 districts" in out and "03153017 7 districts" in out
    # ASSUMPTION R2-a: where rule R2 is off is on the record: the five BgA rows, the three station car parks and the Goslar
    # car park at the ZOB state it, the six campus zones take the default, the other 25 of the 40 zones honour resident permits
    assert ("resident permits (rule R2, ASSUMPTION R2-a): valid on 25 of 40 zones; not valid on 9 stated rows "
            "(bs_bga_an_der_martinikirche, bs_bga_jodutenstrasse_klint, bs_bga_markthalle, bs_bga_suedstrasse, "
            "bs_bga_willy_brandt_platz, bs_hbf_p1_nord, bs_hbf_p2_sued, bs_hbf_p3_west, gs_parkplatz_klubgartenstrasse_zob) "
            "and on 6 campus zones (default)") in out
    # Task 4b (spec Amendments D1, D2, D4, E8) left 13 assumption windows, 16 with the three station rows of Amendment G1;
    # the commuter product is on zone Ib and the six
    # campus zones, no zone row carries a garage product or a search time
    assert "fee_window_source: assumption 16, municipal_page 17, ordinance 7" in out
    assert ("tariff products (schema 2): commuter product on 10 of 40 rows (bs_hbf_p1_nord, bs_hbf_p2_sued, bs_hbf_p3_west, "
            "bs_zone_ib, tu_campus_nord, "
            "tu_campus_ost_beethovenstrasse, tu_campus_ost_langer_kamp, tu_campus_volkmaroder_strasse, "
            "tu_forschungsflughafen, tu_zentralcampus); zone-level garage product on 0 rows (spec Amendment E8: garages enter "
            "through the dataset); search time on 0 rows (decision D4)") in out
    # the garage dataset (spec Amendment E1): one more summary line with the coverage and the assumption rates
    # specs E12 to E14 and G2: the supplement, follow-up and Wolfsburg car-park packages price every listed garage (49 of 49:
    # 36 garages, among them the Parkdeck Hauptbahnhof of Wolfsburg since Amendment G2, and the 13 surface lots of the city layer)
    assert ("garages: 49 listed, 49 priced, 0 not priced (none); per municipality 03101000 12 listed 12 priced") in out
    assert "03103000 23 listed 23 priced" in out
    assert ("priced garages resting on an assumption: P10 4, P11 5, P12 8, P13 15, P4 25, P5 25, P6 8, P7 5, P8 11 of 49 "
            "(at least one assumption 45, P4 or P5 33; in the tiered form 8, in the banded form 11, with the free "
            "schedule 11); by facility kind garage 36 listed 36 priced, surface_lot 13 listed 13 priced; "
            "monthly product on 12 garages") in out
    # the three Braunschweig station products and the Wolfsburg deck product are used (G1, G2): 13 + 4 used, 36 - 4 recorded
    assert "QA: monthly products used 17, recorded and not used 32 (capacity_limited_permits 1, excluded_by_package 1, " in out
    # spec Amendment F3: the monthly products per municipality (published, imputed under ASSUMPTION P13 with the median, none)
    assert ("garage monthly products (ASSUMPTION P13): Braunschweig (03101000) published 2, imputed 10, P13 median 107.48 EUR, "
            "none 0;") in out
    # the Wolfsburg median is 60.00 EUR of five published products since the deck (100.00 EUR) came in (was 57.50 of four)
    assert "Wolfsburg (03103000) published 5, imputed 5, P13 median 60.00 EUR, none 0;" in out
    assert "Goslar (03153017) published 1, imputed 0, none 3;" in out
    assert "total published 12, imputed 15, none 9 (surface lots 13, never imputed)" in out
    assert "medians in EUR: 03101000 107.48, 03103000 60.00, 03157006 48.00" in out
    # one QA row per Wolfsburg car park instead of the aggregated row: the 38 candidates (no_published_tariff 24) became 25
    assert ("candidates that are no garage 24 (bga_zone 2, customer_regime 2, dauerparker_only 2, no_coordinates 1, "
            "outside_source_list 1, station_zone 2, user_group_only 1, zone_street_product 13)") in out
    # the consistency check of the nine paid municipal car parks inside a zone (spec E14)
    assert ("car parks inside a zone (spec E14): 9 checked, the published hourly reference of the tariff area against the "
            "street rate of the zone: 9 equal, 0 differ (none)") in out


@pytest.mark.parametrize("changes, message", [
    # a QA-only row whose recorded decision contradicts rule Q4 (60 % tagging completeness)
    ({"tagging_completeness": "0.5"}, "ags 03103000: q4_decision 'accepted' but rule Q4 gives 'rejected'"),
    # a sensitivity arm must never stand in for the pre-registered run (ruling R-T1-g)
    ({"walk_m": "150.0"}, "ags 03103000: walk_m = 150.0 is not the pre-registered 250"),
], ids=["q4_contradiction", "sensitivity_parameters"])
def test_validator_reapplies_the_acceptance_rule_to_the_qa_table(tmp_path, capsys, changes, message):
    import shutil

    from scripts.validate_parking_zones import main

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv"):
        shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    # The committed QA rows stay (they are consistent with the polygons); the Wolfsburg row (QA only, no polygon
    # depends on it) is replaced by one whose recorded decision contradicts rule Q4 (60 % tagging completeness).
    committed_qa = COMMITTED_PARKING_DIR / "parking_zones_2026_qa.csv"
    rows = pz.load_zone_qa(committed_qa) if committed_qa.is_file() else _qa_row().iloc[0:0]
    contradicting = _qa_row(ags="03103000", name="Wolfsburg, Stadt", role="qa_only", applied="false", zone_ids="",
                            **changes)
    table = pd.concat([rows[rows["ags"] != "03103000"], contradicting], ignore_index=True)
    (target / "parking_zones_2026_qa.csv").write_text("# QA\n" + table.to_csv(index=False, lineterminator="\n"),
                                                      encoding="utf-8")
    assert main(["--data-path", str(tmp_path)]) == 1
    assert message in capsys.readouterr().out


#: The source wording of controller ruling R-C1 (spec Amendment C), binding for the committed release.
BRAUNSCHWEIG_PROVENANCE = ("Stadt Braunschweig, published fee zone map (2025-11-26) and Amtsblatt 2024-04-30 map annex; "
                           "digitised (owner-supplied package 2026-10-01); working accuracy 25 m; base map Open GeoData "
                           "dl-de/by-2-0")
WOLFSBURG_PROVENANCE = ("Stadt Wolfsburg, Geoviewer Themenkarte Parken (Stand 12/2024); open reuse licence not verified; "
                        "used by owner decision 2026-10-01")
#: The source wordings of spec Amendment D (owner data package of 2026-10-07): BgA annex maps, TU campus maps, the Goslar
#: ArcGIS service, the OSM outlines of Bad Harzburg and Seesen, the Braunlage tourism coordinates.
REGIONAL_PROVENANCE = (
    "Stadt Braunschweig, Amtsblatt 2022 Nr. 16 (annex maps of the BgA Entgeltordnung B 660); digitised (owner-supplied "
    "package 2026-10-07); working accuracy 5 m; base map Open GeoData dl-de/by-2-0",
    "TU Braunschweig, GB3 Parkbereiche campus maps (retrieved 2026-10-02; (c) d&d design & distribution); explicit "
    "consent for reuse of TU graphics not obtained; used by owner decision 2026-10-07",
    "Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22); open reuse licence not verified; used by owner "
    "decision 2026-10-07",
    "(c) OpenStreetMap contributors, ODbL 1.0: the outlines of the Bad Harzburg car parks and the street Am Markt in Seesen",
    "Stadt Braunlage, official tourism coordinates of the car parks (source points, not outlines)",
    # ruling R-4a-8: the campus grounds of the TU zones are OSM outlines (ODbL), united with the detection zones of the TU maps
    "the geometry_source campus_outline_and_detection_zones TU campus zones: the campus grounds are (c) OpenStreetMap "
    "contributors, ODbL 1.0 (the OSM university outlines and the OSM car parks at the International House of the v1 release)",
    "the geometry_source campus_detection_zones zone tu_campus_volkmaroder_strasse: TU Braunschweig, GB3 Parkbereiche campus maps")


def test_committed_zones_carry_the_licence_notice():
    document = json.loads((COMMITTED_PARKING_DIR / "parking_zones_2026.geojson").read_text(encoding="utf-8"))
    assert "ODbL" in document["license"]
    assert "OpenStreetMap contributors" in document["attribution"]
    # spec Amendment C: the licence is stated per source; no claim that every polygon derives from OpenStreetMap
    assert "every polygon is derived from OpenStreetMap" not in document["license"]
    assert BRAUNSCHWEIG_PROVENANCE in document["license"] and WOLFSBURG_PROVENANCE in document["license"]
    # spec Amendment D: every source of the regional package is named with its terms, the TU consent gap and the
    # unverified Goslar licence included; the municipal and the regional wording join at a sentence boundary
    for wording in REGIONAL_PROVENANCE:
        assert wording in document["license"], wording
    assert ".." not in document["license"] and ".." not in document["attribution"]
    # the BgA lots cut out of bs_zone_ia are the package polygons, no longer the OSM outlines of v1
    assert "the OSM car-park outlines of the BgA zones" not in document["license"]
    assert "explicit consent for reuse of TU graphics not obtained" in document["attribution"]


def test_committed_parking_files_are_ascii():
    for name in COMMITTED_PARKING_FILES:
        text = (COMMITTED_PARKING_DIR / name).read_text(encoding="utf-8")
        offending = sorted({character for character in text if ord(character) > 127})
        assert not offending, f"{name} contains non-ASCII characters {offending}"


def test_committed_texts_do_not_call_the_parkgo_annex_unpublished():
    # The annex map ('Anlage zur ParkGO') is page 3 of the ParkGO PDF: not digitised in v1, but published.
    paths = [COMMITTED_PARKING_DIR / name for name in COMMITTED_PARKING_FILES]
    paths.append(REPO_ROOT / "docs" / "registry" / "data" / "parking_zones_2026.yml")
    for path in paths:
        for sentence in re.split(r"[.;]\s", path.read_text(encoding="utf-8").lower()):
            if "annex" in sentence:
                assert "not published" not in sentence, f"{path.name}: {sentence.strip()[:160]}"


def test_committed_parkscheininseln_are_street_paid_zones_cut_out_of_the_resident_zone():
    zones = pz.load_zone_polygons(COMMITTED_PARKING_DIR / "parking_zones_2026.geojson").set_index("zone_id")
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    resident = zones.loc["bs_resident_stadthalle_132", "geometry"]
    for zone_id in PARKSCHEININSELN:
        assert tariffs.loc[zone_id, "zone_type"] == "street_paid"
        assert zones.loc[zone_id, "geometry_source"] == "street_list_buffer"
        assert zones.loc[zone_id, "geometry"].intersection(resident).area <= pz.OVERLAP_TOLERANCE_M2
    # Marthastrasse/Koernerstrasse and Gerstaeckerstrasse/Kleine Campestrasse were cut out of zone 132, so they
    # share its outline; Mentestrasse lies outside the concept's resident streets.
    assert zones.loc[PARKSCHEININSELN[0], "geometry"].buffer(1.0).intersects(resident)
    assert zones.loc[PARKSCHEININSELN[1], "geometry"].buffer(1.0).intersects(resident)
    assert not zones.loc[PARKSCHEININSELN[2], "geometry"].buffer(1.0).intersects(resident)


def test_committed_texts_do_not_claim_that_the_detection_zones_include_the_buildings():
    # ruling R-4a-8 (task 4a review): the camera detection zones mark the paid car parks; the buildings, where the
    # activities lie, are in the campus grounds that the TU zones keep from the v1 release
    paths = [COMMITTED_PARKING_DIR / "parking_zones_2026.geojson", COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv",
             COMMITTED_PARKING_DIR / "parking_zones_2026_municipal_qa.csv",
             REPO_ROOT / "docs" / "registry" / "data" / "parking_zones_2026.yml",
             REPO_ROOT / "scripts" / "curation" / "parking_zones_2026" / "regional_zones.py"]
    for path in paths:
        assert "include buildings" not in path.read_text(encoding="utf-8"), path.name


def test_committed_zones_carry_real_provenance_only():
    # valid as stored: the first Task 1d release held a wob_tarifzone_2 that the loader repaired at every load
    zones = pz.load_zone_polygons(COMMITTED_PARKING_DIR / "parking_zones_2026.geojson", max_repairs=0)
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv")
    assert set(zones["geometry_source"]) <= set(pz.GEOMETRY_SOURCES)
    assert not (tariffs["source_url"] == pz.FIXTURE_MARKER).any()
    assert tariffs["source_url"].str.startswith("https://").all()
    districts = pz.load_resident_districts(COMMITTED_PARKING_DIR / "parking_resident_districts_2026.geojson")
    assert set(districts["geometry_source"]) <= set(pz.DISTRICT_GEOMETRY_SOURCES)
    assert districts["source_url"].str.startswith("https://").all()
    # every assumption-grade fee window is explained in its row
    flagged = tariffs[tariffs["fee_window_source"] == "assumption"]
    assert flagged["notes"].str.contains("ASSUMPTION F1").all()


def test_committed_wolfsburg_zones_are_the_three_sourced_tariff_zones():
    # spec Amendment C2: three tariff zones of buffered street sections (ASSUMPTION C-a, 50 m) replace wob_innenstadt;
    # each row opens with the R-C1 wording, bills by ASSUMPTION C-b (ruling R-T1d-a) and invents no value the city
    # does not publish for the sections (ruling R-T1d-c: no cap, maximum stay or long-stay product)
    zones = pz.load_zone_polygons(COMMITTED_PARKING_DIR / "parking_zones_2026.geojson", max_repairs=0).set_index("zone_id")
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv")
    wolfsburg = tariffs[tariffs["municipality_ags"] == "03103000"].set_index("zone_id")
    assert sorted(wolfsburg.index) == ["wob_tarifzone_1", "wob_tarifzone_2", "wob_tarifzone_3"]
    assert "wob_innenstadt" not in zones.index
    assert (zones.loc[wolfsburg.index, "geometry_source"] == pz.MUNICIPAL_SECTIONS_GEOMETRY_SOURCE).all()
    assert (zones.loc[wolfsburg.index, "section_buffer_m"] == 50.0).all()
    assert wolfsburg["notes"].str.startswith(WOLFSBURG_PROVENANCE).all()
    assert wolfsburg["notes"].str.contains("ASSUMPTION C-b").all() and (wolfsburg["billing_unit_min"] == 30).all()
    unpublished = ["free_if_stay_at_most_min", "first_period_min", "first_period_eur", "daily_cap_eur", "max_stay_min",
                   "long_stay_product_eur"]
    assert wolfsburg[unpublished].isna().all().all()


def test_committed_bga_car_parks_state_that_resident_permits_are_not_valid():
    # Ruling R-T1e-a, ASSUMPTION R2-a: no source states that resident permits are valid at the five separately
    # operated BgA car parks of Braunschweig (spec Amendment D1: Kannengiesserstrasse is gone, Willy-Brandt-Platz is
    # new), so rule R2 must not free a stay there; every other row leaves the column empty, i.e. takes the default of
    # its zone type (valid on streets, not on a campus).
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    bga = sorted(zone_id for zone_id in tariffs.index if zone_id.startswith("bs_bga_"))
    assert bga == ["bs_bga_an_der_martinikirche", "bs_bga_jodutenstrasse_klint", "bs_bga_markthalle",
                   "bs_bga_suedstrasse", "bs_bga_willy_brandt_platz"]
    assert (tariffs.loc[bga, "zone_type"] == "street_paid").all()
    assert (tariffs.loc[bga, "resident_permits_valid"] == False).all()  # noqa: E712
    assert tariffs.loc[bga, "notes"].str.contains(
        "separately operated car park (BgA); no source states that resident permits are valid; ASSUMPTION R2-a",
        regex=False).all()
    # the only other stated flag is the Goslar car park at the ZOB (fix round 1 of task 4a, M-1): no resident regime is
    # stated for it, so it is treated like a BgA lot
    assert tariffs.loc["gs_parkplatz_klubgartenstrasse_zob", "resident_permits_valid"] == False  # noqa: E712
    # spec Amendment G1: the three DB BahnPark station car parks state it too (no source says that permits are valid there)
    stations = ["bs_hbf_p1_nord", "bs_hbf_p2_sued", "bs_hbf_p3_west"]
    assert (tariffs.loc[stations, "resident_permits_valid"] == False).all()  # noqa: E712
    assert tariffs.drop(index=bga + stations + ["gs_parkplatz_klubgartenstrasse_zob"])["resident_permits_valid"].isna().all()


#: The six TU campus zones of spec Amendment D1 and ruling R-4a-8 (owner decision of 2026-10-07): the union of the
#: campus grounds of the v1 release (the OSM university outline, the destination area; the International House of v1 is
#: part of the Langer Kamp grounds) and the camera detection zones (the paid car parks) per campus; Volkmaroder Strasse
#: has no v1 outline and is its detection zone alone. Bevenroder Strasse is not zoned (no GB3 page states ticketing).
#: Areas in m2 as released (a regression guard: the zone is the union, not the detection zones alone, whose areas are
#: 93,940, 84,541, 151,992, 88,002, 18,982 and 5,331 m2).
TU_CAMPUS_AREAS_M2 = {"tu_campus_nord": 129_466.0, "tu_campus_ost_beethovenstrasse": 158_571.0,
                      "tu_campus_ost_langer_kamp": 100_002.0, "tu_campus_volkmaroder_strasse": 5_331.0,
                      "tu_forschungsflughafen": 65_118.0, "tu_zentralcampus": 136_610.0}
TU_CAMPUS_ZONES = tuple(sorted(TU_CAMPUS_AREAS_M2))

#: SHA-256 of the owner's data package of 2026-10-07 that the new rows and polygons cite.
REGIONAL_PACKAGE_SHA256 = "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7"

#: The eleven single paid sites of spec Amendment D3 (2026-10-07): municipality, hourly rate in EUR, billing unit in
#: minutes, fee window start and end in hours, fee window source. The 50 m area of each site is ASSUMPTION C-a.
D3_SITES = {
    "gs_parkplatz_baeringerstrasse": ("03153017", 1.0, 30, 10.0, 18.0, "municipal_page"),
    "gs_parkplatz_klubgartenstrasse_zob": ("03153017", 1.0, 30, 10.0, 16.0, "municipal_page"),
    "gs_parkplatz_glockengiesserstrasse": ("03153017", 1.0, 30, 10.0, 18.0, "municipal_page"),
    "bh_sole_therme": ("03153002", 1.0, 30, 8.0, 18.0, "assumption"),
    "bh_kurpark": ("03153002", 1.0, 30, 8.0, 18.0, "assumption"),
    "bh_grossparkplatz": ("03153002", 1.0, 30, 8.0, 18.0, "assumption"),
    "bh_burgberg": ("03153002", 1.0, 30, 8.0, 18.0, "assumption"),
    "bh_berliner_platz": ("03153002", 1.0, 30, 8.0, 18.0, "assumption"),
    "se_am_markt": ("03153012", 0.6, 10, 8.0, 18.0, "ordinance"),
    "br_hexenritt": ("03153016", 1.25, 120, 9.0, 18.0, "assumption"),
    "br_wurmberg": ("03153016", 1.0, 30, 9.0, 18.0, "assumption"),
}


def test_committed_braunschweig_zones_follow_spec_amendment_d1():
    zones = pz.load_zone_polygons(COMMITTED_PARKING_DIR / "parking_zones_2026.geojson", max_repairs=0).set_index("zone_id")
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    # Kannengiesserstrasse is a pocket park since April 2026; the International House is part of the Langer Kamp union
    for removed in ("bs_bga_kannengiesserstrasse", "tu_international_house"):
        assert removed not in zones.index and removed not in tariffs.index
    # the BgA lots come from the annex maps of the Amtsblatt, the new Willy-Brandt-Platz lot included
    bga = [zone_id for zone_id in zones.index if zone_id.startswith("bs_bga_")]
    assert len(bga) == 5 and "bs_bga_willy_brandt_platz" in bga
    assert (zones.loc[bga, "geometry_source"] == "ordinance_map").all()
    # one campus zone per ticketed campus, the union of its campus grounds and its detection zones (ruling R-4a-8)
    campus = sorted(tariffs.index[tariffs["zone_type"] == "campus"])
    assert campus == list(TU_CAMPUS_ZONES)
    with_outline = [zone_id for zone_id in campus if zone_id != "tu_campus_volkmaroder_strasse"]
    assert (zones.loc[with_outline, "geometry_source"] == pz.CAMPUS_OUTLINE_AND_DETECTION_ZONES_GEOMETRY_SOURCE).all()
    assert zones.loc["tu_campus_volkmaroder_strasse", "geometry_source"] == pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE
    for zone_id, area in TU_CAMPUS_AREAS_M2.items():
        assert zones.loc[zone_id, "geometry"].area == pytest.approx(area, rel=2e-3), zone_id
    assert zones.loc[campus, "digitising_note"].str.contains("explicit consent for reuse of TU graphics not obtained",
                                                              regex=False).all()
    # the outline is the destination area (the buildings), the detection zones the paid car parks; no note claims that
    # the detection zones include the buildings, and the outline's v1 provenance (OSM ways) is kept
    notes = zones.loc[campus, "digitising_note"]
    assert not notes.str.contains("include buildings", regex=False).any()
    assert notes.str.contains("paid car parks", regex=False).all()
    assert notes.loc[with_outline].str.contains("destination area", regex=False).all()
    assert notes.loc[with_outline].str.contains("OSM amenity=university", regex=False).all()
    assert "no campus grounds" in notes.loc["tu_campus_volkmaroder_strasse"]
    # no campus is cut out of the street zones by accident: the precedence of ruling R-4a-1 leaves no overlap
    street = [zone_id for zone_id in zones.index if zone_id.startswith("bs_") and zone_id not in campus]
    for campus_id in campus:
        for street_id in street:
            overlap = zones.loc[campus_id, "geometry"].intersection(zones.loc[street_id, "geometry"]).area
            assert overlap <= pz.OVERLAP_TOLERANCE_M2, f"{campus_id} overlaps {street_id} by {overlap:.1f} m2"
    # Volkmaroder Strasse: the TU's GB3 page states the ticketing from 2026-10-01, the campus lies in the outer ring
    assert tariffs.loc["tu_campus_volkmaroder_strasse", "valid_from"] == "2026-10-01"
    assert tariffs.loc["tu_campus_volkmaroder_strasse", "workplace_class"] == "bs_outer"
    # the Willy-Brandt-Platz lot lies in the outer ring of the SrV workplace classes (ASSUMPTION, location based)
    assert tariffs.loc["bs_bga_willy_brandt_platz", "workplace_class"] == "bs_outer"
    assert "ASSUMPTION" in tariffs.loc["bs_bga_willy_brandt_platz", "notes"]


def test_committed_d3_sites_are_single_site_zones_with_sourced_tariffs():
    zones = pz.load_zone_polygons(COMMITTED_PARKING_DIR / "parking_zones_2026.geojson", max_repairs=0).set_index("zone_id")
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    single = zones[zones["geometry_source"] == pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE]
    # the 11 sites of Amendment D3 and the three station car parks of Amendment G1 (their own test pins the tariff rows)
    assert sorted(single.index) == sorted([*D3_SITES, "bs_hbf_p1_nord", "bs_hbf_p2_sued", "bs_hbf_p3_west"])
    assert (single["site_buffer_m"] == 50.0).all()  # ASSUMPTION C-a, the same distance as the Wolfsburg sections
    for zone_id, (ags, rate, unit, start, end, window_source) in D3_SITES.items():
        row = tariffs.loc[zone_id]
        assert (row["municipality_ags"], row["zone_type"], row["workplace_class"]) == (ags, "street_paid", "03153"), zone_id
        assert (row["hourly_rate_eur"], row["billing_unit_min"]) == (rate, unit), zone_id
        assert (row["fee_start_h"], row["fee_end_h"], row["fee_window_source"]) == (start, end, window_source), zone_id
        assert row["resident_exempt"] == False, zone_id  # noqa: E712
        # no source states a maximum stay that this table can model as a rule, a resident permit validity, a garage or
        # commuter price, or a search time: the cells stay empty (never invented, Task 4b owns the later columns)
        assert pd.isna(row["max_stay_min"]) and pd.isna(row["long_stay_product_eur"]), zone_id
        assert pd.isna(row["search_time_min"]), zone_id
        # ASSUMPTION R2-a: the permit flag is false where no resident regime is stated (the ZOB), else the empty default
        if zone_id == "gs_parkplatz_klubgartenstrasse_zob":
            assert row["resident_permits_valid"] == False, zone_id  # noqa: E712
        else:
            assert pd.isna(row["resident_permits_valid"]), zone_id
        # the notes name every assumption the row rests on (fix round 1, M-1, M-3)
        notes = row["notes"]
        if zone_id.startswith("gs_"):
            assert "ASSUMPTION D3-a" in notes and "ASSUMPTION R2-a" in notes, zone_id
        if zone_id.startswith("bh_"):
            assert "ASSUMPTION D3-b" in notes and "ASSUMPTION M2" in notes, zone_id
            # M-4: the package marks the ceiling rule not preferred (prior_snapshot); the OSM charge tags corroborate it
            assert "bh_ordinary_ceiling" in notes and "not preferred for current use" in notes and "prior_snapshot" in notes
            assert ("charge='0.50 EUR/30 min'" in notes) == (zone_id != "bh_berliner_platz"), zone_id
            assert ("no charge tag" in notes) == (zone_id == "bh_berliner_platz"), zone_id
        if zone_id == "se_am_markt":
            assert "ASSUMPTION M2" in notes, zone_id
        # every row cites the package it was built from and states the buffer assumption and its own site kind
        assert "Single paid site of spec Amendment D3" in row["notes"] and "ASSUMPTION C-a" in row["notes"], zone_id
        assert f"Regional_Parkdaten_Belege_2026-10-07.zip (SHA-256 {REGIONAL_PACKAGE_SHA256}" in row["notes"], zone_id
    # the published total-stay bands of Hexenritt (free up to 30 min, 2.50 EUR up to 2 h, then 1.25 EUR per 2 h, 10 EUR
    # a day) are encoded exactly, no other single-band approximation
    hexenritt = tariffs.loc["br_hexenritt"]
    assert (hexenritt["free_if_stay_at_most_min"], hexenritt["first_period_min"], hexenritt["first_period_eur"],
            hexenritt["daily_cap_eur"]) == (30, 120, 2.5, 10.0)
    # an F1 window is labelled as an assumption in the row, a sourced one is not
    for zone_id, spec in D3_SITES.items():
        assert ("ASSUMPTION F1" in tariffs.loc[zone_id, "notes"]) == (spec[5] == "assumption"), zone_id
    # M-8: the OSM free condition of the Sole-Therme (a stay of up to 15 min) is not modelled, and the note says so
    assert "free condition for a stay of up to 15 min is not modelled" in tariffs.loc["bh_sole_therme", "notes"]


#: The monthly or 30-day products behind the commuter products of spec Amendment D2 (ruling R-D2-a), written by hand from
#: the sources: the 30-day ticket of Braunschweig zone Ib (ParkGO sec. 1(2), 79.00 EUR, valid for 30 consecutive calendar
#: days) and the month ticket of the TU members (Parkordnung of 2026-06-03 sec. 6(2), 10 EUR). The Ib 7-day ticket (29 EUR)
#: is no monthly or 30-day product and stays unused.
COMMUTER_MONTHLY_PRODUCT_EUR = {"bs_zone_ib": 79.0, **{zone_id: 10.0 for zone_id in TU_CAMPUS_ZONES},
                                # spec Amendment G1: '1 Monat fuer Stellplatzmieter' of the three DB BahnPark station car parks
                                "bs_hbf_p1_nord": 120.0, "bs_hbf_p2_sued": 74.0, "bs_hbf_p3_west": 74.0}
#: ASSUMPTION P2: 21 working days per month.
COMMUTER_WORKING_DAYS = 21
#: The rows that keep the assumption-grade fee window (ASSUMPTION F1) in the release of spec Amendment D1, 13 of 37: the
#: two rows that Amendment D1 moves to a sourced window (he_innenstadt 9-16 h, gs_altstadt_zone1 10-18 h) are not among them;
#: the three station rows of Amendment G1 add 3 (16 of 40; ruling R-4g-2: a window read from the opening hours of an operator page).
F1_ASSUMPTION_ZONES = (
    "bh_berliner_platz", "bh_burgberg", "bh_grossparkplatz", "bh_kurpark", "bh_sole_therme", "br_hexenritt", "br_wurmberg",
    "bs_bga_an_der_martinikirche", "bs_bga_jodutenstrasse_klint", "bs_bga_willy_brandt_platz", "bs_hbf_p1_nord",
    "bs_hbf_p2_sued", "bs_hbf_p3_west",
    "bs_parkscheininsel_gerstaeckerstrasse_kleine_campestrasse", "bs_parkscheininsel_marthastrasse_koernerstrasse",
    "bs_parkscheininsel_mentestrasse")


def test_committed_commuter_products_follow_spec_amendment_d2():
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    # exactly the Braunschweig zone Ib, the six campus rows and the three station car parks carry a commuter product, every
    # other row none (the station values are 120 / 21 = 5.714 and 74 / 21 = 3.524, to the cent)
    commuter = tariffs["commuter_day_eur"].dropna()
    assert commuter.to_dict() == {"bs_zone_ib": 3.76, **{zone_id: 0.48 for zone_id in TU_CAMPUS_ZONES},
                                  "bs_hbf_p1_nord": 5.71, "bs_hbf_p2_sued": 3.52, "bs_hbf_p3_west": 3.52}
    # the amount is the cheapest monthly or 30-day product over 21 working days, rounded to the cent (ASSUMPTION P2)
    assert set(COMMUTER_MONTHLY_PRODUCT_EUR) == set(commuter.index)
    for zone_id, monthly_eur in COMMUTER_MONTHLY_PRODUCT_EUR.items():
        assert commuter[zone_id] == round(monthly_eur / COMMUTER_WORKING_DAYS, 2), zone_id
    assert set(tariffs.loc[commuter.index, "zone_type"]) == {"street_paid", "campus"}
    # the zone Ib row keeps its street product and states the commuter product; the Ia row has none (the tickets are Ib's)
    assert tariffs.loc["bs_zone_ib", "notes"].count("ASSUMPTION P2") == 1
    assert "79.00 EUR" in tariffs.loc["bs_zone_ib", "notes"] and "7-day ticket (29.00 EUR)" in tariffs.loc["bs_zone_ib", "notes"]
    assert pd.isna(tariffs.loc["bs_zone_ia", "commuter_day_eur"])
    assert (tariffs.loc["bs_zone_ib", "hourly_rate_eur"], tariffs.loc["bs_zone_ib", "daily_cap_eur"]) == (1.80, 9.0)
    # the campus rows keep the day tickets: the month ticket competes with the member day ticket (spec Amendment A4)
    campus = tariffs.loc[list(TU_CAMPUS_ZONES)]
    assert (campus["member_day_eur"] == 3.5).all() and (campus["guest_day_eur"] == 9.0).all()
    assert campus["notes"].str.contains("ASSUMPTION P2", regex=False).all()
    assert campus["notes"].str.contains("monthly_tu_member_month_ticket", regex=False).all()
    assert not campus["notes"].str.contains("overstates", regex=False).any()  # the v1 sentence on the day ticket is gone
    # no garage product on any zone row and no search time (spec Amendment E8, decision D4); the table has no such columns
    assert tariffs[list(pz.GARAGE_COLUMNS)].isna().all().all()
    assert tariffs["search_time_min"].isna().all()


def test_committed_fee_windows_of_helmstedt_and_goslar_zone_1_follow_spec_amendment_d1():
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    helmstedt, goslar = tariffs.loc["he_innenstadt"], tariffs.loc["gs_altstadt_zone1"]
    # the city brochure of 12/2024 (most places Mo-Fr 09:00-16:00) and the Goslar service page (Kornstrasse Mo-Sa
    # 10:00-18:00 at 2 EUR/h) replace the assumption F1 window 9-18 h; the rates and billing units do not change
    assert (helmstedt["fee_start_h"], helmstedt["fee_end_h"], helmstedt["fee_window_source"]) == (9.0, 16.0, "municipal_page")
    assert (goslar["fee_start_h"], goslar["fee_end_h"], goslar["fee_window_source"]) == (10.0, 18.0, "municipal_page")
    assert (helmstedt["hourly_rate_eur"], helmstedt["billing_unit_min"]) == (1.2, 30)
    assert (goslar["hourly_rate_eur"], goslar["billing_unit_min"]) == (2.0, 30)
    assert "ASSUMPTION F1" not in helmstedt["notes"] and "ASSUMPTION F1" not in goslar["notes"]
    assert "Parken_in_Helmstedt.pdf" in helmstedt["notes"] and "helmstedt_gebuehrenzeiten.json" in helmstedt["notes"]
    assert "Wallplatz" in helmstedt["notes"] and "Saturday 09:00-13:00 is not modelled" in helmstedt["notes"]
    assert "https://www.meingoslar.de/service" in goslar["notes"] and "Kornstrasse" in goslar["notes"]
    assert "inference" in goslar["notes"]
    # the Edelhoefe garage left the zone row: it is a garage of the dataset (spec Amendment E8), not a note on the street
    assert "0.50 EUR first 30 min" not in helmstedt["notes"] and "parking_garages_2026" in helmstedt["notes"]
    # every other window source is unchanged: 13 rows keep the assumption (all explained by ASSUMPTION F1 in their notes)
    # (the three station rows of Amendment G1 read the window from the opening hours of an operator page, ruling R-4g-2:
    # assumption 13 + 3)
    assert tariffs["fee_window_source"].value_counts().to_dict() == {"municipal_page": 17, "assumption": 16, "ordinance": 7}
    assert sorted(tariffs.index[tariffs["fee_window_source"] == "assumption"]) == list(F1_ASSUMPTION_ZONES)


def test_committed_register_records_the_regional_audit_of_spec_amendment_d3():
    register = pz.load_coverage_register(COMMITTED_PARKING_DIR / "parking_coverage_register_2026.csv")
    register = register[register["status"] != "excluded"]  # the excluded areas share the AGS of a status row
    status = dict(zip(register["ags"], register["status"]))
    for ags in ("03101000", "03153002", "03153012", "03153016", "03153017"):  # Braunschweig, Bad Harzburg, Seesen, ...
        assert status[ags] == "zoned", ags
    assert status["03154019"] == "no_paid_parking_known"  # Schoeningen
    # the audited towns without a zone stay not_audited: an audit that finds no confirmed paid site is no proof of free
    # parking; the result is in the note
    notes = dict(zip(register["ags"], register["note"]))
    for ags in ("03153018", "03154013", "03151040", "03153019", "03158027"):
        assert status[ags] == "not_audited", ags
        assert "Audit result of 2026-10-07" in notes[ags], ags
    schoeningen = register[register["ags"] == "03154019"].iloc[0]
    assert schoeningen["source"].startswith("https://www.schoeningen.de/")
    # M-7: the unincorporated area Harz holds 85 % of the Bad Harzburg car park Grossparkplatz (a declared exception of the
    # municipality check) and says so, without becoming a zoned municipality
    assert status["03153504"] == "not_audited" and "bh_grossparkplatz" in notes["03153504"]
    assert "declared exception" in notes["03153504"]


def test_validator_rejects_a_single_site_zone_with_a_campus_geometry_source(tmp_path, capsys):
    # The zone polygons and the tariff rows describe the same zone type (spec Amendment D, validator rule): a car park
    # polygon labelled as a campus detection zone must not validate with a street tariff row.
    import shutil

    from scripts.validate_parking_zones import main

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in COMMITTED_PARKING_FILES:
        shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    path = target / "parking_zones_2026.geojson"
    document = json.loads(path.read_text(encoding="utf-8"))
    relabelled = [feature["properties"] for feature in document["features"] if feature["properties"]["zone_id"] == "bh_kurpark"]
    assert len(relabelled) == 1
    relabelled[0]["geometry_source"] = pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE
    relabelled[0].pop("site_buffer_m")  # a campus polygon carries no site buffer, so the zone type is what fails
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["--data-path", str(tmp_path)]) == 1
    assert ("geometry_source campus_detection_zones describes zone_type campus zones, but the tariff row of zone(s) "
            "{'bh_kurpark': 'street_paid'} has another type") in capsys.readouterr().out


def test_validator_runs_the_tariff_model_contract_on_every_row(tmp_path, capsys, monkeypatch):
    import shutil

    import scripts.validate_parking_zones as cli

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv"):
        shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    seen = []
    original = cli.tariff_export.tariff_row_to_zone

    def contract(row):
        seen.append(row["zone_id"])
        if row["zone_id"] == "bs_zone_ia":
            raise ValueError("rejected by the test contract")
        return original(row)

    monkeypatch.setattr(cli.tariff_export, "tariff_row_to_zone", contract)
    assert cli.main(["--data-path", str(tmp_path)]) == 1
    tariffs = pz.load_tariffs(target / "parking_tariffs_2026.csv")
    assert sorted(seen) == sorted(tariffs["zone_id"])
    assert "zone 'bs_zone_ia': rejected by the test contract" in capsys.readouterr().out


def test_validator_requires_the_municipal_qa_table_of_the_municipal_zones(tmp_path, capsys):
    # The committed release carries buffered municipal street sections (spec Amendment C2), whose areas and overlaps
    # live in the municipal QA table; a release copied without that table must not validate.
    import shutil

    from scripts.validate_parking_zones import main

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv",
                 "parking_zones_2026_qa.csv", "parking_zones_2026_supply_share_qa.csv",
                 "parking_resident_districts_2026.geojson"):
        shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    assert main(["--data-path", str(tmp_path)]) == 1
    assert "but no municipal QA table" in capsys.readouterr().out
    shutil.copy(COMMITTED_PARKING_DIR / "parking_zones_2026_municipal_qa.csv", target / "parking_zones_2026_municipal_qa.csv")
    assert main(["--data-path", str(tmp_path)]) == 0
    assert "municipal QA" in capsys.readouterr().out


def test_validator_rejects_a_register_without_the_zoned_municipality(tmp_path, capsys):
    import shutil

    from scripts.validate_parking_zones import main

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv"):
        shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    register = target / "parking_coverage_register_2026.csv"
    text = register.read_text(encoding="utf-8").replace("03102000,\"Salzgitter, Stadt\",zoned",
                                                        "03102000,\"Salzgitter, Stadt\",not_audited")
    register.write_text(text, encoding="utf-8")
    assert main(["--data-path", str(tmp_path)]) == 1
    assert "tariff rows in municipalities not marked 'zoned': ['03102000']" in capsys.readouterr().out


@pytest.mark.parametrize("change, message", [
    ("missing", "parking resident districts missing"),
    ("overlap", "resident districts overlap by more than"),
    ("unknown_municipality", "03101999"),
    ("fixture_marker", "test-set marker"),
])
def test_validator_checks_the_resident_districts_against_themselves_and_the_register(tmp_path, capsys, change, message):
    # spec Amendment C3: the district layer is part of the release, so a release without it, with overlapping
    # districts, with a district in a municipality the register does not know or with test districts must not validate
    import shutil

    from scripts.validate_parking_zones import main

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in ("parking_zones_2026.geojson", "parking_tariffs_2026.csv", "parking_coverage_register_2026.csv",
                 "parking_zones_2026_qa.csv", "parking_zones_2026_supply_share_qa.csv",
                 "parking_zones_2026_municipal_qa.csv", "parking_resident_districts_2026.geojson"):
        shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    districts_file = target / "parking_resident_districts_2026.geojson"
    if change == "missing":
        districts_file.unlink()
    elif change == "overlap":
        districts = gpd.read_file(districts_file).to_crs("EPSG:25832")
        districts.loc[districts["district_id"] == "bs_district_b", "geometry"] = districts.loc[
            districts["district_id"] == "bs_district_a", "geometry"].iloc[0].buffer(100.0)
        districts.to_crs("EPSG:4326").to_file(districts_file, driver="GeoJSON")
    elif change == "unknown_municipality":
        districts = gpd.read_file(districts_file)
        districts.loc[districts["district_id"] == "bs_district_c", "municipality_ags"] = "03101999"
        districts.to_file(districts_file, driver="GeoJSON")
    else:
        districts = gpd.read_file(districts_file)
        districts["geometry_source"] = pz.FIXTURE_MARKER
        districts.to_file(districts_file, driver="GeoJSON")
    assert main(["--data-path", str(tmp_path)]) == 1
    assert message in capsys.readouterr().out


def _copy_committed_release(tmp_path, leave_out=()) -> Path:
    """The committed parking files as a data path of a temporary release (``<data path>/braunschweig/parking``), without
    the files named in ``leave_out``; returns the parking folder."""
    import shutil

    target = tmp_path / "braunschweig" / "parking"
    target.mkdir(parents=True)
    for name in COMMITTED_PARKING_FILES:
        if name not in leave_out:
            shutil.copy(COMMITTED_PARKING_DIR / name, target / name)
    return target


def test_validator_counts_the_zone_level_garage_and_search_time_rows_of_a_tariff_table(capsys):
    # the release leaves them empty (spec Amendment E8, decision D4); a table that fills them shows up in the summary line
    from scripts.validate_parking_zones import _print_schema_2_products

    nan = float("nan")
    tariffs = pd.DataFrame({
        "zone_id": ["a", "b", "c"], "commuter_day_eur": [nan, 1.0, nan],
        "garage_hourly_rate_eur": [nan, 1.2, 1.2], "garage_billing_unit_min": [pd.NA, 60, 60],
        "garage_fee_start_h": [nan, 0.0, 0.0], "garage_fee_end_h": [nan, 24.0, 24.0],
        "search_time_min": pd.array([pd.NA, pd.NA, 5], dtype="Int64")})
    _print_schema_2_products(tariffs)
    assert capsys.readouterr().out.strip() == (
        "[parking-validate] tariff products (schema 2): commuter product on 1 of 3 rows (b); zone-level garage product on 2 "
        "rows (spec Amendment E8: garages enter through the dataset); search time on 1 rows (decision D4)")


def test_validator_accepts_a_release_without_the_garage_dataset_and_says_so(tmp_path, capsys):
    # spec Amendment E1: no stage reads the dataset yet, so a release without it validates, visibly
    from scripts.validate_parking_zones import main

    _copy_committed_release(tmp_path, leave_out=("parking_garages_2026.geojson", "parking_garages_2026_qa.csv"))
    assert main(["--data-path", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "[parking-validate] garages: no dataset at" in out and "(no stage reads it yet)" in out
    assert "[parking-validate] OK" in out


@pytest.mark.parametrize("change, message", [
    ("dataset_without_qa", "but no garage QA table at"),
    ("qa_without_dataset", "but no garage dataset at"),
    ("invalid_garage", "garage 'bs_magni': garage_hourly_rate_eur: must be a positive amount"),
    ("monthly_contradicts_qa",
     "garage 'wob_rathaus': the used monthly product is '50.00' EUR but the dataset's monthly_eur is 51.0"),
    ("tariff_contradicts_qa",
     "zone 'bs_zone_ib': commuter_day_eur is 3.75 but the used product 'monthly_bs_zone_ib_30_day' gives"),
])
def test_validator_checks_the_garage_dataset_against_itself_its_qa_table_and_the_tariffs(tmp_path, capsys, change, message):
    from scripts.validate_parking_zones import main

    target = _copy_committed_release(tmp_path)
    garages_file, qa_file = target / "parking_garages_2026.geojson", target / "parking_garages_2026_qa.csv"
    if change == "dataset_without_qa":
        qa_file.unlink()
    elif change == "qa_without_dataset":
        garages_file.unlink()
    elif change in ("invalid_garage", "monthly_contradicts_qa"):
        garage_id, column, value = (("bs_magni", "garage_hourly_rate_eur", -1.2) if change == "invalid_garage"
                                    else ("wob_rathaus", "monthly_eur", 51.0))
        document = json.loads(garages_file.read_text(encoding="utf-8"))
        features = [feature for feature in document["features"] if feature["properties"]["garage_id"] == garage_id]
        assert len(features) == 1
        features[0]["properties"][column] = value
        garages_file.write_text(json.dumps(document), encoding="utf-8")
    else:
        # the commuter product of zone Ib no longer follows from the used 30-day ticket (79.00 EUR over 21 working days)
        tariffs_file = target / "parking_tariffs_2026.csv"
        text = tariffs_file.read_text(encoding="utf-8")
        assert text.count('",3.76,') == 1
        tariffs_file.write_text(text.replace('",3.76,', '",3.75,'), encoding="utf-8")
    assert main(["--data-path", str(tmp_path)]) == 1
    assert message in capsys.readouterr().out
