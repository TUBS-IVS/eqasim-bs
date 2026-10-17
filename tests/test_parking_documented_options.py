"""The options built from the documented facilities of Bad Harzburg and Seesen (parking cost zones v3 B7, issue #436).

``regional_garages.build_documented_options`` turns the matches of ``documented_lots.match_locator`` into dataset rows and QA rows.
Pinned on a synthetic OpenStreetMap extract (a garage, a zone car park next to an address, a racecourse with a car park, two
touching car parks at a bath) and the real readers on a synthetic evidence zip: the free garage of Seesen, the free car park at the
racecourse, a lot that is a zone car park (no new option, compared with its zone row), a lot without a position (never guessed),
the size threshold of the run, and the refusals. The committed release is pinned in ``test_parking_regional_garages.py``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys

import geopandas as gpd
import pandas as pd
import pytest
from pyproj import Transformer
from shapely.geometry import Point, box

from tests.test_parking_documented_lots import (CURATION_DIR, METRIC_CRS, SEESEN_TEXT, X0, Y0, _evidence_zip, _lot, _parking,
                                                _place)

TO_WGS84 = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
BH, SE = "03153002", "03153012"
PACKAGE_LONGITUDE, PACKAGE_LATITUDE = TO_WGS84.transform(X0 + 5001.0, Y0 + 1.0)
REGIONAL_SHA256 = "a" * 64
RESTLUECKEN_SHA256 = "b" * 64
EXTRACT_SHA256 = "c" * 64


@pytest.fixture(scope="module")
def step():
    """The curation step as a module (it imports its siblings from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("regional_garages_documented_under_test",
                                                      CURATION_DIR / "regional_garages.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


def _zone_row(zone_id, **values):
    row = {"zone_id": zone_id, "hourly_rate_eur": 1.0, "billing_unit_min": 30, "free_if_stay_at_most_min": float("nan"),
           "daily_cap_eur": float("nan"), "max_stay_min": 360.0, "fee_start_h": 8.0, "fee_end_h": 18.0}
    row.update(values)
    return row


def _documented_inputs(step, tmp_path, parking, places=(), threshold=50):
    """The inputs of ``build_documented_options`` around synthetic objects: the real readers on a synthetic evidence zip and
    transcription, the zone car park of the synthetic extract as a zone, and a package facility record per option."""
    path, digest = _evidence_zip(tmp_path)
    harzventure = step.dl.read_harzventure_lots(path, digest, "Parkdaten_Restluecken_2026-10-14")
    brochure = step.dl.read_brochure_free_parking(path, digest, "Parkdaten_Restluecken_2026-10-14")
    page = tmp_path / step.dl.SEESEN_PAGE_FILE
    page.write_text(SEESEN_TEXT, encoding="utf-8")
    seesen = step.dl.read_seesen_garage_evidence(page, hashlib.sha256(page.read_bytes()).hexdigest())
    facilities = {facility: {"attributes": {"osm_way_ids": str(way)}}
                  for facility, way in zip(step.specs.ZONE_CAR_PARK_FACILITIES, (900, 901, 902, 903, 904))}
    facilities["se_parkhaus"] = {"attributes": {"osm_way_ids": "91181792", "point_role": "representative_point_not_entrance",
                                                "longitude": PACKAGE_LONGITUDE, "latitude": PACKAGE_LATITUDE}}
    zones = gpd.GeoDataFrame({"zone_id": ["bh_sole_therme"]}, geometry=[_lot(-10, -10, 60.0)], crs=METRIC_CRS)
    tariffs = pd.DataFrame([_zone_row("bh_sole_therme")])
    objects = step.dl.osm_objects_from_rows(parking, list(places))
    return {"file": {"sha256": REGIONAL_SHA256}, "facilities": facilities,
            "documented": {"objects": objects, "zones": zones, "tariffs": tariffs, "threshold": threshold,
                           "harzventure": harzventure, "brochure": brochure, "seesen": seesen,
                           "evidence_package_sha256": RESTLUECKEN_SHA256, "extract_sha256": EXTRACT_SHA256}}


def _extract_rows():
    """A synthetic extract: the garage of Seesen, the zone car park Sole-Therme next to the Tourist-Information, the racecourse
    and its car park of 100 spaces by area (2500 m2), the baths with two touching car parks."""
    garage = Point(X0 + 5000, Y0).buffer(30.0)
    parking = [
        _parking("way/91181792", garage, "Parkhaus Bahnhofstraße", {"parking": "multi-storey", "fee": "no"}),
        _parking("way/900", _lot(0, 0, 40.0), None, {"parking": "surface", "fee": "no"}),
        _parking("way/700", _lot(1010, 0, 50.0), None, {"parking": "surface", "access": "yes", "fee": "no"}),
        _parking("way/801", _lot(3000, 0, 20.0), None, {"parking": "surface"}),
        _parking("way/802", _lot(3000, 30, 20.0), None, {"parking": "surface"})]
    places = [_place("way/10", _lot(30, 20, 5.0), "Tourist-Information", "Nordhäuser Straße", "4"),
              _place("relation/20", box(X0 + 200, Y0 - 500, X0 + 1000, Y0 + 500), "Galopprennbahn Bad Harzburg"),
              _place("way/30", _lot(3000, 15, 8.0), "Silberbornbad")]
    return parking, places


def _built(step, tmp_path, **kwargs):
    parking, places = _extract_rows()
    return step.build_documented_options(_documented_inputs(step, tmp_path, parking, places, **kwargs))


def test_the_garage_of_seesen_is_a_free_option_on_the_owners_transcription(step, tmp_path, capsys):
    result = _built(step, tmp_path)
    row = {row["garage_id"]: row for row in result["rows"]}["se_parkhaus_bahnhofstrasse"]
    assert (row["facility_kind"], row["municipality_ags"], row["priced"], row["assumptions"]) == ("garage", SE, True, None)
    assert row["tariff_duration_bands"] == "0- free" and row["max_stay_min"] is None and row["capacity_reported"] is None
    assert row["tariff_rule_ids"] == "evidence:seesen_parken_in_seesen_city_page.txt"
    assert row["geometry_method"] == "osm_parking_area_representative_point"
    assert row["geometry_source_url"] == "https://www.openstreetmap.org/way/91181792"
    assert row["package_sha256"] == REGIONAL_SHA256 and row["source_date"] == "2026-10-15"
    assert row["source_url"].startswith("https://www.stadtverwaltung-seesen.de/")
    assert Point(X0 + 5000, Y0).distance(row["geometry"]) < 30.0
    notes = row["notes"]
    for quotation in ("auf den unteren Ebenen bis zur Dauer von 2 Stunden", "ganztaegig gebuehrenfrei"):
        assert quotation in notes
    assert "the owner's transcription" in notes and "SHA-256" in notes and "private" in notes and "not modelled" in notes
    assert "Cross-check: the regional package gives the point of the same OpenStreetMap way" in notes
    assert re.search(r"[0-9]+[.][0-9] m from this point", notes)
    assert "osm_object" in notes and EXTRACT_SHA256 in notes and "multi-storey" in notes
    assert result["notes"]["se_parkhaus_bahnhofstrasse"].startswith("free of charge for every stay")
    assert "documented options" in capsys.readouterr().out


def test_the_racecourse_car_park_is_a_free_option_by_the_area_estimate_and_the_brochure_is_stated_as_historical(step, tmp_path):
    row = {row["garage_id"]: row for row in _built(step, tmp_path)["rows"]}["bh_galopprennbahn"]
    assert (row["facility_kind"], row["municipality_ags"], row["tariff_duration_bands"]) == ("surface_lot", BH, "0- free")
    assert row["geometry_source_url"] == "https://www.openstreetmap.org/way/700"
    assert row["package_sha256"] == f"{REGIONAL_SHA256};{RESTLUECKEN_SHA256}" and row["source_date"] == "2026-10-14"
    assert row["source_url"] == "https://www.komsis.de/brochure.pdf"
    assert row["capacity_reported"] is None
    notes = row["notes"]
    assert "capacity of the object: 100 spaces (estimated_floor_area_m2_divided_by_25" in notes
    assert "HISTORICAL: historical; not certified unchanged in 2026" in notes
    assert "Kostenlos ist das Parken auf den Parkplaetzen im Bereich Galopprennbahn und Silberbornbad" in notes
    assert "names an AREA with several car parks" in notes and "nearest_to_named_place" in notes
    assert "access=yes" in notes and "fee=no" in notes


def test_two_touching_car_parks_at_the_baths_are_a_qa_row_without_a_position_not_a_guess(step, tmp_path, capsys):
    result = _built(step, tmp_path)
    entry = {entry["record_id"]: entry for entry in result["qa"]}["candidate_bh_silberbornbad"]
    assert entry["reason"] == "lot_no_position" and entry["municipality_ags"] == BH
    assert "ambiguous" in entry["note"] and "way/801" in entry["note"] and "way/802" in entry["note"]
    assert "the free status would rest on the brochure sentence 'Kostenlos ist das Parken" in entry["note"]
    assert "HISTORICAL: historical; not certified unchanged in 2026" in entry["note"]
    assert "way/801 (16 spaces by the OpenStreetMap capacity rule)" in entry["note"]
    assert "bh_silberbornbad" not in {row["garage_id"] for row in result["rows"]}
    assert "WARNING: no position for bh_silberbornbad (ambiguous)" in capsys.readouterr().out


def test_a_harzventure_lot_at_a_zone_car_park_is_no_new_option_and_the_page_is_compared_with_the_zone_row(step, tmp_path):
    result = _built(step, tmp_path)
    entry = {entry["record_id"]: entry for entry in result["qa"]}["candidate_bh_harzventure_tourist_information"]
    assert entry["reason"] == "zone_street_product" and entry["zone_ids"] == "bh_sole_therme"
    assert entry["amount_eur"] == "1.00"                       # 0.50 EUR per 30 min = 1.00 EUR per hour, read from the page
    note = entry["note"]
    assert "way/900" in note and "nearest_to_address" in note
    assert "the OpenStreetMap outline of the zone car park bh_sole_therme" in note
    assert "0.50 EUR per 30 min" in note and "a maximum stay of 6 h" in note and "15 min free" in note
    assert "equal: " in note and "rate per hour 1.00 EUR" in note and "maximum stay 360 min" in note
    assert "end of the fee time 18 h" in note
    assert "DIFFERENT: " in note and "free minutes 15 min on the page against none in the zone row" in note
    assert "maximum fee 6.00 EUR on the page against none in the zone row" in note and "The zone row stands" in note
    assert "the fee window of the zone row is 8-18 h" in note
    assert "bh_harzventure_tourist_information" not in {row["garage_id"] for row in result["rows"]}


def test_a_harzventure_lot_without_an_address_is_located_by_nothing_and_says_why(step, tmp_path):
    entry = {entry["record_id"]: entry for entry in _built(step, tmp_path)["qa"]}["candidate_bh_harzventure_seela_tankstelle"]
    assert entry["reason"] == "lot_no_position" and entry["zone_ids"] == ""
    assert "no address" in entry["note"] and "between two" in entry["note"] and "a maximum stay of 10 h" in entry["note"]
    assert entry["note"].count("between two") == 1 and "{'unlocatable'" not in entry["note"]
    assert "no free minutes stated" in entry["note"]          # nothing is carried over from the other lots of the page


def test_the_size_threshold_is_the_one_of_the_run_and_an_unknown_capacity_is_never_zero(step, tmp_path):
    parking, places = _extract_rows()
    small = [{**row, "geometry": _lot(1010, 0, 30.0)} if row["osm_key"] == "way/700" else row for row in parking]  # 900 m2 = 36
    at_50 = step.build_documented_options(_documented_inputs(step, tmp_path, small, places, threshold=50))
    entry = {entry["record_id"]: entry for entry in at_50["qa"]}["candidate_bh_galopprennbahn"]
    assert entry["reason"] == "lot_below_threshold" and "capacity 36" in entry["note"] and "threshold 50" in entry["note"]
    at_30 = step.build_documented_options(_documented_inputs(step, tmp_path, small, places, threshold=30))
    assert "bh_galopprennbahn" in {row["garage_id"] for row in at_30["rows"]}
    node = [{"osm_key": "way/700", "geometry": Point(X0 + 1015, Y0 + 5), "tags": {"parking": "surface"}}
            ] + [row for row in parking if row["osm_key"] != "way/700"]
    unknown = step.build_documented_options(_documented_inputs(step, tmp_path, node, places))
    assert {entry["record_id"]: entry for entry in unknown["qa"]}["candidate_bh_galopprennbahn"]["reason"] == (
        "lot_capacity_unknown")


def test_a_garage_has_no_size_rule(step, tmp_path):
    result = _built(step, tmp_path, threshold=100000)
    assert "se_parkhaus_bahnhofstrasse" in {row["garage_id"] for row in result["rows"]}


def test_a_paid_harzventure_lot_that_is_no_zone_car_park_stops_the_step_instead_of_being_approximated(step, tmp_path):
    parking, places = _extract_rows()
    parking = [row for row in parking if row["osm_key"] != "way/900"] + [
        _parking("way/55", _lot(0, 0, 40.0), None, {"parking": "surface"})]
    with pytest.raises(SystemExit, match="paid lot of its own"):
        step.build_documented_options(_documented_inputs(step, tmp_path, parking, places))


def test_a_zone_car_park_whose_zone_is_not_in_the_tariff_table_stops_the_step(step, tmp_path):
    parking, places = _extract_rows()
    inputs = _documented_inputs(step, tmp_path, parking, places)
    inputs["documented"]["tariffs"] = pd.DataFrame([_zone_row("bh_kurpark")])
    with pytest.raises(SystemExit, match="bh_sole_therme.*not in the tariff table"):
        step.build_documented_options(inputs)


def test_a_zone_car_park_that_no_zone_polygon_touches_stops_the_step(step, tmp_path):
    parking, places = _extract_rows()
    inputs = _documented_inputs(step, tmp_path, parking, places)
    inputs["documented"]["zones"] = gpd.GeoDataFrame({"zone_id": ["bh_sole_therme"]}, geometry=[box(0, 0, 1, 1)], crs=METRIC_CRS)
    with pytest.raises(SystemExit, match="no zone that touches it"):
        step.build_documented_options(inputs)


def test_a_package_that_names_no_single_way_for_a_zone_car_park_is_refused(step, tmp_path):
    parking, places = _extract_rows()
    inputs = _documented_inputs(step, tmp_path, parking, places)
    inputs["facilities"]["bh_kurpark"]["attributes"]["osm_way_ids"] = "1;2"
    with pytest.raises(SystemExit, match="no single OpenStreetMap way"):
        step.build_documented_options(inputs)


def test_the_documented_inputs_need_every_piece_and_the_polygons_of_the_towns(step, tmp_path):
    with pytest.raises(SystemExit, match="need --owner-transcriptions-dir, --wob-evidence-zip, --zones, --tariffs"):
        step._load_documented_inputs({}, tmp_path / "x.osm", None, None, None, None, None, 50, None, None)
    polygons = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], index=["03153002"], crs=METRIC_CRS)
    with pytest.raises(SystemExit, match="no municipality"):
        step._load_documented_inputs({}, tmp_path / "x.osm", tmp_path, tmp_path / "z.zip", object(), object(), polygons, 50,
                                     None, None)
    with pytest.raises(SystemExit, match="are read for the documented options"):
        step.load_garage_inputs(tmp_path, owner_transcriptions_directory=tmp_path)


# --------------------------------------------------------------------------- the committed release (pinned from the sources)
def _committed():
    from braunschweig.parking import garage_qa as pq
    from braunschweig.parking import garages as pg
    from tests.restricted_parking_data import parking_data_path

    garages = pg.load_garages(parking_data_path("parking_garages_2026.geojson")).set_index("garage_id")
    qa = pq.load_garage_qa(parking_data_path("parking_garages_2026_qa.csv"))
    return garages, qa.set_index("record_id")


def test_the_committed_release_holds_the_two_free_documented_options_with_their_evidence():
    garages, _ = _committed()
    seesen, galopp = garages.loc["se_parkhaus_bahnhofstrasse"], garages.loc["bh_galopprennbahn"]
    for row, kind, ags in ((seesen, "garage", SE), (galopp, "surface_lot", BH)):
        assert (row["facility_kind"], row["municipality_ags"], bool(row["priced"])) == (kind, ags, True)
        assert row["tariff_duration_bands"] == "0- free" and pd.isna(row["assumptions"]) and pd.isna(row["max_stay_min"])
        assert pd.isna(row["capacity_reported"]) and row["geometry_method"] == "osm_parking_area_representative_point"
        assert row["tariff_rule_ids"].startswith("evidence:") and row["source_date"].startswith("2026-10-")
    assert seesen["geometry_source_url"] == "https://www.openstreetmap.org/way/91181792"
    assert galopp["geometry_source_url"] == "https://www.openstreetmap.org/way/107233244"
    # the position of the Seesen garage against the independent point of the regional package for the same OpenStreetMap way
    # (point_role representative_point_not_entrance): both are inside the garage's outline, a metre apart
    package_point = gpd.GeoSeries([Point(10.176858045159847, 51.88866195)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    assert seesen.geometry.distance(package_point) < 2.0
    # the evidence: the transcription and its pin, the brochure and its member hash, stated as historical
    assert "e0bee423a5a7ec73b26a2da44af3009b0fe1dee9332a578a175acf3b49b0a123" in seesen["notes"]
    assert "ganztaegig gebuehrenfrei" in seesen["notes"] and "private" in seesen["notes"]
    assert "d77f196598f1faaedaad51aa53ccd81fdcbdee04bab9212696f7d15114c7f4b7" in galopp["notes"]
    assert "HISTORICAL" in galopp["notes"] and "capacity of the object: 100 spaces" in galopp["notes"]
    assert "c2b33b8414584686f9320971300de17f66ba66f92b45b23b24ad0a51b0218a94" in seesen["notes"] + galopp["notes"]


def test_the_committed_qa_table_names_the_harzventure_lots_and_the_silberbornbad_with_their_reasons():
    _, qa = _committed()
    assert "candidate_se_parkhaus" not in qa.index                 # the candidate became the garage row of the option
    assert qa.loc["garage_se_parkhaus_bahnhofstrasse", "decision"] == "priced"
    expected = {"candidate_bh_harzventure_tourist_information": ("zone_street_product", "bh_sole_therme", "1.00"),
                "candidate_bh_harzventure_hotel_seela": ("zone_street_product", "bh_grossparkplatz", "1.00"),
                "candidate_bh_harzventure_seela_tankstelle": ("lot_no_position", "", ""),
                "candidate_bh_silberbornbad": ("lot_no_position", "", "")}
    for record, (reason, zone, amount) in expected.items():
        row = qa.loc[record]
        assert (row["reason_code"], row["zone_ids"], row["amount_eur"], row["municipality_ags"]) == (reason, zone, amount, BH)
    # every HarzVenture value of the page is in the notes; the zone rows are compared, not changed
    tourist = qa.loc["candidate_bh_harzventure_tourist_information", "note"]
    assert "way/116490266" in tourist and "equal: rate per hour 1.00 EUR; billing unit 30 min; maximum stay 360 min" in tourist
    assert "free minutes 15 min on the page against none in the zone row" in tourist
    seela = qa.loc["candidate_bh_harzventure_hotel_seela", "note"]
    assert "way/51517817" in seela and "maximum stay 600 min" in seela and "a maximum stay of 10 h" in seela
    assert "way/107233248" in qa.loc["candidate_bh_silberbornbad", "note"]
    assert "way/376719842" in qa.loc["candidate_bh_silberbornbad", "note"]
