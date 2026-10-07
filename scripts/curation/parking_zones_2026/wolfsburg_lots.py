"""Wolfsburg car-park package of the garage dataset (parking cost zones v2, spec Amendment E14, issue #436, Task 4b3).

Not part of the pipeline and never run by synpp: the fourth, optional input of ``regional_garages.py``
(``--wolfsburg-lots-zip``). The owner-supplied ``Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip`` is a review of the 24 car
parks of the city layer ``wob_parkplaetze`` (the points of the Geoviewer theme "Parken"): per point the fee status the
research found, the documented tariff components, the observed group rules that are not assigned to the point and the open
questions. It marks every record ``full_cost_calculation_ready=false``. This module reads it the way the supplement packages are
read (``garage_supplement``):

* ``load_lots`` checks the zip against the SHA-256 pinned in ``LOTS_SHA256`` before anything is read and every member that is
  read against the zip's own ``manifest.sha256``; nothing is extracted and nothing in the zip (it carries scripts) is ever
  executed. The points come from the GeoPackage layer ``parking_lots`` (EPSG:25832) and are cross-checked against
  ``parking_lots.geojson`` (EPSG:4326) to 1 cm; the layer holds the city's points unchanged, they are no entrances and no
  tariff extents, so a position stays a point.
* ``classify`` decides, from the fee status of the package and the geometry (the point in the committed zone polygons of
  EPSG:25832 and in the published tariff areas of the city, never from a name), which of the five classes of spec E14 a point
  belongs to where the data alone decide it (a paid municipal car park inside a zone is class a; a point without any fee
  evidence outside every zone and every published tariff area is class d); the other classes need the owner decision of
  ``regional_garage_specs.LOT_SPECS``, which ``check_decision`` compares with the data (the fee status the class allows).
* ``convert_rules`` writes the package's tariff components in the regional rule schema (so the garage encoding reads one
  schema), all of them NOT preferred; ``release_rules`` of ``garage_supplement`` marks a rule preferred exactly where an owner
  decision releases it and the package's own field decision still has the status the decision relied on.

CRS: EPSG:25832 throughout, money in EUR, distances in m.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import Point

import curation_common as cc
import garage_supplement as sup
import municipal_zones as mz

LOTS_NAME = "Wolfsburg_Parkplaetze_Pruefung_2026-10-07"
LOTS_FILE = f"{LOTS_NAME}.zip"
#: SHA-256 of the owner's Wolfsburg car-park package (also in the data record parking_garages_2026 and in MANIFEST.md).
LOTS_SHA256 = "650508f87c80ee2b84063def301ff67b5ecb8f2653cef2ab4202fb398c818df0"
POINTS_GEOJSON_MEMBER = "data/parking_lots.geojson"
POINTS_GPKG_MEMBER = "data/parking_lots.gpkg"
#: The one layer of the GeoPackage that is read: the 24 points. The other layers repeat the JSON tables.
POINTS_LAYER = "parking_lots"
REVIEW_MEMBER = "data/facility_review.json"
RULES_MEMBER = "data/tariff_rules.json"
DECISIONS_MEMBER = "data/field_decisions.json"
SOURCES_MEMBER = "data/sources.json"
SUMMARY_MEMBER = "data/summary.json"
MATCHES_MEMBER = "data/point_area_matches.json"
#: The published tariff areas that contain a point of the layer (9 of the 73 areas of the city layer) and, as evidence, the
#: complete layer of the 73 areas that the package keeps: a point without fee evidence must lie in none of them.
MATCHED_AREAS_MEMBER = "data/published_tariff_areas.geojson"
ALL_AREAS_MEMBER = "evidence/input/daten/geojson/wob_handyparkflaechen.geojson"
#: The GeoJSON points and the GeoPackage points are the same points in two CRS (see ``garage_supplement``).
COORDINATE_TOLERANCE_M = sup.COORDINATE_TOLERANCE_M
FEE_STATUSES = ("paid_confirmed", "free_confirmed", "conditional", "mixed", "unknown")
#: The five classes of spec E14 and the fee statuses of the package that each allows (an owner decision for a class whose fee
#: status is not listed stops the step): a paid municipal car park in a zone, an operator tariff, a car park free for the
#: public, a public car park without any fee evidence (the municipal free default), a car park that is no public option.
CLASS_FEE_STATUSES = {"a": ("paid_confirmed",), "b": ("conditional",), "c": ("free_confirmed", "conditional"),
                      "d": ("unknown",), "e": ("conditional", "mixed")}
#: The package's rule types and the regional rule type each becomes (``convert_rules``); a rule without a type that states the
#: amount 0 is a free rule.
RULE_TYPES = {"published_hourly_rate": "increment", "published_half_hour_rate": "increment",
              "zero_price_short_stay": "free_period", "conditional_zero_price": "conditional_free"}
WEEKDAY_KEYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# ---------------------------------------------------------------- the package
def load_lots(path, expected_sha256: Optional[str] = None) -> dict:
    """The verified Wolfsburg car-park package at ``path`` (the zip itself, e.g. next to the regional package).

    The zip must exist with exactly ``LOTS_SHA256`` (or ``expected_sha256``, for a synthetic test package), else
    ``SystemExit``; every member read must match its entry of ``manifest.sha256``. Returns {"path", "manifest", "root", "file"
    ({"file", "sha256", "bytes"}), "points" (GeoDataFrame in EPSG:25832 indexed by ``facility_id``: valid points, one per
    facility), "review" (facility_id -> record of ``facility_review.json``, the same ids as the points), "rules" (rule_id ->
    rule of ``tariff_rules.json``: the assigned components and the observed group rules, the latter with ``assigned`` False),
    "decisions" (decision_id -> record of ``field_decisions.json``), "sources" (source_id -> record), "matches" (facility_id ->
    the published tariff areas that contain the point), "matched_areas" and "all_areas" (GeoDataFrames in EPSG:25832
    indexed by ``facility_id`` of the area), "summary"}. ``SystemExit`` for a layer that is empty, not EPSG:25832, with an
    invalid or non-point geometry, a facility twice, a point of the GeoJSON that differs from the layer by more than
    ``COORDINATE_TOLERANCE_M``, a review that does not list exactly the points and a fee status that is none of
    ``FEE_STATUSES`` or that the points and the review state differently."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's Wolfsburg car-park package {LOTS_FILE} (--wolfsburg-lots-zip) "
                         "unchanged")
    sha256 = expected_sha256 or LOTS_SHA256
    actual = mz.file_sha256(path)
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_garages_2026); a changed "
                         "package is never read")
    with zipfile.ZipFile(path) as archive:
        manifest = sup._manifest(archive, LOTS_NAME)

        def member(name):
            return sup._json_member(archive, manifest, name, LOTS_NAME)

        review = {record["facility_id"]: record for record in member(REVIEW_MEMBER)["facilities"]}
        rule_document = member(RULES_MEMBER)
        decisions = {record["decision_id"]: record for record in member(DECISIONS_MEMBER)["decisions"]}
        sources = {source["source_id"]: source for source in member(SOURCES_MEMBER)["sources"]}
        summary = member(SUMMARY_MEMBER)
        matches = {row["facility_id"]: row["matches"] for row in member(MATCHES_MEMBER)["rows"]}
        points_geojson = member(POINTS_GEOJSON_MEMBER)
        matched_areas = member(MATCHED_AREAS_MEMBER)
        all_areas = member(ALL_AREAS_MEMBER)
        sup._read_member(archive, manifest, POINTS_GPKG_MEMBER, LOTS_NAME)  # GDAL reads the GeoPackage from the zip: verify it
    rules = {}
    for rule in rule_document["rules"]:
        rules[rule["rule_id"]] = {**rule, "assigned": True}
    for rule in rule_document["observed_group_rules_not_assigned"]:
        if rule["rule_id"] in rules:
            raise SystemExit(f"tariff_rules.json holds the rule {rule['rule_id']} twice")
        rules[rule["rule_id"]] = {**rule, "assigned": False}
    lots = {"path": path, "manifest": manifest, "root": LOTS_NAME,
            "file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size}, "review": review, "rules": rules,
            "decisions": decisions, "sources": sources, "matches": matches, "summary": summary}
    lots["points"] = _read_points(path, points_geojson, review)
    lots["matched_areas"] = _areas(matched_areas)
    lots["all_areas"] = _areas(all_areas)
    missing = sorted(set(lots["matched_areas"].index) - set(lots["all_areas"].index))
    if missing:
        raise SystemExit(f"the matched tariff areas {missing} are not areas of the complete layer {ALL_AREAS_MEMBER}")
    counts = {}
    for record in review.values():
        counts[record["fee_status"]] = counts.get(record["fee_status"], 0) + 1
    if counts != summary["fee_status_counts"]:
        raise SystemExit(f"the fee statuses of {REVIEW_MEMBER} {counts} differ from the summary {summary['fee_status_counts']}")
    print(f"Wolfsburg car-park inputs verified: {path.name} ({lots['file']['bytes']} bytes, SHA-256 {actual}); "
          f"{len(manifest)} members in its manifest, {len(lots['points'])} points, {len(rules)} tariff components "
          f"({sum(not rule['assigned'] for rule in rules.values())} observed group rules not assigned), {len(decisions)} field "
          f"decisions, {len(lots['all_areas'])} published tariff areas; fee statuses {dict(sorted(counts.items()))}")
    return lots


def _areas(document: dict) -> gpd.GeoDataFrame:
    """The areas of a GeoJSON document (EPSG:4326) as polygons in EPSG:25832, indexed by their ``facility_id``."""
    frame = gpd.GeoDataFrame.from_features(document["features"], crs="EPSG:4326").to_crs(cc.METRIC_CRS)
    if frame.empty or not frame.geometry.is_valid.all() or frame.geometry.is_empty.any():
        raise SystemExit("a tariff area layer is empty, or holds invalid or empty geometries; the step repairs nothing")
    if frame["facility_id"].duplicated().any():
        raise SystemExit("a tariff area layer holds a facility_id twice")
    return frame.set_index("facility_id")


def _read_points(path: Path, geojson: dict, review: dict) -> gpd.GeoDataFrame:
    """The points of layer ``parking_lots`` (EPSG:25832): valid points, one per facility, the ids of the review; cross-checked
    against the GeoJSON points (EPSG:4326) of the same package, and the fee status of the points against the review."""
    frame = gpd.read_file(f"/vsizip/{path.resolve().as_posix()}/{LOTS_NAME}/{POINTS_GPKG_MEMBER}", layer=POINTS_LAYER)
    if frame.crs is None or frame.crs.to_epsg() != 25832:
        raise SystemExit(f"{path.name} layer {POINTS_LAYER}: CRS {frame.crs} is not EPSG:25832, as the package states")
    if frame.empty or not frame.geometry.is_valid.all() or frame.geometry.is_empty.any():
        raise SystemExit(f"{path.name} layer {POINTS_LAYER}: empty, or invalid or empty geometries; the step repairs nothing")
    if (frame.geometry.geom_type != "Point").any():
        raise SystemExit(f"{path.name} layer {POINTS_LAYER}: a geometry is no point; a position stays a point (no ring, no "
                         "buffer)")
    if frame["facility_id"].duplicated().any():
        raise SystemExit(f"{path.name} layer {POINTS_LAYER}: a facility_id occurs twice")
    indexed = frame.set_index("facility_id")
    if set(indexed.index) != set(review):
        raise SystemExit(f"the review lists the facilities {sorted(review)} but the layer {POINTS_LAYER} the facilities "
                         f"{sorted(indexed.index)}")
    for facility_id, record in review.items():
        if record["fee_status"] not in FEE_STATUSES:
            raise SystemExit(f"facility {facility_id}: fee status {record['fee_status']!r} is none of {list(FEE_STATUSES)}")
        if str(indexed.loc[facility_id, "fee_status"]) != record["fee_status"]:
            raise SystemExit(f"facility {facility_id}: the layer states the fee status {indexed.loc[facility_id, 'fee_status']!r} "
                             f"but the review {record['fee_status']!r}")
    points = {feature["properties"]["facility_id"]: Point(feature["geometry"]["coordinates"][:2])
              for feature in geojson["features"]}
    properties = {feature["properties"]["facility_id"]: feature["properties"] for feature in geojson["features"]}
    projected = gpd.GeoSeries(list(points.values()), index=list(points), crs="EPSG:4326").to_crs(cc.METRIC_CRS)
    if set(projected.index) != set(indexed.index):
        raise SystemExit(f"{POINTS_GEOJSON_MEMBER} holds the points {sorted(projected.index)} but the layer {POINTS_LAYER} the "
                         f"points {sorted(indexed.index)}")
    for facility_id, geometry in projected.items():
        distance = float(geometry.distance(indexed.loc[facility_id, "geometry"]))
        if distance > COORDINATE_TOLERANCE_M:
            raise SystemExit(f"{POINTS_GEOJSON_MEMBER} point {facility_id} differs from the GeoPackage point by {distance:.3f} m "
                             f"(more than {COORDINATE_TOLERANCE_M} m, the rounding of the coordinates)")
        if properties[facility_id]["name"] != indexed.loc[facility_id, "name"]:
            raise SystemExit(f"{POINTS_GEOJSON_MEMBER} point {facility_id} is named {properties[facility_id]['name']!r} but the "
                             f"GeoPackage point {indexed.loc[facility_id, 'name']!r}")
    # the kind of the point in the city layer (the GeoJSON carries it, the GeoPackage does not): a disabled-parking point
    indexed["layer_type"] = [str(properties[facility_id].get("type")) for facility_id in indexed.index]
    return indexed


# ---------------------------------------------------------------- the classes of spec E14
def point_facts(lots: dict, zones: gpd.GeoDataFrame, facility_id: str) -> dict:
    """What the data say about one point: its fee status, the committed zones that contain it (``zones``: a GeoDataFrame in
    EPSG:25832 with ``zone_id``), the distance to the nearest zone, the published tariff areas that contain it (the matched
    ones and the complete layer), the matches the package lists, the tariff components and observed rules the package lists for
    it and the type of the point in the city layer (``layer_type``)."""
    geometry = lots["points"].loc[facility_id, "geometry"]
    review = lots["review"][facility_id]
    zone_ids = sorted(zone for zone, polygon in zip(zones["zone_id"], zones.geometry) if polygon.contains(geometry))
    distance = min(float(polygon.distance(geometry)) for polygon in zones.geometry)
    return {"facility_id": facility_id, "fee_status": review["fee_status"], "zone_ids": zone_ids, "zone_distance_m": distance,
            "matched_areas": sorted(area for area, polygon in zip(lots["matched_areas"].index, lots["matched_areas"].geometry)
                                    if polygon.contains(geometry)),
            "areas": sorted(area for area, polygon in zip(lots["all_areas"].index, lots["all_areas"].geometry)
                            if polygon.contains(geometry)),
            "matches": [match["area_id"] for match in lots["matches"].get(facility_id, [])],
            "rules": list(review.get("tariff_rule_ids") or []) + list(review.get("observed_rule_ids") or []),
            "layer_type": str(lots["points"].loc[facility_id, "layer_type"])}


def classify(facts: dict) -> Optional[str]:
    """The class of spec E14 that the data alone decide, else None (an owner decision is needed).

    * ``a``: a paid municipal car park (fee status paid_confirmed) that lies inside exactly one zone and inside exactly one
      published tariff area, which the package matches to it;
    * ``d``: a car park without any fee evidence (fee status unknown, no tariff component, no observed rule, no match) that
      lies outside every zone and outside every published tariff area.
    A paid car park that is not inside exactly one zone and one matched area, or a point inside an area that has no match, is no
    class by itself and stops the step in :func:`check_decision`."""
    if (facts["fee_status"] == "paid_confirmed" and len(facts["zone_ids"]) == 1 and len(facts["matched_areas"]) == 1
            and facts["matches"] == facts["matched_areas"] and facts["areas"] == facts["matched_areas"]):
        return "a"
    if (facts["fee_status"] == "unknown" and not facts["zone_ids"] and not facts["areas"] and not facts["matches"]
            and not facts["rules"]):
        return "d"
    return None


def check_decision(spec_class: str, facts: dict) -> None:
    """The owner decision (a class of spec E14) must agree with the data, else ``SystemExit`` naming the point: the class
    must exist, the fee status of the point must be one the class allows, a class that the data decide must be that class, and
    a point decided as ``d`` that lies inside a published tariff area or a zone (a point inside an area stops the step)."""
    facility_id = facts["facility_id"]
    if spec_class not in CLASS_FEE_STATUSES:
        raise SystemExit(f"point {facility_id}: the decision names the class {spec_class!r}, none of {sorted(CLASS_FEE_STATUSES)}")
    if facts["fee_status"] not in CLASS_FEE_STATUSES[spec_class]:
        raise SystemExit(f"point {facility_id}: the package states the fee status {facts['fee_status']!r}, which class "
                         f"{spec_class} does not allow (it allows {list(CLASS_FEE_STATUSES[spec_class])})")
    if spec_class == "d" and (facts["areas"] or facts["matched_areas"] or facts["matches"] or facts["zone_ids"]):
        raise SystemExit(f"point {facility_id} is decided as a municipal free default (class d) but lies inside the published "
                         f"tariff area(s) {facts['areas'] or facts['matched_areas'] or facts['matches']} or the zone(s) "
                         f"{facts['zone_ids']}: a point inside an area has a fee evidence and is no free default")
    derived = classify(facts)
    if derived is not None and derived != spec_class:
        raise SystemExit(f"point {facility_id}: the data decide class {derived} (fee status {facts['fee_status']}, zone "
                         f"{facts['zone_ids']}, area {facts['matched_areas']}) but the decision names class {spec_class}")
    if derived is None and spec_class in ("a", "d"):
        raise SystemExit(f"point {facility_id}: the decision names class {spec_class}, but the data do not support it (fee "
                         f"status {facts['fee_status']}, zones {facts['zone_ids']}, areas {facts['areas']}, rules "
                         f"{facts['rules']})")


# ---------------------------------------------------------------- decisions of the package
def require_decision_values(decisions: dict, expected: dict, subject: str) -> list:
    """Check that every field decision of ``expected`` ({decision id: the value}) exists and states that value, else
    ``SystemExit`` starting with ``subject``. Returns the phrases 'field decision <id>: <value>' for the notes. A value that the
    package states differently never carries a decision."""
    parts = []
    for decision_id, value in expected.items():
        if decision_id not in decisions:
            raise SystemExit(f"{subject}, but the package has no field decision {decision_id}")
        if decisions[decision_id]["value"] != value:
            raise SystemExit(f"{subject}, but field decision {decision_id} states {decisions[decision_id]['value']!r}, not "
                             f"{value!r}")
        parts.append(f"field decision {decision_id}: {value}")
    return parts


# ---------------------------------------------------------------- the rules of the package in the regional schema
def _charging_times(schedules) -> dict:
    """The regional ``charging_times`` of the package's ``weekday_schedules``: ``all_days`` states the same interval for every
    day of the week (public holidays are not stated); a schedule with other keys is refused (no day is guessed)."""
    times = {day: None for day in WEEKDAY_KEYS + ("public_holidays",)}
    if not schedules:
        return times
    if set(schedules) != {"all_days"}:
        raise SystemExit(f"weekday schedule {sorted(schedules)}: only 'all_days' is read; no day is guessed")
    for day in WEEKDAY_KEYS:
        times[day] = [{"start": entry["start"], "end": entry["end"], "crosses_midnight": bool(entry.get("crosses_midnight"))}
                      for entry in schedules["all_days"]]
    return times


def convert_rules(lots: dict, rule_ids) -> dict:
    """{rule id: rule} of the named package components (and the ``<id>:cap`` rule of a component with a daily cap) in the
    regional rule schema; all NOT preferred (``garage_supplement.release_rules`` releases the ones an owner decision allows).

    A component of the type ``published_hourly_rate`` or ``published_half_hour_rate`` becomes an ``increment`` rule (amount, unit,
    rounding as stated: none stated stays none, ASSUMPTION P4; the charging times of its ``weekday_schedules``; the free minutes
    it states as ``free_period_minutes``), ``zero_price_short_stay`` a ``free_period`` rule from 0 to its free minutes,
    ``conditional_zero_price`` a ``conditional_free`` rule (a customer condition, never used) and a component without a type
    that states the amount 0 a ``free`` rule. A daily cap becomes a ``daily_cap`` rule with the cap period the package states
    (none stated: the cap is read as a maximum per stay, as for every cap without a day boundary). ``SystemExit`` for a rule the
    package does not hold or a type this reader does not know."""
    rules = {}
    for rule_id in rule_ids:
        if rule_id not in lots["rules"]:
            raise SystemExit(f"the Wolfsburg car-park package holds no tariff component {rule_id}")
        raw = lots["rules"][rule_id]
        kind = RULE_TYPES.get(raw.get("rule_type"))
        if kind is None and raw.get("rule_type") is None and raw.get("amount_eur") == 0:
            kind = "free"
        if kind is None:
            raise SystemExit(f"tariff component {rule_id}: type {raw.get('rule_type')!r} with amount {raw.get('amount_eur')!r} "
                             f"is none of {sorted(RULE_TYPES)} or a free component; the reader refuses what it does not know")
        source_ids = list(raw["source_ids"])
        retrieved_on = str(lots["sources"][source_ids[0]]["retrieved_at_utc"])[:10]
        common = {"source_ids": source_ids, "retrieved_on": retrieved_on, "status": raw.get("status")}
        free_minutes = raw.get("free_minutes")
        fields = {"amount_eur": raw.get("amount_eur"), "billing_unit_minutes": raw.get("billing_unit_minutes"),
                  "rounding": sup._rounding(rule_id, raw.get("rounding")), "charging_times": _charging_times(
                      raw.get("weekday_schedules")),
                  "conditions": "; ".join((raw.get("conditions") or []) + ([f"package statement: {raw['free_minutes_policy']}"]
                                                                          if raw.get("free_minutes_policy") else [])) or None,
                  "free_period_minutes": free_minutes, "origin": "lots", "assigned": raw["assigned"]}
        if kind == "free_period":
            fields.update(elapsed_from_minutes=0, elapsed_to_minutes=free_minutes)
        rules[rule_id] = sup._regional_rule(lots, rule_id, raw["facility_id"], kind, **common, **fields)
        if raw.get("daily_cap_eur") is not None:
            cap_id = f"{rule_id}:cap"
            rules[cap_id] = sup._regional_rule(
                lots, cap_id, raw["facility_id"], "daily_cap", amount_eur=raw["daily_cap_eur"],
                daily_cap_eur=raw["daily_cap_eur"], cap_period=raw.get("daily_cap_period"), origin="lots",
                assigned=raw["assigned"], **common)
    return rules


def attach(inputs: dict, lots: dict, rule_ids, released: dict) -> None:
    """Merge the Wolfsburg car-park package into the garage inputs (in place): the named components become rules of the
    regional schema (an id that clashes with a rule of the other packages stops the step), the released ones preferred, and
    ``inputs["lots"]`` carries the package."""
    rules = convert_rules(lots, rule_ids)
    sup.release_rules(rules, lots["decisions"], released)
    clashes = sorted(set(rules) & set(inputs["rules"]))
    if clashes:
        raise SystemExit(f"Wolfsburg car-park rule id(s) {clashes} clash with rules of the other packages")
    inputs["rules"].update(rules)
    inputs["lots"] = lots


def operator_from_text(lots: dict, source_id: str, operator: str, member: str, quotation: str) -> dict:
    """{"operator", "url", "quotation"}: ``operator`` is taken only where the package's source ``source_id`` is a primary
    operator source, the text ``member`` (a page text or a saved search extract of the package) is checked against the manifest
    and holds the quotation; ``SystemExit`` otherwise. A name is never taken from a page that does not hold it."""
    source = lots["sources"].get(source_id)
    if source is None or source.get("source_type") != "primary_operator":
        raise SystemExit(f"source {source_id} is no primary operator source of the package; an operator is taken from the "
                         "operator's own page only")
    with zipfile.ZipFile(lots["path"]) as archive:
        content = sup._read_member(archive, lots["manifest"], member, LOTS_NAME).decode("utf-8")
    try:
        text = " ".join(_strings(json.loads(content)))
    except ValueError:
        text = content
    if sup._norm(quotation) not in sup._norm(text):
        raise SystemExit(f"the quotation {quotation!r} of the operator {operator!r} is not in the package member {member}")
    return {"operator": operator, "url": source["url"], "quotation": quotation}


def _strings(value) -> list:
    """Every string of a parsed JSON document, in document order."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []
