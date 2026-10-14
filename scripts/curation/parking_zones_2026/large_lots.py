"""Large surface lots as off-street options (parking cost zones v2, spec Amendment I, issue #436, Task 4j).

Not part of the pipeline and never run by synpp: an optional input of ``regional_garages.py``
(``--large-lots-zip``). The owner-supplied ``Grossparkplaetze_ZGB8_2026-10-12.zip`` (data state 2026-10-13) is a research
package of 152 reviewed parking records of the eight ZGB towns: per record the access class, the capacity for the size
threshold with its origin, the geometry scope, the documented tariff components and the sources. It is read the way the
Wolfsburg car-park package is read (``wolfsburg_lots``):

* ``load_package`` checks the zip against the SHA-256 pinned in ``LOTS_SHA256`` before anything is read, and every member of
  the zip against the zip's own ``MANIFEST.json`` (the set of members must equal the set of the manifest and every member must
  have its recorded SHA-256); nothing is extracted and nothing in the zip (it carries scripts) is ever executed. The points are
  those of ``data/researched_locations.geojson`` (EPSG:4326, projected to EPSG:25832) and are cross-checked against the
  GeoPackage layer ``researched_locations`` (EPSG:25832) to 1 cm where the package holds it; they are research points inside a
  parking area and no entrances.
* ``decide`` applies the size and access policy of spec Amendment I2 (ASSUMPTION L50) to one record at a threshold and returns
  the reason of a record that is no option, or None. It uses the fields in the sense of the package's README: the capacity
  for the threshold is ``capacity_selected_for_threshold`` (reported before OSM before the area estimate, the package's
  order), a missing capacity is unknown and never 0, and the access classes that count as general public are those that the
  package's own shortlist rule uses. ``check_package_flags`` recomputes the package's ``public_candidate_<n>`` flags with the
  same rule and stops when they differ, so a changed package cannot make the policy silently disagree with the shortlist.
* ``convert_rules`` writes the package's tariff components in the regional rule schema of ``regional_garages`` (so that one
  encoder reads every garage and lot), with the role each component plays named by the owner decision of
  ``regional_garage_specs.LARGE_LOT_SPECS``; ``zone_product_rules`` does the same for a zone's street product of
  ``parking_tariffs_2026.csv`` (spec Amendment I1: a lot whose documented tariff is its zone's street product carries that
  product as its own schedule). A component the grammar of ``braunschweig.parking.garages`` cannot express stops the row
  (``SystemExit`` from the encoder, reported as ``lot_tariff_not_expressible``); nothing is approximated.
* ``source_of`` names the source of a row: the first source of the package that states a URL and a date and is no web-service
  layer (``is_web_service_url``: a map layer places and zones a site, it states no fee); ``capacity_origin`` tells the origin
  of the capacity that the threshold uses (reported, OSM tag, area estimate, derived), both counted and logged by the step.
* ``median_schedule`` is ASSUMPTION P14 (spec Amendment I4): the median product of the priced lots of the same zone.
* ``load_free_evidence`` checks the archived evidence pages of the free Wolfsburg lots (spec Amendment I5a) against their
  ``MANIFEST.md``.

CRS: EPSG:25832 throughout, money in EUR, distances in m, capacities in parking spaces.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

import curation_common as cc
import municipal_zones as mz

LOTS_NAME = "Grossparkplaetze_ZGB8_2026-10-12"
LOTS_FILE = f"{LOTS_NAME}.zip"
#: SHA-256 of the owner's research package (also in the data record parking_garages_2026).
LOTS_SHA256 = "d467275ced5b68717ff2f426ad94be4debd085a9bebfaa239cfa6f4518fc9db4"
MANIFEST_MEMBER = "MANIFEST.json"
FACILITIES_MEMBER = "data/facilities.json"
RULES_MEMBER = "data/tariff_rules.json"
SOURCES_MEMBER = "data/sources.json"
SUMMARY_MEMBER = "data/summary.json"
LOCATIONS_MEMBER = "data/researched_locations.geojson"
GPKG_MEMBER = "data/surface_parking.gpkg"
GPKG_LAYER = "researched_locations"
CROSSWALK_MEMBER = "review/wolfsburg_24_geometry_crosswalk.json"
ALIASES_MEMBER = "review/municipal_point_aliases.json"
#: The two files the package's README names as the binding reading of its fields.
README_MEMBERS = ("INTEGRATION.md", "README_EINSTIEG_PARKPLATZDATEN.md")
#: ASSUMPTION L50 (spec Amendment I2): the size threshold of a surface lot in parking spaces. 30 and 100 are sensitivities of the
#: calibration (spec I6), written only to a path the caller gives, never to the release path.
DEFAULT_THRESHOLD = 50
SENSITIVITY_THRESHOLDS = (30, 100)
#: Thresholds the package's shortlist flags exist for.
FLAG_THRESHOLDS = (30, 50, 100)
#: The access classes that the package's own shortlist rule (``build_package.py``, never run here) counts as general public use;
#: every other class (customers, events, monthly-only, permit-only, closed, unknown) is no unconditional option (spec I2).
PUBLIC_ACCESS_CLASSES = ("public", "public_with_customer_discount", "mixed_public_and_customers")
#: Operational states of the package that are no open facility.
NOT_OPEN_STATES = ("closed", "planned", "not_yet_open", "under_construction", "closed_redeveloped")
COORDINATE_TOLERANCE_M = 0.01
#: The capacity origins that are reported (a number the facility's own source states) and that therefore fill the dataset column
#: ``capacity_reported``; every other origin (OSM, area estimate, derived) is named in the notes only.
REPORTED_METHODS = ("reported",)
#: The package's town names and the town keys of ``regional_garage_specs.TOWNS``.
TOWN_KEYS = {"Braunschweig": "bs", "Wolfsburg": "wob", "Wolfenb\u00fcttel": "wf", "Gifhorn": "gf", "Helmstedt": "he",
             "Peine": "pe", "Salzgitter": "sz", "Goslar": "gs"}
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DAY_CODES = dict(zip(("MO", "TU", "WE", "TH", "FR", "SA", "SU"), WEEKDAY_NAMES))
#: How a package component is typed where its owner decision does not name the type: a free component and a free short stay
#: (the Broetchentaste of Helmstedt and Salzgitter: a free ticket of up to 30 min, never a free day, spec I3).
FREE_BILLING_PERIODS = ("free", "kostenlos", "free_subarea", "free_with_disc")
FREE_SHORT_STAY_BILLING_PERIODS = ("free_short_stay", "short_stay")
#: Billing periods of a rate whose package text states the rounding (a started interval, ``je angefangene ...``).
STARTED_UNIT_BILLING_PERIODS = ("started_interval", "started_hour", "started_half_hour")
EVIDENCE_COLUMNS = ("file", "url", "retrieved", "sha256", "statement")


# ---------------------------------------------------------------- the package
def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_package(path, expected_sha256: Optional[str] = None, verify_all: bool = True) -> dict:
    """The verified large-lot package at ``path`` (the zip itself).

    The zip must exist with exactly ``LOTS_SHA256`` (or ``expected_sha256``, for a synthetic test package), else ``SystemExit``.
    With ``verify_all`` (the default) the members of the zip must be exactly the members of ``MANIFEST.json`` plus the manifest
    itself and each must have the recorded SHA-256; otherwise every member that is read must. Returns {"path", "file" ({"file",
    "sha256", "bytes"}), "manifest" (member -> SHA-256), "facilities" (facility_key -> record of ``facilities.json``, in the
    package's order), "rules" (rule_id -> tariff component), "sources" (source id -> record with ``id``, ``url``, ``date``),
    "points" (GeoDataFrame in EPSG:25832 indexed by facility_key: the research points, only the records that have one),
    "crosswalk" (the 24 municipal points of Wolfsburg), "aliases" (municipal point -> the package facility that it belongs to),
    "summary"}. ``SystemExit`` for a duplicated facility key or rule id, a rule of a facility that the package does not hold, a
    tariff component id that no rule has, a point that is not a point, a GeoJSON point that differs from the GeoPackage point by
    more than ``COORDINATE_TOLERANCE_M`` and a shortlist flag that the policy of :func:`decide` does not reproduce."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's research package {LOTS_FILE} (--large-lots-zip) unchanged")
    sha256 = expected_sha256 or LOTS_SHA256
    actual = mz.file_sha256(path)
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_garages_2026); a changed "
                         "package is never read")
    with zipfile.ZipFile(path) as archive:
        try:
            manifest_document = json.loads(archive.read(f"{LOTS_NAME}/{MANIFEST_MEMBER}").decode("utf-8"))
        except KeyError:
            raise SystemExit(f"the package {LOTS_NAME} holds no {MANIFEST_MEMBER}; its members cannot be verified") from None
        manifest = {entry["path"]: entry["sha256"] for entry in manifest_document["files"]}
        if len(manifest) != len(manifest_document["files"]):
            raise SystemExit(f"{MANIFEST_MEMBER} lists a member twice")
        members = {name[len(LOTS_NAME) + 1:] for name in archive.namelist()
                   if name.startswith(f"{LOTS_NAME}/") and not name.endswith("/")}
        if verify_all:
            unlisted = sorted(members - set(manifest) - {MANIFEST_MEMBER})
            missing = sorted(set(manifest) - members)
            if unlisted or missing:
                raise SystemExit(f"the members of the zip and of {MANIFEST_MEMBER} differ: not in the manifest {unlisted[:5]}, "
                                 f"not in the zip {missing[:5]}")
            for member in sorted(manifest):
                _read_member(archive, manifest, member)

        def member_json(name):
            return json.loads(_read_member(archive, manifest, name).decode("utf-8"))

        facility_document = member_json(FACILITIES_MEMBER)
        rule_document = member_json(RULES_MEMBER)
        source_list = member_json(SOURCES_MEMBER)
        summary = member_json(SUMMARY_MEMBER)
        locations = member_json(LOCATIONS_MEMBER)
        crosswalk = member_json(CROSSWALK_MEMBER)
        aliases = member_json(ALIASES_MEMBER)
        for readme in README_MEMBERS:
            _read_member(archive, manifest, readme)  # the binding reading of the fields: verify it is the one recorded
        gpkg_present = GPKG_MEMBER in manifest
        if gpkg_present:
            _read_member(archive, manifest, GPKG_MEMBER)  # GDAL reads the GeoPackage from the zip: verify its bytes first
    package = {"path": path, "file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size},
               "manifest": manifest, "summary": summary, "crosswalk": crosswalk, "aliases": aliases}
    facilities = {}
    for record in facility_document["facilities"]:
        if record["facility_key"] in facilities:
            raise SystemExit(f"facilities.json holds the facility {record['facility_key']} twice")
        facilities[record["facility_key"]] = record
    rules = {}
    for rule in rule_document["rules"]:
        if rule["rule_id"] in rules:
            raise SystemExit(f"tariff_rules.json holds the rule {rule['rule_id']} twice")
        if rule["facility_id"] not in facilities:
            raise SystemExit(f"the rule {rule['rule_id']} belongs to the facility {rule['facility_id']}, which the package does "
                             "not hold")
        rules[rule["rule_id"]] = rule
    for key, record in facilities.items():
        unknown = [rule_id for rule_id in record.get("tariff_rule_ids") or [] if rule_id not in rules]
        if unknown:
            raise SystemExit(f"facility {key} lists the tariff components {unknown}, which the package does not hold")
    sources = {}
    for source in source_list:
        source_id = source.get("id") or source.get("source_id")
        retrieved = source.get("retrieved_at") or source.get("retrieved_at_utc") or source.get("date")
        sources[source_id] = {"id": source_id, "url": source.get("url"), "date": None if retrieved is None else str(retrieved)[:10]}
    package.update(facilities=facilities, rules=rules, sources=sources)
    package["points"] = _read_points(path, locations, facilities, gpkg_present)
    check_package_flags(package)
    return package


def _read_member(archive: zipfile.ZipFile, manifest: dict, member: str) -> bytes:
    """The bytes of ``member``, checked against the manifest of the package: a member that the manifest does not list, one that
    is missing from the zip and one that differs from its manifest entry stop the step."""
    if member not in manifest:
        raise SystemExit(f"package member {member} is not listed in {MANIFEST_MEMBER}; a member that the manifest does not vouch "
                         "for is never read")
    try:
        content = archive.read(f"{LOTS_NAME}/{member}")
    except KeyError:
        raise SystemExit(f"package member {member} is listed in {MANIFEST_MEMBER} but missing from the zip") from None
    digest = _sha256(content)
    if digest != manifest[member]:
        raise SystemExit(f"package member {member} has the SHA-256 {digest} but {MANIFEST_MEMBER} states {manifest[member]}: a "
                         "changed member is never read")
    return content


def _read_points(path: Path, locations: dict, facilities: dict, gpkg_present: bool) -> gpd.GeoDataFrame:
    """The research points of the facilities that have one (EPSG:25832, indexed by facility_key). A point is a position inside a
    parking area and no entrance. ``SystemExit`` for a point of an unknown facility, a geometry that is no valid point, a facility
    twice and, where the package holds the GeoPackage, a point that differs from the GeoPackage layer by more than
    ``COORDINATE_TOLERANCE_M``."""
    keys, points = [], []
    for feature in locations["features"]:
        key = feature["properties"]["facility_key"]
        if key not in facilities:
            raise SystemExit(f"{LOCATIONS_MEMBER} holds a point of {key}, which facilities.json does not hold")
        if feature["geometry"] is None:
            continue
        if feature["geometry"]["type"] != "Point":
            raise SystemExit(f"{LOCATIONS_MEMBER}: the geometry of {key} is a {feature['geometry']['type']}; a position stays a "
                             "point (no ring, no buffer)")
        if key in keys:
            raise SystemExit(f"{LOCATIONS_MEMBER} holds a point of {key} twice")
        keys.append(key)
        points.append(Point(feature["geometry"]["coordinates"][:2]))
    frame = gpd.GeoDataFrame({"facility_key": keys}, geometry=points, crs="EPSG:4326").to_crs(cc.METRIC_CRS)
    frame = frame.set_index("facility_key")
    if frame.empty or not frame.geometry.is_valid.all() or frame.geometry.is_empty.any():
        raise SystemExit(f"{LOCATIONS_MEMBER}: empty, or invalid or empty points; the step repairs nothing")
    if gpkg_present:
        layer = gpd.read_file(f"/vsizip/{path.resolve().as_posix()}/{LOTS_NAME}/{GPKG_MEMBER}", layer=GPKG_LAYER)
        if layer.crs is None or layer.crs.to_epsg() != 25832:
            raise SystemExit(f"{GPKG_MEMBER} layer {GPKG_LAYER}: CRS {layer.crs} is not EPSG:25832, as the package states")
        layer_points = {}
        for _, row in layer.iterrows():
            if row.geometry is not None and not row.geometry.is_empty:
                layer_points[json.loads(row["properties_json"])["facility_key"]] = row.geometry
        if set(layer_points) != set(frame.index):
            raise SystemExit(f"the GeoJSON and the GeoPackage layer {GPKG_LAYER} hold points of different facilities: "
                             f"{sorted(set(layer_points) ^ set(frame.index))}")
        for key, geometry in frame.geometry.items():
            distance = float(geometry.distance(layer_points[key]))
            if distance > COORDINATE_TOLERANCE_M:
                raise SystemExit(f"the GeoJSON point of {key} differs from the GeoPackage point by {distance:.3f} m (more than "
                                 f"{COORDINATE_TOLERANCE_M} m)")
    return frame


# ---------------------------------------------------------------- the size and access policy (spec I2, ASSUMPTION L50)
def capacity_for_threshold(record: dict) -> Optional[int]:
    """The capacity of a record for the size threshold: ``capacity_selected_for_threshold`` (reported before OSM before the area
    estimate), None where it is unknown. A missing capacity is never 0, a negative or fractional one stops the step."""
    value = record.get("capacity_selected_for_threshold")
    if value is None:
        return None
    if int(value) != value or value < 0:
        raise SystemExit(f"facility {record['facility_key']}: capacity_selected_for_threshold {value!r} is no non-negative whole "
                         "number of spaces")
    return int(value)


def is_active(record: dict) -> bool:
    """Whether the package marks the record as an open facility (not closed, planned, under construction or excluded from the
    current inventory); the rule of the package's own shortlist."""
    state = record.get("operational_status", record.get("operating_status", ""))
    return not record.get("exclude_current_inventory") and record.get("access_class") != "closed" and state not in NOT_OPEN_STATES


def decide(record: dict, threshold: int, owner_public=()) -> Optional[str]:
    """The reason that a record is no option of the dataset at ``threshold``, or None for an option (spec Amendment I2). The
    order of the checks is the order of the package's shortlist rule: not open (``lot_not_open``), no surface lot
    (``lot_not_surface_lot``), an alias of another record (``lot_alias``), an unconfirmed or secondary-only candidate
    (``lot_unconfirmed_candidate``), access that is not general public (``lot_access_not_public``; ``owner_public`` names the
    facility keys whose public access an owner decision establishes on documented evidence), an unknown capacity
    (``lot_capacity_unknown``) and a capacity below the threshold (``lot_below_threshold``). A reported capacity takes precedence
    over an estimate by the package's own selection order."""
    if not is_active(record):
        return "lot_not_open"
    if record.get("facility_type") != "surface_lot":
        return "lot_not_surface_lot"
    if record.get("alias_of"):
        return "lot_alias"
    if record.get("candidate_only") or record.get("evidence_class") == "secondary_only":
        return "lot_unconfirmed_candidate"
    if record.get("access_class") not in PUBLIC_ACCESS_CLASSES and record["facility_key"] not in owner_public:
        return "lot_access_not_public"
    capacity = capacity_for_threshold(record)
    if capacity is None:
        return "lot_capacity_unknown"
    if capacity < threshold:
        return "lot_below_threshold"
    return None


def check_package_flags(package: dict) -> None:
    """The package's ``public_candidate_<n>`` flags (n in 30, 50, 100) must be exactly what :func:`decide` yields without an owner
    decision, and its summary count of the shortlist of 50 must be their sum; ``SystemExit`` naming the first differences."""
    differences = []
    for key, record in package["facilities"].items():
        for threshold in FLAG_THRESHOLDS:
            flag = record.get(f"public_candidate_{threshold}")
            expected = decide(record, threshold) is None
            if flag is not None and bool(flag) != expected:
                differences.append(f"{key}@{threshold}: flag {flag}, policy {expected}")
    if differences:
        raise SystemExit("the policy of spec Amendment I2 and the shortlist flags of the package disagree (a changed package?): "
                         + "; ".join(differences[:5]))
    count = sum(bool(record.get("public_candidate_50")) for record in package["facilities"].values())
    if package["summary"].get("public_candidates_50") != count:
        raise SystemExit(f"the summary of the package states {package['summary'].get('public_candidates_50')} candidates at 50 "
                         f"but facilities.json flags {count}")


#: The origins of the capacity that the size threshold uses, in the order the step logs them. ``other`` is a selection method that
#: ``capacity_origin`` does not know (a changed package): counted apart and warned, never silently put under another origin.
CAPACITY_ORIGINS = ("reported", "osm_tag", "area_estimate", "derived", "other")
#: The selection methods of the package that are an integer capacity tag of OpenStreetMap, and the ones that are an estimate
#: from the floor area (25 m2 per space), alone or summed over geometry components; a sum that adds an OSM tag to an estimate
#: counts as an estimate because an estimate enters it.
OSM_TAG_METHODS = ("osm_integer_capacity",)
AREA_ESTIMATE_METHODS = ("estimated_floor_area_m2_divided_by_25", "sum_nonoverlapping_component_area_estimates",
                         "sum_nonoverlapping_osm_capacities_and_area_estimates")


def capacity_origin(record: dict) -> str:
    """The origin of the capacity that the size threshold uses, from the selection method of the package (its own order: a reported
    capacity before an OSM tag before an area estimate): one of ``CAPACITY_ORIGINS``. ``derived`` is a derivation of reported
    numbers (the Check In car park: 334 spaces less 130 for hotel guests); a method that is none of the known ones is ``other``."""
    method = str(record.get("capacity_selection_method") or "")
    if method in REPORTED_METHODS:
        return "reported"
    if method in OSM_TAG_METHODS:
        return "osm_tag"
    if method in AREA_ESTIMATE_METHODS:
        return "area_estimate"
    if method.startswith("derived_"):
        return "derived"
    return "other"


def capacity_phrase(record: dict) -> str:
    """The capacity of a record in words with its origin, for the notes: 'capacity 101 for the threshold (reported, evidence
    level primary)' or 'capacity unknown'."""
    capacity = capacity_for_threshold(record)
    if capacity is None:
        return f"capacity unknown ({record.get('capacity_selection_method')})"
    return (f"capacity {capacity} for the threshold ({record.get('capacity_selection_method')}, evidence level "
            f"{record.get('capacity_evidence_level')})")


# ---------------------------------------------------------------- sources
#: The URL of an OGC web service or a map-server layer (the Geoviewer of the city of Wolfsburg is one). A layer places and zones a
#: site but states no fee in words, so it is the source of a tariff only where no other source of the row states a URL and a date.
WEB_SERVICE_URL = re.compile(r"/ows/|[?&]service=|/wms\b|/wfs\b|/arcgis/rest/|/FeatureServer\b|/MapServer\b", re.IGNORECASE)


def is_web_service_url(url) -> bool:
    """Whether ``url`` is the endpoint of a web-service layer (``WEB_SERVICE_URL``); an empty or missing URL is none."""
    return bool(url) and bool(WEB_SERVICE_URL.search(str(url)))


def source_of(package: dict, source_ids) -> tuple:
    """(URL, ISO date) of the source of a row, a source of the package and never a made-up one: the first of ``source_ids`` that
    states both a URL and a date and is no web-service layer (the layer that places a site is no statement of its fee: the first
    source of the Allerpark is the Geoviewer layer, its fee statement is the page after it), else the first that states both. The
    caller counts the second case (``is_web_service_url`` of the result) and logs it as a fallback. ``SystemExit`` when none of
    the sources states both."""
    layer = None
    for source_id in source_ids:
        source = package["sources"].get(source_id)
        if source and source["url"] and source["date"]:
            if not is_web_service_url(source["url"]):
                return source["url"], source["date"]
            layer = layer or (source["url"], source["date"])
    if layer is None:
        raise SystemExit(f"none of the sources {list(source_ids)} states both a URL and a date")
    return layer


# ---------------------------------------------------------------- tariff components in the regional rule schema
def _charging_times(schedule) -> dict:
    """The regional ``charging_times`` of a package schedule: the weekly windows where the package states them explicitly
    (``completeness`` ``explicit_windows_...``), else no time at all (no day is guessed). An interval stated twice for a day is one
    interval."""
    times = {day: None for day in WEEKDAY_NAMES + ("public_holidays",)}
    explicit = bool(schedule) and str(schedule.get("completeness", "")).startswith("explicit_windows")
    if not explicit or not schedule.get("weekly_windows"):
        return times
    for window in schedule["weekly_windows"]:
        for code in window["days"]:
            entry = {"start": window["start"], "end": window["end"], "crosses_midnight": False}
            day = DAY_CODES[code]
            if times[day] is None:
                times[day] = [entry]
            elif entry not in times[day]:
                times[day].append(entry)
    return times


def infer_rule_type(rule: dict) -> tuple:
    """(regional rule type, elapsed from minute, elapsed to minute or None) of a package component whose owner decision names
    no type: a free component is ``free``, a free short stay ``free_period`` from 0 to its maximum stay, a priced component with a
    billing unit ``increment`` from 0. ``SystemExit`` for anything else (the decision must say what it is)."""
    amount, period = rule.get("amount_eur"), rule.get("billing_period")
    # an explicit amount 0 of a component is a known price, not a missing one (the package's README: null is unknown); the
    # Wolfsburg components of the free car parks state the 0 without a billing period and without a type
    if amount == 0 and (period in FREE_BILLING_PERIODS or (period is None and rule.get("rule_type") is None)):
        return "free", 0, None
    if amount == 0 and period in FREE_SHORT_STAY_BILLING_PERIODS and rule.get("max_stay_minutes"):
        return "free_period", 0, int(rule["max_stay_minutes"])
    if amount and rule.get("billing_unit_minutes") and period not in FREE_BILLING_PERIODS + FREE_SHORT_STAY_BILLING_PERIODS:
        return "increment", 0, None
    raise SystemExit(f"tariff component {rule['rule_id']} (billing period {period!r}, amount {amount!r}) is no free component, "
                     "no free short stay and no rate; the owner decision of the lot must name its role")


def convert_rules(package: dict, facility_key: str, roles: dict, used) -> dict:
    """{rule id: rule} of the tariff components of ``facility_key`` in the regional rule schema (``regional_garages``), the
    ``<id>:cap`` rule of a component with a positive daily cap added.

    ``roles`` is the owner decision {rule id: {"type", "from", "to", "rounding"} (every key optional)} for the components whose
    role is not the plain one of :func:`infer_rule_type`; ``used`` are the rule ids (and cap ids) that set a value of the row:
    only those are marked ``preferred_for_current_use`` (the owner decision of spec Amendment I3 plays the part of the package's
    preferred flag, which this package does not have). The rounding is the stated started unit where the component's billing
    period says so (``je angefangene ...``), else the decision's, else none (ASSUMPTION P4). ``SystemExit`` for a component that
    the facility does not hold."""
    record = package["facilities"][facility_key]
    rules = {}
    for rule_id in record.get("tariff_rule_ids") or []:
        raw = package["rules"][rule_id]
        decision = roles.get(rule_id, {})
        kind, start, end = decision.get("type"), decision.get("from", 0), decision.get("to")
        if kind is None and rule_id in used:
            kind, start, end = infer_rule_type(raw)
        elif kind is None:
            # a component that sets no value (a customer condition, a ticket that is not used): typed as the package types it,
            # never inferred, so that it can be named in the notes without being read as a tariff
            kind, start, end = raw.get("rule_type") or "not_used", 0, None
        stated = "started_unit" if raw.get("billing_period") in STARTED_UNIT_BILLING_PERIODS else None
        rounding = decision.get("rounding") or stated
        common = {"facility_id": facility_key, "source_ids": list(raw.get("source_ids") or []), "status": "documented_component",
                  "charging_times": _charging_times(raw.get("schedule")),
                  "time_window": {"from": None, "to": None, "days_raw": None},
                  "conditions": raw.get("conditions"), "origin": "large_lots"}
        rules[rule_id] = {**common, "rule_id": rule_id, "rule_type": kind,
                          "amount_eur": None if kind in ("free", "free_period") else raw.get("amount_eur"),
                          "billing_unit_minutes": raw.get("billing_unit_minutes"), "rounding": rounding,
                          "elapsed_from_minutes": start, "elapsed_to_minutes": end, "free_period_minutes": None,
                          "daily_cap_eur": None, "cap_period": None, "max_stay_minutes": raw.get("max_stay_minutes"),
                          "preferred_for_current_use": rule_id in used}
        cap = raw.get("daily_cap_eur")
        if cap and kind not in ("free", "free_period"):
            cap_id = f"{rule_id}:cap"
            rules[cap_id] = {**common, "rule_id": cap_id, "rule_type": "cap", "amount_eur": cap, "billing_unit_minutes": None,
                             "rounding": None, "elapsed_from_minutes": None, "elapsed_to_minutes": None,
                             "free_period_minutes": None, "daily_cap_eur": cap, "cap_period": None, "max_stay_minutes": None,
                             "preferred_for_current_use": cap_id in used, "charging_times": {day: None for day in
                                                                                          WEEKDAY_NAMES + ("public_holidays",)}}
    unknown = sorted(set(roles) - set(rules))
    if unknown:
        raise SystemExit(f"the owner decision of {facility_key} names the components {unknown}, which the facility does not hold")
    return rules


def _clock_text(hours: float) -> str:
    whole = int(hours)
    return f"{whole:02d}:{int(round((hours - whole) * 60)):02d}"


def zone_product_rules(tariffs, zone_id: str, facility_key: str) -> dict:
    """The street product of the zone ``zone_id`` of the tariff table ``tariffs`` (``parking_tariffs_2026.csv``, a DataFrame
    indexed or not by ``zone_id``) as regional rules of the lot ``facility_key``: the rate (EUR per billing unit = hourly rate x
    unit / 60, a whole number of cents; the unit of the table is a started unit) with the zone's fee window on Monday to Friday
    and, where the zone has a daily cap, the cap rule ``<facility_key>:zone_cap``. A zone with a maximum stay and its long-stay
    product (assumption M1: a stay above the maximum stay buys the product, whatever the metered price would be) gives the rate
    rule the elapsed range 0 min to the maximum stay and the long-stay product the open total rule ``<facility_key>:zone_long_stay``
    from the maximum stay, so the lot's schedule is the BANDS form that reproduces the street exactly (ruling R-4j-6; the bands
    take the chargeable minutes in the fee window like the street, and a total band and tiers may not mix). Returns {"rules":
    {id: rule}, "rate": rate rule id, "cap": cap rule id or None, "long_stay": long-stay rule id or None, "form": the form of the
    specification for the encoder ({"rate": ...} or {"bands": [...]}, "window": "rate", and "cap" where there is one), "row":
    the zone's table row as a dict}. ``SystemExit`` for a zone that the table does not hold or that has no hourly rate, an amount
    that is no whole number of cents, a zone product with a free threshold, a first period or a commuter-only price, a maximum stay
    without a long-stay product (the street product is then unavailable above it) and a daily cap beside a long-stay product (the
    cap would cap the product, the street does not): the garage grammar of the lot cannot carry those exactly, so they stop the row
    instead of being dropped or approximated."""
    table = tariffs.set_index("zone_id") if "zone_id" in tariffs.columns else tariffs
    if zone_id not in table.index:
        raise SystemExit(f"the tariff table holds no zone {zone_id}")
    row = table.loc[zone_id]
    hourly, unit = row["hourly_rate_eur"], row["billing_unit_min"]
    if pd.isna(hourly) or pd.isna(unit) or not hourly > 0:
        raise SystemExit(f"the zone {zone_id} has no hourly street rate in the tariff table (hourly {hourly}, unit {unit})")
    for column in ("free_if_stay_at_most_min", "first_period_min", "first_period_eur"):
        if pd.notna(row[column]):
            raise SystemExit(f"the street product of {zone_id} has {column} {row[column]}, which the schedule of a lot cannot "
                             "carry as one product; this zone needs components of its own")
    max_stay, long_stay = row["max_stay_min"], row["long_stay_product_eur"]
    if pd.notna(max_stay) and pd.isna(long_stay):
        raise SystemExit(f"the street product of {zone_id} has a maximum stay of {max_stay} min and no long-stay product: a stay "
                         "above it loses the street product (assumption M1), which a lot cannot carry; this zone needs "
                         "components of its own")
    if pd.notna(max_stay) and pd.notna(row["daily_cap_eur"]):
        raise SystemExit(f"the street product of {zone_id} has a daily cap beside a long-stay product, which the bands of a lot "
                         "cannot carry exactly: the cap would cap the long-stay product that the street does not cap")
    unit_amount = round(float(hourly) * float(unit) / 60.0, 6)
    if abs(unit_amount * 100 - round(unit_amount * 100)) > 1e-6:
        raise SystemExit(f"the street rate of {zone_id} ({hourly} EUR per hour, unit {unit} min) is {unit_amount} EUR per unit, no "
                         "whole number of cents")
    window = {"start": _clock_text(float(row["fee_start_h"])), "end": _clock_text(float(row["fee_end_h"])),
              "crosses_midnight": False}
    times = {day: ([dict(window)] if day in WEEKDAY_NAMES[:5] else None) for day in WEEKDAY_NAMES + ("public_holidays",)}
    rate_id = f"{facility_key}:zone_rate"
    common = {"facility_id": facility_key, "source_ids": [], "status": "documented_component", "conditions": None,
              "time_window": {"from": None, "to": None, "days_raw": None}, "origin": "tariff_table",
              "preferred_for_current_use": True, "max_stay_minutes": None}
    rules = {rate_id: {**common, "rule_id": rate_id, "rule_type": "increment", "amount_eur": round(unit_amount, 2),
                       "billing_unit_minutes": int(unit), "rounding": "started_unit", "elapsed_from_minutes": 0,
                       "elapsed_to_minutes": None if pd.isna(max_stay) else int(max_stay), "free_period_minutes": None,
                       "daily_cap_eur": None, "cap_period": None, "charging_times": times}}
    long_stay_id = None
    if pd.notna(max_stay):
        long_stay_id = f"{facility_key}:zone_long_stay"
        rules[long_stay_id] = {**common, "rule_id": long_stay_id, "rule_type": "duration_total",
                               "amount_eur": float(long_stay), "billing_unit_minutes": None, "rounding": None,
                               "elapsed_from_minutes": int(max_stay), "elapsed_to_minutes": None, "free_period_minutes": None,
                               "daily_cap_eur": None, "cap_period": None, "charging_times": times}
    cap_id = None
    if pd.notna(row["daily_cap_eur"]):
        cap_id = f"{facility_key}:zone_cap"
        rules[cap_id] = {**common, "rule_id": cap_id, "rule_type": "cap", "amount_eur": float(row["daily_cap_eur"]),
                         "billing_unit_minutes": None, "rounding": None, "elapsed_from_minutes": None,
                         "elapsed_to_minutes": None, "free_period_minutes": None, "daily_cap_eur": float(row["daily_cap_eur"]),
                         "cap_period": None, "charging_times": {day: None for day in WEEKDAY_NAMES + ("public_holidays",)}}
    form = {"bands": [rate_id, long_stay_id], "window": "rate"} if long_stay_id else {"rate": rate_id, "window": "rate"}
    if cap_id:
        form["cap"] = cap_id
    return {"rules": rules, "rate": rate_id, "cap": cap_id, "long_stay": long_stay_id, "form": form,
            "row": {key: (None if pd.isna(value) else value) for key, value in row.to_dict().items()}}


#: A weekday window in the free text of paid hours: an optional day range, then ``HH:MM-HH:MM`` (what follows, such as a shorter
#: Saturday, is not a weekday window; the Saturday part is named by ``regional_garages._lot_limitations`` as not modelled).
_FREE_TEXT_WEEKDAY_WINDOW = re.compile(
    r"^\s*(?:Mo\s*[-\u2013]\s*(?:Fr|Sa|So|Su)\s+)?"
    r"(\d{1,2}):(\d{2})\s*[-\u2013]\s*(\d{1,2}):(\d{2})")


def _named_source(package: dict, source_ids) -> str:
    """The first source of ``source_ids`` that states a URL as '<id> <URL> of <date>' (a source of the package, never made up),
    else a sentence saying that there is none."""
    for source_id in source_ids or ():
        source = package["sources"].get(source_id)
        if source and source["url"]:
            return f"source {source_id} {source['url']}" + (f" of {source['date']}" if source["date"] else "")
    return "no source of the package states a URL"


def _zone_row_source(row: dict) -> str:
    """The source of a table row of the zone as 'source <URL> of <date>' (and the assumption that its notes state for the billing
    unit, such as ASSUMPTION D3-a), else a sentence saying that the row names none."""
    if not row.get("source_url"):
        return "the table row names no source"
    text = f"source {row['source_url']}" + (f" of {row['source_date']}" if row.get("source_date") else "")
    assumption = re.search(r"ASSUMPTION ([A-Z][0-9]+-[a-z]+): billing_unit_min", str(row.get("notes") or ""))
    return text + (f", ASSUMPTION {assumption.group(1)}" if assumption else "")


def zone_check_sentence(package: dict, facility_key: str, rule_id: str, product: dict, zone_id: str) -> tuple:
    """(sentence, verdict) of the consistency check of a lot that carries the street product of its zone (spec Amendment I1): the
    documented component of the package (``rule_id``) against the table row of the zone. The hourly equivalent of the component
    (amount x 60 / unit) is compared with the zone's hourly rate; a difference is named as such and changes nothing (the zone
    tariff is never changed by a lot, the lot carries the zone's product). A billing unit that the package states differently from
    the zone row (the package reads an hour where the zone row bills a started half hour, ruling R-4j-7) is named with both
    sources. The component's paid hours are stated verbatim and the zone's fee window beside them, compared where the package
    structures them as weekday windows or states a weekday window in free text; hours that are neither are named as not compared."""
    raw = package["rules"][rule_id]
    amount, unit = raw.get("amount_eur"), raw.get("billing_unit_minutes")
    if not amount or not unit:
        raise SystemExit(f"the consistency component {rule_id} states no amount per billing unit")
    reference = round(float(amount) * 60.0 / float(unit), 4)
    zone_rate = float(product["row"]["hourly_rate_eur"])
    verdicts = ["rate equal" if abs(reference - zone_rate) < 0.005 else
                f"rate DIFFERS by {abs(reference - zone_rate):.2f} EUR per hour"]
    zone_unit = int(product["row"]["billing_unit_min"])
    if int(unit) != zone_unit:
        package_source = _named_source(package, raw.get("source_ids"))
        verdicts.append(f"billing unit DIFFERS: the package states {int(unit)} min ({package_source}), the zone row "
                        f"{zone_unit} min ({_zone_row_source(product['row'])})")
    window = f"{float(product['row']['fee_start_h']):g}-{float(product['row']['fee_end_h']):g} h"
    raw_hours = raw.get("paid_hours_raw") or raw.get("paid_hours")
    schedule = raw.get("schedule")
    zone_hours = (float(product["row"]["fee_start_h"]), float(product["row"]["fee_end_h"]))
    structured = _charging_times(schedule if isinstance(schedule, dict) else None)
    package_window = structured["monday"]
    if package_window:
        same = (package_window[0]["start"] == product["rules"][product["rate"]]["charging_times"]["monday"][0]["start"]
                and package_window[0]["end"] == product["rules"][product["rate"]]["charging_times"]["monday"][0]["end"])
        verdicts.append("fee window equal" if same else "fee window DIFFERS")
    elif raw_hours == "24/7":
        verdicts.append("fee window equal (24/7)" if zone_hours == (0.0, 24.0) else "fee window DIFFERS (the package states 24/7)")
    elif raw_hours and _FREE_TEXT_WEEKDAY_WINDOW.match(str(raw_hours)):
        found = _FREE_TEXT_WEEKDAY_WINDOW.match(str(raw_hours))
        package_minutes = (int(found.group(1)) * 60 + int(found.group(2)), int(found.group(3)) * 60 + int(found.group(4)))
        zone_minutes = (round(zone_hours[0] * 60), round(zone_hours[1] * 60))
        if package_minutes == zone_minutes:
            verdicts.append(f"fee window equal (read from the free text '{raw_hours}' on weekdays)")
        else:
            verdicts.append(f"fee window DIFFERS (the package states '{raw_hours}', the zone row "
                            f"{_clock_text(zone_hours[0])}-{_clock_text(zone_hours[1])})")
    elif raw_hours:
        verdicts.append("fee window not compared (the package states its weekdays only in free text)")
    sentence = (f"Consistency check of the documented component {rule_id} ({amount:.2f} EUR per {int(unit)} min = "
                f"{reference:.2f} EUR per hour"
                + (f", paid hours '{raw_hours}'" if raw_hours else "") + f") against the street product of {zone_id} "
                f"({zone_rate:.2f} EUR per hour, fee window {window}): " + "; ".join(verdicts)
                + "; the lot carries the zone's product and the zone tariff is not changed by it")
    return sentence, verdicts


# ---------------------------------------------------------------- ASSUMPTION P14 (spec Amendment I4)
def hourly_equivalent_eur(values: dict) -> float:
    """The price of a stay of 60 min at a lot with the tariff columns ``values`` (the ordering key of the median of ASSUMPTION P14):
    the hourly rate of the single-window form or the price of 60 min of the duration bands (the free schedule: 0)."""
    from braunschweig.parking import garages as pg

    if values.get("tariff_duration_bands"):
        return pg.duration_band_price_eur(pg.parse_duration_bands(values["tariff_duration_bands"]), 60.0)
    if values.get("garage_hourly_rate_eur") is not None:
        return float(values["garage_hourly_rate_eur"])
    raise SystemExit("a donor lot of ASSUMPTION P14 has neither an hourly rate nor duration bands")


def median_schedule(donors: list, label: str) -> dict:
    """The donor whose complete schedule is the MEDIAN product of ``donors`` (ASSUMPTION P14, spec Amendment I4): the donors are
    ordered by the price of a 60 min stay (:func:`hourly_equivalent_eur`); with an odd number the middle one is the median, with an
    even number the two middle ones must have IDENTICAL tariff columns (a tie of different schedules has no median product and
    stops the step; a median of two prices is never invented). ``donors``: dicts with ``id`` and ``values`` (the tariff columns),
    at least two. Returns the first middle donor. ``SystemExit`` for fewer than two donors or a tie of different schedules."""
    if len(donors) < 2:
        raise SystemExit(f"{label}: ASSUMPTION P14 needs at least two priced lots, found {len(donors)}")
    ordered = sorted(donors, key=lambda donor: (hourly_equivalent_eur(donor["values"]), donor["id"]))
    count = len(ordered)
    middle = [ordered[count // 2]] if count % 2 else [ordered[count // 2 - 1], ordered[count // 2]]
    if len(middle) == 2 and middle[0]["values"] != middle[1]["values"]:
        raise SystemExit(f"{label}: the two middle lots {middle[0]['id']} and {middle[1]['id']} of ASSUMPTION P14 have different "
                         "schedules; there is no median product, and none is invented")
    return middle[0]


# ---------------------------------------------------------------- the archived free-parking evidence (spec Amendment I5a)
def load_free_evidence(directory) -> dict:
    """{file name: {"url", "retrieved", "sha256", "statement", "path"}} of the evidence pages archived in ``directory`` under the
    ``MANIFEST.md`` that lists each page with its URL, retrieval date, SHA-256 and what it states (a markdown table with the columns
    ``EVIDENCE_COLUMNS``). Every listed file must exist and have exactly the recorded SHA-256, else ``SystemExit``; an absent
    directory or ``MANIFEST.md`` is an empty result (the lots that rest on such evidence are then QA rows, never free without a
    source)."""
    directory = Path(directory) if directory else None
    if directory is None or not (directory / "MANIFEST.md").is_file():
        return {}
    evidence = {}
    for line in (directory / "MANIFEST.md").read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue  # prose, also prose that mentions the column names with pipes
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != len(EVIDENCE_COLUMNS) or cells[0] in ("file", "") or set(cells[0]) <= set("-: "):
            continue
        entry = dict(zip(EVIDENCE_COLUMNS, cells))
        path = directory / entry["file"]
        if not path.is_file():
            raise SystemExit(f"{directory / 'MANIFEST.md'} lists {entry['file']}, which is not in the folder")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) or mz.file_sha256(path) != entry["sha256"]:
            raise SystemExit(f"the archived evidence {path} does not have the SHA-256 {entry['sha256']} of its MANIFEST.md entry")
        evidence[entry["file"]] = {**entry, "path": path}
    return evidence
