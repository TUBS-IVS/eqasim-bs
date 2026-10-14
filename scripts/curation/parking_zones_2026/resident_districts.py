"""Resident parking districts of the owner's municipal packages (parking cost zones v2, spec Amendment C3, issue #436).

Not part of the pipeline and never run by synpp: a person runs this step to build the committed layer
``eqasim-data/data/braunschweig/parking/parking_resident_districts_2026.geojson``. Inputs: two of the owner-supplied
zips, copied unchanged into the gitignored ``eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-01/``;
each is checked against the SHA-256 pinned in ``municipal_zones.PACKAGE_SHA256`` (also in the data record
parking_zones_2026) before its layer is read, and the layers are read straight from the zips (GDAL ``/vsizip/``), so no
extracted copy exists that could drift. The step reads the layer ``resident_parking_zones`` of

* ``Braunschweig_Parkzonen.zip``: the districts A, B and C, digitised from the city's published district map;
* ``Goslar_Parkdaten.zip``: the districts A, B, C, F, G, H and J as the city's ArcGIS service publishes them, plus one
  feature "Parkbereich C - Oberstadt" without a code.

Since spec v3 B3 (2026-10-14) the layer also holds the districts of four more towns, built from the owner's research
package ``Parkdaten_Nachrecherche_2026-10-14.zip`` (``--research-package``) and the OSM extract of the pipeline
(``--osm-extract``): Wolfsburg (zones A-G, K, VO, FA) and Peine (A-G) from their official street lists
(``resident_districts_from_streets.py``: OSM street geometry, house-number ranges, 25 m buffer, ASSUMPTION R3), Helmstedt
(HE_RING) and Wolfenbuettel (WF_CENTRE) from their maps by the OSM geometry of the streets the maps name
(``resident_districts_from_maps.py``). Without those two options the step builds the ten districts of the first release.

It derives the districts of the committed layer:

* ``district_id`` is prefixed per town (``bs_district_a``, ``gs_district_c``, ...) so that it is unique across towns;
  the town's own label stays in ``district_code`` and the AGS in ``municipality_ags``.
* The Goslar feature "Parkbereich C - Oberstadt" has an empty code. Spec Amendment C3 joins it to district C: the
  district ``gs_district_c`` is the union of the two features, and its note records both source features.
* Two districts of one town may overlap in the source (the real Goslar G and H by 6.3 m2). No source says which
  district owns the overlap, so the district with the LATER id gives it up (rule of this step, recorded in the note of
  the district that lost the area): every point then lies in at most one district. Overlaps up to the loader's
  digitisation tolerance (``braunschweig.parking.zones.OVERLAP_TOLERANCE_M2``) are left as they are.
* The districts of the four towns of spec v3 B3 are taken as their builders return them (they never overlap within a
  town; the validator checks all districts together) and carry two more properties: ``source_rows`` (JSON, one entry per
  street entry of the list or map with its resolution) and ``mixed_zone_rows``.
* Nothing else changes a geometry. The committed layer must be valid as stored: ``braunschweig.parking.zones.
  load_resident_districts`` never repairs, and this step reloads the file it wrote with that loader before it replaces
  the target.

The source wording of controller ruling R-C1 opens the ``digitising_note`` of every district
(``BRAUNSCHWEIG_PROVENANCE``, ``GOSLAR_PROVENANCE``) and, per source, the licence member of the file. CRS: EPSG:25832
throughout, areas in m2; the file stores WGS84 with seven decimals (about 1 cm).

Usage::

    PARKING=eqasim-data/data/braunschweig/parking
    python scripts/curation/parking_zones_2026/resident_districts.py \\
        --municipal-dir $PARKING/raw_sources/municipal_2026-10-01 \\
        [--research-package $PARKING/raw_sources/municipal_2026-10-14/Parkdaten_Nachrecherche_2026-10-14.zip \\
         --osm-extract $PARKING/raw_osm/niedersachsen-260929.osm.pbf] \\
        [--out $PARKING/parking_resident_districts_2026.geojson] [--replace]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

import curation_common as cc
import municipal_zones as mz
import resident_districts_from_maps as rdm
import resident_districts_from_streets as rds

# The script runs from its own directory (curation_common); the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import zones as pz  # noqa: E402

DEFAULT_OUT = (Path(__file__).resolve().parents[3]
               / "eqasim-data/data/braunschweig/parking/parking_resident_districts_2026.geojson")
DEFAULT_ZONES = Path(__file__).resolve().parents[3] / "eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson"
DEFAULT_TARIFFS = Path(__file__).resolve().parents[3] / "eqasim-data/data/braunschweig/parking/parking_tariffs_2026.csv"
#: The packages this step reads, in order, and the layer it reads from each (``<name>/<name>.gpkg`` inside the zip).
DISTRICT_PACKAGES = ("Braunschweig_Parkzonen", "Goslar_Parkdaten")
DISTRICT_LAYER = "resident_parking_zones"
#: The "name" member of the written file (the dataset id of the data record).
LAYER_NAME = "parking_resident_districts_2026"

# ---------------------------------------------------------------- provenance (controller ruling R-C1, verbatim)
BRAUNSCHWEIG_PROVENANCE = ("Stadt Braunschweig, resident parking district map (vector PDF 2012 for the inner "
                           "boundaries, the current overview for the outer boundary); digitised (owner-supplied "
                           "package 2026-10-01); working accuracy 30 m; base map Open GeoData dl-de/by-2-0")
GOSLAR_PROVENANCE = ("Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22); open reuse licence not "
                     "verified; used by owner decision 2026-10-01")
#: What the wording states, checked against the layer attributes before any geometry is used.
BRAUNSCHWEIG_WORKING_ACCURACY_M = 30.0
BRAUNSCHWEIG_VECTOR_MAP_YEAR = "2012"
BRAUNSCHWEIG_CODES = ("A", "B", "C")
GOSLAR_CODES = ("A", "B", "C", "F", "G", "H", "J")
#: The uncoded Goslar feature that joins district C (spec Amendment C3): its description in the service.
GOSLAR_UNCODED_DESCRIPTION = "Parkbereich C - Oberstadt"
GOSLAR_JOINED_CODE = "C"
#: The ArcGIS feature service of the Goslar package (its Metadaten_und_Qualitaet.json, source); the layer id of the
#: districts (the service's "Parkbereiche") is the attribute ``source_layer_id``.
GOSLAR_SERVICE_URL = "https://services3.arcgis.com/X0EQlGp2g40JN62M/arcgis/rest/services/Bewohnerparken/FeatureServer"
#: The date the owner supplied both packages: ``source_date`` of every district.
SUPPLIED_ON = mz.MUNICIPAL_DIGITISED_ON

_LICENSE_REGIMES = ("(1) bs_district_a, bs_district_b and bs_district_c: " + BRAUNSCHWEIG_PROVENANCE + ". (2) the "
                    "gs_district_* districts: " + GOSLAR_PROVENANCE + ".")
#: The regimes of the districts of spec v3 B3 (Wolfsburg, Peine, Helmstedt, Wolfenbuettel).
_LICENSE_STREET_REGIMES = (
    "(3) the wob_district_* districts: Stadt Wolfsburg, Bewohnerparken street list of 2023-07-20, and the pe_district_* "
    "districts: Stadt Peine, list Parkzonen-Bewohnerparkausweis, matched to the street geometry of OpenStreetMap and "
    "buffered by 25 m (ASSUMPTION R3). (4) he_district_he_ring and wf_district_wf_centre: the maps of Stadt Helmstedt "
    "(brochure Parken in Helmstedt, December 2024) and Stadt Wolfenbuettel (Karte Geltungsbereich "
    "Bewohnerparkausweis), constructed from the OpenStreetMap geometry of the streets the maps name. Regimes (3) and (4) "
    "contain OpenStreetMap data (c) OpenStreetMap contributors, ODbL 1.0; the city documents are used by owner "
    "decision 2026-10-14 with no reuse licence verified.")
_LICENSE_TAIL = ("Which areas are resident parking districts follows the municipalities' published district maps and "
                 "services; the districts are geographic envelopes, no proof of parking supply or permission.")
#: The first release: Braunschweig and Goslar.
LICENSE = f"Per source; the districts are polygons under two regimes. {_LICENSE_REGIMES} {_LICENSE_TAIL}"
#: With the districts of spec v3 B3.
LICENSE_WITH_STREET_DISTRICTS = (f"Per source; the districts are polygons under four regimes. {_LICENSE_REGIMES} "
                                 f"{_LICENSE_STREET_REGIMES} {_LICENSE_TAIL}")
STREET_DISTRICTS_ATTRIBUTION = (
    " Districts of Wolfsburg, Peine, Helmstedt and Wolfenbuettel: street names and house numbers from the cities' "
    "official lists and maps (owner package Parkdaten_Nachrecherche_2026-10-14.zip), geometry derived from "
    "OpenStreetMap data (c) OpenStreetMap contributors, ODbL 1.0.")
ATTRIBUTION = ("Base map of the digitised Braunschweig districts: Datenquelle: Stadt Braunschweig - Open GeoData, 2026, "
               "Lizenz: dl-de/by-2-0 (https://www.govdata.de/dl-de/by-2-0); data changed (georeference of the "
               "digitised districts). Goslar districts: Stadt Goslar - Stadtplanung_Geodaten, ArcGIS service "
               "Bewohnerparken (last edit 2018-11-22); open reuse licence not verified; used by owner decision "
               "2026-10-01.")


# ---------------------------------------------------------------- packages


def _check_attributes(layers: dict) -> None:
    """Refuse (``SystemExit``) packages whose attributes contradict what the provenance wording and this step assume."""
    bs = layers["Braunschweig_Parkzonen"]
    if sorted(bs["zone_id"].astype(str)) != list(BRAUNSCHWEIG_CODES):
        raise SystemExit(f"Braunschweig resident_parking_zones holds {sorted(bs['zone_id'].astype(str))}; the step "
                         f"needs exactly the districts A, B and C")
    accuracy = sorted({float(value) for value in bs["estimated_accuracy_m"]})
    if accuracy != [BRAUNSCHWEIG_WORKING_ACCURACY_M]:
        raise SystemExit(f"Braunschweig resident_parking_zones: estimated_accuracy_m {accuracy}; the recorded "
                         f"provenance states a working accuracy of {BRAUNSCHWEIG_WORKING_ACCURACY_M:.0f} m")
    for text in bs["source_date"].astype(str):
        if BRAUNSCHWEIG_VECTOR_MAP_YEAR not in text:
            raise SystemExit(f"Braunschweig resident_parking_zones: source_date {text!r} does not name the vector map "
                             f"year {BRAUNSCHWEIG_VECTOR_MAP_YEAR} of the recorded provenance")
    digitised = {str(value)[:10] for value in bs["digitized_on"]}
    if digitised != {mz.BRAUNSCHWEIG_DIGITISED_ON}:
        raise SystemExit(f"Braunschweig resident_parking_zones: digitized on {sorted(digitised)}, not the recorded "
                         f"{mz.BRAUNSCHWEIG_DIGITISED_ON}")
    mz._single(bs["source"], "Braunschweig resident_parking_zones source")

    gs = layers["Goslar_Parkdaten"]
    codes = gs["Parkbereiche_Kennzeichen"]
    uncoded = gs[codes.isna() | (codes.astype(str).str.strip() == "")]
    if len(uncoded) != 1:
        raise SystemExit(f"Goslar resident_parking_zones has {len(uncoded)} features without a code; the step needs "
                         f"exactly one feature without a code, the description {GOSLAR_UNCODED_DESCRIPTION!r}, "
                         f"which joins district {GOSLAR_JOINED_CODE}")
    if str(uncoded["Parkbereiche_Beschreibung"].iloc[0]) != GOSLAR_UNCODED_DESCRIPTION:
        raise SystemExit(f"the Goslar feature without a code is {uncoded['Parkbereiche_Beschreibung'].iloc[0]!r}, not "
                         f"{GOSLAR_UNCODED_DESCRIPTION!r}: only that feature joins district {GOSLAR_JOINED_CODE}")
    coded = sorted(gs.loc[~gs.index.isin(uncoded.index), "Parkbereiche_Kennzeichen"].astype(str))
    if coded != list(GOSLAR_CODES):
        raise SystemExit(f"Goslar resident_parking_zones holds the codes {coded}; the step needs each of "
                         f"{list(GOSLAR_CODES)} exactly once")
    edits = {str(value)[:10] for value in gs["source_data_edit_date"]}
    if edits != {mz.GOSLAR_LAST_EDIT}:
        raise SystemExit(f"Goslar resident_parking_zones: last edit {sorted(edits)}, the recorded provenance states "
                         f"{mz.GOSLAR_LAST_EDIT}")
    mz._single(gs["source_layer_id"], "Goslar resident_parking_zones source_layer_id")


def load_district_packages(directory, expected_sha256: Optional[dict] = None) -> dict:
    """The verified district layers of the owner's packages in ``directory`` (EPSG:25832).

    Every package of ``DISTRICT_PACKAGES`` must exist as ``<name>.zip`` with exactly the SHA-256 of
    ``municipal_zones.PACKAGE_SHA256`` (or of ``expected_sha256``, the same names, for synthetic test packages), else
    ``SystemExit``: a changed package is never read. Returns {"files": name -> {"file", "sha256", "bytes"},
    "bs_districts", "gs_districts"}.
    """
    expected = {name: mz.PACKAGE_SHA256[name] for name in DISTRICT_PACKAGES} if expected_sha256 is None \
        else dict(expected_sha256)
    if set(expected) != set(DISTRICT_PACKAGES):
        raise SystemExit(f"expected SHA-256 for {sorted(expected)}, the step reads {sorted(DISTRICT_PACKAGES)}")
    directory = Path(directory)
    files, layers = {}, {}
    for name in DISTRICT_PACKAGES:
        path = directory / f"{name}.zip"
        if not path.is_file():
            raise SystemExit(f"{path} missing: copy the owner's package {name}.zip unchanged into {directory}")
        actual = mz.file_sha256(path)
        if actual != expected[name]:
            raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {expected[name]} (data record "
                             "parking_zones_2026); a changed package is never read")
        files[name] = {"file": path.name, "sha256": actual, "bytes": path.stat().st_size}
        layers[name] = mz._read_layer(path, name, DISTRICT_LAYER)
    _check_attributes(layers)
    print("district packages verified: " + ", ".join(f"{name} ({entry['bytes']} bytes, SHA-256 {entry['sha256']})"
                                                     for name, entry in files.items()))
    return {"files": files, "bs_districts": layers["Braunschweig_Parkzonen"], "gs_districts": layers["Goslar_Parkdaten"]}


# ---------------------------------------------------------------- the districts


def _ascii(text) -> str:
    """The text with German umlauts transliterated; anything else non-ASCII raises (the committed files are ASCII)."""
    result = cc.ascii_transliteration(str(text))
    offending = sorted({character for character in result if ord(character) > 127})
    if offending:
        raise SystemExit(f"{text!r} contains the non-ASCII characters {offending} that the committed files cannot hold")
    return result


def _polygonal(geometry):
    """The polygonal part of a geometry (a difference can leave lines or points on a shared edge), else None."""
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [part for part in getattr(geometry, "geoms", []) if part.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else None


def _braunschweig_records(inputs: dict) -> list:
    layer = inputs["bs_districts"]
    sha = inputs["files"]["Braunschweig_Parkzonen"]["sha256"]
    source_url = mz._single(layer["source"], "Braunschweig resident_parking_zones source")
    records = []
    for _, row in layer.iterrows():
        code = str(row["zone_id"])
        note = (f"{BRAUNSCHWEIG_PROVENANCE}. District {code} of the layer resident_parking_zones of "
                f"Braunschweig_Parkzonen.gpkg (package Braunschweig_Parkzonen.zip, SHA-256 {sha[:12]}...): the inner "
                "boundaries between A, B and C follow the vector paths of the city's district map "
                "(Bewohnerparkplaetze_ABC.pdf; PDF map code 1112, source date 2012-11 per the package), the outer "
                "boundary the current published overview map, traced at the stroke centre; georeferenced by an "
                "affine fit on the Open GeoData city map "
                f"1:20,000 of March 2025 (fit RMSE {float(row['registration_rmse_m']):.2f} m over the matched map "
                f"features, a consistency measure, not the boundary accuracy; the "
                f"{BRAUNSCHWEIG_WORKING_ACCURACY_M:.0f} m are the package's working estimate, not a confidence "
                "bound). A geographic envelope: it includes buildings, green spaces and other areas without parking, "
                "and street-side rules and overlapping permit areas are not resolved.")
        records.append({"district_id": f"bs_district_{code.lower()}", "district_code": code,
                        "municipality_ags": mz.BS_AGS, "name": _ascii(row["name"]),
                        "geometry_source": pz.DIGITISED_MAP_GEOMETRY_SOURCE, "source_url": source_url,
                        "source_date": SUPPLIED_ON, "digitised_on": str(row["digitized_on"])[:10],
                        "digitising_note": note, "geometry": row.geometry})
    return records


def _goslar_records(inputs: dict) -> list:
    layer = inputs["gs_districts"]
    sha = inputs["files"]["Goslar_Parkdaten"]["sha256"]
    layer_id = int(mz._single(layer["source_layer_id"], "Goslar resident_parking_zones source_layer_id"))
    codes = layer["Parkbereiche_Kennzeichen"]
    uncoded = layer[codes.isna() | (codes.astype(str).str.strip() == "")].iloc[0]
    records = []
    for _, row in layer[~layer.index.isin([uncoded.name])].iterrows():
        code = str(row["Parkbereiche_Kennzeichen"])
        geometry = row.geometry
        sources = [f"OBJECTID {int(row['OBJECTID'])}"]
        sentences = []
        if bool(row["geometry_repaired"]):
            sentences.append("The package repaired the polygon (ring self-touching, GEOS make_valid, area change "
                             "below 1e-8 m2) and flags it geometry_repaired; this layer holds the package's valid "
                             "polygon.")
        if code == GOSLAR_JOINED_CODE:
            geometry = unary_union([geometry, uncoded.geometry])
            sources.append(f"OBJECTID {int(uncoded['OBJECTID'])}")
            sentences.append(
                f"Joined features: {sources[0]} (code {code}) and {sources[1]} (no code, description "
                f"'{GOSLAR_UNCODED_DESCRIPTION}') are united into this one district by owner decision 2026-10-01 "
                "(spec Amendment C3); the package kept them apart and states that it did not merge them.")
        note = (f"{GOSLAR_PROVENANCE}. Layer resident_parking_zones (service layer {layer_id}, 'Parkbereiche') of "
                f"Goslar_Parkdaten.gpkg (package Goslar_Parkdaten.zip, SHA-256 {sha[:12]}...), "
                f"{'feature' if len(sources) == 1 else 'features'} {' and '.join(sources)}: the polygon as the "
                "service publishes it (changes are listed below), exported on 2026-10-01. The service states no "
                "positional accuracy, and the package no reuse licence. " + " ".join(sentences)).strip()
        records.append({"district_id": f"gs_district_{code.lower()}", "district_code": code,
                        "municipality_ags": mz.GS_AGS, "name": _ascii(row["Parkbereiche_Beschreibung"]),
                        "geometry_source": pz.FEATURE_SERVICE_GEOMETRY_SOURCE,
                        "source_url": f"{GOSLAR_SERVICE_URL}/{layer_id}", "source_date": SUPPLIED_ON,
                        "digitised_on": mz.MUNICIPAL_DIGITISED_ON, "digitising_note": note, "geometry": geometry})
    return records


def resolve_overlaps(records: list) -> list:
    """Cut every overlap larger than the loader tolerance from the district with the later ``district_id``.

    Districts are processed in id order and each loses the area it shares with an earlier one, so the earlier district
    keeps its published polygon. The note of the district that lost area records the overlap, the district that kept
    it and the rule. Raises ``SystemExit`` if a district would be left without area.
    """
    ordered = sorted(records, key=lambda record: record["district_id"])
    for position, later in enumerate(ordered):
        cuts = []
        for earlier in ordered[:position]:
            area = later["geometry"].intersection(earlier["geometry"]).area
            if area > pz.OVERLAP_TOLERANCE_M2:
                remaining = _polygonal(later["geometry"].difference(earlier["geometry"]))
                if remaining is None:
                    raise SystemExit(f"{later['district_id']} lies entirely inside {earlier['district_id']}")
                later["geometry"] = remaining
                cuts.append(f"{area:.2f} m2 with {earlier['district_id']}")
        if cuts:
            later["digitising_note"] += (
                " Overlap cut: this polygon overlapped " + "; ".join(cuts) + " in the source. No source says which "
                "district owns an overlap, so the district with the later id gives it up (rule of the curation "
                "step), and every point lies in at most one district.")
    return sorted(ordered, key=lambda record: (record["municipality_ags"], record["district_code"]))


#: The two properties of the districts of spec v3 B3 (the first release has none).
STREET_DISTRICT_EXTRA_COLUMNS = ("source_rows", "mixed_zone_rows")


def build_street_districts(research_package, osm_extract, settings: Optional[rds.MatchSettings] = None) -> tuple:
    """(frame, summaries): the districts of Wolfsburg, Peine, Helmstedt and Wolfenbuettel (spec v3 B3) and the counts of
    each town's run.

    Verifies the research package (SHA-256 of the zip and of the members the districts rest on), reads the OSM extract
    ONCE for the union box of the four towns (the GDAL driver scans the file per layer) and builds each town from its
    list or map; an unmatched street of a list stops the step.
    """
    settings = settings or rds.MatchSettings()
    package_sha256 = rds.verify_package(research_package)
    rdm.verify_map_members(research_package)
    towns = (rds.WOLFSBURG, rds.PEINE, rdm.HELMSTEDT, rdm.WOLFENBUETTEL)
    west, south = min(town.bbox_wgs84[0] for town in towns), min(town.bbox_wgs84[1] for town in towns)
    east, north = max(town.bbox_wgs84[2] for town in towns), max(town.bbox_wgs84[3] for town in towns)
    network = rds.read_osm_extract(osm_extract, (west, south, east, north))
    provenance = rds.Provenance(package_name=f"{rds.PACKAGE_NAME}.zip", package_sha256=package_sha256,
                                osm_extract=Path(osm_extract).name, osm_sha256=rds._file_sha256(osm_extract))
    results = []
    for town in (rds.WOLFSBURG, rds.PEINE):
        parsed = rds.read_town_list(town, research_package)
        spec = replace(town, list_source_url=parsed.source_url, list_document_date=parsed.document_date)
        results.append(rds.build_town_districts(spec, parsed.rows, rds.clip_street_data(network, town.bbox_wgs84), settings,
                                                provenance, parsed.zone_area_text))
    results.append(rdm.build_ring_district(rdm.HELMSTEDT, rdm.HELMSTEDT_SPEC,
                                           rds.clip_street_data(network, rdm.HELMSTEDT.bbox_wgs84), provenance))
    results.append(rdm.build_street_map_district(rdm.WOLFENBUETTEL, rdm.WOLFENBUETTEL_SPEC,
                                                 rds.clip_street_data(network, rdm.WOLFENBUETTEL.bbox_wgs84), settings,
                                                 provenance))
    frame = pd.concat([result.districts for result in results], ignore_index=True)
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=cc.METRIC_CRS), [result.summary for result in results]


def build_districts(inputs: dict, street_districts: Optional[gpd.GeoDataFrame] = None) -> gpd.GeoDataFrame:
    """The committed district layer (EPSG:25832) from the verified packages: one row per district, sorted by
    municipality and code, with ``pz.DISTRICT_PROVENANCE_COLUMNS`` and the geometry (and, with ``street_districts``, the
    properties ``STREET_DISTRICT_EXTRA_COLUMNS``).

    Joins the Goslar feature without a code to district C and cuts overlaps (``resolve_overlaps``); the districts of
    spec v3 B3 (``street_districts``, from ``build_street_districts``) are added as they are. The result passes
    ``braunschweig.parking.zones.validate_resident_districts``.
    """
    records = resolve_overlaps(_braunschweig_records(inputs) + _goslar_records(inputs))
    frame = gpd.GeoDataFrame(pd.DataFrame(records), geometry="geometry", crs=cc.METRIC_CRS)
    columns = list(pz.DISTRICT_PROVENANCE_COLUMNS)
    if street_districts is not None:
        frame = pd.concat([frame, street_districts], ignore_index=True)
        frame = frame.sort_values(["municipality_ags", "district_code"]).reset_index(drop=True)
        frame["mixed_zone_rows"] = frame["mixed_zone_rows"].astype("Int64")
        columns += list(STREET_DISTRICT_EXTRA_COLUMNS)
    frame = gpd.GeoDataFrame(frame[columns + ["geometry"]], geometry="geometry", crs=cc.METRIC_CRS).reset_index(drop=True)
    for column in pz.DISTRICT_PROVENANCE_COLUMNS:
        for value in frame[column]:
            _ascii(value)
    try:
        pz.validate_resident_districts(frame)
    except ValueError as error:
        raise SystemExit(f"the built district layer is invalid: {error}") from error
    return frame


def write_district_file(frame: gpd.GeoDataFrame, path) -> None:
    """WGS84 GeoJSON (seven decimals) with the licence and attribution members; foreign members are allowed by
    RFC 7946 and ignored by GeoJSON readers."""
    with_street_districts = "source_rows" in frame.columns
    members = json.dumps({"license": LICENSE_WITH_STREET_DISTRICTS if with_street_districts else LICENSE,
                          "attribution": ATTRIBUTION + (STREET_DISTRICTS_ATTRIBUTION if with_street_districts else "")})
    # The layer name is the "name" member of the file; fixed, so that the bytes do not depend on the file name.
    frame.to_crs("EPSG:4326").to_file(Path(path), driver="GeoJSON", layer=LAYER_NAME, COORDINATE_PRECISION=7,
                                      FOREIGN_MEMBERS_COLLECTION=members)


def print_paid_zone_shares(districts: gpd.GeoDataFrame, zones_path, tariffs_path) -> None:
    """Print, per district of spec v3 B3, the share of its area inside the paid street zones of its town (an indicator,
    not a gate; ``rds.paid_zone_shares``). Says so when the restricted zone file or the tariff table is absent."""
    if not Path(zones_path).is_file() or not Path(tariffs_path).is_file():
        print(f"paid-zone share indicator skipped: {zones_path} or {tariffs_path} is absent")
        return
    new = districts[districts["source_rows"].notna()]
    table = rds.paid_zone_shares(new, pz.load_zone_polygons(zones_path), pz.load_tariffs(tariffs_path))
    print("share of the district area inside the paid street zones of its town (indicator, not a gate):")
    for _, row in table.iterrows():
        print(f"  {row['district_id']}: {row['paid_street_share']:.1%} of {row['district_area_m2']:,.0f} m2")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--municipal-dir", required=True,
                        help="raw_sources/municipal_2026-10-01 with the owner's packages (SHA-256 checked)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the district file to write")
    parser.add_argument("--replace", action="store_true", help="replace an existing --out file")
    parser.add_argument("--research-package", type=Path,
                        help="Parkdaten_Nachrecherche_2026-10-14.zip (SHA-256 checked): adds the districts of spec v3 B3")
    parser.add_argument("--osm-extract", type=Path, help="the OSM extract of the pipeline (PBF); with --research-package")
    parser.add_argument("--zones-path", type=Path, default=DEFAULT_ZONES,
                        help="zone polygons for the paid-zone share indicator of the districts of spec v3 B3")
    parser.add_argument("--tariffs-path", type=Path, default=DEFAULT_TARIFFS, help="tariff table of the zone polygons")
    parser.add_argument("--buffer-m", type=float, default=rds.DEFAULT_BUFFER_M,
                        help="street buffer of the street-list districts, metres (ASSUMPTION R3: 25)")
    args = parser.parse_args(argv)
    if (args.research_package is None) != (args.osm_extract is None):
        raise SystemExit("--research-package and --osm-extract belong together")
    if args.out.exists() and not args.replace:
        raise SystemExit(f"{args.out} already exists; pass --replace to replace it")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    street_districts, summaries = None, []
    if args.research_package is not None:
        street_districts, summaries = build_street_districts(args.research_package, args.osm_extract,
                                                             rds.MatchSettings(buffer_m=args.buffer_m))
    frame = build_districts(load_district_packages(args.municipal_dir), street_districts)
    # Written next to the target and reloaded with the strict production loader before it replaces the target, so an
    # invalid file never reaches the committed path.
    candidate = args.out.with_name(args.out.name + ".candidate")
    write_district_file(frame, candidate)
    try:
        reloaded = pz.load_resident_districts(candidate)
    except (ValueError, FileNotFoundError) as error:
        candidate.unlink(missing_ok=True)
        raise SystemExit(f"the written district file is not valid as stored: {error}") from error
    existed = args.out.exists()
    os.replace(candidate, args.out)
    for _, row in reloaded.iterrows():
        print(f"{row['district_id']}: {row.geometry.area:,.1f} m2 in {len(getattr(row.geometry, 'geoms', [row.geometry]))} "
              "part(s)")
    for summary in summaries:
        print(json.dumps(summary, sort_keys=True))
    if street_districts is not None:
        print_paid_zone_shares(reloaded, args.zones_path, args.tariffs_path)
    print(f"wrote {args.out} ({len(reloaded)} districts; {'replaced the previous file' if existed else 'new file'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
