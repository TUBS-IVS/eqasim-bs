"""Documented off-street options of Bad Harzburg and Seesen (parking cost zones v3 B7, issue #436).

``documented_lots.py`` reads the positions of facilities that a source documents but no package locates (the three HarzVenture
lots and the two free car park areas of Bad Harzburg, the garage Bahnhofstrasse of Seesen) from the pinned OpenStreetMap extract
and matches them with ONE documented function. What is pinned here, each rule by a test that fails when the rule is removed: the
reading of the OSM layers (keys, tags, addresses, areas), the matching function on synthetic OSM rows (every method, the
precedence, the distance limit, the ambiguity rule, the unmatched case, the logged rates), the capacity rule (the package's own
order of tags and its floor(area / 25) estimate, which a building, a multi-storey type or a node never gets), the evidence gates
(the zip against its pin and its file manifest, the page of the HarzVenture lots, the quotations of the brochure and of the city
page of Seesen). The rows and QA rows built from these results are pinned in ``test_parking_regional_garages.py``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
X0, Y0 = 604_000.0, 5_790_000.0


@pytest.fixture(scope="module")
def dl():
    """The reader and matcher as a module (it imports its siblings from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("documented_lots_under_test", CURATION_DIR / "documented_lots.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


# --------------------------------------------------------------------------- synthetic OSM rows
def _parking(key, geometry, name=None, tags=None):
    return {"osm_key": key, "name": name, "tags": dict(tags or {}), "geometry": geometry}


def _place(key, geometry, name=None, street=None, house_number=None):
    return {"osm_key": key, "name": name, "street": street, "house_number": house_number, "geometry": geometry}


def _objects(dl, parking, places=()):
    return dl.osm_objects_from_rows(parking, list(places))


def _lot(x, y, side=40.0):
    """A square parking area of ``side`` m whose south-west corner is (x, y) metres from the test origin."""
    return box(X0 + x, Y0 + y, X0 + x + side, Y0 + y + side)


# --------------------------------------------------------------------------- the OSM layers
def _layer_frames(dl):
    """The two layers of the GDAL OSM driver that the reader uses, as the driver writes them (EPSG:4326, hstore other_tags)."""
    polygons = _lot(0, 0, 50.0)
    frame = gpd.GeoDataFrame(
        {"osm_id": [None, "7001", None], "osm_way_id": ["111", None, "222"], "name": ["Parkplatz Kurpark", None, "Hotel Seela"],
         "amenity": ["parking", "parking", None],
         "other_tags": ['"capacity"=>"192","parking"=>"surface"', '"addr:street"=>"Nordhäuser Straße"',
                        '"addr:street"=>"Nordhäuser Straße","addr:housenumber"=>"6"']},
        geometry=[polygons, _lot(300, 0), _lot(600, 0, 20.0)], crs=METRIC_CRS).to_crs("EPSG:4326")
    nodes = gpd.GeoDataFrame(
        {"osm_id": ["9001", "9002"], "name": [None, "Silberbornbad"],
         "other_tags": ['"addr:street"=>"Nordhäuser Str.","addr:housenumber"=>"4"', '"leisure"=>"swimming_pool"']},
        geometry=[Point(X0 + 800, Y0), Point(X0 + 900, Y0)], crs=METRIC_CRS).to_crs("EPSG:4326")
    return frame, nodes


def test_the_osm_layers_become_parking_objects_and_named_or_addressed_places(dl):
    polygons, nodes = _layer_frames(dl)
    objects = dl.osm_objects_from_layers(polygons, nodes)
    parking = objects.parking.set_index("osm_key")
    # an amenity=parking way is way/<osm_way_id>, a relation relation/<osm_id>; both have the area in EPSG:25832
    assert set(parking.index) == {"way/111", "relation/7001"}
    assert parking.crs == METRIC_CRS or str(parking.crs).endswith("25832")
    assert abs(parking.loc["way/111", "area_m2"] - 2500.0) < 0.5
    assert parking.loc["way/111", "tags"]["capacity"] == "192"
    assert parking.loc["relation/7001", "street_key"] == dl.normalise_street_name("Nordhaeuser Strasse")
    # a named or addressed object that is no parking area is a place (an anchor for a position by name or address)
    places = objects.places.set_index("osm_key")
    assert {"way/222", "node/9001", "node/9002"} <= set(places.index)
    assert places.loc["way/222", "name"] == "Hotel Seela" and places.loc["way/222", "house_number"] == "6"
    assert places.loc["node/9001", "street_key"] == dl.normalise_street_name("Nordhaeuser Str.")
    assert places.loc["node/9002", "name"] == "Silberbornbad"


def test_a_parking_node_is_a_parking_object_without_an_area(dl):
    polygons, nodes = _layer_frames(dl)
    node = gpd.GeoDataFrame({"osm_id": ["9003"], "name": ["Parkplatz Kursaal"], "other_tags": ['"amenity"=>"parking"']},
                            geometry=[Point(X0 + 5, Y0 + 5)], crs=METRIC_CRS).to_crs("EPSG:4326")
    objects = dl.osm_objects_from_layers(polygons, pd.concat([nodes, node]))
    parking = objects.parking.set_index("osm_key")
    assert "node/9003" in parking.index and pd.isna(parking.loc["node/9003", "area_m2"])
    assert "node/9003" not in set(objects.places["osm_key"])


# --------------------------------------------------------------------------- the matching function
def test_a_named_osm_object_is_matched_by_its_id_and_a_missing_one_is_not(dl):
    objects = _objects(dl, [_parking("way/91181792", _lot(0, 0), "Parkhaus Bahnhofstraße"), _parking("way/2", _lot(500, 0))])
    match = dl.match_locator({"osm_id": "way/91181792"}, objects)
    assert (match.status, match.method, match.osm_key, match.distance_m) == ("matched", "osm_object", "way/91181792", 0.0)
    gone = dl.match_locator({"osm_id": "way/1"}, objects)
    assert gone.status == "unmatched" and gone.osm_key is None and "way/1" in gone.detail


def test_the_name_tag_is_compared_in_the_normalised_form_and_two_hits_are_ambiguous(dl):
    objects = _objects(dl, [_parking("way/1", _lot(0, 0), "Parkplatz Galopprennbahn"),
                            _parking("way/2", _lot(500, 0), "Großparkplatz"), _parking("way/3", _lot(900, 0), "Großparkplatz")])
    hit = dl.match_locator({"names": ("parkplatz GALOPPRENNBAHN",)}, objects)
    assert (hit.status, hit.method, hit.osm_key, hit.distance_m) == ("matched", "name_tag", "way/1", 0.0)
    twice = dl.match_locator({"names": ("Grossparkplatz",)}, objects)
    assert twice.status == "ambiguous" and set(twice.candidates) == {"way/2", "way/3"}


def test_an_address_tag_on_the_parking_area_matches_before_any_nearest_search(dl):
    objects = _objects(dl, [_parking("way/1", _lot(0, 0), None, {"addr:street": "Nordhäuser Str.", "addr:housenumber": "12"}),
                            _parking("way/2", _lot(30, 0), None, {"addr:street": "Nordhäuser Str.", "addr:housenumber": "4"})],
                       [_place("node/5", Point(X0 + 20, Y0 + 20), None, "Nordhäuser Straße", "4")])
    match = dl.match_locator({"street": "Nordhäuser Straße", "house_number": "12"}, objects)
    assert (match.status, match.method, match.osm_key) == ("matched", "address_tag", "way/1")


def test_the_nearest_parking_area_to_the_address_within_the_limit_is_the_match_and_the_distance_is_reported(dl):
    objects = _objects(dl, [_parking("way/1", _lot(60, 0)), _parking("way/2", _lot(400, 0))],
                       [_place("node/5", Point(X0 + 10, Y0 + 20), None, "Nordhäuser Straße", "4")])
    match = dl.match_locator({"street": "Nordhäuser Straße", "house_number": "4"}, objects)
    assert (match.status, match.method, match.osm_key) == ("matched", "nearest_to_address", "way/1")
    assert match.distance_m == pytest.approx(50.0)


def test_the_distance_limit_is_inclusive_and_beyond_it_nothing_is_matched(dl):
    place = [_place("node/5", Point(X0, Y0), None, "Nordhäuser Straße", "4")]
    inside = _objects(dl, [_parking("way/1", _lot(dl.MATCH_DISTANCE_LIMIT_M, -20, 40.0))], place)
    assert dl.match_locator({"street": "Nordhäuser Straße", "house_number": "4"}, inside).osm_key == "way/1"
    beyond = _objects(dl, [_parking("way/1", _lot(dl.MATCH_DISTANCE_LIMIT_M + 1.0, -20, 40.0))], place)
    result = dl.match_locator({"street": "Nordhäuser Straße", "house_number": "4"}, beyond)
    assert result.status == "unmatched" and result.osm_key is None and "75" in result.detail
    assert dl.MATCH_DISTANCE_LIMIT_M == 75.0


def test_two_parking_areas_that_are_nearly_as_near_make_the_match_ambiguous(dl):
    place = [_place("node/5", Point(X0, Y0), None, "Nordhäuser Straße", "4")]
    close = _objects(dl, [_parking("way/1", _lot(20, -20)), _parking("way/2", _lot(-60, -20))], place)
    result = dl.match_locator({"street": "Nordhäuser Straße", "house_number": "4"}, close)
    assert result.status == "ambiguous" and set(result.candidates) == {"way/1", "way/2"} and result.osm_key is None
    clear = _objects(dl, [_parking("way/1", _lot(20, -20)), _parking("way/2", _lot(-100 - dl.AMBIGUITY_MARGIN_M, -20))], place)
    assert dl.match_locator({"street": "Nordhäuser Straße", "house_number": "4"}, clear).osm_key == "way/1"


def test_roadside_parking_is_never_the_nearest_lot_but_a_tagged_name_still_matches_it(dl):
    """A documented car park is an off-street lot: the kerbside types of OpenStreetMap are the street (a 7-space strip 14 m from
    the address would otherwise hide the lot 20 m away)."""
    roadside = _parking("way/1", _lot(0, 14, 5.0), None, {"parking": "street_side", "capacity": "7"})
    lot = _parking("way/2", _lot(0, 20, 40.0), None, {"parking": "surface"})
    objects = _objects(dl, [roadside, lot], [_place("node/5", Point(X0 - 5, Y0), None, "Weg", "4")])
    match = dl.match_locator({"street": "Weg", "house_number": "4"}, objects)
    assert (match.status, match.osm_key) == ("matched", "way/2")
    named = _objects(dl, [{**roadside, "name": "Parkplatz Rathaus"}])
    assert dl.match_locator({"names": ("Parkplatz Rathaus",)}, named).osm_key == "way/1"
    assert set(dl.ROADSIDE_PARKING_TYPES) == {"street_side", "layby", "lane", "half_on_kerb", "on_kerb"}


def test_a_position_by_a_named_place_uses_the_same_nearest_rule(dl):
    objects = _objects(dl, [_parking("way/1", _lot(50, 0)), _parking("way/2", _lot(5000, 0))],
                       [_place("node/9", Point(X0, Y0 + 10), "Silberbornbad")])
    match = dl.match_locator({"anchor_names": ("silberbornbad",)}, objects)
    assert (match.status, match.method, match.osm_key) == ("matched", "nearest_to_named_place", "way/1")
    assert dl.match_locator({"anchor_names": ("Hotel Seela",)}, objects).status == "unmatched"


def test_the_distance_to_a_large_named_area_is_the_distance_to_its_outline(dl):
    """A racecourse of a kilometre is no point: the lot at its edge is near it, the lot at its centre line is inside it."""
    objects = _objects(dl, [_parking("way/1", _lot(1010, 0)), _parking("way/2", _lot(5000, 0))],
                       [_place("relation/9", box(X0, Y0 - 500, X0 + 1000, Y0 + 500), "Galopprennbahn Bad Harzburg")])
    match = dl.match_locator({"anchor_names": ("Galopprennbahn Bad Harzburg",)}, objects)
    assert (match.status, match.osm_key, match.distance_m) == ("matched", "way/1", pytest.approx(10.0))


def test_the_methods_are_tried_in_the_documented_order(dl):
    """id before name before address tag before nearest to the address before nearest to a named place."""
    parking = [_parking("way/1", _lot(0, 0), "Parkplatz Galopprennbahn", {"addr:street": "Rennbahnweg", "addr:housenumber": "1"}),
               _parking("way/2", _lot(500, 0), "Anderer Platz", {"addr:street": "Rennbahnweg", "addr:housenumber": "2"})]
    objects = _objects(dl, parking, [_place("node/1", Point(X0 + 510, Y0 + 10), "Galopprennbahn", "Rennbahnweg", "3")])
    locator = {"osm_id": "way/2", "names": ("Parkplatz Galopprennbahn",), "street": "Rennbahnweg", "house_number": "1"}
    assert dl.match_locator(locator, objects).method == "osm_object"
    del locator["osm_id"]
    assert dl.match_locator(locator, objects).method == "name_tag"
    del locator["names"]
    assert dl.match_locator(locator, objects).method == "address_tag"
    locator = {"street": "Rennbahnweg", "house_number": "3", "anchor_names": ("Galopprennbahn",)}
    assert dl.match_locator(locator, objects).method == "nearest_to_address"
    assert dl.match_locator({"anchor_names": ("Galopprennbahn",)}, objects).method == "nearest_to_named_place"


def test_an_unlocatable_option_is_an_unmatched_locator_with_the_reason_of_its_specification(dl):
    objects = _objects(dl, [_parking("way/1", _lot(0, 0))])
    match = dl.match_locator({"unlocatable": "the page describes it relative to two places"}, objects)
    assert (match.status, match.osm_key) == ("unmatched", None) and "two places" in match.detail


def test_a_locator_without_any_key_is_refused_and_house_number_forms_are_compared_by_value(dl):
    objects = _objects(dl, [_parking("way/1", _lot(0, 0), None, {"addr:street": "Weg", "addr:housenumber": "16 A"})])
    with pytest.raises(SystemExit, match="no way to locate"):
        dl.match_locator({}, objects)
    assert dl.match_locator({"street": "Weg", "house_number": "16a"}, objects).osm_key == "way/1"


def test_the_matching_rates_are_logged_and_an_unmatched_share_is_a_warning(dl, capsys):
    objects = _objects(dl, [_parking("way/1", _lot(0, 0), "Parkplatz Kurpark")])
    results = dl.locate_all({"a": {"names": ("Parkplatz Kurpark",)}, "b": {"names": ("Parkplatz Nirgendwo",)}}, objects)
    out = capsys.readouterr().out
    assert results["a"].status == "matched" and results["b"].status == "unmatched"
    assert "[documented options] position: matched 1/2 (50.0 %)" in out and "name_tag 1" in out and "unmatched 1" in out
    assert "WARNING" in out and "b" in out
    dl.locate_all({"a": {"names": ("Parkplatz Kurpark",)}}, objects)
    assert "WARNING" not in capsys.readouterr().out


# --------------------------------------------------------------------------- the capacity rule
def test_the_capacity_follows_the_order_of_the_package_tags_then_the_area_estimate(dl):
    assert dl.osm_capacity({"capacity:car": "70", "capacity": "99"}, 2500.0) == (70, "osm_integer_capacity")
    assert dl.osm_capacity({"capacity:motorcar": "64", "capacity": "99"}, 2500.0) == (64, "osm_integer_capacity")
    assert dl.osm_capacity({"capacity": "192"}, 100.0) == (192, "osm_integer_capacity")
    # floor(area / 25): 1249.9 m2 -> 49, 1250 m2 -> 50
    assert dl.osm_capacity({"parking": "surface"}, 1249.9) == (49, "estimated_floor_area_m2_divided_by_25")
    assert dl.osm_capacity({}, 1250.0) == (50, "estimated_floor_area_m2_divided_by_25")


def test_no_capacity_is_estimated_for_what_the_package_never_estimates(dl):
    unknown = (None, "unknown")
    assert dl.osm_capacity({"parking": "multi-storey"}, 5000.0) == unknown
    assert dl.osm_capacity({"building": "parking"}, 5000.0) == unknown
    assert dl.osm_capacity({"layer": "1"}, 5000.0) == unknown
    assert dl.osm_capacity({"name": "Busparkplatz"}, 5000.0) == unknown
    assert dl.osm_capacity({"capacity:bus": "5"}, 5000.0) == unknown
    assert dl.osm_capacity({}, None) == unknown                                   # a node has no area
    assert dl.osm_capacity({"capacity": "many"}, 5000.0) == (200, "estimated_floor_area_m2_divided_by_25")
    # a generic capacity of a vehicle-only area is no car capacity
    assert dl.osm_capacity({"name": "Wohnmobilstellplatz", "capacity": "40"}, 5000.0) == unknown


# --------------------------------------------------------------------------- the evidence gates
def _zip_with(path: Path, members: dict) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


HARZVENTURE_HTML = (
    '<div class="article__text"><p><strong>Parkplatz Tourist-Information, Nordhäuser Str. 4</strong></p><ul>'
    "<li>0,50 € / 30 Minuten</li><li>Höchstparkzeit: 6 Stunden</li><li>Mindestgebühr: 0,50 €</li>"
    "<li>Maximalgebühr: 6,00 €</li><li>gebührenpflichtig bis 18 Uhr</li><li>15 Minuten frei</li></ul>"
    "<p><strong>Parkplatz gegenüber Hotel Seela, Nordhäuser Str. 12</strong></p><ul>"
    "<li>0,50 € / 30 Minuten</li><li>Höchstparkzeit: 10 Stunden</li><li>Mindestgebühr: 0,50 €</li>"
    "<li>Maximalgebühr: 10,00 €</li><li>gebührenpflichtig bis 18 Uhr</li><li>15 Minuten frei</li></ul>"
    "<p><strong>Parkplatz zwischen Hotel Seela und Tankstelle</strong></p><ul>"
    "<li>0,50 € / 30 Minuten</li><li>Höchstparkzeit: 10 Stunden</li><li>Mindestgebühr: 0,50 €</li>"
    "<li>Maximalgebühr: 10,00 €</li><li>gebührenpflichtig bis 18 Uhr</li></ul></div>")
BROCHURE_TEXT = ("Kostenlos ist das Parken auf den Parkplätzen im Be-\n   reich Galopprennbahn und Silberbornbad.\n"
                 "    Die Parkgebühr beträgt 50 Cent.\n")


FINDINGS = {"sources": [
    {"id": "bh_harzventure", "url": "https://www.harzventure.de/harzventure/anfahrt", "retrieved_at": "2026-10-14T09:42:48+00:00"},
    {"id": "bh_brochure_calendar", "url": "https://www.komsis.de/brochure.pdf", "retrieved_at": "2026-10-14",
     "publication_date": "2021 copyright; 2022-01-13 filename", "currentness": "historical; not certified unchanged in 2026"}]}


def _evidence_zip(tmp_path, html=HARZVENTURE_HTML, brochure=BROCHURE_TEXT, manifest_overrides=None, findings=FINDINGS):
    root = "Parkdaten_Restluecken_2026-10-14"
    members = {f"{root}/evidence/harz_windows/bh_harzventure.html": html.encode("utf-8"),
               f"{root}/evidence/harz_windows/bad_harzburg_brochure_2022.txt": brochure.encode("utf-8"),
               f"{root}/evidence/harz_windows/harz_windows_findings.json": json.dumps(findings).encode("utf-8")}
    manifest = {"files": [{"path": name[len(root) + 1:], "size_bytes": len(content),
                           "sha256": hashlib.sha256(content).hexdigest()} for name, content in members.items()]}
    for name, change in (manifest_overrides or {}).items():
        next(entry for entry in manifest["files"] if entry["path"].endswith(name)).update(change)
    members[f"{root}/FILE_MANIFEST.json"] = json.dumps(manifest)
    path = tmp_path / "restluecken.zip"
    return path, _zip_with(path, members)


def test_the_harzventure_page_is_read_in_place_and_every_value_comes_from_it(dl, tmp_path):
    path, digest = _evidence_zip(tmp_path)
    lots = dl.read_harzventure_lots(path, expected_sha256=digest, package_name="Parkdaten_Restluecken_2026-10-14")
    assert list(lots) == ["parkplatz_tourist_information", "parkplatz_gegenueber_hotel_seela",
                          "parkplatz_zwischen_hotel_seela_und_tankstelle"]
    first = lots["parkplatz_tourist_information"]
    assert (first["address"], first["rate_eur"], first["unit_min"], first["max_stay_min"], first["minimum_fee_eur"],
            first["maximum_fee_eur"], first["fee_until_h"], first["free_min"]) == (
        "Nordhäuser Str. 4", 0.5, 30, 360, 0.5, 6.0, 18.0, 15)
    second = lots["parkplatz_gegenueber_hotel_seela"]
    assert (second["max_stay_min"], second["maximum_fee_eur"], second["free_min"]) == (600, 10.0, 15)
    third = lots["parkplatz_zwischen_hotel_seela_und_tankstelle"]
    # the page states no free minutes and no address for the third lot: nothing is carried over from the others
    assert third["free_min"] is None and third["address"] is None and third["maximum_fee_eur"] == 10.0
    assert first["member_sha256"] == hashlib.sha256(HARZVENTURE_HTML.encode("utf-8")).hexdigest()
    assert first["retrieved"] == "2026-10-14" and first["url"] == "https://www.harzventure.de/harzventure/anfahrt"
    wrong_url = {"sources": [{**FINDINGS["sources"][0], "url": "https://elsewhere.example/page"}, FINDINGS["sources"][1]]}
    other, other_digest = _evidence_zip(tmp_path, findings=wrong_url)
    with pytest.raises(SystemExit, match="URL"):
        dl.read_harzventure_lots(other, expected_sha256=other_digest, package_name="Parkdaten_Restluecken_2026-10-14")


def test_the_evidence_zip_is_refused_when_it_or_a_member_differs_from_its_pin(dl, tmp_path):
    path, digest = _evidence_zip(tmp_path)
    with pytest.raises(SystemExit, match="SHA-256"):
        dl.read_harzventure_lots(path, expected_sha256="0" * 64, package_name="Parkdaten_Restluecken_2026-10-14")
    bad_path, bad_digest = _evidence_zip(tmp_path, manifest_overrides={"bh_harzventure.html": {"sha256": "1" * 64}})
    with pytest.raises(SystemExit, match="file manifest"):
        dl.read_harzventure_lots(bad_path, expected_sha256=bad_digest, package_name="Parkdaten_Restluecken_2026-10-14")
    with pytest.raises(SystemExit, match="missing"):
        dl.read_harzventure_lots(tmp_path / "absent.zip", expected_sha256=digest, package_name="x")


def test_a_changed_harzventure_page_is_refused_not_read_partly(dl, tmp_path):
    for broken in (HARZVENTURE_HTML.replace("Höchstparkzeit: 6 Stunden", "Höchstparkzeit: sechs Stunden"),
                   HARZVENTURE_HTML.replace("<li>gebührenpflichtig bis 18 Uhr</li>", "", 1),
                   HARZVENTURE_HTML.replace("Parkplatz Tourist-Information, ", "Parkplatz Information, ")):
        path, digest = _evidence_zip(tmp_path, html=broken)
        with pytest.raises(SystemExit, match="bh_harzventure.html"):
            dl.read_harzventure_lots(path, expected_sha256=digest, package_name="Parkdaten_Restluecken_2026-10-14")


def test_the_brochure_quotation_is_found_across_the_column_break_and_a_changed_text_is_refused(dl, tmp_path):
    path, digest = _evidence_zip(tmp_path)
    evidence = dl.read_brochure_free_parking(path, expected_sha256=digest, package_name="Parkdaten_Restluecken_2026-10-14")
    assert evidence["member_sha256"] and evidence["file"] == "bad_harzburg_brochure_2022.txt"
    assert evidence["retrieved"] == "2026-10-14" and evidence["url"] == "https://www.komsis.de/brochure.pdf"
    assert evidence["currentness"] == "historical; not certified unchanged in 2026"
    assert evidence["quotation"] == ("Kostenlos ist das Parken auf den Parkplätzen im Bereich Galopprennbahn und Silberbornbad")
    other, other_digest = _evidence_zip(tmp_path, brochure="Parken ist teuer.")
    with pytest.raises(SystemExit, match="quotation"):
        dl.read_brochure_free_parking(other, expected_sha256=other_digest, package_name="Parkdaten_Restluecken_2026-10-14")


SEESEN_TEXT = ("Parken in Seesen\nIn dem an der Bahnhofstraße gelegenen privaten Parkhaus darf auf den unteren Ebenen bis zur "
               "Dauer von 2 Stunden gebührenfrei geparkt werden. Die oberen Ebenen des Parkhauses können von Dauerparkern "
               "ganztägig gebührenfrei genutzt werden.\n")


def test_the_seesen_city_page_must_have_its_pin_and_both_quotations(dl, tmp_path):
    path = tmp_path / "seesen.txt"
    path.write_text(SEESEN_TEXT, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = dl.read_seesen_garage_evidence(path, expected_sha256=digest)
    assert evidence["sha256"] == digest and len(evidence["quotations"]) == 2
    assert all(quotation in " ".join(SEESEN_TEXT.split()) for quotation in evidence["quotations"])
    with pytest.raises(SystemExit, match="SHA-256"):
        dl.read_seesen_garage_evidence(path, expected_sha256="0" * 64)
    path.write_text(SEESEN_TEXT.replace("ganztägig", "halbtägig"), encoding="utf-8")
    changed = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match="quotation"):
        dl.read_seesen_garage_evidence(path, expected_sha256=changed)
    with pytest.raises(SystemExit, match="missing"):
        dl.read_seesen_garage_evidence(tmp_path / "absent.txt", expected_sha256=digest)


# --------------------------------------------------------------------------- the real reader on a tiny OSM file
def _osm_xml(tmp_path: Path) -> Path:
    """An OSM XML file of three nodes of an address (Nordhaeuser Str. 4), a closed way parking area with a capacity tag and a node
    parking object; the GDAL OSM driver reads it the way it reads the PBF extract."""
    lat0, lon0 = 51.8767, 10.5651
    corners = [(0, 0), (0, 40), (40, 40), (40, 0)]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="test">']
    for index, (dx, dy) in enumerate(corners, start=1):
        lines.append(f'<node id="{index}" lat="{lat0 + dy * 9e-6:.7f}" lon="{lon0 + dx * 1.45e-5:.7f}" version="1"/>')
    lines.append(f'<node id="10" lat="{lat0 + 20 * 9e-6:.7f}" lon="{lon0 + 60 * 1.45e-5:.7f}" version="1">'
                 '<tag k="addr:street" v="Nordh\u00e4user Stra\u00dfe"/><tag k="addr:housenumber" v="4"/></node>')
    lines.append(f'<node id="11" lat="{lat0 + 20 * 9e-6:.7f}" lon="{lon0 + 200 * 1.45e-5:.7f}" version="1">'
                 '<tag k="amenity" v="parking"/><tag k="name" v="Parkplatz Kursaal"/></node>')
    lines.append('<way id="500" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>'
                 '<tag k="amenity" v="parking"/><tag k="parking" v="surface"/><tag k="capacity" v="64"/></way>')
    lines.append("</osm>")
    path = tmp_path / "tiny.osm"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_the_osm_extract_is_checked_against_its_pin_then_read_and_matched(dl, tmp_path, capsys):
    path = _osm_xml(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    clip = box(*gpd.GeoSeries([box(10.56, 51.87, 10.58, 51.89)], crs="EPSG:4326").to_crs(METRIC_CRS).total_bounds)
    objects = dl.read_osm_extract(path, clip, expected_sha256=digest)
    parking = objects.parking.set_index("osm_key")
    assert {"way/500", "node/11"} <= set(parking.index)
    assert parking.loc["way/500", "tags"]["capacity"] == "64" and 1400 < parking.loc["way/500", "area_m2"] < 1800
    assert dl.osm_capacity(parking.loc["way/500", "tags"], parking.loc["way/500", "area_m2"]) == (64, "osm_integer_capacity")
    match = dl.match_locator({"street": "Nordh\u00e4user Str.", "house_number": "4"}, objects)
    assert (match.status, match.method, match.osm_key) == ("matched", "nearest_to_address", "way/500")
    assert 15.0 < match.distance_m < 25.0
    assert "read OSM layer multipolygons" in capsys.readouterr().out


def test_an_osm_extract_with_another_hash_or_missing_is_refused_and_the_clip_area_limits_the_objects(dl, tmp_path):
    path = _osm_xml(tmp_path)
    clip = box(*gpd.GeoSeries([box(10.56, 51.87, 10.58, 51.89)], crs="EPSG:4326").to_crs(METRIC_CRS).total_bounds)
    with pytest.raises(SystemExit, match="SHA-256"):
        dl.read_osm_extract(path, clip, expected_sha256="0" * 64)
    with pytest.raises(SystemExit, match="missing"):
        dl.read_osm_extract(tmp_path / "none.osm", clip, expected_sha256="0" * 64)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    elsewhere = box(*gpd.GeoSeries([box(9.0, 51.0, 9.01, 51.01)], crs="EPSG:4326").to_crs(METRIC_CRS).total_bounds)
    empty = dl.read_osm_extract(path, elsewhere, expected_sha256=digest)
    assert len(empty.parking) == 0 and len(empty.places) == 0
