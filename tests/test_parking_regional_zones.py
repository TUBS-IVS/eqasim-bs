"""Regional evidence package of 2026-10-07 in the parking zone curation (parking cost zones v2, spec Amendment D, issue #436).

The owner's package is gitignored, so the curation step is pinned on a synthetic package written the way the owner
supplied it (a zip holding ``<name>/daten/Regional_Parkdaten.gpkg`` in EPSG:25832 and ``<name>/daten/tariff_rules.json``):
D1, the BgA car parks take the package polygons, ``bs_bga_willy_brandt_platz`` is added and ``bs_bga_kannengiesserstrasse``
removed; the TU campus zones become the union of the camera detection zones of their campus (nested boundaries recorded,
Bevenroder Strasse dropped because no page states ticketing, the International House merged into Langer Kamp) and take
precedence over street zones; D3, the single paid sites of Bad Harzburg, Seesen, Braunlage and Goslar are zones of their
own, the area within 50 m (ASSUMPTION C-a) of a polygon, a street line or a source point, cut out of the area zones and
split by the nearer source where two areas overlap. The release must pass ``load_zone_polygons`` with no repair and the
municipal QA table ``municipal_zone_qa.validate_municipal_qa``; the new tariff rows are checked against the package's
tariff rules, every value either sourced or labelled as an assumption.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import unary_union

from braunschweig.parking import municipal_zone_qa as mq
from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
X0, Y0 = 604_000.0, 5_789_000.0
BS, GS, BH, SE, BR = "03101000", "03153017", "03153002", "03153012", "03153016"
HARZ = "03153504"
PACKAGE = "Regional_Parkdaten_Belege_2026-10-07"
#: Every town lies far from the others, so no 50 m area or precedence cut reaches another town.
GS_X, BH_X, SE_X, BR_X = 30_000.0, 60_000.0, 80_000.0, 100_000.0
#: A release polygon read back from the file differs from its metric geometry by the WGS84 rounding (about 1 cm).
FILE_ROUNDING = 1e-4
#: The same rounding on a lot of 800 to 2,500 m2: up to 1 cm along a perimeter of 120 to 200 m.
SMALL_ZONE_ROUNDING = 5e-3
#: The assembly simplifies by 0.5 m, which cuts up to 0.5 % off a round 50 m buffer.
SIMPLIFIED_BUFFER = 1e-2
GAZETTE = "https://www.braunschweig.de/politik_verwaltung/bekanntmachungen/amtsblatt/amtsblatt_stadt_braunschweig_2022_16.pdf"
EDIT_DATE = "2018-11-22T15:42:08.768000+00:00"


def _box(x0, y0, x1, y1, dx=0.0):
    return box(X0 + dx + x0, Y0 + y0, X0 + dx + x1, Y0 + y1)


def _point(x, y, dx=0.0):
    return Point(X0 + dx + x, Y0 + y)


@pytest.fixture(scope="module")
def assembly():
    """The assembly script as a module (it imports curation_common and the two package steps from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("assemble_parking_zones_regional_under_test",
                                                      CURATION_DIR / "assemble_parking_zones.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


# --------------------------------------------------------------------------- the synthetic package


def _layer(rows: dict, geometry: list) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(rows, geometry=geometry, crs=METRIC_CRS)


def _bga_layer() -> gpd.GeoDataFrame:
    lots = [("bs_bga_markthalle", "BGA_1", "Markthalle", 96, _box(100, 500, 150, 550)),
            ("bs_bga_an_der_martinikirche", "BGA_3", "An der Martinikirche", 98, _box(300, 300, 340, 320)),
            ("bs_bga_jodutenstrasse_klint", "BGA_4", "Jodutenstra\u00dfe/Klint", 99, _box(500, 600, 540, 630)),
            ("bs_bga_suedstrasse", "BGA_5", "S\u00fcdstra\u00dfe", 100, _box(700, 150, 745, 180)),
            ("bs_bga_willy_brandt_platz", "BGA_6", "Willy-Brandt-Platz", 101, _box(1500, 500, 1540, 540))]
    return _layer({"facility_id": [lot[0] for lot in lots], "area_id": [lot[1] for lot in lots],
                   "name": [lot[2] for lot in lots], "source_map_page": [lot[3] for lot in lots],
                   "estimated_position_uncertainty_m": [5] * 5, "source_url": [GAZETTE] * 5},
                  [lot[4] for lot in lots])


def _abandoned_layer() -> gpd.GeoDataFrame:
    return _layer({"facility_id": ["bs_bga_kannengiesserstrasse"], "area_id": ["BGA_2"], "name": ["Kannengie\u00dferstra\u00dfe"],
                   "operation_status": ["converted_to_pocket_park_2026"], "active_parking": [False],
                   "source_map_page": [97], "source_url": [GAZETTE]}, [_box(600, 300, 640, 340)])


#: campus (as the package writes it, with umlauts) -> list of (feature id, geometry, nested flag)
CAMPUS_POLYGONS = {
    "Zentralcampus": [("TU_KAM_01_01", _box(1250, 100, 1450, 300), None), ("TU_KAM_01_02", _box(1500, 100, 1600, 200), None),
                      ("TU_KAM_01_03", _box(1600, 100, 1700, 200), None)],
    "Campus Nord": [("TU_KAM_04_01", _box(2000, 1000, 2300, 1200), None)],
    "Beethovenstra\u00dfe": [("TU_KAM_03_01", _box(2000, 0, 2400, 400), "unresolved nested red boundaries"),
                             ("TU_KAM_03_02", _box(2100, 100, 2200, 200), "unresolved nested red boundaries"),
                             ("TU_KAM_03_03", _box(2500, 0, 2600, 100), None)],
    "Langer Kamp": [("TU_KAM_02_01", _box(2000, 500, 2100, 560), None), ("TU_KAM_02_02", _box(2200, 500, 2400, 700), None)],
    "Bevenroder Stra\u00dfe": [("TU_KAM_05_01", _box(3000, 3000, 3100, 3100), None)],
    "Volkmaroder Stra\u00dfe": [("TU_KAM_06_01", _box(3000, 1000, 3100, 1100), None)],
    "Forschungsflughafen": [("TU_KAM_07_01", _box(3000, 2000, 3050, 2050), None), ("TU_KAM_07_02", _box(3200, 2000, 3300, 2100), None)],
}


def _detection_layer() -> gpd.GeoDataFrame:
    rows = {"feature_id": [], "campus": [], "source_polygon_no": [], "source_url": [], "source_file": [], "source_sha256": [],
            "source_retrieved_at": [], "page_verified_at": [], "nested_boundary_semantics": [], "parking_footprint": [],
            "buffer_m": []}
    geometries = []
    for campus, features in CAMPUS_POLYGONS.items():
        slug = campus.replace("\u00df", "ss").replace(" ", "-")
        for number, (feature_id, geometry, nested) in enumerate(features, 1):
            rows["feature_id"].append(feature_id)
            rows["campus"].append(campus)
            rows["source_polygon_no"].append(number)
            rows["source_url"].append(f"https://www.tu-braunschweig.de/fileadmin/GB3/Parkleitsystem-{slug}.png")
            rows["source_file"].append(f"Parkleitsystem-{slug}.png")
            rows["source_sha256"].append(hashlib.sha256(slug.encode()).hexdigest())
            rows["source_retrieved_at"].append("2026-10-02")
            rows["page_verified_at"].append("2026-10-07")
            rows["nested_boundary_semantics"].append(nested)
            rows["parking_footprint"].append(False)
            rows["buffer_m"].append(0)
            geometries.append(geometry)
    return _layer(rows, geometries)


def _yellow_layer() -> gpd.GeoDataFrame:
    """Yellow parking areas of the campus maps: inside the detection zones, except one area of Campus Nord (half out)."""
    return _layer({"campus": ["Zentralcampus", "Campus Nord", "Langer Kamp"]},
                  [_box(1260, 110, 1300, 150), _box(2250, 1050, 2350, 1100), _box(2210, 510, 2250, 550)])


def _areas_layer() -> gpd.GeoDataFrame:
    sites = [("bh_sole_therme", "Parkplatz Sole-Therme", _box(0, 0, 50, 40, BH_X), "paid", "116490266"),
             ("bh_kurpark", "Parkplatz Kurpark / Kurhausparkplatz", _box(500, 0, 560, 40, BH_X), "paid", "37008653"),
             ("bh_grossparkplatz", "Gro\u00dfparkplatz Bad Harzburg", _box(1000, 0, 1100, 120, BH_X), "paid", "51517817"),
             ("bh_burgberg", "Parkplatz Burgberg", _box(1500, 0, 1560, 60, BH_X), "paid", "617792585"),
             ("bh_berliner_platz", "Parkplatz Berliner Platz", _box(2000, 0, 2040, 100, BH_X), "paid", "58308224"),
             ("se_bahnhofsplatz", "Unterer Bahnhofsplatz", _box(0, 500, 40, 540, SE_X), "historical_paid_unconfirmed", "1"),
             ("se_parkhaus", "Parkhaus Bahnhofstra\u00dfe", _box(500, 500, 540, 540, SE_X), "free_conditional", "2"),
             ("ko_p3", "P3 Niedernhof", _box(0, 0, 40, 40, 200_000.0), "conflicting", "3"),
             ("sc_burgplatz", "Burgplatz", _box(0, 0, 40, 40, 220_000.0), "free_osm", "4")]
    return _layer({"record_id": [s[0] for s in sites], "city": ["Bad Harzburg"] * 5 + ["Seesen"] * 2 + ["K"] + ["S"],
                   "name": [s[1] for s in sites], "fee_status": [s[3] for s in sites],
                   "osm_way_ids": [s[4] for s in sites], "osm_geometry_timestamps": ["2025-01-15T14:52:20Z"] * 9,
                   "geometry_source": [f"https://www.openstreetmap.org/way/{s[4]}" for s in sites]},
                  [s[2] for s in sites])


def _street_layer() -> gpd.GeoDataFrame:
    line = MultiLineString([LineString([(X0 + SE_X, Y0), (X0 + SE_X + 60, Y0 + 30)])])
    return _layer({"record_id": ["se_am_markt"], "name": ["Am Markt - geb\u00fchrenpflichtige Stellpl\u00e4tze"],
                   "fee_status": ["paid"], "osm_way_ids": ["27957006;1192796702"],
                   "osm_geometry_timestamps": ["2026-08-13T08:55:53Z;2026-08-13T08:55:53Z"]}, [line])


def _points_layer() -> gpd.GeoDataFrame:
    return _layer({"id": ["br_hexenritt", "br_wurmberg"], "city": ["Braunlage"] * 2,
                   "name": ["Parkplatz Hexenritt", "Gro\u00dfparkplatz Wurmberg-Seilbahn / Eisstadion"],
                   "capacity": [600, 1000], "fee_status": ["confirmed_paid"] * 2,
                   "source_url": ["https://www.braunlage.city/tourismus/hexenritt/", "https://www.braunlage.city/parkgo.pdf"],
                   "geometry_source": ["https://www.braunlage.de/tour/wurmberg-gipfeltour"] * 2,
                   "source_coordinates": ['{"crs": "EPSG:32632", "easting": 612516}', '{"crs": "EPSG:32632", "easting": 611347}']},
                  [_point(0, 0, BR_X), _point(3000, 0, BR_X)])


def _goslar_layer() -> gpd.GeoDataFrame:
    lots = [("GOS_1EUR_1", "Parkplatz B\u00e4ringerstra\u00dfe", "B\u00e4ringerstra\u00dfe 35", 1, _box(1500, 0, 1560, 40, GS_X)),
            ("GOS_1EUR_18", "Parkplatz Klubgartenstra\u00dfe / ZOB", "Klubgartenstra\u00dfe 12", 18, _box(1500, 500, 1560, 560, GS_X)),
            ("GOS_1EUR_5", "Parkplatz Glockengie\u00dferstra\u00dfe", "Glockengie\u00dferstra\u00dfe 87", 5, _box(950, 400, 1010, 440, GS_X))]
    return _layer({"facility_id": [lot[0] for lot in lots], "name": [lot[1] for lot in lots],
                   "navigation_address": [lot[2] for lot in lots], "location_description": ["Altstadt"] * 3,
                   "geometry_source_objectid": [lot[3] for lot in lots], "geometry_data_edit_date": [EDIT_DATE] * 3,
                   "is_official_zone_boundary": [False] * 3}, [lot[4] for lot in lots])


def _window(start, end, days):
    return {"from": start, "to": end, "days_raw": days}


def _rule(rule_id, amount, unit, rounding="unspecified", window=None, times=None, elapsed=(None, None)):
    days = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "public_holidays")
    return {"rule_id": rule_id, "amount_eur": amount, "billing_unit_minutes": unit, "rounding": rounding,
            "charging_times": {day: (times or {}).get(day) for day in days},
            "time_window": window or {"from": None, "to": None, "days_raw": None},
            "elapsed_from_minutes": elapsed[0], "elapsed_to_minutes": elapsed[1]}


def _rules() -> list:
    weekday = [{"start": "08:00", "end": "18:00", "crosses_midnight": False}]
    bands = [(0, 30, 0), (30, 120, 2.5), (120, 240, 5), (240, 360, 7.5), (360, 1440, 10)]
    return ([_rule("bs_bga_willy_brandt_platz_1", 0.9, 30, "not_30_minute_ceiling; coin_duration_per_display; "
                                                          "mobile_minute_exact"),
             _rule("goslar_baeringer_REV_01", 1, 60, window=_window("10:00", "18:00", "Mo-Sa")),
             _rule("goslar_zob_REV_01", 1, 60, window=_window("10:00", "16:00", "Mo-Fr")),
             _rule("goslar_glocken_REV_01", 1, 60, window=_window("10:00", "18:00", "Mo-Fr")),
             _rule("bh_ordinary_ceiling", 0.5, 30, "started_interval"),
             _rule("se_ordinary", 0.1, 10, "started_interval", times={
                 "monday": weekday, "tuesday": weekday, "wednesday": weekday, "thursday": weekday, "friday": weekday,
                 "saturday": [{"start": "08:00", "end": "13:00", "crosses_midnight": False}]}),
             _rule("br_public_br_wurmberg", 0.5, 30, "each_started_interval")]
            + [_rule(f"br_hexenritt_steps_br_hexenritt_{index}", price, None, elapsed=(low, high))
               for index, (low, high, price) in enumerate(bands)])


def _write_package(directory: Path, layers: dict, rules: list) -> str:
    """``<directory>/<PACKAGE>.zip`` holding the GeoPackage and the tariff rules; returns the SHA-256 of the zip."""
    work = directory / "work"
    work.mkdir()
    gpkg = work / "Regional_Parkdaten.gpkg"
    for layer, frame in layers.items():
        frame.to_file(gpkg, layer=layer, driver="GPKG")
    rules_path = work / "tariff_rules.json"
    rules_path.write_text(json.dumps({"schema_version": "3", "rules": rules}), encoding="utf-8")
    path = directory / f"{PACKAGE}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(gpkg, f"{PACKAGE}/daten/Regional_Parkdaten.gpkg")
        archive.write(rules_path, f"{PACKAGE}/daten/tariff_rules.json")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layers() -> dict:
    return {"bs_bga_parkflaechen": _bga_layer(), "referenz_bs_aufgegeben": _abandoned_layer(),
            "tu_kamera_detektionszonen": _detection_layer(), "tu_kartenflaechen": _yellow_layer(),
            "vier_staedte_parking_areas": _areas_layer(), "vier_staedte_street_references": _street_layer(),
            "neue_orte_parkstandorte": _points_layer(), "gos_1eur_parkplatzflaechen": _goslar_layer()}


@pytest.fixture(scope="module")
def package_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("municipal_2026-10-07")
    return directory, _write_package(directory, _layers(), _rules())


@pytest.fixture(scope="module")
def regional(assembly):
    return assembly.rz


def _v1_zones(assembly) -> list:
    """The v1 records the step meets: the street zones Ia and Ib, the BgA lots of v1 (OSM-derived, Kannengiesserstrasse
    included), the six TU centre approximations and the Goslar centre approximation."""
    record = assembly.zone_record
    return [
        record("bs_zone_ia", BS, _box(0, 100, 1000, 1000), "ordinance_map", "https://v1", "v1 Ia"),
        record("bs_zone_ib", BS, _box(1000, 0, 1300, 1000), "ordinance_map", "https://v1", "v1 Ib"),
        record("bs_bga_markthalle", BS, _box(95, 495, 155, 555), "ordinance_map", "https://v1", "v1 BgA"),
        record("bs_bga_kannengiesserstrasse", BS, _box(600, 300, 650, 340), "osm_fee_tags", "https://v1", "v1 BgA"),
        record("bs_bga_an_der_martinikirche", BS, _box(300, 400, 340, 420), "osm_fee_tags", "https://v1", "v1 BgA"),
        record("bs_bga_jodutenstrasse_klint", BS, _box(490, 590, 550, 640), "osm_fee_tags", "https://v1", "v1 BgA"),
        record("bs_bga_suedstrasse", BS, _box(690, 140, 760, 190), "osm_fee_tags", "https://v1", "v1 BgA"),
        record("tu_zentralcampus", BS, _box(1200, 0, 1500, 400), "centre_approximation", "https://v1", "v1 TU"),
        record("tu_campus_nord", BS, _box(1950, 950, 2350, 1250), "centre_approximation", "https://v1", "v1 TU"),
        record("tu_campus_ost_beethovenstrasse", BS, _box(1950, -50, 2450, 450), "centre_approximation", "https://v1",
               "v1 TU"),
        record("tu_campus_ost_langer_kamp", BS, _box(2150, 450, 2450, 750), "centre_approximation", "https://v1", "v1 TU"),
        record("tu_international_house", BS, _box(2020, 520, 2120, 580), "centre_approximation", "https://v1", "v1 TU"),
        record("tu_forschungsflughafen", BS, _box(2950, 1950, 3350, 2150), "centre_approximation", "https://v1", "v1 TU"),
        record("gs_altstadt_zone1", GS, _box(0, 0, 1000, 1000, GS_X), "centre_approximation", "https://v1", "v1 Goslar"),
    ]


def _municipalities() -> gpd.GeoDataFrame:
    """Municipality polygons of the synthetic towns; the Bad Harzburg Grossparkplatz (x 1000-1100) lies in the
    gemeindefreies Gebiet Harz, which cuts a pocket out of the Bad Harzburg polygon (its 50 m area, x 950-1150,
    crosses the pocket's western boundary at x = 960)."""
    harz = _box(960, -200, 1250, 300, BH_X)
    frame = gpd.GeoDataFrame(
        {"ags": [BS, GS, BH, HARZ, SE, BR]},
        geometry=[_box(-500, -500, 4000, 4000), _box(-500, -500, 3000, 3000, GS_X),
                  _box(-500, -500, 3000, 500, BH_X).difference(harz), harz, _box(-500, -500, 3000, 3000, SE_X),
                  _box(-500, -500, 5000, 500, BR_X)], crs=METRIC_CRS)
    return frame.set_index("ags")


@pytest.fixture(scope="module")
def release(assembly, package_dir, tmp_path_factory):
    """The regional step on the synthetic package, through the regional precedence, written and loaded as the release."""
    directory, sha256 = package_dir
    package = assembly.rz.load_package(directory, expected_sha256=sha256)
    zones, context = assembly.apply_regional_package(_v1_zones(assembly), package)
    before = {zone["zone_id"]: zone["geometry"] for zone in zones}
    zones, trims = assembly.apply_precedence(zones, assembly.PRECEDENCE_REGIONAL)
    assembly.enforce_exact_cuts(zones)
    assembly.record_regional_cuts(context, before, zones, assembly.PRECEDENCE_REGIONAL)
    assembly.check_municipality_containment(zones, _municipalities())
    out = tmp_path_factory.mktemp("release")
    assembly.write_zone_file(assembly.zone_frame(zones), out / "zones.geojson", municipal=True, regional=True)
    loaded = pz.load_zone_polygons(out / "zones.geojson", max_repairs=0)  # valid as written, no polygon repaired
    rows = assembly.rz.qa_rows(context, loaded, _municipalities())
    assembly.mz.write_qa_table(out / "qa.csv", rows, assembly.mz.QA_INTRO + " " + assembly.rz.QA_INTRO_SUFFIX)
    return {"zones": loaded.set_index("zone_id"), "loaded": loaded, "context": context, "package": package,
            "qa_path": out / "qa.csv", "zone_path": out / "zones.geojson", "trims": dict(trims), "sha256": sha256}


# --------------------------------------------------------------------------- the package is verified before it is read


def test_package_is_refused_when_it_cannot_be_trusted(regional, package_dir, tmp_path):
    directory, sha256 = package_dir
    with pytest.raises(SystemExit, match="SHA-256"):
        regional.load_package(directory, expected_sha256="0" * 64)
    with pytest.raises(SystemExit, match="missing: copy the owner's package"):
        regional.load_package(tmp_path)
    assert regional.PACKAGE_SHA256 == "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7"
    assert regional.load_package(directory, expected_sha256=sha256)["file"]["sha256"] == sha256


def _rewritten(regional, tmp_path, name, edit, rules=None):
    """A package whose layer ``name`` went through ``edit`` (a frame -> frame function)."""
    layers = _layers()
    layers[name] = edit(layers[name])
    sha256 = _write_package(tmp_path, layers, rules if rules is not None else _rules())
    return tmp_path, sha256


@pytest.mark.parametrize("name, edit, message", [
    ("bs_bga_parkflaechen", lambda f: f[f["facility_id"] != "bs_bga_willy_brandt_platz"], "needs exactly"),
    ("bs_bga_parkflaechen", lambda f: f.assign(estimated_position_uncertainty_m=25), "working uncertainty of 5 m"),
    ("referenz_bs_aufgegeben", lambda f: f.assign(active_parking=True), "inactive lot"),
    ("tu_kamera_detektionszonen", lambda f: f[f["campus"] != "Volkmaroder Stra\u00dfe"], "no detection zone for"),
    ("tu_kamera_detektionszonen", lambda f: f.assign(campus=f["campus"].replace({"Zentralcampus": "Neuer Campus"})),
     "no ticketing statement"),
    ("tu_kamera_detektionszonen", lambda f: f.assign(buffer_m=10), "without a buffer"),
    ("vier_staedte_parking_areas", lambda f: f[f["record_id"] != "bh_kurpark"], "no outline for the Bad Harzburg"),
    ("vier_staedte_parking_areas", lambda f: f.assign(fee_status=f["fee_status"].replace({"conflicting": "paid"})
                                                     .where(f["record_id"] != "bh_burgberg", "free_osm")),
     "not fee_status paid"),
    ("vier_staedte_parking_areas", lambda f: pd.concat([f, f.iloc[[7]].assign(record_id="ko_p9")]),
     "neither a zone nor listed in AREAS_NOT_ZONED"),
    ("neue_orte_parkstandorte", lambda f: f[f["id"] != "br_hexenritt"], "confirmed paid sites"),
    ("gos_1eur_parkplatzflaechen", lambda f: f.assign(geometry_data_edit_date="2020-05-05T00:00:00+00:00"), "last edit"),
    ("gos_1eur_parkplatzflaechen", lambda f: f.assign(is_official_zone_boundary=True), "official zone boundary"),
    ("bs_bga_parkflaechen", lambda f: f.to_crs("EPSG:4326"), "EPSG:25832"),
    ("bs_bga_parkflaechen", lambda f: f.assign(geometry=[_box(0, 0, 10, 10).union(_box(5, 5, 15, 15).boundary)]
                                               + list(f.geometry[1:])).set_geometry("geometry").assign(
        geometry=[__import__("shapely").wkt.loads("POLYGON ((0 0, 10 10, 10 0, 0 10, 0 0))")] + list(f.geometry[1:])),
     "invalid or empty geometries"),
], ids=["willy_brandt_platz_missing", "uncertainty_not_the_recorded_one", "kannengiesserstrasse_active",
        "volkmaroder_campus_missing", "campus_without_a_ticketing_statement", "detection_zone_with_a_buffer",
        "bad_harzburg_site_missing", "bad_harzburg_site_not_paid", "unknown_area_record", "braunlage_site_missing",
        "goslar_edit_date_not_the_recorded_one", "goslar_zone_boundary_flag", "layer_not_metric", "invalid_geometry"])
def test_step_refuses_packages_it_cannot_trust(regional, tmp_path, name, edit, message):
    directory, sha256 = _rewritten(regional, tmp_path, name, edit)
    with pytest.raises(SystemExit, match=message):
        regional.load_package(directory, expected_sha256=sha256)


def test_layer_accounting_names_what_was_used_and_what_was_dropped(release, capsys):
    ledger = {entry["layer"]: entry for entry in release["package"]["ledger"]}
    assert set(ledger) == set(["bs_bga_parkflaechen", "referenz_bs_aufgegeben", "tu_kamera_detektionszonen",
                               "tu_kartenflaechen", "vier_staedte_parking_areas", "vier_staedte_street_references",
                               "neue_orte_parkstandorte", "gos_1eur_parkplatzflaechen"])
    assert all(entry["repaired"] == 0 for entry in ledger.values())
    # 13 synthetic detection zones: the six ticketed campuses use 12, the Bevenroder zone is dropped
    assert len(ledger["tu_kamera_detektionszonen"]["used"]) == 12 and ledger["tu_kamera_detektionszonen"]["features"] == 13
    assert list(ledger["tu_kamera_detektionszonen"]["dropped"]) == ["TU_KAM_05_01"]
    assert "Bevenroder Strasse" in ledger["tu_kamera_detektionszonen"]["dropped"]["TU_KAM_05_01"]
    assert set(ledger["vier_staedte_parking_areas"]["dropped"]) == {"se_bahnhofsplatz", "se_parkhaus", "ko_p3", "sc_burgplatz"}
    assert "conflicting" in ledger["vier_staedte_parking_areas"]["dropped"]["ko_p3"]
    assert "pocket park" in ledger["referenz_bs_aufgegeben"]["dropped"]["bs_bga_kannengiesserstrasse"]
    # the yellow parking areas are a cross-check layer: nothing used as geometry, nothing dropped for a defect
    assert ledger["tu_kartenflaechen"]["used"] == [] and ledger["tu_kartenflaechen"]["dropped"] == {}
    assert ledger["tu_kartenflaechen"]["features"] > 0


# --------------------------------------------------------------------------- D1: BgA car parks


def test_bga_zones_are_rederived_from_the_package_removed_and_added_with_the_v1_cut_outs(release):
    zones, trims = release["zones"], release["trims"]
    assert "bs_bga_kannengiesserstrasse" not in zones.index
    assert set(release["context"]["bga"]) == {"bs_bga_markthalle", "bs_bga_an_der_martinikirche",
                                              "bs_bga_jodutenstrasse_klint", "bs_bga_suedstrasse",
                                              "bs_bga_willy_brandt_platz"}
    expected = {"bs_bga_markthalle": 2500.0, "bs_bga_an_der_martinikirche": 800.0, "bs_bga_jodutenstrasse_klint": 1200.0,
                "bs_bga_suedstrasse": 1350.0, "bs_bga_willy_brandt_platz": 1600.0}
    for zone_id, area in expected.items():
        zone = zones.loc[zone_id]
        assert zone.geometry.area == pytest.approx(area, rel=SMALL_ZONE_ROUNDING), zone_id
        assert zone["geometry_source"] == "ordinance_map" and zone["source_url"] == GAZETTE
        assert (zone["source_date"], zone["digitised_on"]) == (pd.Timestamp("2026-10-07"), pd.Timestamp("2026-10-07"))
        assert release["sha256"] in zone["digitising_note"] and "bs_bga_parkflaechen" in zone["digitising_note"]
        assert zone["digitising_note"].isascii() and "uncertainty 5 m" in zone["digitising_note"]
    # the package polygon of the Martinikirche lot lies elsewhere than the v1 polygon (the neighbouring kerbside stalls)
    assert not zones.loc["bs_bga_an_der_martinikirche"].geometry.intersects(_box(300, 400, 340, 420))
    # the lots take precedence over Ia: the four lots inside Ia are cut out of it (2500 + 800 + 1200 + 1350 m2)
    assert trims["bs_zone_ia"] == pytest.approx(5850.0, abs=1.0)
    assert zones.loc["bs_zone_ia"].geometry.area == pytest.approx(900_000.0 - 5850.0, rel=FILE_ROUNDING)
    for zone_id in ("bs_bga_markthalle", "bs_bga_suedstrasse"):
        assert zones.loc["bs_zone_ia"].geometry.intersection(zones.loc[zone_id].geometry).area <= 1.0


def test_new_zone_ids_follow_the_release_order(release):
    ids = list(release["loaded"]["zone_id"])
    assert ids.index("bs_bga_willy_brandt_platz") == ids.index("bs_bga_suedstrasse") + 1
    assert ids.index("tu_campus_volkmaroder_strasse") == ids.index("tu_forschungsflughafen") + 1
    goslar = ids.index("gs_altstadt_zone1")
    assert ids[goslar + 1:goslar + 4] == ["gs_parkplatz_baeringerstrasse", "gs_parkplatz_klubgartenstrasse_zob",
                                          "gs_parkplatz_glockengiesserstrasse"]
    assert ids[-8:] == ["bh_sole_therme", "bh_kurpark", "bh_grossparkplatz", "bh_burgberg", "bh_berliner_platz",
                        "se_am_markt", "br_hexenritt", "br_wurmberg"]
    assert len(ids) == len(set(ids)) == 14 - 2 + 2 + 11


# --------------------------------------------------------------------------- D1: TU campus zones


def test_campus_zones_are_the_union_of_the_detection_zones_of_their_campus(release):
    zones = release["zones"]
    assert "tu_international_house" not in zones.index
    # zone id -> (campus, parts of the union): in Zentralcampus the zones 01_02 and 01_03 share an edge and merge
    ticketed = {"tu_zentralcampus": ["Zentralcampus", 2], "tu_campus_nord": ["Campus Nord", 1],
                "tu_campus_ost_beethovenstrasse": ["Beethovenstra\u00dfe", 2], "tu_campus_ost_langer_kamp": ["Langer Kamp", 2],
                "tu_campus_volkmaroder_strasse": ["Volkmaroder Stra\u00dfe", 1], "tu_forschungsflughafen": ["Forschungsflughafen", 2]}
    for zone_id, (campus, parts) in ticketed.items():
        union = unary_union([geometry for _, geometry, _ in CAMPUS_POLYGONS[campus]])
        zone = zones.loc[zone_id]
        assert zone["geometry_source"] == pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE, zone_id
        assert zone.geometry.symmetric_difference(union).area < 0.01 * union.area, zone_id
        assert len(getattr(zone.geometry, "geoms", [zone.geometry])) == parts, zone_id
        assert release["sha256"] in zone["digitising_note"] and "tu_kamera_detektionszonen" in zone["digitising_note"]
        assert zone["digitising_note"].startswith("TU Braunschweig, GB3 Parkbereiche campus maps") and zone["digitising_note"].isascii()
        assert "explicit consent for reuse of TU graphics not obtained; used by owner decision 2026-10-07" in zone["digitising_note"]
    # a campus whose map no GB3 page states as ticketed is no zone
    assert not any("bevenroder" in zone_id for zone_id in zones.index)
    assert release["context"]["campus"].keys() == set(ticketed)


def test_nested_campus_boundaries_use_the_outer_boundary_and_are_recorded(release):
    zone = release["zones"].loc["tu_campus_ost_beethovenstrasse"]
    outer, inner, third = _box(2000, 0, 2400, 400), _box(2100, 100, 2200, 200), _box(2500, 0, 2600, 100)
    assert zone.geometry.area == pytest.approx(outer.area + third.area, rel=SIMPLIFIED_BUFFER)  # the areas are not added
    assert zone.geometry.contains(inner.centroid)
    assert "NESTED BOUNDARIES" in zone["digitising_note"] and "OUTER boundary is used" in zone["digitising_note"]
    assert "TU_KAM_03_01" in zone["digitising_note"] and "TU_KAM_03_02" in zone["digitising_note"]
    assert "NESTED" not in release["zones"].loc["tu_campus_ost_langer_kamp", "digitising_note"]


def test_the_international_house_is_merged_into_the_langer_kamp_campus(release):
    # the v1 International House polygon lies mostly in the Langer Kamp detection zone 02_01, so the union replaces it
    zone = release["zones"].loc["tu_campus_ost_langer_kamp"]
    house = _box(2020, 520, 2120, 580)
    assert zone.geometry.intersection(house).area == pytest.approx(house.intersection(_box(2000, 500, 2100, 560)).area,
                                                                    rel=SIMPLIFIED_BUFFER)
    qa = mq.load_municipal_qa(release["qa_path"]).set_index("row_id")
    row = qa.loc["tu_international_house_v1_vs_tu_campus_ost_langer_kamp"]
    assert row["release_zone_id"] == "" and float(row["subject_share_in_reference"]) == pytest.approx(
        house.intersection(_box(2000, 500, 2100, 560)).area / house.area, abs=1e-3)


def test_campus_zones_take_precedence_over_street_zones(release):
    # the Zentralcampus detection zone 01_01 overlaps the v1 zone Ib by 50 m x 200 m: the campus keeps it
    overlap = _box(1250, 100, 1300, 300)
    assert release["trims"]["bs_zone_ib"] == pytest.approx(overlap.area, abs=1.0)
    assert release["zones"].loc["bs_zone_ib"].geometry.intersection(release["zones"].loc["tu_zentralcampus"].geometry).area <= 1.0
    assert release["zones"].loc["tu_zentralcampus"].geometry.contains(overlap.centroid)
    cut = {(loser, winner): area for loser, winner, area in release["context"]["cuts"]}
    assert cut[("bs_zone_ib", "tu_zentralcampus")] == pytest.approx(overlap.area, abs=2.0)
    assert ("tu_zentralcampus", "bs_zone_ib") not in cut


# --------------------------------------------------------------------------- D3: single paid sites


def test_single_sites_are_the_50_m_area_of_a_polygon_a_street_line_or_a_source_point(release):
    zones = release["zones"]
    sites = release["context"]["sites"]
    assert {zone_id: site["kind"] for zone_id, site in sites.items()} == {
        "gs_parkplatz_baeringerstrasse": "polygon", "gs_parkplatz_klubgartenstrasse_zob": "polygon",
        "gs_parkplatz_glockengiesserstrasse": "polygon", "bh_sole_therme": "polygon", "bh_kurpark": "polygon",
        "bh_grossparkplatz": "polygon", "bh_burgberg": "polygon", "bh_berliner_platz": "polygon",
        "se_am_markt": "street line", "br_hexenritt": "source point", "br_wurmberg": "source point"}
    for zone_id, site in sites.items():
        zone = zones.loc[zone_id]
        exact = site["geometry"].buffer(50.0)
        assert zone["geometry_source"] == pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE and zone["site_buffer_m"] == 50.0
        assert zone["digitising_note"].isascii() and release["sha256"] in zone["digitising_note"]
        assert "ASSUMPTION C-a" in zone["digitising_note"] and site["layer"] in zone["digitising_note"]
        # single sites take precedence and are cut out of nothing here: the zone is its 50 m area up to the 0.5 m
        # simplification and the rounding of the file
        assert zone.geometry.symmetric_difference(exact).area < 0.02 * exact.area, zone_id
    point = zones.loc["br_hexenritt"]
    assert point.geometry.area == pytest.approx(3.141592653589793 * 50.0 ** 2, rel=SIMPLIFIED_BUFFER)
    assert "SOURCE POINT" in point["digitising_note"] and "lot extent is unknown" in point["digitising_note"]
    line = zones.loc["se_am_markt"]
    assert line.geometry.area == pytest.approx(sites["se_am_markt"]["geometry"].buffer(50.0).area, rel=SIMPLIFIED_BUFFER)
    assert "street_reference_not_exact_paid_parking_extent" in line["digitising_note"]
    assert "ties go to the higher tariff" in zones.loc["gs_parkplatz_baeringerstrasse", "digitising_note"]
    assert "Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22); open reuse licence not verified; used by " \
           "owner decision 2026-10-07" in zones.loc["gs_parkplatz_baeringerstrasse", "digitising_note"]


def test_a_single_site_is_cut_out_of_the_area_zone_it_overlaps(release):
    # Goslar: the Glockengiesserstrasse lot (x 950-1010) lies at the eastern edge of the v1 zone 1 (x 0-1000)
    zones = release["zones"]
    lot = zones.loc["gs_parkplatz_glockengiesserstrasse"].geometry
    assert zones.loc["gs_altstadt_zone1"].geometry.intersection(lot).area <= 1.0
    assert release["trims"]["gs_altstadt_zone1"] > 5_000.0
    assert zones.loc["gs_altstadt_zone1"].geometry.area == pytest.approx(
        1_000_000.0 - release["trims"]["gs_altstadt_zone1"], rel=1e-3)
    cut = {(loser, winner): area for loser, winner, area in release["context"]["cuts"]}
    assert cut[("gs_altstadt_zone1", "gs_parkplatz_glockengiesserstrasse")] == pytest.approx(
        release["trims"]["gs_altstadt_zone1"], abs=5.0)
    # the lot keeps its own 50 m area: the cut is on the area zone's side
    exact = release["context"]["sites"]["gs_parkplatz_glockengiesserstrasse"]["geometry"].buffer(50.0)
    assert lot.symmetric_difference(exact).area < 0.02 * exact.area
    # the other two Goslar lots lie outside zone 1 and cut nothing
    assert ("gs_altstadt_zone1", "gs_parkplatz_baeringerstrasse") not in cut


def _noisy_lot(seed, n=120, radius=20.0, amplitude=0.9) -> "Polygon":
    """A detailed lot outline like a digitised one: a circle of ``n`` vertices with +-``amplitude`` m of noise."""
    import math
    import random

    from shapely.geometry import Polygon

    rng = random.Random(seed)
    ring = [(X0 + 100 + (radius + rng.uniform(-amplitude, amplitude)) * math.cos(2 * math.pi * index / n),
             Y0 + 100 + (radius + rng.uniform(-amplitude, amplitude)) * math.sin(2 * math.pi * index / n))
            for index in range(n)]
    return Polygon(ring).buffer(0)


def test_a_zone_that_loses_area_to_a_detailed_lot_loses_it_exactly(assembly):
    # Ia is simplified AFTER its cut (v1 order), which moves the edge of the hole around a detailed lot by up to 0.5 m:
    # seed 4 leaves 2.1 m2 of Ia inside the lot, above the 1 m2 tolerance of the loader (the Suedstrasse lot of the real
    # package left 6.75 m2). The exact cut subtracts the lot's final polygon once more.
    lot = _noisy_lot(4)
    zones = [assembly.zone_record("bs_bga_suedstrasse", BS, lot, "ordinance_map", "https://lot", "lot"),
             assembly.zone_record("bs_zone_ia", BS, _box(0, 0, 200, 200), "ordinance_map", "https://area", "area")]
    zones, trims = assembly.apply_precedence(zones, assembly.PRECEDENCE_REGIONAL)
    by_id = {zone["zone_id"]: zone["geometry"] for zone in zones}
    assert by_id["bs_zone_ia"].intersection(by_id["bs_bga_suedstrasse"]).area > pz.OVERLAP_TOLERANCE_M2, (
        "the layout no longer reproduces the post-cut simplification overlap; pick another noisy lot")
    removed = assembly.enforce_exact_cuts(zones)
    by_id = {zone["zone_id"]: zone["geometry"] for zone in zones}
    assert [(loser, winner) for loser, winner, _ in removed] == [("bs_zone_ia", "bs_bga_suedstrasse")]
    assert removed[0][2] > pz.OVERLAP_TOLERANCE_M2
    assert by_id["bs_zone_ia"].intersection(by_id["bs_bga_suedstrasse"]).area < 1e-6
    assert by_id["bs_zone_ia"].is_valid and by_id["bs_zone_ia"].area == pytest.approx(40_000.0 - by_id[
        "bs_bga_suedstrasse"].area, abs=1.0)
    assert assembly.enforce_exact_cuts(zones) == []  # nothing left to cut, so a second pass changes nothing


def test_the_nearer_source_decides_where_two_site_areas_overlap_ties_to_the_higher_tariff(regional):
    # a polygon, a street line and a point: two sites 80 m apart (areas overlap in a 20 m band), a third far away
    left, right = _box(0, 0, 40, 40), _box(120, 0, 160, 40)
    split = regional.split_single_sites({"left": left, "right": right, "far": _point(1000, 0)}, ["left", "right", "far"])
    assert split["competitors"]["left"] == ["right"] and split["competitors"]["far"] == []
    assert split["zones"]["left"].intersection(split["zones"]["right"]).area < 1e-3
    assert split["zones"]["left"].union(split["zones"]["right"]).area == pytest.approx(
        left.buffer(50.0).union(right.buffer(50.0)).area, abs=0.5)  # up to a 1 mm strip along the split line
    assert split["zones"]["left"].contains(Point(X0 + 80 - 2, Y0 + 20)) and split["zones"]["right"].contains(
        Point(X0 + 80 + 2, Y0 + 20))
    # the split line is the bisector x = 80 (left source at 40, right source at 120)
    assert split["zones"]["left"].bounds[2] == pytest.approx(X0 + 80.0, abs=1.0)
    assert split["zones"]["right"].bounds[0] == pytest.approx(X0 + 80.0, abs=1.0)
    assert split["zones"]["far"].area == pytest.approx(_point(1000, 0).buffer(50.0).area, rel=1e-6)
    # sources that overlap are at distance 0 from each other's area: the higher tariff keeps the overlap (the tie)
    first, second = _box(0, 0, 40, 40), _box(30, 0, 70, 40)
    for ranking in (["first", "second"], ["second", "first"]):
        tie = regional.split_single_sites({"first": first, "second": second}, ranking)
        winner, loser = ranking
        assert tie["zones"][winner].contains(_point(35, 20)) and not tie["zones"][loser].contains(_point(35, 20))
        assert tie["zones"]["first"].intersection(tie["zones"]["second"]).area < 1e-3
    # a point and a street line: the nearer source decides, whatever the ranking
    point, line = Point(X0, Y0), LineString([(X0 + 60, Y0 - 30), (X0 + 60, Y0 + 30)])
    probe = Point(X0 + 40, Y0)  # 40 m from the point, 20 m from the line
    for ranking in (["point", "line"], ["line", "point"]):
        mixed = regional.split_single_sites({"point": point, "line": line}, ranking)
        assert mixed["zones"]["line"].contains(probe) and not mixed["zones"]["point"].contains(probe)
        assert mixed["zones"]["point"].contains(Point(X0 + 5, Y0)) and mixed["zones"]["line"].contains(Point(X0 + 90, Y0))
    with pytest.raises(SystemExit, match="does not list the sites"):
        regional.split_single_sites({"left": left, "right": right}, ["left"])


def test_single_site_geometry_rates_are_logged_as_primary_and_fallback(assembly, package_dir, capsys):
    directory, sha256 = package_dir
    assembly.rz.single_sites(assembly.rz.load_package(directory, expected_sha256=sha256))
    out = capsys.readouterr().out
    assert "single-site geometry: polygon 8/11 (72.7 %), street line 1/11 (9.1 %), source point 2/11 (18.2 %)" in out
    assert "fallback: a source point, whose lot extent is unknown" in out


def test_a_zone_outside_its_municipality_needs_a_declared_exception_that_holds(assembly, release, capsys):
    municipalities = _municipalities()
    inside_harz = {"zone_id": "z", "geometry_source": "single_site_buffered", "_ags": BH,
                   "geometry": _box(1020, 10, 1080, 60, BH_X), "digitising_note": "ascii"}
    straddling = dict(inside_harz, geometry=_box(940, 10, 1000, 60, BH_X))  # 1/3 in Bad Harzburg, 2/3 in the Harz
    with pytest.raises(SystemExit, match="lies only 0.000 inside its municipality 03153002"):
        assembly.check_municipality_containment([inside_harz], municipalities)
    with pytest.raises(SystemExit, match="lies only 0.333 inside its municipality 03153002"):
        assembly.check_municipality_containment([straddling], municipalities)
    reason = "VG250 puts the outline in the gemeindefreies Gebiet"
    assembly.check_municipality_containment([dict(inside_harz, _containment_exception=(HARZ, reason)),
                                             dict(straddling, _containment_exception=(HARZ, reason))], municipalities)
    out = capsys.readouterr().out
    assert f"DECLARED EXCEPTION: inside 03153504 1.0000, inside both 1.0000 ({reason})" in out
    assert f"inside 03153002: 0.3333; DECLARED EXCEPTION: inside 03153504 0.6667, inside both 1.0000 ({reason})" in out
    assert "0/2 zones inside their own municipality, 2 declared exception(s)" in out
    # the declared municipality must really hold the rest of the zone
    for zone, message in ((dict(inside_harz, _containment_exception=(BR, "wrong municipality")),
                           "lies only 0.000 inside its municipality 03153002 and the declared municipality 03153016"),
                           (dict(straddling, geometry=_box(2900, 10, 3300, 60, BH_X), _containment_exception=(HARZ, "x")),
                            "lies only 0.250 inside its municipality 03153002 and the declared municipality 03153504")):
        with pytest.raises(SystemExit, match=message):
            assembly.check_municipality_containment([zone], municipalities)
    # the real exception: the Grossparkplatz row names Bad Harzburg and the step declares the municipality it lies in
    assert assembly.rz.CONTAINMENT_EXCEPTIONS["bh_grossparkplatz"][0] == HARZ
    assert release["context"]["exceptions"].keys() == {"bh_grossparkplatz"}
    assert release["context"]["ags"]["bh_grossparkplatz"] == BH


# --------------------------------------------------------------------------- tariff evidence of the new rows


def _row(zone_id, ags, **values):
    row = {column: None for column in pz.TARIFF_COLUMNS}
    row.update({"zone_id": zone_id, "municipality_ags": ags, "zone_type": "street_paid", "fee_window_source": "assumption",
                "notes": "ASSUMPTION F1: fee window, labelled", "resident_exempt": False})
    row.update(values)
    return row


def _tariff_rows(evidence, f1) -> pd.DataFrame:
    """Rows that carry the evidence of the package: what the sources state, and the F1 window where they state none."""
    rows = []
    for zone_id, values in evidence.items():
        window = values["window"] or tuple(f1[zone_id][:2])
        extras = {column: values[column] for column in ("free_if_stay_at_most_min", "first_period_min", "first_period_eur",
                                                        "daily_cap_eur") if values.get(column) is not None}
        rows.append(_row(zone_id, BS if zone_id.startswith("bs_") else GS, hourly_rate_eur=values["hourly_rate_eur"],
                         billing_unit_min=values["billing_unit_min"], fee_start_h=window[0], fee_end_h=window[1],
                         fee_window_source="assumption" if values["window"] is None else "municipal_page",
                         **extras))
    frame = pd.DataFrame(rows, columns=list(pz.TARIFF_COLUMNS))
    for column in pz.MINUTE_COLUMNS:
        frame[column] = pd.array(frame[column].tolist(), dtype="Int64")
    for column in pz.MONEY_COLUMNS + pz.HOUR_COLUMNS:
        frame[column] = pd.to_numeric(frame[column])
    return frame


def test_tariff_evidence_comes_from_the_package_rules(regional, release):
    evidence = regional.tariff_evidence(release["package"])
    assert set(evidence) == {"bs_bga_willy_brandt_platz", *regional.D3_ZONE_IDS}
    # BgA: 0.90 EUR per 30 min = 1.80 EUR/h, minute-exact with phone parking as at the other BgA lots, no window stated
    assert (evidence["bs_bga_willy_brandt_platz"]["hourly_rate_eur"], evidence["bs_bga_willy_brandt_platz"]["billing_unit_min"],
            evidence["bs_bga_willy_brandt_platz"]["window"]) == (1.8, 1, None)
    # Goslar: 1 EUR/h at the service page's own weekday window; the half-hour unit is ASSUMPTION D3-a (ruling R-4a-3)
    assert [(evidence[z]["hourly_rate_eur"], evidence[z]["billing_unit_min"], evidence[z]["window"]) for z in (
        "gs_parkplatz_baeringerstrasse", "gs_parkplatz_klubgartenstrasse_zob", "gs_parkplatz_glockengiesserstrasse")] == [
        (1.0, 30, (10.0, 18.0)), (1.0, 30, (10.0, 16.0)), (1.0, 30, (10.0, 18.0))]
    assert "ASSUMPTION D3-a" in evidence["gs_parkplatz_baeringerstrasse"]["billing_basis"]
    # Bad Harzburg: the ParkGO ceiling 0.50 EUR per started 30 min is the rate (ASSUMPTION D3-b), no window stated
    assert all((evidence[z]["hourly_rate_eur"], evidence[z]["billing_unit_min"], evidence[z]["window"]) == (1.0, 30, None)
               for z in regional.D3_ZONE_IDS if z.startswith("bh_"))
    # Seesen: 0.10 EUR per started 10 min = 0.60 EUR/h, the ordinance's Monday-to-Friday window 8-18 h
    assert (evidence["se_am_markt"]["hourly_rate_eur"], evidence["se_am_markt"]["billing_unit_min"],
            evidence["se_am_markt"]["window"]) == (0.6, 10, (8.0, 18.0))
    # Braunlage: the ParkGO rate without a window; the Hexenritt bands as a free limit, a first period, a stepped rate
    # and a day cap that reproduce the published total-stay bands exactly
    assert (evidence["br_wurmberg"]["hourly_rate_eur"], evidence["br_wurmberg"]["billing_unit_min"],
            evidence["br_wurmberg"]["window"]) == (1.0, 30, None)
    hexenritt = evidence["br_hexenritt"]
    assert (hexenritt["free_if_stay_at_most_min"], hexenritt["first_period_min"], hexenritt["first_period_eur"],
            hexenritt["hourly_rate_eur"], hexenritt["billing_unit_min"], hexenritt["daily_cap_eur"]) == (
        30, 120, 2.5, 1.25, 120, 10.0)
    assert hexenritt["window"] is None


def test_hexenritt_bands_that_the_row_fields_cannot_express_are_refused(regional, release):
    rules = _rules()
    for rule in rules:
        if rule["rule_id"] == "br_hexenritt_steps_br_hexenritt_3":
            rule["amount_eur"] = 8.0  # +3.00 instead of +2.50: irregular steps
    package = dict(release["package"], rules={rule["rule_id"]: rule for rule in rules})
    with pytest.raises(SystemExit, match="not regular steps"):
        regional.tariff_evidence(package)


def test_a_missing_or_ambiguous_window_is_never_guessed(regional, release):
    rules = _rules()
    ambiguous = next(rule for rule in rules if rule["rule_id"] == "se_ordinary")
    ambiguous["charging_times"]["tuesday"] = [{"start": "09:00", "end": "18:00", "crosses_midnight": False}]
    with pytest.raises(SystemExit, match="Monday to Friday state different charging times"):
        regional.tariff_evidence(dict(release["package"], rules={rule["rule_id"]: rule for rule in rules}))
    odd = _rules()
    next(rule for rule in odd if rule["rule_id"] == "goslar_zob_REV_01")["time_window"] = _window("10:00", "16:00", "Mo-So")
    with pytest.raises(SystemExit, match="neither Mo-Fr nor Mo-Sa"):
        regional.tariff_evidence(dict(release["package"], rules={rule["rule_id"]: rule for rule in odd}))
    without = [rule for rule in _rules() if rule["rule_id"] != "bh_ordinary_ceiling"]
    with pytest.raises(SystemExit, match="has no rule bh_ordinary_ceiling"):
        regional.tariff_evidence(dict(release["package"], rules={rule["rule_id"]: rule for rule in without}))


@pytest.mark.parametrize("zone_id, changes, message", [
    ("gs_parkplatz_baeringerstrasse", {"hourly_rate_eur": 1.5}, "hourly_rate_eur 1.5 but the package gives 1.0"),
    ("gs_parkplatz_baeringerstrasse", {"billing_unit_min": 60}, "ASSUMPTION D3-a"),
    ("gs_parkplatz_klubgartenstrasse_zob", {"fee_end_h": 18.0}, "fee window 10.0-18.0 but the package gives \\(10.0, 16.0\\)"),
    ("gs_parkplatz_klubgartenstrasse_zob", {"fee_window_source": "assumption"}, "must not be 'assumption'"),
    ("bh_kurpark", {"fee_window_source": "municipal_page"}, "state no fee window, so fee_window_source must be 'assumption'"),
    ("bh_kurpark", {"fee_start_h": 9.0}, "ASSUMPTION F1 gives \\(8.0, 18.0\\)"),
    ("bh_kurpark", {"notes": "a window without its label"}, "note must name ASSUMPTION F1"),
    ("bh_kurpark", {"max_stay_min": 360}, "max_stay_min must stay empty"),
    ("se_am_markt", {"long_stay_product_eur": 6.0}, "long_stay_product_eur must stay empty"),
    ("br_hexenritt", {"daily_cap_eur": None}, "daily_cap_eur \\S+ but the package gives 10.0"),
    ("br_wurmberg", {"zone_type": "campus"}, "a single paid site is street_paid"),
    ("bs_bga_willy_brandt_platz", {"billing_unit_min": 30}, "billing_unit_min 30 but 1"),
], ids=["wrong_rate", "billing_unit_not_the_ruled_one", "window_end_differs", "sourced_window_labelled_assumption",
        "assumed_window_labelled_sourced", "assumed_window_differs_from_f1", "assumed_window_without_the_label",
        "maximum_stay_filled", "long_stay_product_filled", "day_cap_missing", "wrong_zone_type", "bga_billing_unit"])
def test_the_new_tariff_rows_must_carry_the_values_the_package_gives(regional, release, capsys, zone_id, changes, message):
    evidence = regional.tariff_evidence(release["package"])
    table = _tariff_rows(evidence, regional.F1_WINDOWS)
    regional.check_tariff_rows(evidence, table)
    assert "fee window sourced 4/12" in capsys.readouterr().out  # Goslar x3 and Seesen; ASSUMPTION F1 on the other 8
    broken = table.copy()
    for column, value in changes.items():
        broken[column] = broken[column].astype(object)
        broken.loc[broken["zone_id"] == zone_id, column] = value if value is not None else pd.NA
    broken = broken.astype({column: table[column].dtype for column in pz.MINUTE_COLUMNS})
    with pytest.raises(SystemExit, match=message):
        regional.check_tariff_rows(evidence, broken)


def test_a_missing_tariff_row_is_reported(regional, release):
    evidence = regional.tariff_evidence(release["package"])
    table = _tariff_rows(evidence, regional.F1_WINDOWS)
    with pytest.raises(SystemExit, match="tariff rows missing for the zones \\['se_am_markt'\\]"):
        regional.check_tariff_rows(evidence, table[table["zone_id"] != "se_am_markt"])


# --------------------------------------------------------------------------- the written release and its QA table


def test_the_release_is_valid_as_written_and_its_zones_do_not_overlap(release):
    zones = release["loaded"]
    pz.validate_zone_polygons(zones)  # pairwise overlap <= 1 m2, valid polygons
    assert set(zones["geometry_source"]) == {"ordinance_map", "campus_detection_zones", "centre_approximation",
                                             "single_site_buffered"}
    tariffs = pd.DataFrame({"zone_id": zones["zone_id"], "zone_type": [
        "campus" if source == "campus_detection_zones" else "street_paid" for source in zones["geometry_source"]]})
    pz.validate_geometry_source_zone_types(zones, tariffs)


def test_release_licence_names_the_regional_sources(release):
    document = json.loads(release["zone_path"].read_text(encoding="utf-8"))
    assert "every polygon is derived from OpenStreetMap" not in document["license"]
    for wording in ("Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22); open reuse licence not verified; "
                    "used by owner decision 2026-10-07",
                    "explicit consent for reuse of TU graphics not obtained; used by owner decision 2026-10-07",
                    "Stadt Braunschweig, Amtsblatt 2022 Nr. 16 (annex maps of the BgA Entgeltordnung B 660); digitised "
                    "(owner-supplied package 2026-10-07); working accuracy 5 m; base map Open GeoData dl-de/by-2-0"):
        assert wording in document["license"]
    assert "Stadt Braunschweig - Open GeoData" in document["attribution"]
    # the municipal and the regional wording join at a sentence boundary: one full stop, not two
    assert ".." not in document["license"] and ".." not in document["attribution"]
    # the BgA lots cut out of bs_zone_ia are the package polygons now, no longer the OSM outlines of v1
    assert "the OSM car-park outlines of the BgA zones" not in document["license"]
    assert "(the BgA car parks of the regional evidence package are cut out of bs_zone_ia)" in document["license"]
    assert release["zone_path"].read_text(encoding="utf-8").isascii()


def test_the_regional_step_needs_the_municipal_step(assembly):
    frame = gpd.GeoDataFrame({"zone_id": ["a"], "geometry_source": ["ordinance_map"], "geometry": [_box(0, 0, 1, 1)]},
                             crs=METRIC_CRS)
    with pytest.raises(SystemExit, match="builds on the municipal step"):
        assembly.write_zone_file(frame, Path("unused.geojson"), regional=True)
    with pytest.raises(SystemExit, match="--regional-dir builds on --municipal-dir"):
        assembly.main(["--ia-ib", "a", "--affine", "b", "--fee-islands", "c", "--raw-overpass", "d", "--network", "e",
                       "--municipalities", "f", "--out", "g", "--regional-dir", "h"])


def test_qa_table_compares_the_new_polygons_and_records_every_cut(release):
    qa = mq.load_municipal_qa(release["qa_path"])
    mq.validate_municipal_qa(qa, release["loaded"])
    assert release["qa_path"].read_text(encoding="utf-8").isascii()
    qa = qa.set_index("row_id")

    def numbers(row_id):
        row = qa.loc[row_id]
        return tuple(float(row[column]) for column in ("subject_area_m2", "reference_area_m2", "overlap_area_m2"))

    # BgA: the release polygon is the package polygon; the Markthalle v1 polygon (3,600 m2) holds the 2,500 m2 lot
    assert numbers("bs_bga_markthalle_release_vs_package") == pytest.approx((2500.0, 2500.0, 2500.0), rel=1e-3)
    assert numbers("bs_bga_markthalle_release_vs_v1") == pytest.approx((2500.0, 3600.0, 2500.0), rel=1e-3)
    assert numbers("bs_bga_an_der_martinikirche_release_vs_v1")[2] == 0.0
    assert "bs_bga_willy_brandt_platz_release_vs_v1" not in qa.index  # a new zone has no v1 polygon
    assert numbers("bs_bga_kannengiesserstrasse_v1_vs_abandoned_lot") == pytest.approx((2000.0, 1600.0, 1600.0), rel=1e-6)
    assert "pocket park" in qa.loc["bs_bga_kannengiesserstrasse_v1_vs_abandoned_lot", "note"]
    # campus zones: against the detection zones, the v1 polygon and the yellow parking areas
    union = unary_union([geometry for _, geometry, _ in CAMPUS_POLYGONS["Zentralcampus"]])
    # a campus takes precedence over the street zone Ib it overlaps, so nothing is cut from it: release = union
    assert numbers("tu_zentralcampus_release_vs_detection_zones") == pytest.approx(
        (union.area, union.area, union.area), rel=SIMPLIFIED_BUFFER)
    assert (qa.loc["tu_zentralcampus_release_vs_detection_zones", "reference_features"],
            qa.loc["tu_zentralcampus_release_vs_detection_zones", "reference_features_overlapping"]) == ("3", "3")
    assert numbers("tu_zentralcampus_release_vs_v1")[2] == pytest.approx(  # the v1 centre approximation holds Z1 only
        _box(1250, 100, 1450, 300).intersection(_box(1200, 0, 1500, 400)).area, rel=SIMPLIFIED_BUFFER)
    # the synthetic yellow area of Campus Nord lies half outside the campus: the cross-check reports the conflict
    assert float(qa.loc["tu_campus_nord_yellow_areas_in_release", "subject_share_in_reference"]) == pytest.approx(0.5, abs=1e-3)
    assert float(qa.loc["tu_zentralcampus_yellow_areas_in_release", "subject_share_in_reference"]) == pytest.approx(1.0, abs=1e-4)
    assert qa.loc["tu_campus_nord_yellow_areas_in_release", "release_zone_id"] == ""
    nested = qa.loc["tu_campus_ost_beethovenstrasse_nested_boundaries"]
    assert float(nested["subject_area_m2"]) == pytest.approx(10_000.0) and float(nested["subject_share_in_reference"]) == 1.0
    assert qa.loc["tu_bevenroder_strasse_detection_zone_not_zoned", "overlap_area_m2"] == "0.0"
    assert "no GB3 page states ticketing" in qa.loc["tu_bevenroder_strasse_detection_zone_not_zoned", "note"]
    # single sites: the release polygon against the 50 m area of its source
    for zone_id in release["context"]["sites"]:
        row = qa.loc[f"{zone_id}_release_vs_50m_area"]
        assert row["release_zone_id"] == zone_id
        assert float(row["reference_share_in_subject"]) > 0.97, zone_id
    assert "NAVIGATION POINT" in qa.loc["br_hexenritt_release_vs_50m_area", "note"]
    # every cut that involves a zone of the step is a row, measured against the final polygon of the winner
    for loser, winner, area in release["context"]["cuts"]:
        row = qa.loc[f"{loser}_cut_by_{winner}"]
        # the release is read back from the rounded file (up to a few tenths of a m2 on a 2,500 m2 lot)
        assert float(row["overlap_area_m2"]) == pytest.approx(area, abs=1.0) and row["release_zone_id"] == ""
    assert {"bs_zone_ia_cut_by_bs_bga_markthalle", "bs_zone_ib_cut_by_tu_zentralcampus",
            "gs_altstadt_zone1_cut_by_gs_parkplatz_glockengiesserstrasse"} <= set(qa.index)
    # the declared municipality exception is a pair of rows
    # the 50 m area (x 950-1150) crosses the boundary of the gemeindefreies Gebiet at x = 960: about 4 % lie in Bad Harzburg
    release_zone = release["zones"].loc["bh_grossparkplatz", "geometry"]
    shares = {ags: float(qa.loc[f"bh_grossparkplatz_release_vs_municipality_{ags}", "subject_share_in_reference"])
              for ags in (BH, HARZ)}
    for ags, share in shares.items():
        assert share == pytest.approx(release_zone.intersection(_municipalities().loc[ags, "geometry"]).area
                                      / release_zone.area, abs=1e-4)
    assert 0.02 < shares[BH] < 0.08 and shares[BH] + shares[HARZ] == pytest.approx(1.0, abs=1e-4)
    header = [line for line in release["qa_path"].read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    for column in mq.MUNICIPAL_QA_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column
    assert "Spec Amendment D (the regional evidence package of 2026-10-07" in " ".join(header)


def test_the_qa_table_must_hold_rows_for_the_new_polygons(release):
    qa = mq.load_municipal_qa(release["qa_path"])
    for dropped in ("tu_zentralcampus", "gs_parkplatz_baeringerstrasse"):
        without = qa[qa["release_zone_id"] != dropped]
        with pytest.raises(ValueError, match=f"{dropped}.*are the release subject of no row"):
            mq.validate_municipal_qa(without, release["loaded"])


def test_two_runs_of_the_step_write_identical_bytes(assembly, package_dir, tmp_path):
    directory, sha256 = package_dir
    written = []
    for run in ("first", "second"):
        (tmp_path / run).mkdir()  # the layer name of a GeoJSON file is its file name: equal names, equal bytes
        package = assembly.rz.load_package(directory, expected_sha256=sha256)
        zones, context = assembly.apply_regional_package(_v1_zones(assembly), package)
        before = {zone["zone_id"]: zone["geometry"] for zone in zones}
        zones, _ = assembly.apply_precedence(zones, assembly.PRECEDENCE_REGIONAL)
        assembly.enforce_exact_cuts(zones)
        assembly.record_regional_cuts(context, before, zones, assembly.PRECEDENCE_REGIONAL)
        path = tmp_path / run / "parking_zones_2026.geojson"
        assembly.write_zone_file(assembly.zone_frame(zones), path, municipal=True, regional=True)
        loaded = pz.load_zone_polygons(path, max_repairs=0)
        qa_path = tmp_path / run / "qa.csv"
        assembly.mz.write_qa_table(qa_path, assembly.rz.qa_rows(context, loaded, _municipalities()))
        written.append((path.read_bytes(), qa_path.read_bytes()))
    assert written[0] == written[1]


def test_without_the_regional_step_the_zone_file_has_no_site_buffer_column(assembly):
    zones = [assembly.zone_record("a", BS, _box(0, 0, 10, 10), "ordinance_map", "https://v1", "v1 note")]
    frame = assembly.zone_frame(zones)
    assert "site_buffer_m" not in frame.columns and "section_buffer_m" not in frame.columns
    assert assembly.precedence_order(zones, ["b", "a"]) == ["a"]
