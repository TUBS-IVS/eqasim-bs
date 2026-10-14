"""Resident parking districts from the official street lists (parking cost zones v3, spec B3, issue #436).

Owner decision: "ueber die Adressen ordnen wir die Strassen zu und berechnen daraus den Bereich mit einem kleinen
Puffer und fertig". Wolfsburg and Peine publish, per resident parking zone, the streets whose residents receive a
permit; neither publishes a polygon. This curation step (not part of the pipeline, never run by synpp) turns a list
into district polygons:

1. **Package.** The lists come from the owner's research package ``Parkdaten_Nachrecherche_2026-10-14.zip`` (SHA-256 in
   ``PACKAGE_SHA256``, also in the data record parking_tariffs_2026): the Wolfsburg list of 67 rows (zones A-G, K, VO,
   FA; JSON member, official list of 2023-07-20) and the Peine list (zones A-G, a PDF table; 29 street entries). Both
   members are read straight from the zip and checked against their pinned SHA-256; a list whose row count differs
   from the expected one stops the step (it is never adapted silently).
2. **Street names.** One function (``normalise_street_name``) folds case, umlauts, ss/sz, "Str."/"Strasse",
   "Friedr."/"Heinr.", hyphens and spaces; a list street and an OSM way match when their keys are equal.
3. **Locality.** A street name exists in many places (Wolfsburg: Stadtmitte, Fallersleben, Vorsfelde, ...). Each zone
   has a locality (a reference point and a radius in EPSG:25832); only OSM ways within it are used, the others are
   counted (``ways_outside_locality``). A name with no way inside the locality stops the step; so does any other
   street of the list that finds no way (every unmatched row is listed, nothing is dropped silently).
4. **House numbers.** A street with a house-number range is cut at the OSM house numbers (``addr:housenumber`` of
   nodes and building areas whose ``addr:street`` has the same key, within ``max_house_distance_m`` of the street):
   the piece of the street between the projections of the extreme houses of the range. A range whose two ends have the
   same parity lies on one side of the street ("1 - 25" = the odd numbers; Wolfsburg's list interleaves zones this
   way, e.g. Schillerstr. 1-25 / 2-30 / 31-57 / 38-66): the piece is then buffered on the side of its houses only
   (parity rule of ``TownSpec.infer_parity_from_range_ends``). A range with fewer than ``min_range_points`` houses is
   ``range_not_resolved``: the whole street is used, flagged in ``source_rows`` and counted as the fallback rate.
5. **Buffer and union.** Every street piece is buffered by 25 m (``--buffer-m``; ASSUMPTION R3, the width of a street
   with both kerbs) and the pieces of a zone are united. The result is a geographic envelope of the streets whose
   residents get permits, NOT the legal parking area of a permit and no proof of parking supply.
6. **Overlaps.** Footprints of different zones overlap near junctions and where a street is listed for two zones
   (the list marks those rows ``mixed_parking_zone``). The more specific footprint (house-number range or single
   number) wins against a whole street; between equally specific footprints the nearer street wins, ties go to the
   street that the zone's own parking-area column names (Peine) and then to the earlier zone code. Districts of a town
   never overlap.

CRS: EPSG:25832 throughout (metres); OSM coordinates are reprojected on reading. Unit-bearing names: ``*_m`` metres,
``*_m2`` square metres.

Usage::

    python scripts/curation/parking_zones_2026/resident_districts_from_streets.py --town wolfsburg \\
        --package eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-14/Parkdaten_Nachrecherche_2026-10-14.zip \\
        --osm eqasim-data/data/braunschweig/parking/raw_osm/niedersachsen-260929.osm.pbf --out wolfsburg_districts.gpkg
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.ops import substring

import curation_common as cc

# The script runs from its own directory (curation_common); the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import zones as pz  # noqa: E402

log = logging.getLogger(__name__)

METRIC_CRS = cc.METRIC_CRS
#: ASSUMPTION R3: the width of a street with both kerbs.
DEFAULT_BUFFER_M = 25.0
#: The ISO date the owner supplied the package: ``source_date`` and ``digitised_on`` of the districts (a fixed value, so
#: that two runs give the same bytes).
SUPPLIED_ON = "2026-10-14"

# ---------------------------------------------------------------- the owner's package (pinned)
PACKAGE_NAME = "Parkdaten_Nachrecherche_2026-10-14"
PACKAGE_SHA256 = "5a81c0b94da2a078540be2c58299bf246a269733ff69598e9a35b1faa4fad3bb"
WOLFSBURG_LIST_MEMBER = f"{PACKAGE_NAME}/data/resident_wolfsburg_address_eligibility.json"
WOLFSBURG_LIST_MEMBER_SHA256 = "525ac99eeb929bdd067d0012a9759b65f60cc0a81556f871c4cf4bbf1475057c"
#: Spec v3 B3: "Wolfsburg: 67 rows zones A-G, K, VO, FA with house-number ranges".
WOLFSBURG_EXPECTED_ROWS = 67
PEINE_LIST_MEMBER = f"{PACKAGE_NAME}/raw_sources/resident_other_cities/peine_zone_street_list.pdf"
PEINE_LIST_MEMBER_SHA256 = "c9c2208e3d9106a9f9930a24cc12d52a5153afc215a84059a0936dcc4f16e99f"
#: The spec states no row count for Peine ("zones A-G"); 29 street entries (A 6, B 2, C 5, D 5, E 8, F 2, G 1) are what
#: the table of the PDF holds on the day of the package; another count stops the step (the list changed or the table
#: could not be read as before).
PEINE_EXPECTED_ROWS = 29
PEINE_LIST_URL = ("https://www.peine.de/de-wAssets/docs/mediadaten_stadt/eigene_daten/Buergerbuero/Team-A/"
                  "Parkzonen-Bewohnerparkausweis.pdf")

# ---------------------------------------------------------------- tuning constants of the OSM reading
#: GDAL keeps the node index of the OSM driver in memory up to this size (MB) and writes temporary files beyond it.
DEFAULT_OSM_MAX_TMPFILE_MB = 4000
#: Highways with a name; every class, because a listed street may be a pedestrian street or a service way.
OSM_LINE_FILTER = "highway IS NOT NULL AND name IS NOT NULL"
OSM_ADDRESS_FILTER = "other_tags LIKE '%addr:housenumber%'"
#: Named areas that are no buildings: a listed "street" that is a square or park without a highway way (Wolfsburg:
#: Otto-Wels-Platz) is matched by the boundary of such an area, as a counted fallback.
OSM_AREA_FILTER = OSM_ADDRESS_FILTER + " OR (name IS NOT NULL AND building IS NULL)"
_HSTORE_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')
_HSTORE_ESCAPE = re.compile(r"\\(.)")

#: Footprint kinds in the order of specificity (lower wins an overlap).
KIND_RANGE, KIND_WHOLE = 0, 1


# ---------------------------------------------------------------- settings and specifications


@dataclass(frozen=True)
class MatchSettings:
    """Parameters of the matching; every value that shapes a polygon is recorded in the district notes."""

    #: ASSUMPTION R3.
    buffer_m: float = DEFAULT_BUFFER_M
    #: An OSM house number counts for a street if it lies within this distance of the street's ways.
    max_house_distance_m: float = 60.0
    #: A range with fewer matching house numbers is ``range_not_resolved`` (a single number needs one).
    min_range_points: int = 2
    #: Share of the houses of a parity range on one side of the street that makes the buffer one-sided.
    side_agreement: float = 0.9
    #: A ``range_not_resolved`` rate above this share of the ranged targets is a warning.
    range_warning_rate: float = 0.2
    #: Spacing of the street samples of the nearer-street split, metres.
    sample_spacing_m: float = 2.0
    #: Parts of a district smaller than this are dropped after the overlap split (they are cut slivers).
    minimum_part_m2: float = 1.0
    #: Precision grid of the overlay operations, metres.
    grid_m: float = 0.01
    #: Fill the holes of a district (the blocks between its street buffers). Only for the maps that colour whole
    #: blocks (Wolfenbuettel); the street lists give streets, and the blocks between them are no part of a district.
    fill_holes: bool = False


@dataclass(frozen=True)
class Locality:
    """Where the streets of a zone lie: a reference point (EPSG:25832) and a radius, metres."""

    x: float
    y: float
    radius_m: float


@dataclass(frozen=True)
class TownSpec:
    """One town of the step: identity, the localities of its zones and the conventions of its list."""

    key: str
    name: str
    municipality_ags: str
    id_prefix: str
    #: zone code -> Locality; the key "default" serves every zone without its own entry.
    localities: dict
    #: (west, south, east, north) in WGS84 of the OSM reading: covers the whole municipality.
    bbox_wgs84: tuple
    #: Whether a range whose two ends share their parity lies on one side of the street (evidenced by the interleaved
    #: ranges of the Wolfsburg list; not stated by the Peine list).
    infer_parity_from_range_ends: bool
    list_source_url: str
    list_document_date: str
    #: ``geometry_source`` of the districts (the street lists: buffered street pieces; the maps of Helmstedt and
    #: Wolfenbuettel: streets named by the map).
    geometry_source: str = pz.STREET_LIST_BUFFER_GEOMETRY_SOURCE
    #: How the note names the source of the streets.
    source_kind: str = "the official street list"
    #: Extra sentence of the note of a map district about the extent of its streets (maps only).
    extent_note: str = ""


@dataclass(frozen=True)
class Provenance:
    """Where the inputs came from, written into the district notes."""

    package_name: str = "not stated"
    package_sha256: str = "not stated"
    osm_extract: str = "not stated"
    osm_sha256: str = "not stated"


class OsmStreetData(NamedTuple):
    """The OSM data of one town in EPSG:25832: named highway ways (``osm_id``, ``name``, geometry), house numbers
    (``street_key``, ``house_number`` as one row per number, point geometry) and, optionally, named areas that are no
    buildings (``name``, polygon geometry; the fallback for a listed square without a highway way)."""

    lines: gpd.GeoDataFrame
    houses: gpd.GeoDataFrame
    areas: Optional[gpd.GeoDataFrame] = None


@dataclass(frozen=True)
class ListRow:
    """One row of an official list: a zone and the wording of its street entry.

    ``mixed`` is the list's own flag (Wolfsburg) or None where the list has none (Peine; then the step derives it: the
    street is listed for more than one zone). ``area_street_keys`` holds the street keys of the zone's own parking-area
    column where the list has one (Peine)."""

    zone: str
    raw: str
    mixed: Optional[bool] = None
    area_street_keys: frozenset = frozenset()


@dataclass(frozen=True)
class ParsedList:
    rows: list
    source_url: str
    document_date: str
    #: zone -> text of the zone's parking-area column, where the list has one (reference only, never used as geometry).
    zone_area_text: dict = field(default_factory=dict)


@dataclass(frozen=True)
class HouseRange:
    low: int
    high: int
    #: 1 = odd side, 0 = even side, None = both sides.
    parity: Optional[int]


@dataclass(frozen=True)
class StreetTarget:
    """One street of a list row: its name as written, the house-number ranges, qualifiers the step cannot apply
    (``unresolved_qualifiers``: exclusions and partial streets) and notes that restrict nothing (entrances)."""

    street_text: str
    ranges: tuple = ()
    unresolved_qualifiers: tuple = ()
    notes: tuple = ()


class TownResult(NamedTuple):
    districts: gpd.GeoDataFrame
    summary: dict


# ---------------------------------------------------------------- street names


_ABBREVIATIONS = (("str.", "strasse"), ("friedr.", "friedrich"), ("heinr.", "heinrich"))
_DASHES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-"})


def normalise_street_name(text: str) -> str:
    """The key of a street name: lower case, umlauts as ae/oe/ue, sharp s as ss, the abbreviations "Str.", "Friedr."
    and "Heinr." expanded, everything but letters and digits dropped. "Friedr.-Ebert-Str." and
    "Friedrich-Ebert-Strasse" give the same key; so do the spellings with a sharp s and with ss."""
    text = str(text).translate(_DASHES).lower().replace("\u00df", "ss")
    text = text.replace("\u00e4", "ae").replace("\u00f6", "oe").replace("\u00fc", "ue")
    for abbreviation, expansion in _ABBREVIATIONS:
        text = text.replace(abbreviation, expansion)
    return re.sub(r"[^a-z0-9]", "", text)


# ---------------------------------------------------------------- the list wording

_RANGE_TOKEN = re.compile(r"^(\d+)\s*[A-Za-z]?\s*(?:-\s*(\d+)\s*[A-Za-z]?)?$")
_EXCLUSION = re.compile(r"(ohne|au(?:ss|\u00df)er)\s+nr\.?", re.IGNORECASE)
_NUMBER_PREFIX = re.compile(r"[,\s]*(?:(?:ohne|au(?:ss|\u00df)er|haus)\s+)?nr\.?\s*$", re.IGNORECASE)
_PARENTHESIS = re.compile(r"\(([^)]*)\)")


def parse_osm_house_numbers(text) -> list:
    """The integers of an OSM ``addr:housenumber`` ("12a" -> [12], "12-14" -> [12, 14], "35,37,39" -> [35, 37, 39]);
    empty when the value holds no digit. Letters are ignored: a house "12b" counts as 12."""
    return [int(number) for number in re.findall(r"\d+", str(text or ""))]


def _parse_ranges(numbers_text: str, parity_override: Optional[int], parity_from_range_ends: bool, raw: str) -> tuple:
    ranges = []
    for token in re.split(r"\s*(?:,|;|\+|\bund\b)\s*", numbers_text.strip()):
        if not token:
            continue
        match = _RANGE_TOKEN.match(token)
        if match is None:
            raise SystemExit(f"street list row {raw!r}: cannot read the house numbers {token!r}; the parser of "
                             "resident_districts_from_streets.py knows single numbers and 'a - b' ranges")
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) is not None else low
        if high < low:
            raise SystemExit(f"street list row {raw!r}: the range {token!r} ends before it starts")
        if parity_override is not None:
            parity = parity_override
        elif parity_from_range_ends and low != high and low % 2 == high % 2:
            parity = low % 2
        else:
            parity = None
        ranges.append(HouseRange(low, high, parity))
    return tuple(ranges)


def _parse_one_street(part: str, parity_from_range_ends: bool, raw: str) -> StreetTarget:
    notes, unresolved, group_numbers = [], [], []
    parity_override = None
    for group in _PARENTHESIS.findall(part):
        lowered = group.strip().lower()
        if lowered in ("unger.", "ungerade"):
            parity_override = 1
        elif lowered in ("ger.", "gerade"):
            parity_override = 0
        elif re.match(r"^haus\s*nr\.?\s*\d", lowered):
            group_numbers.append(re.sub(r"^haus\s*nr\.?\s*", "", group.strip(), flags=re.IGNORECASE))
        elif lowered.startswith("eingang"):
            notes.append(group.strip())
        else:
            unresolved.append(group.strip())
    core = _PARENTHESIS.sub(" ", part).strip(" ,")
    digit = re.search(r"\d", core)
    numbers_text = ""
    if digit is not None:
        street_part, numbers_text = core[:digit.start()], core[digit.start():]
        if _EXCLUSION.search(street_part):
            unresolved.append(f"{street_part.split(',', 1)[-1].strip()} {numbers_text}".strip())
            numbers_text = ""
        street_part = _NUMBER_PREFIX.sub("", street_part)
        street = street_part.strip(" ,")
        if "," in street:
            street, rest = street.split(",", 1)
            unresolved.append(rest.strip())
    else:
        street = core
        if "," in street:
            street, rest = street.split(",", 1)
            unresolved.append(rest.strip())
    ranges = _parse_ranges(" , ".join([text for text in [numbers_text] + group_numbers if text]), parity_override,
                           parity_from_range_ends, raw)
    return StreetTarget(street_text=street.strip(), ranges=ranges, unresolved_qualifiers=tuple(unresolved),
                        notes=tuple(notes))


def parse_street_targets(raw: str, parity_from_range_ends: bool) -> list:
    """The streets of one list row as ``StreetTarget``s.

    Reads the wording of the Wolfsburg and Peine lists: "Street", "Street 13 - 71", "Street, 12 - 54",
    "Street 1 - 47 und 51 - 59", "Street 1 - 19, 2 - 26" (two ranges), "Street 31 - 79 (unger.)", "Street (Haus Nr.
    1 - 8)", "Street 4+9" (two single numbers), "Street 38", "A u. Street 15+16 (Eingang A)" (two streets in one
    row; "u." separates them), a footnote marker "*", and qualifiers the step cannot apply: "ohne/ausser Nr. ...",
    "(Rondell)", ", Westteil, ...". With ``parity_from_range_ends`` a range whose ends share their parity
    ("13 - 71") lies on one side of the street (odd or even); "(unger.)" and "(ger.)" are explicit.
    """
    text = str(raw).translate(_DASHES).strip().rstrip("*").strip()
    targets = [_parse_one_street(part, parity_from_range_ends, raw) for part in re.split(r"\s+u\.\s+", text)]
    if not all(target.street_text for target in targets):
        raise SystemExit(f"street list row {raw!r}: no street name found")
    return targets


def split_cell_entries(cell: str) -> list:
    """The street entries of a table cell: one per line, where a line that leaves a parenthesis open continues on the
    next ("... (Teilstueck gegenueber dem" + "Schuetzenplatz)" is one entry) and a line that starts with a parenthesis
    continues the line before."""
    entries = []
    for line in str(cell).translate(_DASHES).split("\n"):
        line = line.strip()
        if not line:
            continue
        if entries and (entries[-1].count("(") > entries[-1].count(")") or line.startswith("(")):
            entries[-1] = f"{entries[-1]} {line}"
        else:
            entries.append(line)
    return entries


# ---------------------------------------------------------------- the owner's package


def _file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_package(path, expected_sha256: str = PACKAGE_SHA256) -> str:
    """The SHA-256 of the package file; ``SystemExit`` if it is not the pinned one (a changed package is never read)."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: copy the owner's package unchanged into the municipal_2026-10-14 folder")
    actual = _file_sha256(path)
    if actual != expected_sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {expected_sha256} (data record "
                         "parking_tariffs_2026); a changed package is never read")
    return actual


def _read_member(archive_path, member: str, expected_sha256: str) -> bytes:
    with zipfile.ZipFile(archive_path) as archive:
        if member not in archive.namelist():
            raise SystemExit(f"{archive_path}: the member {member} is missing")
        payload = archive.read(member)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise SystemExit(f"{archive_path}: member {member} has SHA-256 {actual}, not the recorded {expected_sha256}; "
                         "a changed list is never read")
    return payload


def _check_row_count(name: str, count: int, expected: int) -> None:
    if count != expected:
        raise SystemExit(f"the {name} list holds {count} rows but the spec states {expected}: the list or its reading "
                         "changed; stop and report the counts (never adapted silently)")


def read_wolfsburg_list(archive_path, expected_row_count: int = WOLFSBURG_EXPECTED_ROWS,
                        expected_member_sha256: str = WOLFSBURG_LIST_MEMBER_SHA256) -> ParsedList:
    """The Wolfsburg street list (JSON member of the owner's package): ``resident_zone``,
    ``eligible_resident_address_raw`` and ``mixed_parking_zone`` per row."""
    payload = _read_member(archive_path, WOLFSBURG_LIST_MEMBER, expected_member_sha256)
    document = json.loads(payload.decode("utf-8"))
    rows = [ListRow(zone=str(row["resident_zone"]).strip(), raw=str(row["eligible_resident_address_raw"]).strip(),
                    mixed=bool(row["mixed_parking_zone"])) for row in document["rows"]]
    _check_row_count("Wolfsburg", len(rows), expected_row_count)
    return ParsedList(rows=rows, source_url=str(document["source_url"]),
                      document_date=str(document["source_document_date"]))


def read_peine_list(archive_path, expected_row_count: int = PEINE_EXPECTED_ROWS,
                    expected_member_sha256: str = PEINE_LIST_MEMBER_SHA256) -> ParsedList:
    """The Peine street list (PDF member of the owner's package): the table "Parkzone | Parkbereich | Strassenzuege,
    deren Bewohner Ausweise erhalten". One row per street entry of the third column; the second column (the zone's
    parking area) is kept as text and as ``area_street_keys`` for the tie rule, never as geometry. The list has no
    mixed flag (``mixed=None``)."""
    payload = _read_member(archive_path, PEINE_LIST_MEMBER, expected_member_sha256)
    try:
        import pdfplumber
    except ImportError as error:  # pragma: no cover - the environment of the pipeline has pdfplumber
        raise SystemExit("reading the Peine list needs pdfplumber (used by the other curation scripts of the "
                         "repository, e.g. scripts/extract_mid_tables.py)") from error
    with pdfplumber.open(io.BytesIO(payload)) as document:
        tables = document.pages[0].find_tables()
        if len(tables) != 1:
            raise SystemExit(f"the Peine list page holds {len(tables)} tables, expected 1")
        table = tables[0].extract()
    header = [str(cell or "") for cell in table[0]]
    if not (header[0].startswith("Parkzone") and header[1].startswith("Parkbereich") and "Stra" in header[2]):
        raise SystemExit(f"the Peine list table header is {header}, not Parkzone | Parkbereich | Strassenzuege")
    rows, zone_area_text = [], {}
    for record in table[1:]:
        zone = str(record[0]).strip()
        area_entries = split_cell_entries(record[1] or "")
        zone_area_text[zone] = " ".join(area_entries)
        area_keys = set()
        for entry in area_entries:
            cleaned = re.sub(r"\bteilw\.?", "", entry)
            for target in parse_street_targets(cleaned, parity_from_range_ends=False):
                area_keys.add(normalise_street_name(target.street_text))
        for entry in split_cell_entries(record[2] or ""):
            rows.append(ListRow(zone=zone, raw=entry, mixed=None, area_street_keys=frozenset(area_keys)))
    _check_row_count("Peine", len(rows), expected_row_count)
    return ParsedList(rows=rows, source_url=PEINE_LIST_URL, document_date="not stated",
                      zone_area_text=zone_area_text)


# ---------------------------------------------------------------- the OSM extract


def _parse_other_tags(text) -> dict:
    if not isinstance(text, str) or not text:
        return {}
    return {_HSTORE_ESCAPE.sub(r"\1", key): _HSTORE_ESCAPE.sub(r"\1", value) for key, value in _HSTORE_PAIR.findall(text)}


def read_osm_extract(path, bbox_wgs84, max_tmpfile_mb: int = DEFAULT_OSM_MAX_TMPFILE_MB) -> OsmStreetData:
    """Named highway ways and house numbers of ``bbox_wgs84`` (west, south, east, north) from an OSM file (PBF or XML)
    with the GDAL OSM driver, in EPSG:25832.

    House numbers come from nodes (layer ``points``) and building areas (layer ``multipolygons``, centroid) with
    ``addr:housenumber`` and ``addr:street``; an object without ``addr:street`` cannot be assigned to a street and is
    counted in the log. The driver scans the whole file once per layer.
    """
    import pyogrio

    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"OSM extract missing: {path}")
    west, south, east, north = bbox_wgs84
    with tempfile.TemporaryDirectory(prefix="gdal_osm_") as scratch:
        pyogrio.set_gdal_config_options({"CPL_TMPDIR": scratch, "OSM_MAX_TMPFILE_SIZE": str(int(max_tmpfile_mb))})
        try:
            frames = {}
            for layer, where in (("lines", OSM_LINE_FILTER), ("points", OSM_ADDRESS_FILTER),
                                 ("multipolygons", OSM_AREA_FILTER)):
                started = time.perf_counter()
                frames[layer] = pyogrio.read_dataframe(str(path), layer=layer, bbox=(west, south, east, north),
                                                       where=where)
                log.info("[resident-districts] read OSM layer %s of %s: %d features in %.1f s", layer, path.name,
                         len(frames[layer]), time.perf_counter() - started)
        finally:
            pyogrio.set_gdal_config_options({"CPL_TMPDIR": None, "OSM_MAX_TMPFILE_SIZE": None})
    lines = frames["lines"].to_crs(METRIC_CRS)[["osm_id", "name", "geometry"]].reset_index(drop=True)
    lines["osm_id"] = lines["osm_id"].astype("int64")
    keys, numbers, points, without_street = [], [], [], 0
    area_names, area_geometries = [], []
    for layer in ("points", "multipolygons"):
        frame = frames[layer].to_crs(METRIC_CRS)
        representative = frame.geometry.representative_point() if layer == "multipolygons" else frame.geometry
        for other_tags, name, geometry, point in zip(frame["other_tags"], frame["name"], frame.geometry, representative):
            tags = _parse_other_tags(other_tags)
            if layer == "multipolygons" and name and not tags.get("building"):
                area_names.append(str(name))
                area_geometries.append(geometry)
            if "addr:housenumber" not in tags:
                continue
            if not tags.get("addr:street"):
                without_street += 1
                continue
            for number in parse_osm_house_numbers(tags.get("addr:housenumber")):
                keys.append(normalise_street_name(tags["addr:street"]))
                numbers.append(number)
                points.append(point)
    houses = gpd.GeoDataFrame({"street_key": keys, "house_number": numbers}, geometry=points, crs=METRIC_CRS)
    areas = gpd.GeoDataFrame({"name": area_names}, geometry=area_geometries, crs=METRIC_CRS)
    log.info("[resident-districts] OSM: %d named highway ways, %d named areas, %d house numbers (%d address objects "
             "without addr:street are not usable)", len(lines), len(areas), len(houses), without_street)
    return OsmStreetData(lines=lines, houses=houses, areas=areas)


def clip_street_data(network: OsmStreetData, bbox_wgs84) -> OsmStreetData:
    """The part of ``network`` inside ``bbox_wgs84`` (west, south, east, north): ways and areas that intersect it, house
    numbers inside it. One reading of a union box serves every town this way (the driver scans the file per layer)."""
    west, south, east, north = bbox_wgs84
    box = gpd.GeoSeries([shapely.box(west, south, east, north)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]
    areas = network.areas
    return OsmStreetData(lines=network.lines[network.lines.intersects(box)].reset_index(drop=True),
                         houses=network.houses[network.houses.within(box)].reset_index(drop=True),
                         areas=None if areas is None else areas[areas.intersects(box)].reset_index(drop=True))


# ---------------------------------------------------------------- overlay helpers


def _polygonal(geometry, grid_m: float):
    """The polygonal part of an overlay result (snap rounding can collapse slivers into lines or points)."""
    if geometry.is_empty or geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [part for part in shapely.get_parts(geometry) if part.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts, grid_size=grid_m) if parts else shapely.Polygon()


def _on_grid(operation, grid_m: float, *geometries):
    return _polygonal(operation(*geometries, grid_size=grid_m), grid_m)


def _samples(geometry, window, spacing_m: float) -> np.ndarray:
    """Coordinates every ``spacing_m`` (and at every vertex) along ``geometry`` inside ``window``."""
    clipped = shapely.intersection(geometry, window)
    if clipped.is_empty:
        return np.empty((0, 2))
    return shapely.get_coordinates(shapely.segmentize(clipped, spacing_m) if clipped.geom_type not in
                                   ("Point", "MultiPoint") else clipped)


def nearer_region(first, second, region, buffer_m: float, settings: MatchSettings):
    """The part of ``region`` that is at most as far from the street geometry ``first`` as from ``second``.

    A generalised Voronoi split by sampling: both geometries are sampled every ``sample_spacing_m`` within ``buffer_m``
    plus two spacings of ``region`` (a point of a footprint lies within ``buffer_m`` of its own street, so every sample
    that can be nearest is kept); the Voronoi cells of the samples are labelled with the geometry of their sample; where
    both geometries have a sample at the same coordinate the first one keeps it, so a tie goes to ``first``.
    """
    if region.is_empty:
        return region
    window = region.buffer(buffer_m + 2.0 * settings.sample_spacing_m)
    first_samples = _samples(first, window, settings.sample_spacing_m)
    second_samples = _samples(second, window, settings.sample_spacing_m)
    if not len(second_samples):
        return region
    if not len(first_samples):
        return shapely.Polygon()
    coordinates = np.round(np.vstack([first_samples, second_samples]) / settings.grid_m) * settings.grid_m
    labels = np.concatenate([np.zeros(len(first_samples), dtype=int), np.ones(len(second_samples), dtype=int)])
    _, keep = np.unique(coordinates, axis=0, return_index=True)
    keep = np.sort(keep)
    coordinates, labels = coordinates[keep], labels[keep]
    if not (labels == 1).any():
        return region
    if not (labels == 0).any():
        return shapely.Polygon()
    points = shapely.points(coordinates)
    cells = shapely.get_parts(shapely.voronoi_polygons(shapely.multipoints(points), extend_to=window))
    cell_index, sample_index = shapely.STRtree(points).query_nearest(shapely.centroid(cells), all_matches=False)
    if not np.array_equal(cell_index, np.arange(len(cells))):
        raise SystemExit(f"nearer-street split: {len(cells) - len(np.unique(cell_index))} of {len(cells)} Voronoi cells "
                         "found no nearest sample")
    first_cells = _on_grid(shapely.union_all, settings.grid_m, cells[labels[sample_index] == 0])
    return _on_grid(shapely.intersection, settings.grid_m, region, first_cells)


# ---------------------------------------------------------------- claims: footprints of one street target


@dataclass
class Claim:
    """The footprint one street target puts on the map, with what the overlap rule needs."""

    zone: str
    row_index: int
    target_index: int
    kind: int
    area_listed: bool
    footprint: object
    centreline: object
    resolution: str
    entry: dict

    @property
    def order(self) -> tuple:
        return (self.kind, 0 if self.area_listed else 1, self.zone, self.row_index, self.target_index)


def _index_by_name(frame: Optional[gpd.GeoDataFrame]) -> dict:
    """street key -> the rows of ``frame`` (named ways or areas) with that key."""
    if frame is None or frame.empty:
        return {}
    keys = frame["name"].map(normalise_street_name)
    return {key: rows for key, rows in frame.assign(street_key=keys).groupby("street_key")}


def _locality(town: TownSpec, zone: str) -> Locality:
    if zone in town.localities:
        return town.localities[zone]
    if "default" not in town.localities:
        raise SystemExit(f"{town.name}: no locality for zone {zone} and no default locality")
    return town.localities["default"]


def _side(part, position_m: float, point) -> int:
    """+1 if ``point`` lies left of ``part`` at ``position_m`` along it, -1 if right (0 on the line)."""
    before = part.interpolate(max(position_m - 0.5, 0.0))
    after = part.interpolate(min(position_m + 0.5, part.length))
    here = part.interpolate(position_m)
    cross = (after.x - before.x) * (point.y - here.y) - (after.y - before.y) * (point.x - here.x)
    return int(np.sign(cross))


def _range_footprints(parts: list, points: list, one_sided: bool, settings: MatchSettings) -> tuple:
    """(footprints, centrelines, one_sided_pieces, sides_unresolved) of the houses of one range: the piece of each part
    between the projections of its houses, buffered on the side of the houses where ``one_sided`` and the houses agree.
    ``one_sided_pieces`` counts the pieces that asked for a one-sided buffer, ``sides_unresolved`` those of them whose
    houses do not agree on a side (both-sided fallback)."""
    by_part = {}
    for point in points:
        index = int(np.argmin([part.distance(point) for part in parts]))
        by_part.setdefault(index, []).append((parts[index].project(point), point))
    footprints, centrelines, sides_unresolved, one_sided_pieces = [], [], 0, 0
    for index in sorted(by_part):
        part = parts[index]
        positions = [position for position, _ in by_part[index]]
        low, high = min(positions), max(positions)
        if high - low < 1e-6:
            piece = part.interpolate(low)
            footprints.append(piece.buffer(settings.buffer_m))
            centrelines.append(piece)
            continue
        piece = substring(part, low, high)
        sides = [_side(part, position, point) for position, point in by_part[index]]
        side = 0
        if one_sided:
            one_sided_pieces += 1
            counts = {1: sides.count(1), -1: sides.count(-1)}
            side = max(counts, key=counts.get)
            if counts[side] / len(sides) < settings.side_agreement:
                side = 0
                sides_unresolved += 1
        footprints.append(piece.buffer(side * settings.buffer_m, single_sided=True) if side
                          else piece.buffer(settings.buffer_m))
        centrelines.append(piece)
    return footprints, centrelines, one_sided_pieces, sides_unresolved


def _target_claims(town: TownSpec, row_index: int, row: ListRow, mixed: bool, target_index: int,
                   target: StreetTarget, ways: dict, areas: dict, houses_by_key: dict, settings: MatchSettings,
                   counters: dict, unmatched: list) -> Optional[Claim]:
    key = normalise_street_name(target.street_text)
    locality = _locality(town, row.zone)
    anchor = Point(locality.x, locality.y)
    candidates, street_source = ways.get(key), "way"
    if candidates is None or candidates.empty:
        candidates, street_source = areas.get(key), "area_boundary"
    if candidates is None or candidates.empty:
        unmatched.append(f"zone {row.zone}: {row.raw!r} (street {target.street_text!r}: no OSM way or named area of "
                         "that name in the extract)")
        return None
    if street_source == "area_boundary":
        candidates = candidates.assign(geometry=candidates.geometry.boundary)
    inside = candidates[candidates.geometry.distance(anchor) <= locality.radius_m]
    counters["ways_outside_locality"] += len(candidates) - len(inside)
    if inside.empty:
        nearest = float(candidates.geometry.distance(anchor).min())
        unmatched.append(f"zone {row.zone}: {row.raw!r} (street {target.street_text!r}: {len(candidates)} OSM way(s) of "
                         f"that name, none within the locality of {locality.radius_m:.0f} m around "
                         f"({locality.x:.0f}, {locality.y:.0f}); the nearest is {nearest:.0f} m away)")
        return None
    places = len(shapely.get_parts(shapely.union_all([geometry.buffer(1.0) for geometry in inside.geometry])))
    if places > 1:
        counters["names_in_several_places"] += 1
        log.warning("[resident-districts] %s: street %r of zone %s exists in several places (%d) inside the locality; "
                    "all are used", town.name, target.street_text, row.zone, places)
    merged = shapely.line_merge(shapely.union_all(list(inside.geometry)))
    parts = [part for part in shapely.get_parts(merged) if part.geom_type == "LineString" and part.length > 0]
    whole_footprint = shapely.union_all(parts).buffer(settings.buffer_m)
    whole_centreline = shapely.union_all(parts)
    if street_source == "area_boundary":
        counters["area_matched_targets"] += 1
    entry = {"row": row_index + 1, "target": target_index + 1, "zone": row.zone, "raw": row.raw, "mixed": mixed,
             "street": key, "street_source": street_source, "ways": int(len(inside)),
             "osm_way_ids": sorted(int(value) for value in inside["osm_id"]) if street_source == "way" else [],
             "places_in_locality": places,
             "qualifiers": list(target.unresolved_qualifiers)}
    if target.unresolved_qualifiers:
        counters["qualifier_not_resolved_targets"] += 1
    area_listed = key in row.area_street_keys
    if not target.ranges:
        resolution = "qualifier_not_resolved" if target.unresolved_qualifiers else "whole_street"
        entry.update({"resolution": resolution, "houses": 0})
        return Claim(row.zone, row_index, target_index, KIND_WHOLE, area_listed, whole_footprint, whole_centreline,
                     resolution, entry)
    counters["ranged_targets"] += 1
    street_houses = houses_by_key.get(key)
    street_geometry = shapely.union_all(parts)
    footprints, centrelines, matched_houses = [], [], 0
    resolved = True
    for house_range in target.ranges:
        points = []
        if street_houses is not None:
            near = street_houses[street_houses.geometry.distance(street_geometry) <= settings.max_house_distance_m]
            for number, point in zip(near["house_number"], near.geometry):
                if house_range.low <= number <= house_range.high and (
                        house_range.parity is None or number % 2 == house_range.parity):
                    points.append(point)
        required = 1 if house_range.low == house_range.high else settings.min_range_points
        if len(points) < required:
            resolved = False
            break
        matched_houses += len(points)
        one_sided = house_range.parity is not None
        pieces, lines, requested_sides, unresolved_sides = _range_footprints(parts, points, one_sided, settings)
        counters["one_sided_pieces"] += requested_sides
        counters["side_not_resolved_pieces"] += unresolved_sides
        footprints.extend(pieces)
        centrelines.extend(lines)
    if not resolved:
        counters["range_not_resolved_targets"] += 1
        entry.update({"resolution": "range_not_resolved", "houses": 0})
        return Claim(row.zone, row_index, target_index, KIND_WHOLE, area_listed, whole_footprint, whole_centreline,
                     "range_not_resolved", entry)
    counters["range_resolved_targets"] += 1
    entry.update({"resolution": "range", "houses": matched_houses})
    return Claim(row.zone, row_index, target_index, KIND_RANGE, area_listed, shapely.union_all(footprints),
                 shapely.union_all(centrelines), "range", entry)


# ---------------------------------------------------------------- overlaps between zones


def resolve_claims(claims: list, settings: MatchSettings) -> tuple:
    """(regions, number of overlaps resolved): the footprint of every claim after the overlaps with claims of other
    zones are given away. Claims are taken in ``Claim.order`` (specificity first); of two overlapping claims the earlier
    keeps the overlap if it is more specific, otherwise the nearer street keeps each part (ties: the earlier). Claims
    of one zone merge and never compete. Pairs are resolved in that order, each on the regions left by the pairs
    before."""
    ordered = sorted(range(len(claims)), key=lambda index: claims[index].order)
    regions = {index: claims[index].footprint for index in range(len(claims))}
    tree = shapely.STRtree([claims[index].footprint for index in range(len(claims))])
    resolved = 0
    for position, first in enumerate(ordered):
        for second in sorted((int(index) for index in tree.query(claims[first].footprint)),
                             key=lambda index: ordered.index(index)):
            if ordered.index(second) <= position or claims[first].zone == claims[second].zone:
                continue
            overlap = _on_grid(shapely.intersection, settings.grid_m, regions[first], regions[second])
            if overlap.is_empty or overlap.area <= 0.0:
                continue
            if claims[first].kind != claims[second].kind:
                first_part = overlap
            else:
                first_part = nearer_region(claims[first].centreline, claims[second].centreline, overlap,
                                           settings.buffer_m, settings)
            second_part = _on_grid(shapely.difference, settings.grid_m, overlap, first_part)
            regions[second] = _on_grid(shapely.difference, settings.grid_m, regions[second], first_part)
            regions[first] = _on_grid(shapely.difference, settings.grid_m, regions[first], second_part)
            resolved += 1
    return regions, resolved


# ---------------------------------------------------------------- the town


def _ascii(text) -> str:
    return cc.ascii_transliteration(str(text).translate(_DASHES))


def _ascii_entry(entry: dict) -> dict:
    """A ``source_rows`` entry with every text (and every text in a list) in the ASCII form of the committed files."""
    return {key: ([_ascii(item) if isinstance(item, str) else item for item in value] if isinstance(value, list)
                  else _ascii(value) if isinstance(value, str) else value) for key, value in entry.items()}


def _district_note(town: TownSpec, zone: str, entries: list, mixed_rows: int, counts: dict, settings: MatchSettings,
                   provenance: Provenance, area_text: Optional[str], holes_filled_m2: float = 0.0) -> str:
    resolutions = {}
    for entry in entries:
        resolutions[entry["resolution"]] = resolutions.get(entry["resolution"], 0) + 1
    detail = ", ".join(f"{count} {name}" for name, count in sorted(resolutions.items()))
    from_list = town.geometry_source == pz.STREET_LIST_BUFFER_GEOMETRY_SOURCE
    note = (
        f"Resident parking district {zone} of {town.name}, built from {town.source_kind} ({town.list_source_url}; "
        f"document date {town.list_document_date}; owner package {provenance.package_name}, SHA-256 "
        f"{provenance.package_sha256[:12]}..., supplied {SUPPLIED_ON}): {len(entries)} street entr"
        f"{'y' if len(entries) == 1 else 'ies'} ({detail}) matched to the OSM street geometry of the extract "
        f"{provenance.osm_extract} (SHA-256 {provenance.osm_sha256[:12]}...)"
        + (", a street with a house-number range cut at the OSM house numbers," if from_list else "")
        + f" every street piece buffered by {settings.buffer_m:g} m (ASSUMPTION R3: the width of a street with both "
        "kerbs) and the pieces united. A geographic envelope of "
        + ("the streets whose residents receive a permit, no legal parking area of a permit" if from_list
           else "the streets the map labels, not a surveyed boundary")
        + " and no proof of parking supply. Overlaps with other zones of the town go to the more specific footprint "
        "and, between equally specific ones, to the nearer street (the town's districts never overlap).")
    if from_list and town.infer_parity_from_range_ends:
        note += (" ASSUMPTION R3-b: a range whose two ends share their parity (for example 1 - 25) lies on one side of the "
                 "street, so its piece is buffered on the side of its houses only (the list interleaves zones this way, "
                 "but does not state it); a piece whose houses do not agree on a side is buffered on both sides.")
    if from_list:
        note += (f" {mixed_rows} list row(s) of this zone are marked as mixed-zone streets (the street also serves "
                 "another zone); source_rows lists every row with its resolution. "
                 f"Town totals: {counts['targets']} street entries, {counts['range_not_resolved_targets']} "
                 "range_not_resolved (whole street used).")
    else:
        locality = _locality(town, zone)
        note += (f" The map is schematic and carries no coordinates: a way is used whole when any part lies within "
                 f"{locality.radius_m:.0f} m of the reference point ({locality.x:.0f}, {locality.y:.0f} in EPSG:25832), so "
                 "the arms of the map and the through streets that leave the old town are cut by this rule, not by the "
                 "map." + (f" {town.extent_note}" if town.extent_note else ""))
    if area_text:
        note += f" The list's parking-area column for this zone reads: {area_text} (reference only, not used as geometry)."
    if settings.fill_holes:
        note += (f" Holes inside the united buffers (blocks between the streets, {holes_filled_m2:,.0f} m2 in total) are "
                 "filled, because the map colours whole blocks.")
    return _ascii(note)


def build_town_districts(town: TownSpec, rows: list, network: OsmStreetData, settings: MatchSettings,
                         provenance: Provenance = Provenance(), zone_area_text: Optional[dict] = None,
                         zone_names: Optional[dict] = None) -> TownResult:
    """The district layer of one town (EPSG:25832, one row per zone, sorted by ``district_code``).

    Every street of ``rows`` must find an OSM way inside the locality of its zone; otherwise ``SystemExit`` lists
    every unmatched row. Returns ``TownResult(districts, summary)``; the districts carry
    ``pz.DISTRICT_PROVENANCE_COLUMNS``, ``source_rows`` (JSON list, one entry per street entry with its resolution) and
    ``mixed_zone_rows``; the summary the counts and rates of the run (also logged).
    """
    started = time.perf_counter()
    ways, areas = _index_by_name(network.lines), _index_by_name(network.areas)
    houses_by_key = {key: frame for key, frame in network.houses.groupby("street_key")}
    parsed = [parse_street_targets(row.raw, town.infer_parity_from_range_ends) for row in rows]
    zones_of_key = {}
    for row, targets in zip(rows, parsed):
        for target in targets:
            zones_of_key.setdefault(normalise_street_name(target.street_text), set()).add(row.zone)
    counters = {name: 0 for name in ("ways_outside_locality", "names_in_several_places", "qualifier_not_resolved_targets",
                                     "ranged_targets", "range_resolved_targets", "range_not_resolved_targets",
                                     "side_not_resolved_pieces", "one_sided_pieces", "area_matched_targets")}
    claims, unmatched, mixed_rows = [], [], {}
    for row_index, (row, targets) in enumerate(zip(rows, parsed)):
        mixed = row.mixed if row.mixed is not None else any(
            len(zones_of_key[normalise_street_name(target.street_text)]) > 1 for target in targets)
        if mixed:
            mixed_rows.setdefault(row.zone, set()).add(row_index)
        for target_index, target in enumerate(targets):
            claim = _target_claims(town, row_index, row, mixed, target_index, target, ways, areas, houses_by_key,
                                   settings, counters, unmatched)
            if claim is not None:
                claims.append(claim)
    target_total = sum(len(targets) for targets in parsed)
    log.info("[resident-districts] %s: %d list rows, %d street entries: matched %d, unmatched %d", town.name, len(rows),
             target_total, len(claims), len(unmatched))
    by_area = counters["area_matched_targets"]
    log.info("[resident-districts] %s: street entries matched by an OSM way primary %d/%d (%.1f %%), fallback %d "
             "(%.1f %%) matched by the boundary of a named area", town.name, len(claims) - by_area, len(claims),
             100.0 * (len(claims) - by_area) / len(claims) if claims else 100.0, by_area,
             100.0 * by_area / len(claims) if claims else 0.0)
    if unmatched:
        raise SystemExit(f"{town.name}: {len(unmatched)} of {target_total} street entries of the list found no OSM way "
                         "(the step stops; nothing is dropped silently):\n  " + "\n  ".join(unmatched))
    ranged = counters["ranged_targets"]
    not_resolved = counters["range_not_resolved_targets"]
    rate = not_resolved / ranged if ranged else 0.0
    log.info("[resident-districts] %s: ranged street entries primary %d/%d (%.1f %%), fallback %d (%.1f %%) "
             "range_not_resolved (whole street used)", town.name, counters["range_resolved_targets"], ranged,
             100.0 * (1.0 - rate) if ranged else 100.0, not_resolved, 100.0 * rate)
    requested, unresolved_sides = counters["one_sided_pieces"], counters["side_not_resolved_pieces"]
    log.info("[resident-districts] %s: one-sided primary %d/%d (%.1f %%), fallback %d (%.1f %%) both-sided buffer (the "
             "houses of a parity range do not agree on a side)", town.name, requested - unresolved_sides, requested,
             100.0 * (requested - unresolved_sides) / requested if requested else 100.0, unresolved_sides,
             100.0 * unresolved_sides / requested if requested else 0.0)
    qualifiers = counters["qualifier_not_resolved_targets"]
    log.info("[resident-districts] %s: qualifiers primary %d/%d (%.1f %%), fallback %d (%.1f %%) qualifier_not_resolved "
             "(whole street used)", town.name, target_total - qualifiers, target_total,
             100.0 * (target_total - qualifiers) / target_total if target_total else 100.0, qualifiers,
             100.0 * qualifiers / target_total if target_total else 0.0)
    if rate > settings.range_warning_rate:
        log.warning("[resident-districts] %s: range_not_resolved rate %.1f %% exceeds %.0f %%: the extract has too few "
                    "house numbers for many ranges", town.name, 100.0 * rate, 100.0 * settings.range_warning_rate)
    regions, overlaps_resolved = resolve_claims(claims, settings)
    area_by_claim = {id(claim): regions[index].area for index, claim in enumerate(claims)}
    entries_by_zone, regions_by_zone = {}, {}
    for index, claim in enumerate(claims):
        claim.entry["area_m2"] = round(regions[index].area, 1)
        entries_by_zone.setdefault(claim.zone, []).append(claim.entry)
        regions_by_zone.setdefault(claim.zone, []).append(regions[index])
    rows_without_area = [claim.entry["raw"] for claim in claims if area_by_claim[id(claim)] <= 0.0]
    kept = len(claims) - len(rows_without_area)
    log.info("[resident-districts] %s: street entries that kept an area primary %d/%d (%.1f %%), fallback %d (%.1f %%) "
             "rows_without_area (absorbed by another zone in an overlap)", town.name, kept, len(claims),
             100.0 * kept / len(claims) if claims else 100.0, len(rows_without_area),
             100.0 * len(rows_without_area) / len(claims) if claims else 0.0)
    if rows_without_area:
        log.warning("[resident-districts] %s: %d street entries kept no area after the overlaps with other zones: %s",
                    town.name, len(rows_without_area), "; ".join(rows_without_area))
    counts = {"targets": target_total, "range_not_resolved_targets": not_resolved}
    records, holes_filled_total = [], 0.0
    for zone in sorted(regions_by_zone):
        geometry = _on_grid(shapely.union_all, settings.grid_m, regions_by_zone[zone])
        parts = [part for part in shapely.get_parts(geometry) if part.area >= settings.minimum_part_m2]
        if not parts:
            raise SystemExit(f"{town.name}: district {zone} has no area after the overlaps with the other zones")
        holes_filled_m2 = 0.0
        if settings.fill_holes:
            filled = [shapely.Polygon(part.exterior) for part in parts]
            holes_filled_m2 = sum(new.area - old.area for new, old in zip(filled, parts))
            parts = list(shapely.get_parts(_on_grid(shapely.union_all, settings.grid_m, filled)))
            holes_filled_total += holes_filled_m2
        geometry = parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)
        entries = sorted(entries_by_zone[zone], key=lambda entry: (entry["row"], entry["target"]))
        source_rows = json.dumps([_ascii_entry(entry) for entry in entries], ensure_ascii=True, separators=(",", ":"))
        records.append({
            "district_id": f"{town.id_prefix}_district_{zone.lower()}", "district_code": zone,
            "municipality_ags": town.municipality_ags,
            "name": _ascii((zone_names or {}).get(zone, f"{town.name} Bewohnerparkzone {zone}")),
            "geometry_source": town.geometry_source, "source_url": town.list_source_url,
            "source_date": SUPPLIED_ON, "digitised_on": SUPPLIED_ON,
            "digitising_note": _district_note(town, zone, entries, len(mixed_rows.get(zone, ())), counts, settings,
                                              provenance, (zone_area_text or {}).get(zone), holes_filled_m2),
            "source_rows": source_rows, "mixed_zone_rows": len(mixed_rows.get(zone, ())), "geometry": geometry})
    districts = gpd.GeoDataFrame(pd.DataFrame(records).astype({"mixed_zone_rows": "int64"}), geometry="geometry",
                                 crs=METRIC_CRS)
    try:
        pz.validate_resident_districts(districts[list(pz.DISTRICT_PROVENANCE_COLUMNS) + ["geometry"]])
    except ValueError as error:
        raise SystemExit(f"{town.name}: the built districts are invalid: {error}") from error
    summary = {"town": town.name, "rows": len(rows), "targets": target_total, "matched_targets": len(claims),
               "unmatched_targets": 0, "districts": len(districts),
               "mixed_rows": sum(len(value) for value in mixed_rows.values()), "overlaps_resolved": overlaps_resolved,
               "rows_without_area": len(rows_without_area), "holes_filled_m2": holes_filled_total,
               "range_not_resolved_rate": rate, "seconds": round(time.perf_counter() - started, 1)}
    summary.update(counters)
    log.info("[resident-districts] %s: %d districts, %d overlaps resolved, %d mixed rows, %d ways outside the locality "
             "dropped, %d names in several places", town.name, len(districts), overlaps_resolved,
             summary["mixed_rows"], counters["ways_outside_locality"], counters["names_in_several_places"])
    if settings.fill_holes:
        log.info("[resident-districts] %s: holes filled inside the districts: %.0f m2", town.name, holes_filled_total)
    return TownResult(districts=districts, summary=summary)


# ---------------------------------------------------------------- indicator: districts and paid street zones


def paid_zone_shares(districts: gpd.GeoDataFrame, zones: gpd.GeoDataFrame, tariffs: pd.DataFrame) -> pd.DataFrame:
    """Per district the share of its area (m2) inside the paid street zones of its own town.

    ``zones`` holds ``zone_id`` and the polygon, ``tariffs`` ``zone_id``, ``municipality_ags`` and ``zone_type``; only
    zones of type ``street_paid`` of the district's municipality count (a campus, a resident zone or a zone of another
    town does not). An INDICATOR for the report (a resident district should lie inside or touch a resident-relevant
    paid zone of its town), not a gate: the districts are envelopes of residential streets that a paid zone need not
    cover. Columns: ``district_id``, ``municipality_ags``, ``district_area_m2``, ``paid_street_area_m2``,
    ``paid_street_share``.
    """
    paid = tariffs[tariffs["zone_type"] == "street_paid"][["zone_id", "municipality_ags"]]
    zone_geometries = zones.merge(paid, on="zone_id", how="inner")
    records = []
    for _, district in districts.iterrows():
        own = zone_geometries[zone_geometries["municipality_ags"] == district["municipality_ags"]]
        area = district.geometry.area
        inside = shapely.union_all(list(own.geometry)).intersection(district.geometry).area if len(own) else 0.0
        records.append({"district_id": district["district_id"], "municipality_ags": district["municipality_ags"],
                        "district_area_m2": area, "paid_street_area_m2": inside,
                        "paid_street_share": inside / area if area else 0.0})
    return pd.DataFrame(records)


# ---------------------------------------------------------------- the two towns

#: Wolfsburg (AGS 03103000). Localities in EPSG:25832, set from the OSM position of reference streets of the list (not
#: free-hand): Stadtmitte from the streets Kaufhof, Dantehof and Fontanehof (all zones A-G and K), Vorsfelde from "An der
#: Propstei" (zone VO), Fallersleben from "Sandkaemper Strasse" (zone FA). The radii cover the extent of each area.
WOLFSBURG = TownSpec(
    key="wolfsburg", name="Wolfsburg", municipality_ags="03103000", id_prefix="wob",
    localities={"default": Locality(621350.0, 5809450.0, 2000.0), "VO": Locality(624950.0, 5811450.0, 800.0),
                "FA": Locality(617000.0, 5809100.0, 800.0)},
    bbox_wgs84=(10.55, 52.30, 11.05, 52.55), infer_parity_from_range_ends=True,
    list_source_url=("https://www.wolfsburg.de/-/media/wolfsburg/statistik_daten_fakten/buergerdienste/"
                     "bewohnerparken_strassenverzeichnis_20230723.pdf"),
    list_document_date="2023-07-20")

#: Peine (AGS 03157006): one locality around the old town, set from the OSM position of the streets Echternstrasse,
#: Marktstrasse and Echternplatz (all within 700 m of the point); the radius covers every listed street.
PEINE = TownSpec(
    key="peine", name="Peine", municipality_ags="03157006", id_prefix="pe",
    localities={"default": Locality(584075.0, 5797290.0, 1500.0)},
    bbox_wgs84=(10.15, 52.28, 10.32, 52.37), infer_parity_from_range_ends=False,
    list_source_url=PEINE_LIST_URL, list_document_date="not stated")

TOWNS = {"wolfsburg": WOLFSBURG, "peine": PEINE}


def read_town_list(town: TownSpec, package_path) -> ParsedList:
    """The street list of ``town`` from the owner's package."""
    if town.key == "wolfsburg":
        return read_wolfsburg_list(package_path)
    if town.key == "peine":
        return read_peine_list(package_path)
    raise SystemExit(f"no street list for the town {town.key!r}")


def build_town_from_package(town: TownSpec, package_path, osm_path, settings: MatchSettings,
                            network: Optional[OsmStreetData] = None) -> TownResult:
    """Verify the package, read the list and the OSM extract and build the districts of ``town``."""
    package_sha256 = verify_package(package_path)
    parsed = read_town_list(town, package_path)
    if network is None:
        network = read_osm_extract(osm_path, town.bbox_wgs84)
    provenance = Provenance(package_name=f"{PACKAGE_NAME}.zip", package_sha256=package_sha256,
                            osm_extract=Path(osm_path).name, osm_sha256=_file_sha256(osm_path))
    spec = replace(town, list_source_url=parsed.source_url, list_document_date=parsed.document_date)
    return build_town_districts(spec, parsed.rows, network, settings, provenance, parsed.zone_area_text)


def write_town_file(districts: gpd.GeoDataFrame, path) -> None:
    """The districts of one town as a GeoPackage in EPSG:25832 (an inspection artefact; the release file is written by
    resident_districts.py)."""
    districts.to_file(Path(path), driver="GPKG", layer="resident_districts_street_list")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--package", required=True, type=Path, help="Parkdaten_Nachrecherche_2026-10-14.zip (SHA-256 checked)")
    parser.add_argument("--osm", required=True, type=Path, help="the OSM extract of the pipeline (PBF)")
    parser.add_argument("--town", required=True, choices=sorted(TOWNS))
    parser.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M, help="street buffer, metres (ASSUMPTION R3: 25)")
    parser.add_argument("--out", required=True, type=Path, help="GeoPackage of the town's districts to write")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.out.exists():
        raise SystemExit(f"{args.out} already exists; remove it first (outputs are never overwritten silently)")
    result = build_town_from_package(TOWNS[args.town], args.package, args.osm, MatchSettings(buffer_m=args.buffer_m))
    write_town_file(result.districts, args.out)
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
