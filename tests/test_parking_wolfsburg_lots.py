"""Wolfsburg car-park package of the garage dataset (parking cost zones v2, spec Amendment E14, issue #436, Task 4b3).

The owner's package ``Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip`` is gitignored, so the fourth input of the curation step
``regional_garages.py`` (``wolfsburg_lots.py``) is pinned on a synthetic package written the way the owner supplied it (a zip
with ``manifest.sha256``, the GeoPackage layer ``parking_lots`` in EPSG:25832 next to the same points as GeoJSON in EPSG:4326,
the JSON tables ``facility_review``, ``tariff_rules`` (with the observed group rules that are not assigned),
``field_decisions``, ``sources``, ``summary`` and ``point_area_matches``, the published tariff areas as GeoJSON and two evidence
texts) with six points, one of every class of spec E14. What is pinned: the verification of the package and of every member
that is read, the classification of a point from its fee status and its position (never from a name), the owner decision that
is checked against the data (a class the fee status does not allow, a point inside a published tariff area decided as a
municipal free default), the conversion of the package's components to the regional rule schema, their release by an owner
decision and the package's own field decisions, the operator that is taken only from a page that holds the quotation, the
rows of the dataset (a tiered garage with a grace period, a grace period with a rate and a cap, a free car park, the municipal
free default under ASSUMPTION P12), the QA rows (a car park inside a zone with its consistency check against the zone's street
rate, a car park that is no option) and the byte-identical reruns. The committed dataset is pinned independently in
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
from shapely.geometry import Point, box

from braunschweig.parking import garage_qa as pq
from braunschweig.parking import garages as pg
from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
LOTS = "Wolfsburg_Parkplaetze_Pruefung_2026-10-07"
X0, Y0 = 604_000.0, 5_790_000.0
WOB = "03103000"
GEOVIEWER = "https://geoviewer.stadt.wolfsburg.de/default/ows/projects/gpt/parken"
SCHEDULE_DAY = {"all_days": [{"start": "06:00", "end": "18:00"}]}
SCHEDULE_NIGHT = {"all_days": [{"start": "18:00", "end": "06:00", "crosses_midnight": True}]}
RULES_TEXT_MEMBER = "evidence/research/operators/text/op_rules.txt"
CLINIC_MEMBER = "evidence/research/operators/search_clinic.json"


@pytest.fixture(scope="module")
def garages_step():
    """The curation step as a module (it imports its siblings, ``wolfsburg_lots`` among them, from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("regional_garages_for_lots", CURATION_DIR / "regional_garages.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture(scope="module")
def regional_tests():
    """The helpers of the regional test file (its synthetic regional package), loaded from the file."""
    spec = importlib.util.spec_from_file_location("regional_garages_tests_for_lots", Path(__file__).with_name(
        "test_parking_regional_garages.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wl(garages_step):
    return garages_step.wl


# --------------------------------------------------------------------------- the synthetic package


def _rule(rule_id, facility, **fields) -> dict:
    """A component of the package's tariff_rules.json (the keys of the real file; ``null`` = unknown)."""
    rule = {"amount_eur": None, "billing_unit_minutes": None, "rounding": None, "free_minutes": None,
            "free_minutes_policy": None, "daily_cap_eur": None, "daily_cap_period": None, "weekday_schedules": None,
            "max_stay_minutes": None, "monthly_price_eur": None, "capacity": None, "rule_id": rule_id,
            "facility_id": facility, "assigned_to_point": True, "full_cost_calculation_ready": False,
            "status": "documented_component", "source_ids": ["op_prices"]}
    rule.update(fields)
    return rule


RULES = [
    _rule("WOB_PARK_A_MOBILE_REFERENCE", "WOB_PARK_A", rule_type="published_hourly_reference", amount_eur=2.0,
          billing_unit_minutes=60, fee_zone=1, linked_mobile_facility_id="WOB_MOBILE_1", source_ids=["ROOT_WOB_MOBILE_CURRENT"]),
    _rule("B1_DAY", "WOB_PARK_B1", rule_type="published_hourly_rate", amount_eur=1.0, billing_unit_minutes=60, free_minutes=30,
          daily_cap_eur=6.0, weekday_schedules=SCHEDULE_DAY, conditions=["the daily maximum needs the validation of the ticket"],
          source_ids=["op_prices", "op_rules"]),
    _rule("B1_NIGHT", "WOB_PARK_B1", rule_type="published_hourly_rate", amount_eur=0.5, billing_unit_minutes=60,
          free_minutes=30, weekday_schedules=SCHEDULE_NIGHT, source_ids=["op_prices", "op_rules"]),
    _rule("B1_GRACE", "WOB_PARK_B1", rule_type="zero_price_short_stay", amount_eur=0, free_minutes=30,
          conditions=["up to 30 minutes no fee; the treatment of longer stays is unknown"], source_ids=["op_rules"]),
    _rule("B1_PREMIUM", "WOB_PARK_B1", rule_type="conditional_zero_price", amount_eur=0, source_ids=["op_rules"],
          conditions=["a valid premium card and the ticket activated at the desk"]),
    _rule("C_R01", "WOB_PARK_C", amount_eur=0, free_minutes_policy="Free of charge, no free-minutes quota and no maximum stay.",
          source_ids=["ev_free"]),
]
GROUP_RULES = [
    _rule("B2_RATE", "WOB_PARK_B2", rule_type="published_half_hour_rate", amount_eur=0.8, billing_unit_minutes=30,
          free_minutes=30, daily_cap_eur=5.0, assigned_to_point=False, status="observed_group_tariff_mapping_unresolved",
          source_ids=["op_clinic"], applicability_to_point="conditional_on_subfacility_match"),
    _rule("B2_GRACE", "WOB_PARK_B2", rule_type="zero_price_short_stay", amount_eur=0, free_minutes=30, assigned_to_point=False,
          status="observed_group_tariff_mapping_unresolved", source_ids=["op_clinic"],
          applicability_to_point="conditional_on_subfacility_match"),
]
FIELDS = ("amount_eur", "billing_unit_minutes", "rounding", "free_minutes", "free_minutes_policy", "daily_cap_eur",
          "daily_cap_period", "weekday_schedules", "max_stay_minutes", "monthly_price_eur", "capacity")

#: facility id -> (offset x, offset y in m from (X0, Y0), name, fee status, type of the point, capacity or None)
POINTS = {"WOB_PARK_A": ((10_000.0, 300.0), "Parkplatz A", "paid_confirmed", "Parkplaetze", None),
          "WOB_PARK_B1": ((10_500.0, 400.0), "Parkplatz Besucher", "conditional", "Parkplaetze", None),
          "WOB_PARK_B2": ((10_600.0, 500.0), "Parkplatz Klinik", "conditional", "Parkplaetze", None),
          "WOB_PARK_C": ((10_800.0, 450.0), "Parkplatz Frei", "free_confirmed", "Parkplaetze", 120),
          "WOB_PARK_D": ((11_000.0, -500.0), "Parkplatz", "unknown", "Parkplaetze", None),
          "WOB_PARK_E": ((11_200.0, 0.0), "Parkplatz Sonder", "conditional", "Behindertenparkplaetze", None)}
CAPACITY_SCOPE = "Test car park, published total capacity; no live availability"
#: the recommendations of the synthetic facility review that the rows of classes b (with an override) and d quote
RECOMMENDATIONS = {"WOB_PARK_B1": "Erfassen, aber keine pr\u00e4zise Preisfunktion erg\u00e4nzen.",
                   "WOB_PARK_B2": "Erst bei best\u00e4tigter Unteranlage als Punkttarif \u00fcbernehmen.",
                   "WOB_PARK_D": "Gebuehrenstatus unbekannt belassen; keine 0-Euro-Regel anlegen."}
OPERATOR_QUOTATION = "Die Parkkarte ist Eigentum der Testbetrieb GmbH"
CLINIC_QUOTATION = "unsere Besucherparkpl\u00e4tze P1 und P2"


def _point(facility) -> Point:
    (dx, dy) = POINTS[facility][0]
    return Point(X0 + dx, Y0 + dy)


def _zones() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"zone_id": ["wob_zone_t1", "wob_zone_t2"]}, crs=METRIC_CRS, geometry=[
        box(X0 + 9_900, Y0 + 200, X0 + 10_100, Y0 + 400), box(X0 + 10_550, Y0 + 450, X0 + 10_650, Y0 + 550)])


def _tariffs() -> pd.DataFrame:
    return pd.DataFrame({"zone_id": ["wob_zone_t1", "wob_zone_t2"], "hourly_rate_eur": [2.0, 1.2],
                         "fee_start_h": [7.0, 7.0], "fee_end_h": [18.0, 18.0], "commuter_day_eur": [float("nan")] * 2})


def _areas(*identifiers) -> dict:
    boxes = {"WOB_MOBILE_1": box(X0 + 9_950, Y0 + 250, X0 + 10_050, Y0 + 350),
             "WOB_MOBILE_2": box(X0 + 20_000, Y0, X0 + 20_100, Y0 + 100)}
    frame = gpd.GeoDataFrame({"facility_id": list(identifiers)}, crs=METRIC_CRS,
                             geometry=[boxes[identifier] for identifier in identifiers]).to_crs("EPSG:4326")
    return json.loads(frame.to_json())


def _members(edit=None) -> dict:
    """The members of the synthetic package as {name: bytes}; ``edit`` may change the parsed JSON documents first."""
    review = []
    for facility, (_, name, fee_status, kind, capacity) in POINTS.items():
        record = {"facility_id": facility, "name": name, "fee_status": fee_status,
                  "tariff_rule_ids": [rule["rule_id"] for rule in RULES if rule["facility_id"] == facility],
                  "observed_rule_ids": [rule["rule_id"] for rule in GROUP_RULES if rule["facility_id"] == facility],
                  "capacity": capacity, "capacity_scope": CAPACITY_SCOPE if capacity else None,
                  "recommendation": RECOMMENDATIONS.get(facility, f"Keep {facility} as a test point.")}
        review.append(record)
    decisions = []
    for rule in RULES + GROUP_RULES:
        for field in FIELDS:
            decisions.append({"decision_id": f"{rule['rule_id']}:{field}", "facility_id": rule["facility_id"],
                              "rule_id": rule["rule_id"], "field": field, "value": rule[field],
                              "status": "documented_component" if rule[field] is not None else "unknown",
                              "assigned_to_point": rule["assigned_to_point"]})
    for facility, (_, _, fee_status, _, capacity) in POINTS.items():
        decisions.append({"decision_id": f"{facility}:fee_status", "facility_id": facility, "rule_id": None,
                          "field": "fee_status", "value": fee_status, "status": "reviewed_evidence", "assigned_to_point": True})
        decisions.append({"decision_id": f"{facility}:capacity", "facility_id": facility, "rule_id": None, "field": "capacity",
                          "value": capacity, "status": "reviewed_evidence" if capacity else "unknown",
                          "assigned_to_point": True})
    decisions.append({"decision_id": "WOB_PARK_B2:geometry_match", "facility_id": "WOB_PARK_B2", "rule_id": None,
                      "field": "geometry_match", "value": {"status": "partial_subfacility_unresolved"},
                      "status": "reviewed_evidence", "assigned_to_point": True})
    counts = {}
    for record in review:
        counts[record["fee_status"]] = counts.get(record["fee_status"], 0) + 1
    sources = [{"source_id": "ROOT_WOB_POINTS_CURRENT", "url": GEOVIEWER, "retrieved_at_utc": "2026-10-07T05:25:03+00:00",
                "source_type": "primary_municipal_vector"},
               {"source_id": "ROOT_WOB_MOBILE_CURRENT", "url": GEOVIEWER, "retrieved_at_utc": "2026-10-07T05:25:03+00:00",
                "source_type": "primary_municipal_vector"},
               {"source_id": "op_prices", "url": "https://operator.example/prices", "retrieved_at_utc": "2026-10-07T18:15:56+00:00",
                "source_type": "primary_operator"},
               {"source_id": "op_rules", "url": "https://operator.example/rules", "retrieved_at_utc": "2026-10-07T18:16:10+00:00",
                "source_type": "primary_operator"},
               {"source_id": "op_clinic", "url": "https://clinic.example/visitors", "retrieved_at_utc": "2026-10-07T18:17:01+00:00",
                "source_type": "primary_operator"},
               {"source_id": "ev_free", "url": "https://free.example/lot", "retrieved_at_utc": "2026-10-07T18:18:00+00:00",
                "source_type": "secondary"}]
    matches = [{"facility_id": facility, "name": POINTS[facility][1], "matches": []} for facility in POINTS]
    matches[0]["matches"] = [{"area_id": "WOB_MOBILE_1", "area_name": "Area One", "relation": "inside",
                              "price_raw": "2 EUR per hour", "fee_zone": 1, "paid_hours_raw": "07:00-18:00"}]
    documents = {"data/facility_review.json": {"facilities": review},
                 "data/tariff_rules.json": {"schema_version": "2.1", "rules": [dict(rule) for rule in RULES],
                                            "observed_group_rules_not_assigned": [dict(rule) for rule in GROUP_RULES]},
                 "data/field_decisions.json": {"decisions": decisions}, "data/sources.json": {"sources": sources},
                 "data/summary.json": {"fee_status_counts": counts}, "data/point_area_matches.json": {"method": "x",
                                                                                                         "rows": matches}}
    if edit is not None:
        edit(documents)
    members = {name: json.dumps(document).encode("utf-8") for name, document in documents.items()}
    frame = gpd.GeoDataFrame({"facility_id": list(POINTS), "name": [POINTS[f][1] for f in POINTS],
                              "fee_status": [POINTS[f][2] for f in POINTS], "details_json": ["{}"] * len(POINTS)},
                             geometry=[_point(facility) for facility in POINTS], crs=METRIC_CRS)
    geojson = json.loads(frame.to_crs("EPSG:4326").to_json())
    for feature, facility in zip(geojson["features"], POINTS):
        feature["properties"] = {"facility_id": facility, "name": POINTS[facility][1], "type": POINTS[facility][3]}
    members["data/parking_lots.geojson"] = json.dumps(geojson).encode("utf-8")
    members["data/published_tariff_areas.geojson"] = json.dumps(_areas("WOB_MOBILE_1")).encode("utf-8")
    members["evidence/input/daten/geojson/wob_handyparkflaechen.geojson"] = json.dumps(
        _areas("WOB_MOBILE_1", "WOB_MOBILE_2")).encode("utf-8")
    members[RULES_TEXT_MEMBER] = f"Parkplatzordnung. {OPERATOR_QUOTATION}.".encode("utf-8")
    members[CLINIC_MEMBER] = json.dumps({"result": {"text": f"In direkter Naehe befinden sich {CLINIC_QUOTATION}."}}).encode()
    return members


def _write_lots_package(directory: Path, members: dict, *, tamper: str | None = None, edit_layer=None) -> str:
    """Write the package zip the way the owner supplied it (members under one folder, ``manifest.sha256`` with the SHA-256 of
    every member) and return the SHA-256 of the zip. ``tamper`` changes a member after the manifest was written."""
    directory.mkdir(parents=True, exist_ok=True)
    gpkg = directory / "parking_lots.gpkg"
    frame = gpd.GeoDataFrame({"facility_id": list(POINTS), "name": [POINTS[f][1] for f in POINTS],
                              "fee_status": [POINTS[f][2] for f in POINTS], "details_json": ["{}"] * len(POINTS)},
                             geometry=[_point(facility) for facility in POINTS], crs=METRIC_CRS)
    if edit_layer is not None:
        frame = edit_layer(frame)
    frame.to_file(gpkg, layer="parking_lots", driver="GPKG")
    members = dict(members)
    members["data/parking_lots.gpkg"] = gpkg.read_bytes()
    gpkg.unlink()
    manifest = "\n".join(f"{hashlib.sha256(content).hexdigest()}  {name}" for name, content in sorted(members.items()))
    path = directory / f"{LOTS}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(f"{LOTS}/{name}", b"tampered" if name == tamper else content)
        archive.writestr(f"{LOTS}/manifest.sha256", manifest)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def lots_package(tmp_path_factory):
    directory = tmp_path_factory.mktemp("lots")
    return directory / f"{LOTS}.zip", _write_lots_package(directory, _members())


@pytest.fixture()
def lots(wl, lots_package):
    return wl.load_lots(lots_package[0], expected_sha256=lots_package[1])


# --------------------------------------------------------------------------- the package is verified before it is read


def test_the_package_is_verified_and_its_points_are_the_valid_points_of_the_layer(wl, lots_package, capsys):
    loaded = wl.load_lots(lots_package[0], expected_sha256=lots_package[1])
    assert loaded["file"]["sha256"] == lots_package[1] and loaded["root"] == LOTS
    assert list(loaded["points"].index) == list(POINTS) and loaded["points"].crs.to_epsg() == 25832
    assert all(geometry.equals(_point(facility)) for facility, geometry in loaded["points"].geometry.items())
    assert loaded["points"].loc["WOB_PARK_E", "layer_type"] == "Behindertenparkplaetze"
    assert set(loaded["rules"]) >= {"B1_DAY", "B2_RATE"} and loaded["rules"]["B2_RATE"]["assigned"] is False
    assert loaded["rules"]["B1_DAY"]["assigned"] is True
    assert list(loaded["all_areas"].index) == ["WOB_MOBILE_1", "WOB_MOBILE_2"] and list(loaded["matched_areas"].index) == [
        "WOB_MOBILE_1"]
    out = capsys.readouterr().out
    assert "Wolfsburg car-park inputs verified" in out and "6 points" in out and "2 observed group rules not assigned" in out


def test_a_changed_missing_or_tampered_package_is_refused(wl, lots_package, tmp_path):
    with pytest.raises(SystemExit, match="SHA-256"):
        wl.load_lots(lots_package[0], expected_sha256="0" * 64)
    with pytest.raises(SystemExit, match="missing: pass the owner's Wolfsburg car-park package"):
        wl.load_lots(tmp_path / "none.zip")
    sha256 = _write_lots_package(tmp_path, _members(), tamper="data/facility_review.json")
    with pytest.raises(SystemExit, match="data/facility_review.json has the SHA-256"):
        wl.load_lots(tmp_path / f"{LOTS}.zip", expected_sha256=sha256)


def test_a_review_that_lists_other_points_or_states_another_fee_status_than_the_layer_is_refused(wl, tmp_path):
    def drop_a_point(documents):
        documents["data/facility_review.json"]["facilities"].pop()
    sha256 = _write_lots_package(tmp_path / "a", _members(drop_a_point))
    with pytest.raises(SystemExit, match="the review lists the facilities"):
        wl.load_lots(tmp_path / "a" / f"{LOTS}.zip", expected_sha256=sha256)

    def change_status(documents):
        documents["data/facility_review.json"]["facilities"][0]["fee_status"] = "free_confirmed"
        documents["data/summary.json"]["fee_status_counts"] = {"free_confirmed": 1, "conditional": 3, "unknown": 1,
                                                                 "paid_confirmed": 0}
    sha256 = _write_lots_package(tmp_path / "b", _members(change_status))
    with pytest.raises(SystemExit, match="the layer states the fee status 'paid_confirmed' but the review 'free_confirmed'"):
        wl.load_lots(tmp_path / "b" / f"{LOTS}.zip", expected_sha256=sha256)

    def unknown_status(documents):
        documents["data/facility_review.json"]["facilities"][0]["fee_status"] = "maybe"
    sha256 = _write_lots_package(tmp_path / "c", _members(unknown_status))
    with pytest.raises(SystemExit, match="fee status 'maybe' is none of"):
        wl.load_lots(tmp_path / "c" / f"{LOTS}.zip", expected_sha256=sha256)

    def wrong_summary(documents):
        documents["data/summary.json"]["fee_status_counts"] = {"unknown": 6}
    sha256 = _write_lots_package(tmp_path / "d", _members(wrong_summary))
    with pytest.raises(SystemExit, match="differ from the summary"):
        wl.load_lots(tmp_path / "d" / f"{LOTS}.zip", expected_sha256=sha256)


def test_the_layer_must_be_valid_metric_points_and_the_geojson_must_equal_it_to_one_centimetre(wl, tmp_path):
    sha256 = _write_lots_package(tmp_path / "a", _members(), edit_layer=lambda frame: frame.to_crs("EPSG:4326"))
    with pytest.raises(SystemExit, match="is not EPSG:25832"):
        wl.load_lots(tmp_path / "a" / f"{LOTS}.zip", expected_sha256=sha256)

    def shifted(frame):
        frame.loc[0, "geometry"] = Point(X0 + 10_000.5, Y0 + 300)
        return frame
    sha256 = _write_lots_package(tmp_path / "b", _members(), edit_layer=shifted)
    with pytest.raises(SystemExit, match=r"differs from the GeoPackage point by 0\.[0-9]+ m"):
        wl.load_lots(tmp_path / "b" / f"{LOTS}.zip", expected_sha256=sha256)

    def doubled(frame):
        frame.loc[1, "facility_id"] = frame.loc[0, "facility_id"]
        return frame
    sha256 = _write_lots_package(tmp_path / "c", _members(), edit_layer=doubled)
    with pytest.raises(SystemExit, match="a facility_id occurs twice"):
        wl.load_lots(tmp_path / "c" / f"{LOTS}.zip", expected_sha256=sha256)


# --------------------------------------------------------------------------- the classes of spec E14


def test_the_class_is_decided_from_the_fee_status_and_the_position_never_from_a_name(wl, lots):
    zones = _zones()
    classes = {facility: wl.classify(wl.point_facts(lots, zones, facility)) for facility in POINTS}
    # a paid municipal car park inside one zone and one matched area; a point without any fee evidence outside every zone and
    # area; every other point needs an owner decision
    assert classes == {"WOB_PARK_A": "a", "WOB_PARK_B1": None, "WOB_PARK_B2": None, "WOB_PARK_C": None, "WOB_PARK_D": "d",
                       "WOB_PARK_E": None}
    facts = wl.point_facts(lots, zones, "WOB_PARK_A")
    assert (facts["zone_ids"], facts["matched_areas"], facts["areas"], facts["matches"]) == (
        ["wob_zone_t1"], ["WOB_MOBILE_1"], ["WOB_MOBILE_1"], ["WOB_MOBILE_1"])
    assert wl.point_facts(lots, zones, "WOB_PARK_B2")["zone_ids"] == ["wob_zone_t2"]   # inside a zone, still no class by itself
    assert wl.point_facts(lots, zones, "WOB_PARK_D")["zone_distance_m"] > 500
    assert wl.point_facts(lots, zones, "WOB_PARK_E")["layer_type"] == "Behindertenparkplaetze"
    # class a needs ONE zone and exactly the area that the package matches: a second area, an unmatched area, no zone and a
    # fee status other than paid_confirmed each leave the point to an owner decision
    paid = wl.point_facts(lots, zones, "WOB_PARK_A")
    for key, value in (("areas", ["WOB_MOBILE_1", "WOB_MOBILE_2"]), ("matches", []), ("matches", ["WOB_MOBILE_2"]),
                       ("zone_ids", []), ("zone_ids", ["wob_zone_t1", "wob_zone_t2"]), ("fee_status", "conditional")):
        assert wl.classify(dict(paid, **{key: value})) is None, key
    # class d needs no zone, no area, no match, no component and the fee status unknown
    free = wl.point_facts(lots, zones, "WOB_PARK_D")
    for key, value in (("zone_ids", ["wob_zone_t1"]), ("areas", ["WOB_MOBILE_2"]), ("matches", ["WOB_MOBILE_2"]),
                       ("rules", ["SOME_RULE"]), ("fee_status", "free_confirmed")):
        assert wl.classify(dict(free, **{key: value})) is None, key
    # the name plays no part: every point keeps its class when it is renamed
    renamed = lots["points"].copy()
    renamed["name"] = "Parkplatz Autostadt"
    lots["points"] = renamed
    assert {facility: wl.classify(wl.point_facts(lots, zones, facility)) for facility in POINTS} == classes


@pytest.mark.parametrize("facility, spec_class, message", [
    ("WOB_PARK_A", "b", r"the package states the fee status 'paid_confirmed', which class b does not allow"),
    ("WOB_PARK_D", "c", r"which class c does not allow"),
    ("WOB_PARK_A", "d", r"the package states the fee status 'paid_confirmed', which class d does not allow"),
    ("WOB_PARK_C", "e", r"which class e does not allow"),
    ("WOB_PARK_A", "z", "none of"),
    ("WOB_PARK_D", "a", r"the package states the fee status 'unknown', which class a does not allow"),
])
def test_an_owner_decision_for_a_class_the_fee_status_does_not_allow_stops_the_step(wl, lots, facility, spec_class, message):
    with pytest.raises(SystemExit, match=message):
        wl.check_decision(spec_class, wl.point_facts(lots, _zones(), facility))


def test_a_decision_that_the_data_decide_otherwise_or_do_not_support_stops_the_step(wl, lots):
    zones = _zones()
    # the data decide class d: a decision c for the same point (unknown does not allow c) and a decision a are both refused
    for spec_class in ("c", "a"):
        with pytest.raises(SystemExit):
            wl.check_decision(spec_class, wl.point_facts(lots, zones, "WOB_PARK_D"))
    # a point with a fee status that the class allows but no evidence the class needs: B1 decided as a (conditional is no a)
    with pytest.raises(SystemExit, match="does not allow"):
        wl.check_decision("a", wl.point_facts(lots, zones, "WOB_PARK_B1"))
    # a paid car park outside every zone is no class a: the data cannot support it
    outside = wl.point_facts(lots, zones, "WOB_PARK_A")
    outside["zone_ids"] = []
    with pytest.raises(SystemExit, match="do not support it"):
        wl.check_decision("a", outside)
    # the data decide class a: a decision b for it is refused by the fee status first; a consistent decision passes
    wl.check_decision("a", wl.point_facts(lots, zones, "WOB_PARK_A"))
    wl.check_decision("d", wl.point_facts(lots, zones, "WOB_PARK_D"))
    for facility, spec_class in (("WOB_PARK_B1", "b"), ("WOB_PARK_B2", "b"), ("WOB_PARK_C", "c"), ("WOB_PARK_E", "e")):
        wl.check_decision(spec_class, wl.point_facts(lots, zones, facility))


def test_a_point_inside_a_published_tariff_area_or_a_zone_is_never_a_municipal_free_default(wl, lots):
    facts = wl.point_facts(lots, _zones(), "WOB_PARK_D")
    for key, value in (("areas", ["WOB_MOBILE_2"]), ("matched_areas", ["WOB_MOBILE_1"]), ("matches", ["WOB_MOBILE_1"]),
                       ("zone_ids", ["wob_zone_t1"])):
        inside = dict(facts, **{key: value})
        with pytest.raises(SystemExit, match="inside the published tariff area"):
            wl.check_decision("d", inside)
    # a point of a package whose complete area layer holds an area at the point is caught although the matches are empty
    wide = lots["all_areas"].copy()
    wide.loc["WOB_MOBILE_2", "geometry"] = box(X0 + 10_950, Y0 - 600, X0 + 11_050, Y0 - 400)
    lots["all_areas"] = wide
    with pytest.raises(SystemExit, match="inside the published tariff area"):
        wl.check_decision("d", wl.point_facts(lots, _zones(), "WOB_PARK_D"))
    assert wl.classify(wl.point_facts(lots, _zones(), "WOB_PARK_D")) is None


# --------------------------------------------------------------------------- the components in the regional schema


def test_the_components_are_written_in_the_regional_rule_schema_all_of_them_not_preferred(wl, lots):
    rules = wl.convert_rules(lots, ["B1_DAY", "B1_NIGHT", "B1_GRACE", "B1_PREMIUM", "B2_RATE", "B2_GRACE", "C_R01"])
    assert set(rules) == {"B1_DAY", "B1_DAY:cap", "B1_NIGHT", "B1_GRACE", "B1_PREMIUM", "B2_RATE", "B2_RATE:cap", "B2_GRACE",
                          "C_R01"}
    assert all(rule["preferred_for_current_use"] is False and rule["origin"] == "lots" for rule in rules.values())
    day, night = rules["B1_DAY"], rules["B1_NIGHT"]
    assert (day["rule_type"], day["amount_eur"], day["billing_unit_minutes"], day["rounding"]) == ("increment", 1.0, 60, None)
    assert day["free_period_minutes"] == 30 and day["source_url"] == "https://operator.example/prices"
    assert day["retrieved_at"] == "2026-10-07"
    assert [(entry["start"], entry["end"], entry["crosses_midnight"]) for entry in day["charging_times"]["monday"]] == [
        ("06:00", "18:00", False)]
    assert day["charging_times"]["sunday"] == day["charging_times"]["monday"] and day["charging_times"]["public_holidays"] is None
    assert night["charging_times"]["friday"][0]["crosses_midnight"] is True
    cap = rules["B1_DAY:cap"]
    assert (cap["rule_type"], cap["daily_cap_eur"], cap["cap_period"]) == ("daily_cap", 6.0, None)
    grace = rules["B1_GRACE"]
    assert (grace["rule_type"], grace["elapsed_from_minutes"], grace["elapsed_to_minutes"]) == ("free_period", 0, 30)
    assert rules["B1_PREMIUM"]["rule_type"] == "conditional_free" and "premium card" in rules["B1_PREMIUM"]["conditions"]
    assert rules["B2_RATE"]["rule_type"] == "increment" and rules["B2_RATE"]["assigned"] is False
    free = rules["C_R01"]
    assert (free["rule_type"], free["amount_eur"], free["elapsed_to_minutes"]) == ("free", 0, None)
    assert "package statement: Free of charge, no free-minutes quota" in free["conditions"]


def test_a_component_the_reader_does_not_know_is_refused_instead_of_guessed(wl, lots):
    with pytest.raises(SystemExit, match="holds no tariff component NOPE"):
        wl.convert_rules(lots, ["NOPE"])
    with pytest.raises(SystemExit, match="reader refuses what it does not know"):
        wl.convert_rules(lots, ["WOB_PARK_A_MOBILE_REFERENCE"])   # a hourly reference is evidence, no component of a row
    lots["rules"]["B1_DAY"]["weekday_schedules"] = {"monday": [{"start": "06:00", "end": "18:00"}]}
    with pytest.raises(SystemExit, match="only 'all_days' is read"):
        wl.convert_rules(lots, ["B1_DAY"])
    lots["rules"]["B1_DAY"]["weekday_schedules"] = SCHEDULE_DAY
    lots["rules"]["B1_DAY"]["rounding"] = "pro_rata"
    with pytest.raises(SystemExit, match="rounding 'pro_rata'"):
        wl.convert_rules(lots, ["B1_DAY"])


RELEASED = {
    "B1_DAY": ("R-T", {"B1_DAY:amount_eur": ("documented_component",), "B1_DAY:billing_unit_minutes": ("documented_component",),
                       "B1_DAY:weekday_schedules": ("documented_component",), "B1_DAY:free_minutes": ("documented_component",),
                       "B1_DAY:rounding": ("unknown",)}),
    "B1_NIGHT": ("R-T", {"B1_NIGHT:amount_eur": ("documented_component",), "B1_NIGHT:rounding": ("unknown",),
                         "B1_NIGHT:billing_unit_minutes": ("documented_component",),
                         "B1_NIGHT:weekday_schedules": ("documented_component",),
                         "B1_NIGHT:free_minutes": ("documented_component",)}),
    "B1_GRACE": ("R-T", {"B1_GRACE:free_minutes": ("documented_component",), "B1_GRACE:free_minutes_policy": ("unknown",)}),
    "B2_RATE": ("R-T", {"B2_RATE:amount_eur": ("documented_component",), "B2_RATE:billing_unit_minutes": ("documented_component",),
                        "B2_RATE:free_minutes": ("documented_component",), "B2_RATE:rounding": ("unknown",),
                        "B2_RATE:weekday_schedules": ("unknown",)}),
    "B2_RATE:cap": ("R-T", {"B2_RATE:daily_cap_eur": ("documented_component",), "B2_RATE:daily_cap_period": ("unknown",)}),
    "B2_GRACE": ("R-T", {"B2_GRACE:free_minutes": ("documented_component",), "B2_GRACE:free_minutes_policy": ("unknown",)}),
    "C_R01": ("R-T", {"C_R01:amount_eur": ("documented_component",), "C_R01:free_minutes_policy": ("documented_component",),
                      "WOB_PARK_C:fee_status": ("reviewed_evidence",)}),
}
RULE_IDS = ("B1_DAY", "B1_NIGHT", "B1_GRACE", "B1_PREMIUM", "B2_RATE", "B2_GRACE", "C_R01")


def test_a_component_is_released_only_where_the_package_field_decisions_have_the_statuses_the_ruling_relied_on(wl, lots):
    inputs = {"rules": {}, "facilities": {}}
    wl.attach(inputs, lots, RULE_IDS, RELEASED)
    assert inputs["lots"] is lots
    for rule_id in RELEASED:
        assert inputs["rules"][rule_id]["preferred_for_current_use"] is True
        assert inputs["rules"][rule_id]["released_by"].startswith("R-T (field decision ")
    # the rules no ruling releases never set a value
    for rule_id in ("B1_PREMIUM", "B1_DAY:cap"):
        assert inputs["rules"][rule_id]["preferred_for_current_use"] is False
    # a field decision that changed its status (the package resolved the rounding) stops the release
    lots["decisions"]["B1_DAY:rounding"] = {**lots["decisions"]["B1_DAY:rounding"], "status": "documented_component"}
    with pytest.raises(SystemExit, match="field decision B1_DAY:rounding has the status 'documented_component'"):
        wl.attach({"rules": {}, "facilities": {}}, lots, RULE_IDS, RELEASED)
    del lots["decisions"]["B1_DAY:rounding"]
    with pytest.raises(SystemExit, match="has no field decision B1_DAY:rounding"):
        wl.attach({"rules": {}, "facilities": {}}, lots, RULE_IDS, RELEASED)


def test_a_rule_id_that_clashes_with_a_rule_of_another_package_stops_the_step(wl, lots):
    with pytest.raises(SystemExit, match=r"rule id\(s\) \['B1_DAY'\] clash"):
        wl.attach({"rules": {"B1_DAY": {}}, "facilities": {}}, lots, RULE_IDS, RELEASED)


def test_field_decision_values_are_checked_where_a_class_relies_on_them(wl, lots):
    parts = wl.require_decision_values(lots["decisions"], {"WOB_PARK_D:fee_status": "unknown"}, "the class d")
    assert parts == ["field decision WOB_PARK_D:fee_status: unknown"]
    with pytest.raises(SystemExit, match="WOB_PARK_D:fee_status states 'unknown', not 'free_confirmed'"):
        wl.require_decision_values(lots["decisions"], {"WOB_PARK_D:fee_status": "free_confirmed"}, "the class c")
    with pytest.raises(SystemExit, match="has no field decision NOPE:fee_status"):
        wl.require_decision_values(lots["decisions"], {"NOPE:fee_status": "unknown"}, "the class d")


def test_an_operator_is_taken_only_from_the_packages_copy_of_its_own_page_that_holds_the_quotation(wl, lots):
    named = wl.operator_from_text(lots, "op_rules", "Testbetrieb GmbH", RULES_TEXT_MEMBER, OPERATOR_QUOTATION)
    assert named == {"operator": "Testbetrieb GmbH", "url": "https://operator.example/rules", "quotation": OPERATOR_QUOTATION}
    # a JSON extract is searched through its strings (the clinic page is kept as a search extract)
    assert wl.operator_from_text(lots, "op_clinic", "Klinik", CLINIC_MEMBER, CLINIC_QUOTATION)["url"] == (
        "https://clinic.example/visitors")
    with pytest.raises(SystemExit, match="is not in the package member"):
        wl.operator_from_text(lots, "op_rules", "Andere GmbH", RULES_TEXT_MEMBER, "Eigentum der Anderen GmbH")
    with pytest.raises(SystemExit, match="no primary operator source"):
        wl.operator_from_text(lots, "ev_free", "Testbetrieb GmbH", RULES_TEXT_MEMBER, OPERATOR_QUOTATION)
    with pytest.raises(SystemExit, match="no primary operator source"):
        wl.operator_from_text(lots, "unknown_source", "Testbetrieb GmbH", RULES_TEXT_MEMBER, OPERATOR_QUOTATION)


# --------------------------------------------------------------------------- the step with the package

LOT_SPECS = (
    {"facility": "WOB_PARK_A", "class": "a", "record_id": "candidate_wob_lot_a", "rule": "WOB_PARK_A_MOBILE_REFERENCE"},
    {"facility": "WOB_PARK_B1", "class": "b", "garage_id": "wob_lot_b1", "tiers": ("B1_DAY", "B1_NIGHT"),
     "tier_grace": "B1_GRACE", "source": "op_prices",
     "override": "the row encodes a price function although the package advises against one",
     "operator": ("lots_page", "Testbetrieb GmbH", RULES_TEXT_MEMBER, OPERATOR_QUOTATION, "op_rules"),
     "ignored": {"B1_DAY:cap": "needs the validation of the ticket, a customer condition",
                 "B1_PREMIUM": "a premium card, a customer group the model cannot identify"},
     "p10_decisions": ("R-T", {"B1_GRACE:free_minutes_policy": ("unknown",)}),
     "p10_basis": "the regulation states no fee up to 30 minutes and nothing on longer stays", "comment": "Test visitor car park."},
    {"facility": "WOB_PARK_B2", "class": "b", "garage_id": "wob_lot_b2", "grace": ("B2_GRACE", "B2_RATE"),
     "cap": "B2_RATE:cap", "window": None, "source": "op_clinic",
     "override": "the row takes the group tariff before the sub-facility is confirmed",
     "operator": ("lots_page", "Klinik", CLINIC_MEMBER, CLINIC_QUOTATION, "op_clinic"),
     "p10_decisions": ("R-T", {"B2_GRACE:free_minutes_policy": ("unknown",)}),
     "p10_basis": "the page says 'danach'; the deduction is open",
     "p11_decisions": ("R-T", {"WOB_PARK_B2:geometry_match": ("reviewed_evidence",)}),
     "p11_basis": "the sub-facility of the point is unresolved; the group rule is assigned by the owner",
     "comment": "Test clinic car park."},
    {"facility": "WOB_PARK_C", "class": "c", "garage_id": "wob_lot_c", "free": "C_R01", "source": "ev_free",
     "expect": {"WOB_PARK_C:fee_status": "free_confirmed"}, "comment": "Test free car park."},
    {"facility": "WOB_PARK_D", "class": "d", "garage_id": "wob_lot_d", "free_default": "WOB_PARK_D:fee_status",
     "expect": {"WOB_PARK_D:fee_status": "unknown"}},
    {"facility": "WOB_PARK_E", "class": "e", "record_id": "candidate_wob_lot_e", "reason": "user_group_only",
     "note": "the disabled-parking point"},
)


@pytest.fixture()
def step(garages_step, regional_tests, monkeypatch):
    """The step with the synthetic specifications in place of the real ones."""
    specs = garages_step.specs
    monkeypatch.setattr(specs, "GARAGE_SPECS", regional_tests._specs())
    monkeypatch.setattr(specs, "MONTHLY_PRODUCTS", regional_tests.MONTHLY)
    monkeypatch.setattr(specs, "PACKAGE_CANDIDATES", regional_tests.CANDIDATES)
    monkeypatch.setattr(specs, "DIRECTORY_DECISIONS", regional_tests.DIRECTORY)
    monkeypatch.setattr(specs, "LOT_SPECS", LOT_SPECS)
    monkeypatch.setattr(specs, "BS_MONTHLY_SPECS", ())  # the Braunschweig monthly package has its own tests
    monkeypatch.setattr(specs, "BS_RECORDED_SPECS", ())
    monkeypatch.setattr(specs, "LOT_RULE_IDS", RULE_IDS)
    monkeypatch.setattr(specs, "LOT_RELEASED", RELEASED)
    return garages_step


@pytest.fixture(scope="module")
def regional_package(tmp_path_factory, regional_tests):
    """The synthetic regional package whose layer wob_parkplaetze holds the six points of the Wolfsburg package."""
    layers = regional_tests._layers()
    layers["wob_parkplaetze"] = gpd.GeoDataFrame(
        {"facility_id": list(POINTS), "name": [POINTS[facility][1] for facility in POINTS], "tariff_rule_ids": "[]"},
        geometry=[_point(facility) for facility in POINTS], crs=METRIC_CRS)
    directory = tmp_path_factory.mktemp("municipal_2026-10-07")
    return directory, regional_tests._write_package(directory, layers, regional_tests._rules(), regional_tests._facilities())


@pytest.fixture()
def inputs(step, regional_package, lots_package):
    directory, sha256 = regional_package
    return step.load_garage_inputs(directory, expected_sha256=sha256, lots_path=lots_package[0],
                                   expected_lots_sha256=lots_package[1], zones=_zones())


@pytest.fixture()
def rows(step, inputs):
    return step.build_garages(inputs).set_index("garage_id")


def test_the_lot_decisions_are_checked_against_the_points_of_both_packages(step, regional_package, lots_package, regional_tests,
                                                                           monkeypatch):
    directory, sha256 = regional_package
    arguments = dict(expected_sha256=sha256, lots_path=lots_package[0], expected_lots_sha256=lots_package[1], zones=_zones())
    # a point without a decision and a decision without a point
    monkeypatch.setattr(step.specs, "LOT_SPECS", LOT_SPECS[:-1])
    with pytest.raises(SystemExit, match=r"points without a decision \['WOB_PARK_E'\]"):
        step.load_garage_inputs(directory, **arguments)
    ghost = {"facility": "WOB_PARK_X", "class": "e", "record_id": "candidate_x", "reason": "user_group_only", "note": "x"}
    monkeypatch.setattr(step.specs, "LOT_SPECS", LOT_SPECS + (ghost,))
    with pytest.raises(SystemExit, match=r"decisions without a point \['WOB_PARK_X'\]"):
        step.load_garage_inputs(directory, **arguments)
    # the classification needs the zone polygons
    monkeypatch.setattr(step.specs, "LOT_SPECS", LOT_SPECS)
    with pytest.raises(SystemExit, match="pass them"):
        step.load_garage_inputs(directory, expected_sha256=sha256, lots_path=lots_package[0],
                                expected_lots_sha256=lots_package[1])
    # a decision that the data refute stops the step before any row is built
    refuted = tuple({**spec, "class": "c", "free": "C_R01"} if spec["facility"] == "WOB_PARK_D" else spec for spec in LOT_SPECS)
    monkeypatch.setattr(step.specs, "LOT_SPECS", refuted)
    with pytest.raises(SystemExit, match="which class c does not allow"):
        step.load_garage_inputs(directory, **arguments)


def test_the_regional_layer_must_hold_the_same_points_as_the_package(step, regional_tests, lots_package, tmp_path):
    layers = regional_tests._layers()
    moved = [_point(facility) for facility in POINTS]
    moved[0] = Point(X0 + 10_000.5, Y0 + 300)
    layers["wob_parkplaetze"] = gpd.GeoDataFrame({"facility_id": list(POINTS), "name": "Parkplatz", "tariff_rule_ids": "[]"},
                                                 geometry=moved, crs=METRIC_CRS)
    sha256 = regional_tests._write_package(tmp_path / "a", layers, regional_tests._rules(), regional_tests._facilities())
    with pytest.raises(SystemExit, match="lies 0.5.. m from the point of the regional layer wob_parkplaetze"):
        step.load_garage_inputs(tmp_path / "a", expected_sha256=sha256, lots_path=lots_package[0],
                                expected_lots_sha256=lots_package[1], zones=_zones())
    layers["wob_parkplaetze"] = gpd.GeoDataFrame({"facility_id": list(POINTS)[:-1], "name": "Parkplatz", "tariff_rule_ids": "[]"},
                                                 geometry=moved[:-1], crs=METRIC_CRS)
    sha256 = regional_tests._write_package(tmp_path / "b", layers, regional_tests._rules(), regional_tests._facilities())
    with pytest.raises(SystemExit, match="list different points: \\['WOB_PARK_E'\\]"):
        step.load_garage_inputs(tmp_path / "b", expected_sha256=sha256, lots_path=lots_package[0],
                                expected_lots_sha256=lots_package[1], zones=_zones())


def test_a_step_with_lot_decisions_needs_the_package(step, regional_package):
    directory, sha256 = regional_package
    inputs = step.load_garage_inputs(directory, expected_sha256=sha256)
    with pytest.raises(SystemExit, match="--wolfsburg-lots-zip"):
        step.build_garages(inputs)


def test_the_lot_rows_are_surface_lots_priced_from_their_components_by_their_roles(rows, inputs):
    assert list(rows.index)[-4:] == ["wob_lot_b1", "wob_lot_b2", "wob_lot_c", "wob_lot_d"]
    assert set(rows.loc[["wob_lot_b1", "wob_lot_b2", "wob_lot_c", "wob_lot_d"], "facility_kind"]) == {"surface_lot"}
    assert set(rows.drop(["wob_lot_b1", "wob_lot_b2", "wob_lot_c", "wob_lot_d"])["facility_kind"]) == {"garage"}
    # class b, tiers with a grace period: the whole of its duration bands is the closed free band
    b1 = rows.loc["wob_lot_b1"]
    assert b1["tariff_tiers"] == "06:00-18:00 1.00/60; 18:00-06:00 0.50/60" and b1["tariff_duration_bands"] == "0-30 free"
    assert b1["assumptions"] == "P4;P6;P10" and b1["tariff_rule_ids"] == "B1_DAY;B1_NIGHT;B1_GRACE"
    assert pd.isna(b1["garage_hourly_rate_eur"]) and pd.isna(b1["garage_fee_start_h"]) and pd.isna(b1["garage_daily_cap_eur"])
    assert b1["operator"] == "Testbetrieb GmbH" and b1["source_url"] == "https://operator.example/prices"
    assert b1["source_date"] == "2026-10-07" and b1["municipality_ags"] == WOB and b1["municipality"] == "Wolfsburg"
    # class b, a grace period of one unit with a rate and a day cap: a stay of 31 min has two started half hours from its arrival
    b2 = rows.loc["wob_lot_b2"]
    assert b2["tariff_duration_bands"] == "0-30 free; 30-60 total 1.60; 60- 0.80/30" and b2["garage_daily_cap_eur"] == 5.0
    assert (b2["garage_fee_start_h"], b2["garage_fee_end_h"]) == (0.0, 24.0) and b2["tariff_tiers"] is None
    assert b2["assumptions"] == "P4;P5;P8;P10;P11" and b2["tariff_rule_ids"] == "B2_GRACE;B2_RATE;B2_RATE:cap"
    assert b2["operator"] == "Klinik" and b2["source_url"] == "https://clinic.example/visitors"
    # class c: free for every stay, no assumption; the capacity the package states with its scope
    c = rows.loc["wob_lot_c"]
    assert c["tariff_duration_bands"] == "0- free" and c["assumptions"] is None and c["tariff_rule_ids"] == "C_R01"
    assert (c["capacity_reported"], c["capacity_scope"]) == (120, CAPACITY_SCOPE)
    assert (c["garage_fee_start_h"], c["garage_fee_end_h"]) == (0.0, 24.0) and pd.isna(c["garage_hourly_rate_eur"])
    assert c["source_url"] == "https://free.example/lot" and c["operator"] is None
    # class d: the municipal free default under ASSUMPTION P12, the field decision of the fee status is its evidence
    d = rows.loc["wob_lot_d"]
    assert d["tariff_duration_bands"] == "0- free" and d["assumptions"] == "P12"
    assert d["tariff_rule_ids"] == "WOB_PARK_D:fee_status" and d["source_url"] == GEOVIEWER and pd.isna(d["capacity_reported"])
    pg.validate_garages(rows.reset_index().pipe(lambda frame: gpd.GeoDataFrame(frame, geometry="geometry", crs=METRIC_CRS)))


def test_the_positions_the_provenance_and_the_package_hashes_of_the_lot_rows(rows, inputs):
    for garage_id, facility in (("wob_lot_b1", "WOB_PARK_B1"), ("wob_lot_b2", "WOB_PARK_B2"), ("wob_lot_c", "WOB_PARK_C"),
                                ("wob_lot_d", "WOB_PARK_D")):
        row = rows.loc[garage_id]
        assert row.geometry.equals(_point(facility)) and row["package_facility_id"] == facility
        assert row["geometry_method"] == "geoviewer_car_park_point" and row["geometry_source_url"] == GEOVIEWER
        # the regional package (it holds the layer, checked) first, then the Wolfsburg car-park package
        assert row["package_sha256"] == f"{inputs['file']['sha256']};{inputs['lots']['file']['sha256']}"
    assert rows.loc["wob_lot_b1", "name"] == "Parkplatz Besucher" and rows.loc["wob_lot_d", "name"] == "Parkplatz"


def test_the_notes_name_every_assumption_the_evidence_and_what_is_not_used(rows):
    b1 = rows.loc["wob_lot_b1", "notes"]
    for phrase in ("Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip", "geoviewer_car_park_point", "no entrance",
                   "Class b of spec E14", "full_cost_calculation_ready=false", "ASSUMPTION P4", "ASSUMPTION P6",
                   "ASSUMPTION P10", "tiers per started 60 min",
                   "a stay of at most 30 min is free (grace period)", "the free minutes are not deducted",
                   "Basis of the reading: the regulation states no fee up to 30 minutes",
                   "B1_DAY:cap: 6.00 EUR at most [not preferred for current use] (needs the validation of the ticket",
                   "B1_PREMIUM: free [not preferred for current use] (a premium card", "Operator Testbetrieb GmbH, named by its "
                   "own page https://operator.example/rules", "Test visitor car park.",
                   "The package states no capacity"):
        assert phrase in b1, phrase
    b2 = rows.loc["wob_lot_b2", "notes"]
    for phrase in ("ASSUMPTION P11", "ASSUMPTION P5", "ASSUMPTION P8", "ASSUMPTION P10", "observed_group_rules_not_assigned",
                   "B2_GRACE, B2_RATE", "conditional_on_subfacility_match", "day boundary of the cap B2_RATE:cap is not stated"):
        assert phrase in b2, phrase
    c = rows.loc["wob_lot_c", "notes"]
    for phrase in ("Class c of spec E14", "free of charge for every stay", "package statement: Free of charge",
                   "Capacity 120 (Test car park", "Test free car park."):
        assert phrase in c, phrase
    d = rows.loc["wob_lot_d", "notes"]
    for phrase in ("Class d of spec E14", "ASSUMPTION P12", "municipal free default", "the field decision WOB_PARK_D:fee_status "
                   "states the fee status unknown", "inside none of the 2 published tariff areas", "nearest zone"):
        assert phrase in d, phrase
    assert all(note.isascii() for note in rows["notes"])


def test_a_row_that_goes_against_a_package_recommendation_quotes_it_and_names_the_owner_direction(rows):
    # spec E14 amendment (2): every class d row and every row with an override; a row that follows the package carries none
    for garage_id, recommendation, what in (
            ("wob_lot_b1", "Erfassen, aber keine praezise Preisfunktion ergaenzen.",
             "the row encodes a price function although the package advises against one"),
            ("wob_lot_b2", "Erst bei bestaetigter Unteranlage als Punkttarif uebernehmen.",
             "the row takes the group tariff before the sub-facility is confirmed"),
            ("wob_lot_d", "Gebuehrenstatus unbekannt belassen; keine 0-Euro-Regel anlegen.",
             "the row takes the municipal free default of ASSUMPTION P12 although the package keeps the fee status unknown")):
        note = rows.loc[garage_id, "notes"]
        sentence = (f"The package recommends against this value ('{recommendation}', facility_review.json of the package); "
                    f"{what}; the owner direction 'alle integrieren' (spec E14 and its amendment, as R-4b2-8 for the "
                    "follow-up package) overrides the recommendation.")
        assert sentence in note, garage_id
    assert "recommends against" not in rows.loc["wob_lot_c", "notes"]


def test_the_override_sentence_needs_a_recommendation_to_quote_and_exists_only_for_an_override_or_a_class_d(step, inputs):
    spec_b = next(spec for spec in LOT_SPECS if spec["facility"] == "WOB_PARK_B1")
    spec_c = next(spec for spec in LOT_SPECS if spec["facility"] == "WOB_PARK_C")
    review = {"recommendation": "Eine Empfehlung."}
    assert "('Eine Empfehlung.', facility_review.json" in step.lot_override_sentence(spec_b, review)
    assert step.lot_override_sentence(spec_c, review) is None
    assert step.lot_override_sentence({**spec_b, "override": None}, review) is None
    with pytest.raises(SystemExit, match="holds none to quote"):
        step.lot_override_sentence(spec_b, {"recommendation": None})


@pytest.mark.parametrize("facility, spec_class, change, message", [
    ("WOB_PARK_B1", "b", {"p10_basis": None}, "states the basis of its reading in 'p10_basis'"),
    ("WOB_PARK_B1", "b", {"p10_decisions": None}, "states the field decisions of its ruling in 'p10_decisions'"),
    ("WOB_PARK_B1", "b", {"source": "ev_free"}, "is no source of the component B1_DAY"),
    ("WOB_PARK_B1", "b", {"source": "nowhere"}, "the package holds no source nowhere"),
    ("WOB_PARK_B1", "b", {"ignored": {"NOPE": "x"}}, r"the components \['NOPE'\] are not tariff components"),
    ("WOB_PARK_B1", "b", {"p10_decisions": ("R-T", {"B1_GRACE:free_minutes_policy": ("documented_component",)})},
     "field decision B1_GRACE:free_minutes_policy has the status 'unknown'"),
    ("WOB_PARK_B2", "b", {"p11_basis": None},
     "states the field decisions of ASSUMPTION P11 but the row does not rest on it"),
    ("WOB_PARK_C", "c", {"expect": {"WOB_PARK_C:fee_status": "unknown"}}, "WOB_PARK_C:fee_status states 'free_confirmed'"),
    ("WOB_PARK_D", "d", {"expect": {"WOB_PARK_D:fee_status": "paid_confirmed"}}, "WOB_PARK_D:fee_status states 'unknown'"),
])
def test_a_lot_specification_that_the_package_does_not_support_stops_the_step(step, inputs, monkeypatch, facility, spec_class,
                                                                              change, message):
    changed = tuple({**spec, **{key: value for key, value in change.items() if value is not None},
                     **{key: None for key, value in change.items() if value is None}} if spec["facility"] == facility else spec
                    for spec in LOT_SPECS)
    monkeypatch.setattr(step.specs, "LOT_SPECS", changed)
    with pytest.raises(SystemExit, match=message):
        step.build_garages(inputs)


def test_p11_needs_an_observed_group_rule_that_the_package_leaves_unassigned(step, inputs):
    for rule_id in ("B2_RATE", "B2_RATE:cap", "B2_GRACE"):
        inputs["rules"][rule_id]["assigned"] = True
    with pytest.raises(SystemExit, match="no component of the row is an observed group rule that the package leaves unassigned"):
        step.build_garages(inputs)


def test_a_capacity_without_its_scope_is_never_guessed(step, inputs):
    inputs["lots"]["review"]["WOB_PARK_C"]["capacity_scope"] = None
    with pytest.raises(SystemExit, match="states the capacity 120 without a scope"):
        step.build_garages(inputs)


def test_a_free_component_that_states_a_free_period_is_no_free_car_park(step, inputs):
    inputs["rules"]["C_R01"]["free_period_minutes"] = 30
    with pytest.raises(SystemExit, match="states a free period or an end"):
        step.build_garages(inputs)
    inputs["rules"]["C_R01"]["free_period_minutes"] = None
    inputs["rules"]["C_R01"]["amount_eur"] = 0.5
    with pytest.raises(SystemExit, match="is no free component"):
        step.build_garages(inputs)


def test_the_tiered_grace_period_needs_a_free_period_that_the_tier_rules_agree_with(step, inputs):
    inputs["rules"]["B1_NIGHT"]["free_period_minutes"] = 20
    with pytest.raises(SystemExit, match="the tier rule B1_NIGHT states a free period of 20 min but the free rule B1_GRACE ends"):
        step.build_garages(inputs)
    inputs["rules"]["B1_NIGHT"]["free_period_minutes"] = 30
    inputs["rules"]["B1_GRACE"]["rule_type"] = "increment"
    with pytest.raises(SystemExit, match="is no free period"):
        step.build_garages(inputs)


def test_the_tier_grace_form_needs_the_tiers_and_a_free_form_excludes_every_other(step, inputs):
    spec = {"garage_id": "x", "facility": "WOB_PARK_B1", "class": "b", "rate": "B1_DAY", "tier_grace": "B1_GRACE", "window": None}
    with pytest.raises(SystemExit, match="'tier_grace' is the grace period of the tiered form and needs 'tiers'"):
        step.encode_tariff(spec, inputs["rules"])
    with pytest.raises(SystemExit, match="exactly one of"):
        step.encode_tariff({"garage_id": "x", "free": "C_R01", "rate": "B1_DAY"}, inputs["rules"])


# --------------------------------------------------------------------------- the QA rows


def _frame_and_rows(step, inputs, tmp_path, monkeypatch, tariffs=None):
    frame = step.build_garages(inputs)
    directory = step.load_directory(_directory(tmp_path, step, monkeypatch))
    return frame, step.qa_rows(inputs, frame, directory, _tariffs() if tariffs is None else tariffs)


def _directory(tmp_path, step, monkeypatch, names=("Parkplatz Markthalle", "Parkplatz Werder")) -> Path:
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.52, 52.26]},
                 "properties": {"name": name, "id": f"id{number}", "mapsightIconId": "parkflaeche",
                                "description": "<p>Tarife:</p><p>Parkzone 1 0,90 \u20ac / 30 Min.</p>"}}
                for number, name in enumerate(names)]
    path = tmp_path / "directory.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    monkeypatch.setattr(step, "DIRECTORY_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_every_point_has_one_qa_row_the_aggregated_row_is_gone_and_the_table_validates(step, inputs, tmp_path, monkeypatch):
    frame, rows = _frame_and_rows(step, inputs, tmp_path, monkeypatch)
    by_id = {row["record_id"]: row for row in rows}
    assert "candidate_wob_car_parks" not in by_id
    # garage rows for the classes b, c and d (priced, no reason), candidate rows for a and e
    for garage_id in ("wob_lot_b1", "wob_lot_b2", "wob_lot_c", "wob_lot_d"):
        assert by_id[f"garage_{garage_id}"]["record_type"] == "garage" and by_id[f"garage_{garage_id}"]["decision"] == "priced"
    assert by_id["garage_wob_lot_b1"]["note"].startswith("tiers per started 60 min, the tier in force at the unit's start: "
                                                          "06:00-18:00 1.00 EUR, 18:00-06:00 0.50 EUR; a stay of at most 30 min "
                                                          "is free (grace period)")
    assert "(P4, P6, P10); The package recommends against this value" in by_id["garage_wob_lot_b1"]["note"]
    assert by_id["garage_wob_lot_d"]["note"].startswith("free of charge for every stay by the municipal default") and \
        "(P12); The package recommends against this value" in by_id["garage_wob_lot_d"]["note"]
    assert by_id["garage_wob_lot_c"]["note"] == "free of charge for every stay (the free schedule 0- free, the fee window 0-24 h " \
                                               "is formal)"
    a, e = by_id["candidate_wob_lot_a"], by_id["candidate_wob_lot_e"]
    assert (a["record_type"], a["decision"], a["reason_code"], a["count"]) == ("candidate", "not_listed",
                                                                                "zone_street_product", "1")
    assert (a["zone_ids"], a["amount_eur"], a["municipality_ags"]) == ("wob_zone_t1", "2.00", WOB)
    assert e["reason_code"] == "user_group_only" and e["note"] == "the disabled-parking point" and e["count"] == "1"
    assert a["subject"] == "Parkplatz A (WOB_PARK_A)"
    assert a["evidence"] == "WOB_PARK_A; WOB_PARK_A_MOBILE_REFERENCE; area WOB_MOBILE_1"
    path = tmp_path / "qa.csv"
    step.write_garage_qa(path, rows)
    loaded = pq.load_garage_qa(path)
    pq.validate_garage_qa(loaded, frame, _tariffs())


def test_the_consistency_check_of_a_car_park_inside_a_zone_is_stated_in_its_qa_note(step, inputs, tmp_path, monkeypatch):
    _, rows = _frame_and_rows(step, inputs, tmp_path, monkeypatch)
    note = next(row["note"] for row in rows if row["record_id"] == "candidate_wob_lot_a")
    for phrase in ("inside the zone wob_zone_t1", "strictly to the published tariff area Area One (WOB_MOBILE_1, fee zone 1",
                   "ruling R-E1, no double role", "published hourly reference 2.00 EUR against the street rate 2.00 EUR per hour "
                   "of wob_zone_t1 (fee window 7-18 h): equal"):
        assert phrase in note, phrase
    # a reference that differs from the zone's street rate is named and the zone tariff is not changed
    differing = _tariffs()
    differing.loc[0, "hourly_rate_eur"] = 1.5
    frame, rows = _frame_and_rows(step, inputs, tmp_path, monkeypatch, differing)
    note = next(row["note"] for row in rows if row["record_id"] == "candidate_wob_lot_a")
    assert "DIFFERS by 0.50 EUR (the zone tariff is not changed here)" in note
    qa = pd.DataFrame(rows, columns=list(pq.GARAGE_QA_COLUMNS))
    pq.validate_garage_qa(qa, frame, differing)   # a difference is a finding, no error
    assert pq.zone_reference_summary(qa, differing) == {"checked": 1, "equal": 0, "differing": ["candidate_wob_lot_a"]}
    assert pq.zone_reference_summary(qa, _tariffs()) == {"checked": 1, "equal": 1, "differing": []}


def test_a_car_park_inside_a_zone_needs_the_tariff_table_and_a_matching_component_and_area(step, inputs, tmp_path, monkeypatch):
    frame = step.build_garages(inputs)
    directory = step.load_directory(_directory(tmp_path, step, monkeypatch))
    with pytest.raises(SystemExit, match="pass the tariff table"):
        step.qa_rows(inputs, frame, directory)
    with pytest.raises(SystemExit, match="holds no hourly street rate of the zone wob_zone_t1"):
        step.qa_rows(inputs, frame, directory, _tariffs().iloc[1:])
    inputs["lots"]["rules"]["WOB_PARK_A_MOBILE_REFERENCE"]["fee_zone"] = 2
    with pytest.raises(SystemExit, match="does not belong to the component"):
        step.qa_rows(inputs, frame, directory, _tariffs())
    inputs["lots"]["rules"]["WOB_PARK_A_MOBILE_REFERENCE"]["fee_zone"] = 1
    inputs["lots"]["rules"]["WOB_PARK_A_MOBILE_REFERENCE"]["rule_type"] = "published_hourly_rate"
    with pytest.raises(SystemExit, match="is no published hourly reference of this point"):
        step.qa_rows(inputs, frame, directory, _tariffs())


def test_the_reference_is_the_amount_per_hour_whatever_the_billing_unit(step, inputs, tmp_path, monkeypatch):
    # the package states the reference per billing unit (60 min); a reference per 30 min is scaled to the hour
    inputs["lots"]["rules"]["WOB_PARK_A_MOBILE_REFERENCE"].update(amount_eur=1.0, billing_unit_minutes=30)
    _, rows = _frame_and_rows(step, inputs, tmp_path, monkeypatch)
    row = next(row for row in rows if row["record_id"] == "candidate_wob_lot_a")
    assert row["amount_eur"] == "2.00" and "equal" in row["note"]


def test_the_rates_name_the_surface_lots_the_free_rows_and_the_package(step, inputs, capsys):
    step.build_garages(inputs)
    out = capsys.readouterr().out
    assert "[garages] Wolfsburg car-park package: 4 rows (surface lots), 4 priced from its components or by the municipal free " \
           "default" in out
    # 11 rows: the 7 garages of the regional specifications (6 priced, one without a tariff) and the 4 lots
    assert "free 2/10" in out and "by facility kind: garage 7 listed, 6 priced, surface_lot 4 listed, 4 priced" in out
    assert "priced garages resting on ASSUMPTION P12: 1/10" in out and "priced garages resting on ASSUMPTION P10: 2/10" in out
    summary = pg.coverage(step.build_garages(inputs))
    assert summary["priced_free"] == 2 and summary["priced_tiered"] == 2 and summary["by_facility_kind"]["surface_lot"] == {
        "listed": 4, "priced": 4, "not_priced": 0}


def test_two_runs_of_the_step_write_identical_bytes(step, regional_package, lots_package, tmp_path, monkeypatch):
    directory, sha256 = regional_package
    written = []
    for run in ("first", "second"):
        (tmp_path / run).mkdir()
        inputs = step.load_garage_inputs(directory, expected_sha256=sha256, lots_path=lots_package[0],
                                         expected_lots_sha256=lots_package[1], zones=_zones())
        frame = step.build_garages(inputs)
        rows = step.qa_rows(inputs, frame, step.load_directory(_directory(tmp_path / run, step, monkeypatch)), _tariffs())
        pg.write_garages(frame, tmp_path / run / "garages.geojson", members=step.dataset_members())
        step.write_garage_qa(tmp_path / run / "qa.csv", rows)
        written.append((tmp_path / run / "garages.geojson").read_bytes() + (tmp_path / run / "qa.csv").read_bytes())
    assert written[0] == written[1]
    document = json.loads((tmp_path / "first" / "garages.geojson").read_text(encoding="utf-8"))
    assert document["features"][-1]["properties"]["facility_kind"] == "surface_lot"
    assert document["features"][-1]["properties"]["package_sha256"].count(";") == 1


def test_the_positions_of_the_lot_rows_are_checked_against_the_municipality(step, inputs, regional_tests):
    frame = step.build_garages(inputs)
    step.check_positions(frame, regional_tests._municipalities())
    moved = frame.copy()
    moved.loc[moved["garage_id"] == "wob_lot_b1", "geometry"] = Point(X0 + 5000, Y0)
    with pytest.raises(SystemExit, match=r"garages outside their municipality: wob_lot_b1 \(03103000, \d+ m outside\)"):
        step.check_positions(moved, regional_tests._municipalities())


# --------------------------------------------------------------------------- the committed rows (spec E14, ruling R-4b3-3)

COMMITTED_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
HOUR_S = 3600
LOTS_SHA256 = "650508f87c80ee2b84063def301ff67b5ecb8f2653cef2ab4202fb398c818df0"


def _hms(hours: int, minutes: int = 0, seconds: int = 0) -> int:
    return hours * 3600 + minutes * 60 + seconds


def _value(cell):
    """A cell as a plain value: None for an empty cell."""
    return None if cell is None or cell is pd.NA or (isinstance(cell, float) and cell != cell) else cell


@pytest.fixture(scope="module")
def committed():
    """The committed garage dataset, loaded and validated."""
    garages = pg.load_garages(COMMITTED_DIR / "parking_garages_2026.geojson")
    pg.validate_garages(garages)
    return garages.set_index("garage_id", drop=False)


@pytest.fixture(scope="module")
def committed_qa():
    return pq.load_garage_qa(COMMITTED_DIR / "parking_garages_2026_qa.csv").set_index("record_id", drop=False)


def test_the_committed_lot_rows_carry_the_values_the_sources_state(committed, regional_tests):
    pins = regional_tests.PRICED_LOTS
    lots = committed[committed["facility_kind"] == "surface_lot"]
    assert sorted(lots.index) == sorted(pins) and len(lots) == 13
    for garage_id, expected in pins.items():
        row = lots.loc[garage_id]
        assert row["package_facility_id"] == "WOB_PARK_" + garage_id.removeprefix("wob_lot_")
        assert (row["municipality_ags"], row["municipality"]) == (WOB, "Wolfsburg") and bool(row["priced"])
        assert (row["geometry_method"], row["geometry_source_url"]) == ("geoviewer_car_park_point", GEOVIEWER)
        assert (row["tariff_tiers"], row["tariff_duration_bands"]) == (expected.tiers, expected.bands)
        assert _value(row["garage_daily_cap_eur"]) == expected.cap
        assert _value(row["garage_fee_start_h"]) == expected.start and _value(row["garage_fee_end_h"]) == expected.end
        assert pd.isna(row["garage_hourly_rate_eur"]) and pd.isna(row["monthly_eur"]) and pd.isna(row["garage_first_period_min"])
        assert _value(row["assumptions"]) == expected.assumptions
        capacity = _value(row["capacity_reported"])
        assert (None if capacity is None else int(capacity)) == expected.capacity
        assert len(row["package_sha256"].split(";")) == 2 and row["package_sha256"].endswith(LOTS_SHA256)
    # the capacity is stated with its scope (P1 only), never guessed; only the Volkswagen Arena has one
    arena = lots.loc["wob_lot_1441813"]
    assert arena["capacity_reported"] == 494 and arena["capacity_scope"].startswith("Volkswagen Arena P1")
    assert lots["capacity_reported"].notna().sum() == 1
    # the operator only where the package's copy of its own page names it
    assert dict(lots["operator"].dropna()) == {"wob_lot_1900571": "Autostadt GmbH", "wob_lot_1966086": "Klinikum Wolfsburg"}


def test_the_municipal_free_default_names_p12_and_its_evidence_and_the_other_free_rows_name_no_assumption(committed):
    defaults = [garage_id for garage_id, row in committed.iterrows()
                if isinstance(row["assumptions"], str) and "P12" in row["assumptions"].split(";")]
    assert sorted(defaults) == sorted(f"wob_lot_{number}" for number in (262165, 327689, 327697, 524301, 589853, 720902,
                                                                           1703940, 1703946))
    for garage_id in defaults:
        row = committed.loc[garage_id]
        assert row["assumptions"] == "P12" and row["tariff_rule_ids"] == f"{row['package_facility_id']}:fee_status"
        for phrase in ("ASSUMPTION P12", "municipal free default", "fee status unknown", "inside none of the 73 published "
                       "tariff areas", "inside none of the committed zones", "no operator tariff"):
            assert phrase in row["notes"], (garage_id, phrase)
    for garage_id in ("wob_lot_1441813", "wob_lot_1835028", "wob_lot_1900551"):
        row = committed.loc[garage_id]
        assert pd.isna(row["assumptions"]) and "ASSUMPTION" not in row["notes"]
        assert row["tariff_rule_ids"] == f"{row['package_facility_id']}_R01"
    # the Volkswagen Arena: match days are not modelled (a named reading), the capacity is that of P1
    arena = committed.loc["wob_lot_1441813", "notes"]
    for phrase in ("Match days are NOT MODELLED (reading)", "reserved for holders of a parking permit", "Capacity 494"):
        assert phrase in arena, phrase


def test_the_autostadt_and_klinikum_rows_name_their_grace_period_the_conditions_not_used_and_the_open_questions(committed):
    autostadt = committed.loc["wob_lot_1900571", "notes"]
    for phrase in ("ASSUMPTION P10", "ASSUMPTION P4", "ASSUMPTION P6", "a stay of at most 30 min is free (grace period)",
                   "OP_AUTOSTADT_P2_DAY:cap: 6.00 EUR at most", "Welcome Desk", "customer condition",
                   "OP_AUTOSTADT_P2_PREMIUM", "OP_AUTOSTADT_COLLECTED_CAR", "contradict each other", "Operator Autostadt GmbH",
                   "visitor car park P2", "short-stay car park PK"):
        assert phrase in autostadt, phrase
    klinikum = committed.loc["wob_lot_1966086", "notes"]
    for phrase in ("ASSUMPTION P10", "ASSUMPTION P11", "ASSUMPTION P5", "ASSUMPTION P8", "observed_group_rules_not_assigned",
                   "partial_subfacility_unresolved", "HTTP 502", "search extract", "two started half hours", "1.60 EUR",
                   "10.00 EUR per day, is a different facility and is not used", "Operator Klinikum Wolfsburg",
                   "inside the zone wob_tarifzone_2", "maximum per stay"):
        assert phrase in klinikum, phrase
    assert committed.loc["wob_lot_1966086", "garage_daily_cap_eur"] == 5.0


def test_the_committed_rows_that_go_against_a_package_recommendation_name_it_and_the_owner_direction(committed, committed_qa):
    owner = "the owner direction 'alle integrieren' (spec E14 and its amendment, as R-4b2-8 for the follow-up package) " \
            "overrides the recommendation."
    for number in (262165, 589853, 720902, 1703940, 1703946):
        assert ("The package recommends against this value ('Als Pruefpunkt erhalten; keinen Preis und keine Gebuehrenfreiheit "
                "in ein Tarifmodell uebernehmen.") in committed.loc[f"wob_lot_{number}", "notes"]
    for number in (327689, 327697):
        assert ("('Gebuehren und Bedingungen vor Ort feststellen; bis dahin keine Kostenberechnung.', facility_review.json"
                in committed.loc[f"wob_lot_{number}", "notes"])
    assert "('Gebuehrenstatus und alle Tarifwerte unbekannt belassen; keine 0-Euro-Regel anlegen.', facility_review.json" in \
        committed.loc["wob_lot_524301", "notes"]
    for garage_id in [f"wob_lot_{number}" for number in (262165, 327689, 327697, 524301, 589853, 720902, 1703940, 1703946)] + [
            "wob_lot_1900571", "wob_lot_1966086"]:
        assert owner in committed.loc[garage_id, "notes"], garage_id
        assert "The package recommends against this value" in committed_qa.loc[f"garage_{garage_id}", "note"], garage_id
    assert "Keine praezise Preisfunktion ergaenzen" in committed.loc["wob_lot_1900571", "notes"]
    assert "Erst bei bestaetigter Unteranlagenidentitaet" in committed.loc["wob_lot_1966086", "notes"]
    for garage_id in ("wob_lot_1441813", "wob_lot_1835028", "wob_lot_1900551"):
        assert "recommends against" not in committed.loc[garage_id, "notes"]
    # the weakest assumption names why the published areas are no proof of a free car park
    assert "section 2(2) of the ordinance defines Zone II as all other streets and places" in pg.ASSUMPTIONS["P12"]
    assert "the weakest assumption of the dataset" in committed.loc["wob_lot_262165", "notes"]


def test_the_committed_autostadt_access_is_unresolved_and_the_klinikum_p10_basis_quotes_both_operator_wordings(committed):
    autostadt = committed.loc["wob_lot_1900571", "notes"]
    assert ("ACCESS: listed as a public option under E14 (b) by the owner direction; the sources contradict each other on "
            "access (introduction of the Parkplatzordnung: visitors of the Autostadt only; section 3: P1 to P3 open to all "
            "persons); not resolved.") in autostadt
    klinikum = committed.loc["wob_lot_1966086", "notes"]
    for phrase in ("'bis zu 30 Minuten: kostenfrei; jede weitere halbe Stunde: 0,80'", "'danach 0,80 Euro je halbe Stunde'",
                   "a stay of 31 min costs 1.60 EUR (two started half hours from its arrival) under P10 and 0.80 EUR under "
                   "the deduction reading"):
        assert phrase in klinikum, phrase


def _tariff(committed, garage_id):
    from braunschweig.parking.tariff_export import garage_row_to_tariff
    return garage_row_to_tariff(committed.loc[garage_id])


@pytest.mark.parametrize("arrival_s, stay_s, expected_cents", [
    # the grace period of 30 min: the end of the grace period is free, one second more is billed from the arrival
    (_hms(10), _hms(0, 30), 0),
    (_hms(10), _hms(0, 30, 1), 100),
    (_hms(10), _hms(0, 31), 100),
    (_hms(10), _hms(1), 100),
    (_hms(10), _hms(1, 1), 200),
    # the day/night boundary at 18:00: 17:45 for 30 min crosses it and is free; 17:45 for 31 min is one unit that starts in
    # the day tier (100 ct); 17:30 for 90 min has the units 17:30 (day, 100 ct) and 18:30 (night, 50 ct); a unit that starts
    # exactly at 18:00 is a night unit (18:00 for 31 min = 50 ct); 17:59:59 is still day (100 ct)
    (_hms(17, 45), _hms(0, 30), 0),
    (_hms(17, 45), _hms(0, 31), 100),
    (_hms(17, 30), _hms(1, 30), 150),
    (_hms(18), _hms(0, 31), 50),
    (_hms(17, 59, 59), _hms(0, 31), 100),
    # the boundary at 06:00: 05:30 for 31 min is a night unit (50 ct), 06:00 for 31 min a day unit (100 ct)
    (_hms(5, 30), _hms(0, 31), 50),
    (_hms(6), _hms(0, 31), 100),
    # a night stay of 8 h = 8 night units; the maximum of 6.00 EUR (a customer condition) is NOT used: 10:00-22:00 = 8 day
    # units and 4 night units = 1000 ct
    (_hms(22), _hms(8), 400),
    (_hms(10), _hms(12), 1000),
])
def test_the_autostadt_tariff_at_its_edges_is_priced_by_the_garage_option_code(committed, arrival_s, stay_s, expected_cents):
    from braunschweig.parking import cost
    garage = _tariff(committed, "wob_lot_1900571")
    assert garage.form == "tiers" and garage.daily_cap_cents is None and garage.bands[0].to_min == 30
    assert cost.garage_metered_cents(garage, arrival_s, arrival_s + stay_s) == expected_cents


@pytest.mark.parametrize("stay_s, expected_cents", [
    # 30 min free; a longer stay is billed from its arrival in started half hours (1.60 EUR for two); the cap is 5.00 EUR:
    # 1801 s is the first second beyond the grace period; 61 min starts a third half hour (2.40 EUR); 180 min = 6 half hours
    # (4.80 EUR), 181 min = 7 (5.60 EUR, capped at 5.00 EUR)
    (0, 0), (1800, 0), (1801, 160), (_hms(0, 31), 160), (_hms(1), 160), (_hms(1, 1), 240), (_hms(1, 30), 240),
    (_hms(1, 31), 320), (_hms(2), 320), (_hms(3), 480), (_hms(3, 1), 500), (_hms(3, 30), 500), (_hms(8), 500),
])
def test_the_klinikum_tariff_at_its_edges_is_priced_by_the_garage_option_code(committed, stay_s, expected_cents):
    from braunschweig.parking import cost
    garage = _tariff(committed, "wob_lot_1966086")
    assert garage.form == "bands" and garage.daily_cap_cents == 500
    assert cost.garage_metered_cents(garage, _hms(9), _hms(9) + stay_s) == expected_cents


def test_the_free_rows_price_every_stay_at_zero_through_the_same_code_without_a_special_case(committed):
    from braunschweig.parking import cost
    free = [garage_id for garage_id, row in committed.iterrows() if row["tariff_duration_bands"] == "0- free"]
    assert len(free) == 11
    for garage_id in free:
        garage = _tariff(committed, garage_id)
        assert garage.form == "bands" and garage.hourly_rate_cents is None and garage.daily_cap_cents is None
        assert garage.to_json()["bands"] == [{"from_min": 0, "to_min": None, "kind": "free", "price_cents": 0, "unit_min": None}]
        for stay_s in (0, 60, 5 * HOUR_S, 30 * HOUR_S):
            assert cost.garage_option_cents(garage, _hms(9), _hms(9) + stay_s, purpose="work") == 0


def _street_zone():
    from braunschweig.parking.cost import ZoneTariff
    return ZoneTariff(zone_id="test_street", zone_type="street_paid", hourly_rate_cents=180, billing_unit_min=1,
                      free_if_stay_at_most_min=None, first_period_min=None, first_period_cents=None, daily_cap_cents=900,
                      max_stay_min=None, long_stay_product_cents=None, member_day_cents=None, guest_day_cents=None,
                      fee_start_s=32400, fee_end_s=72000, resident_exempt=False)


def test_a_committed_free_option_lowers_the_expected_cost_of_a_paid_street_stay_within_the_maximum_distance(committed):
    from braunschweig.parking import cost
    theater = _tariff(committed, "wob_lot_1900551")
    # the destination lies 500 m (a 300-400-500 triangle) from the free Theaterparkplatz; lambda = 400 m
    x, y = theater.x_m + 300.0, theater.y_m + 400.0
    options = cost.garage_options_in_range([theater], x, y)
    assert [(garage.garage_id, distance) for garage, distance in options] == [("wob_lot_1900551", 500.0)]
    # stay 10:00-11:00 = 60 chargeable min: street 60 x 3 ct = 180 ct; w = exp(-1.25) = 0.2865047969,
    # P_garage = w / (1 + w) = 0.2227001388, expected = 180 / 1.2865047969 = 139.91 -> 140 ct (the street alone: 180 ct)
    street = _street_zone()
    assert cost.parking_cost_with_garages(street, _hms(10), _hms(11), purpose="shop", parking_free=False,
                                          resident_of_zone=False)[0] == 180
    cents, outcome, probability = cost.parking_cost_with_garages(
        street, _hms(10), _hms(11), purpose="shop", parking_free=False, resident_of_zone=False, garage_options=options,
        decay_m=400.0)
    assert (cents, outcome) == (140, cost.PAID_EXPECTED) and probability == pytest.approx(0.2227001388, abs=1e-9)


def test_a_committed_free_option_alone_never_makes_a_street_free_stay_cost_anything_e4(committed):
    from braunschweig.parking import cost
    options = [(_tariff(committed, garage_id), 0.0) for garage_id in ("wob_lot_1900551", "wob_lot_1835028", "wob_lot_262165")]
    street = _street_zone()
    # before the street fee window (09:00-20:00) and after it the street is free: nobody pays a garage, a free one adds nothing
    for arrival_s, departure_s in ((_hms(7), _hms(8)), (_hms(21), _hms(22))):
        assert cost.parking_cost_with_garages(street, arrival_s, departure_s, purpose="shop", parking_free=False,
                                              resident_of_zone=False, garage_options=options, decay_m=400.0) == (
            0, cost.OUTSIDE_FEE_HOURS, 0.0)
    # a priced option beside the free ones never changes a free street either (the Autostadt would cost 100 ct)
    paid = options + [(_tariff(committed, "wob_lot_1900571"), 0.0)]
    assert cost.parking_cost_with_garages(street, _hms(7), _hms(8), purpose="shop", parking_free=False, resident_of_zone=False,
                                          garage_options=paid, decay_m=400.0)[0] == 0


def test_the_committed_qa_table_has_one_row_per_wolfsburg_point_and_the_zone_check_is_nine_of_nine(committed, committed_qa):
    tariffs = pz.load_tariffs(COMMITTED_DIR / "parking_tariffs_2026.csv")
    assert "candidate_wob_car_parks" not in committed_qa.index
    in_zone = {"524303": ("wob_tarifzone_1", "2.00"), "720901": ("wob_tarifzone_1", "2.00"),
               "786434": ("wob_tarifzone_1", "2.00"), "983050": ("wob_tarifzone_2", "1.20"),
               "1245204": ("wob_tarifzone_3", "1.00"), "1638410": ("wob_tarifzone_2", "1.20"),
               "1703957": ("wob_tarifzone_3", "1.00"), "1769502": ("wob_tarifzone_1", "2.00"),
               "1835014": ("wob_tarifzone_2", "1.20")}
    for number, (zone_id, reference) in in_zone.items():
        row = committed_qa.loc[f"candidate_wob_lot_{number}"]
        assert (row["record_type"], row["decision"], row["reason_code"], row["count"]) == (
            "candidate", "not_listed", "zone_street_product", "1")
        assert (row["zone_ids"], row["amount_eur"], row["municipality_ags"]) == (zone_id, reference, WOB)
        assert f"{reference} EUR against the street rate {reference} EUR per hour of {zone_id}" in row["note"]
        assert "equal" in row["note"] and "no double role" in row["note"]
    summary = pq.zone_reference_summary(committed_qa.reset_index(drop=True), tariffs)
    assert summary == {"checked": 9, "equal": 9, "differing": []}
    theater, bade = committed_qa.loc["candidate_wob_lot_1048584"], committed_qa.loc["candidate_wob_lot_1114127"]
    assert theater["reason_code"] == "user_group_only" and "disabled-parking" in theater["note"]
    assert bade["reason_code"] == "customer_regime" and "Zone I" in bade["note"] and "NOT modelled" in bade["note"]
    # every one of the 24 points has exactly one row: 13 garage rows (priced), 9 candidates of the zone street product, 2 others
    garage_rows = [record for record in committed_qa.index if record.startswith("garage_wob_lot_")]
    assert len(garage_rows) == 13 and all(committed_qa.loc[record, "decision"] == "priced" for record in garage_rows)
    assert len([record for record in committed_qa.index if record.startswith("candidate_wob_lot_")]) == 11
