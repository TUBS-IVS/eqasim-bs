"""Resident parking districts built from the official street lists (parking cost zones v3, spec B3, issue #436).

The owner decided: "ueber die Adressen ordnen wir die Strassen zu und berechnen daraus den Bereich mit einem kleinen
Puffer und fertig". The curation step ``resident_districts_from_streets.py`` reads the street list of a town (Wolfsburg:
67 rows, zones A-G, K, VO, FA; Peine: zones A-G), matches every street to the OSM street geometry of the pipeline's
Niedersachsen extract, cuts a street at the OSM house numbers of a listed range, buffers by 25 m (ASSUMPTION R3) and
unites the pieces per zone. The tests run on synthetic OSM lines and house numbers (EPSG:25832) and pin:

* the one documented street-name normalisation (case, ss/sz, "Str."/"Strasse", hyphens, umlauts),
* the parser of the list wording (ranges, parity, "(unger.)", exclusions, qualifiers, several streets in one row),
* the geometry: whole street, house-number range cut with the one-sided buffer of a parity range, a single house
  number, the fallback ``range_not_resolved`` (counted, rate reported), union per zone, mixed-zone flag,
* Review Focus 3: a street name that exists in two places is resolved by the zone's locality, never by the first
  hit, and stops when nothing of the name lies inside the locality,
* the overlap rule between zones (the more specific footprint wins; equal specificity: the nearer street),
* an unmatched street stops the step and lists EVERY unmatched row,
* the reading of the owner's package (pinned hashes, row-count guard) and of the OSM extract (GDAL OSM driver).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
X0, Y0 = 621_000.0, 5_809_000.0
BUFFER_M = 25.0
WOB = "03103000"


@pytest.fixture(scope="module")
def rds():
    """The curation step as a module (it imports curation_common from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        yield importlib.import_module("resident_districts_from_streets")
    finally:
        sys.path.remove(str(CURATION_DIR))


# --------------------------------------------------------------------------- synthetic town


def _lines(rows):
    """OSM ways as the step reads them: ``osm_id``, ``name`` and a metric geometry."""
    return gpd.GeoDataFrame(
        {"osm_id": list(range(1, len(rows) + 1)), "name": [name for name, _ in rows]},
        geometry=[LineString([(X0 + x, Y0 + y) for x, y in coordinates]) for _, coordinates in rows], crs=METRIC_CRS)


def _houses(rds, rows):
    """OSM house numbers: rows of (street name, number, x, y) relative to the town origin."""
    return gpd.GeoDataFrame(
        {"street_key": [rds.normalise_street_name(street) for street, _, _, _ in rows],
         "house_number": [number for _, number, _, _ in rows]},
        geometry=[Point(X0 + x, Y0 + y) for _, _, x, y in rows], crs=METRIC_CRS)


def _alleestrasse_houses(rds, street="Alleestra\u00dfe"):
    """Odd numbers 1..19 on the north side (y = +12), even numbers 2..20 on the south side (y = -12) of a street along
    the x axis; number n sits at x = -190 + 20 * (ceil(n / 2) - 1), so 5..11 odd lie at x -150..-90."""
    rows = []
    for number in range(1, 21):
        x = -190.0 + 20.0 * (math.ceil(number / 2) - 1)
        rows.append((street, number, x, 12.0 if number % 2 else -12.0))
    return _houses(rds, rows)


def _town(rds, locality_radius_m=500.0, parity=True):
    return rds.TownSpec(key="testtown", name="Teststadt", municipality_ags=WOB, id_prefix="tt",
                        localities={"default": rds.Locality(x=X0, y=Y0, radius_m=locality_radius_m)},
                        bbox_wgs84=(10.0, 52.0, 11.0, 53.0), infer_parity_from_range_ends=parity,
                        list_source_url="https://example.invalid/list.pdf", list_document_date="2023-07-20")


def _network(rds, lines, houses=None):
    if houses is None:
        houses = gpd.GeoDataFrame({"street_key": [], "house_number": []}, geometry=[], crs=METRIC_CRS)
    return rds.OsmStreetData(lines=lines, houses=houses)


def _rows(rds, entries):
    """List rows (zone, raw wording, mixed flag)."""
    return [rds.ListRow(zone=zone, raw=raw, mixed=mixed) for zone, raw, mixed in entries]


ALLEE = ("Alleestra\u00dfe", [(-200.0, 0.0), (200.0, 0.0)])


def _build(rds, entries, lines=None, houses=None, town=None, settings=None):
    lines = lines if lines is not None else _lines([ALLEE])
    houses = houses if houses is not None else _alleestrasse_houses(rds)
    return rds.build_town_districts(town or _town(rds), _rows(rds, entries), _network(rds, lines, houses),
                                    settings or rds.MatchSettings())


def _by_code(result):
    return result.districts.set_index("district_code")


# --------------------------------------------------------------------------- street-name normalisation


@pytest.mark.parametrize("raw, same_as", [
    ("Goethestr.", "Goethestra\u00dfe"),
    ("Goethestrasse", "Goethestra\u00dfe"),
    ("GOETHESTRASSE", "Goethestra\u00dfe"),
    ("Friedr.-Ebert-Str.", "Friedrich-Ebert-Stra\u00dfe"),
    ("Heinr.-Heine-Str.", "Heinrich-Heine-Stra\u00dfe"),
    ("Schlo\u00dfstra\u00dfe", "Schlossstrasse"),
    ("Am  Markt", "am markt"),
    ("Rothenfelder Stra\u00dfe", "Rothenfelder  Strasse"),
    ("Am M\u00fchlengraben", "Am Muehlengraben"),
    ("Sandk\u00e4mper Stra\u00dfe", "Sandkaemper Strasse"),
    ("Breite Str.", "Breite Stra\u00dfe"),
])
def test_street_names_normalise_to_one_key(rds, raw, same_as):
    assert rds.normalise_street_name(raw) == rds.normalise_street_name(same_as)


def test_different_streets_keep_different_keys(rds):
    assert rds.normalise_street_name("Goethestra\u00dfe") != rds.normalise_street_name("Goetheplatz")
    assert rds.normalise_street_name("Kaufhof") != rds.normalise_street_name("Kaufhofpassage")


# --------------------------------------------------------------------------- the list wording


def _ranges(target):
    return [(r.low, r.high, r.parity) for r in target.ranges]


def test_a_whole_street_has_no_range(rds):
    (target,) = rds.parse_street_targets("Fichtestra\u00dfe", parity_from_range_ends=True)
    assert target.street_text == "Fichtestra\u00dfe" and target.ranges == () and not target.unresolved_qualifiers


@pytest.mark.parametrize("raw, street, ranges", [
    ("Kleiststra\u00dfe 13 - 71", "Kleiststra\u00dfe", [(13, 71, 1)]),
    ("Kleiststra\u00dfe 6 - 12", "Kleiststra\u00dfe", [(6, 12, 0)]),
    ("Friedr.-Ebert-Str.,12 - 54", "Friedr.-Ebert-Str.", [(12, 54, 0)]),
    ("Friedr.-Ebert-Str., 37 - 71", "Friedr.-Ebert-Str.", [(37, 71, 1)]),
    ("Goethestr., 1 - 47 und 51 - 59", "Goethestr.", [(1, 47, 1), (51, 59, 1)]),
    ("Heinr.-Heine-Str.,47 - 55, 36 - 44", "Heinr.-Heine-Str.", [(47, 55, 1), (36, 44, 0)]),
    ("K\u00f6hlerbergstra\u00dfe 1 - 19, 2 - 26", "K\u00f6hlerbergstra\u00dfe", [(1, 19, 1), (2, 26, 0)]),
    ("Meinstra\u00dfe, 1-31, 2-30", "Meinstra\u00dfe", [(1, 31, 1), (2, 30, 0)]),
    ("Lessingstra\u00dfe 58A - 62", "Lessingstra\u00dfe", [(58, 62, 0)]),
    ("Robert-Koch-Platz 2- 8", "Robert-Koch-Platz", [(2, 8, 0)]),
    ("Kaufhof 1- 25", "Kaufhof", [(1, 25, 1)]),
    ("Schachtweg 38", "Schachtweg", [(38, 38, None)]),
    ("Stresemannstr., 5 - 13, 8 - 16", "Stresemannstr.", [(5, 13, 1), (8, 16, 0)]),
])
def test_wolfsburg_ranges_carry_the_parity_of_their_ends(rds, raw, street, ranges):
    (target,) = rds.parse_street_targets(raw, parity_from_range_ends=True)
    assert target.street_text == street
    assert _ranges(target) == ranges


def test_a_range_whose_ends_differ_in_parity_covers_both_sides(rds):
    (target,) = rds.parse_street_targets("Luisenstra\u00dfe (Haus Nr. 1 - 18)", parity_from_range_ends=True)
    assert _ranges(target) == [(1, 18, None)]


def test_the_parity_of_the_ends_is_not_inferred_where_the_town_does_not_state_it(rds):
    (target,) = rds.parse_street_targets("Sch\u00fctzenstra\u00dfe (Haus Nr. 1 - 15)", parity_from_range_ends=False)
    assert _ranges(target) == [(1, 15, None)]


def test_unger_marks_the_odd_side_explicitly(rds):
    (target,) = rds.parse_street_targets("Braunschweiger Stra\u00dfe 31 - 79 (unger.)", parity_from_range_ends=False)
    assert _ranges(target) == [(31, 79, 1)]


def test_an_exclusion_is_not_a_range_and_is_flagged_as_an_unresolved_qualifier(rds):
    for raw in ("Am M\u00fchlengraben, ohne Nr. 22-24", "Schachtweg (au\u00dfer Nr. 38)"):
        (target,) = rds.parse_street_targets(raw, parity_from_range_ends=True)
        assert target.ranges == () and target.unresolved_qualifiers, raw
    assert rds.parse_street_targets("Schachtweg (au\u00dfer Nr. 38)", parity_from_range_ends=True)[0].street_text == "Schachtweg"


@pytest.mark.parametrize("raw, street", [
    ("Kaufhof (Rondell)", "Kaufhof"),
    ("Klieverhagen, Parkhauszuf. + Parkpl.", "Klieverhagen"),
    ("Klieverhagen, Westteil, Verbindgsstr.", "Klieverhagen"),
])
def test_a_part_of_a_street_that_no_house_number_names_is_an_unresolved_qualifier(rds, raw, street):
    (target,) = rds.parse_street_targets(raw, parity_from_range_ends=True)
    assert target.street_text == street and target.ranges == () and target.unresolved_qualifiers


def test_an_entrance_note_restricts_nothing(rds):
    targets = rds.parse_street_targets("Breite Str. 4+9 (Eingang Echternplatz und Eingang Rathausstra\u00dfe)",
                                       parity_from_range_ends=False)
    assert len(targets) == 1 and targets[0].street_text == "Breite Str."
    assert _ranges(targets[0]) == [(4, 4, None), (9, 9, None)]
    assert not targets[0].unresolved_qualifiers and targets[0].notes


def test_one_row_can_list_two_streets(rds):
    twete, breite = rds.parse_street_targets("Twete u. Breite Str. 15+16 (Eingang Twete)", parity_from_range_ends=False)
    assert (twete.street_text, twete.ranges) == ("Twete", ())
    assert breite.street_text == "Breite Str." and _ranges(breite) == [(15, 15, None), (16, 16, None)]
    wall, breite = rds.parse_street_targets("Wallstra\u00dfe u. Breite Str. 30, 31 (Eingang Wallstr.)",
                                            parity_from_range_ends=False)
    assert wall.street_text == "Wallstra\u00dfe" and _ranges(breite) == [(30, 30, None), (31, 31, None)]


def test_a_footnote_marker_is_dropped_and_a_wrapped_cell_is_one_entry(rds):
    (target,) = rds.parse_street_targets("Echternstra\u00dfe*", parity_from_range_ends=False)
    assert target.street_text == "Echternstra\u00dfe"
    cell = "Beethovenstra\u00dfe\nWoltorfer Stra\u00dfe, Haus Nr. 2-8 (Teilst\u00fcck gegen\u00fcber dem\nSch\u00fctzenplatz)"
    assert rds.split_cell_entries(cell) == [
        "Beethovenstra\u00dfe",
        "Woltorfer Stra\u00dfe, Haus Nr. 2-8 (Teilst\u00fcck gegen\u00fcber dem Sch\u00fctzenplatz)"]


# --------------------------------------------------------------------------- house numbers of OSM


@pytest.mark.parametrize("text, numbers", [("12", [12]), ("12a", [12]), ("12A", [12]), ("12-14", [12, 14]),
                                           ("35,37,39", [35, 37, 39]), ("3;5", [3, 5]), ("", []), ("ohne", [])])
def test_osm_house_numbers_are_read_as_integers(rds, text, numbers):
    assert rds.parse_osm_house_numbers(text) == numbers


# --------------------------------------------------------------------------- geometry: whole street, ranges


def test_a_street_without_range_is_buffered_by_25_m(rds):
    result = _build(rds, [("A", "Alleestr.", False)])
    area = _by_code(result).loc["A"].geometry.area
    assert area == pytest.approx(400.0 * 2 * BUFFER_M + math.pi * BUFFER_M ** 2, rel=0.01)
    assert result.summary["rows"] == 1 and result.summary["matched_targets"] == 1


def test_a_parity_range_is_cut_at_its_house_numbers_and_buffered_on_its_side_only(rds):
    result = _build(rds, [("F", "Alleestra\u00dfe 5 - 11", False), ("G", "Alleestra\u00dfe 6 - 12", False)])
    districts = _by_code(result)
    north, south = districts.loc["F"].geometry, districts.loc["G"].geometry
    # Numbers 5..11 sit at x = -150..-90; a one-sided buffer has flat ends, so 60 m x 25 m on each side.
    assert north.area == pytest.approx(60.0 * BUFFER_M, rel=0.02) and south.area == pytest.approx(60.0 * BUFFER_M, rel=0.02)
    assert north.bounds[1] >= Y0 - 0.01 and south.bounds[3] <= Y0 + 0.01
    assert north.bounds[0] == pytest.approx(X0 - 150.0, abs=0.5) and north.bounds[2] == pytest.approx(X0 - 90.0, abs=0.5)
    assert north.intersection(south).area < pz.OVERLAP_TOLERANCE_M2
    assert result.summary["ranged_targets"] == 2 and result.summary["range_resolved_targets"] == 2


def test_a_range_over_both_sides_uses_the_two_sided_buffer(rds):
    result = _build(rds, [("B", "Alleestra\u00dfe (Haus Nr. 5 - 12)", False)])
    geometry = _by_code(result).loc["B"].geometry
    # Numbers 5..12 lie at x = -150..-90 on both sides (5 and 6 at -150, 11 and 12 at -90): a 60 m piece.
    assert geometry.bounds[1] < Y0 - 20.0 and geometry.bounds[3] > Y0 + 20.0
    assert geometry.area == pytest.approx(60.0 * 2 * BUFFER_M + math.pi * BUFFER_M ** 2, rel=0.03)


def test_a_single_house_number_is_a_disc_around_its_projection(rds):
    result = _build(rds, [("E", "Alleestra\u00dfe 9", False)])
    geometry = _by_code(result).loc["E"].geometry
    assert geometry.area == pytest.approx(math.pi * BUFFER_M ** 2, rel=0.01)
    assert geometry.centroid.x == pytest.approx(X0 - 110.0, abs=0.5)


def test_a_range_with_too_few_house_numbers_uses_the_whole_street_and_says_so(rds, caplog):
    caplog.set_level(logging.INFO)
    houses = _houses(rds, [("Alleestra\u00dfe", 5, -150.0, 12.0)])  # one point of the range, two are needed
    result = _build(rds, [("A", "Alleestra\u00dfe 5 - 11", False)], houses=houses)
    geometry = _by_code(result).loc["A"].geometry
    assert geometry.area == pytest.approx(400.0 * 2 * BUFFER_M + math.pi * BUFFER_M ** 2, rel=0.01)
    rows = json.loads(_by_code(result).loc["A", "source_rows"])
    assert rows[0]["resolution"] == "range_not_resolved"
    assert result.summary["range_not_resolved_targets"] == 1 and result.summary["range_not_resolved_rate"] == 1.0
    assert "range_not_resolved" in caplog.text and "100.0 %" in caplog.text


def test_a_high_range_not_resolved_rate_is_a_warning(rds, caplog):
    caplog.set_level(logging.WARNING)
    _build(rds, [("A", "Alleestra\u00dfe 101 - 111", False)], houses=_alleestrasse_houses(rds))
    assert any(record.levelno == logging.WARNING and "range_not_resolved" in record.getMessage()
               for record in caplog.records)


def test_a_resolved_range_is_logged_as_primary_with_its_rate(rds, caplog):
    caplog.set_level(logging.INFO)
    result = _build(rds, [("F", "Alleestra\u00dfe 5 - 11", False)])
    assert result.summary["range_not_resolved_rate"] == 0.0
    assert "primary 1/1 (100.0 %)" in caplog.text and "fallback 0" in caplog.text


def test_an_unresolved_qualifier_uses_the_whole_street_and_is_counted(rds):
    result = _build(rds, [("A", "Alleestra\u00dfe (Rondell)", False)])
    rows = json.loads(_by_code(result).loc["A", "source_rows"])
    assert rows[0]["resolution"] == "qualifier_not_resolved"
    assert result.summary["qualifier_not_resolved_targets"] == 1


# --------------------------------------------------------------------------- zones: union, flag, columns


def test_the_streets_of_one_zone_are_united_into_one_district(rds):
    lines = _lines([ALLEE, ("Bergweg", [(-100.0, 300.0), (100.0, 300.0)]), ("Ostweg", [(-100.0, -300.0), (100.0, -300.0)])])
    result = _build(rds, [("A", "Alleestra\u00dfe", False), ("A", "Bergweg", False), ("B", "Ostweg", False)], lines=lines)
    assert sorted(result.districts["district_code"]) == ["A", "B"]
    district = _by_code(result).loc["A"].geometry
    assert isinstance(district, MultiPolygon) and len(district.geoms) == 2


def test_the_mixed_zone_flag_is_carried_per_row_and_per_district(rds):
    result = _build(rds, [("A", "Alleestra\u00dfe 5 - 11", True), ("B", "Alleestra\u00dfe 6 - 12", False)])
    districts = _by_code(result)
    assert districts.loc["A", "mixed_zone_rows"] == 1 and districts.loc["B", "mixed_zone_rows"] == 0
    assert json.loads(districts.loc["A", "source_rows"])[0]["mixed"] is True
    assert json.loads(districts.loc["B", "source_rows"])[0]["mixed"] is False


def test_the_district_rows_carry_the_provenance_the_loader_requires(rds):
    result = _build(rds, [("A", "Alleestra\u00dfe 5 - 11", False), ("B", "Alleestra\u00dfe 6 - 12", False)])
    districts = result.districts
    for column in pz.DISTRICT_PROVENANCE_COLUMNS + ("source_rows", "mixed_zone_rows"):
        assert column in districts.columns, column
    assert list(districts["district_id"]) == ["tt_district_a", "tt_district_b"]
    assert set(districts["municipality_ags"]) == {WOB}
    assert set(districts["geometry_source"]) == {"street_list_buffer_25m"}
    assert "street_list_buffer_25m" in pz.DISTRICT_GEOMETRY_SOURCES
    assert "ASSUMPTION R3" in districts.loc[0, "digitising_note"] and "25 m" in districts.loc[0, "digitising_note"]
    assert districts.crs.to_epsg() == 25832
    frame = districts[list(pz.DISTRICT_PROVENANCE_COLUMNS) + ["geometry"]]
    pz.validate_resident_districts(frame)


def test_the_source_rows_name_every_list_row_ascii_only(rds):
    result = _build(rds, [("A", "Alleestr. 5 - 11", False), ("A", "M\u00fchlenweg", False)],
                    lines=_lines([ALLEE, ("M\u00fchlenweg", [(0.0, 100.0), (50.0, 100.0)])]))
    text = _by_code(result).loc["A", "source_rows"]
    rows = json.loads(text)
    assert [row["raw"] for row in rows] == ["Alleestr. 5 - 11", "Muehlenweg"]
    assert all(ord(character) < 128 for character in text)


def test_the_qualifier_texts_of_the_source_rows_are_ascii_too(rds):
    result = _build(rds, [("A", "Alleestra\u00dfe (au\u00dfer Nr. 38)", False)])
    text = _by_code(result).loc["A", "source_rows"]
    assert json.loads(text)[0]["qualifiers"] == ["ausser Nr. 38"]
    assert all(ord(character) < 128 for character in text)


# --------------------------------------------------------------------------- unmatched streets stop the step


def test_an_unmatched_street_stops_the_step_and_every_unmatched_row_is_listed(rds):
    entries = [("A", "Alleestra\u00dfe", False), ("B", "Geisterweg", False), ("C", "Phantomallee 3 - 9", False)]
    with pytest.raises(SystemExit) as stop:
        _build(rds, entries)
    message = str(stop.value)
    assert "Geisterweg" in message and "Phantomallee 3 - 9" in message and "zone B" in message and "zone C" in message
    assert "zone A" not in message


# --------------------------------------------------------------------------- a square without a street way


def _square_network(rds):
    areas = gpd.GeoDataFrame({"name": ["Otto-Wels-Platz"]}, geometry=[Polygon([(X0 + 300.0, Y0 + 300.0), (X0 + 340.0, Y0 + 300.0),
                                                                              (X0 + 340.0, Y0 + 340.0), (X0 + 300.0, Y0 + 340.0)])],
                             crs=METRIC_CRS)
    return rds.OsmStreetData(lines=_lines([ALLEE]), houses=_houses(rds, []), areas=areas)


def test_a_square_that_is_no_way_is_matched_by_its_area_and_counted_as_the_fallback(rds, caplog):
    caplog.set_level(logging.INFO)
    result = rds.build_town_districts(_town(rds), _rows(rds, [("D", "Otto-Wels-Platz", True), ("A", "Alleestra\u00dfe", False)]),
                                      _square_network(rds), rds.MatchSettings())
    district = _by_code(result).loc["D"].geometry
    assert district.contains(Point(X0 + 320.0, Y0 + 320.0))
    assert json.loads(_by_code(result).loc["D", "source_rows"])[0]["street_source"] == "area_boundary"
    assert result.summary["area_matched_targets"] == 1
    assert "primary 1/2 (50.0 %), fallback 1 (50.0 %) matched by the boundary of a named area" in caplog.text


def test_a_street_found_neither_as_way_nor_as_area_still_stops(rds):
    with pytest.raises(SystemExit, match="Geisterplatz"):
        rds.build_town_districts(_town(rds), _rows(rds, [("D", "Geisterplatz", False)]), _square_network(rds),
                                 rds.MatchSettings())


# --------------------------------------------------------------------------- Review Focus 3: locality


def _two_places_lines():
    far = 5_000.0
    return _lines([("Schillerstra\u00dfe", [(-100.0, 0.0), (100.0, 0.0)]),
                   ("Schillerstra\u00dfe", [(far, 0.0), (far + 200.0, 0.0)])])


def test_an_ambiguous_street_name_is_resolved_by_locality_or_stops(rds):
    result = _build(rds, [("A", "Schillerstr.", False)], lines=_two_places_lines(), houses=_houses(rds, []))
    district = _by_code(result).loc["A"].geometry
    # Only the way inside the locality (500 m) is used; the way 5 km away is dropped and counted, never the first hit.
    assert district.area == pytest.approx(200.0 * 2 * BUFFER_M + math.pi * BUFFER_M ** 2, rel=0.01)
    assert district.bounds[2] < X0 + 200.0
    assert result.summary["ways_outside_locality"] == 1
    far_town = rds.TownSpec(key="testtown", name="Teststadt", municipality_ags=WOB, id_prefix="tt",
                            localities={"default": rds.Locality(x=X0 + 2_500.0, y=Y0 + 3_000.0, radius_m=500.0)},
                            bbox_wgs84=(10.0, 52.0, 11.0, 53.0), infer_parity_from_range_ends=True,
                            list_source_url="https://example.invalid/list.pdf", list_document_date="2023-07-20")
    with pytest.raises(SystemExit) as stop:
        _build(rds, [("A", "Schillerstr.", False)], lines=_two_places_lines(), houses=_houses(rds, []), town=far_town)
    assert "Schillerstr." in str(stop.value) and "locality" in str(stop.value)


def test_the_locality_is_per_zone(rds):
    lines = _two_places_lines()
    town = rds.TownSpec(key="testtown", name="Teststadt", municipality_ags=WOB, id_prefix="tt",
                        localities={"default": rds.Locality(x=X0, y=Y0, radius_m=500.0),
                                    "VO": rds.Locality(x=X0 + 5_100.0, y=Y0, radius_m=500.0)},
                        bbox_wgs84=(10.0, 52.0, 11.0, 53.0), infer_parity_from_range_ends=True,
                        list_source_url="https://example.invalid/list.pdf", list_document_date="2023-07-20")
    result = _build(rds, [("A", "Schillerstra\u00dfe", False), ("VO", "Schillerstra\u00dfe", False)], lines=lines,
                    houses=_houses(rds, []), town=town)
    districts = _by_code(result)
    assert districts.loc["A"].geometry.bounds[2] < X0 + 1_000.0
    assert districts.loc["VO"].geometry.bounds[0] > X0 + 4_000.0


def test_two_separate_places_inside_the_locality_are_counted_not_silent(rds, caplog):
    caplog.set_level(logging.WARNING)
    lines = _lines([("Schillerstra\u00dfe", [(-100.0, 0.0), (-20.0, 0.0)]), ("Schillerstra\u00dfe", [(300.0, 0.0), (380.0, 0.0)])])
    result = _build(rds, [("A", "Schillerstra\u00dfe", False)], lines=lines, houses=_houses(rds, []))
    rows = json.loads(_by_code(result).loc["A", "source_rows"])
    assert rows[0]["places_in_locality"] == 2 and result.summary["names_in_several_places"] == 1
    assert "several places" in caplog.text


# --------------------------------------------------------------------------- overlaps between zones


def test_overlapping_footprints_of_two_zones_go_to_the_nearer_street(rds):
    lines = _lines([("Ostweg", [(0.0, 0.0), (200.0, 0.0)]), ("Nordweg", [(100.0, 0.0), (100.0, 200.0)])])
    result = _build(rds, [("A", "Ostweg", False), ("B", "Nordweg", False)], lines=lines, houses=_houses(rds, []))
    districts = _by_code(result)
    a, b = districts.loc["A"].geometry, districts.loc["B"].geometry
    assert a.intersection(b).area < pz.OVERLAP_TOLERANCE_M2
    assert a.contains(Point(X0 + 110.0, Y0 + 4.0)) and not b.contains(Point(X0 + 110.0, Y0 + 4.0))
    assert b.contains(Point(X0 + 104.0, Y0 + 12.0)) and not a.contains(Point(X0 + 104.0, Y0 + 12.0))
    assert result.summary["overlaps_resolved"] >= 1
    pz.validate_resident_districts(result.districts[list(pz.DISTRICT_PROVENANCE_COLUMNS) + ["geometry"]])


def test_a_range_footprint_wins_the_overlap_against_a_whole_street(rds):
    result = _build(rds, [("A", "Alleestra\u00dfe 5 - 11", False), ("B", "Alleestra\u00dfe", False)])
    districts = _by_code(result)
    a, b = districts.loc["A"].geometry, districts.loc["B"].geometry
    assert a.area == pytest.approx(60.0 * BUFFER_M, rel=0.02)
    assert a.intersection(b).area < pz.OVERLAP_TOLERANCE_M2
    whole = 400.0 * 2 * BUFFER_M + math.pi * BUFFER_M ** 2
    assert b.area == pytest.approx(whole - a.area, rel=0.02)


def test_a_zone_that_loses_everything_to_another_stops_the_step(rds):
    with pytest.raises(SystemExit, match="no area"):
        _build(rds, [("A", "Alleestra\u00dfe 9", False), ("B", "Alleestra\u00dfe 9", False)])


def test_a_street_listed_for_two_zones_is_mixed_where_the_list_has_no_flag(rds):
    result = _build(rds, [("A", "Alleestra\u00dfe 5 - 11", None), ("B", "Alleestra\u00dfe 6 - 12", None),
                          ("C", "Bergweg", None)], lines=_lines([ALLEE, ("Bergweg", [(0.0, 300.0), (50.0, 300.0)])]))
    districts = _by_code(result)
    assert [districts.loc[code, "mixed_zone_rows"] for code in "ABC"] == [1, 1, 0]


def test_a_tie_between_two_whole_streets_goes_to_the_zone_whose_parking_area_names_the_street(rds):
    rows = [rds.ListRow(zone="E", raw="Alleestra\u00dfe"), rds.ListRow(zone="E", raw="Bergweg"),
            rds.ListRow(zone="G", raw="Alleestra\u00dfe", area_street_keys=frozenset({"alleestrasse"}))]
    lines = _lines([ALLEE, ("Bergweg", [(0.0, 300.0), (50.0, 300.0)])])
    result = rds.build_town_districts(_town(rds), rows, _network(rds, lines), rds.MatchSettings())
    districts = _by_code(result)
    assert districts.loc["G"].geometry.area == pytest.approx(400.0 * 2 * BUFFER_M + math.pi * BUFFER_M ** 2, rel=0.01)
    assert districts.loc["E"].geometry.bounds[1] > Y0 + 200.0
    assert result.summary["rows_without_area"] == 1


def test_the_result_is_deterministic(rds):
    entries = [("F", "Alleestra\u00dfe 5 - 11", False), ("G", "Alleestra\u00dfe 6 - 12", False), ("B", "Alleestra\u00dfe", True)]
    first = _build(rds, entries).districts.geometry.to_wkb()
    second = _build(rds, entries).districts.geometry.to_wkb()
    assert list(first) == list(second)


# --------------------------------------------------------------------------- the owner's package


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def _wolfsburg_json(rows):
    return json.dumps({"meaning": "m", "source_url": "https://example.invalid/w.pdf", "source_document_date": "2023-07-20",
                       "retrieved_on": "2026-10-14", "rows": rows}, ensure_ascii=False).encode("utf-8")


def test_the_wolfsburg_list_is_read_from_the_package_member(rds, tmp_path):
    rows = [{"resident_zone": "C", "eligible_resident_address_raw": "Am M\u00fchlengraben, ohne Nr. 22-24",
             "mixed_parking_zone": True},
            {"resident_zone": "F", "eligible_resident_address_raw": "Dantehof", "mixed_parking_zone": False}]
    member = _wolfsburg_json(rows)
    archive = tmp_path / "Parkdaten_Nachrecherche_2026-10-14.zip"
    _write_zip(archive, {rds.WOLFSBURG_LIST_MEMBER: member})
    parsed = rds.read_wolfsburg_list(archive, expected_row_count=2,
                                     expected_member_sha256=hashlib.sha256(member).hexdigest())
    assert [(row.zone, row.raw, row.mixed) for row in parsed.rows] == [
        ("C", "Am M\u00fchlengraben, ohne Nr. 22-24", True), ("F", "Dantehof", False)]
    assert parsed.document_date == "2023-07-20" and parsed.source_url == "https://example.invalid/w.pdf"


def test_a_list_with_another_row_count_stops_instead_of_adapting(rds, tmp_path):
    rows = [{"resident_zone": "F", "eligible_resident_address_raw": "Dantehof", "mixed_parking_zone": False}]
    member = _wolfsburg_json(rows)
    archive = tmp_path / "package.zip"
    _write_zip(archive, {rds.WOLFSBURG_LIST_MEMBER: member})
    with pytest.raises(SystemExit, match=r"1 rows .* 67"):
        rds.read_wolfsburg_list(archive, expected_row_count=67,
                                expected_member_sha256=hashlib.sha256(member).hexdigest())


def test_a_changed_package_member_is_never_read(rds, tmp_path):
    member = _wolfsburg_json([])
    archive = tmp_path / "package.zip"
    _write_zip(archive, {rds.WOLFSBURG_LIST_MEMBER: member})
    with pytest.raises(SystemExit, match="SHA-256"):
        rds.read_wolfsburg_list(archive, expected_row_count=0, expected_member_sha256="0" * 64)


def test_a_missing_package_member_is_named(rds, tmp_path):
    archive = tmp_path / "package.zip"
    _write_zip(archive, {"other.txt": b"x"})
    with pytest.raises(SystemExit, match="wolfsburg_address_eligibility"):
        rds.read_wolfsburg_list(archive, expected_row_count=0, expected_member_sha256="0" * 64)


@pytest.fixture(scope="module")
def owner_package():
    path = (REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking" / "raw_sources" / "municipal_2026-10-14"
            / "Parkdaten_Nachrecherche_2026-10-14.zip")
    if not path.is_file():
        pytest.skip("the owner's research package is a local, gitignored file")
    return path


def test_the_real_wolfsburg_list_has_the_67_rows_of_the_spec_in_ten_zones(rds, owner_package):
    parsed = rds.read_wolfsburg_list(owner_package)
    assert len(parsed.rows) == 67
    zones = sorted({row.zone for row in parsed.rows})
    assert zones == ["A", "B", "C", "D", "E", "F", "FA", "G", "K", "VO"]
    assert all(rds.parse_street_targets(row.raw, parity_from_range_ends=True) for row in parsed.rows)


def test_the_real_peine_list_has_29_entries_in_the_seven_zones_a_to_g(rds, owner_package):
    parsed = rds.read_peine_list(owner_package)
    assert len(parsed.rows) == 29
    assert sorted({row.zone for row in parsed.rows}) == list("ABCDEFG")
    raw = {row.raw for row in parsed.rows}
    assert "Twete u. Breite Str. 15+16 (Eingang Twete)" in raw and "Echternstra\u00dfe*" in raw
    assert all(row.mixed is None for row in parsed.rows)
    wallstrasse = [row for row in parsed.rows if row.raw.startswith("Wallstra")]
    assert {row.zone for row in wallstrasse} == {"E", "G"}
    assert [row.area_street_keys for row in wallstrasse if row.zone == "G"][0] >= {rds.normalise_street_name("Wallstra\u00dfe")}


# --------------------------------------------------------------------------- the OSM extract (GDAL OSM driver)

OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="52.4000" lon="10.7800"/>
  <node id="2" lat="52.4000" lon="10.7820"/>
  <node id="3" lat="52.4010" lon="10.7800"/>
  <node id="4" lat="52.4010" lon="10.7820"/>
  <node id="10" lat="52.4004" lon="10.7805">
    <tag k="addr:street" v="Alleestra&#223;e"/><tag k="addr:housenumber" v="5"/>
  </node>
  <node id="11" lat="52.4004" lon="10.7810"><tag k="shop" v="bakery"/></node>
  <way id="100"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/><tag k="name" v="Alleestra&#223;e"/></way>
  <way id="101"><nd ref="1"/><nd ref="3"/><tag k="highway" v="footway"/></way>
  <way id="103"><nd ref="1"/><nd ref="2"/><nd ref="4"/><nd ref="3"/><nd ref="1"/><tag k="leisure" v="park"/>
    <tag k="name" v="Rathausplatz"/></way>
  <way id="102"><nd ref="3"/><nd ref="4"/><nd ref="2"/><nd ref="1"/><nd ref="3"/><tag k="building" v="yes"/>
    <tag k="addr:street" v="Alleestra&#223;e"/><tag k="addr:housenumber" v="7a"/></way>
</osm>
"""


def test_the_osm_extract_is_read_into_named_ways_and_house_numbers(rds, tmp_path):
    path = tmp_path / "tiny.osm"
    path.write_text(OSM_XML, encoding="utf-8")
    network = rds.read_osm_extract(path, bbox_wgs84=(10.77, 52.39, 10.79, 52.41))
    assert list(network.lines["name"]) == ["Alleestra\u00dfe"]
    assert network.lines.crs.to_epsg() == 25832
    houses = network.houses.sort_values("house_number")
    assert list(houses["house_number"]) == [5, 7]
    assert set(houses["street_key"]) == {rds.normalise_street_name("Alleestra\u00dfe")}
    assert all(geometry.geom_type == "Point" for geometry in houses.geometry)
    assert list(network.areas["name"]) == ["Rathausplatz"]
    assert network.areas.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon")


# --------------------------------------------------------------------------- indicator: share inside the paid zones


def test_the_paid_zone_share_counts_only_street_paid_zones_of_the_districts_town(rds):
    import pandas as pd
    from shapely.geometry import box

    districts = gpd.GeoDataFrame({"district_id": ["tt_district_a"], "municipality_ags": [WOB]},
                                 geometry=[box(X0, Y0, X0 + 100.0, Y0 + 100.0)], crs=METRIC_CRS)
    zones = gpd.GeoDataFrame({"zone_id": ["paid_a", "campus_a", "paid_elsewhere"]},
                             geometry=[box(X0, Y0, X0 + 50.0, Y0 + 100.0), box(X0 + 50.0, Y0, X0 + 100.0, Y0 + 50.0),
                                       box(X0 + 50.0, Y0 + 50.0, X0 + 100.0, Y0 + 100.0)], crs=METRIC_CRS)
    tariffs = pd.DataFrame({"zone_id": ["paid_a", "campus_a", "paid_elsewhere"],
                            "municipality_ags": [WOB, WOB, "03157006"],
                            "zone_type": ["street_paid", "campus", "street_paid"]})
    table = rds.paid_zone_shares(districts, zones, tariffs)
    row = table.iloc[0]
    assert row["district_area_m2"] == pytest.approx(10_000.0) and row["paid_street_area_m2"] == pytest.approx(5_000.0)
    assert row["paid_street_share"] == pytest.approx(0.5)
