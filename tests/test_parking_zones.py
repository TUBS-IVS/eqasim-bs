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
        text[column] = text[column].astype("object").where(text[column].notna(), "")
    text["resident_exempt"] = text["resident_exempt"].map({True: "true", False: "false"})
    body = text.to_csv(index=False, lineterminator="\n")
    path.write_text("\n".join(header_lines) + "\n" + body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- tariff table


def test_fixture_tariffs_load_typed():
    tariffs = pz.load_tariffs(TARIFF_FIXTURE)
    assert list(tariffs["zone_id"]) == ["fx_bs_ia", "fx_bs_ib", "fx_sz", "fx_wob", "fx_pe", "fx_res_a", "fx_campus",
                                        "fx_frac"]
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


def test_fixture_tariffs_pass_validation():
    pz.validate_tariffs(pz.load_tariffs(TARIFF_FIXTURE))


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
    assert zones.crs.to_epsg() == 25832 and len(zones) == 8 and zones["zone_id"].is_unique
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
                           "parking_zones_2026_qa.csv")
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


def test_committed_zones_carry_the_licence_notice():
    document = json.loads((COMMITTED_PARKING_DIR / "parking_zones_2026.geojson").read_text(encoding="utf-8"))
    assert "ODbL" in document["license"]
    assert "OpenStreetMap contributors" in document["attribution"]


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


def test_committed_zones_carry_real_provenance_only():
    zones = pz.load_zone_polygons(COMMITTED_PARKING_DIR / "parking_zones_2026.geojson")
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv")
    assert set(zones["geometry_source"]) <= set(pz.GEOMETRY_SOURCES)
    assert not (tariffs["source_url"] == pz.FIXTURE_MARKER).any()
    assert tariffs["source_url"].str.startswith("https://").all()
    # every assumption-grade fee window is explained in its row
    flagged = tariffs[tariffs["fee_window_source"] == "assumption"]
    assert flagged["notes"].str.contains("ASSUMPTION F1").all()


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
