"""Resident parking districts of Helmstedt and Wolfenbuettel from their published maps (parking cost zones v3, spec B3).

Neither town publishes a polygon or a street list of its resident parking area, only a map:

* **Helmstedt**: the brochure "Parken in Helmstedt" (December 2024) draws the unified resident zone "Altstadt innerhalb
  des Stadtringes" (Bewohnerparkgebuehrenordnung of 2024-04-10, section 1) as a red dotted line along the old-town ring
  road (one area since 2024-08-01; the four older zone permits stay valid inside the whole ring).
* **Wolfenbuettel**: a schematic map (not to scale, no coordinates) "Karte Geltungsbereich Bewohnerparkausweis" colours
  the old town and four arms (north: Lessingstrasse / Leibnizstrasse; north-west: Sophienstrasse / Lauenstrasse; west:
  Dr.-Heinrich-Jasper-Strasse) pink and labels every street inside.

Owner instruction (task B3): where the boundary of a map follows named streets, the polygon is built from the OSM
geometry of those streets and the street names are the method record, with no free-hand vertices. Both maps qualify, so
neither district is hand-digitised (``geometry_source`` ``map_named_streets_osm``):

1. **Helmstedt** (``enclosed_area_of_ring_streets``): the OSM ways of the six streets that carry the ring (Leuckartstrasse,
   Goethestrasse, Am Ludgerihof, Poststrasse, Wilhelmstrasse, and Walbecker Strasse, which closes the north-western
   corner where Leuckartstrasse and Goethestrasse do not meet) are noded and polygonised; the district is the face that
   contains the street Markt, widened by half the width of the ring street (``HELMSTEDT_RING_HALF_WIDTH_M``: the dotted
   line lies on the middle of the road, the face ends at its inner edge). Spurs of ring-named streets that leave the
   ring (Goethestrasse east of the ring) and the arm of Walbecker Strasse that leads away are outside the face and
   stay out. An open ring (no face contains Markt) stops the step.
2. **Wolfenbuettel** (``build_street_map_district``): the streets labelled inside the pink area (``WOLFENBUETTEL_STREETS``,
   read from the map; abbreviations spelled out) are matched to OSM like the lines of the street lists (locality of 600 m
   around Stadtmarkt, 25 m buffer, ASSUMPTION R3) and united; the holes between the buffers (blocks) are filled, because
   the map colours whole blocks.

Both are geographic envelopes: the Wolfenbuettel map is schematic (the arms end where the locality ends, not where the
map ends), and the Helmstedt ring is the OSM geometry of today, not a surveyed boundary. CRS: EPSG:25832, metres.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Point
from shapely.ops import polygonize_full

import resident_districts_from_streets as rds

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import zones as pz  # noqa: E402

log = logging.getLogger(__name__)

#: Members of the owner's package that hold the two maps, with their SHA-256 (the package record
#: parking_tariffs_2026 pins the zip; these pin the members that the districts rest on).
HELMSTEDT_BROCHURE_MEMBER = f"{rds.PACKAGE_NAME}/raw_sources/resident_other_cities/helmstedt_parking_brochure.pdf"
HELMSTEDT_BROCHURE_SHA256 = "f1dc902583b195972c7ddd83450be46e273867c07e31509e20ce46d9cce824df"
HELMSTEDT_BROCHURE_URL = "https://www.stadt-helmstedt.de/fileadmin/user_upload/02_Kultur/pdf_Broschueren/Parken_in_Helmstedt.pdf"
HELMSTEDT_ORDINANCE_URL = ("https://www.stadt-helmstedt.de/fileadmin/user_upload/01_Rathaus/Virtuelle_Verwaltung/"
                           "Ortsrecht_neu/Bewohnerparkgebuehrenordnung.pdf")
WOLFENBUETTEL_MAP_MEMBER = f"{rds.PACKAGE_NAME}/raw_sources/resident_other_cities/wolfenbuettel_area_map.pdf"
WOLFENBUETTEL_MAP_SHA256 = "06b2e4e98a19a4d096f1ab20e4eacac3cc70e7b4e7689b885f4d54bfd98ba472"
WOLFENBUETTEL_MAP_URL = "https://www.wolfenbuettel.de/loadDocument.phtml?Ext=PDF&FID=2093.5245.1"

#: The ring of Helmstedt (brochure map): the streets that carry it, in OSM spelling. Walbecker Strasse closes the
#: north-western corner (the ways of Leuckartstrasse and Goethestrasse end 100 m apart there).
HELMSTEDT_RING_STREETS = ("Leuckartstrasse", "Goethestrasse", "Am Ludgerihof", "Poststrasse", "Wilhelmstrasse",
                          "Walbecker Strasse")
#: A street inside the ring: the face of the street network that contains it is the district.
HELMSTEDT_INSIDE_STREET = "Markt"
#: Half the width of the ring road, metres (ASSUMPTION: the dotted line of the map lies on the middle of the road, the
#: face of the network ends at the inner edge of its inner carriageway).
HELMSTEDT_RING_HALF_WIDTH_M = 10.0

#: The streets labelled inside the pink area of the Wolfenbuettel map, in OSM spelling (map abbreviations spelled
#: out: Gr./Kl. Kirchstr., Kannengiessserstr., Brauergildenstr., Kommissstrasse, Lohenstr.). Arms: north Lessingstrasse
#: and Leibnizstrasse with Lessingplatz, north-west Sophienstrasse and Lauenstrasse, west Dr.-Heinrich-Jasper-Strasse.
WOLFENBUETTEL_STREETS = (
    "Lessingstrasse", "Leibnizstrasse", "Lessingplatz", "Sophienstrasse", "Lauenstrasse", "Dr.-Heinrich-Jasper-Strasse",
    "Schiffwall", "Schlossplatz", "Loewenstrasse", "Krambuden", "Stobenstrasse", "Neue Strasse", "Am Alten Tore",
    "Lange Herzogstrasse", "Okerstrasse", "Rosenwall", "Am Herzogtore", "Ziegenmarkt", "Wallstrasse", "Kreuzstrasse",
    "Enge Strasse", "Lohenstrasse", "Breite Herzogstrasse", "Kanzleistrasse", "Reichsstrasse", "Stadtmarkt",
    "Brauergildenstrasse", "Kornmarkt", "Kommissstrasse", "Grosse Kirchstrasse", "Kleine Kirchstrasse", "Grosser Zimmerhof",
    "Harztorplatz", "Harzstrasse", "Lustgarten", "Maurenstrasse", "Krumme Strasse", "Schulwall", "Bahnhofstrasse",
    "Harztorwall", "Kannengiesserstrasse", "Karlstrasse", "Landeshuter Platz", "Holzmarkt", "Lange Strasse",
    "Fischerstrasse")

#: Localities (EPSG:25832) set from the OSM position of reference streets: Helmstedt from "Markt", Wolfenbuettel from
#: "Stadtmarkt". Helmstedt's radius covers the ring (its farthest point is about 650 m from Markt); Wolfenbuettel's
#: 600 m cover the old town and the ends of the arms (the map is schematic and ends the arms at the car parks).
HELMSTEDT = rds.TownSpec(
    key="helmstedt", name="Helmstedt", municipality_ags="03154028", id_prefix="he",
    localities={"default": rds.Locality(637312.0, 5788285.0, 900.0)}, bbox_wgs84=(10.95, 52.18, 11.10, 52.28),
    infer_parity_from_range_ends=False, list_source_url=HELMSTEDT_BROCHURE_URL, list_document_date="2024-12")
WOLFENBUETTEL = rds.TownSpec(
    key="wolfenbuettel", name="Wolfenbuettel", municipality_ags="03158037", id_prefix="wf",
    localities={"default": rds.Locality(605001.0, 5780235.0, 600.0)}, bbox_wgs84=(10.45, 52.10, 10.62, 52.22),
    infer_parity_from_range_ends=False, list_source_url=WOLFENBUETTEL_MAP_URL, list_document_date="not stated")


@dataclass(frozen=True)
class RingMapSpec:
    """A map whose boundary is a ring of named streets."""

    code: str
    name: str
    ring_streets: tuple
    inside_street: str
    ring_half_width_m: float
    map_reference: str
    map_description: str


@dataclass(frozen=True)
class StreetMapSpec:
    """A map that labels the streets of its area."""

    code: str
    name: str
    street_names: tuple
    map_reference: str
    map_description: str


HELMSTEDT_SPEC = RingMapSpec(
    code="HE_RING", name="Helmstedt Bewohnerparkzone Altstadt innerhalb des Stadtringes",
    ring_streets=HELMSTEDT_RING_STREETS, inside_street=HELMSTEDT_INSIDE_STREET,
    ring_half_width_m=HELMSTEDT_RING_HALF_WIDTH_M,
    map_reference=("brochure Parken in Helmstedt (December 2024), page 'Bewohnerparken'; zone 'Altstadt innerhalb des "
                   "Stadtringes' of the Bewohnerparkgebuehrenordnung of 2024-04-10, section 1 (" + HELMSTEDT_ORDINANCE_URL + ")"),
    map_description="red dotted line along the old-town ring road")
WOLFENBUETTEL_SPEC = StreetMapSpec(
    code="WF_CENTRE", name="Wolfenbuettel Bewohnerparkgebiet Innenstadt", street_names=WOLFENBUETTEL_STREETS,
    map_reference="Karte Geltungsbereich Bewohnerparkausweis (wolfenbuettel_area_map.pdf)",
    map_description="schematic map, not to scale, pink area with labelled streets")


def verify_map_members(archive_path) -> dict:
    """Check the two map members of the owner's package against their pinned SHA-256; return {member: sha256}.

    The maps are read by a person (the street names above are transcribed from them); the check proves that the
    transcription rests on the pinned files."""
    members = {HELMSTEDT_BROCHURE_MEMBER: HELMSTEDT_BROCHURE_SHA256, WOLFENBUETTEL_MAP_MEMBER: WOLFENBUETTEL_MAP_SHA256}
    for member, sha256 in members.items():
        rds._read_member(archive_path, member, sha256)
    return members


def _ways_in_locality(lines: gpd.GeoDataFrame, names, locality: rds.Locality) -> tuple:
    """({name: ways of that name inside the locality}, names without a way inside, ways outside the locality)."""
    keyed = lines.assign(street_key=lines["name"].map(rds.normalise_street_name))
    anchor = Point(locality.x, locality.y)
    found, missing, outside = {}, [], 0
    for name in names:
        ways = keyed[keyed["street_key"] == rds.normalise_street_name(name)]
        inside = ways[ways.geometry.distance(anchor) <= locality.radius_m]
        outside += len(ways) - len(inside)
        if inside.empty:
            missing.append(f"{name!r} ({len(ways)} OSM way(s) of that name, none within {locality.radius_m:.0f} m of "
                           f"({locality.x:.0f}, {locality.y:.0f}))")
        else:
            found[name] = inside
    return found, missing, outside


def enclosed_area_of_ring_streets(lines: gpd.GeoDataFrame, ring_street_names, inside_street: str,
                                  locality: rds.Locality, ring_half_width_m: float) -> tuple:
    """(area, info): the area enclosed by the ring streets, widened by ``ring_half_width_m``.

    The OSM ways of ``ring_street_names`` inside ``locality`` are noded and polygonised; the face that contains the
    street ``inside_street`` is the enclosed area. A street without a way inside the locality, and a ring that does
    not enclose ``inside_street`` (open ring), stop the step (``SystemExit``). ``info``: ``ring_ways``,
    ``ways_outside_locality``, ``face_area_m2``, ``dangles`` (ends of ways that lead away from the ring or do not
    connect), ``faces`` (polygons the ring streets form).
    """
    found, missing, outside = _ways_in_locality(lines, tuple(ring_street_names) + (inside_street,), locality)
    if missing:
        raise SystemExit("map streets without an OSM way: " + "; ".join(missing))
    ring_geometries = [geometry for name in ring_street_names for geometry in found[name].geometry]
    probe = shapely.union_all(list(found[inside_street].geometry)).representative_point()
    faces, _, dangles, _ = polygonize_full(shapely.union_all(ring_geometries))
    candidates = [face for face in faces.geoms if face.contains(probe)]
    if len(candidates) != 1:
        raise SystemExit(f"the ring streets {list(ring_street_names)} do not enclose the street {inside_street!r} "
                         f"({len(candidates)} face(s) contain it; {len(faces.geoms)} face(s), {len(dangles.geoms)} "
                         "dangle(s)): the ring is open or the street names do not form it")
    face = candidates[0]
    info = {"ring_ways": len(ring_geometries), "ways_outside_locality": outside, "face_area_m2": face.area,
            "dangles": len(dangles.geoms), "faces": len(faces.geoms)}
    return face.buffer(ring_half_width_m), info


def build_ring_district(town: rds.TownSpec, spec: RingMapSpec, network: rds.OsmStreetData,
                        provenance: rds.Provenance) -> rds.TownResult:
    """The district of a ring map (Helmstedt) as a one-row layer (EPSG:25832) with ``source_rows`` naming every ring
    street; passes ``braunschweig.parking.zones.validate_resident_districts``."""
    locality = rds._locality(town, spec.code)
    area, info = enclosed_area_of_ring_streets(network.lines, spec.ring_streets, spec.inside_street, locality,
                                               spec.ring_half_width_m)
    found, _, _ = _ways_in_locality(network.lines, spec.ring_streets, locality)
    source_rows = [{"row": index + 1, "street": rds.normalise_street_name(name), "raw": rds._ascii(name),
                    "street_source": "way", "ways": int(len(found[name])), "resolution": "ring_street"}
                   for index, name in enumerate(spec.ring_streets)]
    note = rds._ascii(
        f"Resident parking district {spec.code} of {town.name}: {spec.map_reference}; {spec.map_description}; owner package "
        f"{provenance.package_name}, SHA-256 {provenance.package_sha256[:12]}..., supplied {rds.SUPPLIED_ON}. The boundary of "
        f"the map follows named streets, so the polygon is built from the OSM geometry (extract {provenance.osm_extract}, "
        f"SHA-256 {provenance.osm_sha256[:12]}...) of the ring streets {', '.join(spec.ring_streets)}: their ways inside "
        f"{locality.radius_m:.0f} m of the reference point are noded and polygonised, the district is the face that "
        f"contains the street {spec.inside_street} ({info['face_area_m2']:,.0f} m2), widened by half the width of the ring "
        f"street ({spec.ring_half_width_m:g} m, ASSUMPTION: the dotted line of the map lies on the middle of the road). "
        "No vertex is drawn by hand. A geographic envelope, not a surveyed boundary; it includes buildings and areas "
        "without parking; the unified zone applies to the managed car parks inside the ring.")
    record = {"district_id": f"{town.id_prefix}_district_{spec.code.lower()}", "district_code": spec.code,
              "municipality_ags": town.municipality_ags, "name": rds._ascii(spec.name),
              "geometry_source": pz.MAP_STREETS_GEOMETRY_SOURCE, "source_url": town.list_source_url,
              "source_date": rds.SUPPLIED_ON, "digitised_on": rds.SUPPLIED_ON, "digitising_note": note,
              "source_rows": json.dumps(source_rows, ensure_ascii=True, separators=(",", ":")), "mixed_zone_rows": 0,
              "geometry": area}
    districts = gpd.GeoDataFrame(pd.DataFrame([record]), geometry="geometry", crs=rds.METRIC_CRS)
    try:
        pz.validate_resident_districts(districts[list(pz.DISTRICT_PROVENANCE_COLUMNS) + ["geometry"]])
    except ValueError as error:
        raise SystemExit(f"{town.name}: the built district is invalid: {error}") from error
    summary = dict(info, town=town.name, districts=1, area_m2=area.area)
    log.info("[resident-districts] %s: ring of %d ways (%d dangling ends), enclosed face %.0f m2, district %.0f m2",
             town.name, info["ring_ways"], info["dangles"], info["face_area_m2"], area.area)
    return rds.TownResult(districts=districts, summary=summary)


def build_street_map_district(town: rds.TownSpec, spec: StreetMapSpec, network: rds.OsmStreetData,
                              settings: rds.MatchSettings, provenance: rds.Provenance = rds.Provenance()) -> rds.TownResult:
    """The district of a map that labels its streets (Wolfenbuettel): the streets matched to OSM, buffered, united and
    with the blocks between them filled; one row. Uses the matching of the street lists (``build_town_districts``)
    with ``fill_holes``; an unmatched street stops the step."""
    rows = [rds.ListRow(zone=spec.code, raw=name, mixed=False) for name in spec.street_names]
    map_town = replace(town, geometry_source=pz.MAP_STREETS_GEOMETRY_SOURCE,
                       source_kind=f"the streets labelled on the map {spec.map_reference} ({spec.map_description})")
    return rds.build_town_districts(map_town, rows, network, replace(settings, fill_holes=True), provenance,
                                    zone_names={spec.code: spec.name})
