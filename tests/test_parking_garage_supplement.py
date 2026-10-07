"""Supplement package of the garage dataset (parking cost zones v2, spec Amendments E10 to E12, issue #436, Task 4b2).

The owner's supplement package ``Parkhaus_Ergaenzungen_2026-10-07.zip`` is gitignored, so the second input of the curation
step ``regional_garages.py`` (``garage_supplement.py``) is pinned on a synthetic package written the way the owner supplied it
(a zip with ``manifest.sha256``, ``data/entrances.geojson``, ``data/parking_patch.gpkg`` with the layers ``entrances`` and
``reference_points``, the JSON tables ``facility_updates``, ``tariff_rules``, ``field_decisions``, ``tariff_observations`` and
``sources`` and two evidence texts) next to a synthetic regional package. What is pinned: the verification of the package and
of every member that is read, the main points (never the reference points), the identity rules of the package README
(``legacy_ids``, the airport key, the Groepern key), the conversion of the supplement rules to the regional rule schema, the
release of a rule by an owner decision and its field decision, the brochure tariff with its quotations, the grace-period form
(ASSUMPTION P10), the rest tier of a day rate, the readings and the QA rows. The committed dataset is pinned independently in
``test_parking_regional_garages.py``.
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
from shapely.geometry import Point, Polygon

from braunschweig.parking import garage_qa as pq
from braunschweig.parking import garages as pg

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
PACKAGE = "Regional_Parkdaten_Belege_2026-10-07"
SUPPLEMENT = "Parkhaus_Ergaenzungen_2026-10-07"
X0, Y0 = 604_000.0, 5_790_000.0
BS, WOB, HE, GS = "03101000", "03103000", "03154028", "03153017"
PULP = "https://www.braunschweig.de/apps/pulp/result/parkhaeuser.geojson"
GEOVIEWER = "https://geoviewer.stadt.wolfsburg.de/default/ows/projects/gpt/parken"
ARCGIS = "https://services3.arcgis.com/X0EQlGp2g40JN62M/arcgis/rest/services/Bewohnerparken/FeatureServer/2"
CITY = "https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/parkhaeuser.php"
BROCHURE_URL = "https://www.stadt-helmstedt.de/fileadmin/user_upload/02_Kultur/pdf_Broschueren/Parken_in_Helmstedt.pdf"
OPERATOR_PAGE = "https://www.parkhausambankplatz.de/"
BROCHURE_MEMBER = "evidence/he_coordinates/raw/stadt_helmstedt_parken.txt"
OPERATOR_MEMBER = "evidence/bs_coordinates/raw/operator_steinstrasse.html"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "public_holidays")

# the brochure text as the package keeps it: columns, the ligature of 'Oeffnungszeit' and the line breaks of the table
BROCHURE_TEXT = (
    "Parkplatz            Plaetze Art der Plaetze        Kosten          Hoechstparkdauer   gebuehren-pflichtige Zeit\n"
    "\n"
    "                                                       0,60€ / 30 Min.                           Öﬀnungszeit:\n"
    "   Edelhoefe (Parkhaus)        17     Parkschein                             unbegrenzt\n"
    "                                                     Tagesgebühr 8,00€                            24 Stunden\n"
    "\n"
    "                                                        0,70€ / 1 Std.                           Öﬀnungszeit:\n"
    "   Gröpern (Tiefgarage)       118    Parkschein                             unbegrenzt\n"
    "                                                     jede weitere 0,30€                           24 Stunden\n"
    "\n"
    " Stobenstraße (Parkdeck)      64     Parkschein        0,80€ / 1 Std.       unbegrenzt      Mo.-Sa.: 7.00 - 19.00 Uhr\n")
OPERATOR_HTML = "<html><body><p>Parkhaus am Bankplatz</p><p>Betreiber: AGR Parking UG</p></body></html>"


@pytest.fixture(scope="module")
def garages_step():
    """The curation step as a module (it imports its siblings, ``garage_supplement`` among them, from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("regional_garages_supplement_under_test",
                                                      CURATION_DIR / "regional_garages.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture(scope="module")
def sup(garages_step):
    return garages_step.sup


# --------------------------------------------------------------------------- the synthetic regional package


def _rule(rule_id, rule_type, facility, amount=None, *, unit=None, rounding=None, start=None, end=None, cap=None, period=None,
          times=None, window=None, monthly=None, status="current_primary_source", source="https://op.example/tariff",
          retrieved="2026-10-07", preferred=True) -> dict:
    return {"rule_id": rule_id, "facility_id": facility, "rule_type": rule_type, "amount_eur": amount,
            "billing_unit_minutes": unit, "rounding": rounding, "elapsed_from_minutes": start, "elapsed_to_minutes": end,
            "max_stay_minutes": None, "daily_cap_eur": cap, "cap_period": period,
            "charging_times": times or {day: None for day in WEEKDAYS}, "monthly_price_eur": monthly,
            "minimum_contract_months": None, "conditions": None, "source_url": source, "retrieved_at": retrieved,
            "status": status, "preferred_for_current_use": preferred,
            "time_window": window or {"from": None, "to": None, "days_raw": None}, "raw_rule": {}}


def _times(start, end, days=WEEKDAYS[:6]) -> dict:
    times = {day: None for day in WEEKDAYS}
    for day in days:
        times[day] = [{"start": start, "end": end, "crosses_midnight": False}]
    return times


def _regional_rules() -> list:
    return [
        # the airport: the feed's free 15 min, 1.50 per hour without a stated rounding, the hidden-template day ticket 15.00
        _rule("air-5", "free_period", "BS_SOURCE_AIR", 0.0, start=0, end=15, source=PULP),
        _rule("air-6", "increment", "BS_SOURCE_AIR", 1.5, unit=60, source=PULP),
        _rule("air-7", "daily_rate_published", "BS_SOURCE_AIR", 15.0, cap=15.0, period="unspecified_day", source=PULP),
        # the Suedkopf-Center: free 30 min, 1.00 per hour
        _rule("sued-1", "free", "WOB_SUED", 0.0, start=0, end=30),
        _rule("sued-2", "increment", "WOB_SUED", 1.0, unit=60),
        # the Poststrasse: day rate, day tariff, an unresolved night tariff (not preferred), a day-and-night cap
        _rule("post-1", "increment", "WOB_POST", 2.0, unit=60, rounding="started_unit"),
        _rule("post-2", "published_day_tariff", "WOB_POST", 9.0, period="day_definition_unspecified"),
        _rule("post-3", "unresolved_night_tariff", "WOB_POST", 1.0, window={"from": "21:00", "to": "06:30", "days_raw": None},
              preferred=False),
        _rule("post-4", "cap", "WOB_POST", 10.0, cap=10.0, period="day_and_night_definition_unspecified"),
        # Goslar: the tourism page (the first hour, the 24 h price)
        _rule("gal-1", "published_duration_tariff", "GS_GAL", 1.5, unit=60, end=60, period="one_hour"),
        _rule("gal-2", "published_duration_tariff", "GS_GAL", 15.0, end=1440, period="24_hours"),
        _rule("ach-1", "published_duration_tariff", "GS_ACH", 15.0, end=720, period="12_hours", preferred=False),
        # the four garages without coordinates in the regional package
        _rule("add1-first", "duration_total", "BS_ADDITIONAL_1", 1.0, start=0, end=60),
        _rule("add1-next", "increment", "BS_ADDITIONAL_1", 0.5, unit=30, start=60),
        _rule("add1-day", "published_day_tariff", "BS_ADDITIONAL_1", 10.0, period="day_definition_unspecified"),
        _rule("add2-hour", "published_hourly_rate", "BS_ADDITIONAL_2", 1.8, unit=60),
        _rule("add2-day", "published_day_tariff", "BS_ADDITIONAL_2", 18.0, period="day_definition_unspecified"),
        _rule("stoben-fee", "published_tariff", "HE_STOBEN", 0.8, unit=60, times=_times("07:00", "19:00")),
        _rule("decoy-fee", "published_tariff", "HE_GROEPERN_STRASSE", 0.5, unit=60),
        _rule("plain-1", "increment", "BS_PLAIN", 1.0, unit=60, rounding="started_unit", times=_times("08:00", "20:00")),
    ]


def _facilities() -> list:
    def record(facility_id, name, rules, capacity=None, scope=None, refs=()):
        return {"facility_id": facility_id, "name": name, "tariff_rule_ids": rules, "capacity": capacity,
                "capacity_scope": scope, "capacity_observations": [], "attributes": {}, "geometry_refs": list(refs)}

    return [
        record("BS_SOURCE_AIR", "Parkhaus Flug", ["air-5", "air-6", "air-7"], refs=["bs_parkhaeuser:1"]),
        record("WOB_SUED", "Parkhaus Sued", ["sued-1", "sued-2"], refs=["wob_parkhaeuser:1"]),
        record("WOB_POST", "Parkhaus Post", ["post-1", "post-2", "post-3", "post-4"], 956, "total",
               refs=["wob_parkhaeuser:2"]),
        record("GS_GAL", "Parkhaus Galeria", ["gal-1", "gal-2"], 193, "secondary_directory_total", refs=["region_parkhaeuser:1"]),
        record("GS_ACH", "Parkhaus Achtermann", ["ach-1"], refs=["region_parkhaeuser:2"]),
        record("GOS_POINT_4", "Parkhaus Charley", [], refs=["gos_parking_locations:1"]),
        record("BS_ADDITIONAL_1", "Parkhaus Lange Strasse Sued", ["add1-first", "add1-next", "add1-day"]),
        record("BS_ADDITIONAL_2", "Parkhaus Steinstrasse", ["add2-hour", "add2-day"]),
        record("HE_STOBEN", "Stobenstrasse (Parkdeck)", ["stoben-fee"], 64, "spaces_listed_for_this_row"),
        record("HE_GROEPERN_STRASSE", "Groepern Strassenabschnitt", ["decoy-fee"], 18, "spaces_listed_for_this_row"),
        record("BS_PLAIN", "Parkhaus Plain", ["plain-1"], 300, "total", refs=["bs_parkhaeuser:2"]),
        record("BS_None", "Parkhaus Flug / Ring (the ambiguous facility of the real package)", []),
    ]


def _point_layer(rows: list) -> gpd.GeoDataFrame:
    frame = pd.DataFrame([{key: value for key, value in row.items() if key != "xy"} for row in rows])
    return gpd.GeoDataFrame(frame, geometry=[Point(X0 + row["xy"][0], Y0 + row["xy"][1]) for row in rows], crs=METRIC_CRS)


def _layers() -> dict:
    bs = _point_layer([{"facility_id": "BS_SOURCE_AIR", "name": "Parkhaus Flug", "source_id": "air",
                        "retrieved_at_utc": "2026-10-07T05:24:39+00:00", "xy": (100, 100), "geometry_source_url": PULP,
                        "geometry_source_id": "src_pulp", "source_url": PULP},
                       {"facility_id": "BS_PLAIN", "name": "Parkhaus Plain", "source_id": "plain",
                        "retrieved_at_utc": "2026-10-07T05:24:39+00:00", "xy": (700, 100), "geometry_source_url": PULP,
                        "geometry_source_id": "src_pulp", "source_url": PULP}])
    wob_base = {"geometry_source_url": GEOVIEWER, "geometry_source_id": "src_wob", "source_url": GEOVIEWER,
                "retrieved_at_utc": "2026-10-07T05:24:49+00:00"}
    wob = _point_layer([{"facility_id": "WOB_SUED", "name": "Parkhaus Sued", "xy": (10_000, 100), **wob_base},
                        {"facility_id": "WOB_POST", "name": "Parkhaus Post", "xy": (10_500, 100), **wob_base}])
    region = _point_layer([
        {"facility_id": "GS_GAL", "name": "Parkhaus Galeria", "operator": None, "geometry_method": "municipal_point_2018",
         "geometry_source_url": ARCGIS, "retrieved_on": "2026-10-02", "xy": (20_000, 100)},
        {"facility_id": "GS_ACH", "name": "Parkhaus Achtermann", "operator": None, "geometry_method": "municipal_point_2018",
         "geometry_source_url": ARCGIS, "retrieved_on": "2026-10-02", "xy": (20_500, 100)}])
    gos = _point_layer([
        {"facility_id": "GOS_POINT_4", "Bezeichnung": "Parkhaus Charley", "Nutzung": "Parkhaus", "geometry_source_url": ARCGIS,
         "geometry_source_id": "src_gos", "xy": (21_000, 100)},
        {"facility_id": "GOS_POINT_3", "Bezeichnung": "Parkplatz Bolzen", "Nutzung": "Parkplatz", "geometry_source_url": ARCGIS,
         "geometry_source_id": "src_gos", "xy": (21_500, 100)}])
    return {"bs_parkhaeuser": bs, "wob_parkhaeuser": wob, "region_parkhaeuser": region, "gos_parking_locations": gos,
            "wob_parkplaetze": _point_layer([
                {"facility_id": f"WOB_PARK_{number}", "name": "Parkplatz", "tariff_rule_ids": "[]", "xy": (10_000, 500 + number)}
                for number in range(3)])}


def _write_regional(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    gpkg = directory / "Regional_Parkdaten.gpkg"
    for layer, frame in _layers().items():
        frame.to_file(gpkg, layer=layer, driver="GPKG")
    path = directory / f"{PACKAGE}.zip"
    sources = [{"source_id": "src_pulp", "url": PULP, "retrieval_dates": ["2026-10-07"]},
               {"source_id": "src_wob", "url": GEOVIEWER, "retrieval_dates": ["2026-10-02", "2026-10-07"]},
               {"source_id": "src_gos", "url": ARCGIS, "retrieval_dates": ["2026-10-01", "2026-10-02"]}]
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(gpkg, f"{PACKAGE}/daten/Regional_Parkdaten.gpkg")
        archive.writestr(f"{PACKAGE}/daten/tariff_rules.json", json.dumps({"schema_version": "2.0", "rules": _regional_rules()}))
        archive.writestr(f"{PACKAGE}/daten/facilities.json", json.dumps(_facilities()))
        archive.writestr(f"{PACKAGE}/daten/sources.json", json.dumps(sources))
    gpkg.unlink()
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- the synthetic supplement package

#: Main points (EPSG:25832, offsets from X0, Y0) of the four garages without coordinates, and the alternatives that must never
#: count: the point type tells an OSM-mapped entrance from a derived access point.
ENTRANCES = (
    ("ADD1_ENTRANCE", "BS_ADDITIONAL_1", (300, 300), "mapped_parking_entrance", True, 310231874, 17, None, None),
    ("ADD2_ENTRANCE", "BS_ADDITIONAL_2", (350, 350), "mapped_parking_entrance", True, 443311265, 5, None, None),
    ("STOBEN_ACCESS", "HE_STOBEN", (30_000, 300), "inferred_driveway_connection", False, 3001883721, 1, 64, "facility_total"),
    ("GROEPERN_ACCESS", "HE_GROEPERN_TG_118", (30_050, 320), "mapped_parking_entrance", True, 771725640, 11, 118,
     "facility_total"))


def _entrance_rows(role="primary") -> list:
    rows = []
    for point_id, facility, xy, point_type, mapped, osm_id, version, capacity, scope in ENTRANCES:
        rows.append({"point_id": point_id if role == "primary" else point_id.replace("ENTRANCE", "MARKER").replace(
            "ACCESS", "ALT"), "facility_id": facility, "name": f"Name of {facility}", "city": "x", "role": role,
            "point_type": point_type if role == "primary" else "address_point", "entrance_mapped": mapped,
            "field_verified": False, "accuracy_m": None, "capacity": capacity, "capacity_scope": scope,
            "osm_id": str(osm_id), "osm_version": version, "osm_timestamp": "2025-08-08T21:29:23Z",
            "source_id": f"osm_node_{osm_id}", "source_url": f"https://www.openstreetmap.org/node/{osm_id}",
            "retrieved_at": "2026-10-07T16:02:42+00:00", "geometry_is_tariff_extent": False,
            "xy": (xy[0] + (0 if role == "primary" else 40), xy[1])})
    return rows


def _sources() -> list:
    def source(source_id, url, retrieved="2026-10-07T16:00:00+00:00"):
        return {"source_id": source_id, "id": source_id, "url": url, "retrieved_at": retrieved}

    return [source("city_parking", CITY), source("operator_steinstrasse", OPERATOR_PAGE),
            source("HE_CITY_PARKING", BROCHURE_URL, "2026-10-07T16:00:31.645995+00:00"),
            source("airop", "https://www.struktur-foerderung.de/parken"), source("provider_faq", "https://www.mh-parkservice.com/faq"),
            source("sued_city", "https://www.wolfsburg.de/mobilitaetverkehr/parken"),
            source("post_op", "https://www.sabaparking.com/de/parkplatze-wolfsburg/saba-parkhaus-poststrasse"),
            source("gs5", "https://locator.uberall.com/api/storefinders/x/locations/4123065", "2026-10-07"),
            source("gs7", "https://www.wvv.de/parking/facilities.json", "2026-10-07"),
            source("gs1", "https://www.meingoslar.de/service", "2026-10-07")]


def _supplement_rule(rule_id, facility, scope, price=None, unit=None, rounding=None, **extra) -> dict:
    rule = {"rule_id": rule_id, "facility_id": facility, "scope": scope, "currency": "EUR", "price_eur": price,
            "billing_unit_minutes": unit, "rounding": rounding, "free_period_minutes": None,
            "free_period_deducted_after_threshold": None, "billing_origin": None, "daily_cap_eur": None, "cap_period": None,
            "max_stay_minutes": None, "fee_times_by_weekday": None, "monthly_eur": None, "capacity": None,
            "effective_from": None, "retrieved_on": "2026-10-07", "timezone": "Europe/Berlin", "source_ids": ["airop"],
            "status": "operator_published_partial", "full_cost_calculation_ready": False}
    rule.update(extra)
    return rule


def _night_times() -> list:
    return [{"iso_weekday": day, "start": "21:00", "end": "06:30", "ends_next_day": True} for day in range(1, 8)]


def _supplement_rules() -> list:
    return [
        _supplement_rule("AIR_REGULAR", "BS_FORSCHUNGSFLUGHAFEN", "regular", 1.5, 60, "ceil", free_period_minutes=15,
                         daily_cap_eur=18, capacity=650),
        _supplement_rule("AIR_WEEK", "BS_FORSCHUNGSFLUGHAFEN", "prebooked_seven_days", 99, 10080, None,
                         status="operator_published"),
        _supplement_rule("SUED_REGULAR", "WOB_SUEDKOPF", "regular", 1.0, 60, None, free_period_minutes=30,
                         source_ids=["sued_city"]),
        _supplement_rule("POST_NIGHT", "WOB_POST", "night_only", 1.0, 60, None, fee_times_by_weekday=_night_times(),
                         source_ids=["post_op", "sued_city"]),
        _supplement_rule("GAL_REGULAR", "GS_GAL", "regular", 1.5, 60, None, source_ids=["gs5", "gs7"],
                         stages=[{"start_minute": 0, "end_minute": 60, "price_eur": 1.5, "billing_unit_minutes": 60},
                                 {"start_minute": 60, "end_minute": None, "price_eur": 1.5, "billing_unit_minutes": 60}]),
        _supplement_rule("GAL_MONTHLY", "GS_GAL", "monthly_product", monthly_eur=39, source_ids=["gs5"],
                         eligibility="contract_required"),
        _supplement_rule("ACH_UNRESOLVED", "GS_ACH", "regular", source_ids=["gs1"], status="unresolved_no_operator_tariff"),
        _supplement_rule("GOS4_UNRESOLVED", "GOS_POINT_4", "regular", source_ids=["gs1"],
                         status="unverified_identity_and_current_tariff"),
    ]


def _decision(decision_id, facility, field, status) -> dict:
    return {"decision_id": decision_id, "facility_id": facility, "field": field, "value": None, "status": status,
            "source_ids": ["airop"], "retrieved_on": "2026-10-07"}


def _decisions() -> list:
    return [_decision("AIR:rounding", "BS_FORSCHUNGSFLUGHAFEN", "rounding", "resolved"),
            _decision("AIR:cap", "BS_FORSCHUNGSFLUGHAFEN", "daily_cap_eur", "resolved_visible_operator_block"),
            _decision("AIR:deduction", "BS_FORSCHUNGSFLUGHAFEN", "free_period_deducted_after_threshold",
                      "open_with_general_operator_indication"),
            _decision("POST:unit", "WOB_POST", "night_billing_unit_minutes", "supported_by_municipal_source_currentness_unclear"),
            _decision("GAL:rate", "GS_GAL", "subsequent_price_eur_per_60_minutes", "resolved_operator_sources"),
            _decision("GAL:cap", "GS_GAL", "daily_cap_eur", "conflict"),
            _decision("GAL:monthly", "GS_GAL", "monthly_eur", "published_terms_unverified")]


def _observations() -> dict:
    return {"research_date": "2026-10-07", "operator_and_secondary_variants": [
        {"facility_id": "GS_GAL", "reported_variants": [
            {"variant_id": "GAL_DIRECT", "source_ids": ["gs5"], "daily_cap_amount": 8, "daily_cap_clock_basis": None},
            {"variant_id": "GAL_WVV", "source_ids": ["gs7"], "daily_cap_amount": 15, "daily_cap_clock_basis": "calendar_day"},
            {"variant_id": "GAL_TOURISM", "source_ids": ["gs1"], "twenty_four_hour_amount": 15}]}]}


def _facility_updates() -> list:
    def update(facility_id, legacy, point, **extra):
        record = {"facility_id": facility_id, "legacy_ids": legacy, "name": f"Name of {facility_id}", "city": "x",
                  "primary_point_id": point, "operator": None, "capacity": None, "source_ids": []}
        record.update(extra)
        return record

    return [update("BS_ADDITIONAL_1", ["BS_ADDITIONAL_1"], "ADD1_ENTRANCE", name="Parkhaus Lange Straße Süd"),
            update("BS_ADDITIONAL_2", ["BS_ADDITIONAL_2"], "ADD2_ENTRANCE", operator="AGR Parking UG"),
            update("HE_STOBEN", ["HE_STOBEN"], "STOBEN_ACCESS", capacity=64),
            update("HE_GROEPERN_TG_118", [], "GROEPERN_ACCESS", capacity=118, name="Tiefgarage Gröpern"),
            update("BS_FORSCHUNGSFLUGHAFEN", ["BS_SOURCE_AIR"], None, capacity=650),
            update("WOB_SUEDKOPF", ["WOB_SUED"], None), update("WOB_POST", ["WOB_POST"], None),
            update("GS_GAL", ["GS_GAL"], None, capacity=194), update("GS_ACH", ["GS_ACH"], None),
            update("GOS_POINT_4", ["GOS_POINT_4"], None)]


def _manifest(members: dict) -> str:
    return "".join(f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in sorted(members.items()))


def _write_supplement(directory: Path, *, drop_member=None, tamper=None, entrances_rows=None, geojson_shift_m=0.0,
                      facility_updates=None) -> tuple:
    """Write the synthetic supplement zip; returns (path, SHA-256). ``tamper`` names a member whose bytes change after the
    manifest was written; ``geojson_shift_m`` moves the GeoJSON points away from the GeoPackage points."""
    directory.mkdir(parents=True, exist_ok=True)
    rows = entrances_rows if entrances_rows is not None else _entrance_rows()
    gpkg = directory / "parking_patch.gpkg"
    _point_layer(rows).to_file(gpkg, layer="entrances", driver="GPKG")
    _point_layer(_entrance_rows("reference")).to_file(gpkg, layer="reference_points", driver="GPKG")
    geojson = _point_layer(rows).to_crs("EPSG:4326")
    geojson = geojson.set_geometry([Point(point.x + geojson_shift_m / 111_000.0, point.y) for point in geojson.geometry])
    members = {
        "data/parking_patch.gpkg": gpkg.read_bytes(),
        "data/entrances.geojson": geojson.to_json().encode("utf-8"),
        "data/facility_updates.json": json.dumps({"facilities": facility_updates or _facility_updates()}).encode(),
        "data/tariff_rules.json": json.dumps({"rules": _supplement_rules()}).encode(),
        "data/field_decisions.json": json.dumps({"decisions": _decisions()}).encode(),
        "data/tariff_observations.json": json.dumps(_observations()).encode(),
        "data/sources.json": json.dumps({"sources": _sources()}).encode(),
        BROCHURE_MEMBER: BROCHURE_TEXT.encode("utf-8"), OPERATOR_MEMBER: OPERATOR_HTML.encode("utf-8")}
    gpkg.unlink()
    manifest = _manifest(members)
    if drop_member:
        members.pop(drop_member)
    if tamper:
        members[tamper] = members[tamper] + b" "
    path = directory / f"{SUPPLEMENT}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(f"{SUPPLEMENT}/{name}", content)
        archive.writestr(f"{SUPPLEMENT}/manifest.sha256", manifest)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


FOLLOWUP = "Parkhaus_Nachrecherche_2026-10-07"
#: The price points of the directory table of Achtermann (minutes, EUR), as the follow-up package states them.
ACH_POINTS = ((60, 1.5), (120, 3.0), (180, 4.5), (210, 5.3), (240, 6.1), (270, 6.9), (300, 7.7), (330, 8.5), (360, 9.3), (390, 10.1),
              (420, 10.9), (450, 11.7), (480, 12.5), (510, 13.3), (540, 14.1), (570, 14.9), (720, 15.0), (1440, 25.0))
ACH_BANDS = "0-180 1.50/60; 180-570 0.80/30; 570-720 total 15.00; 720- 1.00/60"


def _followup_sources() -> list:
    return [{"source_id": name, "url": f"https://followup.example/{name}", "retrieved_at": "2026-10-07T17:21:23+00:00"}
            for name in ("F05", "F08", "F13", "F19", "F15", "H01")]


def _followup_review() -> dict:
    def facility(facility_id, observations, **extra):
        return {"facility_id": facility_id, "name": f"Review of {facility_id}", "source_ids": ["F05"],
                "observations": observations, **extra}

    return {"research_date": "2026-10-07", "facilities": [
        facility("GS_ACH", [
            {"observation_id": "ACH_TABLE", "source_ids": ["F05"], "price_points": [{"minutes": m, "eur": e} for m, e in ACH_POINTS],
             "reported_further_hour_eur": 1, "effective_date": None, "accepted_as_current_operator_tariff": False},
            {"observation_id": "ACH_HOTEL", "source_ids": ["F05"], "price_eur": 15, "unit": "night"}]),
        facility("GOS_POINT_4", [
            {"observation_id": "CHARLEY_DIR", "source_ids": ["F08"], "capacity_reported": 58, "price_eur": 1,
             "billing_unit_minutes": 60, "rounding": None, "accepted_as_current_tariff": False}]),
        facility("WOB_SUEDKOPF", [
            {"observation_id": "SUED_CAP", "source_ids": ["F13"], "daily_cap_eur": 5, "cap_period": None,
             "accepted_as_current_operator_tariff": False}], legacy_ids=["WOB_SUED"]),
        facility("BS_FORSCHUNGSFLUGHAFEN", [], legacy_ids=["BS_SOURCE_AIR"]),
        facility("GS_GAL", [])]}


def _followup_rules() -> list:
    def rule(rule_id, facility, price=None, **extra):
        base = {"rule_id": rule_id, "facility_id": facility, "currency": "EUR", "price_eur": price, "billing_unit_minutes": None,
                "rounding": None, "daily_cap_eur": None, "cap_period": None, "source_ids": ["F05"], "retrieved_on": "2026-10-07",
                "status": "partial", "full_cost_calculation_ready": False}
        base.update(extra)
        return base

    return [rule("FU_ACH", "GS_ACH"), rule("FU_CHAR", "GOS_POINT_4"),
            rule("FU_SUED", "WOB_SUEDKOPF", 1.0, billing_unit_minutes=60, free_period_minutes=30),
            rule("FU_AIR", "BS_FORSCHUNGSFLUGHAFEN", 1.5, billing_unit_minutes=60, rounding="ceil", daily_cap_eur=18,
                 source_ids=["F19"]),
            rule("FU_GAL", "GS_GAL", 1.5, billing_unit_minutes=60, source_ids=["F15"])]


def _followup_decisions() -> list:
    def decision(decision_id, facility, status):
        return {"decision_id": decision_id, "facility_id": facility, "field": "x", "value": None, "status": status,
                "source_ids": ["F05"], "retrieved_on": "2026-10-07"}

    return [decision("ACH:op", "GS_ACH", "open_with_new_operator_and_tariff_lead"),
            decision("CHAR:fac", "GOS_POINT_4", "resolved_official_budget_evidence"),
            decision("CHAR:tariff", "GOS_POINT_4", "open_undated_secondary_tariff_only"),
            decision("SUED:cap", "WOB_SUEDKOPF", "open_secondary_five_euro_candidate")]


def _write_followup(directory: Path, *, tamper=None, points_shift_m=0.0, review=None) -> tuple:
    """Write the synthetic follow-up zip; returns (path, SHA-256)."""
    directory.mkdir(parents=True, exist_ok=True)
    point = gpd.GeoSeries([Point(X0 + 21_000 + points_shift_m, Y0 + 100)], crs=METRIC_CRS).to_crs("EPSG:4326").iloc[0]
    members = {
        "data/facility_review.json": json.dumps(review or _followup_review()).encode(),
        "data/tariff_rules.json": json.dumps({"rules": _followup_rules()}).encode(),
        "data/field_decisions.json": json.dumps({"decisions": _followup_decisions()}).encode(),
        "data/model_actions.json": json.dumps({"actions": [
            {"facility_id": "GS_ACH", "action": "retain_facility_record_flag_tariff_unverified",
             "include_in_costed_facility_list": False},
            {"facility_id": "GOS_POINT_4", "action": "exclude_from_costed_list_preserve_unpriced_evidence_record",
             "include_in_costed_facility_list": False},
            {"facility_id": "WOB_SUEDKOPF", "action": "retain_published_base_tariff_with_currentness_flag"}]}).encode(),
        "data/archived_charley_point.geojson": json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [point.x, point.y]},
             "properties": {"facility_id": "GOS_POINT_4", "geometry_source_id": "H01", "geometry_source_date": "2018-11-22",
                            "geometry_method": "inherited_original_municipal_point"}}]}).encode(),
        "data/sources.json": json.dumps({"sources": _followup_sources()}).encode()}
    manifest = _manifest(members)
    if tamper:
        members[tamper] = members[tamper] + b" "
    path = directory / f"{FOLLOWUP}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(f"{FOLLOWUP}/{name}", content)
        archive.writestr(f"{FOLLOWUP}/manifest.sha256", manifest)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def followup_package(tmp_path_factory):
    return _write_followup(tmp_path_factory.mktemp("followup"))


@pytest.fixture(scope="module")
def regional(tmp_path_factory):
    directory = tmp_path_factory.mktemp("regional")
    return directory, _write_regional(directory)


@pytest.fixture(scope="module")
def supplement_package(tmp_path_factory):
    return _write_supplement(tmp_path_factory.mktemp("supplement"))


# --------------------------------------------------------------------------- the package is verified before it is read


def test_a_changed_or_missing_supplement_is_refused(sup, supplement_package, tmp_path):
    path, sha256 = supplement_package
    with pytest.raises(SystemExit, match="SHA-256"):
        sup.load_supplement(path, expected_sha256="0" * 64)
    with pytest.raises(SystemExit, match="missing: pass the owner's supplement package"):
        sup.load_supplement(tmp_path / "none.zip")
    loaded = sup.load_supplement(path, expected_sha256=sha256)
    assert loaded["file"]["sha256"] == sha256 and loaded["file"]["file"] == f"{SUPPLEMENT}.zip"


def test_the_pinned_supplement_hash_is_the_owner_s(sup):
    assert sup.SUPPLEMENT_SHA256 == "76d2651433e05a4d0d0a75ba352fd17f99b329555eb3bb4525c34990383978fa"
    assert sup.SUPPLEMENT_FILE == "Parkhaus_Ergaenzungen_2026-10-07.zip"


def test_a_member_that_differs_from_the_manifest_or_is_missing_from_the_zip_is_refused(sup, tmp_path):
    path, sha256 = _write_supplement(tmp_path / "tampered", tamper="data/tariff_rules.json")
    with pytest.raises(SystemExit, match=r"data/tariff_rules.json.*manifest.sha256"):
        sup.load_supplement(path, expected_sha256=sha256)
    path, sha256 = _write_supplement(tmp_path / "dropped", drop_member="data/field_decisions.json")
    with pytest.raises(SystemExit, match="data/field_decisions.json"):
        sup.load_supplement(path, expected_sha256=sha256)


# --------------------------------------------------------------------------- the main points, never the reference points


def test_only_the_four_main_points_are_read_and_the_reference_points_are_never_counted(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    entrances = loaded["entrances"]
    assert list(entrances.index) == ["ADD1_ENTRANCE", "ADD2_ENTRANCE", "STOBEN_ACCESS", "GROEPERN_ACCESS"]
    assert entrances.crs.to_epsg() == 25832 and set(entrances["role"]) == {"primary"}
    assert all(geometry.geom_type == "Point" for geometry in entrances.geometry)  # points, never a buffer or a polygon
    assert entrances.loc["STOBEN_ACCESS", "geometry"].equals(Point(X0 + 30_000, Y0 + 300))


@pytest.mark.parametrize("edit, message", [
    # an alternative among the main points would count a garage twice
    (lambda rows: rows[:3] + [{**rows[3], "role": "reference"}], "role 'reference'"),
    (lambda rows: rows + [{**rows[0], "point_id": "ADD1_SECOND"}], "second main point"),
    (lambda rows: [{**rows[0], "geometry_is_tariff_extent": True}] + rows[1:], "geometry_is_tariff_extent"),
])
def test_a_main_point_that_is_no_point_of_one_garage_is_refused(sup, tmp_path, edit, message):
    path, sha256 = _write_supplement(tmp_path, entrances_rows=edit(_entrance_rows()))
    with pytest.raises(SystemExit, match=message):
        sup.load_supplement(path, expected_sha256=sha256)


def test_a_geojson_that_disagrees_with_the_geopackage_is_refused(sup, tmp_path):
    path, sha256 = _write_supplement(tmp_path, geojson_shift_m=5.0)
    with pytest.raises(SystemExit, match="entrances.geojson.*differs from the GeoPackage"):
        sup.load_supplement(path, expected_sha256=sha256)


def test_the_point_type_names_the_entrance_kind(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    mapped = sup.entrance(loaded, "ADD1_ENTRANCE")
    derived = sup.entrance(loaded, "STOBEN_ACCESS")
    assert mapped["method"] == "osm_mapped_parking_entrance" and derived["method"] == "derived_access_point_on_osm_node"
    assert "OSM-mapped entrance" in mapped["description"] and "node 310231874 version 17" in mapped["description"]
    assert "derived access point" in derived["description"] and "no confirmed barrier or portal point" in derived["description"]
    assert mapped["url"] == "https://www.openstreetmap.org/node/310231874" and "not field verified" in mapped["description"]
    with pytest.raises(SystemExit, match="no main point"):
        sup.entrance(loaded, "BS_NONE_ENTRANCE")


# --------------------------------------------------------------------------- identity: stable ids and verified legacy ids


def test_the_facilities_are_matched_by_their_id_or_their_verified_legacy_id(sup, supplement_package, regional, garages_step):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    inputs = garages_step.load_garage_inputs(regional[0], expected_sha256=regional[1])
    mapping = sup.identity_map(loaded, inputs["facilities"])
    assert mapping["BS_ADDITIONAL_1"] == "BS_ADDITIONAL_1"
    # the airport: the supplement's new key BS_FORSCHUNGSFLUGHAFEN is the regional facility of its unambiguous legacy key
    assert mapping["BS_FORSCHUNGSFLUGHAFEN"] == "BS_SOURCE_AIR"
    # the Groepern garage has its own new key and is NOT the street section HE_GROEPERN_STRASSE of the regional package
    assert mapping["HE_GROEPERN_TG_118"] is None and "HE_GROEPERN_STRASSE" in inputs["facilities"]


@pytest.mark.parametrize("edit, message", [
    # the ambiguous legacy key of the regional package is never an identity
    (lambda records: [{**record, "legacy_ids": ["BS_None"]} if record["facility_id"] == "BS_FORSCHUNGSFLUGHAFEN" else record
                      for record in records], "BS_None"),
    # a legacy id that the regional package does not hold is no verified legacy id
    (lambda records: [{**record, "legacy_ids": ["BS_SOURCE_GONE"]} if record["facility_id"] == "BS_FORSCHUNGSFLUGHAFEN" else
                      record for record in records], "BS_SOURCE_GONE"),
    # the Groepern garage must not be joined to the 18 street spaces
    (lambda records: [{**record, "legacy_ids": ["HE_GROEPERN_STRASSE"]} if record["facility_id"] == "HE_GROEPERN_TG_118" else
                      record for record in records], "HE_GROEPERN_STRASSE"),
    # two regional facilities for one supplement facility
    (lambda records: [{**record, "legacy_ids": ["WOB_POST", "WOB_SUED"]} if record["facility_id"] == "WOB_POST" else record
                      for record in records], "two facilities"),
])
def test_an_identity_that_is_not_verified_is_refused(sup, regional, garages_step, tmp_path, edit, message):
    path, sha256 = _write_supplement(tmp_path, facility_updates=edit(_facility_updates()))
    loaded = sup.load_supplement(path, expected_sha256=sha256)
    inputs = garages_step.load_garage_inputs(regional[0], expected_sha256=regional[1])
    with pytest.raises(SystemExit, match=message):
        sup.identity_map(loaded, inputs["facilities"])


# --------------------------------------------------------------------------- the rules of the supplement in the regional schema


@pytest.fixture()
def converted(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    return sup.convert_rules(loaded)


def test_a_supplement_rule_is_converted_to_the_regional_rule_schema_and_is_not_preferred_by_default(converted):
    rule = converted["AIR_REGULAR"]
    assert (rule["rule_type"], rule["amount_eur"], rule["billing_unit_minutes"]) == ("increment", 1.5, 60)
    assert rule["rounding"] == "started_unit"  # the supplement's 'ceil' (resolved) is a started unit
    assert rule["free_period_minutes"] == 15 and rule["origin"] == "supplement"
    assert rule["source_url"] == "https://www.struktur-foerderung.de/parken" and rule["retrieved_at"] == "2026-10-07"
    assert all(rule["preferred_for_current_use"] is False for rule in converted.values())  # nothing is released by default
    cap = converted["AIR_REGULAR:cap"]
    assert (cap["rule_type"], cap["daily_cap_eur"], cap["cap_period"]) == ("daily_cap", 18, None)
    assert converted["AIR_WEEK"]["rule_type"] == "weekly_ticket" and converted["AIR_WEEK"]["amount_eur"] == 99
    assert converted["SUED_REGULAR"]["rounding"] is None  # not stated: the supplement states none
    assert converted["GAL_MONTHLY"]["rule_type"] == "monthly_product" and converted["GAL_MONTHLY"]["monthly_price_eur"] == 39
    assert converted["ACH_UNRESOLVED"]["rule_type"] == "unresolved" and converted["ACH_UNRESOLVED"]["amount_eur"] is None


def test_the_night_times_become_the_charging_times_of_a_midnight_crossing_window(converted):
    times = converted["POST_NIGHT"]["charging_times"]
    assert set(times) == set(WEEKDAYS)
    assert times["monday"] == [{"start": "21:00", "end": "06:30", "crosses_midnight": True}]
    assert times["sunday"] == times["monday"] and times["public_holidays"] is None


def test_the_stages_of_a_rate_and_the_cap_variants_of_the_observations_are_rules_of_their_own(converted):
    first, second = converted["GAL_REGULAR:stage1"], converted["GAL_REGULAR:stage2"]
    assert (first["elapsed_from_minutes"], first["elapsed_to_minutes"], first["amount_eur"]) == (0, 60, 1.5)
    assert (second["elapsed_from_minutes"], second["elapsed_to_minutes"], second["billing_unit_minutes"]) == (60, None, 60)
    assert "GAL_REGULAR" not in converted  # the parent of the stages is no rate of its own
    direct, wvv = converted["GAL_DIRECT"], converted["GAL_WVV"]
    assert (direct["rule_type"], direct["daily_cap_eur"], direct["cap_period"]) == ("daily_cap", 8, None)
    assert (wvv["daily_cap_eur"], wvv["cap_period"]) == (15, "calendar_day")
    assert wvv["source_url"] == "https://www.wvv.de/parking/facilities.json"
    assert "GAL_TOURISM" not in converted  # a variant that states no daily cap amount is no cap rule


def test_a_rounding_other_than_ceil_or_unstated_and_an_unknown_scope_are_refused(sup, supplement_package, monkeypatch):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    loaded["rules"]["AIR_REGULAR"]["rounding"] = "pro_rata"
    with pytest.raises(SystemExit, match="rounding 'pro_rata'"):
        sup.convert_rules(loaded)
    loaded["rules"]["AIR_REGULAR"]["rounding"] = "ceil"
    loaded["rules"]["AIR_REGULAR"]["scope"] = "weekend_special"
    with pytest.raises(SystemExit, match="scope 'weekend_special'"):
        sup.convert_rules(loaded)


# --------------------------------------------------------------------------- the owner's decisions release a rule


RELEASED = {
    "AIR_REGULAR": ("R-4b2-2", {"AIR:rounding": ("resolved",)}),
    "AIR_REGULAR:cap": ("R-4b2-2", {"AIR:cap": ("resolved_visible_operator_block",)}),
    "POST_NIGHT": ("R-4b2-5", {"POST:unit": ("supported_by_municipal_source_currentness_unclear",)}),
    "GAL_REGULAR:stage1": ("R-4b2-4", {"GAL:rate": ("resolved_operator_sources",)}),
    "GAL_REGULAR:stage2": ("R-4b2-4", {"GAL:rate": ("resolved_operator_sources",)}),
    "GAL_WVV": ("R-4b2-4", {"GAL:cap": ("conflict",)}),
    "GAL_MONTHLY": ("R-4b2-4", {"GAL:monthly": ("published_terms_unverified",)}),
}


def test_a_released_rule_is_preferred_with_the_decision_that_released_it_and_the_others_stay_unused(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    rules = sup.convert_rules(loaded)
    sup.release_rules(rules, loaded["decisions"], RELEASED)
    assert rules["AIR_REGULAR"]["preferred_for_current_use"] is True
    assert rules["AIR_REGULAR"]["released_by"] == "R-4b2-2 (field decision AIR:rounding: resolved)"
    assert rules["GAL_WVV"]["released_by"] == "R-4b2-4 (field decision GAL:cap: conflict)"
    assert rules["AIR_WEEK"]["preferred_for_current_use"] is False and rules["GAL_DIRECT"]["preferred_for_current_use"] is False


@pytest.mark.parametrize("released, message", [
    ({"AIR_REGULAR": ("R-4b2-2", {"AIR:deduction": ("resolved",)})},
     "field decision AIR:deduction has the status 'open_with_general_operator_indication'"),
    ({"AIR_REGULAR": ("R-4b2-2", {"AIR:none": ("resolved",)})}, "no field decision AIR:none"),
    ({"AIR_GONE": ("R-4b2-2", {"AIR:rounding": ("resolved",)})}, "no rule AIR_GONE"),
])
def test_a_rule_is_never_released_by_a_decision_that_is_open_or_missing(sup, supplement_package, released, message):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    rules = sup.convert_rules(loaded)
    with pytest.raises(SystemExit, match=message):
        sup.release_rules(rules, loaded["decisions"], released)


# --------------------------------------------------------------------------- the brochure tariff with its quotations

BROCHURE = ({"facility": "HE_GROEPERN_TG_118", "member": BROCHURE_MEMBER, "source_id": "HE_CITY_PARKING",
             "anchor": "Gröpern (Tiefgarage)", "first": "0,70€ / 1 Std.", "further": "jede weitere 0,30€",
             "window": ("Öffnungszeit:", "24 Stunden"),
             "unit_reading": "the brochure states no unit for 'jede weitere'; read as the unit of the first price"},)


def test_the_brochure_tariff_is_read_from_its_quotations_and_checked_against_the_text(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    rules = sup.brochure_rules(loaded, BROCHURE)
    first, nxt = rules["HE_GROEPERN_TG_118_BROCHURE_FIRST"], rules["HE_GROEPERN_TG_118_BROCHURE_NEXT"]
    assert (first["rule_type"], first["amount_eur"], first["elapsed_from_minutes"], first["elapsed_to_minutes"]) == (
        "duration_total", 0.7, 0, 60)
    assert (nxt["rule_type"], nxt["amount_eur"], nxt["billing_unit_minutes"], nxt["elapsed_from_minutes"]) == (
        "increment", 0.3, 60, 60)
    assert nxt["rounding"] is None and "no unit for 'jede weitere'" in nxt["unit_reading"]
    assert first["source_url"] == BROCHURE_URL and first["retrieved_at"] == "2026-10-07"
    assert first["preferred_for_current_use"] is True and first["origin"] == "brochure"


@pytest.mark.parametrize("change, message", [
    ({"first": "0,75€ / 1 Std."}, "quotation '0,75.*1 Std.' is not in the brochure text"),
    ({"further": "jede weitere 0,40€"}, "is not in the brochure text"),
    ({"anchor": "Helmstedt Nirgendwo"}, "anchor 'Helmstedt Nirgendwo' is not a row"),
    ({"window": ("Öffnungszeit:", "12 Stunden")}, "is not in the brochure text"),
    ({"first": "0,70 Euro"}, "no amount in EUR"),
    # a row of the brochure whose lines do not carry the quotations (the Edelhoefe row holds 0,60 and 8,00)
    ({"anchor": "Edelhoefe (Parkhaus)"}, "not in the brochure row of 'Edelhoefe"),
])
def test_a_brochure_quotation_that_is_not_in_the_row_of_its_garage_is_refused(sup, supplement_package, change, message):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    with pytest.raises(SystemExit, match=message):
        sup.brochure_rules(loaded, ({**BROCHURE[0], **change},))


def test_a_quotation_of_another_row_of_the_brochure_is_refused_even_when_the_capacity_fits(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    loaded["facilities"]["HE_GROEPERN_TG_118"]["capacity"] = 17  # the capacity of the Edelhoefe row, so only the quotations can fail
    with pytest.raises(SystemExit, match="is in the brochure text but not in the brochure row of 'Edelhoefe"):
        sup.brochure_rules(loaded, ({**BROCHURE[0], "anchor": "Edelhoefe (Parkhaus)"},))


def test_the_capacity_of_the_facility_must_be_in_the_brochure_row(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    loaded["facilities"]["HE_GROEPERN_TG_118"]["capacity"] = 119
    with pytest.raises(SystemExit, match="capacity 119 is not in the brochure row"):
        sup.brochure_rules(loaded, BROCHURE)


def test_an_operator_is_taken_only_from_a_page_that_states_it(sup, supplement_package):
    loaded = sup.load_supplement(supplement_package[0], expected_sha256=supplement_package[1])
    named = sup.operator_from_page(loaded, "BS_ADDITIONAL_2", "AGR Parking UG", OPERATOR_MEMBER, "Betreiber: AGR Parking UG",
                                   "operator_steinstrasse")
    assert named == {"operator": "AGR Parking UG", "url": OPERATOR_PAGE, "quotation": "Betreiber: AGR Parking UG"}
    with pytest.raises(SystemExit, match="not in the page"):
        sup.operator_from_page(loaded, "BS_ADDITIONAL_2", "AGR Parking UG", OPERATOR_MEMBER, "Betreiber: Anderer GmbH",
                               "operator_steinstrasse")
    with pytest.raises(SystemExit, match="states the operator None"):
        sup.operator_from_page(loaded, "BS_ADDITIONAL_1", "AGR Parking UG", OPERATOR_MEMBER, "Betreiber: AGR Parking UG",
                               "operator_steinstrasse")


# --------------------------------------------------------------------------- the step with the supplement

P10_BASIS = ("the general FAQ of the parking provider linked from the site page describes a grace period without a deduction; "
             "it is no statement of this site")
WINDOW_QUOTATION = "the city brochure gives the garage 'Oeffnungszeit: 24 Stunden' in its column of fee-liable times"
P11_BASIS = "the directory table of the same facility, undated, no operator confirmation"
AGR = ("supplement_page", "AGR Parking UG", OPERATOR_MEMBER, "Betreiber: AGR Parking UG", "operator_steinstrasse")


def _specs() -> tuple:
    """Garage specifications of the synthetic packages: the four garages without coordinates (main points of the supplement),
    the grace-period garages, the rate with stages and a cap variant, the tiers with a rest tier, two unpriced garages and one
    garage that the supplement does not touch."""
    return (
        {"garage_id": "bs_plain", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PLAIN"),
         "facility": "BS_PLAIN", "rate": "plain-1", "window": "rate", "comment": "Untouched by the supplement."},
        {"garage_id": "bs_add1", "town": "bs", "supplement_point": "ADD1_ENTRANCE", "supplement": "BS_ADDITIONAL_1",
         "facility": "BS_ADDITIONAL_1", "first": "add1-first", "rate": "add1-next", "cap": "add1-day", "window": None,
         "comment": "First hour 1.00, then 0.50 per half hour, day tariff as the cap."},
        {"garage_id": "bs_add2", "town": "bs", "supplement_point": "ADD2_ENTRANCE", "supplement": "BS_ADDITIONAL_2",
         "facility": "BS_ADDITIONAL_2", "operator": AGR, "rate": "add2-hour", "cap": "add2-day", "window": None, "comment": ""},
        {"garage_id": "he_stoben", "town": "he", "supplement_point": "STOBEN_ACCESS", "supplement": "HE_STOBEN",
         "facility": "HE_STOBEN", "rate": "stoben-fee", "window": "rate", "comment": ""},
        {"garage_id": "he_groepern", "town": "he", "supplement_point": "GROEPERN_ACCESS", "supplement": "HE_GROEPERN_TG_118",
         "facility": "HE_GROEPERN_TG_118", "first": "HE_GROEPERN_TG_118_BROCHURE_FIRST",
         "rate": "HE_GROEPERN_TG_118_BROCHURE_NEXT", "window": ("stated", "00:00", "24:00", WINDOW_QUOTATION), "comment": ""},
        {"garage_id": "bs_air", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_SOURCE_AIR"),
         "facility": "BS_SOURCE_AIR", "supplement": "BS_FORSCHUNGSFLUGHAFEN", "grace": ("air-5", "AIR_REGULAR"),
         "cap": "AIR_REGULAR:cap", "window": None, "p10_basis": P10_BASIS,
         "ignored": {"air-7": "the day ticket of the feed (a hidden template block); the visible operator block states 18.00",
                     "AIR_WEEK": "a separate product, prebooked for seven days", "FU_AIR": "the follow-up record: 18.00 "
                     "confirmed, period unspecified"}, "followup": "BS_FORSCHUNGSFLUGHAFEN", "comment": "Airport garage."},
        {"garage_id": "wob_sued", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_SUED"),
         "facility": "WOB_SUED", "supplement": "WOB_SUEDKOPF", "followup": "WOB_SUEDKOPF", "grace": ("sued-1", "sued-2"),
         "cap": "SUED_CAP", "window": None, "p10_basis": "no source; the reading of the airport by analogy",
         "p11_basis": "the secondary directory states a day maximum of 5.00, undated", "ignored": {"FU_SUED": "partial record"},
         "comment": "Shopping-centre garage."},
        {"garage_id": "gs_gal", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_GAL"),
         "facility": "GS_GAL", "supplement": "GS_GAL", "rate": "GAL_REGULAR:stage2", "first_equals_rate": "GAL_REGULAR:stage1",
         "cap": "GAL_WVV", "window": None,
         "followup": "GS_GAL", "ignored": {"FU_GAL": "the third provider page", "GAL_DIRECT": "the conflicting variant of the direct operator locator (8.00)",
                     "gal-1": "the tourism page states the same first hour", "gal-2": "the tourism page states the same 24 h price"},
         "qa_comment": "the direct locator states 8.00 (conflicting variant, not used)", "comment": "GALERIA."},
        {"garage_id": "wob_post", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_POST"),
         "facility": "WOB_POST", "supplement": "WOB_POST", "tiers": ("POST_NIGHT",), "rest_tier": "post-1", "cap": "post-2",
         "other_caps": ("post-4",), "ignored": {"post-3": "the operator's night tariff without a unit, replaced by POST_NIGHT"},
         "comment": "Night unit from the city page only."},
        {"garage_id": "gs_ach", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_ACH"),
         "facility": "GS_ACH", "supplement": "GS_ACH", "followup": "GS_ACH", "table": ("ACH_TABLE", ACH_BANDS),
         "cap": "ACH_TABLE:cap", "window": None, "p11_basis": P11_BASIS,
         "ignored": {"ach-1": "the tourism page's 12 h price", "ACH_UNRESOLVED": "the supplement found no operator tariff",
                     "FU_ACH": "the partial record of the follow-up package"}, "comment": "Directory table."},
        {"garage_id": "gs_char", "town": "gs", "layer": "gos_parking_locations", "feature": ("facility_id", "GOS_POINT_4"),
         "facility": "GOS_POINT_4", "supplement": "GOS_POINT_4", "followup": "GOS_POINT_4", "rate": "CHARLEY_DIR",
         "window": None, "p11_basis": P11_BASIS, "capacity_from": "CHARLEY_DIR", "archived_point": True,
         "ignored": {"GOS4_UNRESOLVED": "the supplement's open record", "FU_CHAR": "the partial record"}, "comment": ""},
    )


MONTHLY = (
    {"record_id": "monthly_gs_gal", "rule": "GAL_MONTHLY", "garage_id": "gs_gal", "decision": "used"},
    {"record_id": "monthly_bs_air_week", "rule": "AIR_WEEK", "garage_id": "bs_air", "decision": "not_used",
     "reason": "not_monthly_or_30_day", "amount_eur": 99.0, "subject": "Airport garage, seven days prebooked",
     "note": "a separate product, prebooked for seven days"},
)
CANDIDATES = (
    {"record_id": "candidate_cl", "town": "bs", "subject": "A garage of a brochure", "facility": None, "reason": "no_coordinates",
     "evidence": "city brochure", "note": "no facility record, no coordinates"},
)
DIRECTORY = {"Parkplatz Markthalle": ("bga_zone", "BgA car park"), "Parkplatz Werder": ("zone_street_product", "zone 1")}


@pytest.fixture()
def step(garages_step, monkeypatch):
    """The step with the synthetic specifications and decisions in place of the real ones."""
    monkeypatch.setattr(garages_step.specs, "GARAGE_SPECS", _specs())
    monkeypatch.setattr(garages_step.specs, "MONTHLY_PRODUCTS", MONTHLY)
    monkeypatch.setattr(garages_step.specs, "PACKAGE_CANDIDATES", CANDIDATES)
    monkeypatch.setattr(garages_step.specs, "DIRECTORY_DECISIONS", DIRECTORY)
    monkeypatch.setattr(garages_step.specs, "SUPPLEMENT_RELEASED", RELEASED)
    monkeypatch.setattr(garages_step.specs, "BROCHURE_TARIFFS", BROCHURE)
    monkeypatch.setattr(garages_step.specs, "FOLLOWUP_RELEASED", FOLLOWUP_RELEASED)
    return garages_step


FOLLOWUP_RELEASED = {
    "ACH_TABLE": ("R-4b2-8", {"ACH:op": ("open_with_new_operator_and_tariff_lead",)}),
    "ACH_TABLE:cap": ("R-4b2-8", {"ACH:op": ("open_with_new_operator_and_tariff_lead",)}),
    "CHARLEY_DIR": ("R-4b2-8", {"CHAR:fac": ("resolved_official_budget_evidence",),
                                "CHAR:tariff": ("open_undated_secondary_tariff_only",)}),
    "SUED_CAP": ("R-4b2-8", {"SUED:cap": ("open_secondary_five_euro_candidate",)}),
}


@pytest.fixture()
def inputs(step, regional, supplement_package, followup_package):
    return step.load_garage_inputs(regional[0], expected_sha256=regional[1], supplement_path=supplement_package[0],
                                   expected_supplement_sha256=supplement_package[1], followup_path=followup_package[0],
                                   expected_followup_sha256=followup_package[1])


@pytest.fixture()
def rows(step, inputs):
    frame = step.build_garages(inputs)
    return frame.set_index("garage_id")


def test_the_step_lists_the_four_garages_without_coordinates_at_the_main_points_of_the_supplement(rows):
    assert len(rows) == 11 and rows["priced"].sum() == 11  # every listed garage is priced (spec E13)
    for garage_id, (x, y), method, ags in (("bs_add1", (300, 300), "osm_mapped_parking_entrance", BS),
                                           ("bs_add2", (350, 350), "osm_mapped_parking_entrance", BS),
                                           ("he_stoben", (30_000, 300), "derived_access_point_on_osm_node", HE),
                                           ("he_groepern", (30_050, 320), "osm_mapped_parking_entrance", HE)):
        row = rows.loc[garage_id]
        assert row.geometry.equals(Point(X0 + x, Y0 + y)), garage_id  # the main point, not the alternative, not a buffer
        assert row["geometry_method"] == method and row["municipality_ags"] == ags
        assert row["geometry_source_url"].startswith("https://www.openstreetmap.org/node/")
    assert rows.loc["he_stoben", "capacity_reported"] == 64 and rows.loc["he_stoben", "capacity_scope"] == "facility_total"
    assert rows.loc["he_groepern", "capacity_reported"] == 118
    assert rows.loc["he_groepern", "package_facility_id"] == "HE_GROEPERN_TG_118"


def test_the_names_of_the_new_garages_are_the_ascii_names_of_the_supplement(rows):
    assert rows.loc["he_groepern", "name"] == "Tiefgarage Groepern"
    assert rows.loc["bs_add1", "name"] == "Parkhaus Lange Strasse Sued"


def test_a_row_cites_every_package_that_touches_it_in_the_order_regional_supplement_followup(rows, regional, supplement_package,
                                                                                         followup_package):
    both = f"{regional[1]};{supplement_package[1]}"
    three = f"{both};{followup_package[1]}"
    assert {garage for garage, row in rows.iterrows() if row["package_sha256"] == three} == {
        "bs_air", "wob_sued", "gs_gal", "gs_ach", "gs_char"}
    assert {garage for garage, row in rows.iterrows() if row["package_sha256"] == both} == {
        "bs_add1", "bs_add2", "he_stoben", "he_groepern", "wob_post"}
    assert rows.loc["bs_plain", "package_sha256"] == regional[1]


def test_the_first_hour_the_rate_and_the_day_tariff_of_the_garages_without_coordinates_are_read_from_their_rules(rows):
    add1 = rows.loc["bs_add1"]
    assert (add1["garage_first_period_min"], add1["garage_first_period_eur"], add1["garage_billing_unit_min"]) == (60, 1.0, 30)
    assert add1["garage_hourly_rate_eur"] == 1.0 and add1["garage_daily_cap_eur"] == 10.0
    assert (add1["garage_fee_start_h"], add1["garage_fee_end_h"], add1["assumptions"]) == (0.0, 24.0, "P4;P5")
    add2 = rows.loc["bs_add2"]
    assert (add2["garage_hourly_rate_eur"], add2["garage_billing_unit_min"], add2["garage_daily_cap_eur"]) == (1.8, 60, 18.0)
    assert add2["assumptions"] == "P4;P5" and add2["operator"] == "AGR Parking UG"
    assert "Betreiber: AGR Parking UG" in add2["notes"] and OPERATOR_PAGE in add2["notes"]
    stoben = rows.loc["he_stoben"]
    assert (stoben["garage_hourly_rate_eur"], stoben["garage_fee_start_h"], stoben["garage_fee_end_h"]) == (0.8, 7.0, 19.0)
    assert stoben["assumptions"] == "P4" and "the connection of the driveway to the street" in stoben["notes"]
    groepern = rows.loc["he_groepern"]
    assert (groepern["garage_first_period_min"], groepern["garage_first_period_eur"]) == (60, 0.7)
    assert (groepern["garage_hourly_rate_eur"], groepern["garage_billing_unit_min"]) == (0.3, 60)
    assert (groepern["garage_fee_start_h"], groepern["garage_fee_end_h"], groepern["assumptions"]) == (0.0, 24.0, "P4")
    assert groepern["source_url"] == BROCHURE_URL and groepern["source_date"] == "2026-10-07"
    assert WINDOW_QUOTATION in groepern["notes"] and "no unit for 'jede weitere'" in groepern["notes"]


def test_a_free_period_is_a_grace_period_the_airport_and_the_suedkopf_center_are_priced_by_bands(rows):
    air, sued = rows.loc["bs_air"], rows.loc["wob_sued"]
    assert air["priced"] and air["tariff_duration_bands"] == "0-15 free; 15-60 total 1.50; 60- 1.50/60"
    assert air["garage_daily_cap_eur"] == 18.0 and air["assumptions"] == "P5;P8;P10"  # the rounding is stated: no P4
    assert sued["tariff_duration_bands"] == "0-30 free; 30-60 total 1.00; 60- 1.00/60"
    assert sued["garage_daily_cap_eur"] == 5.0 and sued["assumptions"] == "P4;P5;P8;P10;P11"  # the secondary day maximum: P11
    assert pd.isna(air["garage_hourly_rate_eur"]) and pd.isna(air["garage_first_period_min"]) and air["tariff_tiers"] is None
    for garage in (air, sued):
        assert f"ASSUMPTION P10: {pg.ASSUMPTIONS['P10']}" in garage["notes"] and "Basis of the reading: " in garage["notes"]
    assert P10_BASIS in air["notes"] and "no source; the reading of the airport by analogy" in sued["notes"]
    assert "AIR_REGULAR (R-4b2-2 (field decision AIR:rounding: resolved))" in air["notes"]
    assert "full_cost_calculation_ready" in air["notes"]
    assert "air-7" in air["notes"] and "AIR_WEEK" in air["notes"]
    bands = pg.parse_duration_bands(air["tariff_duration_bands"])
    assert [pg.duration_band_price_eur(bands, minutes, 18.0) for minutes in (15, 16, 60, 61, 24 * 60)] == [0.0, 1.5, 1.5, 3.0, 18.0]


def test_the_galeria_rate_is_the_same_for_the_first_and_every_further_hour_with_the_cap_of_the_decided_variant(rows):
    gal = rows.loc["gs_gal"]
    assert (gal["garage_hourly_rate_eur"], gal["garage_billing_unit_min"], gal["garage_daily_cap_eur"]) == (1.5, 60, 15.0)
    assert pd.isna(gal["garage_first_period_min"]) and gal["assumptions"] == "P4;P5"
    assert gal["monthly_eur"] == 39.0 and "eligibility contract_required" in gal["monthly_product"]
    assert "GAL_DIRECT" in gal["notes"] and "conflicting variant" in gal["notes"] and "8.00 EUR at most" in gal["notes"]
    assert "stated per calendar day" in gal["notes"] and "maximum per stay" in gal["notes"]
    assert "GAL_WVV (R-4b2-4 (field decision GAL:cap: conflict))" in gal["notes"]


def test_the_poststrasse_has_a_day_tier_and_a_night_tier_with_the_unit_from_the_city_page(rows):
    post = rows.loc["wob_post"]
    assert post["tariff_tiers"] == "06:30-21:00 2.00/60; 21:00-06:30 1.00/60" and post["garage_daily_cap_eur"] == 9.0
    assert post["assumptions"] == "P4;P6;P7"  # the night rounding is unknown (P4); P3 and P5 are gone
    assert pd.isna(post["garage_hourly_rate_eur"]) and pd.isna(post["garage_fee_start_h"])
    assert "Reading: the day rate post-1 states no charging times; it applies at every time of day that the other tiers do " \
           "not cover (06:30-21:00)" in post["notes"]
    assert "post-4" in post["notes"] and "post-3" in post["notes"] and "ASSUMPTION P7" in post["notes"]
    assert "POST_NIGHT (R-4b2-5 (field decision POST:unit: supported_by_municipal_source_currentness_unclear))" in post["notes"]


def test_achtermann_and_charley_jacob_are_priced_from_their_directory_observations_under_assumption_p11(rows):
    ach, char = rows.loc["gs_ach"], rows.loc["gs_char"]
    assert ach["priced"] and char["priced"] and ach["not_priced_reason"] is None and char["not_priced_reason"] is None
    assert ach["tariff_duration_bands"] == ACH_BANDS and ach["garage_daily_cap_eur"] == 25.0
    assert ach["assumptions"] == "P4;P5;P8;P11" and ach["operator"] is None
    assert ach["tariff_rule_ids"] == "ACH_TABLE;ACH_TABLE:cap"
    assert (char["garage_hourly_rate_eur"], char["garage_billing_unit_min"], char["garage_fee_start_h"],
            char["garage_fee_end_h"]) == (1.0, 60, 0.0, 24.0)
    assert char["assumptions"] == "P4;P5;P11" and pd.isna(char["garage_daily_cap_eur"])
    assert (char["capacity_reported"], char["capacity_scope"]) == (58, "secondary_directory_total")
    assert char["geometry_method"] == "archived_municipal_point_2018" and char["geometry_source_url"] == "https://followup.example/H01"
    for garage in (ach, char):
        assert f"ASSUMPTION P11: {pg.ASSUMPTIONS['P11']}" in garage["notes"] and P11_BASIS in garage["notes"]
        assert "overridden by the owner (ruling R-4b2-8, spec E13)" in garage["notes"]
    assert "retain_facility_record_flag_tariff_unverified" in ach["notes"]
    assert "exclude_from_costed_list_preserve_unpriced_evidence_record" in char["notes"]


@pytest.mark.parametrize("index, minutes, eur", [(i, m, e) for i, (m, e) in enumerate(ACH_POINTS)])
def test_the_committed_form_of_the_achtermann_table_reproduces_every_price_point_of_the_directory(rows, index, minutes, eur):
    bands = pg.parse_duration_bands(rows.loc["gs_ach", "tariff_duration_bands"])
    assert pg.duration_band_price_eur(bands, minutes, rows.loc["gs_ach", "garage_daily_cap_eur"]) == pytest.approx(eur)
    assert pg.duration_band_price_eur(bands, minutes + 1) >= eur  # the schedule never falls


def test_the_readings_of_the_supplement_are_named_and_counted(step, inputs, capsys):
    frame = step.build_garages(inputs)
    out = capsys.readouterr().out
    assert "a day cap stated per calendar day is held as a maximum per stay: 1 garage(s)" in out
    assert "a rate whose unit the source does not state is read as the unit of the first price: 1 garage(s)" in out
    assert "a day rate without charging times takes the rest of the day beside a night tier: 1 garage(s)" in out
    assert "supplement package: 10 garages touched (4 at its main points), 10 priced from its rules or points" in out
    assert "follow-up package: 5 garages touched, 3 priced from its observations" in out
    assert "priced garages resting on ASSUMPTION P10: 2/11" in out and "priced garages resting on ASSUMPTION P11: 3/11" in out
    assert frame["priced"].sum() == 11


def test_a_specification_that_needs_the_supplement_stops_the_step_without_it(step, regional):
    inputs = step.load_garage_inputs(regional[0], expected_sha256=regional[1])
    with pytest.raises(SystemExit, match="--supplement-zip"):
        step.build_garages(inputs)


@pytest.mark.parametrize("change, message", [
    # the identity of the specification must be the supplement's: the Groepern garage is never the street section
    ({"garage_id": "he_groepern", "facility": "HE_GROEPERN_STRASSE"}, "specification names the facility HE_GROEPERN_STRASSE"),
    ({"garage_id": "bs_air", "facility": "BS_None"}, "specification names the facility BS_None"),
    ({"garage_id": "bs_add1", "supplement": "BS_ADDITIONAL_9"}, "no facility BS_ADDITIONAL_9"),
])
def test_a_specification_whose_facility_is_not_the_identity_of_the_supplement_is_refused(step, inputs, change, message):
    garage = change.pop("garage_id")
    specs = tuple({**spec, **change} if spec["garage_id"] == garage else spec for spec in _specs())
    step.specs.GARAGE_SPECS = specs
    with pytest.raises(SystemExit, match=message):
        step.build_garages(inputs)


def test_a_capacity_that_conflicts_with_the_regional_package_is_refused(step, inputs):
    inputs["supplement"]["entrances"].loc["STOBEN_ACCESS", "capacity"] = 65.0
    with pytest.raises(SystemExit, match="capacity 65.*64"):
        step.build_garages(inputs)


def test_an_operator_quotation_that_the_page_does_not_hold_stops_the_step(step, inputs):
    spec = tuple({**spec, "operator": AGR[:3] + ("Betreiber: Anderer GmbH",) + AGR[4:]} if spec["garage_id"] == "bs_add2" else spec
                 for spec in _specs())
    step.specs.GARAGE_SPECS = spec
    with pytest.raises(SystemExit, match="not in the page"):
        step.build_garages(inputs)


def test_a_grace_period_row_must_state_the_basis_of_its_reading(step, inputs):
    step.specs.GARAGE_SPECS = tuple({k: v for k, v in spec.items() if k != "p10_basis"} if spec["garage_id"] == "bs_air" else spec
                                    for spec in _specs())
    with pytest.raises(SystemExit, match="bs_air.*p10_basis"):
        step.build_garages(inputs)


def test_a_changed_follow_up_package_or_member_is_refused(sup, followup_package, tmp_path):
    path, sha256 = followup_package
    with pytest.raises(SystemExit, match="SHA-256"):
        sup.load_followup(path, expected_sha256="0" * 64)
    with pytest.raises(SystemExit, match="missing: pass the owner's follow-up package"):
        sup.load_followup(tmp_path / "none.zip")
    tampered, tampered_sha = _write_followup(tmp_path / "t", tamper="data/model_actions.json")
    with pytest.raises(SystemExit, match=r"data/model_actions.json.*manifest.sha256"):
        sup.load_followup(tampered, expected_sha256=tampered_sha)
    loaded = sup.load_followup(path, expected_sha256=sha256)
    assert sup.FOLLOWUP_SHA256 == "3bbaff93fb8b26d6cdfb187c7e0bf746f099997c1c7fd04a961fcae7d37329d8"
    assert set(loaded["observation_table"]) == {"ACH_TABLE", "ACH_HOTEL", "CHARLEY_DIR", "SUED_CAP"}
    assert loaded["actions"]["GS_ACH"]["include_in_costed_facility_list"] is False
    assert abs(loaded["point"]["geometry"].x - (X0 + 21_000)) < 0.01


def test_the_observations_that_state_a_tariff_are_rules_and_the_others_are_no_rules(sup, followup_package):
    loaded = sup.load_followup(followup_package[0], expected_sha256=followup_package[1])
    rules = sup.observation_rules(loaded)
    assert set(rules) == {"ACH_TABLE", "ACH_TABLE:cap", "CHARLEY_DIR", "SUED_CAP"}  # the hotel guest price is no rule
    table = rules["ACH_TABLE"]
    assert table["rule_type"] == "price_table" and len(table["price_points"]) == 18 and table["reported_further_hour_eur"] == 1
    assert (rules["ACH_TABLE:cap"]["daily_cap_eur"], rules["ACH_TABLE:cap"]["cap_period"]) == (25, None)  # the 24-hour price
    assert (rules["CHARLEY_DIR"]["amount_eur"], rules["CHARLEY_DIR"]["billing_unit_minutes"], rules["CHARLEY_DIR"]["rounding"],
            rules["CHARLEY_DIR"]["capacity_reported"]) == (1, 60, None, 58)
    assert (rules["SUED_CAP"]["rule_type"], rules["SUED_CAP"]["daily_cap_eur"]) == ("daily_cap", 5)
    assert all(rule["preferred_for_current_use"] is False for rule in rules.values())  # nothing is released by default
    loaded["observation_table"]["ACH_TABLE"]["price_points"][-1]["minutes"] = 1000
    with pytest.raises(SystemExit, match="not at the 24-hour price of 1440 min"):
        sup.observation_rules(loaded)


def test_a_follow_up_rule_is_released_only_while_the_package_still_holds_the_decision_open(step, regional, supplement_package,
                                                                                         followup_package):
    inputs = step.load_garage_inputs(regional[0], expected_sha256=regional[1], supplement_path=supplement_package[0],
                                     expected_supplement_sha256=supplement_package[1])
    followup = step.sup.load_followup(followup_package[0], expected_sha256=followup_package[1])
    followup["decisions"]["CHAR:tariff"]["status"] = "resolved"
    with pytest.raises(SystemExit, match="CHAR:tariff has the status 'resolved'"):
        step.sup.attach_followup(inputs, followup, FOLLOWUP_RELEASED)


def test_a_followup_facility_that_the_regional_package_does_not_hold_is_refused(step, regional, supplement_package, followup_package):
    inputs = step.load_garage_inputs(regional[0], expected_sha256=regional[1], supplement_path=supplement_package[0],
                                     expected_supplement_sha256=supplement_package[1])
    followup = step.sup.load_followup(followup_package[0], expected_sha256=followup_package[1])
    followup["rules"]["FU_ACH"]["facility_id"] = "GS_NOWHERE"
    with pytest.raises(SystemExit, match="GS_NOWHERE"):
        step.sup.attach_followup(inputs, followup, FOLLOWUP_RELEASED)


def test_the_price_table_must_be_reproduced_at_every_point_and_end_with_the_reported_further_hour(step, inputs):
    spec = next(spec for spec in _specs() if spec["garage_id"] == "gs_ach")
    assert step.encode_tariff(spec, inputs["rules"])["values"]["tariff_duration_bands"] == ACH_BANDS
    for bands, message in (("0-180 1.50/60; 180-570 0.80/30; 570-720 total 15.00; 720- 1.20/60", "reported further hour"),
                           ("0-180 1.50/60; 180-570 0.80/60; 570-720 total 15.00; 720- 1.00/60", "do not reproduce the price "
                            "point of 240 min"),
                           ("0-180 1.50/60; 180-570 0.80/30; 570-720 total 14.90; 720- 1.00/60", "do not reproduce the price "
                            "point of 720 min")):
        with pytest.raises(SystemExit, match=message):
            step.encode_tariff({**spec, "table": ("ACH_TABLE", bands)}, inputs["rules"])
    # a table whose cap point is not reached is no 24-hour price: the cap must be the price of the 1440 min point
    with pytest.raises(SystemExit, match="cap"):
        step.encode_tariff({**spec, "cap": None}, inputs["rules"])


def test_the_archived_point_must_be_the_point_of_the_regional_layer(step, regional, supplement_package, tmp_path, monkeypatch):
    moved, moved_sha = _write_followup(tmp_path / "moved", points_shift_m=5.0)
    inputs = step.load_garage_inputs(regional[0], expected_sha256=regional[1], supplement_path=supplement_package[0],
                                     expected_supplement_sha256=supplement_package[1], followup_path=moved,
                                     expected_followup_sha256=moved_sha)
    with pytest.raises(SystemExit, match="archived point.*differs from the point of the layer"):
        step.build_garages(inputs)


def test_a_specification_that_needs_the_follow_up_stops_the_step_without_it(step, regional, supplement_package):
    inputs = step.load_garage_inputs(regional[0], expected_sha256=regional[1], supplement_path=supplement_package[0],
                                     expected_supplement_sha256=supplement_package[1])
    with pytest.raises(SystemExit, match="--followup-zip"):
        step.build_garages(inputs)


# --------------------------------------------------------------------------- the grace form (ASSUMPTION P10)


def _grace_rules(free_end=15, free_start=0, free_type="free_period", unit=60, rounding="started_unit", amount=1.5,
                 preferred=True) -> dict:
    return {"f": _rule("f", free_type, "X", 0.0, start=free_start, end=free_end),
            "r": _rule("r", "increment", "X", amount, unit=unit, rounding=rounding, preferred=preferred),
            "c": _rule("c", "daily_cap", "X", 12.0, cap=12.0, period="calendar_day")}


GRACE = {"garage_id": "g", "grace": ("f", "r"), "window": None}


def test_the_grace_form_is_a_free_band_the_total_of_the_first_unit_and_the_rate_from_the_end_of_the_first_unit(step):
    encoded = step.encode_tariff({**GRACE, "cap": "c"}, _grace_rules())
    assert encoded["values"]["tariff_duration_bands"] == "0-15 free; 15-60 total 1.50; 60- 1.50/60"
    assert encoded["values"]["garage_daily_cap_eur"] == 12.0 and encoded["values"]["garage_hourly_rate_eur"] is None
    assert encoded["assumptions"] == ["P5", "P8", "P10"] and encoded["rule_ids"] == ["f", "r", "c"]
    # no stated rounding: P4 as well, in the order of the ids
    assert step.encode_tariff(GRACE, _grace_rules(rounding=None))["assumptions"] == ["P4", "P5", "P8", "P10"]
    # a 30 min unit: the bands follow the unit of the rule
    assert step.encode_tariff(GRACE, _grace_rules(unit=30, amount=0.5, free_end=10))["values"]["tariff_duration_bands"] == (
        "0-10 free; 10-30 total 0.50; 30- 0.50/30")


@pytest.mark.parametrize("kwargs, message", [
    ({"free_end": 60}, "shorter than the billing unit"),
    ({"free_end": 90}, "shorter than the billing unit"),
    ({"free_type": "increment"}, "is no free period"),
    ({"free_start": 5}, "starts at 5 min, not at 0"),
    ({"rounding": "pro_rata"}, "states the rounding 'pro_rata'"),
    ({"amount": 1.505}, "not a whole number of cents"),
    ({"preferred": False}, "does not mark it preferred_for_current_use"),
])
def test_the_grace_form_refuses_what_it_cannot_express(step, kwargs, message):
    with pytest.raises(SystemExit, match=message):
        step.encode_tariff(GRACE, _grace_rules(**kwargs))


def test_the_grace_form_needs_exactly_a_free_rule_and_a_rate_rule_and_excludes_every_other_form(step):
    with pytest.raises(SystemExit, match="exactly two rule ids"):
        step.encode_tariff({**GRACE, "grace": ("f", "r", "c")}, _grace_rules())
    with pytest.raises(SystemExit, match="exactly one of"):
        step.encode_tariff({**GRACE, "rate": "r"}, _grace_rules())
    with pytest.raises(SystemExit, match="no first period"):
        step.encode_tariff({**GRACE, "first": "f"}, _grace_rules())


def test_the_free_period_of_the_supplement_must_equal_the_free_rule_of_the_regional_package(step, inputs):
    inputs["rules"]["AIR_REGULAR"]["free_period_minutes"] = 20
    with pytest.raises(SystemExit, match="free period of 20 min.*15 min"):
        step.build_garages(inputs)


# --------------------------------------------------------------------------- the rest tier of a day rate


def _tier_rules(night_start="21:00", night_end="06:30", day_unit=60, night_unit=60, preferred=True) -> dict:
    night_times = _times(night_start, night_end, days=WEEKDAYS[:7])
    for entries in night_times.values():
        if entries:
            entries[0]["crosses_midnight"] = night_end < night_start
    return {"n": _rule("n", "increment", "X", 1.0, unit=night_unit, times=night_times),
            "d": _rule("d", "increment", "X", 2.0, unit=day_unit, rounding="started_unit", preferred=preferred)}


def test_a_day_rate_without_charging_times_takes_the_rest_of_the_day_beside_a_night_tier(step):
    encoded = step.encode_tariff({"garage_id": "t", "tiers": ("n",), "rest_tier": "d"}, _tier_rules())
    assert encoded["values"]["tariff_tiers"] == "06:30-21:00 2.00/60; 21:00-06:30 1.00/60"
    assert encoded["assumptions"] == ["P4", "P6"] and ("rest", "d", "06:30-21:00") in encoded["readings"]
    assert encoded["values"]["garage_hourly_rate_eur"] is None and encoded["values"]["garage_fee_start_h"] is None
    # a tier inside the day leaves two rest intervals around it, joined across midnight into one tier at the day rate
    inside = step.encode_tariff({"garage_id": "t", "tiers": ("n",), "rest_tier": "d"}, _tier_rules("10:00", "12:00"))
    assert inside["values"]["tariff_tiers"] == "10:00-12:00 1.00/60; 12:00-10:00 2.00/60"


@pytest.mark.parametrize("kwargs, message", [
    ({"night_start": "00:00", "night_end": "24:00"}, "no time of day is left"),
    ({"day_unit": 30}, "different billing units"),
    ({"preferred": False}, "does not mark it preferred_for_current_use"),
])
def test_the_rest_tier_refuses_what_it_cannot_express(step, kwargs, message):
    with pytest.raises(SystemExit, match=message):
        step.encode_tariff({"garage_id": "t", "tiers": ("n",), "rest_tier": "d"}, _tier_rules(**kwargs))


def test_a_rest_tier_belongs_to_the_tiered_form_only(step):
    with pytest.raises(SystemExit, match="rest_tier belongs to the tiered form"):
        step.encode_tariff({"garage_id": "t", "rate": "d", "rest_tier": "d", "window": None}, _tier_rules())


# --------------------------------------------------------------------------- readings of a cap and of a unit


def test_a_cap_stated_per_calendar_day_is_a_reading_of_its_own_and_a_stated_other_period_is_none(step):
    rules = {"r": _rule("r", "increment", "X", 1.0, unit=60, rounding="started_unit"),
             "c": _rule("c", "daily_cap", "X", 9.0, cap=9.0, period="calendar_day"),
             "k": _rule("k", "daily_cap", "X", 9.0, cap=9.0, period="daytime_07_18"),
             "u": _rule("u", "daily_cap", "X", 9.0, cap=9.0, period="day_definition_unspecified")}
    assert ("cap_calendar", "c", "calendar_day") in step.encode_tariff({"garage_id": "g", "rate": "r", "cap": "c", "window": None},
                                                                      rules)["readings"]
    assert step.encode_tariff({"garage_id": "g", "rate": "r", "cap": "k", "window": None}, rules)["readings"] == []
    assert step.encode_tariff({"garage_id": "g", "rate": "r", "cap": "u", "window": None}, rules)["readings"] == [
        ("cap", "u", "day_definition_unspecified")]


def test_a_rate_whose_unit_the_source_does_not_state_is_a_reading(step):
    rules = {"r": _rule("r", "increment", "X", 0.3, unit=60, start=60, rounding=None),
             "f": _rule("f", "duration_total", "X", 0.7, start=0, end=60)}
    rules["r"]["unit_reading"] = "read as the unit of the first price"
    encoded = step.encode_tariff({"garage_id": "g", "rate": "r", "first": "f", "window": None}, rules)
    assert ("unit", "r") in encoded["readings"]


# --------------------------------------------------------------------------- the QA table and the files


def test_the_qa_table_records_the_decided_monthly_product_the_separate_week_product_and_the_cap_variant(step, inputs, tmp_path,
                                                                                                        monkeypatch):
    frame = step.build_garages(inputs)
    directory = step.load_directory(_directory(tmp_path, step, monkeypatch))
    table = pd.DataFrame(step.qa_rows(inputs, frame, directory))
    used = table[table["decision"] == "used"].iloc[0]
    assert used["garage_id"] == "gs_gal" and used["amount_eur"] == "39.00" and used["evidence"] == "GAL_MONTHLY"
    week = table[table["record_id"] == "monthly_bs_air_week"].iloc[0]
    assert (week["decision"], week["reason_code"], week["amount_eur"]) == ("not_used", "not_monthly_or_30_day", "99.00")
    gal = table[table["record_id"] == "garage_gs_gal"].iloc[0]
    assert "the direct locator states 8.00 (conflicting variant, not used)" in gal["note"]
    candidates = table[table["record_type"] == "candidate"]
    # the directory entries, the one package candidate and the aggregated Wolfsburg car parks of the layer
    assert set(candidates["reason_code"]) == {"bga_zone", "zone_street_product", "no_coordinates", "no_published_tariff"}
    # the four garages that had no coordinates are garage rows, no longer candidates
    assert {"garage_bs_add1", "garage_bs_add2", "garage_he_stoben", "garage_he_groepern"} <= set(table["record_id"])
    pq.validate_garage_qa(table, frame)


def _directory(tmp_path, step, monkeypatch, names=("Parkplatz Markthalle", "Parkplatz Werder")) -> Path:
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.52, 52.26]},
                 "properties": {"name": name, "id": f"id{number}", "mapsightIconId": "parkflaeche",
                                "description": "<p>Tarife:</p><p>Parkzone 1 0,90 € / 30 Min.</p>"}}
                for number, name in enumerate(names)]
    path = tmp_path / "directory.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    monkeypatch.setattr(step, "DIRECTORY_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def _municipalities() -> gpd.GeoDataFrame:
    from shapely.geometry import box

    return gpd.GeoDataFrame({"ags": [BS, WOB, GS, HE]}, geometry=[
        box(X0 - 1000, Y0 - 1000, X0 + 2000, Y0 + 1000), box(X0 + 9_000, Y0 - 1000, X0 + 12_000, Y0 + 1000),
        box(X0 + 19_000, Y0 - 1000, X0 + 23_000, Y0 + 1000), box(X0 + 29_000, Y0 - 1000, X0 + 31_000, Y0 + 1000)],
        crs=METRIC_CRS).set_index("ags")


def test_the_new_points_lie_inside_their_own_municipality(step, rows, capsys):
    step.check_positions(rows.reset_index(), _municipalities())
    assert "position check: 11/11 garages inside their own municipality" in capsys.readouterr().out
    moved = rows.reset_index()
    moved.loc[moved["garage_id"] == "he_stoben", "geometry"] = Point(X0 + 5000, Y0)
    with pytest.raises(SystemExit, match=r"garages outside their municipality: he_stoben"):
        step.check_positions(moved, _municipalities())


def test_the_files_with_the_supplement_are_ascii_documented_and_two_runs_write_identical_bytes(
        step, regional, supplement_package, followup_package, tmp_path, monkeypatch):
    written = []
    for run in ("first", "second"):
        (tmp_path / run).mkdir()
        inputs = step.load_garage_inputs(regional[0], expected_sha256=regional[1], supplement_path=supplement_package[0],
                                         expected_supplement_sha256=supplement_package[1], followup_path=followup_package[0],
                                         expected_followup_sha256=followup_package[1])
        frame = step.build_garages(inputs)
        rows_ = step.qa_rows(inputs, frame, step.load_directory(_directory(tmp_path / run, step, monkeypatch)))
        pg.write_garages(frame, tmp_path / run / "garages.geojson", members=step.dataset_members())
        step.write_garage_qa(tmp_path / run / "qa.csv", rows_)
        written.append((tmp_path / run / "garages.geojson").read_bytes() + (tmp_path / run / "qa.csv").read_bytes())
    assert written[0] == written[1]
    text = (tmp_path / "first" / "garages.geojson").read_text(encoding="utf-8")
    assert text.isascii()
    document = json.loads(text)
    assert "supplement" in document["attribution"].lower() and "ODbL 1.0" in document["license"]
    assert set(document["documentation"]["columns"]) == set(pg.DATASET_COLUMNS)
    assert "supplement package" in document["documentation"]["columns"]["package_sha256"]
    loaded = pg.load_garages(tmp_path / "first" / "garages.geojson")
    pg.validate_garages(loaded)
    pq.validate_garage_qa(pq.load_garage_qa(tmp_path / "first" / "qa.csv"), loaded)
