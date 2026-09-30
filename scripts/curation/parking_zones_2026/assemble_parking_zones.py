"""Assemble eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson (one-off curation, issue #249).

Not part of the pipeline and never run by synpp. Inputs (all gitignored, from the curation session of 2026-09-29):
the Braunschweig zones Ia/Ib of extract_braunschweig_zone_map.py and the affine of georeference_zone_map.py, the
Stadthalle fee islands of digitise_stadthalle_fee_islands.py, the Overpass responses under raw_overpass/ (one per
municipality, written by scripts/build_parking_zones_from_osm.py), the OSM-derived car network of the local MATSim
scenario eqasim-data/output_bs (2026-04-29) for the three towns whose Overpass request failed, and the cached output
of data.spatial.municipalities (containment check). Every polygon gets geometry_source, source_url, source_date,
digitised_on and a digitising_note with its method and parameters; overlaps are resolved by an explicit precedence
(the more specific zone is cut out of the enclosing one); every polygon must lie at least 99 % inside its
municipality. The file carries the ODbL licence and attribution as top-level members, because every polygon's
coordinates depend on OpenStreetMap (outline, street or georeference).

Parking cost zones v2, lever 1 (issue #436, spec amendments A1 and A2, fix round 1 rulings R-T1-e to R-T1-g;
``--erosion-dir``): the rule-based cores of ``scripts/build_parking_zones_from_osm.py --regulation`` with the
pre-registered parameters (``<ags>_core_<tag>.geojson`` and ``<ags>_qa_<tag>.json``,
``zone_geometry.PRE_REGISTERED_PARAMETERS.tag()``; one per municipality of ``EROSION_ZONES_FROM_CORE`` and
``EROSION_QA_ONLY``, ``load_erosion_inputs`` refuses other parameters and counterfactuals) enter as ``osm_fee_erosion``
polygons with the provenance fields ``unavoidable_walk_m`` and ``osm_timestamp``, but only where the pre-registered
acceptance rule Q4 accepted the core. Goslar, Wolfenbuettel and Gifhorn: an accepted core replaces the
``centre_approximation`` polygon of the same zone id (``erosion_replacement``; tariff row unchanged). Braunschweig
(``assign_braunschweig_pieces``, after every v1 zone has taken its area in ``apply_precedence``): the core minus the
zones of the release falls into connected pieces; pieces below the core's ``minimum_island_m2`` are dropped
(ASSUMPTION Q2, re-applied after the cut); every other piece is ASSIGNED WHOLE to the georeferenced ParkGO annex zone
(``--reference-outline``, extract_parkgo_annex_zones.py) it overlaps most, never clipped (R-T1-f): zone Ia ->
``bs_zone_ia_sued``, zone II -> ``bs_zone_ii``; a piece whose largest overlap is zone Ib (which keeps its v1 polygon)
or that overlaps no annex zone is not zoned. The QA table reports every piece with its share outside the assigned
outline. The other municipalities are QA only. A municipality without a response (a request that failed after all
attempts, see ``overpass_failures.log``) keeps its v1 polygon. Every curated municipality gets one row of the
committed QA table ``--qa-out`` (``braunschweig.parking.zones`` ``ZONE_QA_COLUMNS``, every column defined in its header,
``QA_COLUMN_GLOSSARY``); ``--counterfactual-qa`` adds the diagnostic runs of ``--all-kerbside-streets-regulated`` to the
note of their municipality. Without ``--erosion-dir`` the output is the v1 file byte for byte.

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/assemble_parking_zones.py --ia-ib bs_zone_map_ia_ib.geojson \
        --affine bs_zone_map_affine.json --fee-islands stadthalle_fee_islands.geojson \
        --raw-overpass eqasim-data/data/braunschweig/parking/raw_overpass \
        --network <main checkout>/eqasim-data/output_bs/braunschweig_1pct_network.xml.gz \
        --municipalities <main checkout>/eqasim-data/cache_bs_bpsmoke/data.spatial.municipalities__<hash>.p \
        --out eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson \
        [--erosion-dir eqasim-data/data/braunschweig/parking/raw_overpass \
         --reference-outline eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_zones_georeferenced.geojson \
         --qa-out eqasim-data/data/braunschweig/parking/parking_zones_2026_qa.csv \
         --counterfactual-qa eqasim-data/data/braunschweig/parking/raw_overpass/03101000_qa_<tag>_allstreets.json ...]
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import unicodedata
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union

import curation_common as cc

# The script runs from its own directory (curation_common); the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import zone_geometry as zg  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

DIGITISED_ON = "2026-09-29"
LICENSE = ("ODbL-1.0: every polygon is derived from OpenStreetMap data (OSM outlines, OSM streets, or a georeference "
           "on OSM street centrelines); Open Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/")
ATTRIBUTION = ("(c) OpenStreetMap contributors (https://www.openstreetmap.org/copyright). Zone boundaries after the "
               "parking maps and ordinances of the City of Braunschweig (Abt. Geoinformation), the TU Braunschweig GB3 "
               "parking pages and the municipal parking pages of Wolfsburg, Salzgitter, Peine, Goslar, Wolfenbuettel, "
               "Gifhorn and Helmstedt (source_url per feature).")
BS_PARKRAUM_URL = "https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/parkraummanagement.php"
ZONE_MAP_URL = ("https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/ausweitung-parkgebuehrenpflicht/"
                "20251126_Karte-Parkzone-Innenstadt-a-b-2025-01.jpg.scaled/a72c34d0df9f9693b72eca1f31da2e9f.jpg")
BGA_URL = "https://www.braunschweig.de/politik_verwaltung/politik/stadtrecht/2_09_Entgeltordnung-BgA-Parkraumbewirtschaftung.pdf"
TU_URL = "https://www.tu-braunschweig.de/gb3/parkraumbewirtschaftung/parkbereiche"
STREET_BUFFER_M = 15.0
PARKING_BUFFER_M = 5.0
STADTHALLE_STREETS = ["Adolfstraße", "Bertramstraße", "Körnerstraße", "Marthastraße", "Gerstäckerstraße",
                      "Kleine Campestraße", "Lachmannstraße", "Villierstraße", "Kleine Leonhardstraße"]
STADTHALLE_WINDOW = box(604550, 5790650, 605250, 5791350)
PRECEDENCE = ["bs_bga_markthalle", "bs_bga_kannengiesserstrasse", "bs_bga_an_der_martinikirche",
              "bs_bga_jodutenstrasse_klint", "bs_bga_suedstrasse", "tu_international_house",
              "bs_parkscheininsel_marthastrasse_koernerstrasse", "bs_parkscheininsel_gerstaeckerstrasse_kleine_campestrasse",
              "bs_parkscheininsel_mentestrasse", "bs_zone_ia", "bs_zone_ib", "bs_resident_stadthalle_132",
              "tu_zentralcampus", "tu_campus_nord", "tu_campus_ost_beethovenstrasse", "tu_campus_ost_langer_kamp",
              "tu_forschungsflughafen"]
MINIMUM_PART_M2 = 20.0
SIMPLIFY_M = 0.5
MINIMUM_INSIDE_SHARE = 0.99

# ---------------------------------------------------------------- parking cost zones v2, lever 1 (issue #436)
EROSION_DIGITISED_ON = "2026-09-30"
#: osm_fee_erosion geometry is cut against its neighbours grown by this clearance (m): the zone file stores WGS84 with
#: 7 decimals (up to 1.1 cm at 52 deg N), and two independently rounded outlines along a long shared edge would
#: otherwise overlap by more than the 1 m2 tolerance of the validator (1.6 m2 along 800 m in the assembly test).
EROSION_CUT_CLEARANCE_M = 0.05
LICENSE_V2 = ("ODbL-1.0: every polygon is derived from OpenStreetMap data (OSM outlines, OSM streets, the OSM on-street "
              "parking and car-park tags of the osm_fee_erosion zones, or a georeference on OSM street centrelines); "
              "Open Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/")
ATTRIBUTION_V2 = ("(c) OpenStreetMap contributors (https://www.openstreetmap.org/copyright). Zone boundaries after the "
                  "parking maps and ordinances of the City of Braunschweig (Abt. Geoinformation; the ParkGO annex map "
                  "assigns the osm_fee_erosion zones bs_zone_ia_sued and bs_zone_ii), the TU Braunschweig GB3 parking "
                  "pages and the municipal parking pages of Wolfsburg, Salzgitter, Peine, Goslar, Wolfenbuettel, "
                  "Gifhorn and Helmstedt (source_url per feature).")
BS_AGS = "03101000"
#: Municipalities whose accepted core becomes zone geometry (plan Task 1 Step 4): Braunschweig's new zones of spec
#: amendment A2 (assigned by the annex outlines), the other three replace their centre_approximation polygon.
EROSION_ZONES_FROM_CORE = {BS_AGS: None, "03153017": "gs_altstadt_zone1", "03158037": "wf_innenstadt",
                           "03151009": "gf_innenstadt"}
#: Municipalities whose core is computed and recorded for QA only (their v1 polygons stay).
EROSION_QA_ONLY = {"03103000": "wob_innenstadt", "03157006": "pe_innenstadt", "03154028": "he_innenstadt",
                   "03102000": "sz_lebenstedt"}
MUNICIPALITY_NAMES = {BS_AGS: "Braunschweig, Stadt", "03153017": "Goslar, Stadt", "03158037": "Wolfenbuettel, Stadt",
                      "03151009": "Gifhorn, Stadt", "03103000": "Wolfsburg, Stadt", "03157006": "Peine, Stadt",
                      "03154028": "Helmstedt, Stadt", "03102000": "Salzgitter, Stadt"}
#: Braunschweig zones of amendment A2 and the annex outline (extract_parkgo_annex_zones.py) that assigns them.
BS_EROSION_ZONES = {"bs_zone_ia_sued": "ia", "bs_zone_ii": "ii"}
PARKGO_URL = "https://www.braunschweig.de/politik_verwaltung/politik/stadtrecht/2_08_Parkgebuehrenordnung_2025.pdf"
ANNEX_REFERENCE = ("ParkGO annex map (page 3 of 2_08_Parkgebuehrenordnung_2025.pdf), zones Ia, Ib and II, georeferenced "
                   "by the v1 affine (outline RMS 11.1 m, BgA check points 26.8 m RMS)")
QA_INTRO = (
    "Curation QA of the rule-based parking zone cores (parking cost zones v2, lever 1, issue #436; spec amendments A1 and",
    "A2, fix round 1 rulings R-T1-e to R-T1-g): one row per curated municipality, written by",
    "scripts/curation/parking_zones_2026/assemble_parking_zones.py from the <ags>_qa_<tag>.json files of",
    "scripts/build_parking_zones_from_osm.py --regulation (raw responses local under raw_overpass/, gitignored).",
    "Construction (braunschweig/parking/zone_geometry.py): R = ways with a paid, restricted or forbidden side buffered",
    "25 m plus paid or restricted parking objects buffered 10 m; fill(R) = holes of R up to maximum_filled_hole_m2",
    "filled; F = ways with a free public side plus free public street-side areas; Z = erode(fill(R), walk_m) minus the",
    "walk_m buffer of F, parts below minimum_island_m2 dropped. A side mapped separately (parking:<side>=separate) is",
    "neither R nor F; the separately mapped area decides by its own class. Units m and m2; empty = undefined. The owner",
    "reviews the decisions at the PR; scripts/validate_parking_zones.py re-applies rule Q4 and the pre-registered",
    "parameters. Counts and areas are derived from OpenStreetMap data: (c) OpenStreetMap contributors, ODbL 1.0",
    "(https://www.openstreetmap.org/copyright). Columns:",
)
#: One definition per column of ``pz.ZONE_QA_COLUMNS`` (written as '# <column>: <definition>' into the header).
QA_COLUMN_GLOSSARY = {
    "ags": "8-digit AGS of the curated municipality",
    "name": "municipality name (BA Gemeindeband, ASCII)",
    "role": "zones_from_core = an accepted core may become release polygons; qa_only = recorded, the v1 polygon stays",
    "raw_response": "file name of the Overpass regulation response under raw_overpass/ (local, gitignored)",
    "osm_timestamp": "OSM snapshot of that response (timestamp_osm_base, UTC)",
    "walk_m": "walking tolerance W in m (ASSUMPTION Q1, pre-registered 250)",
    "maximum_filled_hole_m2": "largest hole of R that is filled, m2 (ASSUMPTION Q3, pre-registered 20000)",
    "minimum_island_m2": "smallest part of Z that is kept, m2 (ASSUMPTION Q2, pre-registered 10000)",
    "segments": "highway ways in the response",
    "regulated_segments": "ways with a paid, restricted or forbidden side (they enter R)",
    "free_segments": "ways with a free public side (part of F)",
    "free_segments_without_fee_tag": "free ways none of whose free sides carries a fee tag (ASSUMPTION: no fee tag = free)",
    "mixed_segments": "ways with a regulated and a free side (in R and in F; F wins spatially)",
    "separate_segments": "ways with a side mapped separately (parking:<side>=separate: that side is neither R nor F)",
    "lots": "amenity=parking objects in the response (car parks and separately mapped street-side areas)",
    "free_street_side_areas": "free public street-side parking areas (part of F, buffered by walk_m like a free street)",
    "free_street_side_areas_without_fee_tag": "of them without any fee tag (ASSUMPTION: no fee tag = free)",
    "free_offstreet_lots": "free public off-street car parks (no free supply: the rule speaks of street parking)",
    "free_offstreet_lots_in_core": "of them intersecting Z (reported only)",
    "free_offstreet_lot_area_in_core_m2": "their area inside Z, m2",
    "tagging_completeness": "share of the kerbside street length (highway primary to living_street) inside fill(R) "
                            "whose way carries parking information (rule Q4 needs >= 0.60)",
    "regulated_area_m2": "area of R, m2",
    "filled_area_m2": "area of fill(R), m2",
    "eroded_filled_area_m2": "area of erode(fill(R), walk_m) before F is subtracted, m2 (0 = no part of fill(R) is wider "
                             "than 2 walk_m)",
    "core_area_m2": "area of Z, m2 (rule Q4 needs >= 10000)",
    "core_parts": "number of parts of Z",
    "reference": "outline the core is compared with (Braunschweig: the georeferenced ParkGO annex, a plausibility "
                 "check; elsewhere the v1 polygon)",
    "core_share_inside_reference": "area of Z inside the reference / area of Z",
    "reference_share_covered_by_core": "area of Z inside the reference / area of the reference",
    "reference_tagging_completeness": "tagging completeness of the kerbside streets inside the reference outline",
    "largest_outline_distance_m": "Hausdorff distance between the outlines of Z and the reference, m",
    "reference_paid_ways": "paid ways touching the reference outline",
    "reference_paid_street_side_areas": "paid street-side areas touching the reference outline (the fee of a side "
                                        "mapped separately sits on its area)",
    "reference_free_side_ways": "ways with a free side touching the reference outline (conflicts with the ordinance "
                                "area: OSM makes that street side free)",
    "reference_free_side_length_m": "their length inside the reference outline, m",
    "reference_free_street_side_areas": "free street-side areas touching the reference outline",
    "reference_conflicts": "per reference zone: the paid ways and paid street-side areas, the ways with a free side "
                           "(of them without fee tag) and the free street-side areas, with length or area and names",
    "piece_assignment": "Braunschweig: every piece of the core after the v1 zones are cut out, its area, the annex "
                        "zone it overlaps most and the share outside it (assigned whole, never clipped)",
    "q4_decision": "pre-registered acceptance rule Q4 (accepted = core >= 1 ha and completeness >= 0.60; rejected) or "
                   "request_failed (no response after all attempts, the v1 polygon stays)",
    "applied": "true when the core became release polygons (geometry_source osm_fee_erosion)",
    "zone_ids": "the osm_fee_erosion polygons of an applied row (';'-separated)",
    "note": "the Q4 reason, conflicts, diagnostics and the decision in words",
}
BS_PIECE_DIAGNOSIS = ("the ParkGO annex outlines are a plausibility check and a tariff assignment, never a clipping "
                      "geometry; pedestrian streets are not fetched by the regulation query")


def zone_record(zone_id, ags, geometry, geometry_source, source_url, note, *, walk_m=None, osm_timestamp=None,
                source_date=DIGITISED_ON, digitised_on=DIGITISED_ON, minimum_part_m2=MINIMUM_PART_M2) -> dict:
    """One zone of the release as the assembly handles it (keys starting with '_' are not written)."""
    return {"zone_id": zone_id, "_ags": ags, "geometry": geometry, "geometry_source": geometry_source,
            "source_url": source_url, "source_date": source_date, "digitised_on": digitised_on, "digitising_note": note,
            "unavoidable_walk_m": walk_m, "osm_timestamp": osm_timestamp, "_minimum_part_m2": minimum_part_m2}


def last_failure(directory, ags: str) -> str:
    """The last line of overpass_failures.log for ``ags`` (time and error), or '' when there is none."""
    path = Path(directory) / "overpass_failures.log"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    matching = [line for line in lines if line.split("\t")[1:2] == [ags]]
    return matching[-1].replace("\t", " ") if matching else ""


def load_erosion_inputs(directory, municipalities=None, parameters=zg.PRE_REGISTERED_PARAMETERS) -> dict:
    """ags -> {"qa": dict or None, "core": EPSG:25832 frame or None, "failure": last failed request or ''}.

    Reads ``<ags>_qa_<tag>.json`` and ``<ags>_core_<tag>.geojson`` of the pre-registered parameter tag only and
    refuses (``SystemExit``) a QA file whose parameters differ from ``parameters`` or that names a counterfactual,
    and a municipality with neither a QA file nor a failed request (it was never run).
    """
    directory = Path(directory)
    municipalities = tuple(municipalities or tuple(EROSION_ZONES_FROM_CORE) + tuple(EROSION_QA_ONLY))
    tag = parameters.tag()
    expected = parameters.as_dict()
    erosion = {}
    for ags in municipalities:
        qa_path = directory / f"{ags}_qa_{tag}.json"
        failure = last_failure(directory, ags)
        if not qa_path.is_file():
            if not failure:
                raise SystemExit(f"{ags}: neither {qa_path.name} nor a failed request in overpass_failures.log; run "
                                 "scripts/build_parking_zones_from_osm.py --regulation for it first")
            erosion[ags] = {"qa": None, "core": None, "failure": failure}
            continue
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        if qa["ags"] != ags:
            raise SystemExit(f"{qa_path} belongs to {qa['ags']}, not {ags}")
        if qa.get("counterfactual"):
            raise SystemExit(f"{qa_path}: counterfactual {qa['counterfactual']!r} is a diagnostic, never a release input")
        mismatch = {key: qa["parameters"].get(key) for key, value in expected.items()
                    if not math.isclose(float(qa["parameters"].get(key, math.nan)), value, rel_tol=1e-9)}
        if mismatch:
            raise SystemExit(f"{qa_path}: parameters {mismatch} are not the pre-registered {expected}")
        core = gpd.read_file(directory / f"{ags}_core_{tag}.geojson")
        core = core.set_crs("EPSG:4326") if core.crs is None else core
        erosion[ags] = {"qa": qa, "core": core.to_crs(cc.METRIC_CRS), "failure": failure}
    return erosion


def load_counterfactuals(paths) -> list:
    """The QA files of diagnostic counterfactual runs (``--all-kerbside-streets-regulated``) named on the command line."""
    documents = []
    for path in paths or ():
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not document.get("counterfactual"):
            raise SystemExit(f"{path} is not a counterfactual QA file")
        documents.append(dict(document, _path=Path(path).name))
    return documents


def accepted_core(entry: Optional[dict]):
    """Union of the core parts of an accepted QA entry, else None."""
    if not entry or entry["qa"] is None or entry["qa"]["decision"] != "accepted":
        return None
    return unary_union(list(entry["core"].geometry))


def erosion_note(qa: dict, *, replaces: str, assignment: str = "") -> str:
    """The digitising note of an osm_fee_erosion polygon: method, parameters, counts and the QA row."""
    p = qa["parameters"]
    return (f"Rule-based core of parking cost zones v2 (lever 1, spec amendment A1; braunschweig.parking.zone_geometry, "
            f"scripts/build_parking_zones_from_osm.py --regulation): OSM ways with a paid, restricted or forbidden side "
            f"buffered {p['street_buffer_m']:.0f} m and paid or restricted parking objects buffered "
            f"{p['lot_buffer_m']:.0f} m (R {qa['regulated_area_m2']:.0f} m2), holes up to "
            f"{p['maximum_filled_hole_m2']:.0f} m2 filled (fill(R) {qa['filled_area_m2']:.0f} m2), eroded by W = "
            f"{p['walk_m']:.0f} m and minus the {p['walk_m']:.0f} m buffer of the {qa['free_segments']} ways with a free "
            f"public side and the {qa['free_street_side_areas']} free street-side areas, parts below "
            f"{p['minimum_island_m2']:.0f} m2 dropped (Z {qa['core_area_m2']:.0f} m2 in {qa['core_parts']} "
            f"part{'' if qa['core_parts'] == 1 else 's'}). Regulation response "
            f"{qa['raw_response']} of the Overpass API (OSM base {qa['osm_timestamp']}; box {qa['bbox']}): "
            f"{qa['segments']} highway ways ({qa['regulated_segments']} regulated, {qa['free_segments']} with a free "
            f"side, {qa['mixed_segments']} both), {qa['lots']} parking objects; tagging completeness "
            f"{qa['tagging_completeness']:.3f} inside fill(R); acceptance rule Q4 met (Z >= 1 ha, completeness >= "
            f"0.60). " + (assignment + " " if assignment else "") + replaces
            + f" QA row {qa['ags']} of parking_zones_2026_qa.csv.")


def erosion_zone(zone_id, ags, geometry, source_url, qa, *, replaces, assignment="") -> dict:
    """An osm_fee_erosion zone record with walk and snapshot provenance and the core's minimum island size."""
    return zone_record(zone_id, ags, geometry, pz.EROSION_GEOMETRY_SOURCE, source_url,
                       erosion_note(qa, replaces=replaces, assignment=assignment),
                       walk_m=float(qa["parameters"]["walk_m"]), osm_timestamp=qa["osm_timestamp"],
                       source_date=EROSION_DIGITISED_ON, digitised_on=EROSION_DIGITISED_ON,
                       minimum_part_m2=float(qa["parameters"]["minimum_island_m2"]))


def erosion_replacement(zone_id, ags, url, what, erosion: dict) -> Optional[dict]:
    """The osm_fee_erosion polygon that replaces the v1 centre approximation ``zone_id`` (described by ``what``), or
    None when ``ags`` may not replace polygons or its core is not accepted."""
    if EROSION_ZONES_FROM_CORE.get(ags) != zone_id:
        return None
    entry = erosion.get(ags)
    core = accepted_core(entry)
    if core is None:
        return None
    return erosion_zone(zone_id, ags, core, url, entry["qa"], replaces=(
        "Replaces the v1 centre approximation of 2026-09-29 (" + what + ", buffered 40 m, from the car network of the "
        "local MATSim scenario because the fee request of that day failed with HTTP 504); the tariff row is unchanged."))


def apply_precedence(zones: list, precedence=PRECEDENCE) -> tuple:
    """Every zone in ``precedence`` order (then insertion order) takes its area; later zones lose the overlap.

    v1 zones: overlaps above 0.5 m2 are cut, parts below 20 m2 dropped, then simplified by ``SIMPLIFY_M``. An
    osm_fee_erosion zone is simplified BEFORE the cut and always cut against its neighbours grown by
    ``EROSION_CUT_CLEARANCE_M``, so the cut stays exact after the rounding of the file (simplifying a cut edge
    afterwards moves it by up to ``SIMPLIFY_M`` and lets a large core overlap its neighbours along long shared edges),
    and its parts below the core's minimum island size are dropped (Q2). Returns (zones kept, trims); raises when a v1
    zone is emptied.
    """
    by_id = {z["zone_id"]: z for z in zones}
    order = list(precedence) + [z["zone_id"] for z in zones if z["zone_id"] not in precedence]
    taken, trims = None, []
    for zone_id in order:
        if zone_id not in by_id:
            continue
        zone = by_id[zone_id]
        geometry = zone["geometry"].buffer(0)
        eroded_zone = zone["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE
        if eroded_zone:
            geometry = geometry.simplify(SIMPLIFY_M, preserve_topology=True).buffer(0)
        if taken is not None and eroded_zone:
            neighbours = taken.buffer(EROSION_CUT_CLEARANCE_M)
            if geometry.intersects(neighbours):
                trims.append((zone_id, round(geometry.intersection(taken).area, 1)))
                geometry = geometry.difference(neighbours)
        elif taken is not None and geometry.intersects(taken):
            overlap = geometry.intersection(taken).area
            if overlap > 0.5:
                geometry = geometry.difference(taken)
                trims.append((zone_id, round(overlap, 1)))
        geometry = cc.largest_parts(geometry.buffer(0), zone["_minimum_part_m2"])
        if not eroded_zone:
            geometry = geometry.simplify(SIMPLIFY_M, preserve_topology=True)
        zone["geometry"] = geometry
        if not geometry.is_empty:
            taken = geometry if taken is None else taken.union(geometry)
    emptied = [z["zone_id"] for z in zones if z["geometry"].is_empty]
    if [zone_id for zone_id in emptied if by_id[zone_id]["geometry_source"] != pz.EROSION_GEOMETRY_SOURCE]:
        raise SystemExit(f"zones emptied by the precedence cuts: {emptied}")
    if emptied:
        print("osm_fee_erosion zones without a part of the minimum island size after the cuts (not written):", emptied)
    return [z for z in zones if not z["geometry"].is_empty], trims


def _polygon_parts(geometry) -> list:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for member in getattr(geometry, "geoms", []) for part in _polygon_parts(member)]


def assign_braunschweig_pieces(core, zones: list, annex_zones: gpd.GeoDataFrame, qa: dict) -> tuple:
    """Braunschweig core minus every zone of the release -> connected pieces, each ASSIGNED WHOLE (ruling R-T1-f).

    The core is simplified by ``SIMPLIFY_M`` before the cut and cut against the zones grown by
    ``EROSION_CUT_CLEARANCE_M``, so the pieces never overlap a zone, also after the rounding of the file. A piece below the
    core's minimum island size is dropped (Q2 after the cut); every other piece goes to the annex zone it overlaps
    most (``BS_EROSION_ZONES``: Ia -> bs_zone_ia_sued, II -> bs_zone_ii) with the share of its area outside that
    outline reported; a piece whose largest overlap is zone Ib (which keeps its v1 polygon) or that overlaps no annex
    zone is not zoned. Returns (osm_fee_erosion zone records, one report dict per piece).
    """
    minimum = float(qa["parameters"]["minimum_island_m2"])
    taken = unary_union([z["geometry"] for z in zones]).buffer(EROSION_CUT_CLEARANCE_M)
    remainder = core.simplify(SIMPLIFY_M, preserve_topology=True).buffer(0).difference(taken)
    pieces = sorted(_polygon_parts(remainder), key=lambda piece: (piece.centroid.x, piece.centroid.y))
    target_of = {annex: zone_id for zone_id, annex in BS_EROSION_ZONES.items()}
    assigned = {zone_id: [] for zone_id in BS_EROSION_ZONES}
    report = []
    for number, piece in enumerate(pieces, 1):
        overlaps = {str(zone): float(piece.intersection(geometry).area) for zone, geometry in annex_zones.geometry.items()}
        best = max(overlaps, key=overlaps.get) if overlaps and max(overlaps.values()) > 0 else None
        row = {"piece_id": f"p{number:02d}", "area_m2": round(float(piece.area), 1), "annex_zone": best,
               "zone_id": None, "share_outside": None, "status": ""}
        if piece.area < minimum:
            row["status"] = "below_minimum_island"
        elif best is None:
            row["status"] = "no_annex_zone"
        elif best not in target_of:
            row["status"] = "largest_overlap_keeps_v1_polygon"
        else:
            row.update(status="assigned", zone_id=target_of[best],
                       share_outside=round(1.0 - overlaps[best] / piece.area, 4))
            assigned[target_of[best]].append(piece)
        report.append(row)
    records = []
    for zone_id, parts in assigned.items():
        if not parts:
            continue
        label = {"ia": "Ia", "ii": "II"}[BS_EROSION_ZONES[zone_id]]
        shares = ", ".join(f"{row['piece_id']} {row['area_m2']:.0f} m2 {100.0 * row['share_outside']:.1f} % outside"
                           for row in report if row["zone_id"] == zone_id)
        records.append(erosion_zone(zone_id, BS_AGS, unary_union(parts), PARKGO_URL, qa,
                                    replaces=("New in v2 (spec amendment A2): unzoned in v1, where the annex could not "
                                              "be georeferenced to an RMS of 10 m or better."),
                                    assignment=(f"Tariff zone {label} of the ParkGO (sec. 1(2), sec. 2(1)): the pieces of "
                                                "the core left after every v1 zone (Ia and Ib of the city's overview "
                                                "map, BgA car parks, Stadthalle concept, TU campus areas) was cut out, "
                                                f"each assigned WHOLE to the annex zone it overlaps most, zone {label} "
                                                f"({ANNEX_REFERENCE}; scripts/curation/parking_zones_2026/"
                                                "extract_parkgo_annex_zones.py), never clipped (ruling R-T1-f): "
                                                f"{shares}; the georeference carries an uncertainty of about 27 m.")))
    return records, report


def piece_assignment_text(pieces: list, minimum_island_m2: float) -> str:
    """The QA column piece_assignment: one clause per piece."""
    clauses = []
    for row in pieces:
        head = f"{row['piece_id']} {row['area_m2']:.0f} m2"
        if row["status"] == "assigned":
            clauses.append(f"{head} -> {row['zone_id']} (largest overlap annex zone {row['annex_zone']}, "
                           f"{100.0 * row['share_outside']:.1f} % outside it)")
        elif row["status"] == "below_minimum_island":
            clauses.append(f"{head} dropped (below the minimum island {minimum_island_m2:.0f} m2 after the v1 zones "
                           "were cut out)")
        elif row["status"] == "no_annex_zone":
            clauses.append(f"{head} not zoned (no annex zone)")
        else:
            clauses.append(f"{head} not zoned (largest overlap annex zone {row['annex_zone']}, which keeps its v1 "
                           "polygon)")
    return "; ".join(clauses)


def zone_frame(zones: list) -> gpd.GeoDataFrame:
    """The zones as the committed frame: provenance columns, plus ``pz.EROSION_PROVENANCE_COLUMNS`` when an
    osm_fee_erosion zone exists (so a v1-only release keeps its v1 layout)."""
    frame = gpd.GeoDataFrame([{k: v for k, v in z.items() if not k.startswith("_")} for z in zones],
                             geometry="geometry", crs=cc.METRIC_CRS)
    columns = ["zone_id", "geometry_source", "source_url", "source_date", "digitised_on", "digitising_note"]
    if bool((frame["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE).any()):
        columns += list(pz.EROSION_PROVENANCE_COLUMNS)
    return frame[columns + ["geometry"]]


def write_zone_file(frame: gpd.GeoDataFrame, path) -> None:
    """WGS84 GeoJSON with the licence and attribution members (the v2 wording when an osm_fee_erosion zone exists)."""
    has_erosion = bool((frame["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE).any())
    # RFC 7946 allows foreign members; GDAL writes them at the top level and GeoJSON readers ignore them.
    members = json.dumps({"license": LICENSE_V2 if has_erosion else LICENSE,
                          "attribution": ATTRIBUTION_V2 if has_erosion else ATTRIBUTION})
    frame.to_crs("EPSG:4326").to_file(Path(path), driver="GeoJSON", COORDINATE_PRECISION=7,
                                      FOREIGN_MEMBERS_COLLECTION=members)


def _number(value, digits):
    return "" if value is None else f"{value:.{digits}f}"


def conflicts_text(reference: dict) -> str:
    """The QA column reference_conflicts: per reference zone the paid ways and paid street-side areas, the ways with a
    free side (of them without fee tag) and the free street-side areas, with length or area and names."""
    zones = reference.get("zones") or {"reference": reference}
    clauses = []
    for label, zone in zones.items():
        paid, free, areas = zone["paid_ways"], zone["free_side_ways"], zone["free_street_side_areas"]
        paid_areas = zone["paid_street_side_areas"]

        def names(entry):
            return f": {', '.join(entry['names'])}" if entry["names"] else ""

        clauses.append(f"{label}: {paid['count']} paid ways ({paid['length_m']:.0f} m{names(paid)}); "
                       f"{paid_areas['count']} paid street-side areas ({paid_areas['area_m2']:.0f} m2{names(paid_areas)}); "
                       f"{free['count']} ways "
                       f"with a free side ({free['length_m']:.0f} m, {zone['free_side_ways_without_fee_tag']} without "
                       f"fee tag{names(free)}); {areas['count']} free street-side areas ({areas['area_m2']:.0f} "
                       f"m2{names(areas)})")
    return " | ".join(clauses)


def counterfactual_text(counterfactuals: list, ags: str) -> str:
    """The diagnostic counterfactual runs of ``ags`` in words (fill cap, erode(fill(R), W) inside the reference)."""
    clauses = []
    for document in counterfactuals:
        if document["ags"] != ags:
            continue
        p = document["parameters"]
        reference = document.get("reference") or {}
        inside = ", ".join(f"{label} {zone['eroded_filled_inside_m2']:.0f} m2"
                           for label, zone in (reference.get("zones") or {}).items())
        clauses.append(f"counterfactual {document['counterfactual']} at a fill cap of {p['maximum_filled_hole_m2']:.0f} "
                       f"m2 ({document['_path']}): erode(fill(R), {p['walk_m']:.0f} m) = "
                       f"{document['eroded_filled_area_m2']:.0f} m2, inside the reference "
                       f"{reference.get('eroded_filled_inside_m2', 0.0):.0f} m2 ({inside})")
    return "; ".join(clauses)


def qa_row(ags: str, qa, *, role: str, reference: str, applied_zones, note: str, failure: str = "",
           pieces_text: str = "") -> dict:
    """One row of the committed QA table (``pz.ZONE_QA_COLUMNS``)."""
    row = {column: "" for column in pz.ZONE_QA_COLUMNS}
    row.update({"ags": ags, "name": MUNICIPALITY_NAMES[ags], "role": role, "reference": reference,
                "applied": "true" if applied_zones else "false", "zone_ids": ";".join(applied_zones), "note": note,
                "piece_assignment": pieces_text})
    if qa is None:
        row.update({"q4_decision": "request_failed",
                    "note": f"no Overpass response after all attempts ({failure or 'no failure recorded'}); the "
                            f"municipality keeps its v1 polygon. " + note})
        return row
    reference_numbers = qa.get("reference") or {}
    row.update({"raw_response": qa["raw_response"], "osm_timestamp": qa["osm_timestamp"],
                "walk_m": _number(qa["parameters"]["walk_m"], 1),
                "maximum_filled_hole_m2": _number(qa["parameters"]["maximum_filled_hole_m2"], 1),
                "minimum_island_m2": _number(qa["parameters"]["minimum_island_m2"], 1),
                "tagging_completeness": _number(qa["tagging_completeness"], 6),
                "core_share_inside_reference": _number(reference_numbers.get("core_share_inside_reference"), 4),
                "reference_share_covered_by_core": _number(reference_numbers.get("reference_share_covered"), 4),
                "reference_tagging_completeness": _number(reference_numbers.get("tagging_completeness_inside"), 6),
                "largest_outline_distance_m": _number(reference_numbers.get("largest_outline_distance_m"), 1),
                "q4_decision": qa["decision"]})
    for column in ("segments", "regulated_segments", "free_segments", "free_segments_without_fee_tag", "mixed_segments",
                   "separate_segments", "lots", "free_street_side_areas", "free_street_side_areas_without_fee_tag",
                   "free_offstreet_lots", "free_offstreet_lots_in_core", "core_parts"):
        row[column] = str(int(qa[column]))
    for column in ("free_offstreet_lot_area_in_core_m2", "regulated_area_m2", "filled_area_m2", "eroded_filled_area_m2",
                   "core_area_m2"):
        row[column] = _number(qa[column], 1)
    if reference_numbers:
        row.update({"reference_paid_ways": str(reference_numbers["paid_ways"]["count"]),
                    "reference_paid_street_side_areas": str(reference_numbers["paid_street_side_areas"]["count"]),
                    "reference_free_side_ways": str(reference_numbers["free_side_ways"]["count"]),
                    "reference_free_side_length_m": _number(reference_numbers["free_side_ways"]["length_m"], 1),
                    "reference_free_street_side_areas": str(reference_numbers["free_street_side_areas"]["count"]),
                    "reference_conflicts": conflicts_text(reference_numbers)})
    return row


def qa_table_rows(erosion: dict, zones: list, pieces: list, counterfactuals=()) -> list:
    """One QA row per curated municipality of ``erosion`` (in ``EROSION_ZONES_FROM_CORE``, ``EROSION_QA_ONLY`` order)."""
    by_id = {z["zone_id"]: z for z in zones}
    order = [ags for ags in list(EROSION_ZONES_FROM_CORE) + list(EROSION_QA_ONLY) if ags in erosion]
    rows = []
    for ags in order:
        entry = erosion[ags]
        qa = entry["qa"]
        role = "zones_from_core" if ags in EROSION_ZONES_FROM_CORE else "qa_only"
        applied = sorted(z["zone_id"] for z in zones
                         if z["_ags"] == ags and z["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE)
        zone_id = EROSION_ZONES_FROM_CORE.get(ags) or EROSION_QA_ONLY.get(ags)
        if ags == BS_AGS:
            reference = ANNEX_REFERENCE
        elif role == "zones_from_core":
            reference = f"v1 polygon {zone_id} (centre_approximation, replaced where the core is accepted)"
        else:
            reference = f"v1 polygon {zone_id} ({by_id[zone_id]['geometry_source'] if zone_id in by_id else 'absent'})"
        notes = []
        if qa is not None:
            notes.append("Q4 " + (qa["decision"] if not qa["decision_reason"] else
                                  f"{qa['decision']}: {qa['decision_reason']}"))
            if not qa["eroded_filled_area_m2"]:
                notes.append(f"erode(fill(R), {qa['parameters']['walk_m']:.0f} m) is already empty before F is "
                             "subtracted (no part of fill(R) is wider than 2 W)")
            outside = (qa.get("reference") or {}).get("paid_segments_outside")
            if outside:
                notes.append(f"{outside} paid ways ({qa['reference']['paid_length_outside_m']:.0f} m) outside the "
                             "reference outline")
            if qa["free_segments"] or qa["free_street_side_areas"]:
                notes.append(f"{qa['free_segments_without_fee_tag']} of the {qa['free_segments']} free ways and "
                             f"{qa['free_street_side_areas_without_fee_tag']} of the {qa['free_street_side_areas']} free "
                             "street-side areas rest on the assumption 'no fee tag = free'")
            diagnosis = counterfactual_text(counterfactuals, ags)
            if diagnosis:
                notes.append(diagnosis)
        if role == "qa_only":
            notes.append(f"QA only (plan Task 1 Step 4): the v1 polygon {zone_id} stays")
        elif ags == BS_AGS and applied:
            notes.append(f"the core pieces left after the v1 zones are assigned whole (piece_assignment); "
                         f"{BS_PIECE_DIAGNOSIS}")
        elif ags == BS_AGS and qa is not None:
            notes.append(f"ParkGO zone II and the southern part of zone Ia stay unzoned; {BS_PIECE_DIAGNOSIS}")
        elif applied:
            notes.append(f"the core replaces the v1 centre approximation {zone_id}")
        elif qa is not None:
            notes.append(f"the v1 centre approximation {zone_id} stays")
        pieces_text = piece_assignment_text(pieces, float(qa["parameters"]["minimum_island_m2"])) \
            if ags == BS_AGS and pieces and qa is not None else ""
        rows.append(qa_row(ags, qa, role=role, reference=reference, applied_zones=applied, note="; ".join(notes) + ".",
                           failure=entry["failure"], pieces_text=pieces_text))
    return rows


#: German letters in the ASCII form of the committed parking files (umlauts as two letters, sharp s as ss).
_GERMAN_ASCII = str.maketrans({"\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue", "\u00c4": "Ae", "\u00d6": "Oe",
                               "\u00dc": "Ue", "\u00df": "ss", "\u1e9e": "SS"})


def ascii_transliteration(text: str) -> str:
    """``text`` in the ASCII form of the committed files: German letters as ae/oe/ue/ss, other accents dropped
    (NFKD); a character that has no ASCII form raises instead of being replaced silently."""
    folded = unicodedata.normalize("NFKD", text.translate(_GERMAN_ASCII))
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    offending = sorted({character for character in folded if ord(character) > 127})
    if offending:
        raise SystemExit(f"no ASCII form for {offending} in the QA table")
    return folded


def write_qa_table(path, rows: list) -> None:
    """The QA table with its header: the intro and one '# <column>: <definition>' line per column. OSM names are
    written in their ASCII transliteration (``ascii_transliteration``), like every committed parking file."""
    missing = [column for column in pz.ZONE_QA_COLUMNS if column not in QA_COLUMN_GLOSSARY]
    if missing or len(QA_COLUMN_GLOSSARY) != len(pz.ZONE_QA_COLUMNS):
        raise SystemExit(f"QA_COLUMN_GLOSSARY and ZONE_QA_COLUMNS differ: missing {missing}")
    header = [f"# {line}" for line in QA_INTRO] + [f"# {column}: {QA_COLUMN_GLOSSARY[column]}"
                                                    for column in pz.ZONE_QA_COLUMNS]
    table = pd.DataFrame(rows, columns=list(pz.ZONE_QA_COLUMNS))
    text = ascii_transliteration("\n".join(header) + "\n" + table.to_csv(index=False, lineterminator="\n"))
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    print(f"written {path} with {len(table)} rows: " + ", ".join(
        f"{row['ags']} {row['q4_decision']}{' applied ' + row['zone_ids'] if row['applied'] == 'true' else ''}"
        for row in rows))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ia-ib", required=True)
    parser.add_argument("--affine", required=True)
    parser.add_argument("--fee-islands", required=True, help="GeoJSON of digitise_stadthalle_fee_islands.py")
    parser.add_argument("--raw-overpass", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--municipalities", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--erosion-dir", help="v2 lever 1: directory with <ags>_core_<tag>.geojson and "
                                              "<ags>_qa_<tag>.json of the pre-registered parameters")
    parser.add_argument("--reference-outline", help="v2 lever 1: georeferenced ParkGO annex zones (Braunschweig)")
    parser.add_argument("--qa-out", help="v2 lever 1: the QA table to write (parking_zones_2026_qa.csv)")
    parser.add_argument("--counterfactual-qa", action="append", default=[],
                        help="v2 lever 1: QA file of a --all-kerbside-streets-regulated run, cited in the note")
    args = parser.parse_args(argv)
    if args.erosion_dir and not (args.reference_outline and args.qa_out):
        raise SystemExit("--erosion-dir needs --reference-outline and --qa-out")
    zones = []

    def add(zone_id, ags, geometry, geometry_source, source_url, note):
        zones.append(zone_record(zone_id, ags, geometry, geometry_source, source_url, note))

    # ---------------------------------------------------------------- v2 lever 1: rule-based cores (issue #436)
    erosion, counterfactuals, annex_zones = {}, [], None
    if args.erosion_dir:
        erosion = load_erosion_inputs(args.erosion_dir)
        counterfactuals = load_counterfactuals(args.counterfactual_qa)
        annex_zones = gpd.read_file(args.reference_outline).to_crs(cc.METRIC_CRS).set_index("zone")

    responses = {ags: cc.overpass_response(args.raw_overpass, ags)
                 for ags in ("03101000", "03102000", "03103000", "03154028", "03157006")}
    base = {ags: response["osm3s"]["timestamp_osm_base"] for ags, response in responses.items()}

    # ---------------------------------------------------------------- Braunschweig zones Ia / Ib (city zone map)
    bs = gpd.read_file(args.ia_ib).to_crs(cc.METRIC_CRS).set_index("zone_id")
    fit = json.load(open(args.affine, encoding="utf-8"))
    rms_px, mpp = fit["history"][-1]["cp_rms_px"], fit["metres_per_px"]
    ax, ay = fit["affine_px"], fit["affine_py"]
    georef = (f"georeferenced by an affine fit of the map to OSM street centrelines (Overpass 2026-09-29; "
              f"{len(fit['cp_residual_px'])} windowed chamfer control points, RMS {rms_px:.1f} px = {rms_px * mpp:.1f} m at "
              f"{mpp:.2f} m/px; px = {ax[0]:.9f} E + {ax[1]:.9f} N + {ax[2]:.3f}, py = {ay[0]:.9f} E + {ay[1]:.9f} N + "
              f"{ay[2]:.3f}, EPSG:25832, 1920x1450 px image; scripts/curation/parking_zones_2026)")
    # Best fit of the annex (2026-09-29, scratch code, not committed because it produced no committed geometry): an
    # affine chamfer fit of this file's 1a/1b outline to the annex zone lines; RMS 11.1 m over the outline samples
    # within 10 px of an annex line, 26.8 m RMS at the five BgA car-park dots as independent check points.
    annex = ("The zone extents are defined by the ParkGO annex map (sec. 2(1); 'Anlage zur ParkGO', published on page 3 "
             "of 2_08_Parkgebuehrenordnung_2025.pdf), which also draws zone II; the annex was not digitised in v1 "
             "because it could not be georeferenced to an RMS of 10 m or better (best affine fit, about 3.7 m per "
             "pixel: 11.1 m RMS between the zone 1a/1b outline of this file and the annex zone lines, 26.8 m RMS at "
             "the five BgA car parks as check points), so this overview map is used instead.")
    add("bs_zone_ia", "03101000", bs.loc["bs_zone_ia", "geometry"], "ordinance_map", ZONE_MAP_URL,
        "Traced from the City of Braunschweig overview map of parking zones 1a/1b (image 20251126_Karte-Parkzone-"
        "Innenstadt-a-b-2025-01, published on parken-in-der-innenstadt.php): the region enclosed by the dotted zone-1a "
        "outline (dark pixels joined by a 4 px dilation, flood-filled from the core, grown back by 4 px), minus the "
        "green zone-1b fill; " + georef + "; simplified 1 m. The outline leaves the map at its lower edge (Buergerpark / "
        "Wolfenbuetteler Strasse); the polygon is closed along that edge, so the part of zone 1a south of the map extent "
        "(drawn on the ParkGO annex) is missing. The BgA car parks of the Entgeltordnung are cut out (own zones). " + annex
        + " The city describes 1a as the area inside the City-Ring plus Grosser Hof, Werder, Wilhelmstrasse and "
        "Nimesstrasse.")
    add("bs_zone_ib", "03101000", bs.loc["bs_zone_ib", "geometry"], "ordinance_map", ZONE_MAP_URL,
        "Traced from the same City of Braunschweig zone map as bs_zone_ia: the green zone-1b fill (pixels with G-R > 12 "
        "and G-B > 40, closed by 3 px, holes filled, specks < 400 px dropped), two parts (Wallring; Loewenwall / "
        "Windmuehlenberg); " + georef + "; simplified 1 m. The city describes 1b as the Wallring without the "
        "Theaterumfahrt and the Steintorwall (both belong to 1a). " + annex)

    # ---------------------------------------------------------------- Braunschweig BgA car parks (Entgeltordnung 2_09)
    ways_by_id = {e["id"]: e for e in responses["03101000"]["elements"] if e["type"] == "way"}

    def osm_union(ids, buffer_m=0.0):
        geometry = unary_union(cc.to_metric([cc.element_geometry(ways_by_id[i]) for i in ids]))
        return geometry.buffer(buffer_m, join_style=2) if buffer_m else geometry

    add("bs_bga_markthalle", "03101000", osm_union([7743381]), "ordinance_map", BGA_URL,
        "BgA car park 'Markthalle': the lot is named and located by the Entgeltordnung B 660 (Anlage 1 map); its outline "
        "is the OSM amenity=parking way 7743381 (name Markthalle, operator Stadt Braunschweig, capacity 70), so the "
        f"geometry is OSM-derived (ODbL). OSM tags the lot fee=no, the ordinance makes it paid. Overpass 2026-09-29 (OSM "
        f"base {base['03101000']}).")
    for zone_id, ids, label, annex_page in (
            ("bs_bga_kannengiesserstrasse", [1208730136, 1208730137], "Kannengiesserstrasse", "Anlage 2"),
            ("bs_bga_an_der_martinikirche", [1125907066, 1125907067], "An der Martinikirche", "Anlage 3"),
            ("bs_bga_jodutenstrasse_klint", [128210174], "Jodutenstrasse/Klint", "Anlage 4"),
            ("bs_bga_suedstrasse", [336899051], "Suedstrasse", "Anlage 5")):
        add(zone_id, "03101000", osm_union(ids, PARKING_BUFFER_M), "osm_fee_tags", BGA_URL,
            f"BgA car park '{label}' (Entgeltordnung B 660, {annex_page} map): the fee=yes OSM amenity=parking way(s) "
            f"{', '.join(str(i) for i in ids)} at the position drawn in {annex_page}, buffered {PARKING_BUFFER_M:.0f} m. "
            f"Overpass 2026-09-29 (OSM base {base['03101000']}).")

    # ---------------------------------------------------------------- Stadthalle concept: zone 132 and its fee islands
    ways = cc.named_ways(responses["03101000"])
    streets = ways[ways["highway"] != ""]
    islands = gpd.read_file(args.fee_islands).to_crs(cc.METRIC_CRS)
    for island in islands.itertuples():
        label = island.streets.replace(", ", "/")
        add(island.zone_id, "03101000", island.geometry, "street_list_buffer", BS_PARKRAUM_URL,
            f"Parkscheininsel {label} of the resident parking concept Stadthalle (named with its tariff on "
            f"parkraummanagement.php): the sections of the OSM way(s) {island.osm_way_ids} ({island.streets}) spanned "
            f"by the dark-blue 'Parkgebuehren' kerb lines of the concept plan 202308-Beschilderung-Bestand-PRMK-Stadthalle "
            f"({island.sections}), buffered {STREET_BUFFER_M:.0f} m and dissolved. Plan "
            f"georeferenced by a similarity fit on {island.cp_count} street junctions (OSM ways, Overpass 2026-09-29): "
            f"{island.metres_per_px:.3f} m/px, rotation {island.rotation_deg:.2f} deg, RMS {island.cp_rms_px:.1f} px = "
            f"{island.cp_rms_m:.1f} m; the blue pixels within 12 m of the named ways are projected onto them "
            f"(scripts/curation/parking_zones_2026/digitise_stadthalle_fee_islands.py). Cut out of "
            f"bs_resident_stadthalle_132 where they overlap. OSM base {base['03101000']}.")
    selected = streets[streets["name"].isin(STADTHALLE_STREETS) & streets.intersects(STADTHALLE_WINDOW)]
    missing = sorted(set(STADTHALLE_STREETS) - set(selected["name"]))
    if missing:
        raise SystemExit(f"Stadthalle streets missing in OSM: {missing}")
    stadthalle = cc.closed_union([g.intersection(STADTHALLE_WINDOW).buffer(STREET_BUFFER_M) for g in selected.geometry], 60.0)
    add("bs_resident_stadthalle_132", "03101000", stadthalle, "street_list_buffer", BS_PARKRAUM_URL,
        "Resident parking concept 'Stadthalle' (Bewohnerparkzone 132 on the city's Helmstedter Strasse plan): the streets "
        "marked 'Bewohnerparken Mischprinzip' on the concept plan 202308-Beschilderung-Bestand-PRMK-Stadthalle "
        "(Adolfstrasse, Bertramstrasse, Koernerstrasse, Marthastrasse, Gerstaeckerstrasse, Kleine Campestrasse, "
        "Lachmannstrasse, Villierstrasse, Kleine Leonhardstrasse) as OSM ways clipped to E 604550-605250 / N 5790650-"
        f"5791350 (EPSG:25832), buffered {STREET_BUFFER_M:.0f} m, dissolved, closed by 60 m, enclosed blocks filled; "
        "the fee islands Marthastrasse/Koernerstrasse and Gerstaeckerstrasse/Kleine Campestrasse are cut out (own "
        "zones). Street names read from the plan and matched to OSM by name (Overpass 2026-09-29, OSM base "
        f"{base['03101000']}); the main roads with unrestricted or short-term parking (Helmstedter Strasse, "
        "Leonhardstrasse, St. Leonhard) are not part of the zone.")

    # ---------------------------------------------------------------- TU Braunschweig campus areas (OSM outlines)
    for zone_id, ids, buffer_m, label, what in (
            ("tu_zentralcampus", [7972781, 27059288, 27092139], 0.0, "Zentralcampus",
             "OSM amenity=university ways 7972781 and 27059288 (both 'Technische Universitaet Braunschweig - "
             "Zentralbereich') and 27092139 ('Mensa 1')"),
            ("tu_campus_nord", [8011320], 0.0, "Campus Nord",
             "OSM amenity=university way 8011320 ('TU Braunschweig - Campus Nord')"),
            ("tu_campus_ost_beethovenstrasse", [8013640], 0.0, "Campus Ost Beethovenstrasse",
             "OSM amenity=university way 8013640 ('TU Braunschweig - Campus Ost')"),
            ("tu_campus_ost_langer_kamp", [8011319, 23967822], 0.0, "Campus Ost Langer Kamp",
             "OSM amenity=university ways 8011319 ('TU Braunschweig - Campus Ost - Langer Kamp') and 23967822 ('TU "
             "Braunschweig', the block south of Hans-Sommer-Strasse)"),
            ("tu_international_house", [134220301, 172658383], PARKING_BUFFER_M, "car parks at the International House",
             "the two TU-operated OSM amenity=parking ways 134220301 and 172658383 at Bueltenweg (International House, "
             "Bueltenweg 74, per its TU page), matching the two small detection zones west of Brucknerstrasse on the GB3 "
             f"map 'Campus Ost Langer Kamp', buffered {PARKING_BUFFER_M:.0f} m"),
            ("tu_forschungsflughafen", [264963238], 0.0, "Campus Forschungsflughafen",
             "OSM amenity=university way 264963238 ('TU Braunschweig - Campus Forschungsflughafen')")):
        add(zone_id, "03101000", osm_union(ids, buffer_m), "centre_approximation", TU_URL,
            f"TU Braunschweig {label}: {what}; Overpass 2026-09-29 (OSM base {base['03101000']}). The outline "
            "approximates the campus whose TU car parks are ticketed (the GB3 Parkbereiche maps show the camera "
            "detection zones inside it); it is not the detection-zone boundary itself.")

    # ---------------------------------------------------------------- Wolfsburg Innenstadt (OSM fee tags)
    wob_window = box(620900, 5808820, 622250, 5810290)
    wob = cc.paid_objects(responses["03103000"], wob_window)
    buffered = [g.buffer(STREET_BUFFER_M if kind == "street" else PARKING_BUFFER_M) for _, kind, g, _ in wob]
    add("wob_innenstadt", "03103000", cc.largest_parts(cc.closed_union(buffered, 120.0).intersection(wob_window), 5000.0),
        "osm_fee_tags", "https://www.wolfsburg.de/mobilitaetverkehr/parken",
        f"OSM fee evidence in the Wolfsburg city centre (the Parkleitsystem areas Norden, Mitte and Sueden of "
        f"wolfsburg.de): {len(wob)} objects (ticket/fee on-street ways buffered 15 m, fee=yes car parks buffered 5 m) "
        "inside the window E 620900-622250 / N 5808820-5810290 (EPSG:25832; Mittellandkanal/railway to Siemensstrasse), "
        "dissolved, closed by 120 m, enclosed blocks filled, parts < 5000 m2 dropped; Overpass 2026-09-29 (OSM base "
        f"{base['03103000']}). The Autostadt car parks north of the canal are outside the window (coverage register).")

    # ---------------------------------------------------------------- Salzgitter-Lebenstedt (street list of the city page)
    sz_window = box(590300, 5778780, 591450, 5779320)
    sz_names = ["Joachim-Campe-Straße", "Albert-Schweitzer-Straße", "Berliner Straße", "Marienbruchstraße",
                "Chemnitzer Straße", "Konrad-Adenauer-Straße"]
    sz_elements = responses["03102000"]["elements"]
    sz_geometries = cc.to_metric([cc.element_geometry(e) for e in sz_elements])
    sz_streets = [g.intersection(sz_window) for e, g in zip(sz_elements, sz_geometries)
                  if g is not None and e["type"] == "way" and e.get("tags", {}).get("name") in sz_names
                  and g.intersects(sz_window)]
    sz_lots = [g for _, _, g, _ in cc.paid_objects(responses["03102000"], sz_window)]
    add("sz_lebenstedt", "03102000", unary_union([g for g in sz_streets if not g.is_empty] + sz_lots).convex_hull.buffer(25.0),
        "street_list_buffer", "https://www.salzgitter.de/rathaus/fachdienste/tiefbau/parken.php",
        "Salzgitter-Lebenstedt centre: the streets with ticket machines named on the city page (Rathausvorplatz at "
        "Joachim-Campe-Strasse, Albert-Schweitzer-Strasse, Berliner Strasse, Marienbruchstrasse, Chemnitzer Strasse, car "
        "park at Konrad-Adenauer-Strasse) as OSM ways clipped to the centre window E 590300-591450 / N 5778780-5779320 "
        f"(EPSG:25832), plus the {len(sz_lots)} fee=yes car parks in that window; convex hull, buffered 25 m. ASSUMPTION: "
        "the page names the machine streets, not their extent; the window limits them to the Lebenstedt centre. Overpass "
        f"2026-09-29 (OSM base {base['03102000']}).")

    # ---------------------------------------------------------------- Peine and Helmstedt (hull of fee-tagged car parks)
    for zone_id, ags, anchor_name, anchor_ascii, radius_m, url, extra, extra_ascii in (
            ("pe_innenstadt", "03157006", "Breite Straße", "Breite Strasse", 400.0,
             "https://www.peine.de/de/rathaus/stadtportraet/verkehr-und-parken/parkplaetze/parkplatz-luisenstrasse.php",
             ["Luisenstraße"], "Luisenstrasse"),
            ("he_innenstadt", "03154028", "Markt", "Markt", 400.0,
             "https://www.stadt-helmstedt.de/fileadmin/user_upload/01_Rathaus/Virtuelle_Verwaltung/Ortsrecht_neu/"
             "Gebuehrenordnung_Parkscheinautomaten.pdf", [], "")):
        elements = responses[ags]["elements"]
        geoms = cc.to_metric([cc.element_geometry(e) for e in elements])
        anchor = unary_union([g for e, g in zip(elements, geoms) if g is not None and e["type"] == "way"
                              and e.get("tags", {}).get("name") == anchor_name
                              and e.get("tags", {}).get("highway") in ("pedestrian", "living_street", "residential")])
        centre = anchor.centroid
        near = [g for _, _, g, _ in cc.paid_objects(responses[ags], centre.buffer(radius_m))]
        extra_streets = [g for e, g in zip(elements, geoms) if g is not None and e["type"] == "way"
                         and e.get("tags", {}).get("name") in extra and g.distance(centre) < radius_m]
        add(zone_id, ags, unary_union(near + extra_streets).convex_hull.buffer(30.0), "osm_fee_tags", url,
            f"Town centre enclosed by its fee-tagged car parks: convex hull of the {len(near)} OSM fee=yes objects within "
            f"{radius_m:.0f} m of the centroid of the '{anchor_ascii}' way(s) (E {centre.x:.0f} / N {centre.y:.0f}, "
            "EPSG:25832)" + (f" and the street {extra_ascii}" if extra else "") + ", buffered 30 m; Overpass 2026-09-29 "
            f"(OSM base {base[ags]}). ASSUMPTION: destinations inside the ring of paid car parks park at them.")

    # ---------------------------------------------------------------- Goslar, Wolfenbuettel, Gifhorn (Overpass failed)
    links = cc.load_network_links(args.network)
    for zone_id, ags, window, names, url, what in (
            ("gs_altstadt_zone1", "03153017", box(597300, 5750600, 599400, 5752500),
             ["Rosentorstraße", "Petersilienstraße", "Bäckerstraße", "Marktstraße", "Hoher Weg", "Breite Straße",
              "Kornstraße"],
             "https://www.goslar.de/fileadmin/media-goslar/stadt/ortsrecht-satzungen/oeffentliche_sicherheit-ordnung/"
             "20250624_gebuehrenordnung_ueber_das_parken_an_parkscheinautomaten_in_der_stadt_goslar_vom_24.06.2025.pdf",
             "Goslar Altstadt, parking zone 1 ('the inner-city area with the central shopping and business streets' in the "
             "city's announcement of the 2026 fees, reported by regionalheute.de on 2025-12-30): convex hull of the "
             "car-network ways named Rosentorstrasse, Petersilienstrasse, Baeckerstrasse, Marktstrasse, Hoher Weg, Breite "
             "Strasse, Kornstrasse. ASSUMPTION: these old-town shopping streets stand for zone 1; the city publishes no "
             "street list"),
            ("wf_innenstadt", "03158037", box(604100, 5779600, 605700, 5781200),
             ["Rosenwall", "Schulwall", "Harztorwall", "Stadtmarkt", "Kornmarkt", "Holzmarkt", "Kommißstraße",
              "Breite Herzogstraße", "Lange Straße"],
             "https://www.stadtbetriebe-wf.de/parkhaeuser/rosenwall.html",
             "Wolfenbuettel Innenstadt between the old wall streets (Rosenwall with the garage Rosenwall, Schulwall with the "
             "garage Schulwall at the Loewentor, Harztorwall) and the central squares (Stadtmarkt, Kornmarkt, Holzmarkt, "
             "Kommissstrasse, Breite Herzogstrasse, Lange Strasse): convex hull of these car-network ways"),
            ("gf_innenstadt", "03151009", box(604300, 5815000, 605600, 5817000),
             ["Steinweg", "Schillerplatz", "Hindenburgstraße", "Torstraße", "Cardenap"],
             "https://psg-gifhorn.de/parken",
             "Gifhorn centre around the pedestrian zone Steinweg (Schillerplatz, Torstrasse) with the municipal car parks "
             "Hindenburgstrasse and Schottische Muehle (access Cardenap): convex hull of these car-network ways")):
        replacement = erosion_replacement(zone_id, ags, url, what, erosion)
        if replacement is not None:
            zones.append(replacement)
            continue
        x0, y0, x1, y1 = window.bounds
        sub = links.cx[x0:x1, y0:y1]
        chosen = sub[sub["name"].isin(names)]
        missing = sorted(set(names) - set(chosen["name"]))
        if missing:
            raise SystemExit(f"{zone_id}: streets missing in the network: {missing}")
        add(zone_id, ags, chosen.geometry.union_all().convex_hull.buffer(40.0), "centre_approximation", url,
            what + ", buffered 40 m. Street geometry from the car network of the local MATSim scenario (eqasim-data/"
            "output_bs, 2026-04-29; OSM-derived links with osm:way:name) because the Overpass request of 2026-09-29 for "
            "this municipality failed with HTTP 504 and was not repeated (at most one request per municipality).")

    # ---------------------------------------------------------------- precedence, Braunschweig pieces (A2), output
    zones, trims = apply_precedence(zones)
    print("trimmed by precedence:", trims)
    pieces = []
    bs_core = accepted_core(erosion.get(BS_AGS))
    if bs_core is not None:
        records, pieces = assign_braunschweig_pieces(bs_core, zones, annex_zones, erosion[BS_AGS]["qa"])
        zones += records
        print("Braunschweig core pieces:", piece_assignment_text(
            pieces, float(erosion[BS_AGS]["qa"]["parameters"]["minimum_island_m2"])))
    with open(args.municipalities, "rb") as stream:
        municipalities = pickle.load(stream)
    ars = municipalities["commune_id"].astype(str)
    municipalities["ags"] = ars.str[:5] + ars.str[-3:]
    municipalities = municipalities.set_index("ags").to_crs(cc.METRIC_CRS)
    for zone in zones:
        inside = zone["geometry"].intersection(municipalities.loc[zone["_ags"], "geometry"]).area / zone["geometry"].area
        print(f"{zone['zone_id']:58s} {zone['geometry_source']:22s} {zone['geometry'].area:10.0f} m2  inside "
              f"{zone['_ags']}: {inside:.4f}")
        if inside < MINIMUM_INSIDE_SHARE:
            raise SystemExit(f"{zone['zone_id']} lies only {inside:.3f} inside its municipality {zone['_ags']}")
        zone["digitising_note"].encode("ascii")
    frame = zone_frame(zones)
    out = Path(args.out)
    write_zone_file(frame, out)
    print(f"written {out} with {len(frame)} zones")
    if args.erosion_dir:
        write_qa_table(args.qa_out, qa_table_rows(erosion, zones, pieces, counterfactuals))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
