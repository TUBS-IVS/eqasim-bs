"""Regional evidence package of 2026-10-07 in the zone curation (parking cost zones v2, spec Amendment D, issue #436).

Not part of the pipeline and never run by synpp: ``assemble_parking_zones.py --regional-dir`` calls this step after the
municipal step of Amendment C. Input: the owner-supplied consolidated evidence package
``Regional_Parkdaten_Belege_2026-10-07.zip``, copied unchanged into the gitignored
``eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-07/``. ``load_package`` checks it against the
SHA-256 pinned in ``PACKAGE_SHA256`` (also in the data record parking_zones_2026) before any layer is read and reads the
GeoPackage layers and the tariff rules straight from the zip (GDAL ``/vsizip/``, ``zipfile``), so no extracted copy
exists that could drift. Every layer must be EPSG:25832 with valid, non-empty geometries (an invalid geometry stops the
step; nothing is repaired), and the attributes the provenance wording states are checked against the layers. Per layer
the step logs how many features it used, dropped (with the reason) and repaired (always 0).

* D1, ``bga_zones``: the five BgA car parks of layer ``bs_bga_parkflaechen`` (digitised from the annex maps of the
  Amtsblatt der Stadt Braunschweig Nr. 16 of 2022; working uncertainty about 5 m) replace the OSM-derived polygons of
  the four lots that stay and add ``bs_bga_willy_brandt_platz``; ``bs_bga_kannengiesserstrasse`` is removed (a pocket
  park since April 2026, layer ``referenz_bs_aufgegeben`` keeps the historical outline as a reference).
* D1, ``campus_zones`` (ruling R-4a-8, owner decision of 2026-10-07): one TU campus zone per campus of layer
  ``tu_kamera_detektionszonen``, the UNION of the campus grounds of the v1 release (an OSM amenity=university outline: the
  destination area, where the buildings and so the activities are; the International House zone of v1, two TU car parks,
  is merged into the Langer Kamp campus) and the red camera detection zones of its campus map (the paid car parks, where
  the tickets are checked; the layer states they are no parking footprints, and they do not contain the buildings). The
  grounds are the v1 RELEASE polygons, so they never take area from a street zone; the detection zones win over the street
  zones (ruling R-4a-1). A campus joins the release only where the GB3 pages state ticketing (``TU_CAMPUSES``; Bevenroder
  Strasse is dropped and logged). Volkmaroder Strasse has no v1 zone and no OSM university outline at the site (``TU_NO_
  GROUNDS``): its zone is the detection zone alone. Nested boundaries (Beethovenstrasse) use the outer boundary, which the
  union of nested polygons is, and are recorded.
* D3, ``single_sites``: each paid car park (its polygon, or its source point where no polygon exists) or paid street
  section of Bad Harzburg, Seesen, Braunlage and Goslar (the three 1 EUR/h car parks, ruling R-D3-a) is a zone of its
  own, the area within ``SITE_BUFFER_M`` of it (ASSUMPTION C-a, as for the Wolfsburg sections; a point is a navigation
  coordinate and its lot extent unknown). Where the 50 m areas of two sites overlap, the nearer source decides, ties go
  to the higher tariff (``split_single_sites``, the nearest-section split of ``municipal_zones``).
* ``tariff_evidence`` derives the tariff values of the new rows from the package's tariff rules and ``check_tariff_rows``
  checks the hand-written tariff rows against them (the rate, billing unit and fee window the sources state, the
  assumptions F1, D3-a and D3-b for what they do not).
* ``qa_rows`` adds the comparisons of the new polygons with their references and every precedence cut to the municipal
  QA table (layout ``braunschweig.parking.municipal_zone_qa``).

CRS: EPSG:25832 throughout, areas in m2, distances in m.
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union

import curation_common as cc
import municipal_zones as mz

REGIONAL_DIGITISED_ON = "2026-10-07"
PACKAGE_NAME = "Regional_Parkdaten_Belege_2026-10-07"
PACKAGE_FILE = f"{PACKAGE_NAME}.zip"
#: SHA-256 of the owner's package (also in the data record parking_zones_2026 and in MANIFEST.md next to the zip).
PACKAGE_SHA256 = "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7"
GPKG_MEMBER = f"{PACKAGE_NAME}/daten/Regional_Parkdaten.gpkg"
RULES_MEMBER = f"{PACKAGE_NAME}/daten/tariff_rules.json"
#: The GeoPackage layers the step reads. ``vier_staedte_parking_locations`` (navigation points of the Bad Harzburg
#: sites) is not read: every Bad Harzburg site has an outline, the primary geometry.
PACKAGE_LAYERS = ("bs_bga_parkflaechen", "referenz_bs_aufgegeben", "tu_kamera_detektionszonen", "tu_kartenflaechen",
                  "vier_staedte_parking_areas", "vier_staedte_street_references", "neue_orte_parkstandorte",
                  "gos_1eur_parkplatzflaechen")

# ---------------------------------------------------------------- municipalities and parameters
BS_AGS, GS_AGS = mz.BS_AGS, mz.GS_AGS
BH_AGS, SE_AGS, BR_AGS = "03153002", "03153012", "03153016"
#: The gemeindefreies Gebiet Harz (Landkreis Goslar) of the pipeline's municipality polygons (VG250 as cached).
HARZ_AGS = "03153504"
#: ASSUMPTION C-a (spec Amendment C2, applied to single sites by Amendment D3): a destination within this distance of a
#: paid car park or paid street section parks there, m.
SITE_BUFFER_M = mz.SECTION_BUFFER_M
#: Radius of the proxy polygon that stands for a point or a street line in the nearest-source split, m: the split of
#: ``municipal_zones`` samples polygon boundaries, so a point or a line is sampled through its 1 mm buffer; each zone is
#: clipped to the exact buffer of its source afterwards.
SPLIT_PROXY_M = 1e-3
#: Parts of a split zone below this area, m2, are the micrometre slivers that the 1e-6 m grid of the split leaves along
#: its line; they carry no area and are dropped (the precedence step drops parts below 20 m2 anyway).
SPLIT_SLIVER_M2 = 1e-3
BGA_POSITION_UNCERTAINTY_M = 5.0
#: The zone-II layer comparisons need no new parameter; the minimum share of a detection union the yellow parking
#: areas of the same map must reach inside it for the QA cross-check to read 'consistent' (reported, never enforced).
YELLOW_AREAS_CONSISTENCY = 0.99

# ---------------------------------------------------------------- provenance wording (neutral, licence status stated)
BGA_PROVENANCE = ("Stadt Braunschweig, Amtsblatt 2022 Nr. 16 (annex maps of the BgA Entgeltordnung B 660); digitised "
                  "(owner-supplied package 2026-10-07); working accuracy 5 m; base map Open GeoData dl-de/by-2-0")
TU_PROVENANCE = ("TU Braunschweig, GB3 Parkbereiche campus maps (retrieved 2026-10-02; (c) d&d design & distribution); "
                 "explicit consent for reuse of TU graphics not obtained; used by owner decision 2026-10-07")
GOSLAR_PROVENANCE = ("Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22); open reuse licence not "
                     "verified; used by owner decision 2026-10-07")
OSM_SITES_PROVENANCE = ("(c) OpenStreetMap contributors, ODbL 1.0: the outlines of the Bad Harzburg car parks and the "
                        "street Am Markt in Seesen")
BRAUNLAGE_PROVENANCE = ("Stadt Braunlage, official tourism coordinates of the car parks (source points, not outlines)")
#: The campus grounds of the TU zones (ruling R-4a-8) are the OSM outlines of the v1 release.
TU_GROUNDS_PROVENANCE = ("(c) OpenStreetMap contributors, ODbL 1.0 (the OSM university outlines and the OSM car parks at "
                         "the International House of the v1 release)")

# ---------------------------------------------------------------- zones of the step
BGA_ABANDONED_ZONE = "bs_bga_kannengiesserstrasse"
BGA_ZONE_IDS = ("bs_bga_markthalle", "bs_bga_an_der_martinikirche", "bs_bga_jodutenstrasse_klint", "bs_bga_suedstrasse",
                "bs_bga_willy_brandt_platz")
#: campus label of the package layer (ASCII form) -> (release zone id, the GB3 statement that the campus is ticketed).
TU_CAMPUSES = {
    "Zentralcampus": ("tu_zentralcampus", "listed under 'Aktuell ticketpflichtige Campusbereiche'"),
    "Campus Nord": ("tu_campus_nord", "listed under 'Aktuell ticketpflichtige Campusbereiche'"),
    "Beethovenstrasse": ("tu_campus_ost_beethovenstrasse", "listed under 'Aktuell ticketpflichtige Campusbereiche' "
                                                            "('Campus Ost - Langer Kamp und Beethovenstrasse')"),
    "Langer Kamp": ("tu_campus_ost_langer_kamp", "listed under 'Aktuell ticketpflichtige Campusbereiche' ('Campus Ost - "
                                                 "Langer Kamp und Beethovenstrasse')"),
    "Forschungsflughafen": ("tu_forschungsflughafen", "'Ab 1. Oktober 2026: Campus Forschungsflughafen'"),
    "Volkmaroder Strasse": ("tu_campus_volkmaroder_strasse", "'Ab 1. Oktober 2026: ... Standort Volkmaroder Strasse'"),
}
#: Campuses of the layer that are NOT zones, with the reason (logged, never silent).
TU_NOT_ZONED = {
    "Bevenroder Strasse": "no GB3 page states ticketing for this campus (it is neither among the 'Aktuell "
                          "ticketpflichtige Campusbereiche' nor among the campuses integrated from 2026-10-01)",
}
TU_ZONE_IDS = tuple(zone_id for zone_id, _ in TU_CAMPUSES.values())
#: v1 zones that the step removes, with the zone that takes over their area.
TU_MERGED_ZONE = "tu_international_house"
TU_MERGED_INTO = "tu_campus_ost_langer_kamp"
#: Campus zone id -> the v1 zones whose RELEASE polygons are its campus grounds (ruling R-4a-8: the OSM amenity=university
#: outlines of the v1 release, for Langer Kamp with the two TU car parks at the International House). Volkmaroder Strasse
#: has none.
TU_GROUNDS = {
    "tu_zentralcampus": ("tu_zentralcampus",),
    "tu_campus_nord": ("tu_campus_nord",),
    "tu_campus_ost_beethovenstrasse": ("tu_campus_ost_beethovenstrasse",),
    "tu_campus_ost_langer_kamp": ("tu_campus_ost_langer_kamp", TU_MERGED_ZONE),
    "tu_forschungsflughafen": ("tu_forschungsflughafen",),
}
TU_GROUND_ZONE_IDS = tuple(dict.fromkeys(zone_id for zone_ids in TU_GROUNDS.values() for zone_id in zone_ids))
#: Why a ticketed campus has no campus grounds (a finding of the curation, checked on 2026-10-07).
TU_NO_GROUNDS = {
    "tu_campus_volkmaroder_strasse": (
        "the v1 release holds no zone for this site, and no OSM amenity=university outline lies there (checked on "
        "2026-10-07: the v1 Overpass response of Braunschweig, OSM base 2026-09-29T05:10Z, holds 21 amenity=university "
        "features, the nearest outline 1.1 km from the detection zone; the pinned Geofabrik extract "
        "niedersachsen-260929, OSM snapshot 2026-09-29T20:22:51Z, holds 6 amenity=university multipolygons in a box of "
        "4 km around the detection zone, the nearest one 1.1 km away), so the zone is the detection zone alone"),
}
#: The closing remark of the v1 note of a TU zone, which the detection zones supersede: the v1 outline is no longer the
#: approximation of an area 'inside' which the detection zones lie, it is the campus grounds that the zone keeps.
V1_CAMPUS_REMARK = " The outline approximates the campus whose TU car parks are ticketed"
#: The TU GB3 page that states which campuses are ticketed (retrieved 2026-09-29).
TU_PAGE_URL = "https://www.tu-braunschweig.de/gb3/parkraumbewirtschaftung"
NESTED_CAMPUS = "Beethovenstrasse"

#: Single paid sites (D3). ``layer``/``key``: where the geometry comes from (key column of that layer); ``ags``: the
#: municipality of the tariff row. The order is the order of the zones in the release.
SINGLE_SITES = (
    {"zone_id": "gs_parkplatz_baeringerstrasse", "ags": GS_AGS, "layer": "gos_1eur_parkplatzflaechen",
     "key_column": "facility_id", "key": "GOS_1EUR_1", "rule_ids": ("goslar_baeringer_REV_01",)},
    {"zone_id": "gs_parkplatz_klubgartenstrasse_zob", "ags": GS_AGS, "layer": "gos_1eur_parkplatzflaechen",
     "key_column": "facility_id", "key": "GOS_1EUR_18", "rule_ids": ("goslar_zob_REV_01",)},
    {"zone_id": "gs_parkplatz_glockengiesserstrasse", "ags": GS_AGS, "layer": "gos_1eur_parkplatzflaechen",
     "key_column": "facility_id", "key": "GOS_1EUR_5", "rule_ids": ("goslar_glocken_REV_01",)},
    {"zone_id": "bh_sole_therme", "ags": BH_AGS, "layer": "vier_staedte_parking_areas", "key_column": "record_id",
     "key": "bh_sole_therme", "rule_ids": ("bh_ordinary_ceiling",)},
    {"zone_id": "bh_kurpark", "ags": BH_AGS, "layer": "vier_staedte_parking_areas", "key_column": "record_id",
     "key": "bh_kurpark", "rule_ids": ("bh_ordinary_ceiling",)},
    {"zone_id": "bh_grossparkplatz", "ags": BH_AGS, "layer": "vier_staedte_parking_areas", "key_column": "record_id",
     "key": "bh_grossparkplatz", "rule_ids": ("bh_ordinary_ceiling",)},
    {"zone_id": "bh_burgberg", "ags": BH_AGS, "layer": "vier_staedte_parking_areas", "key_column": "record_id",
     "key": "bh_burgberg", "rule_ids": ("bh_ordinary_ceiling",)},
    {"zone_id": "bh_berliner_platz", "ags": BH_AGS, "layer": "vier_staedte_parking_areas", "key_column": "record_id",
     "key": "bh_berliner_platz", "rule_ids": ("bh_ordinary_ceiling",)},
    {"zone_id": "se_am_markt", "ags": SE_AGS, "layer": "vier_staedte_street_references", "key_column": "record_id",
     "key": "se_am_markt", "rule_ids": ("se_ordinary",)},
    {"zone_id": "br_hexenritt", "ags": BR_AGS, "layer": "neue_orte_parkstandorte", "key_column": "id",
     "key": "br_hexenritt", "rule_ids": tuple(f"br_hexenritt_steps_br_hexenritt_{band}" for band in range(5))},
    {"zone_id": "br_wurmberg", "ags": BR_AGS, "layer": "neue_orte_parkstandorte", "key_column": "id",
     "key": "br_wurmberg", "rule_ids": ("br_public_br_wurmberg",)},
)
D3_ZONE_IDS = tuple(site["zone_id"] for site in SINGLE_SITES)
#: Records of ``vier_staedte_parking_areas`` that are NOT zones, with the reason (a zone needs a sourced paid tariff).
AREAS_NOT_ZONED = {
    "se_bahnhofsplatz": "fee status 'historical_paid_unconfirmed': a 2019 report names a fee, the current tariff and "
                        "the affected part are not confirmed",
    "se_parkhaus": "fee status 'free_conditional': a private garage, free on the upper levels and for 2 h below",
    "ko_p3": "fee status 'conflicting': the city page says paid without a price, newer evidence says disc parking",
    "sc_burgplatz": "fee status 'free_osm': OSM states fee=no and the city confirms free city car parks",
}
#: The Bad Harzburg sites, the car parks the tourism page names (BH02).
BAD_HARZBURG_SITES = ("bh_sole_therme", "bh_kurpark", "bh_grossparkplatz", "bh_burgberg", "bh_berliner_platz")
BAD_HARZBURG_PAGE_URL = "https://www.bad-harzburg.de/service/parkmoeglichkeiten"
SEESEN_PAGE_URL = ("https://www.stadtverwaltung-seesen.de/B%C3%BCrger/Rathaus/A-Z-Dienstleistungen/Parken-in-Seesen.php"
                   "?FID=235.166.1&ModID=10")
GOSLAR_SERVICE_URL = "https://www.meingoslar.de/service"

#: A zone whose outline lies in another municipality than its tariff row names, with the reason; the assembly then
#: requires the zone to lie inside the two municipalities together (``assemble_parking_zones.
#: check_municipality_containment``).
CONTAINMENT_EXCEPTIONS = {
    "bh_grossparkplatz": (HARZ_AGS, "the pipeline's municipality polygons (VG250 as cached) place the outline of the car "
                                    "park in the gemeindefreies Gebiet Harz (Landkreis Goslar), 13 m beyond the boundary "
                                    "of Bad Harzburg, so the 50 m area around it crosses that boundary; the tariff is the "
                                    "town's ParkGO and the town's tourism page lists the car park, so the tariff row "
                                    "names Bad Harzburg"),
}

# ---------------------------------------------------------------- tariff assumptions of the new rows
#: ASSUMPTION F1 fee windows of the single sites whose sources state none (decimal hours of the weekday), with the basis.
F1_WINDOWS = {
    "bs_bga_willy_brandt_platz": (9.0, 20.0, "as at the directory-listed BgA car parks Markthalle and Suedstrasse"),
    "bh_sole_therme": (8.0, 18.0, "the window of the 2022 brochure for the outlying car parks"),
    "bh_kurpark": (8.0, 18.0, "the window of the 2022 brochure for the outlying car parks"),
    "bh_grossparkplatz": (8.0, 18.0, "the window of the 2022 brochure for the outlying car parks"),
    "bh_burgberg": (8.0, 18.0, "the window of the 2022 brochure for the outlying car parks"),
    "bh_berliner_platz": (8.0, 18.0, "the window of the 2022 brochure for the outlying car parks"),
    "br_hexenritt": (9.0, 18.0, "the generic window of the Goslar zone 1 row"),
    "br_wurmberg": (9.0, 18.0, "the generic window of the Goslar zone 1 row"),
}
#: ASSUMPTION D3-a (ruling R-4a-3): the three Goslar 1 EUR/h car parks bill per started half hour, the unit of the
#: city's ParkGO (sec. 1(2)); the service page states the rate per hour and no unit.
GOSLAR_LOT_BILLING_UNIT_MIN = 30
BGA_BILLING_UNIT_MIN = 1
#: ASSUMPTION R2-a: the new rows whose ``resident_permits_valid`` is false, because no source states that resident permits
#: are valid there (the logic of the separately operated BgA car parks); every other new row leaves the cell empty, the
#: default of its zone type (valid on streets).
RESIDENT_PERMITS_NOT_VALID = ("bs_bga_willy_brandt_platz", "gs_parkplatz_klubgartenstrasse_zob")


# ---------------------------------------------------------------- package
def _hours(text) -> float:
    """Decimal hours of a 'HH:MM' clock text (24:00 allowed)."""
    hours, minutes = str(text).split(":")
    return int(hours) + int(minutes) / 60.0


#: Typographic characters of the package's names with a plain ASCII equivalent (a dash or a quotation mark carries no
#: letter); every other character without an ASCII form still raises in ``curation_common.ascii_transliteration``.
#: En dash, em dash, single and double curly quotation marks, and the no-break space.
TYPOGRAPHY_TO_ASCII = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"',
                                     "\u201d": '"', "\u00a0": " "})


def _ascii(text) -> str:
    return cc.ascii_transliteration(str(text).translate(TYPOGRAPHY_TO_ASCII))


def _read_zip_layer(zip_path: Path, layer: str) -> gpd.GeoDataFrame:
    frame = gpd.read_file(f"/vsizip/{zip_path.resolve().as_posix()}/{GPKG_MEMBER}", layer=layer)
    if frame.crs is None or frame.crs.to_epsg() != 25832:
        raise SystemExit(f"{zip_path.name} layer {layer}: CRS {frame.crs} is not EPSG:25832, as the package states")
    if frame.empty or not frame.geometry.is_valid.all() or frame.geometry.is_empty.any():
        raise SystemExit(f"{zip_path.name} layer {layer}: empty, or invalid or empty geometries; the step repairs "
                         "nothing")
    return frame


def _by_key(frame: gpd.GeoDataFrame, column: str) -> gpd.GeoDataFrame:
    keys = frame[column].astype(str)
    duplicated = sorted(set(keys[keys.duplicated()]))
    if duplicated:
        raise SystemExit(f"layer key {column}: duplicate value(s) {duplicated}")
    return frame.set_index(keys)


def _check_attributes(layers: dict) -> None:
    """Refuse (``SystemExit``) a package whose attributes contradict what the provenance wording and this step assume."""
    bga = _by_key(layers["bs_bga_parkflaechen"], "facility_id")
    if sorted(bga.index) != sorted(BGA_ZONE_IDS):
        raise SystemExit(f"bs_bga_parkflaechen holds {sorted(bga.index)}; the step needs exactly {sorted(BGA_ZONE_IDS)}")
    accuracy = sorted({float(value) for value in bga["estimated_position_uncertainty_m"]})
    if accuracy != [BGA_POSITION_UNCERTAINTY_M]:
        raise SystemExit(f"bs_bga_parkflaechen: estimated_position_uncertainty_m {accuracy}; the recorded provenance "
                         f"states a working uncertainty of {BGA_POSITION_UNCERTAINTY_M:.0f} m")
    abandoned = _by_key(layers["referenz_bs_aufgegeben"], "facility_id")
    if list(abandoned.index) != [BGA_ABANDONED_ZONE] or bool(abandoned["active_parking"].iloc[0]):
        raise SystemExit(f"referenz_bs_aufgegeben must hold exactly the inactive lot {BGA_ABANDONED_ZONE}, found "
                         f"{list(abandoned.index)} (active_parking {list(abandoned['active_parking'])})")
    detection = layers["tu_kamera_detektionszonen"]
    campuses = {_ascii(campus) for campus in detection["campus"]}
    unknown = sorted(campuses - set(TU_CAMPUSES) - set(TU_NOT_ZONED))
    if unknown:
        raise SystemExit(f"tu_kamera_detektionszonen: campus(es) {unknown} have no ticketing statement in TU_CAMPUSES or "
                         "TU_NOT_ZONED; read the GB3 pages and add them before the step may use or drop them")
    missing = sorted((set(TU_CAMPUSES) | set(TU_NOT_ZONED)) - campuses)
    if missing:
        raise SystemExit(f"tu_kamera_detektionszonen has no detection zone for {missing}")
    if bool(detection["parking_footprint"].astype(bool).any()) or (detection["buffer_m"].astype(float) != 0.0).any():
        raise SystemExit("tu_kamera_detektionszonen: the layer must hold detection zones without a buffer (buffer_m 0, "
                         "parking_footprint false), as the provenance wording states")
    yellow = {_ascii(campus) for campus in layers["tu_kartenflaechen"]["campus"]}
    if not yellow <= campuses:
        raise SystemExit(f"tu_kartenflaechen names campuses without a detection zone: {sorted(yellow - campuses)}")
    areas = _by_key(layers["vier_staedte_parking_areas"], "record_id")
    missing = sorted(set(BAD_HARZBURG_SITES) - set(areas.index))
    if missing:
        raise SystemExit(f"vier_staedte_parking_areas has no outline for the Bad Harzburg site(s) {missing}")
    not_paid = sorted(site for site in BAD_HARZBURG_SITES if areas.loc[site, "fee_status"] != "paid")
    if not_paid:
        raise SystemExit(f"vier_staedte_parking_areas: Bad Harzburg site(s) {not_paid} are not fee_status paid")
    unexpected = sorted(set(areas.index) - set(BAD_HARZBURG_SITES) - set(AREAS_NOT_ZONED))
    if unexpected:
        raise SystemExit(f"vier_staedte_parking_areas holds record(s) {unexpected} that are neither a zone nor listed in "
                         "AREAS_NOT_ZONED with a reason")
    for layer, columns in (("vier_staedte_parking_areas", ("max_stay_minutes", "osm_tags_json")),
                           ("vier_staedte_street_references", ("max_stay_ordinance_minutes", "max_stay_webpage_minutes"))):
        absent = [column for column in columns if column not in layers[layer].columns]
        if absent:
            raise SystemExit(f"{layer} lacks the column(s) {absent}, which the notes of the tariff rows are checked "
                             "against (stated maximum stays, OSM charge tags)")
    street = _by_key(layers["vier_staedte_street_references"], "record_id")
    if list(street.index) != ["se_am_markt"] or street["fee_status"].iloc[0] != "paid":
        raise SystemExit(f"vier_staedte_street_references must hold the paid street se_am_markt, found {list(street.index)}")
    points = _by_key(layers["neue_orte_parkstandorte"], "id")
    if sorted(points.index) != ["br_hexenritt", "br_wurmberg"] or (points["fee_status"] != "confirmed_paid").any():
        raise SystemExit(f"neue_orte_parkstandorte must hold the confirmed paid sites br_hexenritt and br_wurmberg, found "
                         f"{list(points.index)}")
    goslar = _by_key(layers["gos_1eur_parkplatzflaechen"], "facility_id")
    if sorted(goslar.index) != ["GOS_1EUR_1", "GOS_1EUR_18", "GOS_1EUR_5"]:
        raise SystemExit(f"gos_1eur_parkplatzflaechen holds {sorted(goslar.index)}; the step needs the three 1 EUR/h car "
                         "parks")
    edits = {str(value)[:10] for value in goslar["geometry_data_edit_date"]}
    if edits != {mz.GOSLAR_LAST_EDIT}:
        raise SystemExit(f"gos_1eur_parkplatzflaechen: last edit {sorted(edits)}, the recorded provenance states "
                         f"{mz.GOSLAR_LAST_EDIT}")
    if goslar["is_official_zone_boundary"].astype(bool).any():
        raise SystemExit("gos_1eur_parkplatzflaechen: a feature is flagged as an official zone boundary; the step treats "
                         "the layer as facility areas only")


def load_package(directory, expected_sha256: Optional[str] = None) -> dict:
    """The verified layers and tariff rules of the owner's package in ``directory`` (EPSG:25832).

    The package must exist as ``Regional_Parkdaten_Belege_2026-10-07.zip`` with exactly ``PACKAGE_SHA256`` (or
    ``expected_sha256``, for a synthetic test package), else ``SystemExit``: a changed package is never read. Returns
    {"file": {"file", "sha256", "bytes"}, "bga", "abandoned", "detection", "yellow", "areas", "street", "points",
    "goslar" (each a GeoDataFrame indexed by the record key), "rules" (rule_id -> rule), "ledger" (the per-layer
    accounting, filled by the selection functions)}.
    """
    path = Path(directory) / PACKAGE_FILE
    if not path.is_file():
        raise SystemExit(f"{path} missing: copy the owner's package {PACKAGE_FILE} unchanged into {path.parent}")
    sha256 = expected_sha256 or PACKAGE_SHA256
    actual = mz.file_sha256(path)
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_zones_2026); a "
                         "changed package is never read")
    layers = {layer: _read_zip_layer(path, layer) for layer in PACKAGE_LAYERS}
    _check_attributes(layers)
    with zipfile.ZipFile(path) as archive:
        rules = {rule["rule_id"]: rule for rule in json.loads(archive.read(RULES_MEMBER).decode("utf-8"))["rules"]}
    package = {"file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size},
               "bga": _by_key(layers["bs_bga_parkflaechen"], "facility_id"),
               "abandoned": _by_key(layers["referenz_bs_aufgegeben"], "facility_id"),
               "detection": layers["tu_kamera_detektionszonen"].assign(
                   campus_ascii=[_ascii(campus) for campus in layers["tu_kamera_detektionszonen"]["campus"]]),
               "yellow": layers["tu_kartenflaechen"].assign(
                   campus_ascii=[_ascii(campus) for campus in layers["tu_kartenflaechen"]["campus"]]),
               "areas": _by_key(layers["vier_staedte_parking_areas"], "record_id"),
               "street": _by_key(layers["vier_staedte_street_references"], "record_id"),
               "points": _by_key(layers["neue_orte_parkstandorte"], "id"),
               "goslar": _by_key(layers["gos_1eur_parkplatzflaechen"], "facility_id"),
               "rules": rules, "ledger": []}
    print(f"regional package verified: {path.name} ({package['file']['bytes']} bytes, SHA-256 {actual})")
    return package


def _log(package: dict, layer: str, total: int, used: list, dropped: dict, note: str = "") -> None:
    """Record and print the accounting of one layer: features, used, dropped with the reason, repaired (always 0)."""
    entry = {"layer": layer, "features": total, "used": list(used), "dropped": dict(dropped), "repaired": 0}
    package["ledger"].append(entry)
    reasons = "; ".join(f"{key}: {reason}" for key, reason in dropped.items())
    print(f"[regional] {layer}: {total} features, {len(used)} used, {len(dropped)} dropped"
          + (f" ({reasons})" if dropped else "") + ", 0 repaired (an invalid geometry stops the step)"
          + (f"; {note}" if note else ""))


# ---------------------------------------------------------------- D1: BgA car parks
def bga_zones(package: dict) -> dict:
    """zone id -> {"geometry", "uncertainty_m", "name", "area_id", "anlage", "page", "source_url", "note"} of the five
    BgA car parks of layer bs_bga_parkflaechen (D1)."""
    sha = package["file"]["sha256"]
    zones = {}
    for zone_id in BGA_ZONE_IDS:
        row = package["bga"].loc[zone_id]
        name = _ascii(row["name"])
        anlage = int(str(row["area_id"]).split("_")[1])
        area = float(row.geometry.area)
        note = (f"{BGA_PROVENANCE}. BgA car park '{name}' (Entgeltordnung B 660 of 2022-12-20, sec. 1(1)): the lot as "
                f"drawn on annex {anlage} of the Amtsblatt der Stadt Braunschweig Nr. 16 of 2022-12-29 (gazette page "
                f"{int(row['source_map_page'])}), digitised from the raster annex map at the stroke midpoint (layer "
                f"bs_bga_parkflaechen, feature {row['area_id']}, facility {zone_id} of the package "
                f"{PACKAGE_FILE}, SHA-256 {sha}); estimated position uncertainty "
                f"{float(row['estimated_position_uncertainty_m']):.0f} m (the package's working estimate, not a "
                f"confidence bound), {area:.0f} m2 before the cuts. The lot takes precedence over the area zones it "
                "overlaps (cut out of bs_zone_ia, v1 precedence) and is simplified 0.5 m.")
        zones[zone_id] = {"geometry": row.geometry, "uncertainty_m": float(row["estimated_position_uncertainty_m"]),
                          "name": name, "area_id": str(row["area_id"]), "anlage": anlage, "area_m2": area,
                          "source_url": str(row["source_url"]), "note": note}
    _log(package, "bs_bga_parkflaechen", len(package["bga"]), list(BGA_ZONE_IDS), {},
         "4 re-derived zones and 1 new zone (bs_bga_willy_brandt_platz)")
    row = package["abandoned"].loc[BGA_ABANDONED_ZONE]
    _log(package, "referenz_bs_aufgegeben", len(package["abandoned"]), [],
         {BGA_ABANDONED_ZONE: f"{row['operation_status']}: a pocket park since April 2026, the zone is removed"},
         "kept as the historical outline of the removed zone (QA reference)")
    return zones


# ---------------------------------------------------------------- D1: TU campus zones
def _ground_provenance(note: str) -> str:
    """The v1 provenance of a campus outline: the v1 note up to its closing remark about the detection zones
    (``V1_CAMPUS_REMARK``), which the detection zones of the package supersede."""
    return note.partition(V1_CAMPUS_REMARK)[0].rstrip().rstrip(".")


def campus_zones(package: dict, grounds: dict) -> dict:
    """campus zone id -> {"geometry" (the union of the campus grounds and the detection zones), "grounds" (the union of
    the v1 release polygons of the campus grounds, None for a campus without), "detection" (the union of the detection
    zones), "campus", "features", "parts", "map", "nested", "source_url", "note"}, one per campus of ``TU_CAMPUSES``;
    campuses of ``TU_NOT_ZONED`` are dropped and logged (D1).

    ``grounds`` maps the id of every v1 zone of ``TU_GROUNDS`` to {"geometry" (its v1 RELEASE polygon, so a v1 outline
    never takes area from a street zone), "note" (its v1 digitising note)}; a missing v1 zone raises. A campus of
    ``TU_GROUNDS`` is the union of its grounds and its detection zones (ruling R-4a-8); a campus without grounds
    (``TU_NO_GROUNDS``) is its detection zone alone."""
    sha = package["file"]["sha256"]
    detection = package["detection"]
    missing = sorted(set(TU_GROUND_ZONE_IDS) - set(grounds))
    if missing:
        raise SystemExit(f"the campus zones need the v1 release polygons of {missing} as campus grounds")
    zones, dropped, used = {}, {}, []
    for campus, (zone_id, statement) in TU_CAMPUSES.items():
        group = detection[detection["campus_ascii"] == campus]
        detection_union = unary_union(list(group.geometry))
        ids = ", ".join(sorted(group["feature_id"].astype(str)))
        ground_ids = TU_GROUNDS.get(zone_id, ())
        ground_union = unary_union([grounds[ground_id]["geometry"] for ground_id in ground_ids]) if ground_ids else None
        union = detection_union if ground_union is None else unary_union([ground_union, detection_union])
        parts = len(getattr(detection_union, "geoms", [detection_union]))
        nested = ""
        if campus == NESTED_CAMPUS:
            flagged = group[group["nested_boundary_semantics"].notna()]
            outer = flagged.loc[flagged.geometry.area.idxmax()]
            inside = [row for _, row in flagged.iterrows() if row["feature_id"] != outer["feature_id"]
                      and row.geometry.within(outer.geometry.buffer(1e-6))]
            nested = (f" NESTED BOUNDARIES: the map draws nested red outlines ({outer['feature_id']}, "
                      f"{outer.geometry.area:.0f} m2, and "
                      + ", ".join(f"{row['feature_id']}, {row.geometry.area:.0f} m2" for row in inside)
                      + " inside it); the package flags them 'unresolved nested red boundaries' and the map does not "
                        "say whether the inner outline excludes an area or marks a zone of its own, so the OUTER "
                        "boundary is used (spec Amendment D1) and the inner area belongs to the zone.")
        source_map = str(group["source_file"].iloc[0])
        detection_text = (
            f"the union of the {len(group)} red camera detection zone(s) {ids} of the campus map {source_map} (image "
            f"SHA-256 {group['source_sha256'].iloc[0]}, GB3 page Parkbereiche, retrieved "
            f"{str(group['source_retrieved_at'].iloc[0])[:10]}, page verified {str(group['page_verified_at'].iloc[0])[:10]}), "
            f"layer tu_kamera_detektionszonen of the package {PACKAGE_FILE} (SHA-256 {sha}); {parts} part(s), "
            f"{detection_union.area:.0f} m2")
        outline_text = (
            "The detection outlines are traced manually along the red lines in source pixels and mapped by the unchanged "
            "affine matrix of the TU package (fit residuals 5.7-16.1 m, a consistency measure, not an accuracy; "
            "'approximate; schematic source; deviations of several tens of metres possible'); entrance gaps are closed "
            "straight. They are camera detection zones (layer attribute parking_footprint false), not building outlines. ")
        if ground_union is None:
            body = (f": the paid car parks, where the tickets are checked, alone: {detection_text}. The zone has no campus "
                    f"grounds: {TU_NO_GROUNDS[zone_id]}. {outline_text}")
        else:
            provenance = " / ".join(f"{ground_id}: {_ground_provenance(grounds[ground_id]['note'])}"
                                    for ground_id in ground_ids)
            inside = detection_union.intersection(ground_union).area / detection_union.area
            body = (f" (ruling R-4a-8, owner decision 2026-10-07): the union of the campus grounds and the paid car parks. "
                    f"CAMPUS GROUNDS, the destination area, where the buildings and so the activities are "
                    f"({ground_union.area:.0f} m2): the release polygon{'s' if len(ground_ids) > 1 else ''} of the v1 "
                    f"zone{'s' if len(ground_ids) > 1 else ''} {', '.join(ground_ids)}, kept with the v1 provenance "
                    f"({provenance}). PAID CAR PARKS, where the tickets are checked: {detection_text}; "
                    f"{100.0 * inside:.1f} % of them lie inside the campus grounds. {outline_text}"
                    f"The union is {union.area:.0f} m2 before the cuts and the simplification. ")
        zones[zone_id] = {
            "geometry": union, "grounds": ground_union, "detection": detection_union, "campus": campus,
            "features": list(group["feature_id"].astype(str)), "parts": parts, "map": source_map, "nested": nested,
            "source_url": str(group["source_url"].iloc[0]),
            "note": f"{TU_PROVENANCE}. Campus '{campus}'{body}Ticketed per the GB3 page {TU_PAGE_URL}: {statement}.{nested}"}
        used += list(group["feature_id"].astype(str))
    for campus, reason in TU_NOT_ZONED.items():
        group = detection[detection["campus_ascii"] == campus]
        for feature_id in group["feature_id"].astype(str):
            dropped[feature_id] = f"{campus}: {reason}"
    _log(package, "tu_kamera_detektionszonen", len(detection), used, dropped,
         f"{len(TU_CAMPUSES)} campus zones ({len(TU_GROUNDS)} united with their campus grounds)")
    # The yellow parking areas are no zone geometry (activities sit at buildings): no feature is used and none is dropped
    # for a defect; the layer is read as a QA cross-check of the georeference only.
    _log(package, "tu_kartenflaechen", len(package["yellow"]), [], {},
         "read as a QA cross-check of the georeference only, no zone geometry")
    return zones


# ---------------------------------------------------------------- D3: single paid sites
def _site_kind(geometry) -> str:
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return "polygon"
    if geometry.geom_type in ("LineString", "MultiLineString"):
        return "street line"
    return "source point"


def single_sites(package: dict) -> dict:
    """zone id -> {"geometry" (the source geometry), "kind" ('polygon', 'street line' or 'source point'), "ags",
    "source_url", "note"} of the single paid sites of ``SINGLE_SITES`` (D3), in release order. The primary geometry is
    the site's polygon; a street section is its OSM line; a source point (a navigation coordinate, the lot extent
    unknown) is the fallback where no polygon exists. The rates of the three kinds are logged."""
    sha = package["file"]["sha256"]
    layer_frames = {"gos_1eur_parkplatzflaechen": package["goslar"], "vier_staedte_parking_areas": package["areas"],
                    "vier_staedte_street_references": package["street"], "neue_orte_parkstandorte": package["points"]}
    sites = {}
    for spec in SINGLE_SITES:
        row = layer_frames[spec["layer"]].loc[spec["key"]]
        geometry = row.geometry
        kind = _site_kind(geometry)
        head = "Single paid site (spec Amendment D3): "
        if spec["layer"] == "gos_1eur_parkplatzflaechen":
            source_url = GOSLAR_SERVICE_URL
            body = (f"the car park '{_ascii(row['name'])}' ({_ascii(row['navigation_address'])}, "
                    f"{_ascii(row['location_description'])}; facility {spec['key']}, feature OBJECTID "
                    f"{int(row['geometry_source_objectid'])} of layer 6 of the city's ArcGIS service, data edit date "
                    f"{str(row['geometry_data_edit_date'])[:10]}; controller ruling R-D3-a: one of the three car parks "
                    "at 1 EUR/h). The outline is the city's historical facility area of 2018, matched to the tariff by "
                    "the facility name; it is no confirmed current tariff extent and no official zone boundary, and "
                    "the zone-2 membership of the car park is not asserted. Layer gos_1eur_parkplatzflaechen of the "
                    f"package {PACKAGE_FILE} (SHA-256 {sha}); outline {geometry.area:.0f} m2. {GOSLAR_PROVENANCE}.")
        elif spec["layer"] == "vier_staedte_parking_areas":
            source_url = BAD_HARZBURG_PAGE_URL
            body = (f"the car park '{_ascii(row['name'])}' (facility {spec['key']}), one of the five paid car parks "
                    f"that the tourism page of the Kur-, Tourismus- und Wirtschaftsbetriebe Bad Harzburg names with "
                    f"official navigation coordinates ({BAD_HARZBURG_PAGE_URL}, retrieved 2026-10-07). The outline is "
                    f"OSM way {row['osm_way_ids']} (OSM geometry timestamp {row['osm_geometry_timestamps']}), matched "
                    "by name and location and not surveyed, and no official fee boundary. Layer "
                    f"vier_staedte_parking_areas of the package {PACKAGE_FILE} (SHA-256 {sha}); outline "
                    f"{geometry.area:.0f} m2. {OSM_SITES_PROVENANCE}.")
        elif spec["layer"] == "vier_staedte_street_references":
            source_url = SEESEN_PAGE_URL
            body = (f"the paid street spaces '{_ascii(row['name'])}' (facility {spec['key']}) named by the city's "
                    f"information page {SEESEN_PAGE_URL} (retrieved 2026-10-02). The line is the OSM street reference "
                    f"(ways {row['osm_way_ids']}, {geometry.length:.0f} m, OSM geometry timestamps "
                    f"{row['osm_geometry_timestamps']}), only a location: the package states "
                    "'street_reference_not_exact_paid_parking_extent', and a historical city message puts the "
                    "ticket machine on the eastern side of the street after the rebuild of 2013. Layer "
                    f"vier_staedte_street_references of the package {PACKAGE_FILE} (SHA-256 {sha}). "
                    f"{OSM_SITES_PROVENANCE}.")
        else:
            source_url = str(row["source_url"])
            body = (f"the car park '{_ascii(row['name'])}' (facility {spec['key']}, capacity "
                    f"{int(row['capacity'])} reported, not verified). The geometry is a SOURCE POINT, the official "
                    f"tourism coordinate ({_ascii(row['source_coordinates'])}), a navigation coordinate and not a "
                    "boundary: the lot extent is unknown, so the zone is the circle of the buffer around it. Layer "
                    f"neue_orte_parkstandorte of the package {PACKAGE_FILE} (SHA-256 {sha}); geometry source "
                    f"{row['geometry_source']}. {BRAUNLAGE_PROVENANCE}.")
        sites[spec["zone_id"]] = {"geometry": geometry, "kind": kind, "ags": spec["ags"], "source_url": source_url,
                                  "layer": spec["layer"], "key": spec["key"],
                                  "note": (f"{head}{body} The zone is the area within {SITE_BUFFER_M:.0f} m of the "
                                           f"{kind} (ASSUMPTION C-a: the access walk from the destination to the "
                                           "paid site), cut out of the area zones it overlaps (single sites take "
                                           "precedence), simplified 0.5 m and cut with a 0.05 m clearance; where the "
                                           "50 m areas of two sites overlap, the nearer source decides, ties go to "
                                           "the higher tariff.")}
    by_layer = {}
    for spec in SINGLE_SITES:
        by_layer.setdefault(spec["layer"], []).append(spec["key"])
    for layer, keys in by_layer.items():
        frame = layer_frames[layer]
        dropped = {key: AREAS_NOT_ZONED[key] for key in frame.index if layer == "vier_staedte_parking_areas"
                   and key in AREAS_NOT_ZONED}
        _log(package, layer, len(frame), keys, dropped)
    counts = pd.Series([site["kind"] for site in sites.values()]).value_counts()
    total = len(sites)
    print("[regional] single-site geometry: " + ", ".join(
        f"{kind} {int(counts.get(kind, 0))}/{total} ({100.0 * counts.get(kind, 0) / total:.1f} %)"
        for kind in ("polygon", "street line", "source point")) + " (primary: the site's outline or street line; "
          "fallback: a source point, whose lot extent is unknown)")
    return sites


def split_single_sites(sources: dict, ranking: list, buffer_m: float = SITE_BUFFER_M) -> dict:
    """The zone of every site: the area within ``buffer_m`` of its source geometry, split by the nearer source where
    the areas of two sites overlap (spec Amendment D3, ruling R-4a-2).

    ``sources`` maps a zone id to its polygon, street line or point; ``ranking`` lists the ids from the highest tariff
    to the lowest (ties in the nearer-source rule go to the higher tariff, as for the Wolfsburg sections). The split is
    ``municipal_zones.split_by_nearest_section`` (a generalised Voronoi split by sampling the source boundaries every
    metre); a point or a line has no polygon boundary, so it is sampled through its ``SPLIT_PROXY_M`` buffer. Every zone
    is its EXACT buffer minus the part of the contested area the split gives to another site, so a site without a
    competitor keeps exactly its buffer and two zones never overlap. Returns {"zones", "buffers" (the exact areas),
    "contested" (per id: the part of its area another site's area covers as well), "competitors", "samples"}.
    """
    if sorted(ranking) != sorted(sources):
        raise SystemExit(f"the tariff ranking {sorted(ranking)} does not list the sites {sorted(sources)}")
    proxies = {key: (geometry if geometry.geom_type in ("Polygon", "MultiPolygon") else geometry.buffer(SPLIT_PROXY_M))
               for key, geometry in sources.items()}
    split = mz.split_by_nearest_section(proxies, list(ranking), buffer_m=buffer_m)
    exact = {key: geometry.buffer(buffer_m) for key, geometry in sources.items()}
    zones = {}
    for key in ranking:
        lost = split["contested"][key].difference(split["zones"][key])
        zones[key] = exact[key] if lost.is_empty else cc.largest_parts(exact[key].difference(lost), SPLIT_SLIVER_M2)
    return {"zones": zones, "buffers": exact, "contested": split["contested"], "competitors": split["competitors"],
            "samples": split["samples"]}


# ---------------------------------------------------------------- tariff evidence of the new rows
def _rule(package: dict, rule_id: str) -> dict:
    if rule_id not in package["rules"]:
        raise SystemExit(f"tariff_rules.json of the package has no rule {rule_id}")
    return package["rules"][rule_id]


def _weekday_window(rule: dict) -> Optional[tuple]:
    """The fee window (decimal hours) of the weekday Monday to Friday that the rule states, else None (unsourced).

    Read from ``charging_times`` (monday to friday must state the one same interval) or from ``time_window`` with the
    days 'Mo-Fr' or 'Mo-Sa'; a Saturday interval is not modelled (the model's average weekday, D1). Any other day
    specification or a missing window gives None, never a guessed window."""
    times = rule.get("charging_times") or {}
    weekday = [times.get(day) for day in ("monday", "tuesday", "wednesday", "thursday", "friday")]
    if all(weekday):
        windows = {(entry[0]["start"], entry[0]["end"]) for entry in weekday if len(entry) == 1}
        if len(windows) == 1 and all(len(entry) == 1 for entry in weekday):
            start, end = next(iter(windows))
            return _hours(start), _hours(end)
        raise SystemExit(f"rule {rule['rule_id']}: Monday to Friday state different charging times {weekday}")
    window = rule.get("time_window") or {}
    if window.get("from") and window.get("to"):
        if window.get("days_raw") not in ("Mo-Fr", "Mo-Sa"):
            raise SystemExit(f"rule {rule['rule_id']}: time window days {window.get('days_raw')!r} are neither Mo-Fr nor "
                             "Mo-Sa; the weekday window cannot be read")
        return _hours(window["from"]), _hours(window["to"])
    return None


def _hourly_rate(rule: dict) -> float:
    amount, unit = float(rule["amount_eur"]), float(rule["billing_unit_minutes"])
    return round(amount / unit * 60.0, 6)


def _note_evidence(package: dict, zone_id: str, rules: list) -> dict:
    """What the note of a new row must say beyond its numbers (checked by ``check_tariff_rows``): ``assumptions`` (the
    ids it must name: C-a for every single site, D3-a for the Goslar lots, D3-b for the Bad Harzburg ceiling, M2 where a
    source states a maximum stay that the row does not model, R2-a where the permit flag is set or a Goslar district
    can act), ``max_stay_stated_min`` (the stated maximum stays in minutes: the Bad Harzburg and Seesen sources),
    ``unpreferred_rule`` (the rule the row rests on although the package marks it not preferred for current use) and
    ``osm_charge`` (the OSM ``charge`` tag of a Bad Harzburg car park, the corroboration of the ceiling, None where it
    has none)."""
    assumptions = ["C-a"] if zone_id in D3_ZONE_IDS else []
    if zone_id.startswith("gs_"):
        assumptions.append("D3-a")
    if zone_id.startswith("bh_"):
        assumptions.append("D3-b")
    stays, osm_charge = [], None
    if zone_id.startswith("bh_"):
        row = package["areas"].loc[zone_id]
        stays = [int(row["max_stay_minutes"])] if pd.notna(row["max_stay_minutes"]) else []
        charges = {str(tags["charge"]) for tags in json.loads(row["osm_tags_json"] or "{}").values() if tags.get("charge")}
        osm_charge = "; ".join(sorted(charges)) or None
    elif zone_id == "se_am_markt":
        row = package["street"].loc[zone_id]
        stays = sorted({int(row[column]) for column in ("max_stay_ordinance_minutes", "max_stay_webpage_minutes")
                        if pd.notna(row[column])})
    if stays:
        assumptions.append("M2")
    if zone_id.startswith("gs_") or zone_id in RESIDENT_PERMITS_NOT_VALID:
        assumptions.append("R2-a")
    unpreferred = next((rule["rule_id"] for rule in rules if not rule.get("preferred_for_current_use", True)), None)
    return {"assumptions": assumptions, "max_stay_stated_min": stays, "unpreferred_rule": unpreferred,
            "osm_charge": osm_charge}


def tariff_evidence(package: dict) -> dict:
    """zone id -> the tariff values the package's tariff rules support for the new street rows, and what they leave to
    an assumption. Keys: ``hourly_rate_eur``, ``billing_unit_min``, ``fee_start_h``/``fee_end_h`` (None: the sources
    state no window, ASSUMPTION F1), ``free_if_stay_at_most_min``, ``first_period_min``, ``first_period_eur``,
    ``daily_cap_eur`` (None where the rules state none), ``rule_ids``, ``billing_basis`` and the keys of
    ``_note_evidence`` (what the note must say).

    BgA Willy-Brandt-Platz: the BgA rule (0.90 EUR per 30 min, minute-exact with phone parking, billing unit 1 as at the
    other BgA lots). Goslar lots: the service page's hourly rate and weekday window per lot; the billing unit is the
    half hour of the ParkGO (ASSUMPTION D3-a, ruling R-4a-3) because the page states none. Bad Harzburg: the ParkGO
    ceiling is the rate at every site (ASSUMPTION D3-b; the sites' own rates are not verified). Seesen: the ordinance's
    rate and its weekday window. Braunlage Wurmberg: the ParkGO rate (no window); Hexenritt: the published total-stay
    bands, which a free limit, a first period, a stepped rate and a day cap reproduce exactly (``_band_evidence``).
    """
    evidence = {}
    rule = _rule(package, "bs_bga_willy_brandt_platz_1")
    if "mobile_minute_exact" not in str(rule["rounding"]):
        raise SystemExit("the BgA rule no longer states minute-exact phone-parking billing; the row's billing unit 1 "
                         "rests on it")
    evidence["bs_bga_willy_brandt_platz"] = {
        "hourly_rate_eur": _hourly_rate(rule), "billing_unit_min": BGA_BILLING_UNIT_MIN, "window": _weekday_window(rule),
        "rule_ids": [rule["rule_id"]], "billing_basis": "Entgeltordnung sec. 2: minute-exact with phone parking",
        **_note_evidence(package, "bs_bga_willy_brandt_platz", [rule])}
    for spec in SINGLE_SITES:
        zone_id, rules = spec["zone_id"], [_rule(package, rule_id) for rule_id in spec["rule_ids"]]
        first = rules[0]
        if zone_id.startswith("gs_"):
            evidence[zone_id] = {"hourly_rate_eur": _hourly_rate(first), "billing_unit_min": GOSLAR_LOT_BILLING_UNIT_MIN,
                                 "window": _weekday_window(first), "rule_ids": [first["rule_id"]],
                                 "billing_basis": "ASSUMPTION D3-a: the ParkGO's started half hour (the service page "
                                                  f"states {int(first['billing_unit_minutes'])} min and no rounding)",
                                 **_note_evidence(package, zone_id, rules)}
        elif zone_id.startswith("bh_"):
            evidence[zone_id] = {"hourly_rate_eur": _hourly_rate(first), "billing_unit_min": int(first["billing_unit_minutes"]),
                                 "window": None, "rule_ids": [first["rule_id"]],
                                 "billing_basis": "ParkGO sec. 2(1): per started half hour (the ceiling)",
                                 **_note_evidence(package, zone_id, rules)}
        elif zone_id == "se_am_markt":
            evidence[zone_id] = {"hourly_rate_eur": _hourly_rate(first), "billing_unit_min": int(first["billing_unit_minutes"]),
                                 "window": _weekday_window(first), "rule_ids": [first["rule_id"]],
                                 "billing_basis": "ParkGO sec. 2(1): per started 10 min",
                                 **_note_evidence(package, zone_id, rules)}
        elif zone_id == "br_wurmberg":
            evidence[zone_id] = {"hourly_rate_eur": _hourly_rate(first), "billing_unit_min": int(first["billing_unit_minutes"]),
                                 "window": _weekday_window(first), "rule_ids": [first["rule_id"]],
                                 "billing_basis": "ParkGO of Braunlage: per each started interval of 30 min",
                                 **_note_evidence(package, zone_id, rules)}
        else:
            evidence[zone_id] = dict(_band_evidence(rules), window=_weekday_window(first),
                                     rule_ids=[rule["rule_id"] for rule in rules],
                                     **_note_evidence(package, zone_id, rules))
    return evidence


def _band_evidence(rules: list) -> dict:
    """The row values that reproduce the published total-stay bands of the Hexenritt car park exactly.

    The bands are contiguous, start at 0 min and begin with a free band; the next band is the first period (price
    ``first_period_eur`` for ``first_period_min`` = its end, charged once); the bands after it are steps of one constant
    width ``w`` and one constant price increase ``d`` (a rate of ``d`` per started ``w`` min: ``billing_unit_min`` ``w``,
    ``hourly_rate_eur`` d / (w / 60)); the last band is the price of the whole remaining day, the ``daily_cap_eur``,
    which must not exceed the price of the next step (a cap above it would not be reached by the steps). Anything else
    raises ``SystemExit``: the row fields cannot express it and no value may be invented."""
    bands = sorted(((int(rule["elapsed_from_minutes"]), int(rule["elapsed_to_minutes"]), float(rule["amount_eur"]))
                    for rule in rules))
    if len(bands) < 4 or bands[0][0] != 0 or bands[0][2] != 0.0:
        raise SystemExit(f"the total-stay bands {bands} do not start at 0 min with a free band and hold at least a first "
                         "period, one step and a last band")
    if any(following[0] != band[1] for band, following in zip(bands, bands[1:])):
        raise SystemExit(f"the total-stay bands {bands} are not contiguous")
    free_until, (_, first_end, first_price) = bands[0][1], bands[1]
    steps = bands[2:-1]
    previous_prices = [first_price] + [step[2] for step in steps[:-1]]
    widths = {end - start for start, end, _ in steps}
    increases = {round(price - previous, 6) for previous, (_, _, price) in zip(previous_prices, steps)}
    if len(widths) != 1 or len(increases) != 1 or min(increases) <= 0:
        raise SystemExit(f"the total-stay bands {bands} are not regular steps of one constant price increase per one "
                         "constant width after the first period; the row fields cannot express them")
    width, increase = widths.pop(), increases.pop()
    last_price = bands[-1][2]
    if last_price < steps[-1][2] or last_price > round(steps[-1][2] + increase, 6):
        raise SystemExit(f"the last total-stay band {bands[-1]} is not the day cap of the steps ({steps[-1]} + {increase})")
    return {"hourly_rate_eur": round(increase / (width / 60.0), 6), "billing_unit_min": width,
            "free_if_stay_at_most_min": free_until, "first_period_min": first_end, "first_period_eur": first_price,
            "daily_cap_eur": last_price,
            "billing_basis": f"the published bands: free up to {free_until} min, {first_price:.2f} EUR for the first "
                             f"{first_end} min, then {increase:.2f} EUR per started {width} min, at most "
                             f"{last_price:.2f} EUR per day (last band to {bands[-1][1]} min)"}


_ROW_NUMBER_COLUMNS = ("hourly_rate_eur", "free_if_stay_at_most_min", "first_period_min", "first_period_eur",
                       "daily_cap_eur")


def check_tariff_rows(evidence: dict, tariffs: pd.DataFrame, f1_windows: Optional[dict] = None) -> None:
    """The new street rows of ``tariffs`` carry the package's values: the rate, billing unit, the extra fields of the
    Hexenritt bands (empty where the rules state none), no maximum stay and no long-stay product (ruling R-4a-4), and the
    fee window the rules state with a source other than 'assumption'; where the rules state no window, the F1 window of
    ``F1_WINDOWS`` with ``fee_window_source`` 'assumption'. The note of a row names every assumption it rests on
    (``tariff_evidence``: C-a, D3-a, D3-b, M2, R2-a, and F1 where it has no sourced window), says that the package marks
    the rule it uses as not preferred where it does, and says what the OSM ``charge`` tag of a Bad Harzburg car park is
    (the corroboration of the ceiling) or that it has none; the permit flag is false exactly on
    ``RESIDENT_PERMITS_NOT_VALID`` (ASSUMPTION R2-a) and empty elsewhere. Raises ``SystemExit`` listing every deviation;
    prints how many windows are sourced and how many fall back to ASSUMPTION F1."""
    f1_windows = F1_WINDOWS if f1_windows is None else f1_windows
    rows = tariffs.set_index("zone_id")
    problems, sourced, assumed, corroborated = [], [], [], []
    missing = sorted(set(evidence) - set(rows.index))
    if missing:
        problems.append(f"tariff rows missing for the zones {missing}")
    for zone_id, values in sorted(evidence.items()):
        if zone_id not in rows.index:
            continue
        row = rows.loc[zone_id]
        if row["zone_type"] != "street_paid":
            problems.append(f"{zone_id}: zone_type {row['zone_type']}, a single paid site is street_paid")
        for column in _ROW_NUMBER_COLUMNS:
            expected = values.get(column)
            actual = row[column]
            if (expected is None) != bool(pd.isna(actual)) or (
                    expected is not None and not math.isclose(float(actual), float(expected), abs_tol=1e-9)):
                problems.append(f"{zone_id}: {column} {actual} but the package gives {expected}")
        unit = row["billing_unit_min"]
        if pd.isna(unit) or int(unit) != values["billing_unit_min"]:
            problems.append(f"{zone_id}: billing_unit_min {unit} but {values['billing_unit_min']} ({values['billing_basis']})")
        for column in ("max_stay_min", "long_stay_product_eur", "member_day_eur", "guest_day_eur"):
            if pd.notna(row[column]):
                problems.append(f"{zone_id}: {column} must stay empty (ruling R-4a-4: stays above a stated maximum are "
                                "priced metered; no source states a long-stay product)")
        notes = str(row["notes"])
        for assumption in values["assumptions"]:
            if f"ASSUMPTION {assumption}" not in notes:
                problems.append(f"{zone_id}: the note must name ASSUMPTION {assumption}")
        unpreferred = values["unpreferred_rule"]
        if unpreferred is not None and (unpreferred not in notes or "not preferred for current use" not in notes):
            problems.append(f"{zone_id}: the package marks the rule {unpreferred} as not preferred for current use; the "
                            "note must name the rule and say so")
        if zone_id.startswith("bh_"):
            if values["osm_charge"] is not None and f"charge='{values['osm_charge']}'" not in notes:
                problems.append(f"{zone_id}: the note must quote the OSM tag charge='{values['osm_charge']}' that "
                                "corroborates the ceiling")
            if values["osm_charge"] is None and "no charge tag" not in notes:
                problems.append(f"{zone_id}: the OSM tags hold no charge tag; the note must say that nothing corroborates "
                                "the ceiling here")
            if values["osm_charge"] is not None:
                corroborated.append(zone_id)
        permits = row["resident_permits_valid"]
        if zone_id in RESIDENT_PERMITS_NOT_VALID:
            if pd.isna(permits) or bool(permits):
                problems.append(f"{zone_id}: resident_permits_valid must be false (ASSUMPTION R2-a: no source states that "
                                "resident permits are valid there)")
        elif pd.notna(permits):
            problems.append(f"{zone_id}: resident_permits_valid must stay empty, the default of the zone type "
                            "(ASSUMPTION R2-a)")
        if values["window"] is not None:
            sourced.append(zone_id)
            if row["fee_window_source"] == "assumption":
                problems.append(f"{zone_id}: the sources state the fee window {values['window']}, so fee_window_source "
                                "must not be 'assumption'")
            if (float(row["fee_start_h"]), float(row["fee_end_h"])) != tuple(values["window"]):
                problems.append(f"{zone_id}: fee window {row['fee_start_h']}-{row['fee_end_h']} but the package gives "
                                f"{values['window']}")
        else:
            assumed.append(zone_id)
            if zone_id not in f1_windows:
                problems.append(f"{zone_id}: no sourced window and no F1 window declared")
            elif row["fee_window_source"] != "assumption" or "ASSUMPTION F1" not in str(row["notes"]):
                problems.append(f"{zone_id}: the sources state no fee window, so fee_window_source must be 'assumption' "
                                "and the note must name ASSUMPTION F1")
            elif (float(row["fee_start_h"]), float(row["fee_end_h"])) != tuple(f1_windows[zone_id][:2]):
                problems.append(f"{zone_id}: fee window {row['fee_start_h']}-{row['fee_end_h']} but ASSUMPTION F1 gives "
                                f"{f1_windows[zone_id][:2]}")
    if problems:
        raise SystemExit("regional tariff rows contradict the package's tariff rules:\n  " + "\n  ".join(problems))
    total = len(sourced) + len(assumed)
    print(f"regional tariff rows agree with the package's rules: fee window sourced {len(sourced)}/{total} "
          f"({', '.join(sourced) or 'none'}), ASSUMPTION F1 {len(assumed)}/{total} ({', '.join(assumed) or 'none'}); the "
          f"notes name their assumptions; the OSM charge tag corroborates the Bad Harzburg ceiling at "
          f"{len(corroborated)}/{sum(1 for zone_id in evidence if zone_id.startswith('bh_'))} car parks")


# ---------------------------------------------------------------- QA rows of the municipal QA table
def _row(row_id: str, ags: str, subject: str, reference: str, subject_geometry, features: list, *, note: str,
         release_zone_id: str = "") -> dict:
    return mz._row(row_id, ags, subject, reference, subject_geometry, features, note=note,
                   release_zone_id=release_zone_id)


def precedence_cuts(before: dict, after: dict, order: list, winners: set, minimum_m2: float = 0.5) -> list:
    """The precedence cuts that involve a zone of ``winners``: [(loser, winner, area in m2)], largest area first per
    loser. ``before`` holds the geometry of every zone before ``assemble_parking_zones.apply_precedence``, ``after`` the
    result, ``order`` the precedence order (a zone loses area to the zones before it); the cut is the part of the
    loser's input inside the final polygon of an earlier winner, kept where it exceeds ``minimum_m2``."""
    cuts = []
    for position, loser in enumerate(order):
        if loser not in before:
            continue
        for winner in order[:position]:
            if winner not in winners or winner not in after:
                continue
            area = float(before[loser].intersection(after[winner]).area)
            if area > minimum_m2:
                cuts.append((loser, winner, area))
    return sorted(cuts, key=lambda cut: (order.index(cut[0]), -cut[2], cut[1]))


def qa_rows(context: dict, release: gpd.GeoDataFrame, municipalities: Optional[gpd.GeoDataFrame] = None) -> list:
    """The rows of the regional step in the municipal QA table, from the step's ``context`` (``assemble_parking_zones.
    apply_regional_package``) and the ``release`` as loaded from the written zone file (EPSG:25832).

    Per BgA zone: the release polygon against the package polygon and against the v1 polygon it replaces; the removed
    Kannengiesserstrasse polygon against the abandoned lot; per campus zone (ruling R-4a-8): the release against the
    detection zones and against the campus grounds of the v1 release, the detection zones against the grounds, the
    share of the campus map's yellow parking areas inside the detection zones (the georeference cross-check), the nested
    boundaries, the merged International House and the dropped Bevenroder detection zone; per single site: the release
    against the 50 m area of its source; every precedence cut that involves one of these zones; and the declared
    municipality exceptions against the municipality polygons (``municipalities``, indexed by AGS).
    """
    if release.crs is None or release.crs.to_epsg() != 25832:
        raise SystemExit(f"the release must be measured in EPSG:25832, found {release.crs}")
    polygons = dict(zip(release["zone_id"].astype(str), release.geometry))
    package, v1 = context["package"], context["v1"]
    rows = []
    # ---- BgA
    for zone_id, values in context["bga"].items():
        rows.append(_row(f"{zone_id}_release_vs_package", BS_AGS, f"release polygon {zone_id}",
                         f"BgA lot {values['area_id']} of layer bs_bga_parkflaechen (annex {values['anlage']} of the "
                         "Amtsblatt 2022 Nr. 16)", polygons[zone_id], mz._features(values["geometry"]),
                         release_zone_id=zone_id, note=(
                             "D1: the release polygon is the package polygon after the 0.5 m simplification and the cuts "
                             f"of other zones (none expected); working uncertainty {values['uncertainty_m']:.0f} m")))
        if zone_id in v1:
            rows.append(_row(f"{zone_id}_release_vs_v1", BS_AGS, f"release polygon {zone_id}",
                             f"v1 polygon of {zone_id} (OSM lot buffered 5 m, 2026-09-29), before the precedence cuts",
                             polygons[zone_id], mz._features(v1[zone_id]), release_zone_id=zone_id, note=(
                                 "D1: the package polygon replaces the v1 polygon" + (
                                     "; the v1 polygon lies on the neighbouring kerbside stalls" if zone_id.endswith(
                                         "martinikirche") else ""))))
    abandoned = package["abandoned"].loc[BGA_ABANDONED_ZONE]
    rows.append(_row(f"{BGA_ABANDONED_ZONE}_v1_vs_abandoned_lot", BS_AGS,
                     f"v1 polygon of {BGA_ABANDONED_ZONE} (removed from the release)",
                     "the historical outline of the lot Kannengiesserstrasse (layer referenz_bs_aufgegeben, feature "
                     f"{abandoned['area_id']})", v1[BGA_ABANDONED_ZONE], mz._features(abandoned.geometry), note=(
                         "D1: the lot became a pocket park (opened April 2026; city project page "
                         "ris-pocket-park-kannengiesserstrasse), so the zone is removed; the v1 polygon lay on the "
                         "neighbouring kerbside stalls")))
    # ---- TU campuses (ruling R-4a-8: the campus grounds of the v1 release united with the detection zones)
    detection, yellow = package["detection"], package["yellow"]
    for zone_id, values in context["campus"].items():
        detection_union, ground_union = values["detection"], values["grounds"]
        maps = f"the union of the {len(values['features'])} detection zone(s) of the campus map {values['map']}"
        rows.append(_row(f"{zone_id}_release_vs_detection_zones", BS_AGS, f"release polygon {zone_id}", maps,
                         polygons[zone_id], [detection_union], release_zone_id=zone_id, note=(
                             "D1: the paid car parks lie in the campus zone, so reference_share_in_subject is 1 up to the "
                             "0.5 m simplification, and subject_share_in_reference is the part of the campus zone that "
                             "is detection zone" + ("" if ground_union is not None else " (no campus grounds: the zone "
                                                    "is the detection zones alone)") + "; reference_features counts "
                             "the detection zones")))
        rows[-1]["reference_features"] = str(len(values["features"]))
        rows[-1]["reference_features_overlapping"] = str(sum(
            1 for geometry in detection.loc[detection["campus_ascii"] == values["campus"], "geometry"]
            if polygons[zone_id].intersection(geometry).area > 0.0))
        if ground_union is not None:
            grounds = (f"the campus grounds: the v1 release polygon(s) of {', '.join(TU_GROUNDS[zone_id])} (OSM "
                       "amenity=university outline, 2026-09-29)")
            rows.append(_row(f"{zone_id}_release_vs_campus_grounds", BS_AGS, f"release polygon {zone_id}", grounds,
                             polygons[zone_id], mz._features(ground_union), release_zone_id=zone_id, note=(
                                 "R-4a-8: the campus grounds (the destination area, where the buildings are) lie in the "
                                 "campus zone, so reference_share_in_subject is 1 up to the 0.5 m simplification, and "
                                 "subject_share_in_reference is the part of the campus zone that is campus grounds")))
            rows.append(_row(f"{zone_id}_detection_zones_vs_campus_grounds", BS_AGS, maps, grounds, detection_union,
                             mz._features(ground_union), note=(
                                 "R-4a-8: subject_share_in_reference is the part of the paid car parks (the detection "
                                 "zones) that lies inside the campus grounds; the rest extends the campus zone beyond "
                                 "the grounds")))
        group = yellow[yellow["campus_ascii"] == values["campus"]]
        if len(group):
            rows.append(_row(f"{zone_id}_yellow_areas_in_detection_zones", BS_AGS,
                             f"the {len(group)} yellow parking areas of the campus map (layer tu_kartenflaechen)",
                             f"the union of the {len(values['features'])} detection zone(s) of the same map",
                             unary_union(list(group.geometry)), [detection_union],
                             note=("D1 cross-check of the georeference: the yellow parking areas of the same map should "
                                   f"lie inside the detection zones (subject_share_in_reference >= "
                                   f"{YELLOW_AREAS_CONSISTENCY:.2f}); a deviation would show a georeference conflict")))
    nested = package["detection"][package["detection"]["campus_ascii"] == NESTED_CAMPUS]
    flagged = nested[nested["nested_boundary_semantics"].notna()]
    if len(flagged) > 1:
        outer = flagged.loc[flagged.geometry.area.idxmax()]
        inner = flagged.loc[flagged.index != outer.name]
        rows.append(_row("tu_campus_ost_beethovenstrasse_nested_boundaries", BS_AGS,
                         "the inner red outline(s) " + ", ".join(inner["feature_id"].astype(str)),
                         f"the outer red outline {outer['feature_id']}", unary_union(list(inner.geometry)),
                         [outer.geometry], note=(
                             "D1: the map draws nested boundaries and does not say whether the inner one excludes an "
                             "area; the outer boundary is used, so the inner area is part of the zone (the areas must "
                             "not be added)")))
    if TU_MERGED_ZONE in v1:
        rows.append(_row(f"{TU_MERGED_ZONE}_v1_vs_{TU_MERGED_INTO}", BS_AGS,
                         f"v1 polygon of {TU_MERGED_ZONE} (merged, removed from the release)",
                         f"release polygon {TU_MERGED_INTO}", v1[TU_MERGED_ZONE], mz._features(polygons[TU_MERGED_INTO]),
                         note=("D1, R-4a-8: the car parks at the International House (OSM ways 134220301 and "
                               "172658383, buffered 5 m) are part of the campus grounds of the Langer Kamp zone (the v1 "
                               "zone is merged into it), so subject_share_in_reference is 1 up to the 0.5 m "
                               "simplification; the two small detection zones west of Brucknerstrasse on the Langer Kamp "
                               "map mark them as paid car parks")))
    for campus, reason in TU_NOT_ZONED.items():
        group = detection[detection["campus_ascii"] == campus]
        tu_zones = unary_union([polygons[zone_id] for zone_id in TU_ZONE_IDS if zone_id in polygons])
        rows.append(_row(f"tu_{_slug(campus)}_detection_zone_not_zoned", BS_AGS,
                         f"the {len(group)} detection zone(s) of the campus {campus} (layer tu_kamera_detektionszonen)",
                         "the release polygons of the TU campus zones", unary_union(list(group.geometry)),
                         mz._features(tu_zones), note=f"D1: not a zone; {reason}"))
    # ---- single sites
    sites, split = context["sites"], context["split"]
    for zone_id, values in sites.items():
        rows.append(_row(f"{zone_id}_release_vs_50m_area", values["ags"], f"release polygon {zone_id}",
                         f"the area within {SITE_BUFFER_M:.0f} m of its source {values['kind']} (layer {values['layer']}, "
                         f"record {values['key']})", polygons[zone_id], mz._features(split["zones"][zone_id]),
                         release_zone_id=zone_id, note=(
                             f"D3: reference_share_in_subject is the share of the {SITE_BUFFER_M:.0f} m area the zone "
                             "keeps after the nearest-source split, the 0.5 m simplification, the 0.05 m cut clearance "
                             "and the cuts of zones that take precedence" + (
                                 "; the source is a NAVIGATION POINT, the lot extent is unknown"
                                 if values["kind"] == "source point" else "") + (
                                 "; the source is only a street location reference, not the exact paid extent"
                                 if values["kind"] == "street line" else ""))))
    # ---- precedence cuts
    for loser, winner, area in context["cuts"]:
        rows.append(_row(f"{loser}_cut_by_{winner}", context["ags"].get(loser, BS_AGS),
                         f"{loser} before the precedence cuts", f"release polygon {winner}", context["before"][loser],
                         mz._features(polygons[winner]), note=(
                             f"R-4a-1: {winner} takes precedence over {loser} where they overlap; the overlap "
                             f"({area:.1f} m2 measured against the final polygon) is cut from {loser}")))
    # ---- declared municipality exceptions
    for zone_id, (ags, reason) in context["exceptions"].items():
        for name, label in ((context["ags"][zone_id], "the municipality of its tariff row"), (ags, "the municipality "
                                                                                                     "that contains it")):
            if municipalities is not None and name in municipalities.index:
                rows.append(_row(f"{zone_id}_release_vs_municipality_{name}", name, f"release polygon {zone_id}",
                                 f"the municipality polygon {name} ({label}; pipeline VG250 polygons)",
                                 polygons[zone_id], [municipalities.loc[name, "geometry"]], release_zone_id=zone_id,
                                 note=f"declared exception of the municipality check: {reason}"))
    return rows


def _slug(text: str) -> str:
    return _ascii(text).lower().replace(" ", "_")


#: The paragraph the regional step adds to the header of the municipal QA table (``qa_intro`` places it; ASCII, like the
#: whole table).
QA_INTRO_SUFFIX = (
    "Spec Amendment D (the regional evidence package of 2026-10-07, scripts/curation/parking_zones_2026/regional_zones.py "
    "via --regional-dir; owner-supplied package under raw_sources/municipal_2026-10-07/, gitignored, SHA-256 in the data "
    "record parking_zones_2026; controller rulings R-4a-1 to R-4a-7). Sources: BgA car parks: " + BGA_PROVENANCE
    + ". TU campus zones (ruling R-4a-8), the detection zones of the TU campus maps: " + TU_PROVENANCE + "; the campus "
    "grounds: " + TU_GROUNDS_PROVENANCE + ". Goslar car parks at 1 EUR/h: " + GOSLAR_PROVENANCE + ". Bad Harzburg and "
    "Seesen: " + OSM_SITES_PROVENANCE + ". Braunlage: " + BRAUNLAGE_PROVENANCE + ". D1: every BgA zone is compared with "
    "its package geometry and with the v1 polygon it replaces (the removed zone bs_bga_kannengiesserstrasse with its "
    "own); every TU campus zone, the union of the campus grounds (the v1 release polygons, the destination area) and the "
    "camera detection zones (the paid car parks), is compared with both parts and the detection zones with the grounds; "
    "the yellow parking areas of each campus map cross-check the georeference of its detection zones. D3: every "
    "single-site zone is compared with the area within 50 m (ASSUMPTION C-a) of its source. Every precedence cut that involves a zone of the step is a row <loser>_cut_by_<winner> (R-4a-1), "
    "measured against the final polygon of the winner. The rows of a declared municipality exception compare the polygon "
    "with the municipality polygons of the pipeline.")
#: The sentence of the municipal intro that precedes the column list; the regional paragraph goes before it.
_QA_UNITS_SENTENCE = " Units m2 (EPSG:25832"


def qa_intro(municipal_intro: str) -> str:
    """``municipal_intro`` (``municipal_zones.QA_INTRO``) with ``QA_INTRO_SUFFIX`` inserted before its units sentence,
    so that the header still ends with the lead-in of the column list. Raises ``SystemExit`` when the municipal intro
    no longer has that sentence."""
    head, separator, tail = municipal_intro.partition(_QA_UNITS_SENTENCE)
    if not separator:
        raise SystemExit("the municipal QA intro no longer holds the units sentence the regional paragraph precedes")
    return head + " " + QA_INTRO_SUFFIX + separator + tail
