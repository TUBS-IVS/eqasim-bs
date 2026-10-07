"""Supplement package of the garage dataset (parking cost zones v2, spec Amendments E10 to E12, issue #436, Task 4b2).

Not part of the pipeline and never run by synpp: the second, optional input of ``regional_garages.py`` (``--supplement-zip``).
The owner-supplied ``Parkhaus_Ergaenzungen_2026-10-07.zip`` is NOT a replacement of the regional evidence package: it holds
four main points (the entrances of four garages the regional package lists without coordinates) and the findings of six tariff
checks, as partial records that the package itself marks ``full_cost_calculation_ready=false`` ("review individual fields; do
not replace complete existing records"). This module reads it the way the regional package is read:

* ``load_supplement`` checks the zip against the SHA-256 pinned in ``SUPPLEMENT_SHA256`` before anything is read and every
  member that is read against the zip's own ``manifest.sha256``; nothing is extracted and nothing in the zip (it carries
  scripts) is ever executed. The main points come from the GeoPackage layer ``entrances`` (EPSG:25832) and are cross-checked
  against ``entrances.geojson`` (EPSG:4326); the layer ``reference_points`` (alternatives) is never read, so no alternative can
  count as a garage. A position stays a point: no ring, no buffer, no tariff extent.
* ``identity_map`` applies the identity rules of the package README: a supplement facility is matched to a regional facility by
  its ``facility_id`` or by a verified ``legacy_id`` (a legacy id that the regional package does not hold is refused); the
  ambiguous legacy key ``BS_None`` is never an identity, and the new Groepern garage ``HE_GROEPERN_TG_118`` is never joined to
  ``HE_GROEPERN_STRASSE`` (18 fee-liable street spaces, not the underground garage with 118 spaces).
* ``convert_rules`` writes the supplement's rules in the regional rule schema (so the garage encoding reads one schema), all
  of them NOT preferred; ``release_rules`` marks a rule preferred exactly where an owner decision of the task releases it and
  the package's own field decision still has the status the decision relied on, and records which decision released it. A rule
  that is not released can never set a value (ruling R-4b-8, no waiver), and a value that the package leaves open stays open.
* ``brochure_rules`` reads the one tariff that no package rule holds (the Helmstedt underground garage Groepern) from the
  quotations of the city brochure text that the supplement keeps: the amounts and the unit are PARSED from the quoted text, and
  the step refuses a quotation that is not in the brochure row of the garage.

CRS: EPSG:25832 throughout, money in EUR, distances in m.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import Point

import curation_common as cc
import municipal_zones as mz

FOLLOWUP_NAME = "Parkhaus_Nachrecherche_2026-10-07"
FOLLOWUP_FILE = f"{FOLLOWUP_NAME}.zip"
#: SHA-256 of the owner's follow-up package (spec E13; also in the data record parking_garages_2026 and in MANIFEST.md).
FOLLOWUP_SHA256 = "3bbaff93fb8b26d6cdfb187c7e0bf746f099997c1c7fd04a961fcae7d37329d8"
FOLLOWUP_REVIEW_MEMBER = "data/facility_review.json"
FOLLOWUP_ACTIONS_MEMBER = "data/model_actions.json"
FOLLOWUP_POINT_MEMBER = "data/archived_charley_point.geojson"
SUPPLEMENT_NAME = "Parkhaus_Ergaenzungen_2026-10-07"
SUPPLEMENT_FILE = f"{SUPPLEMENT_NAME}.zip"
#: SHA-256 of the owner's supplement package (also in the data record parking_garages_2026 and in MANIFEST.md next to the zip).
SUPPLEMENT_SHA256 = "76d2651433e05a4d0d0a75ba352fd17f99b329555eb3bb4525c34990383978fa"
MANIFEST_MEMBER = "manifest.sha256"
ENTRANCES_GEOJSON_MEMBER = "data/entrances.geojson"
PATCH_MEMBER = "data/parking_patch.gpkg"
#: The one layer of the GeoPackage that is read: the main points. ``reference_points`` holds alternatives and is never read.
ENTRANCES_LAYER = "entrances"
FACILITY_UPDATES_MEMBER = "data/facility_updates.json"
TARIFF_RULES_MEMBER = "data/tariff_rules.json"
FIELD_DECISIONS_MEMBER = "data/field_decisions.json"
OBSERVATIONS_MEMBER = "data/tariff_observations.json"
SOURCES_MEMBER = "data/sources.json"
#: The GeoJSON points and the GeoPackage points are the same points in two CRS; they may differ by the rounding of the
#: coordinates (7 decimals of a degree are about 1 cm) and by nothing else.
COORDINATE_TOLERANCE_M = 0.01
#: The legacy key of the regional package that mixes two garages (the Forschungsflughafen and the Ring-Center): never an identity.
AMBIGUOUS_LEGACY_ID = "BS_None"
#: Regional facilities that a supplement facility must NOT be joined to (package README, "Feldweise Uebernahme"): {supplement
#: facility: {regional facility: why}}.
FORBIDDEN_JOINS = {"HE_GROEPERN_TG_118": {"HE_GROEPERN_STRASSE": "the old object means 18 fee-liable street spaces, not the "
                                                                  "underground garage with 118 spaces"}}
#: How the package's ``point_type`` of a main point is named as the geometry method of the dataset row, and how it is told in the
#: notes: an OSM-mapped entrance (a node tagged as a parking entrance) or a derived access point (the connection of a driveway
#: to the street, no confirmed barrier or portal point).
ENTRANCE_METHODS = {"mapped_parking_entrance": "osm_mapped_parking_entrance",
                    "inferred_driveway_connection": "derived_access_point_on_osm_node"}
#: The supplement's rounding words in the regional schema: ``ceil`` is a started unit; no other word is accepted.
ROUNDING_WORDS = {None: None, "unspecified": None, "ceil": "started_unit"}
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
#: The rule scopes of the supplement and the regional rule type each becomes.
SCOPE_RULE_TYPES = {"regular": "increment", "night_only": "increment", "prebooked_seven_days": "weekly_ticket",
                    "monthly_product": "monthly_product"}


def _norm(text: str) -> str:
    """NFKC form of a text (the brochure's ligature 'ff' becomes 'ff'), so that a quotation and the text compare equal."""
    return unicodedata.normalize("NFKC", str(text))


# ---------------------------------------------------------------- the package
def _manifest(archive: zipfile.ZipFile, root: str = SUPPLEMENT_NAME) -> dict:
    """{relative member path: SHA-256} of the zip's own ``manifest.sha256`` (lines ``<sha256>  <path>``)."""
    try:
        text = archive.read(f"{root}/{MANIFEST_MEMBER}").decode("utf-8")
    except KeyError:
        raise SystemExit(f"the package {root} holds no {MANIFEST_MEMBER}; its members cannot be verified") from None
    manifest = {}
    for line in text.splitlines():
        if line.strip():
            digest, _, member = line.strip().partition("  ")
            manifest[member.strip()] = digest.strip()
    return manifest


def _read_member(archive: zipfile.ZipFile, manifest: dict, member: str, root: str = SUPPLEMENT_NAME) -> bytes:
    """The bytes of ``member``, checked against the manifest of the zip: a member the manifest does not list, one that is
    missing from the zip and one that differs from its manifest entry stop the step."""
    if member not in manifest:
        raise SystemExit(f"package member {member} is not listed in {MANIFEST_MEMBER}; a member that the manifest does not "
                         "vouch for is never read")
    try:
        content = archive.read(f"{root}/{member}")
    except KeyError:
        raise SystemExit(f"package member {member} is listed in {MANIFEST_MEMBER} but missing from the zip") from None
    actual = hashlib.sha256(content).hexdigest()
    if actual != manifest[member]:
        raise SystemExit(f"package member {member} has the SHA-256 {actual} but {MANIFEST_MEMBER} states {manifest[member]}: "
                         "a changed member is never read")
    return content


def _json_member(archive, manifest, member: str, root: str = SUPPLEMENT_NAME):
    return json.loads(_read_member(archive, manifest, member, root).decode("utf-8"))


def read_text(package: dict, member: str) -> str:
    """The text of a member (an evidence page or text) of a package that the zip's manifest vouches for."""
    with zipfile.ZipFile(package["path"]) as archive:
        return _read_member(archive, package["manifest"], member, package.get("root", SUPPLEMENT_NAME)).decode("utf-8")


def load_supplement(path, expected_sha256: Optional[str] = None) -> dict:
    """The verified supplement package at ``path`` (the zip itself, e.g. next to the regional package).

    The zip must exist with exactly ``SUPPLEMENT_SHA256`` (or ``expected_sha256``, for a synthetic test package), else
    ``SystemExit``; every member read must match its entry of ``manifest.sha256``. Returns {"path", "manifest", "file" ({"file",
    "sha256", "bytes"}), "entrances" (GeoDataFrame in EPSG:25832 indexed by ``point_id``: the main points only, role primary,
    one per facility), "facilities" (facility_id -> record of ``facility_updates.json``), "rules" (rule_id -> rule of
    ``tariff_rules.json``, in the supplement's own schema), "decisions" (decision_id -> record of ``field_decisions.json``),
    "observations", "sources" (source_id -> record)}."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's supplement package {SUPPLEMENT_FILE} (--supplement-zip) unchanged")
    sha256 = expected_sha256 or SUPPLEMENT_SHA256
    actual = mz.file_sha256(path)
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_garages_2026); a changed "
                         "package is never read")
    with zipfile.ZipFile(path) as archive:
        manifest = _manifest(archive)
        facilities = {record["facility_id"]: record for record in _json_member(archive, manifest, FACILITY_UPDATES_MEMBER)["facilities"]}
        rules = {rule["rule_id"]: rule for rule in _json_member(archive, manifest, TARIFF_RULES_MEMBER)["rules"]}
        decisions = {record["decision_id"]: record for record in _json_member(archive, manifest, FIELD_DECISIONS_MEMBER)["decisions"]}
        observations = _json_member(archive, manifest, OBSERVATIONS_MEMBER)
        sources = {source["source_id"]: source for source in _json_member(archive, manifest, SOURCES_MEMBER)["sources"]}
        _read_member(archive, manifest, PATCH_MEMBER)  # the GeoPackage is read by GDAL from the zip: verify its bytes first
        geojson = _json_member(archive, manifest, ENTRANCES_GEOJSON_MEMBER)
    supplement = {"path": path, "manifest": manifest,
                  "file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size}, "facilities": facilities,
                  "rules": rules, "decisions": decisions, "observations": observations, "sources": sources}
    supplement["entrances"] = _read_entrances(path, geojson, facilities)
    print(f"supplement inputs verified: {path.name} ({supplement['file']['bytes']} bytes, SHA-256 {actual}); {len(manifest)} "
          f"members in its manifest, {len(facilities)} facility updates, {len(rules)} tariff rules, {len(decisions)} field "
          f"decisions, {len(supplement['entrances'])} main points (the reference points are never read)")
    return supplement


def _read_entrances(path: Path, geojson: dict, facilities: dict) -> gpd.GeoDataFrame:
    """The main points of layer ``entrances`` (EPSG:25832): role primary, one per facility, valid points, no tariff extent;
    cross-checked against the GeoJSON points (EPSG:4326) of the same package."""
    frame = gpd.read_file(f"/vsizip/{path.resolve().as_posix()}/{SUPPLEMENT_NAME}/{PATCH_MEMBER}", layer=ENTRANCES_LAYER)
    if frame.crs is None or frame.crs.to_epsg() != 25832:
        raise SystemExit(f"{path.name} layer {ENTRANCES_LAYER}: CRS {frame.crs} is not EPSG:25832, as the package states")
    if frame.empty or not frame.geometry.is_valid.all() or frame.geometry.is_empty.any():
        raise SystemExit(f"{path.name} layer {ENTRANCES_LAYER}: empty, or invalid or empty geometries; the step repairs nothing")
    seen = {}
    for _, row in frame.iterrows():
        if row["role"] != "primary":
            raise SystemExit(f"entrances layer: point {row['point_id']} has the role {row['role']!r}; only main points (role "
                             "primary) belong to it, the alternatives are reference points and are never counted as a garage")
        if row.geometry.geom_type != "Point":
            raise SystemExit(f"entrances layer: point {row['point_id']} is a {row.geometry.geom_type}; a position stays a point "
                             "(no ring, no buffer)")
        if bool(row["geometry_is_tariff_extent"]):
            raise SystemExit(f"entrances layer: point {row['point_id']} states geometry_is_tariff_extent; a position is no "
                             "tariff area")
        if row["facility_id"] in seen:
            raise SystemExit(f"entrances layer: facility {row['facility_id']} has a second main point {row['point_id']} (the "
                             f"first is {seen[row['facility_id']]}); one main point per facility")
        seen[row["facility_id"]] = row["point_id"]
        record = facilities.get(row["facility_id"])
        if record is None or record.get("primary_point_id") != row["point_id"]:
            raise SystemExit(f"entrances layer: point {row['point_id']} of facility {row['facility_id']} is not the "
                             "primary_point_id of that facility in facility_updates.json")
    if len(set(frame["point_id"])) != len(frame):
        raise SystemExit("entrances layer: a point_id occurs twice")
    # the GeoJSON states the same points in WGS84 (longitude, latitude): compare in metres
    points = {feature["properties"]["point_id"]: Point(feature["geometry"]["coordinates"][:2]) for feature in geojson["features"]}
    projected = gpd.GeoSeries(list(points.values()), index=list(points), crs="EPSG:4326").to_crs(cc.METRIC_CRS)
    indexed = frame.set_index("point_id")
    if set(projected.index) != set(indexed.index):
        raise SystemExit(f"{ENTRANCES_GEOJSON_MEMBER} holds the points {sorted(projected.index)} but the GeoPackage layer "
                         f"{ENTRANCES_LAYER} the points {sorted(indexed.index)}")
    for point_id, geometry in projected.items():
        distance = float(geometry.distance(indexed.loc[point_id, "geometry"]))
        if distance > COORDINATE_TOLERANCE_M:
            raise SystemExit(f"{ENTRANCES_GEOJSON_MEMBER} point {point_id} differs from the GeoPackage point by {distance:.3f} m "
                             f"(more than {COORDINATE_TOLERANCE_M} m, the rounding of the coordinates)")
    return indexed


def entrance(supplement: dict, point_id: str) -> dict:
    """The main point ``point_id`` as the dataset row needs it: {"point_id", "facility_id", "geometry" (EPSG:25832),
    "name" (ASCII), "method" (the geometry method naming the entrance kind), "description" (the position in words for the
    notes), "url" (the OSM node), "capacity", "capacity_scope"}. ``SystemExit`` for a point that is no main point."""
    entrances = supplement["entrances"]
    if point_id not in entrances.index:
        raise SystemExit(f"the supplement has no main point {point_id} (its main points: {sorted(entrances.index)})")
    row = entrances.loc[point_id]
    method = ENTRANCE_METHODS.get(str(row["point_type"]))
    if method is None:
        raise SystemExit(f"main point {point_id}: point_type {row['point_type']!r} is none of {sorted(ENTRANCE_METHODS)}")
    node = f"node {row['osm_id']} version {row['osm_version']} of {str(row['osm_timestamp'])[:10]}"
    if row["point_type"] == "mapped_parking_entrance":
        what = f"OSM-mapped entrance ({node})"
    else:
        what = (f"derived access point (the connection of the driveway to the street at OSM {node}; no confirmed barrier or "
                "portal point)")
    description = (f"{what}; the supplement states no accuracy; not field verified; a point, no buffer and no tariff extent "
                   "(the alternative points of the supplement are not used)")
    capacity = row["capacity"]
    return {"point_id": point_id, "facility_id": str(row["facility_id"]), "geometry": row["geometry"],
            "name": cc.ascii_transliteration(str(row["name"])), "method": method, "description": description,
            "url": str(row["source_url"]), "capacity": None if capacity is None or capacity != capacity else int(capacity),
            "capacity_scope": None if row["capacity_scope"] is None or row["capacity_scope"] != row["capacity_scope"]
            else str(row["capacity_scope"])}


# ---------------------------------------------------------------- identity
def identity_map(supplement: dict, regional_facilities: dict) -> dict:
    """{supplement facility id: regional facility id, or None for a facility the regional package does not hold}.

    A supplement facility is matched by its own ``facility_id`` or by a verified ``legacy_id`` (a facility of the regional
    package). ``SystemExit`` for the ambiguous legacy key ``BS_None``, a legacy id that the regional package does not hold, a
    forbidden join (``FORBIDDEN_JOINS``) and a supplement facility that matches two regional facilities."""
    mapping = {}
    for facility_id, record in supplement["facilities"].items():
        legacy = list(record.get("legacy_ids") or [])
        if AMBIGUOUS_LEGACY_ID in legacy or facility_id == AMBIGUOUS_LEGACY_ID:
            raise SystemExit(f"supplement facility {facility_id}: the legacy key {AMBIGUOUS_LEGACY_ID} is ambiguous in the "
                             "regional package (it mixes the Forschungsflughafen and the Ring-Center) and is never an identity")
        missing = [key for key in legacy if key not in regional_facilities]
        if missing:
            raise SystemExit(f"supplement facility {facility_id}: the legacy id(s) {missing} are no facility of the regional "
                             "package; a legacy id is verified against it")
        matches = []
        for key in [facility_id] + legacy:
            if key in regional_facilities and key not in matches:
                matches.append(key)
        for key in matches:
            why = FORBIDDEN_JOINS.get(facility_id, {}).get(key)
            if why:
                raise SystemExit(f"supplement facility {facility_id} must not be joined to {key}: {why}")
        if len(matches) > 1:
            raise SystemExit(f"supplement facility {facility_id} matches two facilities of the regional package {matches}; "
                             "the identity is not unique")
        mapping[facility_id] = matches[0] if matches else None
    return mapping


# ---------------------------------------------------------------- the rules of the supplement in the regional schema
def _source(supplement: dict, source_id: str) -> dict:
    if source_id not in supplement["sources"]:
        raise SystemExit(f"the supplement's sources.json has no source {source_id}")
    return supplement["sources"][source_id]


def _charging_times(fee_times) -> dict:
    """The regional ``charging_times`` of the supplement's ``fee_times_by_weekday`` (ISO weekday 1 to 7); a time that ends the
    next day crosses midnight."""
    times = {day: None for day in WEEKDAY_NAMES + ("public_holidays",)}
    for entry in fee_times or []:
        day = WEEKDAY_NAMES[int(entry["iso_weekday"]) - 1]
        times[day] = (times[day] or []) + [{"start": entry["start"], "end": entry["end"],
                                            "crosses_midnight": bool(entry.get("ends_next_day"))}]
    return times


def _regional_rule(supplement: dict, rule_id: str, facility: str, rule_type: str, *, source_ids, retrieved_on: str, status,
                   **fields) -> dict:
    """One rule of the regional schema with the fields the garage encoding reads; ``preferred_for_current_use`` is False until a
    decision releases it."""
    if not source_ids:
        raise SystemExit(f"supplement rule {rule_id} names no source")
    rule = {"rule_id": rule_id, "facility_id": facility, "supplement_facility": facility, "rule_type": rule_type,
            "amount_eur": None, "billing_unit_minutes": None, "rounding": None, "elapsed_from_minutes": None,
            "elapsed_to_minutes": None, "max_stay_minutes": None, "daily_cap_eur": None, "cap_period": None,
            "charging_times": {day: None for day in WEEKDAY_NAMES + ("public_holidays",)}, "monthly_price_eur": None,
            "minimum_contract_months": None, "conditions": None, "source_url": _source(supplement, source_ids[0])["url"],
            "retrieved_at": retrieved_on, "status": status, "preferred_for_current_use": False,
            "time_window": {"from": None, "to": None, "days_raw": None}, "raw_rule": {}, "origin": "supplement",
            "source_ids": list(source_ids), "free_period_minutes": None}
    rule.update(fields)
    return rule


def _rounding(rule_id: str, word) -> Optional[str]:
    if word not in ROUNDING_WORDS:
        raise SystemExit(f"supplement rule {rule_id}: rounding {word!r} is neither unstated nor 'ceil' (a started unit); a "
                         "rounding the encoding cannot express is never read")
    return ROUNDING_WORDS[word]


def convert_rules(supplement: dict) -> dict:
    """{rule id: rule} of the supplement's tariff rules (and the cap variants of its observations) in the regional rule schema.

    All rules are returned NOT preferred (``release_rules`` releases the ones an owner decision allows). Per rule: the rate (an
    ``increment`` rule with its amount, unit and rounding: the supplement's ``ceil`` is ``started_unit``), a ``<id>:cap`` rule
    for a stated daily cap, one ``<id>:stage<n>`` rule per stage of a rate that states stages (the parent is then no rate of its
    own), the charging times of a night window, a monthly product, a seven-day product; a rule without an amount is
    ``unresolved``. Every ``daily_cap_amount`` of a reported variant of the observations is a cap rule named by its variant id."""
    rules = {}
    for rule_id, rule in supplement["rules"].items():
        scope = rule.get("scope", "regular")
        if scope not in SCOPE_RULE_TYPES:
            raise SystemExit(f"supplement rule {rule_id}: scope {scope!r} is none of {sorted(SCOPE_RULE_TYPES)}")
        facility = rule["facility_id"]
        common = {"source_ids": rule["source_ids"], "retrieved_on": rule["retrieved_on"], "status": rule.get("status")}
        if scope == "monthly_product":
            product_name = f"monthly product {rule_id}"
            for entry in supplement["observations"].get("operator_and_secondary_variants", []):
                for product in entry.get("published_long_term_products", []) if entry.get("facility_id") == facility else []:
                    if product.get("amount") == rule["monthly_eur"]:
                        product_name = product["name"]
            rules[rule_id] = _regional_rule(
                supplement, rule_id, facility, "monthly_product", monthly_price_eur=rule["monthly_eur"],
                raw_rule={"product_name": product_name},
                conditions=f"eligibility {rule.get('eligibility')}; status {rule.get('status')}", **common)
            continue
        rounding = _rounding(rule_id, rule.get("rounding"))
        if rule.get("stages"):
            for number, stage in enumerate(rule["stages"], start=1):
                rules[f"{rule_id}:stage{number}"] = _regional_rule(
                    supplement, f"{rule_id}:stage{number}", facility, "increment", amount_eur=stage["price_eur"],
                    billing_unit_minutes=stage["billing_unit_minutes"], rounding=rounding,
                    elapsed_from_minutes=stage["start_minute"], elapsed_to_minutes=stage["end_minute"], **common)
        elif rule.get("price_eur") is None:
            rules[rule_id] = _regional_rule(supplement, rule_id, facility, "unresolved", **common)
        else:
            rules[rule_id] = _regional_rule(
                supplement, rule_id, facility, SCOPE_RULE_TYPES[scope], amount_eur=rule["price_eur"],
                billing_unit_minutes=rule.get("billing_unit_minutes"), rounding=rounding,
                charging_times=_charging_times(rule.get("fee_times_by_weekday")),
                free_period_minutes=rule.get("free_period_minutes"), **common)
        if rule.get("daily_cap_eur") is not None:
            rules[f"{rule_id}:cap"] = _regional_rule(
                supplement, f"{rule_id}:cap", facility, "daily_cap", amount_eur=rule["daily_cap_eur"],
                daily_cap_eur=rule["daily_cap_eur"], cap_period=rule.get("cap_period"), **common)
    observations = supplement["observations"]
    for entry in observations.get("operator_and_secondary_variants", []):
        for variant in entry.get("reported_variants", []):
            if variant.get("daily_cap_amount") is None:
                continue
            variant_id = variant["variant_id"]
            if variant_id in rules or variant_id in supplement["rules"]:
                raise SystemExit(f"cap variant {variant_id} has the id of a rule")
            rules[variant_id] = _regional_rule(
                supplement, variant_id, entry["facility_id"], "daily_cap", amount_eur=variant["daily_cap_amount"],
                daily_cap_eur=variant["daily_cap_amount"], cap_period=variant.get("daily_cap_clock_basis"),
                source_ids=variant["source_ids"], retrieved_on=str(observations["research_date"])[:10],
                status=f"variant_{variant.get('source_class')}")
    return rules


def release_rules(rules: dict, decisions: dict, released: dict) -> None:
    """Mark the rules that an owner decision releases ``preferred_for_current_use`` (in place).

    ``released`` is {rule id: (the ruling of the owner decision, {field decision id: the statuses that decision may have})}.
    The rule must exist and every field decision must exist with one of the statuses the ruling relied on, else ``SystemExit``:
    a decision that the package still holds open never releases a value. The released rule records ``released_by`` (the ruling
    and the field decisions with their statuses); every rule that is not released stays unpreferred and sets no value."""
    for rule_id, (ruling, needed) in released.items():
        if rule_id not in rules:
            raise SystemExit(f"the release of a rule names no rule {rule_id} (the supplement's rules: {sorted(rules)})")
        parts = []
        for decision_id, statuses in needed.items():
            if decision_id not in decisions:
                raise SystemExit(f"rule {rule_id} is released by the ruling {ruling}, but the supplement has no field decision "
                                 f"{decision_id}")
            status = decisions[decision_id]["status"]
            if status not in statuses:
                raise SystemExit(f"rule {rule_id} is released by the ruling {ruling}, but field decision {decision_id} has the "
                                 f"status {status!r}, not one of {list(statuses)} that the ruling relied on")
            parts.append(f"field decision {decision_id}: {status}")
        rules[rule_id]["preferred_for_current_use"] = True
        rules[rule_id]["released_by"] = f"{ruling} ({'; '.join(parts)})"


# ---------------------------------------------------------------- the brochure tariff
_AMOUNT = re.compile(r"(\d+),(\d{2})\s*€")
_UNIT = re.compile(r"/\s*(\d+)\s*(Std\.|Min\.)")


def _amount_eur(quotation: str) -> float:
    match = _AMOUNT.search(quotation)
    if match is None:
        raise SystemExit(f"the quotation {quotation!r} states no amount in EUR (a number with a decimal comma and the euro sign)")
    return float(f"{match.group(1)}.{match.group(2)}")


def _unit_minutes(quotation: str) -> Optional[int]:
    match = _UNIT.search(quotation)
    if match is None:
        return None
    return int(match.group(1)) * (60 if match.group(2) == "Std." else 1)


def brochure_rules(supplement: dict, brochure_specs) -> dict:
    """The rules of a tariff that only a brochure text states (``{rule id: rule}``, regional schema, already preferred: the
    specification's quotations are the evidence).

    A brochure specification names the ``facility`` (a supplement facility), the text ``member`` of the supplement, the
    ``source_id`` of the brochure, the ``anchor`` (the row of the garage), the quotations ``first`` (the first price with its
    unit) and ``further`` (the price of each further unit), the parts of the ``window`` quotation and the ``unit_reading`` (the
    reading that applies where the brochure states no unit for ``further``). The amounts and the unit are PARSED from the
    quotations, never typed beside them. The anchor must be exactly one row of the NFKC-normalised text, every quotation must be
    in that row (the line of the anchor with the line before and the two after it: the table's columns break across lines) and
    the capacity of the facility must be in the anchor line, else ``SystemExit``. Rules: ``<facility>_BROCHURE_FIRST`` (a total
    for the first unit) and ``<facility>_BROCHURE_NEXT`` (an increment per unit from the first unit's end, no stated rounding)."""
    rules = {}
    for spec in brochure_specs:
        facility = spec["facility"]
        record = supplement["facilities"].get(facility)
        if record is None:
            raise SystemExit(f"the brochure specification names the facility {facility}, which the supplement does not hold")
        text = _norm(read_text(supplement, spec["member"]))
        first_eur, further_eur = _amount_eur(spec["first"]), _amount_eur(spec["further"])
        unit = _unit_minutes(spec["first"])
        if unit is None:
            raise SystemExit(f"the quotation {spec['first']!r} of the first price states no unit ('/ 1 Std.' or '/ 30 Min.')")
        lines = text.splitlines()
        anchor = _norm(spec["anchor"])
        hits = [index for index, line in enumerate(lines) if anchor in line]
        if len(hits) != 1:
            raise SystemExit(f"brochure {spec['member']}: the anchor {spec['anchor']!r} is not a row of the text (found in "
                             f"{len(hits)} lines)")
        row = "\n".join(lines[max(hits[0] - 1, 0):hits[0] + 3])
        for quotation in (spec["first"], spec["further"]) + tuple(spec["window"]):
            if _norm(quotation) not in text:
                raise SystemExit(f"quotation {quotation!r} is not in the brochure text {spec['member']}")
            if _norm(quotation) not in row:
                raise SystemExit(f"quotation {quotation!r} is in the brochure text but not in the brochure row of "
                                 f"{spec['anchor']!r}")
        capacity = record.get("capacity")
        if capacity is None or not re.search(rf"(?<!\d){int(capacity)}(?!\d)", lines[hits[0]]):
            raise SystemExit(f"the capacity {capacity} is not in the brochure row of {spec['anchor']!r}")
        source = _source(supplement, spec["source_id"])
        common = {"facility_id": facility, "supplement_facility": facility, "source_url": source["url"],
                  "retrieved_at": str(source["retrieved_at"])[:10], "status": "municipal_brochure_quoted",
                  "preferred_for_current_use": True, "origin": "brochure",
                  "released_by": f"quotations of the brochure text {spec['member']} (SHA-256 verified)"}
        first_id, next_id = f"{facility}_BROCHURE_FIRST", f"{facility}_BROCHURE_NEXT"
        base = _regional_rule(supplement, first_id, facility, "duration_total", source_ids=[spec["source_id"]],
                              retrieved_on=common["retrieved_at"], status=common["status"], amount_eur=first_eur,
                              elapsed_from_minutes=0, elapsed_to_minutes=unit)
        base.update(common)
        following = _regional_rule(supplement, next_id, facility, "increment", source_ids=[spec["source_id"]],
                                   retrieved_on=common["retrieved_at"], status=common["status"], amount_eur=further_eur,
                                   billing_unit_minutes=unit, elapsed_from_minutes=unit)
        following.update(common)
        following["unit_reading"] = spec["unit_reading"]
        rules[first_id], rules[next_id] = base, following
    return rules


def load_followup(path, expected_sha256: Optional[str] = None) -> dict:
    """The verified follow-up package at ``path`` (spec E13), read like the supplement: pinned SHA-256, every member against
    its manifest, nothing executed. Returns {"path", "manifest", "root", "file", "facilities" (facility_id -> record of
    ``facility_review.json``), "rules" (the package's partial records), "decisions", "observations" ({} : the follow-up has no
    cap variants), "observation_table" (observation id -> observation, with its ``facility_id``), "sources", "actions"
    (facility id -> the recommendation of ``model_actions.json``), "point" (the archived point of Charley-Jacob-Strasse:
    {"geometry" (EPSG:25832), "properties"})}."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's follow-up package {FOLLOWUP_FILE} (--followup-zip) unchanged")
    sha256 = expected_sha256 or FOLLOWUP_SHA256
    actual = mz.file_sha256(path)
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_garages_2026); a changed "
                         "package is never read")
    with zipfile.ZipFile(path) as archive:
        manifest = _manifest(archive, FOLLOWUP_NAME)

        def member(name):
            return _json_member(archive, manifest, name, FOLLOWUP_NAME)

        review = member(FOLLOWUP_REVIEW_MEMBER)
        rules = {rule["rule_id"]: rule for rule in member(TARIFF_RULES_MEMBER)["rules"]}
        decisions = {record["decision_id"]: record for record in member(FIELD_DECISIONS_MEMBER)["decisions"]}
        sources = {source["source_id"]: source for source in member(SOURCES_MEMBER)["sources"]}
        actions = {action["facility_id"]: action for action in member(FOLLOWUP_ACTIONS_MEMBER)["actions"]}
        geojson = member(FOLLOWUP_POINT_MEMBER)
    facilities = {record["facility_id"]: record for record in review["facilities"]}
    table = {}
    for facility_id, record in facilities.items():
        for observation in record.get("observations", []):
            if observation["observation_id"] in table:
                raise SystemExit(f"follow-up observation {observation['observation_id']} occurs twice")
            table[observation["observation_id"]] = {**observation, "facility_id": facility_id,
                                                    "research_date": review["research_date"]}
    features = geojson["features"]
    if len(features) != 1:
        raise SystemExit(f"{FOLLOWUP_POINT_MEMBER} holds {len(features)} points, expected the one archived point")
    coordinates = features[0]["geometry"]["coordinates"][:2]
    point = gpd.GeoSeries([Point(coordinates)], crs="EPSG:4326").to_crs(cc.METRIC_CRS).iloc[0]
    followup = {"path": path, "manifest": manifest, "root": FOLLOWUP_NAME,
                "file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size}, "facilities": facilities,
                "rules": rules, "decisions": decisions, "observations": {}, "observation_table": table, "sources": sources,
                "actions": actions, "point": {"geometry": point, "properties": features[0]["properties"]}}
    print(f"follow-up inputs verified: {path.name} ({followup['file']['bytes']} bytes, SHA-256 {actual}); {len(manifest)} members "
          f"in its manifest, {len(facilities)} facility reviews, {len(table)} observations, {len(rules)} partial tariff rules, "
          f"{len(decisions)} field decisions")
    return followup


def observation_rules(followup: dict) -> dict:
    """The observations of the follow-up package that state a tariff, as rules of the regional schema (NOT preferred until
    ``release_rules`` releases them; the package marks none of them as the current operator tariff).

    * an observation with ``price_points`` (a directory's table of the price at given durations) becomes a ``price_table`` rule
      with its points and its ``reported_further_hour_eur``, and a ``<id>:cap`` rule holding the price of the 1440 min point
      (the 24-hour price), with no stated cap period;
    * an observation with a ``price_eur`` per ``billing_unit_minutes`` becomes an ``increment`` rule (its reported capacity in
      ``capacity_reported``);
    * an observation with a ``daily_cap_eur`` only becomes a ``daily_cap`` rule.
    Every other observation (a hotel guest price, an event, a hours mismatch) is evidence for the notes and no rule."""
    rules = {}
    for observation_id, observation in followup["observation_table"].items():
        accepted = observation.get("accepted_as_current_operator_tariff", observation.get("accepted_as_current_tariff"))
        common = {"source_ids": observation["source_ids"], "retrieved_on": str(observation["research_date"])[:10],
                  "status": f"observation_accepted_as_current_tariff_{accepted}"}
        facility = observation["facility_id"]
        if observation.get("price_points"):
            points = sorted(observation["price_points"], key=lambda point: point["minutes"])
            if points[-1]["minutes"] != 1440:
                raise SystemExit(f"observation {observation_id}: the last price point is at {points[-1]['minutes']} min, not at "
                                 "the 24-hour price of 1440 min")
            rules[observation_id] = _regional_rule(
                followup, observation_id, facility, "price_table", price_points=points,
                reported_further_hour_eur=observation.get("reported_further_hour_eur"), **common)
            rules[f"{observation_id}:cap"] = _regional_rule(
                followup, f"{observation_id}:cap", facility, "daily_cap", amount_eur=points[-1]["eur"],
                daily_cap_eur=points[-1]["eur"], **common)
        elif observation.get("price_eur") is not None and observation.get("billing_unit_minutes"):
            rules[observation_id] = _regional_rule(
                followup, observation_id, facility, "increment", amount_eur=observation["price_eur"],
                billing_unit_minutes=observation["billing_unit_minutes"], rounding=_rounding(observation_id, observation.get("rounding")),
                capacity_reported=observation.get("capacity_reported"), **common)
        elif observation.get("daily_cap_eur") is not None:
            rules[observation_id] = _regional_rule(
                followup, observation_id, facility, "daily_cap", amount_eur=observation["daily_cap_eur"],
                daily_cap_eur=observation["daily_cap_eur"], cap_period=observation.get("cap_period"), **common)
    return rules


def attach_followup(inputs: dict, followup: dict, released: dict) -> None:
    """Merge the follow-up package into the garage inputs (in place), after :func:`attach`: its partial records and the tariff
    observations become rules (released ones preferred), join the facility they belong to (every follow-up facility must be a
    facility of the regional package, by its id or a verified legacy id) and ``inputs["followup"]`` carries the package."""
    rules = convert_rules(followup)
    for rule_id, rule in observation_rules(followup).items():
        if rule_id in rules:
            raise SystemExit(f"follow-up observation {rule_id} has the id of a follow-up rule")
        rules[rule_id] = rule
    release_rules(rules, followup["decisions"], released)
    clashes = sorted(set(rules) & set(inputs["rules"]))
    if clashes:
        raise SystemExit(f"follow-up rule id(s) {clashes} clash with rules of the other packages")
    identity = identity_map(followup, inputs["facilities"])
    for rule_id, rule in rules.items():
        rule["origin"] = "followup"
        facility = identity.get(rule["supplement_facility"])
        if facility is None:
            raise SystemExit(f"follow-up rule {rule_id}: the facility {rule['supplement_facility']} is no facility of the "
                             "regional package")
        inputs["facilities"][facility]["tariff_rule_ids"] = list(inputs["facilities"][facility]["tariff_rule_ids"]) + [rule_id]
    inputs["rules"].update(rules)
    inputs["followup"], inputs["followup_identity"] = followup, identity


def operator_from_page(supplement: dict, facility: str, operator: str, member: str, quotation: str, source_id: str) -> dict:
    """{"operator", "url", "quotation"}: ``operator`` is taken only where the supplement's facility record states it AND the
    supplement's copy of the operator's page (``member``) holds the quotation; ``SystemExit`` otherwise."""
    record = supplement["facilities"].get(facility)
    if record is None or record.get("operator") != operator:
        raise SystemExit(f"supplement facility {facility} states the operator {None if record is None else record.get('operator')!r}, "
                         f"not {operator!r}")
    if _norm(quotation) not in _norm(read_text(supplement, member)):
        raise SystemExit(f"the quotation {quotation!r} of the operator is not in the page {member}")
    return {"operator": operator, "url": _source(supplement, source_id)["url"], "quotation": quotation}


def attach(inputs: dict, supplement: dict, released: dict, brochures) -> None:
    """Merge the supplement into the garage inputs of the regional package (in place): the converted and released rules and the
    brochure rules join ``inputs["rules"]`` (an id that clashes with a regional rule stops the step), the supplement's facilities
    are matched to the regional facilities by their identity (their rule ids join the facility's ``tariff_rule_ids``; a
    supplement facility the regional package does not hold becomes a facility record of its own), and ``inputs["supplement"]``
    and ``inputs["identity"]`` carry the package and the map."""
    rules = convert_rules(supplement)
    for rule_id, rule in brochure_rules(supplement, brochures).items():
        if rule_id in rules:
            raise SystemExit(f"brochure rule {rule_id} has the id of a supplement rule")
        rules[rule_id] = rule
    release_rules(rules, supplement["decisions"], released)
    clashes = sorted(set(rules) & set(inputs["rules"]))
    if clashes:
        raise SystemExit(f"supplement rule id(s) {clashes} clash with rules of the regional package")
    identity = identity_map(supplement, inputs["facilities"])
    by_facility = {}
    for rule_id, rule in rules.items():
        if rule["supplement_facility"] not in supplement["facilities"]:
            raise SystemExit(f"supplement rule {rule_id} names the facility {rule['supplement_facility']}, which "
                             "facility_updates.json does not hold")
        by_facility.setdefault(rule["supplement_facility"], []).append(rule_id)
    for facility_id, record in supplement["facilities"].items():
        target = identity[facility_id]
        ids = by_facility.get(facility_id, [])
        if target is None:
            inputs["facilities"][facility_id] = {
                "facility_id": facility_id, "name": record["name"], "city": record.get("city"), "capacity": None,
                "capacity_scope": None, "capacity_observations": [], "attributes": {}, "geometry_refs": [],
                "tariff_rule_ids": list(ids), "supplement_facility": facility_id}
        else:
            facility = inputs["facilities"][target]
            facility["tariff_rule_ids"] = list(facility.get("tariff_rule_ids") or []) + ids
            facility["supplement_facility"] = facility_id
    inputs["rules"].update(rules)
    inputs["supplement"], inputs["identity"] = supplement, identity
