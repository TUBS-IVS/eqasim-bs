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

Parking cost zones v2, spec Amendment B (majority rule over the parking supply, issue #436; ``--supply-share-dir``):
the per-town outputs of ``scripts/build_parking_zones_from_osm.py --supply-share`` of arm B, the default parameters of
owner decision 2 (``supply_share.DEFAULT_ARM``: share 0.3, a POST HOC change of the pre-registered 0.5, full inventory;
``load_supply_inputs`` refuses other parameters, variants and counterfactuals). The application gate of owner
decision 2 is re-applied (``supply_gate``): H1, B5 recomputed at the default parameters on the Braunschweig metrics
(``supply_b5``), and H2, the pre-registered holdout check pooled from the holdout overlaps of the town QA files
(``supply_h2``); only when both pass (B6) do the rule polygons enter as
``osm_supply_majority`` polygons (provenance ``supply_walk_m``, ``paid_share_threshold``, ``minimum_usable_spaces``,
``osm_timestamp``): Goslar, Wolfenbuettel and Gifhorn replace their centre approximation (``supply_replacement``;
tariff rows unchanged), the Braunschweig pieces left after the v1 zones are assigned whole to the annex zone they
overlap most (``assign_braunschweig_pieces``, ruling R-T1-f: ``bs_zone_ia_sued``, ``bs_zone_ii``), the other towns are
QA only. Written in every case: the QA table ``--supply-qa-out`` (``supply_qa_rows``, every column defined in its
header, ``SUPPLY_QA_COLUMN_GLOSSARY``; H1, H2, the payment evidence and the arms: the Amendment B arms and the
information arms S, T and S+T of owner decisions 2 and 3 with their H1 and H2, never applied) and the B7 release of the
classified cells ``--paid-share-out`` (``write_paid_share_release``, gzip CSV, EPSG:25832). When H1 or H2 fails
nothing is applied and the zone file stays the v1 file byte for byte. ``--supply-counterfactual-qa`` cites POST HOC
counterfactual runs (``--counterfactual`` of the builder) in the note of their town, next to the interpretation they
vary; they are never applied.

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
        [--supply-share-dir eqasim-data/data/braunschweig/parking/raw_osm/derived_supply_share \
         --reference-outline eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_zones_georeferenced.geojson \
         --supply-qa-out eqasim-data/data/braunschweig/parking/parking_zones_2026_supply_share_qa.csv \
         --paid-share-out eqasim-data/data/braunschweig/parking/parking_paid_share_2026.csv.gz]
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
from braunschweig.parking import supply_share as ss  # noqa: E402
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
#: Rule-based geometry sources (lever 1 erosion, Amendment B supply majority): simplified before and cut with clearance.
RULE_GEOMETRY_SOURCES = tuple(pz.RULE_PROVENANCE_COLUMNS)
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
                source_date=DIGITISED_ON, digitised_on=DIGITISED_ON, minimum_part_m2=MINIMUM_PART_M2,
                supply_walk_m=None, paid_share_threshold=None, minimum_usable_spaces=None) -> dict:
    """One zone of the release as the assembly handles it (keys starting with '_' are not written; the provenance
    keys of a rule-based source, ``pz.RULE_PROVENANCE_COLUMNS``, only appear in the file when such a zone exists)."""
    return {"zone_id": zone_id, "_ags": ags, "geometry": geometry, "geometry_source": geometry_source,
            "source_url": source_url, "source_date": source_date, "digitised_on": digitised_on, "digitising_note": note,
            "unavoidable_walk_m": walk_m, "osm_timestamp": osm_timestamp, "supply_walk_m": supply_walk_m,
            "paid_share_threshold": paid_share_threshold, "minimum_usable_spaces": minimum_usable_spaces,
            "_minimum_part_m2": minimum_part_m2}


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

    v1 zones: overlaps above 0.5 m2 are cut, parts below 20 m2 dropped, then simplified by ``SIMPLIFY_M``. A
    rule-based zone (``RULE_GEOMETRY_SOURCES``) is simplified BEFORE the cut and always cut against its neighbours grown by
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
        rule_zone = zone["geometry_source"] in RULE_GEOMETRY_SOURCES
        if rule_zone:
            geometry = geometry.simplify(SIMPLIFY_M, preserve_topology=True).buffer(0)
        if taken is not None and rule_zone:
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
        if not rule_zone:
            geometry = geometry.simplify(SIMPLIFY_M, preserve_topology=True)
        zone["geometry"] = geometry
        if not geometry.is_empty:
            taken = geometry if taken is None else taken.union(geometry)
    emptied = [z["zone_id"] for z in zones if z["geometry"].is_empty]
    if [zone_id for zone_id in emptied if by_id[zone_id]["geometry_source"] not in RULE_GEOMETRY_SOURCES]:
        raise SystemExit(f"zones emptied by the precedence cuts: {emptied}")
    if emptied:
        print("rule-based zones without a part of the minimum island size after the cuts (not written):", emptied)
    return [z for z in zones if not z["geometry"].is_empty], trims


def _polygon_parts(geometry) -> list:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for member in getattr(geometry, "geoms", []) for part in _polygon_parts(member)]


def assign_braunschweig_pieces(core, zones: list, annex_zones: gpd.GeoDataFrame, qa: dict, *, zone_factory=None,
                               noun: str = "core") -> tuple:
    """Braunschweig core minus every zone of the release -> connected pieces, each ASSIGNED WHOLE (ruling R-T1-f).

    The core is simplified by ``SIMPLIFY_M`` before the cut and cut against the zones grown by
    ``EROSION_CUT_CLEARANCE_M``, so the pieces never overlap a zone, also after the rounding of the file. A piece below the
    core's minimum island size is dropped (Q2 after the cut); every other piece goes to the annex zone it overlaps
    most (``BS_EROSION_ZONES``: Ia -> bs_zone_ia_sued, II -> bs_zone_ii) with the share of its area outside that
    outline reported; a piece whose largest overlap is zone Ib (which keeps its v1 polygon) or that overlaps no annex
    zone is not zoned. ``zone_factory`` makes the zone records (default ``erosion_zone``; ``supply_zone`` for the
    majority rule, whose geometry ``noun`` names in the note). Returns (zone records, one report dict per piece).
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
        records.append((zone_factory or erosion_zone)(
                                    zone_id, BS_AGS, unary_union(parts), PARKGO_URL, qa,
                                    replaces=("New in v2: unzoned in v1, where the annex could not be georeferenced to "
                                              "an RMS of 10 m or better."),
                                    assignment=(f"Tariff zone {label} of the ParkGO (sec. 1(2), sec. 2(1)): the pieces of "
                                                f"the {noun} left after every v1 zone (Ia and Ib of the city's overview "
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
    """The zones as the committed frame: provenance columns, plus the provenance columns of every rule-based source
    that has a zone (``pz.RULE_PROVENANCE_COLUMNS``; so a v1-only release keeps its v1 layout)."""
    frame = gpd.GeoDataFrame([{k: v for k, v in z.items() if not k.startswith("_")} for z in zones],
                             geometry="geometry", crs=cc.METRIC_CRS)
    columns = ["zone_id", "geometry_source", "source_url", "source_date", "digitised_on", "digitising_note"]
    present = set(frame["geometry_source"])
    columns += list(dict.fromkeys(column for source, listed in pz.RULE_PROVENANCE_COLUMNS.items() if source in present
                                  for column in listed))
    return frame[columns + ["geometry"]]


def write_zone_file(frame: gpd.GeoDataFrame, path) -> None:
    """WGS84 GeoJSON with the licence and attribution members (the v2 wording when a rule-based zone exists)."""
    sources = set(frame["geometry_source"])
    if pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE in sources:
        license_text, attribution = LICENSE_SUPPLY, ATTRIBUTION_SUPPLY
    elif pz.EROSION_GEOMETRY_SOURCE in sources:
        license_text, attribution = LICENSE_V2, ATTRIBUTION_V2
    else:
        license_text, attribution = LICENSE, ATTRIBUTION
    # RFC 7946 allows foreign members; GDAL writes them at the top level and GeoJSON readers ignore them.
    members = json.dumps({"license": license_text, "attribution": attribution})
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


# ---------------------------------------------------------------- parking cost zones v2, Amendment B (issue #436)
SUPPLY_DIGITISED_ON = "2026-09-30"
LICENSE_SUPPLY = ("ODbL-1.0: every polygon is derived from OpenStreetMap data (OSM outlines, OSM streets, the OSM "
                  "parking tags of the Geofabrik extract behind the osm_supply_majority zones and of the Overpass "
                  "responses behind any osm_fee_erosion zone, or a georeference on OSM street centrelines); Open "
                  "Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/")
ATTRIBUTION_SUPPLY = ("(c) OpenStreetMap contributors (https://www.openstreetmap.org/copyright). Zone boundaries after "
                      "the parking maps and ordinances of the City of Braunschweig (Abt. Geoinformation; the ParkGO "
                      "annex map assigns the osm_supply_majority zones bs_zone_ia_sued and bs_zone_ii), the TU "
                      "Braunschweig GB3 parking pages and the municipal parking pages of Wolfsburg, Salzgitter, Peine, "
                      "Goslar, Wolfenbuettel, Gifhorn and Helmstedt (source_url per feature).")
SUPPLY_QA_INTRO = (
    "Curation QA of the majority rule over the parking supply (parking cost zones v2, spec Amendment B, issue #436):",
    "one row per curated town, written by scripts/curation/parking_zones_2026/assemble_parking_zones.py",
    "--supply-share-dir from the <ags>_supply_qa_<tag>.json files of scripts/build_parking_zones_from_osm.py",
    "--supply-share (the pinned Geofabrik extract and the derived files stay local under raw_osm/, gitignored).",
    "Rule (braunschweig/parking/supply_share.py): every public parking element inside the town's query box (street",
    "sides, street-side areas, off-street lots and garages) is paid, restricted, free or excluded (B1: B-a street",
    "parking without a fee tag is free, B-b disc parking is free, B-c off-street lots without an explicit fee tag are",
    "excluded); its capacity is the capacity tag, else 5.5 m per street space, 12.5 m2 per street-side area space,",
    "25 m2 per lot space times the levels of a multi-storey car park (B-d); a 25 m cell is classified when at least",
    "minimum_usable_spaces usable spaces lie within walk_m of its centre (B-e) and paid when (paid + restricted) /",
    "usable >= share_threshold (B-f); the paid cells are united, smoothed +/-smoothing_m and parts below",
    "minimum_island_m2 dropped. B5 (pre-registered with the share 0.5): in Braunschweig recall (rule inside the",
    "ordinance polygons Ia and Ib / their area) and precision (rule inside the annex zones / rule inside the annex map",
    "frame) must both reach 0.70; it failed with 0.5 (Task 1b). Owner decision 2 (2026-09-30, a POST HOC change of",
    "B-f): the share threshold of this table is 0.3; the 0.70 and 0.50 bounds are set values, not laws. The rule is",
    "applied in any town (B6) only when H1 (B5 recomputed with this table's parameters, the b5_* columns) and H2 pass.",
    "H2 (the holdout check, pre-registered before any holdout overlap at 0.3 was computed): references not used by B5",
    "and no approximations (sz_lebenstedt, wob_innenstadt, pe_innenstadt, he_innenstadt, the three Braunschweig",
    "Parkscheininseln and bs_resident_stadthalle_132); recall per town = rule inside the references / their area,",
    "precision per town (the four towns only) = rule inside the references / rule inside the town's query box; pass",
    "when the pooled recall (area-weighted over every reference) and the pooled precision reach 0.70 and every town's",
    "recall reaches 0.50 (ASSUMPTION Q6). The arms (the Amendment B arms around its share 0.5 and the information arms",
    "S = street supply only and T = payment evidence of owner decision 3 within 75 m, ASSUMPTION T-a: ticket machines",
    "and parking elements with an app-payment tag, never phone wallets or non-parking elements, ruling R-T1c-a) are",
    "computed with H1 and H2 and never applied: seven arms of owner decisions 2 and 3 (B and six information arms) plus",
    "four Amendment B arms, so a passing information arm is no validation of that arm.",
    "Element counts are street sides (two per way), street-side areas and lots inside the query box; spaces are",
    "usable capacity. Interpretations beyond B-a..B-f (implementation choices, not owner rulings) are listed in the data",
    "record parking_zones_2026; the one that decided B5 at the share 0.5 (parking:<side>=yes read as street parking,",
    "literal B-a) is stated with a POST HOC counterfactual in the Braunschweig note.",
    "Units m, m2, spaces; empty = undefined. scripts/validate_parking_zones.py re-applies H1, H2 and the default",
    "parameters. Counts and areas are derived from OpenStreetMap data: (c) OpenStreetMap contributors, ODbL 1.0",
    "(https://www.openstreetmap.org/copyright). Columns:",
)
#: One definition per column of ``supply_share.SUPPLY_SHARE_QA_COLUMNS``.
SUPPLY_QA_COLUMN_GLOSSARY = {
    "ags": "8-digit AGS of the curated town",
    "name": "municipality name (BA Gemeindeband, ASCII)",
    "role": "zones_from_rule = the rule may become release polygons after H1 and H2; qa_only = recorded, the v1 polygon "
            "stays",
    "osm_extract": "the Geofabrik extract the supply was read from (raw_osm/, local, gitignored)",
    "osm_extract_md5": "its MD5, checked against the Geofabrik MD5 file before use",
    "osm_timestamp": "OSM snapshot of the extract (osmosis_replication_timestamp of the PBF header, UTC)",
    "walk_m": "walk distance W in m (pre-registered 250)",
    "share_threshold": "paid share from which a classified cell is paid (ASSUMPTION B-f: 0.3 by owner decision 2, a "
                       "POST HOC change of the pre-registered 0.5)",
    "minimum_usable_spaces": "usable spaces within W from which a cell is classified (ASSUMPTION B-e, pre-registered 50)",
    "cell_m": "raster cell size in m (pre-registered 25)",
    "smoothing_m": "buffer +/- of the smoothing of the paid cells in m (pre-registered 12.5)",
    "minimum_island_m2": "smallest rule part that is kept, m2 (ASSUMPTION Q2, pre-registered 10000)",
    "street_ways": "highway ways inside the query box (kerbside streets and ways with a parking:* key)",
    "street_side_areas": "separately mapped street-side parking areas inside the query box (amenity=parking, parking="
                         "street_side, lane, on_kerb, half_on_kerb, shoulder or layby)",
    "offstreet_lots": "off-street car parks and garages inside the query box (the other amenity=parking objects)",
    "paid_elements": "elements of class paid (street sides, street-side areas and lots)",
    "restricted_elements": "elements of class restricted (resident permit)",
    "free_elements": "elements of class free (B-a, B-b, explicit fee=no)",
    "excluded_elements": "elements of class excluded (not public, forbidden, mapped separately, B-c, no parking "
                         "information)",
    "overpass_response": "the saved Overpass regulation response of Task 1 (raw_overpass/, read only) of the cross-check",
    "overpass_osm_timestamp": "its OSM snapshot (timestamp_osm_base, UTC)",
    "overpass_paid_elements": "paid elements of the same classification applied to the Overpass response",
    "overpass_restricted_elements": "restricted elements of the Overpass response",
    "overpass_free_elements": "free elements of the Overpass response",
    "overpass_excluded_elements": "excluded elements of the Overpass response",
    "cross_check": "per kind and class the extract count / the Overpass count (difference), and the two snapshots",
    "usable_spaces": "usable capacity (paid + restricted + free) inside the query box, spaces",
    "paid_spaces": "paid capacity inside the query box, spaces",
    "restricted_spaces": "restricted capacity inside the query box, spaces",
    "free_spaces": "free capacity inside the query box, spaces",
    "tagged_capacity_share": "share of the usable capacity from capacity tags (primary)",
    "heuristic_capacity_share": "share of the usable capacity from the heuristic B-d (fallback)",
    "free_street_spaces_without_fee_tag_share": "share of the free street capacity (street sides and street-side "
                                                "areas) that rests on a missing fee tag (ASSUMPTION B-a)",
    "offstreet_lots_without_fee_tag": "off-street lots excluded because they carry no explicit fee tag (ASSUMPTION B-c)",
    "offstreet_spaces_without_fee_tag_share": "share of the public off-street capacity (usable lots plus those excluded "
                                              "for their fee tag) that is excluded for lack of a fee tag (ASSUMPTION "
                                              "B-c)",
    "ticket_machines": "variant T evidence (from the arm T at the default share): parking ticket machines inside the "
                       "query box (nodes with amenity=vending_machine and vending=parking_tickets)",
    "app_payment_elements": "variant T evidence (ruling R-T1c-a): parking elements inside the query box (parking "
                            "facilities, street ways with parking:* tags, parking payment devices) with an app-payment "
                            "key (payment:app*, payment:mobile* or a named parking-app key, value not no; phone "
                            "wallets and non-parking elements never count)",
    "payment_evidence_paid_elements": "variant T at the default share (information only): street sides, street-side "
                                      "areas and lots inside the query box that the payment evidence turns paid",
    "payment_evidence_paid_spaces": "their capacity, spaces",
    "cells": "25 m cells over the query box (its EPSG:25832 bounds)",
    "classified_cells": "cells with at least minimum_usable_spaces usable spaces within W",
    "classified_cell_share": "classified_cells / cells (the rest is unclassified)",
    "paid_cells": "classified cells with paid_share >= share_threshold",
    "rule_area_m2": "area of the rule polygons (paid cells united, smoothed, islands dropped), m2",
    "rule_parts": "number of rule polygons",
    "b5_recall": "Braunschweig, H1: area(rule inside the ordinance polygons Ia and Ib) / area(Ia and Ib), B5 with this "
                 "table's parameters",
    "b5_precision": "Braunschweig, H1: area(rule inside the annex zones Ia, Ib and II) / area(rule inside the annex map "
                    "frame)",
    "b5_passed": "Braunschweig: true when H1 recall and precision are both >= 0.70 (ASSUMPTION Q5, a set value)",
    "h2_pooled_recall": "Braunschweig row, H2: sum of the rule inside the holdout references / sum of their areas over "
                        "every holdout town (area-weighted)",
    "h2_pooled_precision": "Braunschweig row, H2: sum of the rule inside the references / sum of the rule inside the "
                           "query box over the four towns with a precision frame",
    "h2_minimum_town_recall": "Braunschweig row, H2: the smallest holdout_recall of the holdout towns",
    "h2_passed": "Braunschweig row: true when both pooled values are >= 0.70 and every town's recall >= 0.50 "
                 "(ASSUMPTION Q6); B6 applies in any town only when b5_passed and h2_passed are both true",
    "holdout_references": "holdout towns: the reference polygons of H2 in the zone release (';'-separated)",
    "holdout_reference_area_m2": "their area (union), m2",
    "holdout_rule_inside_reference_m2": "area of the rule inside them, m2",
    "holdout_rule_inside_query_box_m2": "the four precision towns: area of the rule inside the town's query box, m2 "
                                        "(Braunschweig: recall only)",
    "holdout_recall": "holdout_rule_inside_reference_m2 / holdout_reference_area_m2",
    "holdout_precision": "the four precision towns: holdout_rule_inside_reference_m2 / holdout_rule_inside_query_box_m2",
    "reference": "outline the rule is compared with (Braunschweig: the georeferenced ParkGO annex; elsewhere the v1 "
                 "polygon)",
    "rule_share_inside_reference": "area of the rule inside the reference / area of the rule",
    "reference_share_covered_by_rule": "area of the rule inside the reference / area of the reference",
    "largest_outline_distance_m": "Hausdorff distance between the outlines of the rule and the reference, m",
    "sensitivity": "Amendment B arms around its pre-registered share 0.5 (share 0.5, W 150 m, W 400 m, share 0.7): rule "
                   "area, H1 in Braunschweig, the holdout overlaps in the holdout towns and the pooled H2 on the "
                   "Braunschweig row (information only, never used to select a passing combination)",
    "variant_arms": "information arms of owner decisions 2 and 3 (S = street supply only, T = payment evidence within "
                    "75 m after ruling R-T1c-a, S+T; each at 0.3 and 0.5), reported like the sensitivity column plus "
                    "the elements T turns paid; never applied",
    "applied": "true when the rule became release polygons (geometry_source osm_supply_majority)",
    "zone_ids": "the osm_supply_majority polygons of an applied row (';'-separated)",
    "decision": "applied; gate_failed (H1 or H2 failed, nothing is applied anywhere); no_rule_polygon (both passed, "
                "nothing to apply); qa_only",
    "note": "H1, H2, the payment evidence, the application and the decision in words",
}
RELEASE_INTRO = (
    "Paid-parking share of the public parking supply per 25 m cell (parking cost zones v2, spec Amendment B7, issue",
    "#436): the classified cells of the eight curated towns, the preparation of a probabilistic variant C (a paid",
    "probability per stay); no pipeline stage reads this file. Written by",
    "scripts/curation/parking_zones_2026/assemble_parking_zones.py --supply-share-dir from the rasters",
    "<ags>_paid_share_<tag>.csv.gz of scripts/build_parking_zones_from_osm.py --supply-share",
    "(braunschweig.parking.supply_share.paid_share_raster). CRS EPSG:25832: x_m and y_m are cell centres in metres on",
    "the lattice of multiples of the cell size. A cell is classified when at least minimum_usable_spaces usable",
    "spaces (paid, restricted or free public parking; capacity tag, else the heuristic B-d) lie within W of its",
    "centre; unclassified cells are not listed. Assumptions of spec Amendment B: B-a street parking without a fee tag",
    "is free, B-b disc parking is free, B-c off-street lots without an explicit fee tag are excluded, B-d the capacity",
    "heuristics, B-e the minimum supply. Derived from OpenStreetMap data: (c) OpenStreetMap contributors, ODbL 1.0",
    "(https://www.openstreetmap.org/copyright).",
)
RELEASE_COLUMN_GLOSSARY = {
    "x_m": "cell centre, EPSG:25832 easting in metres",
    "y_m": "cell centre, EPSG:25832 northing in metres",
    "municipality_ags": "8-digit AGS of the curated town whose query box the cell covers",
    "paid_share": "(paid + restricted spaces) / usable spaces within W of the cell centre, 0 to 1",
    "usable_spaces": "paid + restricted + free public spaces within W of the cell centre (at least the minimum supply)",
    "heuristic_capacity_share": "share of the usable spaces whose capacity comes from the heuristic B-d (no capacity "
                                "tag), 0 to 1",
}
#: The arms reported next to B (information only): the Amendment B arms (column sensitivity) and the information arms
#: of owner decisions 2 and 3 (column variant_arms).
REPORTED_ARMS = tuple(ss.AMENDMENT_B_ARMS) + tuple(ss.VARIANT_ARMS)
#: The arm whose payment-evidence conversions the QA table reports (T at the default share).
PAYMENT_EVIDENCE_ARM = ss.SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, ss.PAYMENT_EVIDENCE)


def load_supply_inputs(directory, municipalities=None, arm=ss.DEFAULT_ARM) -> dict:
    """ags -> {"qa", "rule" (EPSG:25832), "raster", "arms" (tag -> QA of the ``REPORTED_ARMS`` present)}.

    Reads ``<ags>_supply_qa_<tag>.json``, ``<ags>_supply_zones_<tag>.geojson`` and ``<ags>_paid_share_<tag>.csv.gz``
    of the tag of ``arm`` (default B: the parameters of owner decision 2 on the full inventory) and refuses
    (``SystemExit``) a missing town, a QA file whose parameters are not the default ones, a variant or a counterfactual
    (never release inputs) and towns read from different extracts or snapshots.
    """
    directory = Path(directory)
    municipalities = tuple(municipalities or tuple(EROSION_ZONES_FROM_CORE) + tuple(EROSION_QA_ONLY))
    tag = arm.tag()
    expected = arm.parameters.as_dict()
    supply = {}
    for ags in municipalities:
        qa_path = directory / f"{ags}_supply_qa_{tag}.json"
        if not qa_path.is_file():
            raise SystemExit(f"{ags}: {qa_path.name} missing in {directory}; run scripts/build_parking_zones_from_osm.py "
                             "--supply-share for it first")
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        if qa["ags"] != ags:
            raise SystemExit(f"{qa_path} belongs to {qa['ags']}, not {ags}")
        if qa.get("counterfactual"):
            raise SystemExit(f"{qa_path}: counterfactual {qa['counterfactual']!r} is a POST HOC diagnostic, never a "
                             "release input")
        mismatch = {key: qa["parameters"].get(key) for key, value in expected.items()
                    if not math.isclose(float(qa["parameters"].get(key, math.nan)), value, rel_tol=1e-9)}
        if mismatch:
            raise SystemExit(f"{qa_path}: parameters {mismatch} are not the default {expected} (owner decision 2)")
        if qa.get("variant") != arm.variant.as_dict():
            raise SystemExit(f"{qa_path}: variant {qa.get('variant')} is not the inventory of arm "
                             f"{arm.variant.as_dict()}; the variants S and T are information arms, never release inputs")
        rule = gpd.read_file(directory / f"{ags}_supply_zones_{tag}.geojson")
        rule = (rule.set_crs("EPSG:4326") if rule.crs is None else rule).to_crs(cc.METRIC_CRS)
        raster = pd.read_csv(directory / f"{ags}_paid_share_{tag}.csv.gz")
        arms = {}
        for reported in REPORTED_ARMS:
            arm_path = directory / f"{ags}_supply_qa_{reported.tag()}.json"
            if arm_path.is_file() and reported != arm:
                document = json.loads(arm_path.read_text(encoding="utf-8"))
                if document.get("variant") != reported.variant.as_dict() or document.get("counterfactual"):
                    raise SystemExit(f"{arm_path}: its variant or counterfactual does not match the arm "
                                     f"{reported.label}")
                arms[reported.tag()] = document
        supply[ags] = {"qa": qa, "rule": rule, "raster": raster, "arms": arms}
    sources = {(entry["qa"]["extract"]["md5"], entry["qa"]["osm_timestamp"]) for entry in supply.values()}
    if len(sources) > 1:
        raise SystemExit(f"the towns were read from different extracts or snapshots {sorted(sources)}")
    return supply


def load_supply_counterfactuals(paths) -> list:
    """The QA files of POST HOC counterfactual runs (builder ``--counterfactual``) named on the command line; each
    must name its counterfactual and carry the default parameters of owner decision 2 or the pre-registered ones of
    Amendment B (only the tag reading differs; the note states the share)."""
    documents = []
    allowed = [ss.DEFAULT_SUPPLY_PARAMETERS.as_dict(), ss.PRE_REGISTERED_SUPPLY_PARAMETERS.as_dict()]
    for path in paths or ():
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if not document.get("counterfactual"):
            raise SystemExit(f"{path} is not a counterfactual QA file")
        if not any(all(math.isclose(float(document["parameters"].get(key, math.nan)), value, rel_tol=1e-9)
                       for key, value in expected.items()) for expected in allowed):
            raise SystemExit(f"{path}: a counterfactual varies the tag reading only, not the parameters (default or "
                             f"pre-registered: {allowed})")
        documents.append(dict(document, _path=Path(path).name))
    return documents


def decisive_interpretation_text(entry: dict, counterfactuals=()) -> str:
    """The Braunschweig note on the interpretation beyond B-a..B-f that decided B5 at the pre-registered share 0.5:
    parking:<side>=yes read as street parking (literal B-a) with the H1 numbers of this table, and every POST HOC
    counterfactual run of it with its share and numbers."""
    qa = entry["qa"]
    validation, supply = qa.get("validation") or {}, qa["supply"]
    text = (f"interpretation beyond B-a..B-f that decided B5 at the pre-registered share 0.5 (an implementation choice "
            f"fixed before the result was known, not an owner ruling): parking:<side>=yes, the fallback value of the "
            f"street parking scheme, is read as street parking, free without a fee tag (the literal B-a reading; "
            f"{supply['free_street_sides_position_yes']} free sides with "
            f"{supply['free_street_side_spaces_position_yes']:.0f} spaces in the query box); with it H1 at the share "
            f"{qa['parameters']['share_threshold']:g}: recall {validation.get('recall', math.nan):.3f}, precision "
            f"{validation.get('precision', math.nan):.3f}")
    cited = [document for document in counterfactuals if document["ags"] == qa["ags"]]
    for document in cited:
        other = document.get("validation") or {}
        passes = ss.passes_validation(other)
        text += (f"; POST HOC counterfactual {document['counterfactual']} at the share "
                 f"{document['parameters']['share_threshold']:g} ({document['_path']}; those sides read as no parking "
                 f"information, as lever 1 reads them): recall {other.get('recall', math.nan):.3f}, precision "
                 f"{other.get('precision', math.nan):.3f}, which would {'pass' if passes else 'fail'} B5 - not a "
                 "validation and never a default; the owner decides the tag meaning in the Amendment-B ADR")
    if not cited:
        text += "; no counterfactual run cited"
    return text + "; every further interpretation: data record parking_zones_2026"


def supply_b5(supply: dict) -> bool:
    """H1: the B5 gate re-applied to the Braunschweig metrics of the default parameters
    (``supply_share.passes_validation``); refuses inputs without metrics or whose recorded decision contradicts them."""
    entry = supply.get(BS_AGS)
    validation = (entry or {}).get("qa", {}).get("validation")
    if not validation:
        raise SystemExit(f"{BS_AGS}: no B5 metrics; run the builder with --legal-zones, --reference {BS_AGS}=<annex>, "
                         "--annex-affine and --annex-image")
    passed = ss.passes_validation(validation)
    if bool(validation.get("passes")) != passed:
        raise SystemExit(f"{BS_AGS}: the recorded B5 decision {validation.get('passes')} contradicts recall "
                         f"{validation.get('recall')} and precision {validation.get('precision')}")
    share = entry["qa"]["parameters"]["share_threshold"]
    print(f"H1 (B5 at the share {share:g} of owner decision 2, POST HOC; minimum {ss.VALIDATION_MINIMUM:.2f}): recall "
          f"{validation['recall']:.3f}, precision {validation['precision']:.3f}: {'passed' if passed else 'FAILED'}")
    return passed


def holdout_blocks(supply: dict, tag: Optional[str] = None) -> dict:
    """ags -> the holdout block of every holdout town (``supply_share.HOLDOUT_REFERENCE_ZONES``) in ``supply``, of the
    loaded arm (``tag`` None) or of the reported arm ``tag``; towns without the block are left out."""
    blocks = {}
    for ags in ss.HOLDOUT_REFERENCE_ZONES:
        entry = supply.get(ags)
        qa = None if entry is None else (entry["qa"] if tag is None else entry["arms"].get(tag))
        if qa is not None and qa.get("holdout"):
            blocks[ags] = qa["holdout"]
    return blocks


def supply_h2(supply: dict) -> dict:
    """H2: the pre-registered holdout check pooled from the holdout overlaps of the town QA files
    (``supply_share.holdout_pooled_metrics``); ``SystemExit`` when a holdout town or its overlaps are missing (H2 is
    undefined then, never passed)."""
    try:
        pooled = ss.holdout_pooled_metrics(holdout_blocks(supply))
    except ValueError as error:
        raise SystemExit(f"H2 cannot be computed: {error}; run the builder for every holdout town with "
                         "--holdout-zones") from error
    print(f"H2 (pre-registered holdout check, minimums {ss.HOLDOUT_POOLED_MINIMUM:.2f} pooled and "
          f"{ss.HOLDOUT_TOWN_RECALL_MINIMUM:.2f} per town): pooled recall {pooled['pooled_recall']:.3f}, pooled precision "
          f"{pooled['pooled_precision']:.3f}, smallest town recall {pooled['minimum_town_recall']:.3f}: "
          f"{'passed' if pooled['passes'] else 'FAILED'}")
    return pooled


def supply_gate(supply: dict) -> dict:
    """The application gate of owner decision 2: H1 (``supply_b5``) and H2 (``supply_h2``) must both pass; returns
    {"h1", "h1_passed", "h2", "h2_passed", "passed"}."""
    h1_passed = supply_b5(supply)
    h2 = supply_h2(supply)
    gate = {"h1": supply[BS_AGS]["qa"]["validation"], "h1_passed": h1_passed, "h2": h2, "h2_passed": bool(h2["passes"]),
            "passed": bool(h1_passed and h2["passes"])}
    print(f"application gate (H1 and H2): {'passed, B6 applies' if gate['passed'] else 'FAILED, nothing is applied'}")
    return gate


def supply_rule(entry: Optional[dict]):
    """Union of the rule polygons of one town (EPSG:25832), or None when there is none."""
    if not entry or not len(entry["rule"]):
        return None
    return unary_union(list(entry["rule"].geometry))


def supply_note(qa: dict, *, replaces: str, assignment: str = "") -> str:
    """The digitising note of an osm_supply_majority polygon: rule, parameters, supply, extract and the QA row."""
    p, s = qa["parameters"], qa["supply"]
    classes = s["elements_by_class"]
    heuristic = s["heuristic_capacity_share"]
    return (f"Majority rule over the public parking supply (parking cost zones v2, spec Amendment B; "
            f"braunschweig.parking.supply_share, scripts/build_parking_zones_from_osm.py --supply-share): a "
            f"{p['cell_m']:.0f} m cell is paid when at least {p['minimum_usable_spaces']:.0f} usable spaces lie within "
            f"W = {p['walk_m']:.0f} m of its centre and at least {p['share_threshold']:.2f} of them are paid or "
            f"restricted; the paid cells united, smoothed +/-{p['smoothing_m']:.1f} m, parts below "
            f"{p['minimum_island_m2']:.0f} m2 dropped (rule {qa['rule']['area_m2']:.0f} m2 in {qa['rule']['parts']} "
            f"part{'' if qa['rule']['parts'] == 1 else 's'}). Supply of the Geofabrik extract {qa['extract']['file']} "
            f"(MD5 {qa['extract']['md5']}, OSM snapshot {qa['osm_timestamp']}; box {qa['bbox']}): {s['elements']} "
            f"elements ({classes['paid']} paid, {classes['restricted']} restricted, {classes['free']} free, "
            f"{classes['excluded']} excluded), {s['usable_spaces']:.0f} usable spaces, "
            f"{100.0 * (heuristic if heuristic is not None else math.nan):.1f} % of them from the capacity heuristic "
            f"B-d. The share threshold {p['share_threshold']:g} is the owner's POST HOC choice (owner decision 2, the "
            f"pre-registered value was 0.5); H1 (B5 at that share, Braunschweig) and the pre-registered holdout check H2 "
            f"passed. " + (assignment + " " if assignment else "")
            + replaces + f" QA row {qa['ags']} of parking_zones_2026_supply_share_qa.csv.")


def supply_zone(zone_id, ags, geometry, source_url, qa, *, replaces, assignment="") -> dict:
    """An osm_supply_majority zone record with the rule provenance (W, share threshold, minimum supply, snapshot)."""
    p = qa["parameters"]
    return zone_record(zone_id, ags, geometry, pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE, source_url,
                       supply_note(qa, replaces=replaces, assignment=assignment), osm_timestamp=qa["osm_timestamp"],
                       source_date=SUPPLY_DIGITISED_ON, digitised_on=SUPPLY_DIGITISED_ON,
                       minimum_part_m2=float(p["minimum_island_m2"]), supply_walk_m=float(p["walk_m"]),
                       paid_share_threshold=float(p["share_threshold"]),
                       minimum_usable_spaces=float(p["minimum_usable_spaces"]))


def supply_replacement(zone_id, ags, url, what, supply: dict, gate_passed: bool) -> Optional[dict]:
    """The osm_supply_majority polygon that replaces the v1 centre approximation ``zone_id`` (B6: Goslar,
    Wolfenbuettel, Gifhorn), or None when the application gate (H1 and H2) failed, ``ags`` may not replace polygons or
    its rule is empty."""
    if not gate_passed or EROSION_ZONES_FROM_CORE.get(ags) != zone_id:
        return None
    entry = supply.get(ags)
    rule = supply_rule(entry)
    if rule is None:
        return None
    return supply_zone(zone_id, ags, rule, url, entry["qa"], replaces=(
        "Replaces the v1 centre approximation of 2026-09-29 (" + what + ", buffered 40 m, from the car network of the "
        "local MATSim scenario because the fee request of that day failed with HTTP 504); the tariff row is unchanged."))


def _share(value, digits=6) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    return f"{value:.{digits}f}"


def cross_check_text(cross: dict, extract_timestamp: str) -> str:
    """The QA column cross_check: per kind and class 'extract/Overpass (difference)' and the two snapshots."""
    clauses = []
    for kind in ss.ELEMENT_KINDS:
        parts = []
        for name in ss.SUPPLY_CLASSES:
            extract, overpass = cross["extract"][kind][name], cross["overpass"][kind][name]
            parts.append(f"{name} {extract}/{overpass}" + (f" ({extract - overpass:+d})" if extract != overpass else ""))
        clauses.append(f"{kind}: " + ", ".join(parts))
    return ("extract/Overpass: " + "; ".join(clauses) + f"; snapshots extract {extract_timestamp}, Overpass "
            f"{cross['overpass_osm_timestamp']}")


def pooled_holdout_by_arm(supply: dict) -> dict:
    """tag -> the pooled H2 of every reported arm whose QA files exist for every holdout town, else None."""
    pooled = {}
    for arm in REPORTED_ARMS:
        blocks = holdout_blocks(supply, arm.tag())
        pooled[arm.tag()] = (ss.holdout_pooled_metrics(blocks) if len(blocks) == len(ss.HOLDOUT_REFERENCE_ZONES)
                             else None)
    return pooled


def _gate_word(passed: bool) -> str:
    return "passes" if passed else "fails"


def arm_text(arm, qa: Optional[dict], *, ags: str, pooled: Optional[dict]) -> str:
    """One arm in the QA columns sensitivity and variant_arms: the rule area; in Braunschweig H1; in a holdout town
    its holdout recall (and precision); for T the elements the payment evidence turns paid; on the Braunschweig row
    the pooled H2 of the arm."""
    if qa is None:
        return f"{arm.label}: not run"
    parts = int(qa["rule"]["parts"])
    text = f"{arm.label}: rule {qa['rule']['area_m2']:.0f} m2 in {parts} part{'' if parts == 1 else 's'}"
    validation = qa.get("validation")
    if ags == BS_AGS and validation:
        text += (f", H1 recall {validation['recall']:.3f}, precision {validation['precision']:.3f} "
                 f"({_gate_word(ss.passes_validation(validation))})")
    holdout = qa.get("holdout")
    if holdout:
        text += f", holdout recall {holdout['recall']:.3f}" + (
            f", precision {holdout['precision']:.3f}" if holdout.get("precision") is not None else "")
    payment = qa.get("payment_evidence") or {}
    if payment.get("converted") is not None:
        text += (f", T turns {sum(payment['converted'].values())} elements ({payment['converted_spaces']:.0f} spaces) "
                 "paid")
    if ags == BS_AGS:
        text += ("; H2 not computed (a holdout town was not run)" if pooled is None else
                 f"; H2 pooled recall {pooled['pooled_recall']:.3f}, pooled precision {pooled['pooled_precision']:.3f}, "
                 f"smallest town recall {pooled['minimum_town_recall']:.3f} ({_gate_word(pooled['passes'])})")
    return text


def arms_text(entry: dict, arms, *, ags: str, pooled: dict, closing: str) -> str:
    """The clauses of ``arms`` (``arm_text``) for one town, joined, with the ``closing`` remark."""
    return " | ".join(arm_text(arm, entry["arms"].get(arm.tag()), ags=ags, pooled=pooled.get(arm.tag()))
                      for arm in arms) + f" ({closing})"


def payment_evidence(entry: dict) -> dict:
    """The payment evidence of variant T of one town: the ``payment_evidence`` block of the arm T at the default share
    (``PAYMENT_EVIDENCE_ARM``; its one source), empty when that arm was not run."""
    return (entry["arms"].get(PAYMENT_EVIDENCE_ARM.tag()) or {}).get("payment_evidence") or {}


def payment_evidence_text(entry: dict) -> str:
    """The note on the payment evidence of variant T in the town's query box (ruling R-T1c-a: ticket machines and
    parking elements with an app-payment tag) and what T at the default share turns paid (information only)."""
    payment = payment_evidence(entry)
    if not payment.get("evidence"):
        return "payment evidence of variant T: the arm T at the default share was not run"
    evidence, converted, by = payment["evidence"], payment["converted"], payment["converted_by"]
    return (f"payment evidence of variant T in the query box (ruling R-T1c-a): {evidence['ticket_machines']} parking "
            f"ticket machines, {evidence['app_payment_elements']} parking elements with an app-payment tag; T within "
            f"{payment['distance_m']:.0f} m turns {converted['street_side']} street sides, {converted['street_side_area']} "
            f"street-side areas and {converted['lot']} lots ({payment['converted_spaces']:.0f} spaces) paid: "
            f"{by['ticket_machine']} by a ticket machine, {by['app_payment_parking']} by an app-payment tag on a parking "
            f"element, {by['own_app_payment_tag']} by their own app-payment tag (information only)")


def supply_qa_row(ags: str, entry: dict, *, role: str, reference: str, applied_zones, decision: str,
                  note: str, gate: Optional[dict] = None, pooled: Optional[dict] = None) -> dict:
    """One row of the committed supply-share QA table (``supply_share.SUPPLY_SHARE_QA_COLUMNS``); ``gate``
    (``supply_gate``) fills the H1 and H2 columns of the Braunschweig row, ``pooled`` (``pooled_holdout_by_arm``) the
    arms of that row."""
    pooled = pooled or {}
    qa = entry["qa"]
    p, s, cross, raster = qa["parameters"], qa["supply"], qa["cross_check"], qa["raster"]
    compared = qa.get("reference") or {}
    by_kind = s["elements_by_kind_and_class"]
    usable = s["usable_spaces"]
    row = {column: "" for column in ss.SUPPLY_SHARE_QA_COLUMNS}
    row.update({
        "ags": ags, "name": MUNICIPALITY_NAMES[ags], "role": role, "osm_extract": qa["extract"]["file"],
        "osm_extract_md5": qa["extract"]["md5"], "osm_timestamp": qa["osm_timestamp"],
        "walk_m": _number(p["walk_m"], 1), "share_threshold": _number(p["share_threshold"], 3),
        "minimum_usable_spaces": _number(p["minimum_usable_spaces"], 1), "cell_m": _number(p["cell_m"], 1),
        "smoothing_m": _number(p["smoothing_m"], 1), "minimum_island_m2": _number(p["minimum_island_m2"], 1),
        "street_ways": str(int(qa["street_ways"])), "street_side_areas": str(sum(by_kind["street_side_area"].values())),
        "offstreet_lots": str(sum(by_kind["lot"].values())),
        "overpass_response": cross["overpass_response"], "overpass_osm_timestamp": cross["overpass_osm_timestamp"],
        "cross_check": cross_check_text(cross, qa["osm_timestamp"]),
        "usable_spaces": _number(usable, 1), "tagged_capacity_share": _share(s["tagged_spaces"] / usable if usable
                                                                             else None),
        "heuristic_capacity_share": _share(s["heuristic_capacity_share"]),
        "free_street_spaces_without_fee_tag_share": _share(s["free_street_spaces_without_fee_tag_share"]),
        "offstreet_lots_without_fee_tag": str(int(s["offstreet_lots_without_fee_tag"])),
        "offstreet_spaces_without_fee_tag_share": _share(s["offstreet_spaces_without_fee_tag_share"]),
        "cells": str(int(raster["cells"])), "classified_cells": str(int(raster["classified_cells"])),
        "classified_cell_share": _share(raster["classified_cell_share"]), "paid_cells": str(int(raster["paid_cells"])),
        "rule_area_m2": _number(qa["rule"]["area_m2"], 1), "rule_parts": str(int(qa["rule"]["parts"])),
        "reference": reference, "rule_share_inside_reference": _share(compared.get("core_share_inside_reference")),
        "reference_share_covered_by_rule": _share(compared.get("reference_share_covered")),
        "largest_outline_distance_m": _number(compared.get("largest_outline_distance_m"), 1),
        "sensitivity": arms_text(entry, ss.AMENDMENT_B_ARMS, ags=ags, pooled=pooled, closing=(
            "Amendment B arms around its pre-registered share 0.5, information only")),
        "variant_arms": arms_text(entry, ss.VARIANT_ARMS, ags=ags, pooled=pooled, closing=(
            "information arms of owner decisions 2 and 3, never applied")),
        "applied": "true" if applied_zones else "false",
        "zone_ids": ";".join(applied_zones), "decision": decision, "note": note})
    for name in ss.SUPPLY_CLASSES:
        row[f"{name}_elements"] = str(int(s["elements_by_class"][name]))
        row[f"overpass_{name}_elements"] = str(sum(int(cross["overpass"][kind][name]) for kind in ss.ELEMENT_KINDS))
    for name in ss.USABLE_CLASSES:
        row[f"{name}_spaces"] = _number(s["spaces_by_class"][name], 1)
    payment = payment_evidence(entry)
    if payment.get("evidence"):
        row.update({"ticket_machines": str(int(payment["evidence"]["ticket_machines"])),
                    "app_payment_elements": str(int(payment["evidence"]["app_payment_elements"]))})
    if payment.get("converted") is not None:
        row.update({"payment_evidence_paid_elements": str(sum(int(value) for value in payment["converted"].values())),
                    "payment_evidence_paid_spaces": _number(payment["converted_spaces"], 1)})
    holdout = qa.get("holdout")
    if ags in ss.HOLDOUT_REFERENCE_ZONES and holdout:
        row.update({"holdout_references": ";".join(ss.HOLDOUT_REFERENCE_ZONES[ags]),
                    "holdout_reference_area_m2": _number(holdout["reference_area_m2"], 1),
                    "holdout_rule_inside_reference_m2": _number(holdout["rule_inside_reference_m2"], 1),
                    "holdout_recall": _share(holdout["recall"])})
        if ags in ss.HOLDOUT_PRECISION_TOWNS:
            row.update({"holdout_rule_inside_query_box_m2": _number(holdout["rule_inside_query_box_m2"], 1),
                        "holdout_precision": _share(holdout["precision"])})
    if ags == BS_AGS and qa.get("validation"):
        validation = qa["validation"]
        row.update({"b5_recall": _share(validation["recall"]), "b5_precision": _share(validation["precision"]),
                    "b5_passed": "true" if ss.passes_validation(validation) else "false"})
    if ags == BS_AGS and gate is not None:
        h2 = gate["h2"]
        row.update({"h2_pooled_recall": _share(h2["pooled_recall"]), "h2_pooled_precision": _share(h2["pooled_precision"]),
                    "h2_minimum_town_recall": _share(h2["minimum_town_recall"]),
                    "h2_passed": "true" if gate["h2_passed"] else "false"})
    return row


def gate_text(gate: dict) -> str:
    """The Braunschweig note on the application gate of owner decision 2: H1 and H2 with their numbers and bounds."""
    h1, h2 = gate["h1"], gate["h2"]
    towns = ", ".join(f"{ags} {value:.3f}" for ags, value in h2["town_recall"].items())
    return (f"H1 (B5 recomputed with the owner's POST HOC share threshold of this table, owner decision 2; recall and "
            f"precision >= {ss.VALIDATION_MINIMUM:.2f}, a set value, not a law): recall {h1['recall']:.3f}, precision "
            f"{h1['precision']:.3f}: H1 {'passed' if gate['h1_passed'] else 'failed'}; H2 (the holdout check, "
            f"pre-registered before any holdout overlap at that share was computed): pooled recall "
            f"{h2['pooled_recall']:.3f} ({h2['rule_inside_reference_m2']:.0f} of {h2['reference_area_m2']:.0f} m2 of "
            f"every holdout reference), pooled precision {h2['pooled_precision']:.3f} "
            f"({h2['precision_rule_inside_reference_m2']:.0f} of {h2['rule_inside_query_box_m2']:.0f} m2 of the rule in "
            f"the query boxes of the four towns), minimum {ss.HOLDOUT_POOLED_MINIMUM:.2f} each; town recall {towns} "
            f"(minimum {ss.HOLDOUT_TOWN_RECALL_MINIMUM:.2f}, ASSUMPTION Q6): H2 {'passed' if gate['h2_passed'] else 'failed'}; "
            f"the rule is applied only when both pass")


def supply_qa_rows(supply: dict, zones: list, pieces: list, gate: dict, counterfactuals=()) -> list:
    """One supply-share QA row per town of ``supply`` (``EROSION_ZONES_FROM_CORE``, ``EROSION_QA_ONLY`` order), with
    the application gate ``gate`` (``supply_gate``: H1 and H2) and the reported arms; ``counterfactuals``
    (``load_supply_counterfactuals``) are cited in the Braunschweig note."""
    by_id = {z["zone_id"]: z for z in zones}
    order = [ags for ags in list(EROSION_ZONES_FROM_CORE) + list(EROSION_QA_ONLY) if ags in supply]
    pooled = pooled_holdout_by_arm(supply)
    rows = []
    for ags in order:
        entry = supply[ags]
        role = "zones_from_rule" if ags in EROSION_ZONES_FROM_CORE else "qa_only"
        applied = sorted(z["zone_id"] for z in zones
                         if z["_ags"] == ags and z["geometry_source"] == pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE)
        zone_id = EROSION_ZONES_FROM_CORE.get(ags) or EROSION_QA_ONLY.get(ags)
        if role == "qa_only":
            decision = "qa_only"
        elif not gate["passed"]:
            decision = "gate_failed"
        else:
            decision = "applied" if applied else "no_rule_polygon"
        notes = []
        if ags == BS_AGS:
            reference = ANNEX_REFERENCE
            notes.append(gate_text(gate))
            notes.append(decisive_interpretation_text(entry, counterfactuals))
        elif role == "zones_from_rule":
            reference = f"v1 polygon {zone_id} (centre_approximation, replaced where the rule is applied)"
        else:
            reference = f"v1 polygon {zone_id} ({by_id[zone_id]['geometry_source'] if zone_id in by_id else 'absent'})"
        holdout = entry["qa"].get("holdout")
        if holdout:
            notes.append("H2 references " + ", ".join(
                f"{zone_id_} {values['rule_inside_m2']:.0f} of {values['area_m2']:.0f} m2 inside the rule"
                for zone_id_, values in holdout["references"].items()) + f": recall {holdout['recall']:.3f}" + (
                f", precision {holdout['precision']:.3f} ({holdout['rule_inside_reference_m2']:.0f} of "
                f"{holdout['rule_inside_query_box_m2']:.0f} m2 of the rule in the query box)"
                if holdout.get("precision") is not None else " (recall only)"))
        if decision == "gate_failed":
            failed = " and ".join(name for name, passed in (("H1", gate["h1_passed"]), ("H2", gate["h2_passed"]))
                                  if not passed)
            notes.append(f"B6 not applied in any town because {failed} failed; " + (
                "ParkGO zone II and the southern part of zone Ia stay unzoned" if ags == BS_AGS else
                f"the v1 centre approximation {zone_id} stays"))
        elif decision == "applied" and ags == BS_AGS:
            notes.append("the rule pieces left after the v1 zones are assigned whole to the annex zone they overlap "
                         "most (ruling R-T1-f): " + piece_assignment_text(
                             pieces, float(entry["qa"]["parameters"]["minimum_island_m2"])))
        elif decision == "applied":
            notes.append(f"the rule polygons replace the v1 centre approximation {zone_id} (tariff row unchanged)")
        elif decision == "no_rule_polygon":
            notes.append("H1 and H2 passed but no rule polygon is left to apply" + (
                ": " + piece_assignment_text(pieces, float(entry["qa"]["parameters"]["minimum_island_m2"]))
                if ags == BS_AGS and pieces else f"; the v1 polygon {zone_id} stays"))
        else:
            notes.append(f"QA only (spec Amendment B6): the v1 polygon {zone_id} stays")
        supply_numbers = entry["qa"]["supply"]
        share = supply_numbers["free_street_spaces_without_fee_tag_share"]
        if share is not None:
            notes.append(f"{100.0 * share:.1f} % of the free street capacity rests on a missing fee tag (ASSUMPTION B-a)")
        # the fallbacks inside the capacity heuristic B-d, next to its rate (column heuristic_capacity_share)
        notes.append(f"capacity: {supply_numbers['usable_elements_without_extent']} usable nodes without a capacity tag "
                     f"carry 0 spaces, {supply_numbers['multi_storey_lots_without_levels']} of "
                     f"{supply_numbers['multi_storey_lots']} multi-storey car parks count one level (no levels tag), "
                     f"{supply_numbers['capacity_tags_invalid']} capacity tags are no whole number (heuristic used)")
        notes.append(payment_evidence_text(entry))
        rows.append(supply_qa_row(ags, entry, role=role, reference=reference, applied_zones=applied, decision=decision,
                                  note="; ".join(notes) + ".", gate=gate if ags == BS_AGS else None, pooled=pooled))
    return rows


def write_supply_share_qa(path, rows: list) -> None:
    """The supply-share QA table with its header: the intro and one '# <column>: <definition>' line per column."""
    missing = [column for column in ss.SUPPLY_SHARE_QA_COLUMNS if column not in SUPPLY_QA_COLUMN_GLOSSARY]
    if missing or len(SUPPLY_QA_COLUMN_GLOSSARY) != len(ss.SUPPLY_SHARE_QA_COLUMNS):
        raise SystemExit(f"SUPPLY_QA_COLUMN_GLOSSARY and SUPPLY_SHARE_QA_COLUMNS differ: missing {missing}")
    header = [f"# {line}" for line in SUPPLY_QA_INTRO] + [f"# {column}: {SUPPLY_QA_COLUMN_GLOSSARY[column]}"
                                                           for column in ss.SUPPLY_SHARE_QA_COLUMNS]
    table = pd.DataFrame(rows, columns=list(ss.SUPPLY_SHARE_QA_COLUMNS))
    text = ascii_transliteration("\n".join(header) + "\n" + table.to_csv(index=False, lineterminator="\n"))
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    print(f"written {path} with {len(table)} rows: " + ", ".join(f"{row['ags']} {row['decision']}" for row in rows))


def paid_share_release(supply: dict) -> pd.DataFrame:
    """B7: the classified cells of every town (``supply_share.release_frame``), ordered by AGS, validated."""
    release = pd.concat([ss.release_frame(supply[ags]["raster"], ags) for ags in sorted(supply)], ignore_index=True)
    ss.validate_paid_share_release(release)
    return release


def paid_share_provenance(supply: dict) -> list:
    """Header lines of the release: the extract, the parameters and per town the box and the classified cells."""
    first = supply[sorted(supply)[0]]["qa"]
    extract, p = first["extract"], first["parameters"]
    lines = [f"Source: the Geofabrik extract {extract['file']} ({extract.get('bytes', 'unknown')} bytes, MD5 "
             f"{extract['md5']} checked against the Geofabrik MD5 file, SHA-256 {extract.get('sha256', 'unknown')}; "
             f"OSM snapshot {first['osm_timestamp']}), read with the GDAL OSM driver.",
             f"Parameters (spec Amendment B): cells of {p['cell_m']:.0f} m, W = {p['walk_m']:.0f} m, "
             f"minimum_usable_spaces = {p['minimum_usable_spaces']:.0f} (B-e); the share threshold "
             f"{p['share_threshold']:.2f} of the zone rule B4 (owner decision 2, a POST HOC change of the pre-registered "
             f"0.50) is not applied to this file."]
    for ags in sorted(supply):
        qa = supply[ags]["qa"]
        lines.append(f"{ags} {MUNICIPALITY_NAMES.get(ags, ags)}: query box {qa['bbox']} (south, west, north, east), "
                     f"{qa['raster']['classified_cells']} of {qa['raster']['cells']} cells classified.")
    return lines


def write_paid_share_release(path, frame: pd.DataFrame, provenance) -> None:
    """The B7 release as gzip CSV without a time stamp (equal content, equal bytes): the intro, the provenance lines
    and one '# <column>: <definition>' line per column, then the cells (coordinates 0.1 m, shares 6 decimals,
    spaces 0.01)."""
    ss.validate_paid_share_release(frame)
    if set(RELEASE_COLUMN_GLOSSARY) != set(ss.PAID_SHARE_RELEASE_COLUMNS):
        raise SystemExit("RELEASE_COLUMN_GLOSSARY and PAID_SHARE_RELEASE_COLUMNS differ")
    header = [f"# {line}" for line in list(RELEASE_INTRO) + list(provenance) + ["Columns:"]]
    header += [f"# {column}: {RELEASE_COLUMN_GLOSSARY[column]}" for column in ss.PAID_SHARE_RELEASE_COLUMNS]
    body = pd.DataFrame({"x_m": [f"{value:.1f}" for value in frame["x_m"]],
                         "y_m": [f"{value:.1f}" for value in frame["y_m"]],
                         "municipality_ags": frame["municipality_ags"].astype(str),
                         "paid_share": [f"{value:.6f}" for value in frame["paid_share"]],
                         "usable_spaces": [f"{value:.2f}" for value in frame["usable_spaces"]],
                         "heuristic_capacity_share": [f"{value:.6f}" for value in frame["heuristic_capacity_share"]]})
    text = ascii_transliteration("\n".join(header) + "\n" + body.to_csv(index=False, lineterminator="\n"))
    Path(path).write_bytes(ss.deterministic_gzip(text))
    counts = frame["municipality_ags"].value_counts().sort_index()
    print(f"written {path} with {len(frame)} classified cells: " + ", ".join(f"{ags} {count}"
                                                                             for ags, count in counts.items()))


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
    parser.add_argument("--supply-share-dir", help="v2 Amendment B: directory with <ags>_supply_qa_<tag>.json, "
                                                   "<ags>_supply_zones_<tag>.geojson and <ags>_paid_share_<tag>.csv.gz "
                                                   "of the default arm B (owner decision 2) and the reported arms")
    parser.add_argument("--supply-qa-out", help="v2 Amendment B: the QA table to write "
                                                "(parking_zones_2026_supply_share_qa.csv)")
    parser.add_argument("--paid-share-out", help="v2 Amendment B7: the release of the classified cells to write "
                                                 "(parking_paid_share_2026.csv.gz)")
    parser.add_argument("--supply-counterfactual-qa", action="append", default=[],
                        help="v2 Amendment B: QA file of a POST HOC --counterfactual run, cited in the note of its town")
    args = parser.parse_args(argv)
    if args.erosion_dir and not (args.reference_outline and args.qa_out):
        raise SystemExit("--erosion-dir needs --reference-outline and --qa-out")
    if args.supply_share_dir and not (args.reference_outline and args.supply_qa_out and args.paid_share_out):
        raise SystemExit("--supply-share-dir needs --reference-outline, --supply-qa-out and --paid-share-out")
    zones = []

    def add(zone_id, ags, geometry, geometry_source, source_url, note):
        zones.append(zone_record(zone_id, ags, geometry, geometry_source, source_url, note))

    # ---------------------------------------------------------------- v2 lever 1: rule-based cores (issue #436)
    erosion, counterfactuals, annex_zones = {}, [], None
    if args.erosion_dir:
        erosion = load_erosion_inputs(args.erosion_dir)
        counterfactuals = load_counterfactuals(args.counterfactual_qa)
        annex_zones = gpd.read_file(args.reference_outline).to_crs(cc.METRIC_CRS).set_index("zone")

    # ---------------------------------------------------------------- v2 Amendment B: majority rule (issue #436)
    supply, gate, supply_counterfactuals = {}, None, []
    if args.supply_share_dir:
        supply = load_supply_inputs(args.supply_share_dir)
        supply_counterfactuals = load_supply_counterfactuals(args.supply_counterfactual_qa)
        gate = supply_gate(supply)
        if annex_zones is None:
            annex_zones = gpd.read_file(args.reference_outline).to_crs(cc.METRIC_CRS).set_index("zone")
    gate_passed = bool(gate and gate["passed"])

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
        supplied = supply_replacement(zone_id, ags, url, what, supply, gate_passed)
        if replacement is not None and supplied is not None:
            raise SystemExit(f"{zone_id}: an accepted erosion core and the supply rule would both replace it")
        replacement = replacement if replacement is not None else supplied
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
    supply_pieces = []
    bs_rule = supply_rule(supply.get(BS_AGS)) if gate_passed else None
    if bs_rule is not None:
        records, supply_pieces = assign_braunschweig_pieces(bs_rule, zones, annex_zones, supply[BS_AGS]["qa"],
                                                            zone_factory=supply_zone, noun="rule polygons")
        zones += records
        print("Braunschweig rule pieces:", piece_assignment_text(
            supply_pieces, float(supply[BS_AGS]["qa"]["parameters"]["minimum_island_m2"])))
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
    if args.supply_share_dir:
        write_supply_share_qa(args.supply_qa_out, supply_qa_rows(supply, zones, supply_pieces, gate,
                                                                 supply_counterfactuals))
        write_paid_share_release(args.paid_share_out, paid_share_release(supply), paid_share_provenance(supply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
