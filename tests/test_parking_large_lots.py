"""Large surface lots as off-street options (parking cost zones v2, spec Amendment I, issue #436, Task 4j).

The owner's research package ``Grossparkplaetze_ZGB8_2026-10-12.zip`` is gitignored, so this optional input of the curation step
``regional_garages.py`` (``large_lots.py``) is pinned on a synthetic package written the way the owner supplied it (a zip with
``MANIFEST.json``, the JSON tables ``facilities``, ``tariff_rules``, ``sources`` and ``summary``, the research points as GeoJSON in
EPSG:4326, the crosswalk of the 24 Wolfsburg points and the alias file) with one record of every branch of the policy.

What is pinned, each rule by a test that fails when the rule is removed: the verification of the package (the pinned hash, every
member against the manifest, a member the manifest does not list, a missing member, a duplicated key, a rule of an unknown
facility, a shortlist flag that the policy does not reproduce); the size and access policy of spec Amendment I2 at the three
thresholds (every reason, a reported capacity before an estimate, a missing capacity that is unknown and never 0, the owner
decision of public access); the conversion of the package's components to the rule schema of the encoder (the role of each
component, the stated started unit, the cap rule, a component that sets no value, an explicit 0); the street product of a zone
(spec Amendment I1) with its consistency check and the zones that the schedule of a lot cannot carry; the median product of
ASSUMPTION P14 (spec Amendment I4) by zone, then by municipality, and its refusals; the evidence gate of the free Wolfsburg lots
(spec Amendment I5a); the rows and QA rows of ``build_large_lots`` (the rates are logged, a tariff that the grammar cannot
express is a named QA row and never approximated, an option without an owner decision stops the step); the dataset and the QA
table validate. The primary path is pinned on the real package where it exists (the counts of the policy, the rows of the
release), independently of the code.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import random
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from pyproj import Transformer
from shapely.geometry import Point, box

from braunschweig.parking import cost
from braunschweig.parking import garage_qa as pq
from braunschweig.parking import garages as pg
from braunschweig.parking import tariff_export as te
from braunschweig.parking import zones as pz
from tests.restricted_parking_data import parking_data_path

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
COMMITTED_PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
METRIC_CRS = "EPSG:25832"
PACKAGE = "Grossparkplaetze_ZGB8_2026-10-12"
REAL_PACKAGE_SHA256 = "d467275ced5b68717ff2f426ad94be4debd085a9bebfaa239cfa6f4518fc9db4"
X0, Y0 = 604_000.0, 5_790_000.0
BS, WOB, SZ, GS, HE, PE = "03101000", "03103000", "03102000", "03153017", "03154028", "03157006"
TO_WGS84 = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
SOURCE_URL = "https://op.example/prices"
SCHEDULE_MO_FR = {"timezone": "Europe/Berlin", "public_holiday_policy": None, "raw": "Mo-Fr 09:00-16:00; Sa 09:00-13:00",
                  "completeness": "explicit_windows_only_other_days_not_inferred",
                  "weekly_windows": [{"days": ["MO", "TU", "WE", "TH", "FR"], "start": "09:00", "end": "16:00"},
                                     {"days": ["SA"], "start": "09:00", "end": "13:00"}]}
SCHEDULE_MO_SA = {"timezone": "Europe/Berlin", "public_holiday_policy": None, "raw": "Mo-Sa 10:00-18:00",
                  "completeness": "explicit_windows_only_other_days_not_inferred",
                  "weekly_windows": [{"days": ["MO", "TU", "WE", "TH", "FR", "SA"], "start": "10:00", "end": "18:00"},
                                     {"days": ["SA"], "start": "10:00", "end": "18:00"}]}
SCHEDULE_UNPARSED = {"timezone": "Europe/Berlin", "public_holiday_policy": None, "raw": "24/7", "weekly_windows": None,
                     "completeness": "unparsed_or_weekdays_unknown"}


@pytest.fixture(scope="module")
def garages_step():
    """The curation step as a module (it imports its siblings, ``large_lots`` among them, from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("regional_garages_for_large_lots", CURATION_DIR / "regional_garages.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture(scope="module")
def ll(garages_step):
    return garages_step.ll


@pytest.fixture(scope="module")
def regional_tests():
    """The helpers of the regional test file (its synthetic regional package), loaded from the file."""
    spec = importlib.util.spec_from_file_location("regional_garages_tests_for_large_lots", Path(__file__).with_name(
        "test_parking_regional_garages.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- the synthetic package


def _xy_to_lonlat(dx: float, dy: float) -> list:
    longitude, latitude = TO_WGS84.transform(X0 + dx, Y0 + dy)
    return [longitude, latitude]


def _flags(record: dict) -> dict:
    """The shortlist flags of the package, written independently of ``large_lots.decide`` (the rule of the package's own
    ``build_package.py``: public access, open, a surface lot, no candidate-only record, a known capacity of at least n)."""
    selected = record.get("capacity_selected_for_threshold")
    public = record["access_class"] in ("public", "public_with_customer_discount", "mixed_public_and_customers")
    active = record["access_class"] != "closed" and not record.get("exclude_current_inventory") \
        and record.get("operational_status", "operational") not in ("closed", "planned", "closed_redeveloped")
    surface = record["facility_type"] == "surface_lot"
    candidate_only = bool(record.get("candidate_only") or record.get("alias_of"))
    return {f"public_candidate_{n}": bool(public and active and surface and not candidate_only and selected is not None
                                         and selected >= n) for n in (30, 50, 100)}


def _facility(key: str, city: str = "Goslar", **fields) -> dict:
    record = {"facility_key": key, "name": f"Lot {key}", "city": city, "facility_type": "surface_lot", "access_class": "public",
              "access_notes": "", "capacity_reported": 80, "capacity_selected_for_threshold": 80,
              "capacity_selection_method": "reported", "capacity_evidence_level": "primary",
              "capacity_applies_to": "named_facility_once_not_each_geometry_component", "tariff_rule_ids": [],
              "source_ids": ["src_prices"], "coordinate_source_ids": ["src_prices"], "osm_ids": [],
              "price_readiness": "evidence_components_require_model_mapping", "notes": f"note of {key}"}
    record.update(fields)
    record.update(_flags(record))
    return record


def _rule(rule_id: str, facility: str, amount=None, **fields) -> dict:
    rule = {"rule_id": rule_id, "facility_id": facility, "amount_eur": amount, "billing_period": None,
            "billing_unit_minutes": None, "paid_hours": None, "max_stay_minutes": None, "daily_cap_eur": None,
            "monthly_price_eur": None, "conditions": "", "source_ids": ["src_prices"], "currency": "EUR",
            "retrieved_at": "2026-10-12", "schedule": None, "rounding": None}
    rule.update(fields)
    return rule


#: key -> (facility fields, point offset in m from (X0, Y0) or None, the rules)
def _catalogue() -> dict:
    """The records of the synthetic package, one per branch of the policy and of the tariff forms."""
    return {
        # Braunschweig: two lots with the street product of the zone t_ia, a lot without a stated tariff (P14)
        "t_zone_a": (_facility("t_zone_a", "Braunschweig", capacity_selected_for_threshold=101, capacity_reported=101),
                     (100, 100),
                     [_rule("t_zone_a__1", "t_zone_a", 0.9, billing_period="30 Minuten", billing_unit_minutes=30)]),
        "t_zone_b": (_facility("t_zone_b", "Braunschweig", capacity_selected_for_threshold=60, capacity_reported=None,
                               capacity_selection_method="estimated_floor_area_m2_divided_by_25",
                               capacity_evidence_level="assumption"), (150, 100),
                     [_rule("t_zone_b__1", "t_zone_b", 0.9, billing_period="30 Minuten", billing_unit_minutes=30)]),
        "t_p14_zone": (_facility("t_p14_zone", "Braunschweig", capacity_selected_for_threshold=74, capacity_reported=74),
                       (120, 150), [_rule("t_p14_zone__1", "t_p14_zone", None, conditions="no general tariff stated")]),
        # Goslar: free, a rate with a cap, an explicit 0 without a billing period
        "t_free": (_facility("t_free", capacity_selected_for_threshold=100, capacity_reported=None,
                             capacity_selection_method="osm_integer_capacity", capacity_evidence_level="osm"), (3000, 0),
                   [_rule("t_free__1", "t_free", 0, billing_period="free", daily_cap_eur=0)]),
        "t_zero": (_facility("t_zero"), (3100, 0), [_rule("t_zero__1", "t_zero", 0)]),
        "t_rate": (_facility("t_rate", capacity_selected_for_threshold=70, capacity_reported=70), (3200, 0),
                   [_rule("t_rate__1", "t_rate", 1.0, billing_period="started_hour", billing_unit_minutes=60,
                          daily_cap_eur=8.0, schedule=SCHEDULE_MO_SA, max_stay_minutes=120)]),
        "t_rate_free_day": (_facility("t_rate_free_day"), (3300, 0),
                            [_rule("t_rate_free_day__1", "t_rate_free_day", 2.0, billing_period="hour",
                                   billing_unit_minutes=60, schedule=SCHEDULE_UNPARSED)]),
        # Helmstedt: a free short stay (a grace band) with a rate and a day ticket
        "t_grace": (_facility("t_grace", "Helmstedt"), (5000, 0),
                    [_rule("t_grace__1", "t_grace", 0.6, billing_period="started_interval", billing_unit_minutes=30,
                           schedule=SCHEDULE_MO_FR, max_stay_minutes=180),
                     _rule("t_grace__2", "t_grace", 0, billing_period="free_short_stay", billing_unit_minutes=30,
                           schedule=SCHEDULE_MO_FR, max_stay_minutes=30),
                     _rule("t_grace__3", "t_grace", 3.0, billing_period="day_ticket", daily_cap_eur=3.0,
                           schedule=SCHEDULE_MO_FR)]),
        "t_free_he": (_facility("t_free_he", "Helmstedt"), (5100, 0),
                      [_rule("t_free_he__1", "t_free_he", 0, billing_period="free")]),
        "t_p14_he": (_facility("t_p14_he", "Helmstedt", capacity_selected_for_threshold=75, capacity_reported=75), (5200, 0),
                     [_rule("t_p14_he__1", "t_p14_he", None)]),
        # Salzgitter: a duration schedule (two lots with the same schedule), one lot without a stated tariff outside every zone
        "t_bands": (_facility("t_bands", "Salzgitter", capacity_selected_for_threshold=123, capacity_reported=123), (8000, 0),
                    [_rule("t_bands__1", "t_bands", 0, billing_period="short_stay", billing_unit_minutes=30,
                           max_stay_minutes=30, schedule=SCHEDULE_MO_FR),
                     _rule("t_bands__2", "t_bands", 0.7, billing_period="first_hour", billing_unit_minutes=60,
                           schedule=SCHEDULE_MO_FR),
                     _rule("t_bands__3", "t_bands", 0.1, billing_period="subsequent_unit", billing_unit_minutes=6,
                           schedule=SCHEDULE_MO_FR)]),
        "t_bands2": (_facility("t_bands2", "Salzgitter", capacity_selected_for_threshold=90, capacity_reported=90),
                     (8100, 0),
                     [_rule("t_bands2__1", "t_bands2", 0, billing_period="short_stay", billing_unit_minutes=30,
                            max_stay_minutes=30, schedule=SCHEDULE_MO_FR),
                      _rule("t_bands2__2", "t_bands2", 0.7, billing_period="first_hour", billing_unit_minutes=60,
                            schedule=SCHEDULE_MO_FR),
                      _rule("t_bands2__3", "t_bands2", 0.1, billing_period="subsequent_unit", billing_unit_minutes=6,
                            schedule=SCHEDULE_MO_FR)]),
        "t_p14_mun": (_facility("t_p14_mun", "Salzgitter", capacity_selected_for_threshold=89, capacity_reported=None,
                                capacity_selection_method="estimated_floor_area_m2_divided_by_25",
                                capacity_evidence_level="assumption"), (8500, 0),
                      [_rule("t_p14_mun__1", "t_p14_mun", None)]),
        # Peine: a mixed paid and free site, a lot without a stated tariff and no priced lot to take a median of
        "t_mixed": (_facility("t_mixed", "Peine", capacity_selected_for_threshold=300, capacity_reported=300,
                              price_readiness="requires_subarea_split"), (12000, 0),
                    [_rule("t_mixed__1", "t_mixed", 0.5, billing_period="started_interval", billing_unit_minutes=30)]),
        "t_p14_none": (_facility("t_p14_none", "Peine", capacity_selected_for_threshold=55, capacity_reported=55), (12100, 0),
                       [_rule("t_p14_none__1", "t_p14_none", None)]),
        # Wolfsburg: a zone product that the schedule of a lot cannot carry (the zone has a first period)
        "t_gap": (_facility("t_gap", "Wolfsburg", capacity_selected_for_threshold=96, capacity_reported=None,
                            capacity_selection_method="estimated_floor_area_m2_divided_by_25"), (16000, 0),
                  [_rule("t_gap__1", "t_gap", 1.0, billing_period="30 Minuten", billing_unit_minutes=30)]),
        # the records that are no option
        "t_small": (_facility("t_small", capacity_selected_for_threshold=20, capacity_reported=20), (3400, 0), []),
        "t_nocap": (_facility("t_nocap", capacity_selected_for_threshold=None, capacity_reported=None,
                              capacity_selection_method="unknown"), (3500, 0), []),
        "t_customers": (_facility("t_customers", access_class="customers_only"), (3600, 0), []),
        "t_closed": (_facility("t_closed", access_class="closed"), (3700, 0), []),
        "t_group": (_facility("t_group", facility_type="parking_site_group"), (3800, 0), []),
        "t_alias": (_facility("t_alias", alias_of="t_free"), (3900, 0), []),
        "t_candidate": (_facility("t_candidate", candidate_only=True), (4000, 0), []),
        "t_nopos": (_facility("t_nopos", capacity_selected_for_threshold=90, capacity_reported=90), None,
                    [_rule("t_nopos__1", "t_nopos", 0, billing_period="free")]),
        # the Wolfsburg lots whose free status rests on a per-site source (spec I5a): the package keeps the access unknown
        "WOB_PARK_E1": (_facility("WOB_PARK_E1", "Wolfsburg", access_class="unknown", capacity_selected_for_threshold=71,
                                  capacity_reported=None, capacity_selection_method="estimated_floor_area_m2_divided_by_25",
                                  capacity_evidence_level="assumption"), (16500, 0), []),
        "WOB_PARK_E2": (_facility("WOB_PARK_E2", "Wolfsburg", access_class="unknown", capacity_selected_for_threshold=68,
                                  capacity_reported=None, capacity_selection_method="estimated_floor_area_m2_divided_by_25",
                                  capacity_evidence_level="assumption"), (16600, 0), []),
        "WOB_PARK_E3": (_facility("WOB_PARK_E3", "Wolfsburg", access_class="unknown", capacity_selected_for_threshold=40,
                                  capacity_reported=None, capacity_selection_method="estimated_floor_area_m2_divided_by_25",
                                  capacity_evidence_level="assumption"), (16700, 0), []),
    }


CROSSWALK = [{"facility_key": "WOB_PARK_E1", "name": "Parkplatz E1"}, {"facility_key": "WOB_PARK_SUB", "name": "Subpunkt"},
             {"facility_key": "WOB_PARK_ALIAS", "name": "Alias"}]
ALIASES = {"WOB_PARK_SUB": "t_free", "WOB_PARK_ALIAS": "t_free"}


def _write_package(directory: Path, catalogue: dict = None, *, summary_count=None, mutate=None, aliases=None,
                   crosswalk=None) -> tuple:
    """Write the package zip the way the owner supplied it and return (path, SHA-256). ``mutate`` is called with the members
    ({name: bytes}) after the manifest is computed, so a test can break one member."""
    catalogue = catalogue or _catalogue()
    facilities = [fields for fields, _, _ in catalogue.values()]
    rules = [rule for _, _, rule_list in catalogue.values() for rule in rule_list]
    for fields, _, rule_list in catalogue.values():
        fields["tariff_rule_ids"] = [rule["rule_id"] for rule in rule_list]
    features = [{"type": "Feature", "id": key, "properties": {"facility_key": key},
                 "geometry": None if offset is None else {"type": "Point", "coordinates": _xy_to_lonlat(*offset)}}
                for key, (_, offset, _) in catalogue.items()]
    sources = [{"id": "src_prices", "url": SOURCE_URL, "retrieved_at": "2026-10-12"}]
    count = sum(bool(fields["public_candidate_50"]) for fields in facilities) if summary_count is None else summary_count
    members = {
        "INTEGRATION.md": b"integration", "README_EINSTIEG_PARKPLATZDATEN.md": b"readme",
        "data/facilities.json": json.dumps({"schema_version": "1", "as_of": "2026-10-13", "facilities": facilities}).encode(),
        "data/tariff_rules.json": json.dumps({"schema_version": "1", "rules": rules}).encode(),
        "data/sources.json": json.dumps(sources).encode(),
        "data/summary.json": json.dumps({"public_candidates_50": count}).encode(),
        "data/researched_locations.geojson": json.dumps({"type": "FeatureCollection", "features": features}).encode(),
        "review/wolfsburg_24_geometry_crosswalk.json": json.dumps(CROSSWALK if crosswalk is None else crosswalk).encode(),
        "review/municipal_point_aliases.json": json.dumps(ALIASES if aliases is None else aliases).encode(),
    }
    manifest = {"package_name": PACKAGE, "files": [{"path": name, "size_bytes": len(content),
                                                    "sha256": hashlib.sha256(content).hexdigest()}
                                                   for name, content in sorted(members.items())]}
    members["MANIFEST.json"] = json.dumps(manifest).encode()
    if mutate is not None:
        mutate(members)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{PACKAGE}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(f"{PACKAGE}/{name}", content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def package_file(tmp_path):
    return _write_package(tmp_path / "pkg")


# --------------------------------------------------------------------------- the package is verified before it is read


def test_a_changed_or_missing_package_is_refused(ll, package_file, tmp_path):
    path, sha256 = package_file
    with pytest.raises(SystemExit, match="missing"):
        ll.load_package(tmp_path / "absent.zip", sha256)
    with pytest.raises(SystemExit, match="a changed package is never read"):
        ll.load_package(path, "0" * 64)
    assert ll.LOTS_SHA256 == REAL_PACKAGE_SHA256


def test_a_member_that_differs_from_its_manifest_entry_is_refused(ll, tmp_path):
    def mutate(members):
        members["data/summary.json"] = b'{"public_candidates_50": 0}'

    path, sha256 = _write_package(tmp_path / "m", mutate=mutate)
    with pytest.raises(SystemExit, match=r"package member data/summary.json has the SHA-256 .* but MANIFEST.json states"):
        ll.load_package(path, sha256)


def test_a_member_the_manifest_does_not_list_or_a_missing_member_is_refused(ll, tmp_path):
    def add(members):
        members["data/extra.json"] = b"{}"

    path, sha256 = _write_package(tmp_path / "a", mutate=add)
    with pytest.raises(SystemExit, match=r"not in the manifest \['data/extra.json'\]"):
        ll.load_package(path, sha256)

    def drop(members):
        del members["INTEGRATION.md"]

    path, sha256 = _write_package(tmp_path / "d", mutate=drop)
    with pytest.raises(SystemExit, match=r"not in the zip \['INTEGRATION.md'\]"):
        ll.load_package(path, sha256)
    # a member that is read must be listed, present and as recorded, with or without the whole-package check
    path, sha256 = _write_package(tmp_path / "e", mutate=drop)
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(SystemExit, match="INTEGRATION.md is listed in MANIFEST.json but missing from the zip"):
            ll._read_member(archive, {"INTEGRATION.md": "x"}, "INTEGRATION.md")
        with pytest.raises(SystemExit, match="is not listed in MANIFEST.json; a member that the manifest does not vouch for"):
            ll._read_member(archive, {}, "data/summary.json")


def test_a_package_without_a_manifest_is_refused(ll, tmp_path):
    def drop(members):
        del members["MANIFEST.json"]

    path, sha256 = _write_package(tmp_path / "n", mutate=drop)
    with pytest.raises(SystemExit, match="holds no MANIFEST.json"):
        ll.load_package(path, sha256)


def test_the_package_is_read_into_records_rules_sources_and_points_in_the_metric_crs(ll, package_file):
    path, sha256 = package_file
    package = ll.load_package(path, sha256)
    catalogue = _catalogue()
    assert list(package["facilities"]) == list(catalogue) and package["file"]["sha256"] == sha256
    assert package["rules"]["t_rate__1"]["daily_cap_eur"] == 8.0
    assert package["sources"]["src_prices"] == {"id": "src_prices", "url": SOURCE_URL, "date": "2026-10-12"}
    assert package["points"].crs.to_epsg() == 25832 and "t_nopos" not in package["points"].index
    assert len(package["points"]) == len(catalogue) - 1
    expected = Point(X0 + 3200, Y0)
    assert package["points"].loc["t_rate", "geometry"].distance(expected) < 0.001
    assert package["aliases"] == ALIASES and [point["facility_key"] for point in package["crosswalk"]] == [
        "WOB_PARK_E1", "WOB_PARK_SUB", "WOB_PARK_ALIAS"]


def test_a_duplicated_key_a_rule_of_an_unknown_facility_and_an_unknown_component_are_refused(ll, tmp_path):
    base = _catalogue()

    def twice(members):
        document = json.loads(members["data/facilities.json"])
        document["facilities"].append(document["facilities"][0])
        members["data/facilities.json"] = json.dumps(document).encode()
        _rehash(members, "data/facilities.json")

    path, sha256 = _write_package(tmp_path / "t", base, mutate=twice)
    with pytest.raises(SystemExit, match="facilities.json holds the facility t_zone_a twice"):
        ll.load_package(path, sha256)

    def orphan(members):
        document = json.loads(members["data/tariff_rules.json"])
        document["rules"].append(dict(document["rules"][0], rule_id="x__1", facility_id="nobody"))
        members["data/tariff_rules.json"] = json.dumps(document).encode()
        _rehash(members, "data/tariff_rules.json")

    path, sha256 = _write_package(tmp_path / "o", _catalogue(), mutate=orphan)
    with pytest.raises(SystemExit, match="belongs to the facility nobody"):
        ll.load_package(path, sha256)

    def doubled(members):
        document = json.loads(members["data/tariff_rules.json"])
        document["rules"].append(document["rules"][0])
        members["data/tariff_rules.json"] = json.dumps(document).encode()
        _rehash(members, "data/tariff_rules.json")

    path, sha256 = _write_package(tmp_path / "r", _catalogue(), mutate=doubled)
    with pytest.raises(SystemExit, match="tariff_rules.json holds the rule t_zone_a__1 twice"):
        ll.load_package(path, sha256)

    def lost(members):
        document = json.loads(members["data/facilities.json"])
        document["facilities"][0]["tariff_rule_ids"] = ["nowhere__1"]
        members["data/facilities.json"] = json.dumps(document).encode()
        _rehash(members, "data/facilities.json")

    path, sha256 = _write_package(tmp_path / "l", _catalogue(), mutate=lost)
    with pytest.raises(SystemExit, match=r"lists the tariff components \['nowhere__1'\]"):
        ll.load_package(path, sha256)


def _rehash(members: dict, name: str) -> None:
    """Record the mutated member in the manifest, so that the mutation is about its content and not about the manifest."""
    manifest = json.loads(members["MANIFEST.json"])
    for entry in manifest["files"]:
        if entry["path"] == name:
            entry["sha256"] = hashlib.sha256(members[name]).hexdigest()
    members["MANIFEST.json"] = json.dumps(manifest).encode()


def test_a_position_that_is_no_point_is_refused(ll, tmp_path):
    def polygon(members):
        document = json.loads(members["data/researched_locations.geojson"])
        document["features"][0]["geometry"] = {"type": "Polygon", "coordinates": [[[10, 52], [10, 53], [11, 53], [10, 52]]]}
        members["data/researched_locations.geojson"] = json.dumps(document).encode()
        _rehash(members, "data/researched_locations.geojson")

    path, sha256 = _write_package(tmp_path / "p", mutate=polygon)
    with pytest.raises(SystemExit, match="the geometry of t_zone_a is a Polygon; a position stays a point"):
        ll.load_package(path, sha256)

    def unknown(members):
        document = json.loads(members["data/researched_locations.geojson"])
        document["features"][0]["properties"]["facility_key"] = "unknown_key"
        members["data/researched_locations.geojson"] = json.dumps(document).encode()
        _rehash(members, "data/researched_locations.geojson")

    path, sha256 = _write_package(tmp_path / "u", mutate=unknown)
    with pytest.raises(SystemExit, match="holds a point of unknown_key, which facilities.json does not hold"):
        ll.load_package(path, sha256)


def test_the_policy_and_the_shortlist_flags_of_the_package_must_agree(ll, tmp_path):
    def flag(members):
        document = json.loads(members["data/facilities.json"])
        record = next(item for item in document["facilities"] if item["facility_key"] == "t_small")
        record["public_candidate_50"] = True  # a 20-space lot on the shortlist: a changed package or a wrong policy
        members["data/facilities.json"] = json.dumps(document).encode()
        _rehash(members, "data/facilities.json")

    path, sha256 = _write_package(tmp_path / "f", mutate=flag)
    with pytest.raises(SystemExit, match=r"the policy of spec Amendment I2 and the shortlist flags of the package disagree.*"
                                         r"t_small@50"):
        ll.load_package(path, sha256)
    path, sha256 = _write_package(tmp_path / "s", summary_count=99)
    with pytest.raises(SystemExit, match="summary of the package states 99 candidates at 50"):
        ll.load_package(path, sha256)


# --------------------------------------------------------------------------- the size and access policy (spec I2, L50)


def _record(**fields) -> dict:
    return _facility("probe", **fields)


@pytest.mark.parametrize("fields, threshold, reason", [
    ({}, 50, None),
    ({"capacity_selected_for_threshold": 50}, 50, None),                       # the threshold itself is in
    ({"capacity_selected_for_threshold": 49}, 50, "lot_below_threshold"),
    ({"capacity_selected_for_threshold": 49}, 30, None),                       # the sensitivity at 30
    ({"capacity_selected_for_threshold": 99}, 100, "lot_below_threshold"),     # the sensitivity at 100
    ({"capacity_selected_for_threshold": 100}, 100, None),
    ({"capacity_selected_for_threshold": 0}, 30, "lot_below_threshold"),       # a known 0 is below the threshold
    ({"capacity_selected_for_threshold": None}, 50, "lot_capacity_unknown"),   # unknown, never 0
    ({"access_class": "public_with_customer_discount"}, 50, None),
    ({"access_class": "mixed_public_and_customers"}, 50, None),
    ({"access_class": "customers_only"}, 50, "lot_access_not_public"),
    ({"access_class": "event"}, 50, "lot_access_not_public"),
    ({"access_class": "monthly_only"}, 50, "lot_access_not_public"),
    ({"access_class": "permit_restricted"}, 50, "lot_access_not_public"),
    ({"access_class": "unknown"}, 50, "lot_access_not_public"),
    ({"access_class": "closed"}, 50, "lot_not_open"),
    ({"operational_status": "closed_redeveloped"}, 50, "lot_not_open"),
    ({"exclude_current_inventory": True}, 50, "lot_not_open"),
    ({"facility_type": "unresolved_street_or_surface_lot"}, 50, "lot_not_surface_lot"),
    ({"facility_type": "parking_site_group"}, 50, "lot_not_surface_lot"),
    ({"alias_of": "other"}, 50, "lot_alias"),
    ({"candidate_only": True}, 50, "lot_unconfirmed_candidate"),
    ({"evidence_class": "secondary_only"}, 50, "lot_unconfirmed_candidate"),
])
def test_the_policy_decides_every_record_by_access_and_capacity(ll, fields, threshold, reason):
    assert ll.decide(_record(**fields), threshold) == reason


def test_the_owner_decision_of_public_access_replaces_the_package_access_class_only(ll):
    record = _record(access_class="unknown", facility_key="probe")
    assert ll.decide(record, 50) == "lot_access_not_public"
    assert ll.decide(record, 50, owner_public={"probe"}) is None
    # the size policy still applies to a lot whose access the owner decided
    small = _record(access_class="unknown", capacity_selected_for_threshold=10)
    assert ll.decide(small, 50, owner_public={"probe"}) == "lot_below_threshold"
    assert ll.decide(_record(access_class="closed"), 50, owner_public={"probe"}) == "lot_not_open"


def test_a_capacity_that_is_not_a_whole_number_of_spaces_stops_the_step(ll):
    with pytest.raises(SystemExit, match="no non-negative whole number of spaces"):
        ll.capacity_for_threshold(_record(capacity_selected_for_threshold=-3))
    with pytest.raises(SystemExit, match="no non-negative whole number of spaces"):
        ll.capacity_for_threshold(_record(capacity_selected_for_threshold=40.5))
    assert ll.capacity_for_threshold(_record(capacity_selected_for_threshold=40.0)) == 40


def test_the_capacity_is_named_with_its_origin_and_an_unknown_one_is_not_a_zero(ll):
    assert ll.capacity_phrase(_record()) == "capacity 80 for the threshold (reported, evidence level primary)"
    unknown = _record(capacity_selected_for_threshold=None, capacity_selection_method="unknown")
    assert ll.capacity_phrase(unknown) == "capacity unknown (unknown)"


# --------------------------------------------------------------------------- the components in the encoder's schema


def _converted(ll, package_file, key, roles=None, used=()):
    path, sha256 = package_file
    package = ll.load_package(path, sha256)
    return package, ll.convert_rules(package, key, roles or {}, set(used))


def test_a_component_is_typed_by_its_role_and_only_a_used_one_is_preferred(ll, package_file):
    package, rules = _converted(ll, package_file, "t_grace", used=("t_grace__1", "t_grace__2"))
    rate, free = rules["t_grace__1"], rules["t_grace__2"]
    assert (rate["rule_type"], rate["amount_eur"], rate["billing_unit_minutes"], rate["rounding"]) == (
        "increment", 0.6, 30, "started_unit")  # the package's started interval is a stated rounding
    assert (free["rule_type"], free["amount_eur"], free["elapsed_from_minutes"], free["elapsed_to_minutes"]) == (
        "free_period", None, 0, 30)  # the free short stay is a free band of the length of its maximum stay
    assert rate["preferred_for_current_use"] and free["preferred_for_current_use"]
    assert not rules["t_grace__3"]["preferred_for_current_use"]  # a day ticket that sets no value of the row
    # the weekday window of the schedule: Monday to Friday explicit, the Saturday window beside it
    assert rate["charging_times"]["monday"] == [{"start": "09:00", "end": "16:00", "crosses_midnight": False}]
    assert rate["charging_times"]["saturday"] == [{"start": "09:00", "end": "13:00", "crosses_midnight": False}]
    # a component with a daily cap gets a cap rule of its own that is used only where the decision names it
    cap = rules["t_grace__3:cap"]
    assert (cap["rule_type"], cap["daily_cap_eur"], cap["cap_period"], cap["preferred_for_current_use"]) == (
        "cap", 3.0, None, False)
    package, used = _converted(ll, package_file, "t_grace", used=("t_grace__3:cap",))
    assert used["t_grace__3:cap"]["preferred_for_current_use"]


def test_the_rounding_is_stated_only_where_the_package_states_a_started_unit(ll, package_file):
    _, rules = _converted(ll, package_file, "t_rate_free_day", used=("t_rate_free_day__1",))
    assert rules["t_rate_free_day__1"]["rounding"] is None  # 'hour': the package says the rounding is not published (P4)
    assert rules["t_rate_free_day__1"]["charging_times"]["monday"] is None  # a schedule that is not explicit gives no window
    _, rules = _converted(ll, package_file, "t_bands", roles={"t_bands__3": {"type": "increment", "from": 60, "to": None,
                                                                        "rounding": "started_unit"}},
                          used=("t_bands__3",))
    assert rules["t_bands__3"]["rounding"] == "started_unit"   # the decision may state it where the text does
    assert (rules["t_bands__3"]["elapsed_from_minutes"], rules["t_bands__3"]["elapsed_to_minutes"]) == (60, None)


def test_an_explicit_zero_is_a_free_component_and_a_free_billing_period_too(ll, package_file):
    _, zero = _converted(ll, package_file, "t_zero", used=("t_zero__1",))
    _, free = _converted(ll, package_file, "t_free", used=("t_free__1",))
    for rules, rule_id in ((zero, "t_zero__1"), (free, "t_free__1")):
        assert rules[rule_id]["rule_type"] == "free" and rules[rule_id]["amount_eur"] is None
    assert "t_free__1:cap" not in free  # a daily cap of 0 of a free component is no cap


def test_a_component_that_is_neither_free_nor_a_rate_needs_a_decision_of_its_role(ll, package_file):
    with pytest.raises(SystemExit, match="t_p14_zone__1 .* is no free component, no free short stay and no rate"):
        _converted(ll, package_file, "t_p14_zone", used=("t_p14_zone__1",))
    # a component that sets no value is typed as the package types it and never inferred
    _, rules = _converted(ll, package_file, "t_p14_zone")
    assert rules["t_p14_zone__1"]["rule_type"] == "not_used" and not rules["t_p14_zone__1"]["preferred_for_current_use"]


def test_a_decision_that_names_a_component_the_facility_does_not_hold_is_refused(ll, package_file):
    with pytest.raises(SystemExit, match=r"names the components \['nowhere__1'\], which the facility does not hold"):
        _converted(ll, package_file, "t_free", roles={"nowhere__1": {"type": "free"}}, used=("t_free__1",))


# --------------------------------------------------------------------------- the street product of a zone (spec I1)


def _tariffs() -> pd.DataFrame:
    columns = ["zone_id", "hourly_rate_eur", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
               "first_period_eur", "daily_cap_eur", "fee_start_h", "fee_end_h", "max_stay_min", "long_stay_product_eur"]
    nan = float("nan")
    return pd.DataFrame([["t_ia", 1.8, 1, nan, nan, nan, nan, 9.0, 20.0, 180, 9.0],
                         ["t_hbf", 2.2, 30, nan, nan, nan, 11.0, 0.0, 24.0, nan, nan],
                         ["t_first", 1.0, 6, 30, 60, 0.7, nan, 10.0, 18.0, nan, nan],
                         ["t_firstonly", 1.0, 30, nan, 60, 1.5, nan, 10.0, 18.0, nan, nan],
                         ["t_cents", 1.0, 7, nan, nan, nan, nan, 8.0, 18.0, nan, nan],
                         ["t_none", nan, nan, nan, nan, nan, nan, 8.0, 18.0, nan, nan],
                         ["t_capms", 1.0, 30, nan, nan, nan, 5.0, 8.0, 18.0, 180, 4.2],
                         ["t_msonly", 1.0, 30, nan, nan, nan, nan, 8.0, 18.0, 120, nan]], columns=columns)


def test_the_street_product_of_a_zone_is_its_rate_per_unit_its_window_and_its_cap(ll):
    product = ll.zone_product_rules(_tariffs(), "t_hbf", "lot")
    rate, cap = product["rules"][product["rate"]], product["rules"][product["cap"]]
    assert (rate["amount_eur"], rate["billing_unit_minutes"], rate["rounding"]) == (1.1, 30, "started_unit")  # 2.20 EUR/h
    assert rate["charging_times"]["friday"] == [{"start": "00:00", "end": "24:00", "crosses_midnight": False}]
    assert rate["charging_times"]["saturday"] is None and cap["daily_cap_eur"] == 11.0
    assert rate["preferred_for_current_use"] and cap["preferred_for_current_use"]
    minute = ll.zone_product_rules(_tariffs(), "t_ia", "lot")
    assert minute["cap"] is None and minute["rules"][minute["rate"]]["amount_eur"] == 0.03  # per started minute
    # the maximum stay of the zone with its long-stay product (assumption M1, ruling R-4j-6) are the two bands of its schedule: the
    # rate from 0 min to the maximum stay and the long-stay product as one open total band after it
    rate_rule, long_stay = minute["rules"][minute["rate"]], minute["rules"][minute["long_stay"]]
    assert (rate_rule["elapsed_from_minutes"], rate_rule["elapsed_to_minutes"]) == (0, 180)
    assert (long_stay["rule_type"], long_stay["amount_eur"], long_stay["elapsed_from_minutes"],
            long_stay["elapsed_to_minutes"]) == ("duration_total", 9.0, 180, None)
    assert long_stay["preferred_for_current_use"] and long_stay["charging_times"] == rate_rule["charging_times"]
    assert minute["form"] == {"bands": ["lot:zone_rate", "lot:zone_long_stay"], "window": "rate"}
    plain = ll.zone_product_rules(_tariffs(), "t_hbf", "lot")
    assert plain["long_stay"] is None and plain["form"] == {"rate": "lot:zone_rate", "window": "rate", "cap": "lot:zone_cap"}
    assert plain["max_stay_min"] is None and minute["max_stay_min"] is None   # the bands carry the maximum, the column stays empty


def test_a_zone_with_a_maximum_stay_and_no_long_stay_product_gives_the_plain_rate_form_and_carries_the_maximum(ll):
    # spec v3 B1, ruling R-B-2: the lot takes the zone's maximum stay as its own (column max_stay_min) instead of being refused; it
    # compares the ELAPSED stay, the street the chargeable minutes, so the lot is never less restrictive than the street (R-B-11)
    product = ll.zone_product_rules(_tariffs(), "t_msonly", "lot")
    assert product["max_stay_min"] == 120 and product["long_stay"] is None
    rate_rule = product["rules"][product["rate"]]
    assert (rate_rule["elapsed_from_minutes"], rate_rule["elapsed_to_minutes"]) == (0, None)    # no band: the rate is open-ended
    assert product["form"] == {"rate": "lot:zone_rate", "window": "rate"}
    assert not [rule_id for rule_id in product["rules"] if rule_id.endswith("zone_long_stay")]


@pytest.mark.parametrize("zone, message", [
    ("t_first", "free_if_stay_at_most_min 30.0, which the schedule of a lot cannot carry"),
    ("t_firstonly", "first_period_min 60.0, which the schedule of a lot cannot carry"),
    ("t_cents", "no whole number of cents"),
    ("t_none", "has no hourly street rate in the tariff table"),
    ("t_unknown", "the tariff table holds no zone t_unknown"),
    # a day cap would cap the long-stay product of a lot's bands, which the street does not (a long stay buys the product)
    ("t_capms", "a daily cap beside a long-stay product, which the bands of a lot cannot carry exactly"),
])
def test_a_zone_product_that_a_lot_cannot_carry_stops_the_row_and_is_never_approximated(ll, zone, message):
    with pytest.raises(SystemExit, match=message):
        ll.zone_product_rules(_tariffs(), zone, "lot")


def test_the_consistency_check_names_a_difference_and_changes_nothing(ll, package_file):
    path, sha256 = package_file
    package = ll.load_package(path, sha256)
    product = ll.zone_product_rules(_tariffs(), "t_ia", "t_zone_a")
    sentence, verdicts = ll.zone_check_sentence(package, "t_zone_a", "t_zone_a__1", product, "t_ia")
    # the price is stated per 30 min and the zone row bills per started minute: the same hourly price, a named difference of unit
    assert verdicts[0] == "rate equal" and "0.90 EUR per 30 min = 1.80 EUR per hour" in sentence
    assert verdicts[1].startswith("billing unit DIFFERS: the package states 30 min") and len(verdicts) == 2
    assert "the zone tariff is not changed by it" in sentence
    differing = ll.zone_product_rules(_tariffs().replace({"hourly_rate_eur": {1.8: 3.0}}), "t_ia", "t_zone_a")
    sentence, verdicts = ll.zone_check_sentence(package, "t_zone_a", "t_zone_a__1", differing, "t_ia")
    assert verdicts[0] == "rate DIFFERS by 1.20 EUR per hour" and "rate DIFFERS by 1.20 EUR per hour" in sentence
    # the lot keeps the zone's product (3.00 EUR per hour = 0.05 EUR per started minute), not the documented one
    assert differing["rules"][differing["rate"]]["amount_eur"] == 0.05


def test_the_consistency_check_compares_the_fee_window_where_the_package_structures_it(ll, package_file):
    path, sha256 = package_file
    package = ll.load_package(path, sha256)
    rule = package["rules"]["t_grace__1"]
    rule["amount_eur"], rule["billing_unit_minutes"] = 1.0, 30          # 2.00 EUR per hour
    tariffs = _tariffs()
    tariffs.loc[tariffs["zone_id"] == "t_ia", ["hourly_rate_eur", "billing_unit_min", "fee_start_h", "fee_end_h"]] = [
        2.0, 30, 9.0, 16.0]
    product = ll.zone_product_rules(tariffs, "t_ia", "t_grace")
    assert ll.zone_check_sentence(package, "t_grace", "t_grace__1", product, "t_ia")[1] == ["rate equal", "fee window equal"]
    tariffs.loc[tariffs["zone_id"] == "t_ia", "fee_end_h"] = 20.0
    product = ll.zone_product_rules(tariffs, "t_ia", "t_grace")
    assert ll.zone_check_sentence(package, "t_grace", "t_grace__1", product, "t_ia")[1] == ["rate equal", "fee window DIFFERS"]
    rule["schedule"] = SCHEDULE_UNPARSED
    rule["paid_hours"] = "24/7"
    assert ll.zone_check_sentence(package, "t_grace", "t_grace__1", product, "t_ia")[1][1].startswith("fee window DIFFERS")
    rule["schedule"], rule["paid_hours"] = None, "free text"
    assert "not compared" in ll.zone_check_sentence(package, "t_grace", "t_grace__1", product, "t_ia")[1][1]


# --------------------------------------------------------------------------- ASSUMPTION P14, the median product


def _donor(name, values):
    return {"id": name, "values": values}


def _single(rate, unit=60):
    return {"garage_hourly_rate_eur": rate, "garage_billing_unit_min": unit, "tariff_duration_bands": None}


def test_the_median_product_is_the_middle_schedule_ordered_by_the_price_of_an_hour(ll):
    donors = [_donor("dear", _single(3.0)), _donor("cheap", _single(1.0)), _donor("middle", _single(2.0))]
    assert ll.median_schedule(donors, "lot")["id"] == "middle"
    banded = {"garage_hourly_rate_eur": None, "garage_billing_unit_min": None,
              "tariff_duration_bands": "0-30 free; 30-60 total 0.70; 60- 0.10/6"}
    assert ll.hourly_equivalent_eur(banded) == 0.7 and ll.hourly_equivalent_eur(_single(1.8, 1)) == 1.8
    donors = [_donor("a", _single(1.5)), _donor("b", banded), _donor("c", _single(0.4))]
    assert ll.median_schedule(donors, "lot")["id"] == "b"    # 0.40 < 0.70 < 1.50


def test_an_even_number_of_donors_needs_identical_middle_schedules_and_never_invents_a_mean(ll):
    same = [_donor("a", _single(1.8, 1)), _donor("b", _single(1.8, 1))]
    assert ll.median_schedule(same, "lot")["id"] == "a"
    four = [_donor("a", _single(1.0)), _donor("b", _single(2.0)), _donor("c", _single(2.0)), _donor("d", _single(5.0))]
    assert ll.median_schedule(four, "lot")["id"] == "b"
    different = [_donor("a", _single(1.0)), _donor("b", _single(2.0))]
    with pytest.raises(SystemExit, match="have different schedules; there is no median product, and none is invented"):
        ll.median_schedule(different, "lot x")
    # the same price with another unit is another schedule: a tie of different schedules stops the step too
    with pytest.raises(SystemExit, match="have different schedules"):
        ll.median_schedule([_donor("a", _single(1.8, 1)), _donor("b", _single(1.8, 30))], "lot")


def test_a_median_needs_at_least_two_priced_lots(ll):
    with pytest.raises(SystemExit, match="ASSUMPTION P14 needs at least two priced lots, found 1"):
        ll.median_schedule([_donor("a", _single(1.0))], "lot x")
    with pytest.raises(SystemExit, match="found 0"):
        ll.median_schedule([], "lot x")
    with pytest.raises(SystemExit, match="neither an hourly rate nor duration bands"):
        ll.hourly_equivalent_eur({"garage_hourly_rate_eur": None, "tariff_duration_bands": None})


# --------------------------------------------------------------------------- the archived evidence (spec I5a)


def _evidence_folder(tmp_path, text="Sie koennen auf den kostenlosen Parkplaetzen parken.", sha=None, listed="page.html"):
    folder = tmp_path / "wob_free_lots_evidence"
    folder.mkdir(parents=True)
    (folder / "page.html").write_text(text, encoding="utf-8")
    digest = sha or hashlib.sha256((folder / "page.html").read_bytes()).hexdigest()
    (folder / "MANIFEST.md").write_text(
        "# Evidence\n\n| file | url | retrieved | sha256 | statement |\n|---|---|---|---|---|\n"
        f"| {listed} | https://example.org/page | 2026-10-14 | {digest} | free parking next to the pool |\n\n"
        "The table has the columns `file | url | retrieved | sha256 | statement`; prose with pipes is no entry.\n"
        "Not archived: a page that was not found.\n", encoding="utf-8")
    return folder


def test_archived_evidence_is_read_only_where_its_hash_is_the_recorded_one(ll, tmp_path):
    folder = _evidence_folder(tmp_path)
    evidence = ll.load_free_evidence(folder)
    assert list(evidence) == ["page.html"] and evidence["page.html"]["url"] == "https://example.org/page"
    assert evidence["page.html"]["retrieved"] == "2026-10-14" and len(evidence["page.html"]["sha256"]) == 64
    assert ll.load_free_evidence(None) == {} and ll.load_free_evidence(tmp_path / "absent") == {}
    (folder / "page.html").write_text("changed", encoding="utf-8")
    with pytest.raises(SystemExit, match="does not have the SHA-256"):
        ll.load_free_evidence(folder)


def test_archived_evidence_that_the_manifest_lists_but_the_folder_lacks_is_refused(ll, tmp_path):
    folder = _evidence_folder(tmp_path, listed="missing.html")
    with pytest.raises(SystemExit, match="lists missing.html, which is not in the folder"):
        ll.load_free_evidence(folder)
    folder = _evidence_folder(tmp_path / "x", sha="not-a-hash")
    with pytest.raises(SystemExit, match="does not have the SHA-256 not-a-hash"):
        ll.load_free_evidence(folder)


# --------------------------------------------------------------------------- the pages of the Restluecken package (spec v3 B4)

RESTLUECKEN_NAME = "Parkdaten_Restluecken_2026-10-14"
RESTLUECKEN_SHA256 = "80f301c15fa13668397b6f7d127b089370224ce0ad16215dd9b96ec2fa1d8d2c"
SOURCE_POINT = {"latitude": 52.0, "longitude": 10.0}


def _restluecken_package(directory: Path, *, pages=None, sources="default", mutate=None) -> tuple:
    """Write a synthetic package shaped like the owner's Restluecken package: ``evidence/wob_belege/`` with original pages, a
    ``findings.json`` of their sources and a ``file_manifest.json`` whose paths are those of the machine that wrote it (the reader
    matches by file name). ``mutate`` is called with the members ({name: bytes}) after the manifest is computed. Returns
    (path, SHA-256)."""
    pages = pages or {"page_a.html": "Der Parkplatz ist kostenlos.", "page_b.html": "Auch dieser ist kostenlos."}
    members = {name: text.encode("utf-8") for name, text in pages.items()}
    if sources == "default":
        sources = [{"source_id": Path(name).stem, "publisher": "Publisher A", "url": f"https://example.org/{Path(name).stem}",
                    "title": f"Title of {name}", "retrieved_date": "2026-10-14", "price_status": "free",
                    **({"source_point_wgs84": SOURCE_POINT} if name == "page_a.html" else {})} for name in pages]
    members["findings.json"] = json.dumps({"sources": sources}).encode("utf-8")
    entries = [{"path": f"/elsewhere/wob_belege/{name}", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                "role": "derived_research" if name == "findings.json" else "original_response"}
               for name, content in members.items()]
    members["file_manifest.json"] = json.dumps(entries).encode("utf-8")
    if mutate is not None:
        mutate(members)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{RESTLUECKEN_NAME}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(f"{RESTLUECKEN_NAME}/evidence/wob_belege/{name}", content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_pages_of_the_restluecken_package_are_read_from_the_zip_against_its_file_manifest(ll, tmp_path):
    path, sha256 = _restluecken_package(tmp_path / "ok")
    evidence = ll.load_package_free_evidence(path, sha256)
    assert sorted(evidence) == ["page_a.html", "page_b.html"]      # the original responses, not findings.json or the manifest
    entry = evidence["page_a.html"]
    assert entry["sha256"] == hashlib.sha256(b"Der Parkplatz ist kostenlos.").hexdigest()
    assert entry["url"] == "https://example.org/page_a" and entry["retrieved"] == "2026-10-14"
    assert entry["member"] == f"{RESTLUECKEN_NAME}/evidence/wob_belege/page_a.html" and entry["package_sha256"] == sha256
    assert "Publisher A" in entry["statement"] and "Title of page_a.html" in entry["statement"]
    assert entry["text"] == "Der Parkplatz ist kostenlos." and entry["source_point_wgs84"] == SOURCE_POINT
    assert "source_point_wgs84" not in evidence["page_b.html"]
    assert ll.load_package_free_evidence(None) == {}       # no package given: no evidence, the lots stay QA rows
    assert ll.WOB_EVIDENCE_SHA256 == RESTLUECKEN_SHA256


def test_a_changed_or_missing_restluecken_package_is_refused(ll, tmp_path):
    path, sha256 = _restluecken_package(tmp_path / "a")
    with pytest.raises(SystemExit, match="a changed package is never read"):
        ll.load_package_free_evidence(path, "0" * 64)
    with pytest.raises(SystemExit, match="missing"):
        ll.load_package_free_evidence(tmp_path / "absent.zip", sha256)


def test_a_page_that_differs_from_the_file_manifest_is_missing_or_has_no_source_is_refused(ll, tmp_path):
    def change(members):
        members["page_a.html"] = b"changed"

    path, sha256 = _restluecken_package(tmp_path / "c", mutate=change)
    with pytest.raises(SystemExit, match=r"page_a.html .*does not have the SHA-256 .*file_manifest.json"):
        ll.load_package_free_evidence(path, sha256)

    def drop(members):
        del members["page_b.html"]

    path, sha256 = _restluecken_package(tmp_path / "d", mutate=drop)
    with pytest.raises(SystemExit, match="file_manifest.json lists page_b.html, which is not in the zip"):
        ll.load_package_free_evidence(path, sha256)
    only_a = [{"source_id": "page_a", "publisher": "P", "url": "https://example.org/a", "title": "T",
               "retrieved_date": "2026-10-14"}]
    path, sha256 = _restluecken_package(tmp_path / "e", sources=only_a)
    with pytest.raises(SystemExit, match="findings.json holds no source for the archived page page_b.html"):
        ll.load_package_free_evidence(path, sha256)


# --------------------------------------------------------------------------- the rows of the dataset


def _zones() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"zone_id": ["t_ia", "t_gap_zone"]}, crs=METRIC_CRS, geometry=[
        box(X0 + 0, Y0 - 100, X0 + 500, Y0 + 400), box(X0 + 15_900, Y0 - 100, X0 + 16_200, Y0 + 100)])


def _step_tariffs() -> pd.DataFrame:
    tariffs = _tariffs()
    extra = tariffs[tariffs["zone_id"] == "t_first"].copy()
    extra["zone_id"] = "t_gap_zone"
    return pd.concat([tariffs, extra], ignore_index=True)


SPECS = {
    "t_zone_a": {"zone_product": "t_ia", "check": "t_zone_a__1"},
    "t_zone_b": {"zone_product": "t_ia", "check": "t_zone_b__1"},
    "t_p14_zone": {"p14": True, "unknown_component": "t_p14_zone__1", "comment": "a bathers discount is not modelled"},
    "t_free": {"free": "t_free__1"},
    "t_free_he": {"free": "t_free_he__1"},
    "t_p14_he": {"p14": True, "unknown_component": "t_p14_he__1"},
    "t_zero": {"free": "t_zero__1"},
    "t_rate": {"rate": "t_rate__1", "window": "rate", "cap": "t_rate__1:cap"},
    "t_rate_free_day": {"rate": "t_rate_free_day__1", "window": None},
    "t_grace": {"grace": ("t_grace__2", "t_grace__1"), "window": "rate", "cap": "t_grace__3:cap",
                "p10_basis": "the free ticket of up to 30 min is a grace band", "ignored": {}},
    "t_bands": {"bands": ("t_bands__1", "t_bands__2", "t_bands__3"), "window": "rate",
                "roles": {"t_bands__1": {"type": "free", "from": 0, "to": 30},
                          "t_bands__2": {"type": "duration_total", "from": 30, "to": 60},
                          "t_bands__3": {"type": "increment", "from": 60, "to": None}},
                "p10_basis": "the free ticket of up to 30 min is a grace band"},
    "t_bands2": {"bands": ("t_bands2__1", "t_bands2__2", "t_bands2__3"), "window": "rate",
                 "roles": {"t_bands2__1": {"type": "free", "from": 0, "to": 30},
                           "t_bands2__2": {"type": "duration_total", "from": 30, "to": 60},
                           "t_bands2__3": {"type": "increment", "from": 60, "to": None}},
                 "p10_basis": "the free ticket of up to 30 min is a grace band"},
    "t_p14_mun": {"p14": True, "unknown_component": "t_p14_mun__1"},
    "t_mixed": {"mixed": True},
    "t_p14_none": {"p14": True, "unknown_component": "t_p14_none__1"},
    "t_gap": {"zone_product": "t_gap_zone", "check": "t_gap__1"},
}
EVIDENCE_SPECS = {
    "WOB_PARK_E1": {"name": "Freibad test", "pages": (("page.html", "kostenlosen Parkplaetzen"),),
                    "not_found": "the page is not archived"},
    "WOB_PARK_E2": {"name": "Seeufer test", "pages": (), "not_found": "no page found on 2026-10-14"},
    "WOB_PARK_E3": {"name": "Bad test", "pages": (), "not_found": "no page found on 2026-10-14"},
}


@pytest.fixture()
def step(garages_step, monkeypatch):
    monkeypatch.setattr(garages_step.specs, "LARGE_LOT_SPECS", SPECS)
    monkeypatch.setattr(garages_step.specs, "WOB_FREE_LOT_EVIDENCE", EVIDENCE_SPECS)
    monkeypatch.setattr(garages_step.specs, "LARGE_LOT_SUBPOINTS", {"WOB_PARK_SUB": "an accessible-parking point of the lot"})
    monkeypatch.setattr(garages_step.specs, "LARGE_LOT_QA_DETAILS", {"t_closed": "the owner's reason"})
    return garages_step


def _inputs(step, package_file, threshold=50, evidence=None, zones=None, tariffs=None):
    path, sha256 = package_file
    package = step.ll.load_package(path, sha256)
    return {"large": {"package": package, "threshold": threshold, "zones": _zones() if zones is None else zones,
                      "tariffs": _step_tariffs() if tariffs is None else tariffs, "evidence": evidence or {}},
            "rounding_census": {"rate_rules": 4, "stated": 2, "started_unit": 2}}


def _by_id(result) -> dict:
    return {row["garage_id"]: row for row in result["rows"]}


def _reasons(result) -> dict:
    return {entry["record_id"]: entry["reason"] for entry in result["qa"]}


def test_every_record_is_an_option_or_a_qa_row_with_its_reason(step, package_file):
    result = step.build_large_lots(_inputs(step, package_file))
    ids = set(_by_id(result))
    assert ids == {"lot_t_zone_a", "lot_t_zone_b", "lot_t_p14_zone", "lot_t_free", "lot_t_zero", "lot_t_rate",
                   "lot_t_rate_free_day", "lot_t_grace", "lot_t_free_he", "lot_t_bands", "lot_t_bands2", "lot_t_p14_mun"}
    assert _reasons(result) == {
        "candidate_lot_t_mixed": "lot_mixed_tariff_unseparated", "candidate_lot_t_p14_none": "lot_tariff_unknown",
        "candidate_lot_t_p14_he": "lot_tariff_unknown",
        "candidate_lot_t_gap": "lot_tariff_not_expressible", "candidate_lot_t_small": "lot_below_threshold",
        "candidate_lot_t_nocap": "lot_capacity_unknown", "candidate_lot_t_customers": "lot_access_not_public",
        "candidate_lot_t_closed": "lot_not_open", "candidate_lot_t_group": "lot_not_surface_lot",
        "candidate_lot_t_alias": "lot_alias", "candidate_lot_t_candidate": "lot_unconfirmed_candidate",
        "candidate_lot_t_nopos": "lot_no_position", "candidate_lot_wob_park_e1": "lot_free_evidence_missing",
        "candidate_lot_wob_park_e2": "lot_free_evidence_missing", "candidate_lot_wob_park_e3": "lot_below_threshold",
        "candidate_lot_wob_park_sub": "lot_alias"}
    # every package record is accounted for exactly once (an option, or one QA row), the subpoint of the crosswalk beside them
    package = _inputs(step, package_file)["large"]["package"]
    accounted = [row["package_facility_id"] for row in result["rows"]] + [
        entry["subject"].rsplit("(", 1)[1].rstrip(")") for entry in result["qa"]]
    assert sorted(accounted) == sorted(list(package["facilities"]) + ["WOB_PARK_SUB"])
    assert result["counts"]["options"] == 12 and result["counts"]["failures"] == 1


def test_the_threshold_decides_which_records_are_options(step, package_file):
    low = step.build_large_lots(_inputs(step, package_file, threshold=30))
    high = step.build_large_lots(_inputs(step, package_file, threshold=100))
    assert "candidate_lot_t_small" in _reasons(low) and "candidate_lot_t_small" in _reasons(high)
    assert set(_by_id(high)) == {"lot_t_zone_a", "lot_t_free", "lot_t_bands"} and "lot_t_zone_a" in _by_id(low)
    assert _reasons(high)["candidate_lot_t_rate"] == "lot_below_threshold"      # 70 spaces
    assert _reasons(low)["candidate_lot_wob_park_e3"] == "lot_free_evidence_missing"  # 40 spaces reach the sensitivity at 30


def test_a_zone_product_lot_carries_the_street_product_of_its_zone_with_the_consistency_check(step, package_file):
    row = _by_id(step.build_large_lots(_inputs(step, package_file)))["lot_t_zone_a"]
    # the zone's table row: per started minute, its fee window, and its maximum stay with the long-stay product as the bands
    assert row["tariff_duration_bands"] == "0-180 0.03/1; 180- total 9.00" and row["garage_hourly_rate_eur"] is None
    assert (row["garage_fee_start_h"], row["garage_fee_end_h"]) == (9.0, 20.0)
    assert row["facility_kind"] == "surface_lot" and row["assumptions"] == "P8"
    assert row["capacity_reported"] == 101 and "reported" in row["notes"]
    assert row["tariff_rule_ids"] == "parking_tariffs_2026.csv:t_ia;t_zone_a__1"
    assert "Tariff: the street product of the zone t_ia (spec Amendment I1)" in row["notes"]
    assert "rate equal" in row["notes"] and "the zone tariff is not changed by it" in row["notes"]
    # the maximum stay of the zone is carried by the bands, not dropped: the notes say so
    assert "Not modelled (the garage columns carry no maximum stay or long-stay product)" not in row["notes"]
    assert ("The maximum stay 180 min and the long-stay product 9.00 EUR of the zone (assumption M1) are the bands of the "
            "schedule.") in row["notes"]
    assert row["package_sha256"] == package_file[1] and row["municipality_ags"] == BS
    assert row["geometry"].distance(Point(X0 + 100, Y0 + 100)) < 0.001
    assert row["geometry_method"] == "package_research_point_in_parking_area" and row["source_url"] == SOURCE_URL


def test_a_lot_whose_point_lies_outside_the_zone_of_its_product_stops_the_step(step, package_file):
    inputs = _inputs(step, package_file)
    inputs["large"]["zones"] = _zones().assign(geometry=[box(X0 + 900, Y0 - 100, X0 + 1000, Y0 + 100),
                                                         _zones().geometry.iloc[1]])
    with pytest.raises(SystemExit, match="t_zone_a: the owner decision gives it the street product of t_ia, but its point "
                                         "does not lie inside that zone"):
        step.build_large_lots(inputs)


def test_a_free_lot_is_the_free_schedule_without_an_assumption_and_an_explicit_zero_is_free_too(step, package_file):
    rows = _by_id(step.build_large_lots(_inputs(step, package_file)))
    for garage_id in ("lot_t_free", "lot_t_zero"):
        row = rows[garage_id]
        assert row["tariff_duration_bands"] == "0- free" and row["assumptions"] is None
        assert (row["garage_fee_start_h"], row["garage_fee_end_h"], row["garage_hourly_rate_eur"]) == (0.0, 24.0, None)
    # only a REPORTED capacity fills the column; an OSM value is named in the notes
    assert rows["lot_t_free"]["capacity_reported"] is None and "capacity 100 for the threshold (osm_integer_capacity" in \
        rows["lot_t_free"]["notes"]
    assert rows["lot_t_zero"]["capacity_reported"] == 80


def test_a_rate_lot_reads_window_cap_and_rounding_from_its_component_and_names_what_it_does_not_carry(step, package_file):
    rows = _by_id(step.build_large_lots(_inputs(step, package_file)))
    rate = rows["lot_t_rate"]
    assert (rate["garage_hourly_rate_eur"], rate["garage_billing_unit_min"], rate["garage_daily_cap_eur"]) == (1.0, 60, 8.0)
    assert (rate["garage_fee_start_h"], rate["garage_fee_end_h"]) == (10.0, 18.0) and rate["assumptions"] is None
    assert "maximum stay 120 min (t_rate__1)" in rate["notes"] and "the Saturday window of 'Mo-Sa 10:00-18:00'" in rate["notes"]
    assert "the day boundary of the cap t_rate__1:cap is not stated" in rate["notes"]
    # no stated rounding and no stated charging times: ASSUMPTIONS P4 and P5, named
    open_rate = rows["lot_t_rate_free_day"]
    assert open_rate["assumptions"] == "P4;P5" and (open_rate["garage_fee_start_h"], open_rate["garage_fee_end_h"]) == (0.0, 24.0)
    assert "ASSUMPTION P4" in open_rate["notes"] and "ASSUMPTION P5" in open_rate["notes"]


def test_a_free_short_stay_is_a_grace_band_and_never_a_free_day(step, package_file):
    grace = _by_id(step.build_large_lots(_inputs(step, package_file)))["lot_t_grace"]
    assert grace["tariff_duration_bands"] == "0-30 free; 30-60 total 1.20; 60- 0.60/30"   # 31 min cost two started half hours
    assert grace["garage_daily_cap_eur"] == 3.0 and (grace["garage_fee_start_h"], grace["garage_fee_end_h"]) == (9.0, 16.0)
    assert grace["assumptions"] == "P8;P10" and "ASSUMPTION P10" in grace["notes"]
    bands = pg.parse_duration_bands(grace["tariff_duration_bands"])
    assert pg.duration_band_price_eur(bands, 30) == 0.0 and pg.duration_band_price_eur(bands, 31) == 1.2
    # the 30 min of the free ticket are the length of the free band and no maximum stay of the lot; the 180 min are one
    assert "maximum stay 180 min (t_grace__1)" in grace["notes"] and "maximum stay 30 min" not in grace["notes"]


def test_a_duration_schedule_is_read_from_the_components_with_their_elapsed_ranges(step, package_file):
    row = _by_id(step.build_large_lots(_inputs(step, package_file)))["lot_t_bands"]
    assert row["tariff_duration_bands"] == "0-30 free; 30-60 total 0.70; 60- 0.10/6"
    assert (row["garage_fee_start_h"], row["garage_fee_end_h"]) == (9.0, 16.0)
    # the free band that opens the schedule is a grace period (ASSUMPTION P10) whichever form wrote it, named once
    assert row["assumptions"] == "P4;P8;P10" and "ASSUMPTION P10" in row["notes"]
    assert "Basis of the reading: the free ticket of up to 30 min is a grace band" in row["notes"]


def test_the_p14_lot_takes_the_median_product_of_its_zone_and_names_it(step, package_file, capsys):
    result = step.build_large_lots(_inputs(step, package_file))
    rows = _by_id(result)
    imputed, donor = rows["lot_t_p14_zone"], rows["lot_t_zone_a"]
    for column in step.TARIFF_VALUE_COLUMNS:
        assert imputed[column] == donor[column], column
    assert imputed["assumptions"] == "P8;P14" and "ASSUMPTION P14 basis" in imputed["notes"]
    assert "the median product of the 2 priced lots of the zone t_ia" in imputed["notes"]
    assert "level zone" in imputed["notes"] and imputed["source_url"] == SOURCE_URL
    assert imputed["tariff_rule_ids"] == "parking_tariffs_2026.csv:t_ia;t_zone_a__1;t_p14_zone__1"
    assert "A bathers discount is not modelled." in imputed["notes"]
    assert result["notes"]["lot_t_p14_zone"].startswith("imputed (ASSUMPTION P14, level zone) from the median lot t_zone_a")
    printed = capsys.readouterr().out
    assert "[garages] P14 t_p14_zone: median product of 2 priced lots of the zone t_ia" in printed


def test_a_p14_lot_outside_every_zone_takes_the_median_of_its_municipality(step, package_file):
    rows = _by_id(step.build_large_lots(_inputs(step, package_file)))
    imputed = rows["lot_t_p14_mun"]
    assert imputed["tariff_duration_bands"] == rows["lot_t_bands"]["tariff_duration_bands"]
    assert "the median product of the 2 priced lots of the municipality 03102000" in imputed["notes"]
    assert "level municipality" in imputed["notes"] and imputed["assumptions"] == "P4;P8;P10;P14"


def test_p14_needs_two_priced_lots_and_a_free_lot_is_no_donor(step, package_file):
    result = step.build_large_lots(_inputs(step, package_file))
    assert _reasons(result)["candidate_lot_t_p14_none"] == "lot_tariff_unknown"   # Peine has no priced lot at all
    # Helmstedt has one paid lot (the grace lot) and one free lot: a free schedule is no product to take a median of, so the
    # lot without a stated tariff finds one donor and no median (a free lot counted as a donor would give two)
    assert _reasons(result)["candidate_lot_t_p14_he"] == "lot_tariff_unknown"
    note = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_t_p14_he")["note"]
    assert "fewer than two priced lots in its zone and in its municipality" in note


def test_a_p14_lot_whose_package_states_a_tariff_stops_the_step(step, package_file):
    inputs = _inputs(step, package_file)
    inputs["large"]["package"]["rules"]["t_p14_zone__1"]["amount_eur"] = 1.0
    with pytest.raises(SystemExit, match="ASSUMPTION P14 imputes a tariff that the package does not state, but "
                                         "t_p14_zone__1 states one"):
        step.build_large_lots(inputs)


def test_a_tie_of_different_middle_schedules_stops_the_step(step, package_file):
    inputs = _inputs(step, package_file)
    inputs["large"]["package"]["rules"]["t_zone_b__1"]["amount_eur"] = 1.2    # 2.40 EUR per hour against 1.80 in the same zone
    # the zone product is the zone's, so the donors stay identical; a donor with a rate of its own makes the tie
    step.specs.LARGE_LOT_SPECS["t_zone_b"] = {"rate": "t_zone_b__1", "window": None}
    try:
        with pytest.raises(SystemExit, match="large lot t_p14_zone: the two middle lots t_zone_a and t_zone_b of ASSUMPTION P14 "
                                             "have different schedules"):
            step.build_large_lots(inputs)
    finally:
        step.specs.LARGE_LOT_SPECS["t_zone_b"] = {"zone_product": "t_ia", "check": "t_zone_b__1"}


def test_a_tariff_the_grammar_cannot_express_is_a_named_qa_row_and_is_never_approximated(step, package_file, capsys):
    result = step.build_large_lots(_inputs(step, package_file))
    assert "lot_t_gap" not in _by_id(result)
    entry = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_t_gap")
    assert entry["reason"] == "lot_tariff_not_expressible" and "free_if_stay_at_most_min 30.0" in entry["note"]
    assert "the named error:" in entry["note"]
    assert [key for key, _ in result["failures"]] == ["t_gap"]
    printed = capsys.readouterr().out
    assert "WARNING: the tariff of the large lot t_gap is not expressible in the schedule grammar" in printed


def test_a_schedule_with_different_weekday_windows_is_a_named_qa_row_too(step, package_file):
    """The encoder reads one window of Monday to Friday; a package that states another window on Friday is a grammar gap."""
    inputs = _inputs(step, package_file)
    windows = inputs["large"]["package"]["rules"]["t_rate__1"]["schedule"]["weekly_windows"]
    windows[:] = [{"days": ["MO", "TU", "WE", "TH"], "start": "10:00", "end": "18:00"},
                  {"days": ["FR"], "start": "10:00", "end": "14:00"}]
    result = step.build_large_lots(inputs)
    entry = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_t_rate")
    assert entry["reason"] == "lot_tariff_not_expressible"
    assert "Monday to Friday state different charging intervals" in entry["note"]
    assert "lot_t_rate" not in _by_id(result) and "t_rate" in [key for key, _ in result["failures"]]


def test_a_mistake_of_an_owner_decision_is_no_grammar_gap_and_stops_the_step(step, package_file, monkeypatch):
    """A decision that names a component the package does not hold is an error of the decision, not a tariff to report."""
    inputs = _inputs(step, package_file)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", {**SPECS, "t_rate": {"rate": "t_rate__1", "window": "rate",
                                                                            "cap": "t_rate__1:nothing"}})
    with pytest.raises(SystemExit, match="t_rate__1:nothing"):
        step.build_large_lots(inputs)


def test_an_option_without_an_owner_decision_stops_the_step_and_a_decision_without_a_record_too(step, package_file, monkeypatch):
    inputs = _inputs(step, package_file)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", {key: value for key, value in SPECS.items() if key != "t_rate"})
    with pytest.raises(SystemExit, match="large lot t_rate is an option at the threshold 50 but has no owner decision"):
        step.build_large_lots(inputs)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", {**SPECS, "nobody": {"free": "x"}})
    with pytest.raises(SystemExit, match=r"LARGE_LOT_SPECS names facilities that the package does not hold: \['nobody'\]"):
        step.build_large_lots(inputs)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", SPECS)
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {"nobody": {}})
    with pytest.raises(SystemExit, match=r"WOB_FREE_LOT_EVIDENCE names facilities that the package does not hold"):
        step.build_large_lots(inputs)


def test_a_decision_must_name_exactly_one_form_and_a_mixed_site_must_be_marked_so_by_the_package(step, package_file, monkeypatch):
    inputs = _inputs(step, package_file)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", {**SPECS, "t_free": {"free": "t_free__1", "rate": "t_free__1"}})
    with pytest.raises(SystemExit, match="large lot t_free: the owner decision names exactly one of"):
        step.build_large_lots(inputs)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", SPECS)
    inputs["large"]["package"]["facilities"]["t_mixed"]["price_readiness"] = "evidence_components_require_model_mapping"
    with pytest.raises(SystemExit, match="marks it mixed, but the package states the price readiness"):
        step.build_large_lots(inputs)


def test_a_component_the_decision_ignores_must_exist_and_is_named_with_its_reason(step, package_file, monkeypatch):
    inputs = _inputs(step, package_file)
    ignored = {**SPECS, "t_rate": {**SPECS["t_rate"], "ignored": {"nowhere__1": "x"}}}
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", ignored)
    with pytest.raises(SystemExit, match=r"ignores the components \['nowhere__1'\]"):
        step.build_large_lots(inputs)
    named = {**SPECS, "t_grace": {**SPECS["t_grace"], "ignored": {"t_grace__3": "a day ticket that the cap already is"}}}
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", named)
    row = _by_id(step.build_large_lots(_inputs(step, package_file)))["lot_t_grace"]
    assert "Not used: t_grace__3 (day_ticket, 3.0 EUR): a day ticket that the cap already is." in row["notes"]


def test_a_free_wolfsburg_lot_is_free_only_on_archived_evidence_and_never_by_default(step, package_file, tmp_path):
    # without the archived page: a QA row with what was searched, not a free row (ASSUMPTION P12 is withdrawn)
    result = step.build_large_lots(_inputs(step, package_file))
    assert "lot_wob_park_e1" not in _by_id(result)
    entry = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_wob_park_e1")
    assert entry["reason"] == "lot_free_evidence_missing" and "the page is not archived" in entry["note"]
    assert "ASSUMPTION P12 withdrawn" in entry["note"]
    # with the page in the folder and its quotation: a free row on that source
    evidence = step.ll.load_free_evidence(_evidence_folder(tmp_path))
    rows = _by_id(step.build_large_lots(_inputs(step, package_file, evidence=evidence)))
    row = rows["lot_wob_park_e1"]
    assert row["tariff_duration_bands"] == "0- free" and row["assumptions"] is None
    assert row["source_url"] == "https://example.org/page" and row["source_date"] == "2026-10-14"
    assert row["tariff_rule_ids"] == "evidence:page.html" and "archived evidence page page.html" in row["notes"]
    assert "owner decision (spec Amendment I5a" in row["notes"]
    # the other two have no page: still QA rows, whatever else is archived
    assert "lot_wob_park_e2" not in rows and "lot_wob_park_e3" not in rows
    # an archived page without the quotation stops the step
    folder = _evidence_folder(tmp_path / "other", text="a page about something else")
    with pytest.raises(SystemExit, match="does not hold the quotation 'kostenlosen Parkplaetzen' that the free status of "
                                         "WOB_PARK_E1 rests on"):
        step.build_large_lots(_inputs(step, package_file, evidence=step.ll.load_free_evidence(folder)))


def test_a_free_lot_names_every_page_with_its_hash_and_the_condition_that_is_not_modelled(step, package_file, tmp_path,
                                                                                          monkeypatch):
    path, sha256 = _restluecken_package(tmp_path / "r")
    evidence = step.ll.load_package_free_evidence(path, sha256)
    condition = ("ASSUMPTION W-match (owner decision 2026-10-14): the lot is free outside match days; match days are not "
                 "modelled")
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {**EVIDENCE_SPECS, "WOB_PARK_E1": {
        "name": "Arena test", "pages": (("page_a.html", "kostenlos"), ("page_b.html", "Auch dieser")),
        "condition": condition, "not_found": "x"}})
    row = _by_id(step.build_large_lots(_inputs(step, package_file, evidence=evidence)))["lot_wob_park_e1"]
    assert row["tariff_duration_bands"] == "0- free" and row["assumptions"] is None and row["max_stay_min"] is None
    assert row["tariff_rule_ids"] == "evidence:page_a.html;evidence:page_b.html"
    assert row["source_url"] == "https://example.org/page_a" and row["source_date"] == "2026-10-14"
    for name, content in (("page_a.html", b"Der Parkplatz ist kostenlos."), ("page_b.html", b"Auch dieser ist kostenlos.")):
        assert name in row["notes"] and hashlib.sha256(content).hexdigest() in row["notes"]
    assert sha256 in row["notes"] and RESTLUECKEN_NAME in row["notes"]
    assert condition in row["notes"]
    # the point of the page is named with its distance to the research point of the lot (a plausibility figure, no polygon match)
    assert "m from the research point of the lot" in row["notes"]
    # a second page without its quotation stops the step, and names the page
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {**EVIDENCE_SPECS, "WOB_PARK_E1": {
        "name": "Arena test", "pages": (("page_a.html", "kostenlos"), ("page_b.html", "nowhere in the page")),
        "not_found": "x"}})
    with pytest.raises(SystemExit, match="page_b.html does not hold the quotation 'nowhere in the page' that the free status "
                                         "of WOB_PARK_E1 rests on"):
        step.build_large_lots(_inputs(step, package_file, evidence=evidence))


def test_an_owner_decided_free_lot_without_a_position_is_a_qa_row_that_names_the_decision(step, package_file, tmp_path,
                                                                                         monkeypatch):
    path, sha256 = _restluecken_package(tmp_path / "r")
    evidence = step.ll.load_package_free_evidence(path, sha256)
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {
        **EVIDENCE_SPECS, "WOB_PARK_E1": {**EVIDENCE_SPECS["WOB_PARK_E2"]}, "t_nopos": {
            "name": "Lot without a position", "pages": (("page_a.html", "kostenlos"),), "not_found": "x"}})
    monkeypatch.setattr(step.specs, "LARGE_LOT_QA_DETAILS", {"t_nopos": "the owner decides the lot free on its page"})
    result = step.build_large_lots(_inputs(step, package_file, evidence=evidence))
    assert "lot_t_nopos" not in _by_id(result)
    entry = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_t_nopos")
    assert entry["reason"] == "lot_no_position" and "none is invented" in entry["note"]
    assert "the owner decides the lot free on its page" in entry["note"]


def test_a_page_that_the_table_names_and_the_given_archive_lacks_stops_the_step(step, package_file, tmp_path, monkeypatch):
    path, sha256 = _restluecken_package(tmp_path / "r")
    evidence = step.ll.load_package_free_evidence(path, sha256)
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {**EVIDENCE_SPECS, "WOB_PARK_E1": {
        "name": "Lot", "pages": (("page_a.html", "kostenlos"), ("missing.html", "kostenlos")), "not_found": "x"}})
    with pytest.raises(SystemExit, match=r"names the page\(s\) \['missing.html'\] for WOB_PARK_E1, which the archived evidence "
                                         r"does not hold"):
        step.build_large_lots(_inputs(step, package_file, evidence=evidence))
    # without any archived evidence the lot is a QA row with what was searched (the documented, logged state)
    result = step.build_large_lots(_inputs(step, package_file))
    assert "lot_wob_park_e1" not in _by_id(result)
    assert _reasons(result)["candidate_lot_wob_park_e1"] == "lot_free_evidence_missing"


def test_a_page_held_by_two_sources_of_evidence_is_refused(step, tmp_path):
    folder_evidence = step.ll.load_free_evidence(_evidence_folder(tmp_path / "f"))        # holds page.html
    path, sha256 = _restluecken_package(tmp_path / "r", pages={"page.html": "kostenlosen Parkplaetzen"})
    zip_evidence = step.ll.load_package_free_evidence(path, sha256)
    with pytest.raises(SystemExit, match=r"the archived evidence pages \['page.html'\] are held by two sources"):
        step._merge_evidence(folder_evidence, zip_evidence)
    other_path, other_sha256 = _restluecken_package(tmp_path / "r2")
    other = step.ll.load_package_free_evidence(other_path, other_sha256)
    assert sorted(step._merge_evidence(folder_evidence, other)) == ["page.html", "page_a.html", "page_b.html"]
    assert step._merge_evidence({}, {}) == {}


def test_archived_evidence_that_releases_no_owner_decided_lot_is_a_warning_not_silence(step, package_file, tmp_path, monkeypatch,
                                                                                    capsys):
    path, sha256 = _restluecken_package(tmp_path / "r")
    evidence = step.ll.load_package_free_evidence(path, sha256)
    # every owner-decided lot has no page in the table: evidence is given, nothing is released (a name or format mismatch)
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {key: {**spec, "pages": ()} for key, spec in EVIDENCE_SPECS.items()})
    result = step.build_large_lots(_inputs(step, package_file, evidence=evidence))
    printed = capsys.readouterr().out
    assert "free options on archived pages 0/3 (0.0 %), QA rows 3" in printed
    assert "[large lots] WARNING: archived evidence was given but no owner-decided free lot was released" in printed
    assert not [row for row in result["rows"] if row["garage_id"].startswith("lot_wob_park_e")]
    # no evidence given: the same rate line without the warning (the lots are QA rows by design)
    step.build_large_lots(_inputs(step, package_file))
    assert "WARNING: archived evidence was given" not in capsys.readouterr().out
    # a lot released on its pages: no warning, the rate counts it
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {**EVIDENCE_SPECS, "WOB_PARK_E1": {
        "name": "Lot", "pages": (("page_a.html", "kostenlos"),), "not_found": "x"}})
    step.build_large_lots(_inputs(step, package_file, evidence=evidence))
    printed = capsys.readouterr().out
    assert "free options on archived pages 1/3 (33.3 %)" in printed and "WARNING: archived evidence was given" not in printed


def test_the_assumption_of_a_free_lot_is_a_column_value_and_the_claim_scope_of_a_page_is_quoted(step, package_file, tmp_path,
                                                                                               monkeypatch):
    # the claim scope is data of findings.json, whose own hash is checked, so the synthetic package carries it from the start
    pages = {"page_a.html": "Der Parkplatz ist kostenlos."}
    path, sha256 = _restluecken_package(tmp_path / "r", pages=pages, sources=[{
        "source_id": "page_a", "publisher": "Publisher A", "url": "https://example.org/page_a", "title": "T",
        "retrieved_date": "2026-10-14", "claim_scope": "This named POI only."}])
    evidence = step.ll.load_package_free_evidence(path, sha256)
    assert evidence["page_a.html"]["claim_scope"] == "This named POI only."
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {**EVIDENCE_SPECS, "WOB_PARK_E1": {
        "name": "Arena test", "pages": (("page_a.html", "kostenlos"),), "assumption": "W-match",
        "condition": "Owner decision: free outside match days", "not_found": "x"}})
    row = _by_id(step.build_large_lots(_inputs(step, package_file, evidence=evidence)))["lot_wob_park_e1"]
    assert row["assumptions"] == "W-match" and "ASSUMPTION W-match: " + pg.ASSUMPTIONS["W-match"] in row["notes"]
    assert "the claim scope that the package records for it: 'This named POI only.'" in row["notes"]
    # an assumption that the dataset vocabulary does not know stops the step
    monkeypatch.setattr(step.specs, "WOB_FREE_LOT_EVIDENCE", {**EVIDENCE_SPECS, "WOB_PARK_E1": {
        "name": "Lot", "pages": (("page_a.html", "kostenlos"),), "assumption": "W-nothing", "not_found": "x"}})
    with pytest.raises(SystemExit, match="names the assumption 'W-nothing' for WOB_PARK_E1"):
        step.build_large_lots(_inputs(step, package_file, evidence=evidence))
    # the validator accepts W-match on a free surface lot and refuses it elsewhere
    frame = _lot_frame([{**row, "assumptions": "W-match"}])
    pg.validate_garages(frame)
    with pytest.raises(ValueError, match="ASSUMPTION W-match qualifies the free use of a surface lot"):
        pg.validate_garages(_lot_frame([{**row, "assumptions": "W-match", "facility_kind": "garage"}]))


def test_the_subpoint_of_a_lot_is_a_qa_row_and_every_municipal_point_is_accounted_for(step, package_file, tmp_path):
    result = step.build_large_lots(_inputs(step, package_file))
    entry = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_wob_park_sub")
    assert entry["reason"] == "lot_alias" and "counted once" in entry["note"] and "package facility t_free" in entry["note"]
    assert entry["municipality_ags"] == GS     # derived from the town of the target lot, never typed (finding I4)
    # the alias that is no subpoint is the record of its target (no extra row); the point of a facility needs none
    assert "candidate_lot_wob_park_alias" not in _reasons(result) and "lot_wob_park_alias" not in _by_id(result)
    path, sha256 = _write_package(tmp_path / "w", crosswalk=CROSSWALK + [{"facility_key": "WOB_PARK_LOST", "name": "x"}])
    with pytest.raises(SystemExit, match="WOB_PARK_LOST of the Wolfsburg crosswalk is neither a package facility, an alias nor "
                                         "a named subpoint"):
        step.build_large_lots(_inputs(step, (path, sha256)))
    path, sha256 = _write_package(tmp_path / "v", aliases={})
    with pytest.raises(SystemExit, match="the subpoint WOB_PARK_SUB must name a package facility in the aliases"):
        step.build_large_lots(_inputs(step, (path, sha256)))


def test_the_alias_of_a_site_is_named_in_the_notes_of_its_row(step, package_file):
    row = _by_id(step.build_large_lots(_inputs(step, package_file)))["lot_t_free"]
    assert "The municipal point(s) WOB_PARK_ALIAS, WOB_PARK_SUB of the Wolfsburg city layer are this site" in row["notes"]


def test_the_owner_reason_of_a_withdrawn_default_is_appended_to_its_qa_note(step, package_file):
    result = step.build_large_lots(_inputs(step, package_file))
    entry = next(item for item in result["qa"] if item["record_id"] == "candidate_lot_t_closed")
    assert "the owner's reason" in entry["note"] and entry["municipality_ags"] == GS


def test_the_rates_are_logged_and_a_high_imputation_share_is_a_warning(step, package_file, capsys, monkeypatch):
    result = step.build_large_lots(_inputs(step, package_file))
    printed = capsys.readouterr().out
    assert f"[garages] large lots (threshold 50): {len(_catalogue())} package records, 12 options" in printed
    assert "tariff from the site's own components 5/12, zone street product 2/12, free 3/12, imputed (ASSUMPTION P14) 2/12 " \
           "(16.7 %)" in printed
    assert f"QA rows {len(result['qa'])} by reason" in printed and "WARNING: more than 25 % of the large lots" not in printed
    assert ("[garages] large lots, source of the tariff: a page that states it 12/12, only a web-service layer (fallback) 0/12"
            in printed)
    assert ("[garages] large lots, source of the position: OpenStreetMap object 0/12, coordinate source of the package 12/12, "
            "tariff-source fallback 0/12") in printed
    assert ("[garages] large lots, capacity origin: reported 9/12 (75.0 %), OSM tag 1/12 (8.3 %), area estimate 2/12 (16.7 %), "
            "derived 0/12 (0.0 %)") in printed
    assert result["counts"]["capacity_origin"] == {"reported": 9, "osm_tag": 1, "area_estimate": 2, "derived": 0, "other": 0}
    assert result["counts"]["position_origin"] == {"osm_object": 0, "coordinate_source": 12, "tariff_source_fallback": 0}
    assert result["counts"]["tariff_source_web_service"] == 0
    assert result["counts"]["by_town"] == {"Braunschweig": 3, "Goslar": 4, "Helmstedt": 2, "Salzgitter": 3}
    assert (result["counts"]["free"], result["counts"]["imputed"], result["counts"]["zone_products"],
            result["counts"]["failures"]) == (3, 2, 2, 1)
    monkeypatch.setattr(step, "LARGE_LOT_IMPUTATION_WARNING_SHARE", 0.1)
    step.build_large_lots(_inputs(step, package_file))
    assert "WARNING: more than 10 % of the large lots rest on ASSUMPTION P14 (an imputed tariff)" in capsys.readouterr().out


def test_the_rows_make_a_valid_dataset_and_a_valid_qa_table(step, package_file, regional_tests):
    result = step.build_large_lots(_inputs(step, package_file))
    rows = result["rows"]
    facts = [row.pop("_facts") for row in rows]
    assert all(fact["lot"] and fact["large"] for fact in facts)
    frame = gpd.GeoDataFrame(pd.DataFrame(rows).drop(columns=["geometry"]).reindex(columns=list(pg.DATASET_COLUMNS)),
                             geometry=[row["geometry"] for row in rows], crs=METRIC_CRS)
    for column in pg.MINUTE_COLUMNS + pg.INTEGER_COLUMNS:
        frame[column] = frame[column].astype("Int64")
    for column in pg.MONEY_COLUMNS + pg.HOUR_COLUMNS:
        frame[column] = frame[column].astype(float)
    frame["priced"] = frame["priced"].astype(bool)
    pg.validate_garages(frame)   # ids, positions, one tariff form per row, assumptions named in the notes, five hashes at most
    assert set(frame["facility_kind"]) == {"surface_lot"} and frame["priced"].all()
    assert pg.coverage(frame)["by_facility_kind"]["surface_lot"]["listed"] == 12
    # P14 belongs to a surface lot only
    broken = frame.copy()
    broken.loc[broken["garage_id"] == "lot_t_free", "facility_kind"] = "garage"
    broken.loc[broken["garage_id"] == "lot_t_free", "assumptions"] = "P14"
    broken.loc[broken["garage_id"] == "lot_t_free", "notes"] = "ASSUMPTION P14"
    with pytest.raises(ValueError, match="ASSUMPTION P14 imputes the tariff of a surface lot only"):
        pg.validate_garages(broken)


def test_the_qa_rows_of_the_large_lots_validate_against_the_dataset(step, package_file, regional_tests, tmp_path, monkeypatch):
    """The synthetic regional package and the synthetic research package through ``build_garages`` and ``qa_rows``."""
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", regional_tests._specs())
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", regional_tests.MONTHLY)
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", regional_tests.CANDIDATES)
    monkeypatch.setattr(step.specs, "BS_MONTHLY_SPECS", ())
    monkeypatch.setattr(step.specs, "BS_RECORDED_SPECS", ())
    monkeypatch.setattr(step.specs, "DIRECTORY_DECISIONS", {
        "Parkplatz Markthalle": (step.specs.DIRECTORY_SURFACE_LOT, "a site of the research package"),
        "Parkplatz Werder": ("lot_capacity_unknown", "no capacity")})
    monkeypatch.setattr(step.specs, "DIRECTORY_LOT_KEYS", {"Parkplatz Markthalle": "t_zone_a"})
    regional_tests._write_package(tmp_path / "regional", regional_tests._layers(), regional_tests._rules(),
                                  regional_tests._facilities())
    regional_dir = tmp_path / "regional"
    regional_sha = hashlib.sha256((regional_dir / f"{regional_tests.PACKAGE}.zip").read_bytes()).hexdigest()
    path, large_sha = package_file
    inputs = step.load_garage_inputs(regional_dir, expected_sha256=regional_sha, large_lots_path=path,
                                     expected_large_lots_sha256=large_sha, zones=_zones(), tariffs=_step_tariffs())
    frame = step.build_garages(inputs)
    assert (frame["facility_kind"] == "surface_lot").sum() == 12 and (frame["facility_kind"] == "garage").sum() == 7
    assert frame["garage_id"].is_unique
    rows = step.qa_rows(inputs, frame, step.load_directory(regional_tests._directory(tmp_path, step, monkeypatch)))
    table = pd.DataFrame(rows)
    assert (table["record_type"] == "garage").sum() == 19
    assert "candidate_bs_directory_parkplatz_markthalle" not in set(table["record_id"])   # a site of the package: no second record
    assert table[table["record_id"] == "candidate_bs_directory_parkplatz_werder"].iloc[0]["reason_code"] == "lot_capacity_unknown"
    assert table[table["record_id"] == "garage_lot_t_zone_a"].iloc[0]["decision"] == "priced"
    assert "consistency check against the zone product: rate equal" in table[
        table["record_id"] == "garage_lot_t_zone_a"].iloc[0]["note"]
    assert "candidate_wob_car_parks" not in set(table["record_id"])
    pq.validate_garage_qa(table, frame)
    coverage = pq.qa_coverage(table)
    assert coverage["candidates_by_reason"]["lot_below_threshold"] == 2
    assert coverage["candidates_by_reason"]["lot_tariff_not_expressible"] == 1


def test_a_directory_entry_decided_as_a_site_of_the_package_needs_the_package(
        step, package_file, regional_tests, tmp_path, monkeypatch):
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", regional_tests._specs())
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", regional_tests.MONTHLY)
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", regional_tests.CANDIDATES)
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", {})
    monkeypatch.setattr(step.specs, "BS_MONTHLY_SPECS", ())
    monkeypatch.setattr(step.specs, "BS_RECORDED_SPECS", ())
    monkeypatch.setattr(step.specs, "DIRECTORY_DECISIONS", {
        "Parkplatz Markthalle": (step.specs.DIRECTORY_SURFACE_LOT, "a site"), "Parkplatz Werder": ("bga_zone", "x")})
    monkeypatch.setattr(step.specs, "DIRECTORY_LOT_KEYS", {"Parkplatz Markthalle": "t_zone_a"})
    regional_tests._write_package(tmp_path / "regional", regional_tests._layers(), regional_tests._rules(),
                                  regional_tests._facilities())
    regional_sha = hashlib.sha256((tmp_path / "regional" / f"{regional_tests.PACKAGE}.zip").read_bytes()).hexdigest()
    inputs = step.load_garage_inputs(tmp_path / "regional", expected_sha256=regional_sha)
    frame = step.build_garages(inputs)
    with pytest.raises(SystemExit, match=r"the directory entry 'Parkplatz Markthalle' is decided as a site of the large-lot "
                                         r"package \(t_zone_a\)"):
        step.qa_rows(inputs, frame, step.load_directory(regional_tests._directory(tmp_path, step, monkeypatch)))


# --------------------------------------------------------------------------- the inputs of the step


def test_the_large_lot_package_needs_zones_and_tariffs_and_a_known_threshold(step, package_file, regional_tests, tmp_path):
    regional_tests._write_package(tmp_path / "regional", regional_tests._layers(), regional_tests._rules(),
                                  regional_tests._facilities())
    regional_sha = hashlib.sha256((tmp_path / "regional" / f"{regional_tests.PACKAGE}.zip").read_bytes()).hexdigest()
    path, large_sha = package_file
    common = dict(expected_sha256=regional_sha, large_lots_path=path, expected_large_lots_sha256=large_sha)
    with pytest.raises(SystemExit, match="pass the committed zone polygons .* and the tariff table"):
        step.load_garage_inputs(tmp_path / "regional", **common)
    with pytest.raises(SystemExit, match="pass the committed zone polygons"):
        step.load_garage_inputs(tmp_path / "regional", zones=_zones(), **common)
    with pytest.raises(SystemExit, match="the size threshold 75 is none of the release value 50 and the sensitivities"):
        step.load_garage_inputs(tmp_path / "regional", zones=_zones(), tariffs=_step_tariffs(), lot_threshold=75, **common)
    inputs = step.load_garage_inputs(tmp_path / "regional", zones=_zones(), tariffs=_step_tariffs(), lot_threshold=30, **common)
    assert inputs["large"]["threshold"] == 30 and "lots" not in inputs and inputs["large"]["evidence"] == {}


def test_the_reader_of_the_superseded_wolfsburg_package_is_gone(step, tmp_path):
    """Task 4l: the Wolfsburg car-park package of spec E14 is superseded by the large-lot package and no code reads it: the
    command-line option and the loader argument are gone, only its name and SHA-256 stay in the attribution of the dataset."""
    with pytest.raises(SystemExit) as refused:
        step.main(["--regional-dir", str(tmp_path), "--directory", "d", "--municipalities", "m", "--out", "o", "--qa-out", "q",
                   "--wolfsburg-lots-zip", "x.zip"])
    assert refused.value.code == 2
    assert "lots_path" not in inspect.signature(step.load_garage_inputs).parameters
    assert not hasattr(step.specs, "LOT_SPECS") and not hasattr(step, "wl")
    assert step.SUPERSEDED_WOLFSBURG_LOTS_FILE in step.ATTRIBUTION and step.SUPERSEDED_WOLFSBURG_LOTS_SHA256 in step.ATTRIBUTION


def test_a_sensitivity_threshold_is_never_written_under_the_release_names(step, tmp_path):
    arguments = ["--regional-dir", str(tmp_path), "--directory", "d", "--municipalities", "m", "--lot-threshold", "30"]
    with pytest.raises(SystemExit, match="is a sensitivity of the calibration .*never the release"):
        step.main(arguments + ["--out", str(tmp_path / "parking_garages_2026.geojson"), "--qa-out", str(tmp_path / "q.csv")])
    with pytest.raises(SystemExit, match="is a sensitivity of the calibration .*never the release"):
        step.main(arguments + ["--out", str(tmp_path / "g.geojson"), "--qa-out", str(tmp_path / "parking_garages_2026_qa.csv")])


def test_the_lot_ids_are_stable_keys_of_the_package_sites(step):
    assert step.large_lot_garage_id("braunschweig_wendenstrasse_werder") == "lot_braunschweig_wendenstrasse_werder"
    assert step.large_lot_garage_id("WOB_PARK_1114127") == "lot_wob_park_1114127"
    assert step.large_lot_garage_id("WOB_THEATERPARKPLATZ") == "lot_wob_theaterparkplatz"


def test_texts_of_the_package_are_ascii_with_the_euro_and_section_signs_read_as_such(step):
    assert step._lot_ascii("7 \u20ac nach \u00a7 3 f\u00fcr 2 \u2265 1") == "7 EUR nach sec. 3 fuer 2 >= 1"
    with pytest.raises(SystemExit, match="no ASCII form"):
        step._lot_ascii("a \u4e2d b")


# --------------------------------------------------------------------------- the real package: the primary path

REAL_PACKAGE = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-13" / f"{PACKAGE}.zip"
REAL_ZONES = COMMITTED_PARKING_DIR / "parking_zones_2026.geojson"
REAL_TARIFFS = COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv"
REAL_MONTHLY = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-08" / "Braunschweig_Monatstarife_2026-10-08.zip"
REAL_MONTHLY_EVIDENCE = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-08" / "contipark_configurator_2026-10-08"
REAL_WOB_EVIDENCE = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-14" / f"{RESTLUECKEN_NAME}.zip"
#: The counts of spec Amendment I and of the package's README, written from the package's own tables (its summary and
#: facilities.json), not read back from the code: 152 reviewed records, 57 / 72 / 40 public size candidates at 50 / 30 / 100.
PACKAGE_RECORDS, SHORTLIST_COUNTS = 152, {30: 72, 50: 57, 100: 40}


@pytest.fixture(scope="module")
def real_package(garages_step):
    if not (REAL_PACKAGE.is_file() and REAL_ZONES.is_file() and REAL_TARIFFS.is_file()):
        pytest.skip("the owner's research package and the restricted zone polygons are gitignored and absent here")
    return garages_step.ll.load_package(REAL_PACKAGE)


def test_the_real_package_verifies_and_the_policy_reproduces_its_shortlists(ll, real_package):
    assert real_package["file"]["sha256"] == REAL_PACKAGE_SHA256 and len(real_package["manifest"]) == 307
    assert len(real_package["facilities"]) == PACKAGE_RECORDS and len(real_package["rules"]) == 198
    for threshold, count in SHORTLIST_COUNTS.items():
        options = [key for key, record in real_package["facilities"].items() if ll.decide(record, threshold) is None]
        assert len(options) == count
        assert options == [key for key, record in real_package["facilities"].items() if record[f"public_candidate_{threshold}"]]
    assert len(real_package["points"]) == 120 and real_package["points"].crs.to_epsg() == 25832
    assert real_package["aliases"] == {"WOB_PARK_1900551": "WOB_THEATERPARKPLATZ", "WOB_PARK_1048584": "WOB_THEATERPARKPLATZ",
                                       "WOB_PARK_1441813": "WOB_ARENA_P1"}
    assert len(real_package["crosswalk"]) == 24


@pytest.fixture(scope="module")
def real_inputs(garages_step, real_package):
    if not (REAL_MONTHLY.is_file() and REAL_MONTHLY_EVIDENCE.is_dir()):
        pytest.skip("the Braunschweig monthly package (the commuter products of decision A) is gitignored and absent here")
    if not REAL_WOB_EVIDENCE.is_file():
        pytest.skip("the owner's Restluecken package (the archived pages of the free Wolfsburg lots) is gitignored and absent")
    evidence = garages_step.ll.load_package_free_evidence(REAL_WOB_EVIDENCE)
    zones = gpd.read_file(REAL_ZONES).to_crs(METRIC_CRS)
    tariffs = pz.load_tariffs(REAL_TARIFFS)
    census = {"rate_rules": 54, "stated": 25, "started_unit": 25}   # the regional package's census, as the step prints it
    monthly = garages_step.bsm.load_monthly_inputs(REAL_MONTHLY, REAL_MONTHLY_EVIDENCE)
    recorded = garages_step.bsm.read_recorded_products(monthly, garages_step.specs.BS_RECORDED_SPECS)

    def inputs(threshold):
        return {"large": {"package": real_package, "threshold": threshold, "zones": zones, "tariffs": tariffs,
                          "evidence": evidence},
                "rounding_census": census, "bs_recorded": recorded}

    return inputs


def test_the_real_package_gives_the_rows_of_the_release_by_the_primary_method(garages_step, real_inputs, capsys):
    result = garages_step.build_large_lots(real_inputs(50))
    counts = result["counts"]
    assert counts["options"] == 58 and counts["failures"] == 0 and result["failures"] == []
    # the primary path: 13 + 11 + 32 + 2 = 58 (spec v3 B4 adds the three owner-decided free Wolfsburg lots to the 29 free ones);
    # the imputed lots are the two without a stated tariff
    assert (counts["zone_products"], counts["free"], counts["imputed"]) == (11, 32, 2)
    assert counts["by_town"] == {"Braunschweig": 11, "Gifhorn": 3, "Goslar": 9, "Helmstedt": 5, "Peine": 4, "Salzgitter": 9,
                                 "Wolfenbuettel": 7, "Wolfsburg": 10}
    # the five owner-decided Wolfsburg sites at the threshold 50: the Suedufer, the Freibad and Arena P1 are options (three free
    # pages), P2 has no position, VW-Bad no page; so lot_access_not_public falls by the two Arena lots, lot_free_evidence_missing
    # by the Suedufer and the Freibad, and lot_no_position appears once
    assert counts["qa_by_reason"] == {
        "lot_access_not_public": 46, "lot_alias": 2, "lot_below_threshold": 23, "lot_capacity_unknown": 3,
        "lot_free_evidence_missing": 1, "lot_mixed_tariff_unseparated": 2, "lot_no_position": 1, "lot_not_open": 2,
        "lot_not_surface_lot": 6, "lot_unconfirmed_candidate": 9}
    printed = capsys.readouterr().out
    assert "tariff from the site's own components 13/58, zone street product 11/58, free 32/58, imputed (ASSUMPTION P14) 2/58 " \
           "(3.4 %)" in printed
    assert "free options on archived pages 3/5 (60.0 %), QA rows 2 {'WOB_ARENA_P2': 'lot_no_position', " \
           "'WOB_PARK_327697': 'lot_free_evidence_missing'}" in printed
    # the primary source of each provenance column; the one fallback is Arena P1, whose position is the prior municipal point
    # that the package keeps as 'not a verified entrance' without an OpenStreetMap object or coordinate source (counted, warned)
    assert counts["tariff_source_web_service"] == 0 and counts["limited"] == 18
    assert counts["position_origin"] == {"osm_object": 53, "coordinate_source": 4, "tariff_source_fallback": 1}
    assert printed.count("WARNING") == 1 and "WOB_ARENA_P1 has no source in the package" in printed
    assert counts["capacity_origin"] == {"reported": 34, "osm_tag": 5, "area_estimate": 18, "derived": 1, "other": 0}
    assert "capacity origin: reported 34/58 (58.6 %), OSM tag 5/58 (8.6 %), area estimate 18/58 (31.0 %), derived 1/58 (1.7 %)" \
        in printed
    # 22 lots inside a zone polygon; 9 differ from their zone row (the six of the release at 50 and, since spec v3 B1 and ruling
    # R-B-3,
    # the Salzgitter and Helmstedt lots against the new maximum stays and the Wolfenbuettel Lessingtheater against the new rate)
    assert (counts["in_zone"], len(counts["in_zone_differing"])) == (22, 9)
    ids = [row["garage_id"] for row in result["rows"]]
    assert len(set(ids)) == 58 and all(garage_id.startswith("lot_") for garage_id in ids)
    assert not any("P12" in str(row["assumptions"]) for row in result["rows"])     # the municipal free default is withdrawn
    # every record of the package is an option or exactly one QA row (the 24th point of Wolfsburg, a subpoint, is a QA row too)
    accounted = [row["package_facility_id"] for row in result["rows"]] + [
        entry["subject"].rsplit("(", 1)[1].rstrip(")") for entry in result["qa"]]
    assert len(accounted) == PACKAGE_RECORDS + 1 and len(set(accounted)) == len(accounted)


def test_the_sensitivities_at_30_and_100_stop_at_no_tariff_and_have_the_counts_of_the_package(garages_step, real_inputs):
    # the package's own shortlists are 72 and 40; two mixed Peine sites (and, at 30, three sites without a position) are no row;
    # spec v3 B4 adds the owner-decided free lots that reach the threshold: the Suedufer (68 spaces), the Freibad (71) and Arena P1
    # (494) at 30 (70 options), Arena P1 alone at 100 (39 options); Arena P2 (380 spaces) has no position at any threshold
    low = garages_step.build_large_lots(real_inputs(30))["counts"]
    high = garages_step.build_large_lots(real_inputs(100))["counts"]
    assert low["options"] == 70 and low["failures"] == 0 and low["qa_by_reason"]["lot_no_position"] == 4
    assert high["options"] == 39 and high["failures"] == 0 and high["imputed"] == 0
    assert high["qa_by_reason"]["lot_no_position"] == 1


def test_the_imputed_lots_carry_the_median_product_of_their_zone(garages_step, real_inputs):
    rows = {row["garage_id"]: row for row in garages_step.build_large_lots(real_inputs(50))["rows"]}
    # Buergerpark: the product of bs_zone_ia of Wendenstrasse/Werder and Grosser Hof (1.80 EUR per hour, started minute,
    # 09:00-20:00 and, since spec v3 B1 and ruling R-B-2, the maximum stay of 180 min of the zone in the column max_stay_min: the
    # zone has no long-stay product, so the lots have the plain rate form and no bands)
    park = rows["lot_braunschweig_buergerpark"]
    assert (park["tariff_duration_bands"], park["garage_hourly_rate_eur"], park["garage_billing_unit_min"],
            park["garage_fee_start_h"], park["garage_fee_end_h"], park["assumptions"], park["max_stay_min"]) == (
        None, 1.8, 1, 9.0, 20.0, "P14", 180)
    for donor in ("lot_braunschweig_wendenstrasse_werder", "lot_braunschweig_grosser_hof"):
        assert rows[donor]["tariff_duration_bands"] is None and rows[donor]["assumptions"] is None
        assert (rows[donor]["garage_hourly_rate_eur"], rows[donor]["max_stay_min"]) == (1.8, 180)
    # Buechereivorplatz: the municipal tariff of Konrad-Adenauer-Strasse and Rathausvorplatz (a 30 min grace band)
    library = rows["lot_sz_buechereivorplatz"]
    assert library["tariff_duration_bands"] == "0-30 free; 30-60 total 0.70; 60- 0.10/6"
    assert library["assumptions"] == "P4;P8;P10;P14"
    for donor in ("lot_sz_konrad_adenauer_16", "lot_sz_rathausvorplatz"):
        assert rows[donor]["tariff_duration_bands"] == library["tariff_duration_bands"]
        assert (rows[donor]["garage_fee_start_h"], rows[donor]["garage_fee_end_h"]) == (10.0, 18.0)


def test_decision_a_the_station_lots_and_the_markthalle_carry_exactly_the_product_of_their_own_zone(garages_step, real_inputs):
    rows = {row["garage_id"]: row for row in garages_step.build_large_lots(real_inputs(50))["rows"]}
    tariffs = pz.load_tariffs(REAL_TARIFFS).set_index("zone_id")
    # ruling R-B-2: the maximum stay of the zone, which has no long-stay product, is the column max_stay_min of the lot
    note = rows["lot_braunschweig_wendenstrasse_werder"]["notes"]
    assert "The maximum stay 180 min of the zone, which has no long-stay product, is the column max_stay_min of this row" in note
    assert "9.00 EUR of the zone (assumption M1)" not in note
    assert "long-stay product" not in rows["lot_braunschweig_hbf_nord"]["notes"]
    for garage_id, zone in (("lot_braunschweig_hbf_nord", "bs_hbf_p1_nord"), ("lot_braunschweig_hbf_sued", "bs_hbf_p2_sued"),
                            ("lot_braunschweig_hbf_west", "bs_hbf_p3_west"), ("lot_braunschweig_markthalle", "bs_bga_markthalle")):
        row, product = rows[garage_id], tariffs.loc[zone]
        assert row["facility_kind"] == "surface_lot" and row["assumptions"] is None
        assert (row["garage_hourly_rate_eur"], row["garage_billing_unit_min"]) == (
            product["hourly_rate_eur"], product["billing_unit_min"])
        assert (row["garage_fee_start_h"], row["garage_fee_end_h"]) == (product["fee_start_h"], product["fee_end_h"])
        cap = None if pd.isna(product["daily_cap_eur"]) else product["daily_cap_eur"]
        assert row["garage_daily_cap_eur"] == cap
        assert "rate equal" in row["notes"]   # the package documents the same hourly price as the table
    assert "lot_braunschweig_hbf_nord" in rows and rows["lot_braunschweig_hbf_nord"]["garage_daily_cap_eur"] == 17.0


def test_the_wolfsburg_withdrawal_leaves_no_free_default_and_a_free_site_is_free_only_on_its_archived_page(
        garages_step, real_inputs):
    result = garages_step.build_large_lots(real_inputs(50))
    reasons = {entry["record_id"]: entry["reason"] for entry in result["qa"]}
    # spec v3 B4: the Suedufer and the Freibad are free options on the pages of the Restluecken package (no QA row any more);
    # VW-Bad has no page and stays the one QA row of the owner-decided sites that is no position problem
    assert "candidate_lot_wob_park_720902" not in reasons and "candidate_lot_wob_park_1703940" not in reasons
    assert reasons["candidate_lot_wob_park_327697"] == "lot_free_evidence_missing"
    for number in (327689, 524301, 589853, 262165, 1703946):
        assert reasons[f"candidate_lot_wob_park_{number}"] == "lot_access_not_public"
    notes = {entry["record_id"]: entry["note"] for entry in result["qa"]}
    assert "only wedding guests are evidenced" in notes["candidate_lot_wob_park_327689"]
    assert "about 17 spaces" in notes["candidate_lot_wob_park_262165"]
    assert "the figure 48 is not traceable in the package" in notes["candidate_lot_wob_park_589853"]
    # the Autostadt and the Klinikum are no public option (access conflict, customers only); the Arena is an event regime
    assert reasons["candidate_lot_wob_park_1900571"] == reasons["candidate_lot_wob_park_1966086"] == "lot_access_not_public"
    assert reasons["candidate_lot_wob_park_1048584"] == "lot_alias"
    # the Arena (spec v3 B4): P1 is a free option outside match days; P2 has no position in the package and is no option
    assert "candidate_lot_wob_arena_p1" not in reasons and reasons["candidate_lot_wob_arena_p2"] == "lot_no_position"
    notes = {entry["record_id"]: entry["note"] for entry in result["qa"]}
    assert "owner decision 2026-10-14" in notes["candidate_lot_wob_arena_p2"] and "none is invented" in notes[
        "candidate_lot_wob_arena_p2"]


# the SHA-256 of the pages in the file_manifest.json of the Restluecken package, written from that manifest (not from the code)
PAGE_SHA256 = {"wmg_schotter_suedufer.html": "913948d7e76e8557b8ded1d05dda81ef571f6877491e10a106a831fe78f683d2",
               "wmg_befestigt_suedufer.html": "c3c69b58f1c42ee836137461a8fc9fcca17826915b32d67ff753c219af610227",
               "vfb_geschaeftsstelle.html": "a3fc98c9bddc0de859595fb14e71b8a75b64bdbeb49f8a4350321ac3de1051ab",
               "vfl_anreise.html": "096e2054a8e933ea31d335b1c31752ffdc0eb9776c6abf88675ca7b53fd7c2b4"}


def test_the_pages_of_the_real_restluecken_package_verify_and_hold_the_quotations(ll, real_inputs):
    evidence = ll.load_package_free_evidence(REAL_WOB_EVIDENCE)
    for name, digest in PAGE_SHA256.items():
        assert evidence[name]["sha256"] == digest and evidence[name]["package_sha256"] == RESTLUECKEN_SHA256, name
    assert len(evidence) == 8 and "findings.json" not in evidence          # the original responses only
    assert evidence["vfl_anreise.html"]["url"] == "https://www.vfl-wolfsburg.de/stadien/volkswagen-arena/anreise"
    assert evidence["wmg_schotter_suedufer.html"]["retrieved"] == "2026-10-14"


def test_the_free_wolfsburg_lots_of_spec_v3_b4_rest_on_their_archived_pages_and_carry_no_maximum_stay(garages_step, real_inputs):
    rows = {row["garage_id"]: row for row in garages_step.build_large_lots(real_inputs(50))["rows"]}
    package_hashes = f"{REAL_PACKAGE_SHA256};{RESTLUECKEN_SHA256}"
    for garage_id, pages, url in (
            ("lot_wob_park_720902", ("wmg_schotter_suedufer.html", "wmg_befestigt_suedufer.html"),
             "https://www.wolfsburg-erleben.de/poi/schotter-parkplatz-suedufer-am-allersee"),
            ("lot_wob_park_1703940", ("vfb_geschaeftsstelle.html",), "https://www.vfb-fallersleben.de/dein-vfb/geschaeftsstelle/"),
            ("lot_wob_arena_p1", ("vfl_anreise.html",), "https://www.vfl-wolfsburg.de/stadien/volkswagen-arena/anreise")):
        row = rows[garage_id]
        assert row["facility_kind"] == "surface_lot" and row["priced"], garage_id
        # ASSUMPTION W-match (match days not modelled) is the one assumption of the Arena lot; the pages of the others state it
        assert row["assumptions"] == ("W-match" if garage_id == "lot_wob_arena_p1" else None), garage_id
        assert row["tariff_duration_bands"] == "0- free" and row["max_stay_min"] is None, garage_id
        assert row["tariff_rule_ids"] == ";".join(f"evidence:{page}" for page in pages), garage_id
        assert row["source_url"] == url and row["source_date"] == "2026-10-14" and row["package_sha256"] == package_hashes
        for page in pages:
            assert page in row["notes"] and PAGE_SHA256[page] in row["notes"], (garage_id, page)
        assert "carries no maximum stay" in row["notes"] and "owner decision" in row["notes"]
        assert "Priced from the package components" not in row["notes"] and "Tariff: free of charge" in row["notes"]
    # the Suedufer: the nearest WMG point (Schotter-Parkplatz) lies 162 m, the other 443 m from the research point; no polygon match
    note = rows["lot_wob_park_720902"]["notes"]
    assert "lies 162 m from the research point" in note and "lies 443 m from the research point" in note
    assert "polygon match not_established" in note
    # the claim scope of the package is quoted, and the release extends the claim to the lot by the owner's decision
    assert "'This named POI only. No extension to neighboring facility lots.'" in note
    assert "the release extends the claim to this lot by the owner's decision" in note
    assert rows["lot_wob_park_720902"]["capacity_reported"] is None       # an area estimate (68 spaces), never reported
    # the Freibad: the VfB page names its car parks relatively and has no point of its own
    assert "from the research point" not in rows["lot_wob_park_1703940"]["notes"]
    assert "next to the Freibad or opposite the school" in rows["lot_wob_park_1703940"]["notes"]
    assert ("the free schedule '0- free' (free for every stay) is the owner's decision too: the page gives no hours or stay rule "
            "and the package makes no unrestricted 24/7 claim") in rows["lot_wob_park_1703940"]["notes"]
    # the Arena: the match-day condition is named as an assumption and not modelled; the package reports 494 spaces
    arena = rows["lot_wob_arena_p1"]
    assert "ASSUMPTION W-match" in arena["notes"] and "Match days and event closures are NOT modelled" in arena["notes"]
    assert arena["capacity_reported"] == 494 and "WOB_PARK_1441813" in arena["notes"]
    # its position is the prior municipal point, with its own method and sentence (no research point inside a parking area)
    assert arena["geometry_method"] == "package_prior_municipal_point"
    assert "position: package_prior_municipal_point, the prior municipal point WOB_PARK_1441813" in arena["notes"]
    assert "it is not a research point inside the parking area" in arena["notes"]
    assert all(row["geometry_method"] == "package_research_point_in_parking_area"
               for garage_id, row in rows.items() if garage_id != "lot_wob_arena_p1")   # only P1 has another method
    assert arena["geometry_source_url"] == "https://www.vfl-wolfsburg.de/stadien/volkswagen-arena/anreise/"   # the counted stand-in
    # BadeLand stays a paid option and VW-Bad is no option
    assert rows["lot_wob_park_1114127"]["tariff_duration_bands"] is None and "lot_wob_park_327697" not in rows


# --------------------------------------------------------------------------- the committed release (spec Amendment I)

HOUR_S = 3600
COMMITTED_QA = COMMITTED_PARKING_DIR / "parking_garages_2026_qa.csv"
EVIDENCE_FOLDER = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-13" / "wob_free_lots_evidence"


def _hms(hours: int, minutes: int = 0, seconds: int = 0) -> int:
    return hours * 3600 + minutes * 60 + seconds


#: The documented options of spec v3 B7 (the free garage of Seesen, the free car park of Bad Harzburg) are located in the
#: OpenStreetMap extract and are no sites of the large-lot research package: the tests of this file, which pin the package's lots
#: against the package, leave them out (their pins are in tests/test_parking_documented_options.py).
DOCUMENTED_OPTION_IDS = ("se_parkhaus_bahnhofstrasse", "bh_galopprennbahn")


@pytest.fixture(scope="module")
def committed():
    """The committed garage dataset (a restricted local file: skips when absent), loaded and validated, without the documented
    options of spec v3 B7."""
    garages = pg.load_garages(parking_data_path("parking_garages_2026.geojson"))
    pg.validate_garages(garages)
    garages = garages[~garages["garage_id"].isin(DOCUMENTED_OPTION_IDS)]
    return garages.set_index("garage_id", drop=False)


@pytest.fixture(scope="module")
def committed_qa():
    qa = pq.load_garage_qa(COMMITTED_QA)
    return qa[~qa["garage_id"].isin(DOCUMENTED_OPTION_IDS)].set_index("record_id", drop=False)


def _tariff(committed, garage_id):
    from braunschweig.parking.tariff_export import garage_row_to_tariff
    return garage_row_to_tariff(committed.loc[garage_id])


def _site_of(row) -> str:
    """The package facility key in the subject of a QA row, 'Name (key)'."""
    return row["subject"].rsplit("(", 1)[1].rstrip(")")


def test_the_option_set_of_the_release_is_the_package_shortlist_less_the_two_mixed_peine_sites(committed, real_package):
    shortlist = {key for key, record in real_package["facilities"].items() if record["public_candidate_50"]}
    lots = committed[committed["facility_kind"] == "surface_lot"]
    assert len(shortlist) == 57 and len(lots) == 58
    # the package's own shortlist (written by its author, not by the policy of ``large_lots``) less the two mixed Peine sites, plus
    # the three owner-decided free Wolfsburg lots of spec v3 B4 (the package keeps their access unknown or event-conditional)
    assert set(lots["package_facility_id"]) == shortlist - {"peine_friedrich_ebert_platz", "peine_schuetzenplatz_sued"} | {
        "WOB_PARK_720902", "WOB_PARK_1703940", "WOB_ARENA_P1"}
    # no site of the dataset has the withdrawn municipal free default and none is free without a statement of its source
    assert not committed["assumptions"].fillna("").str.contains("P12").any()
    free = lots[lots["tariff_duration_bands"] == "0- free"]
    for garage_id, row in free.iterrows():
        if row["tariff_rule_ids"].startswith("evidence:"):       # spec v3 B4: the archived pages of the Restluecken package
            assert garage_id in ("lot_wob_park_720902", "lot_wob_park_1703940", "lot_wob_arena_p1"), garage_id
            assert row["package_sha256"].endswith(";80f301c15fa13668397b6f7d127b089370224ce0ad16215dd9b96ec2fa1d8d2c")
            continue
        components = [rule_id for rule_id in row["tariff_rule_ids"].split(";") if rule_id in real_package["rules"]]
        assert components and all(real_package["rules"][rule_id].get("amount_eur") == 0 for rule_id in components), garage_id


def test_every_record_of_the_package_is_exactly_once_a_row_or_a_qa_row_with_the_reason_of_its_fields(
        committed, committed_qa, real_package):
    records = real_package["facilities"]
    options = set(committed.loc[committed["facility_kind"] == "surface_lot", "package_facility_id"])
    qa = committed_qa[committed_qa["record_id"].str.startswith("candidate_lot_")]
    sites = [_site_of(row) for _, row in qa.iterrows()]
    assert len(sites) == len(set(sites)) and not options & set(sites)       # a site is one record
    assert options | set(sites) == set(records) | {"WOB_PARK_1048584"}      # nothing of the package is left out
    # the reason of every record, from the package's own fields in the order of its shortlist rule (a second implementation of
    # the policy; the three Wolfsburg lots that are free only on a per-site source and the two mixed sites are the owner's)
    public = ("public", "public_with_customer_discount", "mixed_public_and_customers")
    owner = {"WOB_ARENA_P2": "lot_no_position", "WOB_PARK_327697": "lot_free_evidence_missing",
             "peine_friedrich_ebert_platz": "lot_mixed_tariff_unseparated",
             "peine_schuetzenplatz_sued": "lot_mixed_tariff_unseparated"}
    for _, row in qa.iterrows():
        site = _site_of(row)
        if site == "WOB_PARK_1048584":
            assert row["reason_code"] == "lot_alias"
            continue
        record = records[site]
        state = record.get("operational_status") or record.get("operating_status") or ""
        capacity = record["capacity_selected_for_threshold"]
        if site in owner:
            expected = owner[site]
        elif record["access_class"] == "closed" or record.get("exclude_current_inventory") or state in (
                "closed_redeveloped", "planned", "under_construction"):
            expected = "lot_not_open"
        elif record["facility_type"] != "surface_lot":
            expected = "lot_not_surface_lot"
        elif record.get("alias_of"):
            expected = "lot_alias"
        elif record.get("candidate_only") or record.get("evidence_class") == "secondary_only":
            expected = "lot_unconfirmed_candidate"
        elif record["access_class"] not in public:
            expected = "lot_access_not_public"
        elif capacity is None:
            expected = "lot_capacity_unknown"
        else:
            expected = "lot_below_threshold"
            assert capacity < 50, site
        assert row["reason_code"] == expected, site
    assert qa["reason_code"].value_counts().sort_index().to_dict() == {
        "lot_access_not_public": 46, "lot_alias": 2, "lot_below_threshold": 23, "lot_capacity_unknown": 3,
        "lot_free_evidence_missing": 1, "lot_mixed_tariff_unseparated": 2, "lot_no_position": 1, "lot_not_open": 2,
        "lot_not_surface_lot": 6, "lot_unconfirmed_candidate": 9}


def test_the_five_withdrawn_wolfsburg_defaults_are_qa_rows_and_the_owner_decided_sites_follow_their_evidence(committed_qa):
    for number, phrase in ((327689, "only wedding guests are evidenced"), (524301, "8-year-old press note"),
                           (262165, "about 17 spaces"), (1703946, "neither a fee nor an access evidence"),
                           (589853, "the figure 48 is not traceable in the package")):
        row = committed_qa.loc[f"candidate_lot_wob_park_{number}"]
        assert row["reason_code"] == "lot_access_not_public" and phrase in row["note"], number
    # spec v3 B4: the Suedufer and the Freibad are free options on the pages of the Restluecken package (their QA rows are the
    # garage rows now), VW-Bad has no page and stays a QA row with what was searched
    for number in (720902, 1703940):
        assert f"candidate_lot_wob_park_{number}" not in committed_qa.index
        assert committed_qa.loc[f"garage_lot_wob_park_{number}", "decision"] == "priced"
    row = committed_qa.loc["candidate_lot_wob_park_327697"]
    assert row["reason_code"] == "lot_free_evidence_missing" and "never free without a source" in row["note"]
    assert "HTTP 404" in row["note"]
    # the folder of the (older) archived evidence exists with its MANIFEST.md (local data): no page is archived in it; the pages of
    # spec v3 B4 are read in place from the Restluecken package
    if EVIDENCE_FOLDER.is_dir():
        manifest = (EVIDENCE_FOLDER / "MANIFEST.md").read_text(encoding="utf-8")
        assert "NO PAGE IS ARCHIVED IN THIS FOLDER" in manifest and "never free by a default" in manifest
        # the pages of spec v3 B4 are named with their member hashes: the manifest is as current as the archive
        for digest in PAGE_SHA256.values():
            assert digest in manifest
    # Autostadt, Klinikum and the Volkswagen Arena are no public options (spec I2: customer, event and conflicting access)
    for number in (1900571, 1966086):
        assert committed_qa.loc[f"candidate_lot_wob_park_{number}", "reason_code"] == "lot_access_not_public"
    # the Arena (spec v3 B4): P1 is a free option outside match days, P2 has no position in the package
    assert "candidate_lot_wob_arena_p1" not in committed_qa.index
    assert committed_qa.loc["garage_lot_wob_arena_p1", "decision"] == "priced"
    assert committed_qa.loc["candidate_lot_wob_arena_p2", "reason_code"] == "lot_no_position"
    assert "candidate_lot_wob_park_1900551" not in committed_qa.index     # the Theater point is the Theaterparkplatz row
    assert committed_qa.loc["candidate_lot_wob_park_1048584", "reason_code"] == "lot_alias"


def test_the_zone_products_of_decision_a_equal_the_table_rows_of_their_zones_and_the_checks_are_equal(committed, committed_qa):
    tariffs = pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")
    pairs = {"lot_braunschweig_hbf_nord": "bs_hbf_p1_nord", "lot_braunschweig_hbf_sued": "bs_hbf_p2_sued",
             "lot_braunschweig_hbf_west": "bs_hbf_p3_west", "lot_braunschweig_markthalle": "bs_bga_markthalle",
             "lot_braunschweig_wendenstrasse_werder": "bs_zone_ia", "lot_braunschweig_grosser_hof": "bs_zone_ia",
             "lot_wob_park_1638410": "wob_tarifzone_2", "lot_wob_park_1769502": "wob_area_680",
             "lot_wob_park_1835014": "wob_tarifzone_2", "lot_gs_baeringer": "gs_parkplatz_baeringerstrasse",
             "lot_gs_klubgarten": "gs_parkplatz_klubgartenstrasse_zob"}
    for garage_id, zone in pairs.items():
        row, product = committed.loc[garage_id], tariffs.loc[zone]
        # the street equivalent of the row equals the table row in every compared column: rate, unit, grace, cap, fee window and
        # the maximum stay with its long-stay product (the bands of zone Ia) and the commuter product (ruling R-4j-6)
        assert pq.street_differences(pq.lot_street_equivalent(row), pq.zone_street_equivalent(product)) == [], garage_id
        assert f"parking_tariffs_2026.csv:{zone}" in row["tariff_rule_ids"]
        assert "the zone tariff is not changed by it" in row["notes"]
        assert "rate equal" in committed_qa.loc[f"garage_{garage_id}", "note"], garage_id
        # the plain rate form for every zone product; the maximum stay of a zone WITHOUT a long-stay product (Ia, ruling R-B-2)
        # is the
        # column max_stay_min of the lot, empty on every other lot
        assert pd.isna(row["tariff_duration_bands"]) and pd.isna(row["assumptions"]), garage_id
        assert (row["garage_hourly_rate_eur"], row["garage_billing_unit_min"]) == (
            product["hourly_rate_eur"], product["billing_unit_min"]), garage_id
        assert (row["garage_fee_start_h"], row["garage_fee_end_h"]) == (product["fee_start_h"], product["fee_end_h"]), garage_id
        assert _same(row["garage_daily_cap_eur"], product["daily_cap_eur"]), garage_id
        assert _same(row["max_stay_min"], product["max_stay_min"]), garage_id
        if zone == "bs_zone_ia":
            assert row["max_stay_min"] == 180
            assert "the column max_stay_min of this row (ASSUMPTION M3, ruling R-B-2)" in row["notes"]
    # the paid hours that the package states in free text are compared where they state a weekday window (finding I5): the
    # CongressPark states 07:00-15:00 against the zone's 07:00-18:00, Cinemaxx and the Amtsgericht 07:00-18:00
    assert ("fee window DIFFERS (the package states '07:00-15:00, Sa 07:00-14:00', the zone row 07:00-18:00)"
            in committed_qa.loc["garage_lot_wob_park_1638410", "note"])
    for garage_id in ("lot_wob_park_1769502", "lot_wob_park_1835014"):
        assert "fee window equal (read from the free text '07:00-18:00, Sa 07:00-14:00' on weekdays)" in committed_qa.loc[
            f"garage_{garage_id}", "note"]
    assert "fee window equal (24/7)" in committed_qa.loc["garage_lot_braunschweig_hbf_nord", "note"]
    # the unit of the package against the unit of the zone row, both sources named (ruling R-4j-7 a)
    for garage_id in ("lot_wob_park_1638410", "lot_braunschweig_grosser_hof", "lot_gs_baeringer"):
        assert "billing unit DIFFERS: the package states" in committed_qa.loc[f"garage_{garage_id}", "note"], garage_id


def _same(left, right) -> bool:
    return (pd.isna(left) and pd.isna(right)) or left == right


def test_the_imputed_lots_are_named_with_their_donors_and_carry_the_donors_schedule(committed):
    for garage_id, donors in (("lot_braunschweig_buergerpark", ("lot_braunschweig_wendenstrasse_werder",
                                                                  "lot_braunschweig_grosser_hof")),
                              ("lot_sz_buechereivorplatz", ("lot_sz_konrad_adenauer_16", "lot_sz_rathausvorplatz"))):
        row = committed.loc[garage_id]
        assert "P14" in row["assumptions"].split(";") and "ASSUMPTION P14 basis" in row["notes"]
        assert "the median product of the 2 priced lots of the zone" in row["notes"] and "level zone" in row["notes"]
        columns = ["garage_hourly_rate_eur", "garage_billing_unit_min", "garage_daily_cap_eur", "garage_fee_start_h",
                   "garage_fee_end_h", "tariff_duration_bands"]
        for donor in donors:
            for column in columns:
                assert _same(row[column], committed.loc[donor, column]), (garage_id, donor, column)
    assert committed["assumptions"].fillna("").str.contains("P14").sum() == 2


def test_the_free_lots_price_every_stay_at_zero_through_the_same_code_without_a_special_case(committed):
    from braunschweig.parking import cost
    free = [garage_id for garage_id, row in committed.iterrows() if row["tariff_duration_bands"] == "0- free"]
    assert len(free) == 32 and all(garage_id.startswith("lot_") for garage_id in free)
    for garage_id in free:
        garage = _tariff(committed, garage_id)
        assert garage.form == "bands" and garage.hourly_rate_cents is None and garage.daily_cap_cents is None
        assert garage.to_json()["bands"] == [{"from_min": 0, "to_min": None, "kind": "free", "price_cents": 0, "unit_min": None}]
        for stay_s in (0, 60, 5 * HOUR_S, 30 * HOUR_S):
            assert cost.garage_option_cents(garage, _hms(9), _hms(9) + stay_s, purpose="work") == 0


@pytest.mark.parametrize("stay_s, expected_cents", [
    # the Broetchentaste of the Helmstedt Suedertor, Mo-Fr 09-16 h: 30 min are free, a longer stay is billed from its arrival in
    # started half hours at 0.60 EUR (the free minutes are not deducted: ASSUMPTION P10), the day ticket of 3.00 EUR is the cap
    (0, 0), (_hms(0, 30), 0), (_hms(0, 30, 1), 120), (_hms(0, 31), 120), (_hms(1), 120), (_hms(1, 0, 1), 180),
    (_hms(1, 30), 180), (_hms(1, 30, 1), 240), (_hms(2), 240), (_hms(2, 30), 300), (_hms(3), 300), (_hms(6), 300),
])
def test_the_helmstedt_grace_lot_is_priced_at_its_edges_by_the_garage_option_code(committed, stay_s, expected_cents):
    from braunschweig.parking import cost
    garage = _tariff(committed, "lot_helmstedt_suedertor")
    assert garage.form == "bands" and garage.daily_cap_cents == 300 and (garage.fee_start_s, garage.fee_end_s) == (
        _hms(9), _hms(16))
    assert cost.garage_metered_cents(garage, _hms(10), _hms(10) + stay_s) == expected_cents


def test_a_stay_outside_the_fee_window_of_a_lot_is_free_and_one_across_its_end_pays_the_part_inside(committed):
    from braunschweig.parking import cost
    garage = _tariff(committed, "lot_helmstedt_suedertor")
    assert cost.garage_metered_cents(garage, _hms(16, 30), _hms(18)) == 0
    assert cost.garage_metered_cents(garage, _hms(7), _hms(8, 30)) == 0
    # 15:00 to 17:00: one chargeable hour (15:00-16:00) = 1.20 EUR
    assert cost.garage_metered_cents(garage, _hms(15), _hms(17)) == 120


def test_the_peine_schlosswall_is_the_cheapest_of_its_three_products_for_every_stay_of_the_day(committed):
    """0.50 EUR per started half hour, the half-day ticket (2.60 EUR, 4 h) and the day ticket (4.20 EUR), Mo-Fr 09-17 h: the price
    of every stay of 1 to 480 min from 09:00 is the minimum of the products that are valid for it (written from the published
    products, not from the bands)."""
    from braunschweig.parking import cost
    garage = _tariff(committed, "lot_peine_schlosswall")
    for minutes in range(1, 481):
        metered = 50 * (-(-minutes // 30))
        products = [metered, 420] + ([260] if minutes <= 240 else [])
        assert cost.garage_metered_cents(garage, _hms(9), _hms(9) + minutes * 60) == min(products), minutes


@pytest.mark.parametrize("minutes, expected_cents", [
    # the municipal tariff of Lebenstedt at the Rathausvorplatz, Mo-Fr 10-18 h: 30 min free, the first hour of a longer stay 0.70
    # EUR, then 0.10 EUR per started 6 min (the free minutes are not deducted)
    (30, 0), (31, 70), (60, 70), (61, 80), (66, 80), (67, 90), (120, 170), (180, 270),
])
def test_the_salzgitter_municipal_tariff_is_priced_at_its_edges_and_the_imputed_lot_costs_the_same(
        committed, minutes, expected_cents):
    from braunschweig.parking import cost
    for garage_id in ("lot_sz_rathausvorplatz", "lot_sz_konrad_adenauer_16", "lot_sz_buechereivorplatz"):
        garage = _tariff(committed, garage_id)
        assert cost.garage_metered_cents(garage, _hms(10), _hms(10) + minutes * 60) == expected_cents, garage_id


def test_a_zone_product_lot_charges_the_zone_rate_per_started_minute_inside_the_zone_window(committed):
    from braunschweig.parking import cost
    for garage_id in ("lot_braunschweig_wendenstrasse_werder", "lot_braunschweig_buergerpark"):
        garage = _tariff(committed, garage_id)
        assert cost.garage_metered_cents(garage, _hms(10), _hms(11, 30)) == 270     # 90 min at 1.80 EUR per hour
        assert cost.garage_metered_cents(garage, _hms(10), _hms(10, 0, 1)) == 3     # one started minute
        assert cost.garage_metered_cents(garage, _hms(21), _hms(22)) == 0           # outside 09-20 h
    nord = _tariff(committed, "lot_braunschweig_hbf_nord")
    assert nord.daily_cap_cents == 1700 and cost.garage_metered_cents(nord, _hms(8), _hms(8) + 10 * HOUR_S) == 1700


def _street_zone():
    from braunschweig.parking.cost import ZoneTariff
    return ZoneTariff(zone_id="test_street", zone_type="street_paid", hourly_rate_cents=180, billing_unit_min=1,
                      free_if_stay_at_most_min=None, first_period_min=None, first_period_cents=None, daily_cap_cents=900,
                      max_stay_min=None, long_stay_product_cents=None, member_day_cents=None, guest_day_cents=None,
                      fee_start_s=32400, fee_end_s=72000, resident_exempt=False)


def test_a_committed_free_option_lowers_the_expected_cost_of_a_paid_street_stay_within_the_maximum_distance(committed):
    from braunschweig.parking import cost
    theater = _tariff(committed, "lot_wob_theaterparkplatz")
    # the destination lies 500 m (a 300-400-500 triangle) from the free Theaterparkplatz; lambda = 400 m
    x, y = theater.x_m + 300.0, theater.y_m + 400.0
    options = cost.garage_options_in_range([theater], x, y)
    assert [(garage.garage_id, distance) for garage, distance in options] == [("lot_wob_theaterparkplatz", 500.0)]
    # stay 10:00-11:00 = 60 chargeable min: street 60 x 3 ct = 180 ct; w = exp(-1.25) = 0.2865047969,
    # P_garage = w / (1 + w) = 0.2227001388, expected = 180 / 1.2865047969 = 139.91 -> 140 ct (the street alone: 180 ct)
    street = _street_zone()
    assert cost.parking_cost_with_garages(street, _hms(10), _hms(11), purpose="shop", parking_free=False,
                                          resident_of_zone=False, decay_commute_m=400.0)[0] == 180
    cents, outcome, probability = cost.parking_cost_with_garages(
        street, _hms(10), _hms(11), purpose="shop", parking_free=False, resident_of_zone=False, garage_options=options,
        decay_m=400.0, decay_commute_m=400.0)
    assert (cents, outcome) == (140, cost.PAID_EXPECTED) and probability == pytest.approx(0.2227001388, abs=1e-9)


def test_a_committed_free_option_alone_never_makes_a_street_free_stay_cost_anything_e4(committed):
    from braunschweig.parking import cost
    options = [(_tariff(committed, garage_id), 0.0)
               for garage_id in ("lot_wob_theaterparkplatz", "lot_wob_park_1835028", "lot_gs_fuellekuhle")]
    street = _street_zone()
    # before the street fee window (09:00-20:00) and after it the street is free: nobody pays a lot, a free one adds nothing
    for arrival_s, departure_s in ((_hms(7), _hms(8)), (_hms(21), _hms(22))):
        assert cost.parking_cost_with_garages(street, arrival_s, departure_s, purpose="shop", parking_free=False,
                                              resident_of_zone=False, garage_options=options, decay_m=400.0,
                                              decay_commute_m=400.0) == (0, cost.OUTSIDE_FEE_HOURS, 0.0)
    # a priced option beside the free ones never changes a free street either (the Wendenstrasse/Werder lot costs 270 ct per 90 min)
    paid = options + [(_tariff(committed, "lot_braunschweig_wendenstrasse_werder"), 0.0)]
    assert cost.parking_cost_with_garages(street, _hms(7), _hms(8, 30), purpose="shop", parking_free=False,
                                          resident_of_zone=False, garage_options=paid, decay_m=400.0,
                                          decay_commute_m=400.0)[0] == 0


def test_the_tariff_model_export_converts_every_lot_of_the_release_with_its_form(committed):
    lots = [garage_id for garage_id in committed.index if garage_id.startswith("lot_")]
    entries = [_tariff(committed, garage_id).to_json() for garage_id in lots]
    assert len(entries) == 58 and {entry["garage_id"] for entry in entries} == set(lots)
    free = [entry for entry in entries
            if entry["bands"] and entry["bands"][0]["kind"] == "free" and entry["bands"][0]["to_min"] is None]
    assert len(free) == 32


# --------------------------------------------------------------------------- the zone product is exact to the cent (R-4j-6)
HOUR_SECONDS = 3600
SWEEP_ARRIVALS_H = (6, 9, 14, 19)
SWEEP_PURPOSES = ("work", "shopping")


def _zone_tariff(row: dict) -> cost.ZoneTariff:
    """The ``ZoneTariff`` of a row of the synthetic tariff table (the columns that it lacks are empty cells)."""
    full = {column: float("nan") for column in te.REQUIRED_COLUMNS}
    full.update(zone_type="street_paid", resident_exempt=False)
    full.update({key: value for key, value in row.items() if key in te.TARIFF_COLUMNS})
    return te.tariff_row_to_zone(full, snapshot_date=None)


def _lot_frame(rows) -> gpd.GeoDataFrame:
    """The dataset of the rows that ``build_large_lots`` returns (the ``_facts`` of the rows removed)."""
    rows = [{key: value for key, value in row.items() if key != "_facts"} for row in rows]
    frame = gpd.GeoDataFrame(pd.DataFrame(rows).drop(columns=["geometry"]).reindex(columns=list(pg.DATASET_COLUMNS)),
                             geometry=[row["geometry"] for row in rows], crs=METRIC_CRS)
    for column in pg.MINUTE_COLUMNS + pg.INTEGER_COLUMNS:
        frame[column] = frame[column].astype("Int64")
    for column in pg.MONEY_COLUMNS + pg.HOUR_COLUMNS:
        frame[column] = frame[column].astype(float)
    frame["priced"] = frame["priced"].astype(bool)
    return frame


def sweep_mismatches(zone: cost.ZoneTariff, garage: cost.GarageTariff, durations_min=range(1, 721)) -> list:
    """Every stay of ``durations_min`` minutes at the arrivals ``SWEEP_ARRIVALS_H`` and for the purposes ``SWEEP_PURPOSES`` that the
    street product of ``zone`` (``cost.parking_cost_cents``, the ZONE path) and the garage option of ``garage`` (``cost.
    garage_option_cents``, the LOT row alone) price differently: (arrival h, minutes, purpose, street cents, lot cents)."""
    mismatches = []
    for arrival_h in SWEEP_ARRIVALS_H:
        arrival_s = arrival_h * HOUR_SECONDS
        for minutes in durations_min:
            for purpose in SWEEP_PURPOSES:
                street = cost.parking_cost_cents(zone, arrival_s, arrival_s + minutes * 60, purpose=purpose, parking_free=False,
                                                 resident_of_zone=False)[0]
                lot = cost.garage_option_cents(garage, arrival_s, arrival_s + minutes * 60, purpose=purpose)
                if street != lot:
                    mismatches.append((arrival_h, minutes, purpose, street, lot))
    return mismatches


def test_the_maximum_stay_of_a_zone_is_written_as_bands_that_price_every_stay_like_the_street(step, package_file):
    result = step.build_large_lots(_inputs(step, package_file))
    frame = _lot_frame(result["rows"]).set_index("garage_id", drop=False)
    zone = _zone_tariff(_step_tariffs().set_index("zone_id", drop=False).loc["t_ia"].to_dict())
    for garage_id in ("lot_t_zone_a", "lot_t_zone_b", "lot_t_p14_zone"):      # the P14 lot takes the donors' schedule
        garage = te.garage_row_to_tariff(frame.loc[garage_id])
        assert garage.bands is not None and garage.hourly_rate_cents is None
        assert sweep_mismatches(zone, garage) == [], garage_id
    # the same on stays that are no whole number of minutes: a seeded sample at second resolution, over several days
    garage = te.garage_row_to_tariff(frame.loc["lot_t_zone_a"])
    generator = random.Random(7)
    for _ in range(20_000):
        arrival_s = generator.randrange(0, 86_400)
        departure_s = arrival_s + generator.randrange(1, 40 * HOUR_SECONDS)
        for purpose in SWEEP_PURPOSES:
            assert cost.garage_option_cents(garage, arrival_s, departure_s, purpose=purpose) == cost.parking_cost_cents(
                zone, arrival_s, departure_s, purpose=purpose, parking_free=False, resident_of_zone=False)[0]


def test_the_sweep_would_catch_a_zone_product_without_the_bands_of_its_maximum_stay(step, package_file):
    """The control of the sweep: the plain rate (the lot as it was before ruling R-4j-6) prices the long stays differently."""
    result = step.build_large_lots(_inputs(step, package_file))
    row = _by_id(result)["lot_t_zone_a"]
    plain = dict(row, tariff_duration_bands=None, garage_hourly_rate_eur=1.8, garage_billing_unit_min=1)
    zone = _zone_tariff(_step_tariffs().set_index("zone_id", drop=False).loc["t_ia"].to_dict())
    garage = te.garage_row_to_tariff(_lot_frame([plain]).iloc[0])
    mismatches = sweep_mismatches(zone, garage)
    # 06:00 plus 361 min: 181 min lie in the fee window 09:00-20:00, so the street buys the long-stay product (9.00 EUR) and the
    # plain rate charges 181 started minutes (5.43 EUR)
    assert mismatches[0] == (6, 361, "work", 900, 543) and len(mismatches) > 1000


# --------------------------------------------------------------------------- decision A: the commuter product (R-4j-6 a)


def _recorded_station_product(**changes) -> dict:
    product = {"record_id": "monthly_t_zone_a", "facility": "BS_X", "name": "Parkplatz X", "decision": "used", "reason": "",
               "zone_ids": ("t_ia",), "amount_eur": 74.0, "product_name": "1 Monat", "note": "n", "evidence": ["pkg", "offer"],
               "monthly_source_url": "https://op.example/month", "monthly_product": "1 Monat: 74.00 EUR per month, VAT included"}
    product.update(changes)
    return product


def test_a_decision_a_lot_carries_the_commuter_product_of_its_zone_as_its_monthly_product(step, package_file, monkeypatch):
    monkeypatch.setattr(step.specs, "LARGE_LOT_ZONE_MONTHLY", {"t_zone_a": "monthly_t_zone_a"})
    inputs = _inputs(step, package_file)
    inputs["bs_recorded"] = [_recorded_station_product()]
    result = step.build_large_lots(inputs)
    rows = _by_id(result)
    lot = rows["lot_t_zone_a"]
    assert (lot["monthly_eur"], lot["monthly_source_url"], lot["monthly_product"]) == (
        74.0, "https://op.example/month", "1 Monat: 74.00 EUR per month, VAT included")
    # the zone's commuter product per working day (ASSUMPTION P2: 74.00 / 21 = 3.52 EUR) is named, and the price is the zone's
    assert ("Monthly product (spec Amendment I1, owner decision A): 1 Monat: 74.00 EUR per month, VAT included; it is the "
            "commuter product of the zone t_ia (3.52 EUR per working day, ASSUMPTION P2).") in lot["notes"]
    assert "monthly product 74.00 EUR" in result["notes"]["lot_t_zone_a"]
    assert rows["lot_t_zone_b"]["monthly_eur"] is None       # a lot without the decision carries none
    frame = _lot_frame(result["rows"]).set_index("garage_id", drop=False)
    assert te.garage_row_to_tariff(frame.loc["lot_t_zone_a"]).monthly_cents == 7400


@pytest.mark.parametrize("change, message", [
    ({"decision": "not_used", "reason": "not_the_cheapest", "zone_ids": ()}, "is not the used commuter product"),
    ({"zone_ids": ("t_hbf",)}, "is the commuter product of the zone"),
    ({"record_id": "monthly_other"}, "needs the recorded product monthly_t_zone_a"),
])
def test_a_commuter_product_that_is_not_the_one_of_the_zone_of_the_lot_stops_the_step(step, package_file, monkeypatch, change,
                                                                                    message):
    monkeypatch.setattr(step.specs, "LARGE_LOT_ZONE_MONTHLY", {"t_zone_a": "monthly_t_zone_a"})
    inputs = _inputs(step, package_file)
    inputs["bs_recorded"] = [_recorded_station_product(**change)]
    with pytest.raises(SystemExit, match=message):
        step.build_large_lots(inputs)


def test_the_commuter_product_needs_the_monthly_package(step, package_file, monkeypatch):
    monkeypatch.setattr(step.specs, "LARGE_LOT_ZONE_MONTHLY", {"t_zone_a": "monthly_t_zone_a"})
    with pytest.raises(SystemExit, match="decision A gives it the commuter product monthly_t_zone_a of its zone, which needs the "
                                         "Braunschweig monthly package"):
        step.build_large_lots(_inputs(step, package_file))


def test_the_monthly_qa_row_of_a_station_product_names_the_lot_that_carries_it(step):
    recorded = [_recorded_station_product(), _recorded_station_product(record_id="monthly_other", zone_ids=())]
    rows = step.bsm.qa_product_rows({}, recorded, {}, lot_garage_ids={"monthly_t_zone_a": "lot_t_zone_a"})
    assert [(row["record_id"], row["garage_id"], row["zone_ids"]) for row in rows] == [
        ("monthly_t_zone_a", "lot_t_zone_a", "t_ia"), ("monthly_other", None, "")]
    # without the lots the rows are as before (a recorded facility has no garage_id)
    assert [row["garage_id"] for row in step.bsm.qa_product_rows({}, recorded, {})] == [None, None]


def test_the_coverage_counts_the_monthly_products_of_the_garages_and_names_those_of_the_lots_apart():
    from braunschweig.parking import garages as garages_module

    assert "lots_with_monthly_product" in garages_module.coverage.__doc__
    frame = _lot_frame([{"garage_id": "lot_a", "package_facility_id": "a", "name": "A", "facility_kind": "surface_lot",
                         "municipality": "Braunschweig", "municipality_ags": BS, "capacity_reported": None, "capacity_scope": None,
                         "monthly_eur": 74.0, "monthly_source_url": "https://x.example/m", "monthly_product": "m", "priced": True,
                         "not_priced_reason": None, "assumptions": None, "source_url": SOURCE_URL, "source_date": "2026-10-12",
                         "tariff_rule_ids": "r", "geometry_method": "m", "geometry_source_url": SOURCE_URL,
                         "package_sha256": "0" * 64, "notes": "n", "operator": None, "garage_hourly_rate_eur": 1.0,
                         "garage_billing_unit_min": 30, "garage_fee_start_h": 8.0, "garage_fee_end_h": 18.0,
                         "geometry": Point(X0, Y0)}])
    counts = garages_module.coverage(frame)
    assert counts["with_monthly_product"] == 0 and counts["lots_with_monthly_product"] == 1



def test_the_station_lots_carry_the_commuter_product_of_their_zone_and_name_every_other_component(committed, committed_qa):
    """Decision A (ruling R-4j-6): the lot prices a work stay like the zone, the other components are named, none is dropped."""
    tariffs = pz.load_tariffs(REAL_TARIFFS).set_index("zone_id")
    stations = (("lot_braunschweig_hbf_nord", "bs_hbf_p1_nord", "monthly_bs_hbf_nord_p1", 120.0,
                 ("braunschweig_hbf_nord__2", "braunschweig_hbf_nord__3", "braunschweig_hbf_nord__5")),
                ("lot_braunschweig_hbf_sued", "bs_hbf_p2_sued", "monthly_bs_hbf_sued_p2", 74.0,
                 ("braunschweig_hbf_sued__2", "braunschweig_hbf_sued__3", "braunschweig_hbf_sued__5")),
                ("lot_braunschweig_hbf_west", "bs_hbf_p3_west", "monthly_bs_hbf_west_p3", 74.0,
                 ("braunschweig_hbf_west__2", "braunschweig_hbf_west__3", "braunschweig_hbf_west__5")))
    for garage_id, zone, record_id, amount, others in stations:
        row = committed.loc[garage_id]
        assert row["monthly_eur"] == amount
        assert row["monthly_product"].startswith(f"1 Monat fuer Stellplatzmieter: {amount:.2f} EUR")
        assert row["monthly_source_url"].startswith("https://") and "commuter product of the zone" in row["notes"]
        assert round(amount / 21.0 + 1e-9, 2) == tariffs.loc[zone, "commuter_day_eur"]
        month = committed_qa.loc[record_id]
        assert month["garage_id"] == garage_id and month["zone_ids"] == zone and month["decision"] == "used"
        # the package components that the lot does not model are named in its notes, each with its rule id
        for rule_id in others:
            assert f"Not used: {rule_id}" in row["notes"], rule_id
    # the Markthalle has no commuter product (its zone has none), and no lot is a garage: the garage counts are unchanged
    assert pd.isna(committed.loc["lot_braunschweig_markthalle", "monthly_eur"])
    assert committed["monthly_eur"].notna().sum() == 15 and committed.loc[committed["facility_kind"] == "garage", "monthly_eur"
                                                                         ].notna().sum() == 12


def test_a_billing_unit_that_the_package_states_differently_is_named_with_both_sources(ll, package_file):
    path, sha256 = package_file
    package = ll.load_package(path, sha256)
    rule = package["rules"]["t_zone_a__1"]
    rule["amount_eur"], rule["billing_unit_minutes"] = 1.0, 60          # 1.00 EUR per hour, the package's reading of the unit
    tariffs = _tariffs()
    tariffs["source_url"], tariffs["source_date"] = "https://zone.example/service", "2026-10-07"
    tariffs["notes"] = ("ASSUMPTION D3-a: billing_unit_min 30, the started half hour of the ordinance, because the page "
                        "states no unit")
    tariffs.loc[tariffs["zone_id"] == "t_ia", ["hourly_rate_eur", "billing_unit_min"]] = [1.0, 30]
    product = ll.zone_product_rules(tariffs, "t_ia", "t_zone_a")
    sentence, verdicts = ll.zone_check_sentence(package, "t_zone_a", "t_zone_a__1", product, "t_ia")
    assert verdicts == ["rate equal", "billing unit DIFFERS: the package states 60 min (source src_prices "
                                      "https://op.example/prices of 2026-10-12), the zone row 30 min (source "
                                      "https://zone.example/service of 2026-10-07, ASSUMPTION D3-a)"]
    assert verdicts[1] in sentence and "the zone tariff is not changed by it" in sentence
    # the lot keeps the zone's reading (a started half hour): the package component stands as documented, only named
    assert product["rules"][product["rate"]]["billing_unit_minutes"] == 30
    # the same unit on both sides is no difference to name
    rule["billing_unit_minutes"], rule["amount_eur"] = 30, 0.5
    equal = ll.zone_check_sentence(package, "t_zone_a", "t_zone_a__1", product, "t_ia")[1]
    assert equal == ["rate equal"]


@pytest.mark.parametrize("hours, verdict", [
    ("07:00-18:00, Sa 07:00-14:00", "fee window equal (read from the free text '07:00-18:00, Sa 07:00-14:00' on weekdays)"),
    ("Mo-Fr 07:00-18:00", "fee window equal (read from the free text 'Mo-Fr 07:00-18:00' on weekdays)"),
    ("07:00-15:00, Sa 07:00-14:00",
     "fee window DIFFERS (the package states '07:00-15:00, Sa 07:00-14:00', the zone row 07:00-18:00)"),
    ("by arrangement", "fee window not compared (the package states its weekdays only in free text)"),
])
def test_paid_hours_in_free_text_are_compared_where_they_state_a_weekday_window_and_named_where_they_differ(
        ll, package_file, hours, verdict):
    path, sha256 = package_file
    package = ll.load_package(path, sha256)
    rule = package["rules"]["t_zone_a__1"]
    rule["schedule"], rule["paid_hours"] = None, hours
    tariffs = _tariffs()
    tariffs.loc[tariffs["zone_id"] == "t_ia", ["fee_start_h", "fee_end_h"]] = [7.0, 18.0]
    product = ll.zone_product_rules(tariffs, "t_ia", "t_zone_a")
    sentence, verdicts = ll.zone_check_sentence(package, "t_zone_a", "t_zone_a__1", product, "t_ia")
    assert verdict in verdicts and verdict in sentence


def test_the_two_goslar_lots_that_are_single_site_zones_carry_the_zone_row_and_name_the_unit_of_the_package(
        committed, committed_qa):
    """Ruling R-4j-7 a: the same car park as the zone, so the lot is zone AND lot (decision A) and carries exactly the zone row; the
    unit that the package reads (an hour) beside the one of the zone row (a started half hour, assumption D3-a) is named."""
    for garage_id, zone, window in (("lot_gs_baeringer", "gs_parkplatz_baeringerstrasse", (10.0, 18.0)),
                                    ("lot_gs_klubgarten", "gs_parkplatz_klubgartenstrasse_zob", (10.0, 16.0))):
        row = committed.loc[garage_id]
        assert (row["garage_hourly_rate_eur"], row["garage_billing_unit_min"], row["garage_fee_start_h"],
                row["garage_fee_end_h"]) == (1.0, 30, *window)
        assert pd.isna(row["assumptions"]) and f"parking_tariffs_2026.csv:{zone}" in row["tariff_rule_ids"]
        assert "owner decision A" in row["notes"]
        note = committed_qa.loc[f"garage_{garage_id}", "note"]
        assert "billing unit DIFFERS: the package states 60 min" in note and "the zone row 30 min" in note
        assert "ASSUMPTION D3-a" in note and "https://www.meingoslar.de/service" in note


# --------------------------------------------------------------------------- the pass over every lot inside a zone (R-4j-7)


def _street_zone_tables():
    """The synthetic zones and tariffs with one more zone, ``t_street``, around the lot ``t_rate``: 1.00 EUR per hour in started
    units of 30 min, at most 8.00 EUR, 10-18 h (the lot reads an hour: 1.00 EUR per started 60 min, the same cap and window)."""
    nan = float("nan")
    zones = pd.concat([_zones(), gpd.GeoDataFrame({"zone_id": ["t_street"]}, crs=METRIC_CRS,
                                                   geometry=[box(X0 + 3150, Y0 - 50, X0 + 3250, Y0 + 50)])], ignore_index=True)
    base = _step_tariffs()
    tariffs = pd.concat([base, pd.DataFrame([["t_street", 1.0, 30, nan, nan, nan, 8.0, 10.0, 18.0, nan, nan]],
                                            columns=base.columns)], ignore_index=True)
    return zones, tariffs


def test_a_lot_inside_a_zone_polygon_is_compared_with_the_zone_row_and_every_difference_is_named(step, package_file, capsys):
    zones, tariffs = _street_zone_tables()
    result = step.build_large_lots(_inputs(step, package_file, zones=zones, tariffs=tariffs))
    consistency = result["zone_consistency"]
    # an injected lot whose unit differs (an hour against a started half hour): named, and the values stay as the package says
    assert consistency["lot_t_rate"] == {"t_street": ["billing_unit_min 60 against 30"]}
    row = _by_id(result)["lot_t_rate"]
    assert row["garage_billing_unit_min"] == 60 and row["garage_hourly_rate_eur"] == 1.0 and row["garage_daily_cap_eur"] == 8.0
    sentence = ("Inside the zone t_street (report-only pass, ruling R-4j-7): the row DIFFERS from the zone row: "
                "billing_unit_min 60 against 30; the package component stands as documented and no value is changed.")
    assert sentence in row["notes"] and result["notes"]["lot_t_rate"].endswith(sentence)
    # a lot that carries the zone's product is equal; a lot outside every zone polygon is not part of the pass
    assert consistency["lot_t_zone_a"] == {"t_ia": []} and consistency["lot_t_zone_b"] == {"t_ia": []}
    assert consistency["lot_t_p14_zone"] == {"t_ia": []}
    assert "lot_t_free" not in consistency and "lot_t_bands" not in consistency
    equal = ("Inside the zone t_ia (report-only pass, ruling R-4j-7): the row equals the zone row in every compared column.")
    assert equal in _by_id(result)["lot_t_zone_a"]["notes"]
    printed = capsys.readouterr().out
    assert "[garages] lots inside a zone polygon (report-only pass, ruling R-4j-7): 4 of 12 lots, equal to the zone row 3, " \
           "differing 1 (lot_t_rate)" in printed


def test_a_lot_inside_a_zone_polygon_is_written_to_its_qa_row_with_the_zone_ids(step, package_file, regional_tests, tmp_path,
                                                                                monkeypatch):
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", regional_tests._specs())
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", regional_tests.MONTHLY)
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", regional_tests.CANDIDATES)
    monkeypatch.setattr(step.specs, "BS_MONTHLY_SPECS", ())
    monkeypatch.setattr(step.specs, "BS_RECORDED_SPECS", ())
    monkeypatch.setattr(step.specs, "DIRECTORY_DECISIONS", regional_tests.DIRECTORY)
    monkeypatch.setattr(step.specs, "DIRECTORY_LOT_KEYS", {})
    regional_tests._write_package(tmp_path / "regional", regional_tests._layers(), regional_tests._rules(),
                                  regional_tests._facilities())
    regional_sha = hashlib.sha256((tmp_path / "regional" / f"{regional_tests.PACKAGE}.zip").read_bytes()).hexdigest()
    path, large_sha = package_file
    zones, tariffs = _street_zone_tables()
    inputs = step.load_garage_inputs(tmp_path / "regional", expected_sha256=regional_sha, large_lots_path=path,
                                     expected_large_lots_sha256=large_sha, zones=zones, tariffs=tariffs)
    frame = step.build_garages(inputs)
    rows = step.qa_rows(inputs, frame, step.load_directory(regional_tests._directory(tmp_path, step, monkeypatch)))
    table = pd.DataFrame(rows).set_index("record_id")
    assert table.loc["garage_lot_t_rate", "zone_ids"] == "t_street" and table.loc["garage_lot_t_zone_a", "zone_ids"] == "t_ia"
    assert table.loc["garage_lot_t_free", "zone_ids"] == "" and table.loc["garage_lot_t_bands", "zone_ids"] == ""
    assert "billing_unit_min 60 against 30" in table.loc["garage_lot_t_rate", "note"]
    # the garages of the regional package are no lots: no zone ids, whatever zone they lie in
    assert (table[table["subject"].str.startswith("Parkhaus")]["zone_ids"] == "").all()
    # the validator reads the QA table against the dataset and the zone table: the difference is reported, never an error
    qa = pd.DataFrame(rows)
    checks = pq.zone_consistency_check(qa, frame, tariffs)
    assert {(check["garage_id"], check["zone_id"]): check["differences"] for check in checks}[("lot_t_rate", "t_street")] == [
        "billing_unit_min 60 against 30"]


# --------------------------------------------------------------------------- sources, origins and counts (fix round 1)


@pytest.mark.parametrize("url, expected", [
    ("https://geoviewer.stadt.wolfsburg.de/default/ows/projects/gpt/parken", True),
    ("https://maps.example/arcgis/rest/services/parking/MapServer", True),
    ("https://maps.example/geoserver/wfs?service=WFS&request=GetCapabilities", True),
    ("https://www.wolfsburg-erleben.de/poi/parkplatz-allerpark-wolfsburg", False),
    ("https://www.wolfsburg.de/-/media/satzungen/parkgebuehrenordnung_20200721.pdf", False),
    ("", False),
    (None, False),
])
def test_a_web_service_layer_is_told_from_a_page_that_states_a_fee(ll, url, expected):
    assert ll.is_web_service_url(url) is expected


def test_the_source_of_a_row_is_the_first_page_with_a_url_and_a_date_that_is_no_web_service_layer(ll):
    """Finding L2: the Geoviewer layer comes first in the source list of the Allerpark but carries no fee statement."""
    layer = "https://geo.example/default/ows/projects/gpt/parken"
    package = {"sources": {"ROOT_LAYER": {"id": "ROOT_LAYER", "url": layer, "date": "2026-10-07"},
                           "EV_PAGE": {"id": "EV_PAGE", "url": "https://page.example/lot", "date": "2026-10-08"},
                           "EV_NODATE": {"id": "EV_NODATE", "url": "https://other.example/x", "date": None},
                           "EV_LATER": {"id": "EV_LATER", "url": "https://later.example/y", "date": "2026-10-09"}}}
    assert ll.source_of(package, ["ROOT_LAYER", "EV_NODATE", "EV_PAGE", "EV_LATER"]) == ("https://page.example/lot", "2026-10-08")
    # a layer is the source only where no other source states a URL and a date (the caller counts and logs it)
    assert ll.source_of(package, ["ROOT_LAYER", "EV_NODATE"]) == (layer, "2026-10-07")
    with pytest.raises(SystemExit, match="none of the sources"):
        ll.source_of(package, ["EV_NODATE", "UNKNOWN"])


def test_a_tariff_source_that_is_only_a_web_service_layer_is_counted_and_warned(step, package_file, capsys):
    inputs = _inputs(step, package_file)
    inputs["large"]["package"]["sources"]["src_prices"]["url"] = "https://geo.example/default/ows/projects/gpt/parken"
    result = step.build_large_lots(inputs)
    assert result["counts"]["tariff_source_web_service"] == 12
    printed = capsys.readouterr().out
    assert "source of the tariff: a page that states it 0/12, only a web-service layer (fallback) 12/12" in printed
    assert "[garages] WARNING: the tariff source of the large lot(s) " in printed and "is a web-service layer" in printed


def test_the_position_source_is_the_osm_object_then_the_coordinate_sources_then_the_counted_tariff_source(
        step, package_file, capsys):
    """Finding L3: the sources that the package names for a position are read before the tariff source stands in."""
    inputs = _inputs(step, package_file)
    facilities = inputs["large"]["package"]["facilities"]
    facilities["t_zone_a"]["osm_ids"] = ["way/25102260"]                        # primary: the OpenStreetMap object
    facilities["t_zone_b"].update(coordinate_source_ids=None, coordinates={      # primary: the sources of the coordinates
        "longitude": 10.0, "latitude": 52.0, "source_ids": ["src_prices"], "method": "published_primary_jsonld_geo"})
    facilities["t_rate"].update(coordinate_source_ids=None)                      # fallback: the package names no source
    result = step.build_large_lots(inputs)
    rows = _by_id(result)
    assert rows["lot_t_zone_a"]["geometry_source_url"] == "https://www.openstreetmap.org/way/25102260"
    assert rows["lot_t_zone_b"]["geometry_source_url"] == SOURCE_URL and rows["lot_t_zone_b"]["source_url"] == SOURCE_URL
    assert rows["lot_t_rate"]["geometry_source_url"] == rows["lot_t_rate"]["source_url"] == SOURCE_URL
    assert result["counts"]["position_origin"] == {"osm_object": 1, "coordinate_source": 10, "tariff_source_fallback": 1}
    # a point of which the package names no source at all (and no prior municipal point) is no research point in a parking area
    assert rows["lot_t_rate"]["geometry_method"] == "package_point_without_source"
    assert "it is not a research point inside the parking area" in rows["lot_t_rate"]["notes"]
    assert rows["lot_t_zone_a"]["geometry_method"] == "package_research_point_in_parking_area"
    assert {row["geometry_method"] for row in rows.values()} <= set(step.LARGE_LOT_GEOMETRY_METHODS)
    printed = capsys.readouterr().out
    assert ("source of the position: OpenStreetMap object 1/12, coordinate source of the package 10/12, tariff-source "
            "fallback 1/12") in printed
    assert "[garages] WARNING: the position of the large lot(s) t_rate has no source in the package" in printed


def test_a_lot_with_a_prior_municipal_point_and_no_position_source_has_its_own_geometry_method(step, package_file):
    inputs = _inputs(step, package_file)
    inputs["large"]["package"]["facilities"]["t_rate"].update(coordinate_source_ids=None,
                                                              prior_municipal_point_ids=["WOB_PARK_PRIOR"])
    row = _by_id(step.build_large_lots(inputs))["lot_t_rate"]
    assert row["geometry_method"] == step.LARGE_LOT_PRIOR_POINT_METHOD == "package_prior_municipal_point"
    assert "the prior municipal point WOB_PARK_PRIOR of the package, which it keeps for review as no verified entrance" in row[
        "notes"]
    assert row["geometry_source_url"] == row["source_url"]       # the counted stand-in of the tariff source
    assert step.LARGE_LOT_GEOMETRY_METHODS == ("package_research_point_in_parking_area", "package_prior_municipal_point",
                                               "package_point_without_source")
    assert "package_prior_municipal_point" in step.COLUMN_GLOSSARY["geometry_method"]


@pytest.mark.parametrize("method, origin", [
    ("reported", "reported"),
    ("osm_integer_capacity", "osm_tag"),
    ("estimated_floor_area_m2_divided_by_25", "area_estimate"),
    ("sum_nonoverlapping_component_area_estimates", "area_estimate"),
    ("sum_nonoverlapping_osm_capacities_and_area_estimates", "area_estimate"),
    ("derived_public_capacity_334_minus_130", "derived"),
    ("unknown", "other"),
    (None, "other"),
])
def test_the_capacity_origin_is_told_by_the_selection_method_of_the_package(ll, method, origin):
    assert ll.capacity_origin({"capacity_selection_method": method}) == origin


def test_an_unknown_capacity_origin_is_counted_apart_and_warned(step, package_file, capsys):
    inputs = _inputs(step, package_file)
    inputs["large"]["package"]["facilities"]["t_zero"]["capacity_selection_method"] = "typed_by_hand"
    result = step.build_large_lots(inputs)
    assert result["counts"]["capacity_origin"]["other"] == 1 and result["counts"]["capacity_origin"]["reported"] == 8
    assert "[garages] WARNING: the capacity origin of the large lot(s) t_zero is none of the known ones" in capsys.readouterr().out


def test_the_counts_read_the_fields_of_the_rows_and_never_the_text_of_their_notes(step, package_file, capsys):
    inputs = _inputs(step, package_file)
    result = step.build_large_lots(inputs)
    package, rows = inputs["large"]["package"], result["rows"]
    imputed = {row["garage_id"]: row for row in rows if "P14" in str(row["assumptions"]).split(";")}
    before = (result["counts"]["zone_products"], result["counts"]["limited"])
    assert before[0] == 2 and before[1] > 0
    for row in rows:        # a sentence of the notes that is no fact of the row changes no count
        row["notes"] += " The street product of the zone t_ia is not the tariff of this row. Not modelled: nothing."
    again = step._large_lot_counts(package, rows, result["qa"], [], imputed, 50)
    assert (again["zone_products"], again["limited"]) == before
    # and a row that is a zone product by its fact is counted whatever its notes say
    for row in rows:
        row["notes"] = "no sentence of the notes"
    assert step._large_lot_counts(package, rows, result["qa"], [], imputed, 50)["zone_products"] == 2
    capsys.readouterr()


@pytest.mark.parametrize("imputed, warns", [(2, False), (3, True)])
def test_the_imputation_warning_share_is_pinned_at_a_quarter_of_the_options(step, package_file, capsys, imputed, warns):
    """Two of eight options imputed is exactly 25 % and gives no warning (the rule is: more than); three of eight is above it. The
    pair fails for a share below 0.25 (2 of 8 would warn) and for one of 0.375 or more (3 of 8 would not)."""
    assert step.LARGE_LOT_IMPUTATION_WARNING_SHARE == 0.25
    inputs = _inputs(step, package_file)
    result = step.build_large_lots(inputs)
    capsys.readouterr()
    step._large_lot_counts(inputs["large"]["package"], result["rows"][:8], [], [], {f"imputed_{n}": None for n in range(imputed)},
                           50)
    printed = capsys.readouterr().out
    assert ("WARNING: more than 25 % of the large lots rest on ASSUMPTION P14" in printed) is warns


def test_a_zone_with_one_priced_lot_gives_the_p14_lot_the_median_of_its_municipality(step, package_file, tmp_path, monkeypatch,
                                                                                    capsys):
    """Finding L5: the zone t_ia holds one priced lot only (a median needs two), so the lot without a stated tariff takes the
    median of the three priced lots of the municipality, two of them outside the zone with products of their own."""
    catalogue = _catalogue()
    del catalogue["t_zone_b"]
    for key, rate, offset in (("t_out_a", 2.0, (900, 100)), ("t_out_b", 3.0, (950, 100))):
        catalogue[key] = (_facility(key, "Braunschweig", capacity_selected_for_threshold=80, capacity_reported=80), offset,
                          [_rule(f"{key}__1", key, rate, billing_period="started_hour", billing_unit_minutes=60)])
    specs = {key: value for key, value in SPECS.items() if key != "t_zone_b"}
    specs.update(t_out_a={"rate": "t_out_a__1", "window": None}, t_out_b={"rate": "t_out_b__1", "window": None})
    monkeypatch.setattr(step.specs, "LARGE_LOT_SPECS", specs)
    result = step.build_large_lots(_inputs(step, _write_package(tmp_path / "one_donor", catalogue)))
    imputed = _by_id(result)["lot_t_p14_zone"]
    assert "level municipality" in imputed["notes"] and "level zone" not in imputed["notes"]
    assert f"the median product of the 3 priced lots of the municipality {BS}" in imputed["notes"]
    assert (imputed["garage_hourly_rate_eur"], imputed["garage_billing_unit_min"]) == (2.0, 60)      # the middle one, t_out_a
    assert "t_out_a__1" in imputed["tariff_rule_ids"] and "t_zone_a__1" not in imputed["tariff_rule_ids"]
    assert result["notes"]["lot_t_p14_zone"].startswith("imputed (ASSUMPTION P14, level municipality) from the median lot t_out_a")
    assert "[garages] P14 t_p14_zone: median product of 3 priced lots of the municipality" in capsys.readouterr().out


def test_the_qa_intro_names_the_large_lot_package_the_threshold_the_evidence_and_the_reasons(step):
    """Finding M3: the intro of the committed QA table is a statement of provenance, so it names the inputs of the run that wrote
    it: the large-lot package with its SHA-256, the size threshold, the evidence folder, the reason codes and the convention of
    the zone evidence; the superseded Wolfsburg car-park package is no input of it."""
    intro = step.QA_INTRO
    assert step.ll.LOTS_FILE in intro and step.ll.LOTS_SHA256 in intro
    assert "ASSUMPTION L50" in intro and "wob_free_lots_evidence" in intro
    assert "parking_tariffs_2026.csv:<zone_id>" in intro
    lot_reasons = [reason for reason in pq.CANDIDATE_REASONS if reason.startswith("lot_")]
    assert len(lot_reasons) == 12 and all(reason in intro for reason in lot_reasons)
    assert step.SUPERSEDED_WOLFSBURG_LOTS_FILE not in intro and step.SUPERSEDED_WOLFSBURG_LOTS_SHA256 not in intro


# --------------------------------------------------------------------------- the released rows after fix round 1 (R-4j-6 to R-4j-8)


def _zone_product_lots(committed) -> dict:
    """{garage_id: zone_id} of the released lots whose evidence names a row of the tariff table."""
    return {garage_id: pq.ZONE_PRODUCT_EVIDENCE.search(str(row["tariff_rule_ids"])).group(1)
            for garage_id, row in committed.iterrows() if pq.ZONE_PRODUCT_EVIDENCE.search(str(row["tariff_rule_ids"]))}


def test_every_released_zone_product_lot_prices_every_stay_like_the_street_of_its_zone(committed):
    """Ruling R-4j-6 c on the released rows: the 12 lots that carry a zone's street product (ten of the research package and the two
    single-site zones of Goslar) against the street path of their zone, stays of 1 to 720 min at 06:00, 09:00, 14:00 and 19:00 for
    the purposes work and shopping, equal to the cent (69,120 pairs)."""
    tariffs = pz.load_tariffs(REAL_TARIFFS).set_index("zone_id", drop=False)
    lots = _zone_product_lots(committed)
    assert len(lots) == 12 and {"lot_gs_baeringer", "lot_gs_klubgarten", "lot_braunschweig_buergerpark"} <= set(lots)
    checked = 0
    for garage_id, zone_id in lots.items():
        zone = _zone_tariff(tariffs.loc[zone_id].to_dict())
        assert sweep_mismatches(zone, te.garage_row_to_tariff(committed.loc[garage_id])) == [], garage_id
        checked += len(SWEEP_ARRIVALS_H) * 720 * len(SWEEP_PURPOSES)
    assert checked == 69_120
    # spec v3 B1 dropped the 9.00 EUR ticket of zone Ia, so the street has no product above its 180 min and the plain rate
    # prices every
    # stay like the street (a stay the street cannot serve is priced as the metered street, the cheapest infeasible option); what
    # separates the lot from the street is the FEASIBILITY, tested in the next test (one direction only, ruling R-B-11)


def test_a_zone_ia_lot_is_never_less_restrictive_than_the_street_but_can_be_stricter(committed):
    # ruling R-B-2 gives the lot the zone's maximum as its own (max_stay_min 180) and ruling R-B-11 keeps the simple ELAPSED
    # rule: the
    # lot compares the elapsed stay, the street the CHARGEABLE minutes inside the fee window. So the implication holds in ONE
    # direction only: wherever the street cannot serve a stay (its chargeable minutes exceed 180) the lot cannot either, because the
    # elapsed stay is never shorter than the chargeable one; the converse is false (stricter lot), shown by the example below
    tariffs = pz.load_tariffs(REAL_TARIFFS).set_index("zone_id", drop=False)
    zone = _zone_tariff(tariffs.loc["bs_zone_ia"].to_dict())
    assert zone.max_stay_min == 180 and zone.long_stay_product_cents is None
    stricter = 0
    for garage_id in ("lot_braunschweig_wendenstrasse_werder", "lot_braunschweig_grosser_hof", "lot_braunschweig_buergerpark"):
        garage = te.garage_row_to_tariff(committed.loc[garage_id])
        assert garage.max_stay_min == 180 and garage.bands is None
        for arrival_h in SWEEP_ARRIVALS_H:
            arrival_s = arrival_h * HOUR_SECONDS
            for minutes in range(1, 721):
                chargeable_s = cost.chargeable_seconds(arrival_s, arrival_s + minutes * 60, zone.fee_start_s, zone.fee_end_s)
                if not cost.street_feasible(zone, chargeable_s):
                    assert not cost.option_feasible(garage, minutes * 60), (garage_id, arrival_h, minutes)
                elif not cost.option_feasible(garage, minutes * 60):
                    stricter += 1
        assert cost.option_feasible(garage, 180 * 60) and not cost.option_feasible(garage, 180 * 60 + 1)
        # the asymmetry: 07:00 to 11:00 has 120 chargeable minutes in the window 09:00-20:00 (street feasible) and 240 elapsed
        # minutes
        arrival_s, departure_s = 7 * HOUR_SECONDS, 11 * HOUR_SECONDS
        chargeable_s = cost.chargeable_seconds(arrival_s, departure_s, zone.fee_start_s, zone.fee_end_s)
        assert chargeable_s == 2 * HOUR_SECONDS and cost.street_feasible(zone, chargeable_s)
        assert not cost.option_feasible(garage, departure_s - arrival_s)
    assert stricter > 0
    # the control of the sweep: a plain rate WITHOUT the maximum would offer the lot for stays the street cannot serve
    row = committed.loc["lot_braunschweig_grosser_hof"].copy()
    row["max_stay_min"] = pd.NA
    assert cost.option_feasible(te.garage_row_to_tariff(row), 361 * 60)


#: The lots inside a zone polygon that differ from the table row of that zone, with the named differences (ruling R-4j-7 b): the
#: package component stands as documented and the difference is named in the QA note, never changed.
#: Since spec v3 B1 (ruling R-B-3): the Wolfenbuettel Lessingtheater carries the 2026 street rate (0.25 EUR, then 0.05 EUR per 3
#: min), so
#: only its fee window differs; the Helmstedt lots (zone maximum 180 min) and the Salzgitter lots (zone maximum 150 min) do not
#: carry
#: the zone's maximum stay in their own components, which the pass names.
EXPECTED_ZONE_DIFFERENCES = {
    "lot_wf_lessingtheater": ("wf_innenstadt", ["fee_start_h 0 against 10", "fee_end_h 24 against 18"]),
    "lot_helmstedt_harsleber_tor": ("he_innenstadt", ["free_if_stay_at_most_min 30 against 0", "max_stay_min none against 180"]),
    "lot_helmstedt_holzberg": ("he_innenstadt", ["free_if_stay_at_most_min 30 against 0", "max_stay_min none against 180"]),
    "lot_helmstedt_suedertor": ("he_innenstadt", ["free_if_stay_at_most_min 30 against 0", "daily_cap_eur 3 against none",
                                                  "max_stay_min none against 180"]),
    "lot_sz_buechereivorplatz": ("sz_lebenstedt", ["max_stay_min none against 150"]),
    "lot_sz_konrad_adenauer_16": ("sz_lebenstedt", ["max_stay_min none against 150"]),
    "lot_sz_rathausvorplatz": ("sz_lebenstedt", ["max_stay_min none against 150"]),
    "lot_gs_marktstrasse": ("gs_altstadt_zone1", ["billing_unit_min 60 against 30"]),
    "lot_gs_kornstrasse": ("gs_altstadt_zone1", ["billing_unit_min 60 against 30"]),
}


def test_the_pass_over_every_released_lot_inside_a_zone_polygon_finds_13_equal_and_9_named_differences(committed, committed_qa):
    tariffs = pz.load_tariffs(REAL_TARIFFS)
    garages, qa = committed.reset_index(drop=True), committed_qa.reset_index(drop=True)
    checks = pq.zone_consistency_check(qa, garages, tariffs)
    assert len(checks) == 22 and all(check["zone_in_table"] for check in checks)
    differing = {check["garage_id"]: (check["zone_id"], check["differences"]) for check in checks if check["differences"]}
    assert differing == EXPECTED_ZONE_DIFFERENCES
    # the 12 zone-product lots are equal by construction; Iseparkplatz is the only other lot equal to the row of its zone (the three
    # Salzgitter lots differ since the zone row has its maximum stay, ruling R-B-3)
    assert sum(check["zone_product"] and not check["differences"] for check in checks) == 12
    assert {check["garage_id"] for check in checks if not check["zone_product"] and not check["differences"]} == {
        "lot_gf_iseparkplatz"}
    summary = pq.zone_consistency_summary(qa, garages, tariffs)
    assert (summary["checked"], summary["equal"]) == (22, 13) and set(summary["differing"]) == set(EXPECTED_ZONE_DIFFERENCES)
    # every difference is named in the QA note of its lot, and the values of the row are the package's, never the zone's
    for garage_id, (zone_id, differences) in EXPECTED_ZONE_DIFFERENCES.items():
        note = committed_qa.loc[f"garage_{garage_id}", "note"]
        assert f"Inside the zone {zone_id} (report-only pass, ruling R-4j-7): the row DIFFERS from the zone row: " + "; ".join(
            differences) in note, garage_id
        assert committed_qa.loc[f"garage_{garage_id}", "zone_ids"] == zone_id
    assert "the row equals the zone row in every compared column" in committed_qa.loc["garage_lot_gf_iseparkplatz", "note"]
    # the garages of the regional packages are no lots: no zone ids, whatever zone they lie in
    garage_rows = qa[qa["record_type"] == "garage"]
    kinds = garages.set_index("garage_id")["facility_kind"].reindex(garage_rows["garage_id"]).to_numpy()
    assert len(garage_rows) == 94 and (garage_rows.loc[kinds == "garage", "zone_ids"] == "").all()


def test_the_released_rows_cite_a_page_for_the_tariff_and_the_position_by_the_primary_method(ll, committed):
    lots = committed[committed["facility_kind"] == "surface_lot"]
    assert not any(ll.is_web_service_url(url) for url in lots["source_url"])
    # finding L2: the Allerpark cites the page of the Wolfsburg Marketing GmbH that states it free, not the Geoviewer layer
    wmg = "https://www.wolfsburg-erleben.de/poi/"
    assert committed.loc["lot_wob_park_1835028", "source_url"] == wmg + "parkplatz-allerpark-wolfsburg"
    assert committed.loc["lot_wob_park_1114127", "source_url"] == wmg + "parkplatz-badeland-wolfsburg"
    for garage_id in ("lot_wob_park_1638410", "lot_wob_park_1769502", "lot_wob_park_1835014"):
        assert committed.loc[garage_id, "source_url"].endswith("/parkgebuehrenordnung_20200721.pdf"), garage_id
    # finding L3: a position source is the OpenStreetMap object of the package (53 lots) or a source that the package names for the
    # coordinates (four lots; Arena P1 has neither, its tariff source stands in, counted and warned); the count of that fallback
    # is pinned on the real package
    # (counts["position_origin"] of the primary-method test), because the same page can be both sources of a lot
    assert int(lots["geometry_source_url"].str.startswith("https://www.openstreetmap.org/").sum()) == 53
    assert lots["geometry_source_url"].str.startswith("https://").all()


def test_the_two_lots_at_exactly_the_threshold_are_named_with_their_capacity_origin(ll, real_package):
    """Finding L4: the options at exactly 50 spaces are the cases that the sensitivities at 30 and 100 do not settle; the data
    record names them: the Iseparkplatz (an area estimate) and the Rathausvorplatz (an OSM tag of 50 against a historic survey
    of 47)."""
    edge = {key: (record["capacity_selected_for_threshold"], ll.capacity_origin(record))
            for key, record in real_package["facilities"].items()
            if ll.decide(record, 50) is None and record["capacity_selected_for_threshold"] == 50}
    assert edge == {"gf_iseparkplatz": (50, "area_estimate"), "sz_rathausvorplatz": (50, "osm_tag")}
    assert "Historic municipal survey capacity 47" in real_package["facilities"]["sz_rathausvorplatz"]["notes"]


def test_the_check_in_lot_names_the_derived_capacity_of_its_mixed_access(committed, real_package):
    """Finding I3 (ruling R-4j-8): the numbers of the note are the package's, not typed: 334 in total, 130 for hotel guests."""
    record = real_package["facilities"]["wf_bahnhof_checkin"]
    assert record["access_class"] == "mixed_public_and_customers"
    assert (record["capacity_reported"], record["capacity_selected_for_threshold"]) == (334, 204)
    assert "130 Pl" in record["access_notes"] and record["capacity_selection_method"] == "derived_public_capacity_334_minus_130"
    row = committed.loc["lot_wf_bahnhof_checkin"]
    assert "derived capacity 204 of 334 (130 hotel guests)" in row["notes"] and pd.isna(row["capacity_reported"])


def test_the_committed_qa_header_names_the_inputs_of_the_run_that_wrote_it(ll):
    header = " ".join(line[1:].strip() for line in COMMITTED_QA.read_text(encoding="utf-8").splitlines() if line.startswith("#"))
    assert f"{ll.LOTS_FILE} (SHA-256 {ll.LOTS_SHA256}" in header and "ASSUMPTION L50" in header
    assert "wob_free_lots_evidence" in header and "parking_tariffs_2026.csv:<zone_id>" in header
    assert "Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip" not in header
    assert all(reason in header for reason in pq.CANDIDATE_REASONS if reason.startswith("lot_"))


def test_a_component_maximum_stay_equal_to_the_maximum_stay_of_the_row_is_not_listed_as_not_modelled(garages_step):
    # spec v3 B2: Cinemaxx lies in the Wolfsburg area zone wob_area_680 (maximum 60 min, no long-stay product), so the row carries
    # that maximum as max_stay_min; the 60 min of its own component are the same restriction, modelled, and the note must not
    # call them "not modelled" while another stay of a component (120 min here) still is
    package = {"rules": {"R1": {"max_stay_minutes": 60, "billing_period": "hour"},
                         "R2": {"max_stay_minutes": 120, "billing_period": "hour"}}}
    sentences, restricted = garages_step._lot_limitations(package, ["R1", "R2"], {}, row_max_stay_min=60)
    not_modelled = next(sentence for sentence in sentences if sentence.startswith("Not modelled"))
    assert restricted and "maximum stay 120 min (R2)" in not_modelled and "maximum stay 60 min" not in not_modelled
    assert any("maximum stay 60 min (R1)" in sentence and "max_stay_min of this row" in sentence
               and "modelled" in sentence and not sentence.startswith("Not modelled") for sentence in sentences)
    # without a maximum stay on the row, or with another one, the component stays "not modelled", as before
    for row_max_stay_min in (None, 90):
        sentences, restricted = garages_step._lot_limitations(package, ["R1", "R2"], {}, row_max_stay_min=row_max_stay_min)
        assert restricted and len(sentences) == 1
        assert "maximum stay 60 min (R1); maximum stay 120 min (R2)" in sentences[0]
    only, restricted = garages_step._lot_limitations(package, ["R1"], {}, row_max_stay_min=60)
    assert restricted and len(only) == 1 and not only[0].startswith("Not modelled")


def test_the_two_lots_in_a_wolfsburg_area_zone_state_the_measured_overlap_with_the_street_section(committed, garages_step):
    # spec v3 B2: the max_stay_min of Cinemaxx (60 min) and of the Hoffmannstrasse lot (120 min, in the 30-space set) rests on the
    # lot lying in the street section of an area with that maximum; the overlap numbers are in the notes (measured on the Wolfsburg
    # layer and the outlines of the lot package) and the lot's own component states the same maximum
    cinemaxx = committed.loc["lot_wob_park_1769502", "notes"]
    for phrase in ("wob_area_680", "979 m2", "100 % inside the OSM outline of the lot (way 115027361, 2,416 m2",
                   "40.5 % of the outline", "research point lies inside the section", "Hoechstparkdauer 60 Minuten"):
        assert phrase in cinemaxx, phrase
    specs = garages_step.specs.LARGE_LOT_SPECS
    hoffmann = specs["WOB_PARK_1245204"]
    assert hoffmann["zone_product"] == "wob_area_736"
    for phrase in ("89.1 %", "way 25672155, 680 m2", "1,317 m2", "WOB_PARK_1245204__1 states a maximum stay of 120 min"):
        assert phrase in hoffmann["comment"], phrase
