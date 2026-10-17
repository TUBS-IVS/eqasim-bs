"""Documented off-street options outside the large-lot package: reader and locator (parking cost zones v3 B7, issue #436).

Not part of the pipeline and never run by synpp: an input of ``regional_garages.py`` (``--osm-extract``,
``--owner-transcriptions-dir``). The Bad Harzburg lots of the HarzVenture page, the two free car park areas of the town's
citizen-information brochure and the private garage Bahnhofstrasse of Seesen are DOCUMENTED by a source and LOCATED by none: the
owner's packages hold their tariffs or their free status and no position. This module finds the position in the pinned
OpenStreetMap extract with ONE documented function (:func:`match_locator`) and reads the evidence the rows rest on:

* ``read_osm_extract`` reads the layers ``multipolygons`` and ``points`` of the extract with the GDAL OSM driver (the way
  ``resident_districts_from_streets.read_osm_extract`` reads the street network), after checking the file against its pinned
  SHA-256; ``osm_objects_from_layers`` turns them into the parking objects (``amenity=parking`` ways, relations and nodes, with
  their tags, the street and house number, the area in EPSG:25832) and the places (every other named or addressed object, the
  anchor of a position by name or address). No geocoding service is used: an address is located by an OpenStreetMap object that
  carries it, never by a web service and never by a typed coordinate.
* ``match_locator`` tries the keys of a locator in a fixed order and takes the first that finds a candidate: the OpenStreetMap
  object that a source names (``osm_object``), the name tag (``name_tag``, compared after ``normalise_street_name``), the
  address tags of the parking area itself (``address_tag``), the parking area NEAREST to an OpenStreetMap object that carries
  the address (``nearest_to_address``) and the parking area nearest to a named place (``nearest_to_named_place``). A nearest
  match needs a distance of at most ``MATCH_DISTANCE_LIMIT_M`` and a clear lead: a second area within the limit that is less than
  ``AMBIGUITY_MARGIN_M`` farther makes the match ``ambiguous``. Nothing found is ``unmatched``; an unmatched or ambiguous locator
  is never resolved by a guess, the option is a QA row (reason lot_no_position). ``locate_all`` logs the rates of the methods and
  warns of every locator that is not matched (fallback transparency).
* ``osm_capacity`` is the capacity rule of the large-lot package for a lot without a reported capacity (its own
  ``evidence/osm/process_osm.py``): an integer ``capacity:car``, ``capacity:motorcar`` or ``capacity`` tag, else
  ``floor(area_m2 / 25)`` of a surface polygon that is no building, no multi-storey, underground or roadside type, has one layer
  and no vehicle-only reading; never for a node. The size threshold is applied by the caller with the existing policy.
* ``read_harzventure_lots``, ``read_brochure_free_parking`` and ``read_seesen_garage_evidence`` read the evidence in place: the
  page of the HarzVenture lots and the brochure text from the owner's ``Parkdaten_Restluecken_2026-10-14.zip`` (the zip against its
  pinned SHA-256, the member against the package's own ``FILE_MANIFEST.json``), the city page of Seesen from the owner's
  transcription (against its pinned SHA-256). A page that no longer says what the rows rest on stops the step.

CRS: EPSG:25832 throughout (the extract is read in EPSG:4326 and projected), distances in m, areas in m2, money in EUR.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import tempfile
import time
import zipfile
from pathlib import Path
from typing import NamedTuple, Optional

import geopandas as gpd
import pandas as pd
import shapely

import curation_common as cc
import resident_districts_from_streets as rds

METRIC_CRS = cc.METRIC_CRS
normalise_street_name = rds.normalise_street_name

#: The pinned OpenStreetMap extract (Geofabrik, snapshot 2026-09-29): the file the large-lot package, the resident districts and
#: the zone polygons were derived from; its SHA-256 is the one of the ``.sha256`` file beside it (data record
#: parking_paid_share_2026).
OSM_EXTRACT_FILE = "niedersachsen-260929.osm.pbf"
OSM_EXTRACT_SHA256 = "c2b33b8414584686f9320971300de17f66ba66f92b45b23b24ad0a51b0218a94"
#: GDAL keeps the node index of the OSM driver in memory up to this size (MB) and writes temporary files beyond it.
DEFAULT_OSM_MAX_TMPFILE_MB = 4000
#: Margin in m around the municipality polygons of the towns whose objects are read (an address object next to the boundary).
OSM_READ_MARGIN_M = 1000.0
#: The layers and attribute filters of the read. A parking object is an ``amenity=parking`` way or relation (column ``amenity``
#: of the layer ``multipolygons``) or node (a tag in ``other_tags`` of the layer ``points``); a place is any other object with a
#: name or a house number.
OSM_POLYGON_FILTER = "amenity = 'parking' OR name IS NOT NULL OR other_tags LIKE '%addr:housenumber%'"
OSM_POINT_FILTER = "name IS NOT NULL OR other_tags LIKE '%addr:housenumber%' OR other_tags LIKE '%\"amenity\"=>\"parking\"%'"

#: The largest distance in m from the address (or the named place) to the parking area for a nearest match (inclusive). ASSUMPTION
#: (nothing committed fixes it): the HarzVenture addresses are the street numbers of the Tourist-Information and the Hotel Seela;
#: a lot that belongs to a house lies next to it, and 75 m is about the depth of one lot plus its access lane. A larger limit
#: would reach the next lot of the street; a smaller one would miss a lot behind its building.
MATCH_DISTANCE_LIMIT_M = 75.0
#: A second parking area within the limit that is farther than the nearest by less than this margin (m) makes the match ambiguous
#: (two adjacent lots cannot be told apart by the address).
AMBIGUITY_MARGIN_M = 10.0
#: ``parking=*`` types that are kerbside parking, the street itself: a documented car park (a lot with its own tariff) is never
#: one of them in a search by nearness (a name or an address tag of the object itself still matches it).
ROADSIDE_PARKING_TYPES = ("street_side", "layby", "lane", "half_on_kerb", "on_kerb")
#: The methods of a match, in the order ``match_locator`` tries them.
MATCH_METHODS = ("osm_object", "name_tag", "address_tag", "nearest_to_address", "nearest_to_named_place")

#: The floor area per space of the package's area estimate (m2); the estimate is ``floor(area_m2 / AREA_PER_SPACE_M2)``.
AREA_PER_SPACE_M2 = 25.0
OSM_INTEGER_CAPACITY = "osm_integer_capacity"
AREA_ESTIMATE = "estimated_floor_area_m2_divided_by_25"
#: ``parking=*`` types that are no surface lot (the exclusion list of the package's ``process_osm.py``).
EXCLUDED_PARKING_TYPES = ("multi-storey", "underground", "rooftop", "street_side", "layby", "lane", "half_on_kerb", "on_kerb",
                          "garage_boxes", "garage", "carports", "sheds")
_VEHICLE_ONLY = re.compile(r"\b(lkw|truck|busparkplatz|wohnmobilstellplatz)\b")
_CAPACITY_TAGS = ("capacity:car", "capacity:motorcar", "capacity")
_VEHICLE_CAPACITY_TAGS = ("capacity:bus", "capacity:hgv", "capacity:tourist_bus", "capacity:caravan")


class OsmObjects(NamedTuple):
    """The parking objects (``osm_key``, ``name``, ``name_key``, ``street_key``, ``house_number``, ``tags``, ``area_m2``,
    ``geometry``) and the other named or addressed places (``osm_key``, ``name``, ``name_key``, ``street_key``,
    ``house_number``, ``geometry`` = the object's own geometry: the outline of a building or an area, so that the distance of a
    nearest match is the distance to the outline), both in EPSG:25832. A place with several house numbers has one
    row per number."""

    parking: gpd.GeoDataFrame
    places: gpd.GeoDataFrame


class Match(NamedTuple):
    """The result of :func:`match_locator`: ``status`` (matched, unmatched or ambiguous), ``method`` (one of ``MATCH_METHODS``
    for a match or for an ambiguity, else None), ``osm_key`` (the matched object, else None), ``distance_m`` (0 for a position
    by id, name or address tag of the parking area; else the distance of the address or place to the area), ``candidates`` (the
    keys that an ambiguity could not tell apart) and ``detail`` (a sentence for the notes and the QA table)."""

    status: str
    method: Optional[str]
    osm_key: Optional[str]
    distance_m: Optional[float]
    candidates: tuple
    detail: str


# ---------------------------------------------------------------- the OSM extract
def normalise_house_number(text) -> str:
    """The key of one house number: lower case without blanks ("16 A" and "16a" are the same)."""
    return re.sub(r"\s+", "", str(text)).lower()


def _house_numbers(tags: dict) -> list:
    value = tags.get("addr:housenumber")
    return [normalise_house_number(part) for part in re.split(r"[;,]", value) if part.strip()] if value else []


def _normalised(text) -> str:
    return normalise_street_name(text) if isinstance(text, str) and text else ""


def _osm_key(osm_id, osm_way_id, layer: str) -> str:
    """The key of an object: ``way/<id>`` for a way of the layer ``multipolygons`` (its ``osm_way_id``), ``relation/<id>`` for a
    relation and ``node/<id>`` for a node of the layer ``points``."""
    if layer == "points":
        return f"node/{int(osm_id)}"
    if osm_way_id is not None and not pd.isna(osm_way_id):
        return f"way/{int(osm_way_id)}"
    return f"relation/{int(osm_id)}"


def osm_objects_from_layers(polygons: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> OsmObjects:
    """The parking objects and places of the two layers of the GDAL OSM driver (``multipolygons``: ``osm_id``, ``osm_way_id``,
    ``name``, ``amenity``, ``other_tags``; ``points``: ``osm_id``, ``name``, ``other_tags``; any CRS, projected here to
    EPSG:25832). A polygon with ``amenity=parking`` is a parking object with its area; a node whose tags say ``amenity=parking``
    is a parking object without an area; every other object with a name or a house number is a place."""
    parking, places = [], []
    for layer, frame in (("multipolygons", polygons), ("points", nodes)):
        if frame is None or len(frame) == 0:
            continue
        frame = frame.to_crs(METRIC_CRS)
        for _, row in frame.iterrows():
            tags = rds._parse_other_tags(row.get("other_tags"))
            name = row.get("name") if isinstance(row.get("name"), str) and row.get("name") else None
            key = _osm_key(row["osm_id"], row.get("osm_way_id"), layer)
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                continue
            is_parking = row.get("amenity") == "parking" if layer == "multipolygons" else tags.get("amenity") == "parking"
            street_key, numbers = _normalised(tags.get("addr:street")), _house_numbers(tags)
            if is_parking:
                parking.append({"osm_key": key, "name": name, "name_key": _normalised(name), "street_key": street_key,
                                "house_number": numbers[0] if numbers else "", "tags": tags,
                                "area_m2": float(geometry.area) if layer == "multipolygons" else math.nan,
                                "geometry": geometry})
            elif name or numbers:
                for number in numbers or [""]:
                    places.append({"osm_key": key, "name": name, "name_key": _normalised(name), "street_key": street_key,
                                   "house_number": number, "geometry": geometry})
    return _frames(parking, places)


def _frames(parking: list, places: list) -> OsmObjects:
    parking_columns = ["osm_key", "name", "name_key", "street_key", "house_number", "tags", "area_m2", "geometry"]
    place_columns = ["osm_key", "name", "name_key", "street_key", "house_number", "geometry"]
    return OsmObjects(
        parking=gpd.GeoDataFrame(pd.DataFrame(parking, columns=parking_columns), geometry="geometry", crs=METRIC_CRS),
        places=gpd.GeoDataFrame(pd.DataFrame(places, columns=place_columns), geometry="geometry", crs=METRIC_CRS))


def osm_objects_from_rows(parking: list, places: list) -> OsmObjects:
    """The objects from plain rows (the synthetic input of the tests and of callers that hold their own objects): a parking row
    has ``osm_key``, ``name``, ``tags`` and ``geometry`` (EPSG:25832; the area, the name key and the address of the tags are
    derived), a place row ``osm_key``, ``name``, ``street``, ``house_number`` and ``geometry``."""
    parking_rows = []
    for row in parking:
        tags = row.get("tags") or {}
        numbers = _house_numbers(tags)
        polygonal = row["geometry"].geom_type in ("Polygon", "MultiPolygon")
        parking_rows.append({"osm_key": row["osm_key"], "name": row.get("name"), "name_key": _normalised(row.get("name")),
                             "street_key": _normalised(tags.get("addr:street")), "house_number": numbers[0] if numbers else "",
                             "tags": tags, "area_m2": float(row["geometry"].area) if polygonal else math.nan,
                             "geometry": row["geometry"]})
    place_rows = [{"osm_key": row["osm_key"], "name": row.get("name"), "name_key": _normalised(row.get("name")),
                   "street_key": _normalised(row.get("street")),
                   "house_number": normalise_house_number(row["house_number"]) if row.get("house_number") else "",
                   "geometry": row["geometry"]} for row in places]
    return _frames(parking_rows, place_rows)


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_clip_area(polygons: gpd.GeoSeries, margin_m: float = OSM_READ_MARGIN_M):
    """The area whose OSM objects are read: the union of ``polygons`` (the municipality polygons of the towns of the run,
    EPSG:25832) with a margin of ``margin_m`` m (an address object or a lot next to the boundary)."""
    return polygons.union_all().buffer(margin_m) if hasattr(polygons, "union_all") else polygons.unary_union.buffer(margin_m)


def read_osm_extract(path, clip_area, expected_sha256: Optional[str] = None,
                     max_tmpfile_mb: int = DEFAULT_OSM_MAX_TMPFILE_MB) -> OsmObjects:
    """The parking objects and places inside ``clip_area`` (a shapely geometry in EPSG:25832, see :func:`read_clip_area`) from the
    OSM extract at ``path`` (PBF or XML) with the GDAL OSM driver, in EPSG:25832. The file must have the SHA-256
    ``expected_sha256`` (``OSM_EXTRACT_SHA256``), else ``SystemExit``. The driver reads the box around the area and scans the whole
    file once per layer (two layers: one call reads every town of the run); an object that does not touch the area is dropped."""
    import pyogrio

    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"OSM extract missing: {path}; pass the Geofabrik extract {OSM_EXTRACT_FILE} (--osm-extract)")
    wanted = expected_sha256 or OSM_EXTRACT_SHA256
    actual = file_sha256(path)
    if actual != wanted:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the pinned {wanted} of {OSM_EXTRACT_FILE}; another extract would "
                         "change the positions of the options")
    box = gpd.GeoSeries([shapely.box(*clip_area.bounds)], crs=METRIC_CRS).to_crs("EPSG:4326")
    bbox_wgs84 = tuple(float(value) for value in box.total_bounds)
    frames = {}
    with tempfile.TemporaryDirectory(prefix="gdal_osm_") as scratch:
        pyogrio.set_gdal_config_options({"CPL_TMPDIR": scratch, "OSM_MAX_TMPFILE_SIZE": str(int(max_tmpfile_mb))})
        try:
            for layer, where in (("multipolygons", OSM_POLYGON_FILTER), ("points", OSM_POINT_FILTER)):
                started = time.perf_counter()
                frame = pyogrio.read_dataframe(str(path), layer=layer, bbox=bbox_wgs84, where=where)
                frame = frame.to_crs(METRIC_CRS)
                frames[layer] = frame[frame.geometry.intersects(clip_area)]
                print(f"[documented options] read OSM layer {layer} of {path.name}: {len(frame)} features in the box, "
                      f"{len(frames[layer])} in the area, {time.perf_counter() - started:.0f} s")
        finally:
            pyogrio.set_gdal_config_options({"CPL_TMPDIR": None, "OSM_MAX_TMPFILE_SIZE": None})
    objects = osm_objects_from_layers(frames["multipolygons"], frames["points"])
    print(f"[documented options] OSM extract {path.name} (SHA-256 {actual}): {len(objects.parking)} parking objects, "
          f"{len(objects.places)} named or addressed places in the municipalities and their {OSM_READ_MARGIN_M:g} m margin")
    return objects


# ---------------------------------------------------------------- the matching function
def _nearest(points: list, parking: gpd.GeoDataFrame, limit_m: float, margin_m: float, method: str, what: str) -> Match:
    """The parking area nearest to any of ``points`` within ``limit_m`` (the rule of :func:`match_locator`); the kerbside types
    of ``ROADSIDE_PARKING_TYPES`` are the street and never a candidate."""
    parking = parking[~parking["tags"].map(lambda tags: tags.get("parking") in ROADSIDE_PARKING_TYPES)]
    best = {}
    for point in points:
        for key, geometry in zip(parking["osm_key"], parking.geometry):
            distance = float(point.distance(geometry))
            if distance <= limit_m and distance < best.get(key, math.inf):
                best[key] = distance
    if not best:
        return Match("unmatched", None, None, None, (), f"no parking area within {limit_m:g} m of {what}")
    ranked = sorted(best.items(), key=lambda item: (item[1], item[0]))
    first_key, first_distance = ranked[0]
    close = [key for key, distance in ranked if distance - first_distance < margin_m]
    if len(close) > 1:
        return Match("ambiguous", method, None, None, tuple(sorted(close)),
                     f"{len(close)} parking areas within {limit_m:g} m of {what} differ by less than {margin_m:g} m in "
                     f"distance: {', '.join(sorted(close))}")
    return Match("matched", method, first_key, round(first_distance, 2), (first_key,),
                 f"the parking area {first_key} nearest to {what}, {first_distance:.1f} m away (limit {limit_m:g} m)")


def match_locator(locator: dict, objects: OsmObjects, limit_m: float = MATCH_DISTANCE_LIMIT_M,
                  margin_m: float = AMBIGUITY_MARGIN_M) -> Match:
    """The OpenStreetMap parking object that a locator describes, by ONE documented procedure.

    A locator has any of ``osm_id`` ("way/91181792": the object that a source names), ``names`` (name tags), ``street`` with
    ``house_number`` (the address) and ``anchor_names`` (names of places beside which the area lies). The keys are tried in the
    order of ``MATCH_METHODS`` and the FIRST that finds at least one candidate decides:

    1. ``osm_object``: the object with that key must be a parking object of the extract (distance 0);
    2. ``name_tag``: parking objects whose name equals one of ``names`` after ``normalise_street_name`` (distance 0);
    3. ``address_tag``: parking objects that carry the street and the house number themselves (distance 0);
    4. ``nearest_to_address``: the parking object nearest to an OpenStreetMap place that carries the street and the house number;
    5. ``nearest_to_named_place``: the parking object nearest to a place whose name is one of ``anchor_names``.

    Methods 2 and 3 with more than one candidate and methods 4 and 5 with a second candidate that is less than ``margin_m`` farther
    than the nearest are ``ambiguous``; a nearest candidate must be at most ``limit_m`` away. A locator without any key is
    ``SystemExit`` (a mistake of the specification); ``{"unlocatable": <reason>}`` states that the source gives no way to locate
    the option and is an unmatched locator with that reason. Nothing is guessed: unmatched and ambiguous locators return no
    object."""
    if locator.get("unlocatable"):
        return Match("unmatched", None, None, None, (), str(locator["unlocatable"]))
    parking, places = objects.parking, objects.places
    street_key = _normalised(locator.get("street"))
    number = normalise_house_number(locator["house_number"]) if locator.get("house_number") else ""
    names = [_normalised(name) for name in locator.get("names") or ()]
    anchors = [_normalised(name) for name in locator.get("anchor_names") or ()]
    if not (locator.get("osm_id") or names or (street_key and number) or anchors):
        raise SystemExit(f"locator {locator!r} gives no way to locate the option (an osm_id, names, a street with a house number "
                         "or anchor_names)")
    notes = []
    if locator.get("osm_id"):
        hit = parking[parking["osm_key"] == locator["osm_id"]]
        if len(hit):
            return Match("matched", "osm_object", locator["osm_id"], 0.0, (locator["osm_id"],),
                         f"the OpenStreetMap object {locator['osm_id']} that the source names")
        notes.append(f"the named object {locator['osm_id']} is not a parking object of the extract")
    if names:
        hit = parking[parking["name_key"].isin(names)]
        if len(hit) == 1:
            key = hit["osm_key"].iloc[0]
            return Match("matched", "name_tag", key, 0.0, (key,), f"the name tag of {key} equals the documented name")
        if len(hit) > 1:
            return Match("ambiguous", "name_tag", None, None, tuple(sorted(hit["osm_key"])),
                         f"{len(hit)} parking objects carry the documented name: {', '.join(sorted(hit['osm_key']))}")
        notes.append("no parking object carries the documented name")
    if street_key and number:
        hit = parking[(parking["street_key"] == street_key) & (parking["house_number"] == number)]
        if len(hit) == 1:
            key = hit["osm_key"].iloc[0]
            return Match("matched", "address_tag", key, 0.0, (key,), f"the address tags of {key} are the documented address")
        if len(hit) > 1:
            return Match("ambiguous", "address_tag", None, None, tuple(sorted(hit["osm_key"])),
                         f"{len(hit)} parking objects carry the documented address: {', '.join(sorted(hit['osm_key']))}")
        sources = places[(places["street_key"] == street_key) & (places["house_number"] == number)]
        if len(sources):
            result = _nearest(list(sources.geometry), parking, limit_m, margin_m, "nearest_to_address",
                              f"the address object(s) {', '.join(sorted(set(sources['osm_key'])))}")
            if result.status != "unmatched":
                return result
            notes.append(result.detail)
        else:
            notes.append("no OpenStreetMap object carries the documented address")
    if anchors:
        sources = places[places["name_key"].isin(anchors)]
        if len(sources):
            result = _nearest(list(sources.geometry), parking, limit_m, margin_m, "nearest_to_named_place",
                              f"the named place(s) {', '.join(sorted(set(sources['osm_key'])))}")
            if result.status != "unmatched":
                return result
            notes.append(result.detail)
        else:
            notes.append("no OpenStreetMap place carries the documented anchor name")
    return Match("unmatched", None, None, None, (), "; ".join(notes))


def locate_all(locators: dict, objects: OsmObjects, limit_m: float = MATCH_DISTANCE_LIMIT_M,
               margin_m: float = AMBIGUITY_MARGIN_M) -> dict:
    """{key: Match} of every locator, with the rates logged: how many were matched and by which method, how many are ambiguous
    or unmatched; a warning names every locator that is not matched (its option becomes a QA row)."""
    results = {key: match_locator(locator, objects, limit_m, margin_m) for key, locator in locators.items()}
    total = len(results)
    matched = [key for key, match in results.items() if match.status == "matched"]
    by_method = {method: sum(results[key].method == method for key in matched) for method in MATCH_METHODS}
    missed = {key: match.status for key, match in results.items() if match.status != "matched"}
    print(f"[documented options] position: matched {len(matched)}/{total} ({100.0 * len(matched) / max(total, 1):.1f} %) "
          f"(distance limit {limit_m:g} m): " + ", ".join(f"{method} {count}" for method, count in by_method.items())
          + f"; ambiguous {sum(status == 'ambiguous' for status in missed.values())}, "
            f"unmatched {sum(status == 'unmatched' for status in missed.values())}")
    for key, status in missed.items():
        print(f"[documented options] WARNING: no position for {key} ({status}): {results[key].detail}; the option is a QA row")
    return results


# ---------------------------------------------------------------- the capacity rule
def osm_capacity(tags: dict, area_m2) -> tuple:
    """(capacity, method) of a parking object, the rule of the large-lot package (its ``process_osm.py``): the first integer of
    ``capacity:car``, ``capacity:motorcar`` and ``capacity`` (the generic tag not for a vehicle-only area: a name with lkw, truck,
    busparkplatz or wohnmobilstellplatz, or motorcar=no) is an ``osm_integer_capacity``; without one, ``floor(area_m2 / 25)`` is
    an ``estimated_floor_area_m2_divided_by_25`` of a surface polygon only: no parking type of ``EXCLUDED_PARKING_TYPES`` or other
    than surface, no building, underground or roof location, no disused or construction tag, one layer and level, no vehicle-only
    reading and no bus, hgv, tourist-bus or caravan capacity tag, and an area (a node has none). Otherwise (None, "unknown")."""
    text = " ".join(str(tags.get(key, "")) for key in ("name", "description", "note", "access:conditional",
                                                       "opening_hours")).lower()
    vehicle_only = bool(_VEHICLE_ONLY.search(text)) or tags.get("motorcar") == "no"
    for key in _CAPACITY_TAGS:
        value = tags.get(key)
        if value is not None and re.fullmatch(r"\d+", str(value)) and (key != "capacity" or not vehicle_only):
            return int(value), OSM_INTEGER_CAPACITY
    parking_type = tags.get("parking")
    surface = (parking_type in (None, "surface") and parking_type not in EXCLUDED_PARKING_TYPES
               and tags.get("location") not in ("underground", "roof", "rooftop")
               and tags.get("building") in (None, "no")
               and tags.get("disused") not in ("yes", "parking") and tags.get("abandoned") not in ("yes", "parking")
               and tags.get("construction") != "parking")
    single_layer = (all(tags.get(key) in (None, "0") for key in ("layer", "level"))
                    and all(tags.get(key) in (None, "0", "1") for key in ("levels", "building:levels", "parking:levels")))
    vehicle_review = any(key in tags for key in _VEHICLE_CAPACITY_TAGS)
    if (surface and single_layer and not vehicle_only and not vehicle_review and area_m2 is not None
            and not pd.isna(area_m2)):
        return int(math.floor(float(area_m2) / AREA_PER_SPACE_M2)), AREA_ESTIMATE
    return None, "unknown"


# ---------------------------------------------------------------- the evidence
RESTLUECKEN_FILE = "Parkdaten_Restluecken_2026-10-14.zip"
RESTLUECKEN_FILE_MANIFEST = "FILE_MANIFEST.json"
HARZ_EVIDENCE_DIRECTORY = "evidence/harz_windows"
HARZVENTURE_PAGE = "bh_harzventure.html"
HARZVENTURE_URL = "https://www.harzventure.de/harzventure/anfahrt"
#: The lots of the page in the order of the page (the ASCII slug of the name of each heading).
HARZVENTURE_LOT_KEYS = ("parkplatz_tourist_information", "parkplatz_gegenueber_hotel_seela",
                        "parkplatz_zwischen_hotel_seela_und_tankstelle")
BROCHURE_TEXT_FILE = "bad_harzburg_brochure_2022.txt"
#: The sentence of the municipal brochure (printed in two columns, hyphenated at the line break): both halves must be in the text.
BROCHURE_FREE_PARKING_QUOTATION = ("Kostenlos ist das Parken auf den Parkplätzen im Be", "reich Galopprennbahn und Silberbornbad")


def _verified_member(path, expected_sha256: str, package_name: str, member: str) -> tuple:
    """(bytes of the member, its SHA-256) of ``member`` of the owner's package at ``path``: the zip must have ``expected_sha256``
    and the member the SHA-256 and size of its entry in the package's own ``FILE_MANIFEST.json``; the zip is read in place."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's package {RESTLUECKEN_FILE} (--wob-evidence-zip)")
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {expected_sha256}; a changed package is never read")
    with zipfile.ZipFile(path) as archive:
        try:
            manifest = json.loads(archive.read(f"{package_name}/{RESTLUECKEN_FILE_MANIFEST}").decode("utf-8"))
        except KeyError:
            raise SystemExit(f"the package {package_name} holds no {RESTLUECKEN_FILE_MANIFEST}") from None
        entries = {entry["path"]: entry for entry in manifest["files"]}
        if member not in entries:
            raise SystemExit(f"package member {member} is not listed in the file manifest of {path.name}")
        try:
            content = archive.read(f"{package_name}/{member}")
        except KeyError:
            raise SystemExit(f"package member {member} is listed in the file manifest but missing from {path.name}") from None
    digest = hashlib.sha256(content).hexdigest()
    if digest != entries[member]["sha256"] or len(content) != entries[member]["size_bytes"]:
        raise SystemExit(f"package member {member} differs from its entry in the file manifest of {path.name} (SHA-256 {digest}, "
                         f"{len(content)} bytes): a changed member is never read")
    return content, digest


_BLOCK = re.compile(r"<p><strong>([^<]+)</strong></p>\s*<ul>(.*?)</ul>", re.DOTALL)
_ITEM = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL)


def _eur(text: str) -> float:
    return float(text.replace(",", "."))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", cc.ascii_transliteration(text).lower()).strip("_")


HARZ_FINDINGS_FILE = "harz_windows_findings.json"


def _findings_source(path, expected_sha256: str, package_name: str, source_id: str) -> dict:
    """{"url", "retrieved" (ISO date), "publication_date", "currentness"} of the source ``source_id`` in the findings file of the
    Bad Harzburg evidence (``harz_windows_findings.json``, read in place like every member): where the package states the page
    it archived, when it retrieved it and, for the brochure, that it is historical."""
    content, _ = _verified_member(path, expected_sha256, package_name, f"{HARZ_EVIDENCE_DIRECTORY}/{HARZ_FINDINGS_FILE}")
    sources = {entry["id"]: entry for entry in json.loads(content.decode("utf-8"))["sources"]}
    if source_id not in sources:
        raise SystemExit(f"{HARZ_FINDINGS_FILE} holds no source {source_id!r}: a changed package is never read")
    entry = sources[source_id]
    return {"url": entry["url"], "retrieved": str(entry["retrieved_at"])[:10], "publication_date": entry.get("publication_date"),
            "currentness": entry.get("currentness")}


def read_harzventure_lots(path, expected_sha256: str, package_name: str) -> dict:
    """{lot key: the facts of the lot} of the three parking lots of the HarzVenture page (``bh_harzventure.html`` of the owner's
    Restluecken package, read in place, the member checked against the package's file manifest). A lot is a block of the page: a
    heading ("Parkplatz Tourist-Information, Nordhaeuser Str. 4": name and, where it is stated, the address) and its list items. The
    facts are parsed from the items, none is typed: ``rate_eur`` per ``unit_min`` ("0,50 EUR / 30 Minuten"), ``max_stay_min``
    ("Hoechstparkzeit: 6 Stunden"), ``minimum_fee_eur``, ``maximum_fee_eur``, ``fee_until_h`` ("gebuehrenpflichtig bis 18 Uhr", the
    END of the fee time: the page states no start) and ``free_min`` ("15 Minuten frei", None where the lot's block does not state
    it; nothing is carried over from another lot). The key is the ASCII slug of the heading's name. ``name``, ``address``,
    ``member_sha256`` and ``url`` complete the facts. ``SystemExit`` for a page without exactly three lots or a lot that lacks
    one of the first five facts or states one twice or in another form: a changed page is never read partly."""
    content, digest = _verified_member(path, expected_sha256, package_name, f"{HARZ_EVIDENCE_DIRECTORY}/{HARZVENTURE_PAGE}")
    source = _findings_source(path, expected_sha256, package_name, "bh_harzventure")
    if source["url"] != HARZVENTURE_URL:
        raise SystemExit(f"{HARZ_FINDINGS_FILE} gives the URL {source['url']} for the HarzVenture page, not {HARZVENTURE_URL}")
    page = content.decode("utf-8")
    lots = {}
    for heading, body in _BLOCK.findall(page):
        heading = html.unescape(heading).strip()
        if not heading.startswith("Parkplatz "):
            continue
        name, _, address = heading.partition(", ")
        items = [html.unescape(re.sub(r"<[^>]+>", "", item)).strip() for item in _ITEM.findall(body)]
        facts = {"name": name, "address": address or None, "member_sha256": digest, "url": HARZVENTURE_URL,
                 "retrieved": source["retrieved"], "free_min": None}
        for item in items:
            for field, pattern, build in (
                    ("rate", r"^([\d,]+)\s*€\s*/\s*(\d+)\s*Minuten$", lambda m: {"rate_eur": _eur(m.group(1)),
                                                                                       "unit_min": int(m.group(2))}),
                    ("max_stay", r"^Höchstparkzeit:\s*(\d+)\s*Stunden$", lambda m: {"max_stay_min": 60 * int(m.group(1))}),
                    ("minimum_fee", r"^Mindestgebühr:\s*([\d,]+)\s*€$", lambda m: {"minimum_fee_eur": _eur(m.group(1))}),
                    ("maximum_fee", r"^Maximalgebühr:\s*([\d,]+)\s*€$", lambda m: {"maximum_fee_eur": _eur(m.group(1))}),
                    ("fee_until", r"^gebührenpflichtig bis (\d+) Uhr$", lambda m: {"fee_until_h": float(m.group(1))}),
                    ("free", r"^(\d+) Minuten frei$", lambda m: {"free_min": int(m.group(1))})):
                match = re.match(pattern, item)
                if match:
                    new = build(match)
                    if any(key in facts and facts[key] is not None for key in new):
                        raise SystemExit(f"{HARZVENTURE_PAGE}: the lot {heading!r} states {field} twice")
                    facts.update(new)
                    break
            else:
                raise SystemExit(f"{HARZVENTURE_PAGE}: the lot {heading!r} has the item {item!r}, which the reader does not know: "
                                 "a changed page is never read partly")
        missing = [key for key in ("rate_eur", "max_stay_min", "minimum_fee_eur", "maximum_fee_eur", "fee_until_h")
                   if key not in facts]
        if missing:
            raise SystemExit(f"{HARZVENTURE_PAGE}: the lot {heading!r} states no {missing}: a changed page is never read partly")
        lots[_slug(name)] = facts
    if tuple(lots) != HARZVENTURE_LOT_KEYS:
        raise SystemExit(f"{HARZVENTURE_PAGE} holds the parking lots {list(lots)}, not the three {list(HARZVENTURE_LOT_KEYS)} that "
                         "the options rest on: a changed page is never read partly")
    return lots


def read_brochure_free_parking(path, expected_sha256: str, package_name: str) -> dict:
    """The evidence of the free car parks at the Galopprennbahn and the Silberbornbad: the brochure text of the town (2021/22,
    ``bad_harzburg_brochure_2022.txt`` of the Restluecken package, read in place and checked against the file manifest). The
    sentence is printed in two columns and hyphenated, so both halves of ``BROCHURE_FREE_PARKING_QUOTATION`` must be in the text.
    Returns {"file", "member_sha256", "quotation" (the sentence), "url" and "retrieved" (the brochure PDF of the town's
    citizen information that the package archived, from its findings file), "publication_date", "currentness" (the package's own
    statement that the brochure is historical and not certified unchanged in 2026)}; ``SystemExit`` where a half is missing."""
    content, digest = _verified_member(path, expected_sha256, package_name, f"{HARZ_EVIDENCE_DIRECTORY}/{BROCHURE_TEXT_FILE}")
    text = content.decode("utf-8")
    missing = [part for part in BROCHURE_FREE_PARKING_QUOTATION if part not in text]
    if missing:
        raise SystemExit(f"{BROCHURE_TEXT_FILE} no longer holds the quotation {missing} that the free car parks rest on")
    source = _findings_source(path, expected_sha256, package_name, "bh_brochure_calendar")
    return {"file": BROCHURE_TEXT_FILE, "member_sha256": digest, "quotation": "".join(BROCHURE_FREE_PARKING_QUOTATION), **source}


SEESEN_PAGE_FILE = "seesen_parken_in_seesen_city_page.txt"
#: SHA-256 of the owner's transcription of the city page of Seesen (also in the data record
#: parking_owner_transcriptions_2026_10_15).
SEESEN_PAGE_SHA256 = "e0bee423a5a7ec73b26a2da44af3009b0fe1dee9332a578a175acf3b49b0a123"
SEESEN_PAGE_URL = ("https://www.stadtverwaltung-seesen.de/B%C3%BCrger/Rathaus/A-Z-Dienstleistungen/Parken-in-Seesen.php?ModID=10&"
                   "FID=235.166.1")
#: The two sentences of the city page that the free status of the garage rests on.
SEESEN_GARAGE_QUOTATIONS = (
    "In dem an der Bahnhofstraße gelegenen privaten Parkhaus darf auf den unteren Ebenen bis zur Dauer von 2 Stunden "
    "gebührenfrei geparkt werden.",
    "Die oberen Ebenen des Parkhauses können von Dauerparkern ganztägig gebührenfrei genutzt werden.")


def read_seesen_garage_evidence(path, expected_sha256: Optional[str] = None) -> dict:
    """The evidence of the free garage Bahnhofstrasse of Seesen: the owner's transcription of the city page "Parken in Seesen"
    (a text file; the owner pasted it on 2026-10-15, the pipeline downloaded nothing). The file must have ``SEESEN_PAGE_SHA256``
    and hold both ``SEESEN_GARAGE_QUOTATIONS`` (blanks collapsed). Returns {"file", "sha256", "url", "quotations"}; ``SystemExit``
    otherwise."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's transcription folder with {SEESEN_PAGE_FILE} "
                         "(--owner-transcriptions-dir)")
    wanted = expected_sha256 or SEESEN_PAGE_SHA256
    actual = file_sha256(path)
    if actual != wanted:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {wanted}; a changed transcription is never read")
    text = " ".join(path.read_text(encoding="utf-8").split())
    missing = [quotation for quotation in SEESEN_GARAGE_QUOTATIONS if quotation not in text]
    if missing:
        raise SystemExit(f"{path.name} no longer holds the quotation {missing[0]!r} that the free garage rests on")
    return {"file": path.name, "sha256": actual, "url": SEESEN_PAGE_URL, "quotations": list(SEESEN_GARAGE_QUOTATIONS)}
