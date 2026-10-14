"""Resident parking districts of Helmstedt and Wolfenbuettel from their published maps (parking cost zones v3, spec B3).

Both towns publish the resident parking area only as a map (Helmstedt: the brochure "Parken in Helmstedt", a red dotted
line along the old-town ring; Wolfenbuettel: a schematic pink area map, not to scale), and no street list. Where the
boundary of a map follows named streets the polygon is constructed from the OSM geometry of those streets, never from
free-hand vertices (owner instruction of task B3); the street names and the parameters are the method record.

* Helmstedt: the area enclosed by the ring streets (the face of the street network that contains a named street inside
  the ring), widened by half the width of the ring street, so that the dotted line lies on the street.
* Wolfenbuettel: the streets labelled inside the pink area, buffered by 25 m (ASSUMPTION R3, the method of the street
  lists) and united; holes inside (blocks that the buffers leave open) are filled, because the map colours whole blocks.

The tests run on synthetic ways in EPSG:25832.
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
X0, Y0 = 637_000.0, 5_788_000.0


@pytest.fixture(scope="module")
def mods():
    """(maps module, streets module); both import curation_common from their own directory."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        yield importlib.import_module("resident_districts_from_maps"), importlib.import_module(
            "resident_districts_from_streets")
    finally:
        sys.path.remove(str(CURATION_DIR))


def _lines(rows):
    return gpd.GeoDataFrame({"osm_id": list(range(1, len(rows) + 1)), "name": [name for name, _ in rows]},
                            geometry=[LineString([(X0 + x, Y0 + y) for x, y in coordinates]) for _, coordinates in rows],
                            crs=METRIC_CRS)


def _network(streets, lines):
    houses = gpd.GeoDataFrame({"street_key": [], "house_number": []}, geometry=[], crs=METRIC_CRS)
    return streets.OsmStreetData(lines=lines, houses=houses)


#: A square ring of 600 m x 600 m made of four named streets, a spur of one ring street that leaves the ring (like the
#: Goethestrasse east of its junction), and a street "Markt" in the middle.
RING = [("Nordring", [(0.0, 600.0), (600.0, 600.0)]), ("Ostring", [(600.0, 600.0), (600.0, 0.0)]),
        ("Suedring", [(600.0, 0.0), (0.0, 0.0)]), ("Westring", [(0.0, 0.0), (0.0, 600.0)]),
        ("Ostring", [(600.0, 300.0), (900.0, 300.0)]), ("Markt", [(250.0, 300.0), (350.0, 300.0)])]
RING_STREETS = ("Nordring", "Ostring", "Suedring", "Westring")


def _locality(streets, radius_m=1000.0):
    return streets.Locality(x=X0 + 300.0, y=Y0 + 300.0, radius_m=radius_m)


def test_the_enclosed_area_of_the_ring_streets_is_widened_by_half_a_street(mods):
    maps, streets = mods
    area, info = maps.enclosed_area_of_ring_streets(_lines(RING), RING_STREETS, "Markt", _locality(streets),
                                                    ring_half_width_m=10.0)
    assert area.area == pytest.approx(620.0 ** 2, rel=0.01)
    assert area.contains(Point(X0 + 300.0, Y0 + 300.0)) and not area.contains(Point(X0 + 750.0, Y0 + 300.0))
    assert info["face_area_m2"] == pytest.approx(600.0 ** 2, rel=0.001)
    assert info["ring_ways"] == 5


def test_an_open_ring_stops_the_step(mods):
    maps, streets = mods
    open_ring = [row for row in RING if row[0] != "Westring"]
    with pytest.raises(SystemExit, match="do not enclose"):
        maps.enclosed_area_of_ring_streets(_lines(open_ring), ("Nordring", "Ostring", "Suedring"), "Markt",
                                           _locality(streets), ring_half_width_m=10.0)


def test_a_missing_ring_street_or_inside_street_is_named(mods):
    maps, streets = mods
    with pytest.raises(SystemExit, match="Geisterring"):
        maps.enclosed_area_of_ring_streets(_lines(RING), RING_STREETS + ("Geisterring",), "Markt", _locality(streets),
                                           ring_half_width_m=10.0)
    with pytest.raises(SystemExit, match="Geistermarkt"):
        maps.enclosed_area_of_ring_streets(_lines(RING), RING_STREETS, "Geistermarkt", _locality(streets),
                                           ring_half_width_m=10.0)


def test_a_ring_street_of_the_same_name_in_another_place_is_ignored_by_locality(mods):
    maps, streets = mods
    far = _lines(RING + [("Nordring", [(8000.0, 600.0), (8600.0, 600.0)])])
    area, info = maps.enclosed_area_of_ring_streets(far, RING_STREETS, "Markt", _locality(streets), 10.0)
    assert info["ring_ways"] == 5 and info["ways_outside_locality"] == 1
    assert area.area == pytest.approx(620.0 ** 2, rel=0.01)


def test_the_helmstedt_district_row_names_its_method(mods):
    maps, streets = mods
    town = streets.TownSpec(key="helmstedt", name="Helmstedt", municipality_ags="03154028", id_prefix="he",
                            localities={"default": _locality(streets)}, bbox_wgs84=(10.0, 52.0, 11.0, 53.0),
                            infer_parity_from_range_ends=False, list_source_url="https://example.invalid/b.pdf",
                            list_document_date="not stated")
    spec = maps.RingMapSpec(code="HE_RING", name="Helmstedt Bewohnerparkzone Altstadt innerhalb des Stadtringes",
                            ring_streets=RING_STREETS, inside_street="Markt", ring_half_width_m=10.0,
                            map_reference="brochure page 1", map_description="red dotted line along the ring")
    result = maps.build_ring_district(town, spec, _network(streets, _lines(RING)), streets.Provenance())
    row = result.districts.iloc[0]
    assert row["district_id"] == "he_district_he_ring" and row["district_code"] == "HE_RING"
    assert row["municipality_ags"] == "03154028" and row["geometry_source"] == pz.MAP_STREETS_GEOMETRY_SOURCE
    assert "Nordring" in row["digitising_note"] and "half" in row["digitising_note"]
    assert json.loads(row["source_rows"])[0]["street"] == streets.normalise_street_name("Nordring")
    pz.validate_resident_districts(result.districts[list(pz.DISTRICT_PROVENANCE_COLUMNS) + ["geometry"]])


# --------------------------------------------------------------------------- Wolfenbuettel: streets of the map


BLOCKS = [("Nordstrasse", [(0.0, 150.0), (150.0, 150.0)]), ("Suedstrasse", [(0.0, 0.0), (150.0, 0.0)]),
          ("Weststrasse", [(0.0, 0.0), (0.0, 150.0)]), ("Oststrasse", [(150.0, 0.0), (150.0, 150.0)])]


def _wolfenbuettel(maps, streets, names, lines, **kwargs):
    town = streets.TownSpec(key="wolfenbuettel", name="Wolfenbuettel", municipality_ags="03158037", id_prefix="wf",
                            localities={"default": streets.Locality(X0 + 75.0, Y0 + 75.0, 400.0)},
                            bbox_wgs84=(10.0, 52.0, 11.0, 53.0), infer_parity_from_range_ends=False,
                            list_source_url="https://example.invalid/map.pdf", list_document_date="not stated")
    spec = maps.StreetMapSpec(code="WF_CENTRE", name="Wolfenbuettel Bewohnerparkgebiet Innenstadt", street_names=names,
                              map_reference="map page 1", map_description="pink area, schematic", **kwargs)
    return maps.build_street_map_district(town, spec, _network(streets, lines), streets.MatchSettings())


def test_the_streets_of_a_map_are_buffered_united_and_the_blocks_between_them_are_filled(mods, caplog):
    maps, streets = mods
    caplog.set_level(logging.INFO)
    result = _wolfenbuettel(maps, streets, ("Nordstrasse", "Suedstrasse", "Weststrasse", "Oststrasse"), _lines(BLOCKS))
    row = result.districts.iloc[0]
    geometry = row.geometry
    # The four buffers (25 m) leave a hole of 100 m x 100 m -> filled; the outer boundary is the buffered square.
    assert geometry.geom_type == "Polygon" and len(geometry.interiors) == 0
    assert geometry.contains(Point(X0 + 75.0, Y0 + 75.0))
    assert row["geometry_source"] == pz.MAP_STREETS_GEOMETRY_SOURCE
    assert result.summary["holes_filled_m2"] == pytest.approx(100.0 * 100.0, rel=0.05)
    assert "filled" in row["digitising_note"] and "schematic" in row["digitising_note"]
    assert "end where this radius ends" in row["digitising_note"] and "house-number range" not in row["digitising_note"]
    assert "holes filled" in caplog.text


def test_an_unmatched_map_street_stops_the_step(mods):
    maps, streets = mods
    with pytest.raises(SystemExit, match="Geisterstrasse"):
        _wolfenbuettel(maps, streets, ("Nordstrasse", "Geisterstrasse"), _lines(BLOCKS))


def test_a_far_street_of_the_same_name_is_clipped_by_the_locality(mods):
    maps, streets = mods
    lines = _lines(BLOCKS + [("Nordstrasse", [(9000.0, 150.0), (9150.0, 150.0)])])
    result = _wolfenbuettel(maps, streets, ("Nordstrasse", "Suedstrasse", "Weststrasse", "Oststrasse"), lines)
    assert result.districts.iloc[0].geometry.bounds[2] < X0 + 1000.0
    assert result.summary["ways_outside_locality"] == 1
