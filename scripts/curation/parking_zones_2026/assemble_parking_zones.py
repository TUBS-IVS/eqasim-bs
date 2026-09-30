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

Parking cost zones v2, lever 1 (issue #436, spec amendments A1 and A2; ``--erosion-dir``): the rule-based cores of
``scripts/build_parking_zones_from_osm.py --regulation`` (``<ags>_core.geojson`` and ``<ags>_qa.json``, one per
municipality of ``EROSION_ZONES_FROM_CORE`` and ``EROSION_QA_ONLY``) enter as ``osm_fee_erosion`` polygons with the
provenance fields ``unavoidable_walk_m`` and ``osm_timestamp``, but only where the pre-registered acceptance rule Q4
accepted the core. Goslar, Wolfenbuettel and Gifhorn: an accepted core replaces the ``centre_approximation``
polygon of the same zone id (tariff row unchanged). Braunschweig: the core is split by the georeferenced ParkGO
annex outlines (``--reference-outline``, extract_parkgo_annex_zones.py) into ``bs_zone_ia_sued`` (inside the annex
outline of zone Ia) and ``bs_zone_ii`` (inside zone II); every v1 zone is cut out of them by precedence (ordinance
and street-list polygons win), core outside both outlines is not zoned. After the cuts the parts below the core's
``minimum_island_m2`` are dropped again (ASSUMPTION Q2). The other municipalities are QA only. A municipality without
a response (a request that failed after all attempts, see ``overpass_failures.log``) keeps its v1 polygon. Every
curated municipality gets one row of the committed QA table ``--qa-out`` (``braunschweig.parking.zones``
``ZONE_QA_COLUMNS``). Without ``--erosion-dir`` the output is the v1 file byte for byte.

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/assemble_parking_zones.py --ia-ib bs_zone_map_ia_ib.geojson \
        --affine bs_zone_map_affine.json --fee-islands stadthalle_fee_islands.geojson \
        --raw-overpass eqasim-data/data/braunschweig/parking/raw_overpass \
        --network <main checkout>/eqasim-data/output_bs/braunschweig_1pct_network.xml.gz \
        --municipalities <main checkout>/eqasim-data/cache_bs_bpsmoke/data.spatial.municipalities__<hash>.p \
        --out eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson \
        [--erosion-dir eqasim-data/data/braunschweig/parking/raw_overpass \
         --reference-outline eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_zones_georeferenced.geojson \
         --qa-out eqasim-data/data/braunschweig/parking/parking_zones_2026_qa.csv]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union

import curation_common as cc

# The script runs from its own directory (curation_common); the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
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
QA_HEADER = (
    "# Curation QA of the rule-based parking zone cores (parking cost zones v2, lever 1, issue #436; spec amendments A1",
    "# and A2): one row per curated municipality. Written by scripts/curation/parking_zones_2026/assemble_parking_zones.py",
    "# from the <ags>_qa.json files of scripts/build_parking_zones_from_osm.py --regulation (raw responses local under",
    "# raw_overpass/, gitignored). Construction (braunschweig/parking/zone_geometry.py): R = ways with a paid, restricted",
    "# or forbidden side buffered 25 m plus paid or restricted car parks buffered 10 m; fill(R) = holes up to",
    "# maximum_filled_hole_m2 filled (ASSUMPTION Q3); Z = erode(fill(R), walk_m) minus the walk_m buffer of the ways with",
    "# a free public side (ASSUMPTION Q1: walk_m 250), parts below minimum_island_m2 dropped (ASSUMPTION Q2). Units: m and",
    "# m2; counts are OSM elements of the regulation response. tagging_completeness = share of the kerbside street",
    "# length (highway primary to living_street) inside fill(R) whose way carries parking information. free_lots_in_core",
    "# = public car parks without fee inside Z (not free supply by the rule, which speaks of street parking; reported",
    "# only). eroded_filled_area_m2 = erode(fill(R), walk_m) before the free ways are subtracted (0 means the regulated",
    "# area is too fragmented for walk_m, not that free ways carved it). reference = the outline the core is compared",
    "# with (Braunschweig: the georeferenced ParkGO annex, a plausibility check; elsewhere the v1 polygon);",
    "# core_share_inside_reference and reference_share_covered_by_core = intersection over core and over reference;",
    "# reference_tagging_completeness = tagging completeness of the kerbside streets inside the reference outline;",
    "# largest_outline_distance_m = Hausdorff distance of the two outlines. Empty = undefined (e.g. no core).",
    "# q4_decision = pre-registered acceptance rule Q4 (core >= 1 ha and tagging completeness >= 0.60) or request_failed",
    "# (no response after all attempts: the municipality keeps its v1 polygon). role zones_from_core: an accepted core",
    "# becomes the polygons in zone_ids (applied true, geometry_source osm_fee_erosion); qa_only: recorded, v1 stays.",
    "# The owner reviews the decisions at the PR. Validated by scripts/validate_parking_zones.py (Q4 re-applied).",
    "# Counts and areas are derived from OpenStreetMap data: (c) OpenStreetMap contributors, ODbL 1.0",
    "# (https://www.openstreetmap.org/copyright).",
)


def erosion_inputs(directory, ags: str) -> tuple:
    """(QA dict, core parts in EPSG:25832) of one municipality, or (None, None) when no response exists."""
    qa_path = Path(directory) / f"{ags}_qa.json"
    if not qa_path.is_file():
        return None, None
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    if qa["ags"] != ags:
        raise SystemExit(f"{qa_path} belongs to {qa['ags']}, not {ags}")
    core = gpd.read_file(Path(directory) / f"{ags}_core.geojson")
    core = core.set_crs("EPSG:4326") if core.crs is None else core
    return qa, core.to_crs(cc.METRIC_CRS)


def last_failure(directory, ags: str) -> str:
    """The last line of overpass_failures.log for ``ags`` (time and error), or '' when there is none."""
    path = Path(directory) / "overpass_failures.log"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    matching = [line for line in lines if line.split("\t")[1:2] == [ags]]
    return matching[-1].replace("\t", " ") if matching else ""


def erosion_note(qa: dict, *, replaces: str, assignment: str = "") -> str:
    """The digitising note of an osm_fee_erosion polygon: method, parameters, counts and the QA row."""
    p = qa["parameters"]
    return (f"Rule-based core of parking cost zones v2 (lever 1, spec amendment A1; braunschweig.parking.zone_geometry, "
            f"scripts/build_parking_zones_from_osm.py --regulation): OSM ways with a paid, restricted or forbidden side "
            f"buffered {p['street_buffer_m']:.0f} m and paid or restricted car parks buffered {p['lot_buffer_m']:.0f} m "
            f"(R {qa['regulated_area_m2']:.0f} m2), holes up to {p['maximum_filled_hole_m2']:.0f} m2 filled (fill(R) "
            f"{qa['filled_area_m2']:.0f} m2), eroded by W = {p['walk_m']:.0f} m and minus the {p['walk_m']:.0f} m buffer "
            f"of the {qa['free_segments']} ways with a free public side, parts below {p['minimum_island_m2']:.0f} m2 "
            f"dropped (Z {qa['core_area_m2']:.0f} m2 in {qa['core_parts']} part{'' if qa['core_parts'] == 1 else 's'}). "
            f"Regulation response "
            f"{qa['raw_response']} of the Overpass API (OSM base {qa['osm_timestamp']}; box {qa['bbox']}): "
            f"{qa['segments']} highway ways ({qa['regulated_segments']} regulated, {qa['free_segments']} with a free "
            f"side, {qa['mixed_segments']} both), {qa['lots']} car parks; tagging completeness "
            f"{qa['tagging_completeness']:.3f} inside fill(R); acceptance rule Q4 met (Z >= 1 ha, completeness >= "
            f"0.60). " + (assignment + " " if assignment else "") + replaces
            + f" QA row {qa['ags']} of parking_zones_2026_qa.csv.")


def qa_row(ags: str, qa, *, role: str, reference: str, applied_zones, note: str, failure: str = "") -> dict:
    """One row of the committed QA table (``pz.ZONE_QA_COLUMNS``)."""
    row = {column: "" for column in pz.ZONE_QA_COLUMNS}
    row.update({"ags": ags, "name": MUNICIPALITY_NAMES[ags], "role": role, "reference": reference,
                "applied": "true" if applied_zones else "false", "zone_ids": ";".join(applied_zones), "note": note})
    if qa is None:
        row.update({"q4_decision": "request_failed",
                    "note": f"no Overpass response after all attempts ({failure or 'no failure recorded'}); the "
                            f"municipality keeps its v1 polygon. " + note})
        return row
    reference_numbers = qa.get("reference") or {}

    def number(value, digits):
        return "" if value is None else f"{value:.{digits}f}"

    row.update({"raw_response": qa["raw_response"], "osm_timestamp": qa["osm_timestamp"],
                "walk_m": number(qa["parameters"]["walk_m"], 1),
                "maximum_filled_hole_m2": number(qa["parameters"]["maximum_filled_hole_m2"], 1),
                "minimum_island_m2": number(qa["parameters"]["minimum_island_m2"], 1),
                "tagging_completeness": number(qa["tagging_completeness"], 6),
                "core_share_inside_reference": number(reference_numbers.get("core_share_inside_reference"), 4),
                "reference_share_covered_by_core": number(reference_numbers.get("reference_share_covered"), 4),
                "reference_tagging_completeness": number(reference_numbers.get("tagging_completeness_inside"), 6),
                "largest_outline_distance_m": number(reference_numbers.get("largest_outline_distance_m"), 1),
                "q4_decision": qa["decision"]})
    for column in ("segments", "regulated_segments", "free_segments", "mixed_segments", "separate_segments", "lots",
                   "free_lots", "free_lots_in_core", "core_parts"):
        row[column] = str(int(qa[column]))
    for column in ("free_lot_area_in_core_m2", "regulated_area_m2", "filled_area_m2", "eroded_filled_area_m2",
                   "core_area_m2"):
        row[column] = number(qa[column], 1)
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ia-ib", required=True)
    parser.add_argument("--affine", required=True)
    parser.add_argument("--fee-islands", required=True, help="GeoJSON of digitise_stadthalle_fee_islands.py")
    parser.add_argument("--raw-overpass", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--municipalities", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--erosion-dir", help="v2 lever 1: directory with <ags>_core.geojson and <ags>_qa.json")
    parser.add_argument("--reference-outline", help="v2 lever 1: georeferenced ParkGO annex zones (Braunschweig)")
    parser.add_argument("--qa-out", help="v2 lever 1: the QA table to write (parking_zones_2026_qa.csv)")
    args = parser.parse_args(argv)
    if args.erosion_dir and not (args.reference_outline and args.qa_out):
        raise SystemExit("--erosion-dir needs --reference-outline and --qa-out")
    zones = []

    def add(zone_id, ags, geometry, geometry_source, source_url, note, *, walk_m=None, osm_timestamp=None,
            source_date=DIGITISED_ON, digitised_on=DIGITISED_ON, minimum_part_m2=MINIMUM_PART_M2):
        zones.append({"zone_id": zone_id, "_ags": ags, "geometry": geometry, "geometry_source": geometry_source,
                      "source_url": source_url, "source_date": source_date, "digitised_on": digitised_on,
                      "digitising_note": note, "unavoidable_walk_m": walk_m, "osm_timestamp": osm_timestamp,
                      "_minimum_part_m2": minimum_part_m2})

    # ---------------------------------------------------------------- v2 lever 1: rule-based cores (issue #436)
    erosion = {}
    if args.erosion_dir:
        for ags in list(EROSION_ZONES_FROM_CORE) + list(EROSION_QA_ONLY):
            qa, core = erosion_inputs(args.erosion_dir, ags)
            failure = last_failure(args.erosion_dir, ags)
            if qa is None and not failure:
                raise SystemExit(f"{ags}: neither {ags}_qa.json nor a failed request in overpass_failures.log; run "
                                 "scripts/build_parking_zones_from_osm.py --regulation for it first")
            erosion[ags] = {"qa": qa, "core": core, "failure": failure}
        annex_zones = gpd.read_file(args.reference_outline).to_crs(cc.METRIC_CRS).set_index("zone")

    def accepted_core(ags):
        """Union of the core parts of an accepted zones_from_core municipality, else None."""
        entry = erosion.get(ags)
        if not entry or entry["qa"] is None or entry["qa"]["decision"] != "accepted" or ags not in EROSION_ZONES_FROM_CORE:
            return None
        return unary_union(list(entry["core"].geometry))

    def add_erosion(zone_id, ags, geometry, source_url, *, replaces, assignment=""):
        qa = erosion[ags]["qa"]
        add(zone_id, ags, geometry, pz.EROSION_GEOMETRY_SOURCE, source_url,
            erosion_note(qa, replaces=replaces, assignment=assignment), walk_m=float(qa["parameters"]["walk_m"]),
            osm_timestamp=qa["osm_timestamp"], source_date=EROSION_DIGITISED_ON, digitised_on=EROSION_DIGITISED_ON,
            minimum_part_m2=float(qa["parameters"]["minimum_island_m2"]))

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
        core = accepted_core(ags)
        if core is not None:
            add_erosion(zone_id, ags, core, url, replaces=(
                "Replaces the v1 centre approximation of 2026-09-29 (" + what + ", buffered 40 m, from the car network "
                "of the local MATSim scenario because the fee request of that day failed with HTTP 504); the tariff row "
                "is unchanged."))
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

    # ---------------------------------------------------------------- Braunschweig zone II and southern Ia (A2, v2)
    bs_core = accepted_core(BS_AGS)
    bs_inside_annex = {}
    if bs_core is not None:
        for zone_id, annex_zone in BS_EROSION_ZONES.items():
            label = {"ia": "Ia", "ii": "II"}[annex_zone]
            assigned = bs_core.intersection(annex_zones.loc[annex_zone, "geometry"])
            bs_inside_annex[zone_id] = (label, assigned.area)
            add_erosion(zone_id, BS_AGS, assigned, PARKGO_URL,
                        replaces=("New in v2 (spec amendment A2): unzoned in v1, where the annex could not be "
                                  "georeferenced to an RMS of 10 m or better."),
                        assignment=(f"Tariff zone {label} of the ParkGO (sec. 1(2), sec. 2(1)): the core inside the "
                                    f"annex outline of zone {label} ({ANNEX_REFERENCE}; scripts/curation/"
                                    "parking_zones_2026/extract_parkgo_annex_zones.py); the georeference is a "
                                    "plausibility check, so near a zone border the assignment carries its uncertainty "
                                    "of about 27 m. Every v1 zone (Ia and Ib of the city's overview map, BgA car parks, "
                                    "Stadthalle concept, TU campus areas) is cut out by precedence; the core outside "
                                    "the annex zones Ia and II is not zoned."))

    # ---------------------------------------------------------------- precedence, containment, output
    by_id = {z["zone_id"]: z for z in zones}
    order = PRECEDENCE + [z["zone_id"] for z in zones if z["zone_id"] not in PRECEDENCE]
    taken, trims = None, []
    for zone_id in order:
        zone = by_id[zone_id]
        geometry = zone["geometry"].buffer(0)
        eroded_zone = zone["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE
        if eroded_zone:
            # A core is simplified BEFORE the cut, so that the cut stays exact: simplifying a cut edge afterwards
            # moves it by up to SIMPLIFY_M and lets a large core overlap its neighbours along long shared edges.
            geometry = geometry.simplify(SIMPLIFY_M, preserve_topology=True).buffer(0)
        if taken is not None and geometry.intersects(taken):
            overlap = geometry.intersection(taken).area
            if overlap > 0.5 or (eroded_zone and overlap > 0):
                geometry = geometry.difference(taken)
                trims.append((zone_id, round(overlap, 1)))
        geometry = cc.largest_parts(geometry.buffer(0), zone["_minimum_part_m2"])
        if not eroded_zone:
            geometry = geometry.simplify(SIMPLIFY_M, preserve_topology=True)
        zone["geometry"] = geometry
        if not geometry.is_empty:
            taken = geometry if taken is None else taken.union(geometry)
    print("trimmed by precedence:", trims)
    emptied = [z["zone_id"] for z in zones if z["geometry"].is_empty]
    if [zone_id for zone_id in emptied if by_id[zone_id]["geometry_source"] != pz.EROSION_GEOMETRY_SOURCE]:
        raise SystemExit(f"zones emptied by the precedence cuts: {emptied}")
    zones = [z for z in zones if not z["geometry"].is_empty]
    if emptied:
        print("osm_fee_erosion zones without a part of the minimum island size after the cuts (not written):", emptied)
    bs_note = ""
    if bs_core is not None:
        bs_final = unary_union([z["geometry"] for z in zones if z["_ags"] == BS_AGS])
        written = {z["zone_id"]: z["geometry"].area for z in zones if z["zone_id"] in BS_EROSION_ZONES}
        bs_note = (f"Braunschweig core {bs_core.area:.0f} m2, of it inside the annex zone " + ", zone ".join(
            f"{label} {area:.0f} m2" for label, area in bs_inside_annex.values()) + "; after the cuts by the v1 zones and "
            "the minimum island size " + ", ".join(
            f"{zone_id} {written[zone_id]:.0f} m2" if zone_id in written else f"{zone_id} not written (no part left)"
            for zone_id in BS_EROSION_ZONES) + f"; {bs_core.difference(bs_final).area:.0f} m2 of the core lie outside "
            "every zone of the release and are not zoned")
        print(bs_note)
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
    frame = gpd.GeoDataFrame([{k: v for k, v in z.items() if not k.startswith("_")} for z in zones],
                             geometry="geometry", crs=cc.METRIC_CRS)
    columns = ["zone_id", "geometry_source", "source_url", "source_date", "digitised_on", "digitising_note"]
    has_erosion = bool((frame["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE).any())
    if has_erosion:
        columns += list(pz.EROSION_PROVENANCE_COLUMNS)
    frame = frame[columns + ["geometry"]]
    out = Path(args.out)
    # RFC 7946 allows foreign members; GDAL writes them at the top level and GeoJSON readers ignore them.
    members = json.dumps({"license": LICENSE_V2 if has_erosion else LICENSE,
                          "attribution": ATTRIBUTION_V2 if has_erosion else ATTRIBUTION})
    frame.to_crs("EPSG:4326").to_file(out, driver="GeoJSON", COORDINATE_PRECISION=7, FOREIGN_MEMBERS_COLLECTION=members)
    print(f"written {out} with {len(frame)} zones")
    if args.erosion_dir:
        write_qa_table(args.qa_out, erosion, zones, by_id, bs_note)
    return 0


def write_qa_table(path, erosion: dict, zones: list, by_id: dict, bs_note: str) -> None:
    """One row per curated municipality (``EROSION_ZONES_FROM_CORE`` then ``EROSION_QA_ONLY``) -> ``path``."""
    rows = []
    for ags in list(EROSION_ZONES_FROM_CORE) + list(EROSION_QA_ONLY):
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
            reference = f"v1 polygon {zone_id} ({by_id[zone_id]['geometry_source']})"
        notes = []
        if qa is not None:
            notes.append("Q4 " + (qa["decision"] if not qa["decision_reason"] else
                                  f"{qa['decision']}: {qa['decision_reason']}"))
            outside = (qa.get("reference") or {}).get("paid_segments_outside")
            if outside:
                notes.append(f"{outside} paid ways ({qa['reference']['paid_length_outside_m']:.0f} m) outside the "
                             "reference outline")
            if qa["separate_segments"]:
                notes.append(f"{qa['separate_segments']} ways with a side mapped separately (parking:<side>=separate, "
                             f"forbidden by the rule), {qa['free_street_side_lots_in_core']} street-side car parks "
                             "without fee inside Z")
        if role == "qa_only":
            notes.append(f"QA only (plan Task 1 Step 4): the v1 polygon {zone_id} stays")
        elif ags == BS_AGS and bs_note:
            notes.append(bs_note)
        elif ags == BS_AGS and qa is not None:
            notes.append("ParkGO zone II and the southern part of zone Ia stay unzoned")
        elif applied:
            notes.append(f"the core replaces the v1 centre approximation {zone_id}")
        elif qa is not None:
            notes.append(f"the v1 centre approximation {zone_id} stays")
        rows.append(qa_row(ags, qa, role=role, reference=reference, applied_zones=applied, note="; ".join(notes) + ".",
                           failure=entry["failure"]))
    table = pd.DataFrame(rows, columns=list(pz.ZONE_QA_COLUMNS))
    text = "\n".join(QA_HEADER) + "\n" + table.to_csv(index=False, lineterminator="\n")
    text.encode("ascii")
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    print(f"written {path} with {len(table)} rows: " + ", ".join(
        f"{row['ags']} {row['q4_decision']}{' applied ' + row['zone_ids'] if row['applied'] == 'true' else ''}"
        for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
