"""Garage dataset of the regional evidence package of 2026-10-07 (parking cost zones v2, spec Amendment E1, issue #436).

The owner's package is gitignored, so the curation step ``regional_garages.py`` is pinned on a synthetic package written the
way the owner supplied it (a zip holding ``<name>/daten/Regional_Parkdaten.gpkg`` in EPSG:25832, ``facilities.json``,
``tariff_rules.json`` and ``sources.json``) with small garage specifications of its own: the roles of the rules (a first
period with its clock window, a rate, a day cap, the fee window, the time-of-day tiers or the duration bands), the rule that
is not preferred and therefore never sets a value (ruling R-4b-8), the assumptions P3 to P8 and their counts, the two
readings, a garage that cannot be priced, the twin of
a garage in another layer, the monthly products and the candidates that are no garage (a station BahnPark, a lot of long-term
renters). The committed dataset is pinned independently at the end (``PRICED_GARAGES``: the values as read from the sources,
not from the code) and reproduced from the local package where it exists.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from typing import NamedTuple

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.parking import garage_qa as pq
from braunschweig.parking import garages as pg
from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
COMMITTED_PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
METRIC_CRS = "EPSG:25832"
PACKAGE = "Regional_Parkdaten_Belege_2026-10-07"
X0, Y0 = 604_000.0, 5_790_000.0
BS, WOB, GS = "03101000", "03103000", "03153017"
PULP = "https://www.braunschweig.de/apps/pulp/result/parkhaeuser.geojson"
GEOVIEWER = "https://geoviewer.stadt.wolfsburg.de/default/ows/projects/gpt/parken"
ARCGIS = "https://services3.arcgis.com/X0EQlGp2g40JN62M/arcgis/rest/services/Bewohnerparken/FeatureServer/2"


@pytest.fixture(scope="module")
def garages_step():
    """The curation step as a module (it imports its siblings from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("regional_garages_under_test", CURATION_DIR / "regional_garages.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


# --------------------------------------------------------------------------- the synthetic package


def _rule(rule_id, rule_type, facility, amount=None, *, unit=None, rounding=None, start=None, end=None, cap=None,
          period=None, times=None, window=None, monthly=None, contract=None, conditions=None, status="current_primary_source",
          source="https://op.example/tariff", retrieved="2026-10-07", product=None, preferred=True) -> dict:
    return {"rule_id": rule_id, "facility_id": facility, "rule_type": rule_type, "amount_eur": amount,
            "billing_unit_minutes": unit, "rounding": rounding, "elapsed_from_minutes": start, "elapsed_to_minutes": end,
            "max_stay_minutes": None, "daily_cap_eur": cap, "cap_period": period,
            "charging_times": times or {day: None for day in ("monday", "tuesday", "wednesday", "thursday", "friday",
                                                               "saturday", "sunday", "public_holidays")},
            "monthly_price_eur": monthly, "minimum_contract_months": contract, "conditions": conditions,
            "source_url": source, "retrieved_at": retrieved, "status": status, "preferred_for_current_use": preferred,
            "time_window": window or {"from": None, "to": None, "days_raw": None},
            "raw_rule": {"product_name": product} if product else {}}


def _times(start, end, days=("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
           crosses=False) -> dict:
    times = {day: None for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                                   "public_holidays")}
    for day in days:
        times[day] = [{"start": start, "end": end, "crosses_midnight": crosses}]
    return times


DAY = _times("07:00", "18:00")
NIGHT = _times("18:00", "07:00", crosses=True)


def _rules() -> list:
    return [
        # BS_A: a day family with a first period and a day cap, a night tier, two monthly products
        _rule("a-1", "increment", "BS_A", 0.60, unit=60, rounding="started_unit", start=0, end=60, times=DAY),
        _rule("a-2", "increment", "BS_A", 1.20, unit=60, rounding="started_unit", start=60, times=DAY),
        _rule("a-3", "daily_cap", "BS_A", 9.60, cap=9.60, period="daytime_07_18", times=DAY),
        _rule("a-4", "increment", "BS_A", 1.00, unit=60, rounding="started_unit", times=NIGHT),
        _rule("a-5", "night_cap", "BS_A", 6.00, period="night_18_07", times=NIGHT),
        _rule("a-m1", "monthly_product", "BS_A", 50.0, monthly=50.0, conditions="waitlist", product="Dauerparker Tag"),
        _rule("a-m2", "monthly_product", "BS_A", 60.0, monthly=60.0, product="Dauerparker Tag/Nacht"),
        # BS_B: the same price for the first and every further hour, no rounding stated, no charging times
        _rule("b-1", "increment", "BS_B", 1.50, unit=60, start=0, end=60),
        _rule("b-2", "increment", "BS_B", 1.50, unit=60, start=60),
        _rule("b-3", "daily_rate_published", "BS_B", 12.0, cap=12.0, period="unspecified_day"),
        _rule("b-4", "lost_ticket_fee", "BS_B", 10.0),
        _rule("b-5", "increment", "BS_B", 9.99, unit=60, preferred=False),
        # WOB_X: a rate without charging times of a preferred rule; the only rule that states a window is not preferred
        _rule("x-1", "increment", "WOB_X", 2.00, unit=60, rounding="started_unit"),
        _rule("x-2", "published_day_tariff", "WOB_X", 9.0, period="day_definition_unspecified"),
        _rule("x-3", "unresolved_night_tariff", "WOB_X", 1.0, window={"from": "21:00", "to": "06:30", "days_raw": None},
              preferred=False),
        # GS_A: a rate that changes after 3 h (two increment bands) and a published 24-hour price (the day cap)
        _rule("g-1", "increment", "GS_A", 1.50, unit=60, start=0, end=180, period="elapsed_first_3_hours"),
        _rule("g-2", "increment", "GS_A", 0.80, unit=30, start=180, rounding="unspecified"),
        _rule("g-3", "published_duration_tariff", "GS_A", 25.0, end=1440, period="24_hours"),
        # GS_B: one rate, all day, in a window that is stated as a time window with the days of the model's weekday
        _rule("h-1", "increment", "GS_B", 1.00, unit=60, rounding="started_unit",
              window={"from": "08:00", "to": "00:00", "days_raw": "Mo-Sa_except_public_holidays"}),
        _rule("h-2", "cap", "GS_B", 8.0, cap=8.0),
        # BS_T: tiers of 30 min: a morning tier without stated days, a day tier, an evening tier across midnight (on another
        # page), a day cap, a night cap that the day-cap column cannot hold and a first period for the mutation tests
        _rule("t-1", "increment", "BS_T", 0.30, unit=30, rounding="started_unit",
              window={"from": "08:00", "to": "10:00", "days_raw": None}),
        _rule("t-2", "increment", "BS_T", 0.60, unit=30, rounding="started_unit", times=_times("10:00", "18:00")),
        _rule("t-3", "increment", "BS_T", 0.20, unit=30, rounding="started_unit",
              times=_times("18:00", "02:00", crosses=True), source="https://op.example/evening"),
        _rule("t-4", "daily_cap", "BS_T", 5.00, cap=5.00, period="calendar_day"),
        _rule("t-5", "duration_total", "BS_T", 0.50, start=0, end=30),
        _rule("t-6", "night_cap", "BS_T", 2.00, period="night_18_02"),
        # WOB_STATION: a station car park (a candidate, no garage of the dataset) with a monthly product
        _rule("s-1", "increment", "WOB_STATION", 1.00, unit=60),
        _rule("s-m1", "monthly_product", "WOB_STATION", 100.0, monthly=100.0, product="Dauerparken"),
    ]


def _facilities() -> list:
    def record(facility_id, name, rules, capacity=None, scope=None, observations=None, attributes=None, refs=()):
        return {"facility_id": facility_id, "name": name, "tariff_rule_ids": rules, "capacity": capacity,
                "capacity_scope": scope, "capacity_observations": observations or [], "attributes": attributes or {},
                "geometry_refs": list(refs)}

    return [
        record("BS_A", "Parkhaus A", ["a-1", "a-2", "a-3", "a-4", "a-5", "a-m1", "a-m2"], 500, None,
               [{"value": 500, "scope": "reported_in_PULP", "source_url": PULP},
                {"value": 677, "scope": "operator_total", "source_url": "https://op.example/a"}], refs=["bs_parkhaeuser:1"]),
        record("BS_B", "Parkhaus B", ["b-1", "b-2", "b-3", "b-4", "b-5"], refs=["bs_parkhaeuser:2"]),
        record("BS_T", "Parkhaus T", ["t-1", "t-2", "t-3", "t-4", "t-5", "t-6"], refs=["bs_parkhaeuser:3"]),
        record("WOB_X", "Parkhaus X", ["x-1", "x-2", "x-3"], 956, "total", attributes={"operator": "Saba"},
               refs=["wob_parkhaeuser:1", "region_parkhaeuser:1"]),
        record("WOB_STATION", "Parkdeck Bahnhof", ["s-1", "s-m1"], refs=["wob_parkhaeuser:2"]),
        record("GS_A", "Parkhaus Ga", ["g-1", "g-2", "g-3"], refs=["region_parkhaeuser:2"]),
        record("GS_B", "Parkhaus Gb", ["h-1", "h-2"], 250, "secondary_directory_total",
               refs=["gos_parking_locations:1", "region_parkhaeuser:3"]),
        record("GOS_POINT_4", "Parkhaus Charley", [], refs=["gos_parking_locations:2"]),
        record("BS_ADDITIONAL_1", "Parkhaus Lange Strasse Sued", ["b-1"]),
    ]


def _point_layer(rows: list) -> gpd.GeoDataFrame:
    frame = pd.DataFrame([{key: value for key, value in row.items() if key != "xy"} for row in rows])
    return gpd.GeoDataFrame(frame, geometry=[Point(X0 + row["xy"][0], Y0 + row["xy"][1]) for row in rows], crs=METRIC_CRS)


def _layers() -> dict:
    base = {"geometry_source_url": PULP, "geometry_source_id": "src_pulp", "source_url": PULP}
    bs = _point_layer([
        {"facility_id": "BS_A", "name": "Parkhaus A", "source_id": "aa", "retrieved_at_utc": "2026-10-07T05:24:39+00:00",
         "xy": (100, 100), **base},
        {"facility_id": "BS_B", "name": "Parkhaus B", "source_id": "bb", "retrieved_at_utc": "2026-10-07T05:24:39+00:00",
         "xy": (500, 100), **base},
        {"facility_id": "BS_T", "name": "Parkhaus T", "source_id": "tt", "retrieved_at_utc": "2026-10-07T05:24:39+00:00",
         "xy": (900, 100), **base}])
    wob_base = {"geometry_source_url": GEOVIEWER, "geometry_source_id": "src_wob", "source_url": GEOVIEWER,
                "retrieved_at_utc": "2026-10-07T05:24:49+00:00"}
    wob = _point_layer([{"facility_id": "WOB_X", "name": "Parkhaus X", "xy": (10_000, 100), **wob_base},
                        {"facility_id": "WOB_STATION", "name": "Parkdeck Bahnhof", "xy": (10_500, 100), **wob_base}])
    region = _point_layer([
        {"facility_id": "WOB_X", "name": "Saba X", "operator": "Saba", "geometry_method": "municipal_point",
         "geometry_source_url": GEOVIEWER, "retrieved_on": "2026-10-02", "xy": (10_000, 100)},
        {"facility_id": "GS_A", "name": "Parkhaus Ga", "operator": None, "geometry_method": "municipal_point_2018",
         "geometry_source_url": ARCGIS, "retrieved_on": "2026-10-02", "xy": (20_000, 100)},
        {"facility_id": "GS_B", "name": "Parkhaus Gb", "operator": "Goslar GmbH", "geometry_method": "directory_map_point",
         "geometry_source_url": "https://parkito.example/gb", "retrieved_on": "2026-10-02", "xy": (20_500, 100)}])
    gos = _point_layer([
        {"facility_id": "GS_B", "Bezeichnung": "Parkhaus Gb", "Nutzung": "Parkhaus", "geometry_source_url": ARCGIS,
         "geometry_source_id": "src_gos", "xy": (20_500, 155)},
        {"facility_id": "GOS_POINT_4", "Bezeichnung": "Parkhaus Charley", "Nutzung": "Parkhaus",
         "geometry_source_url": ARCGIS, "geometry_source_id": "src_gos", "xy": (21_000, 100)},
        {"facility_id": "GOS_POINT_3", "Bezeichnung": "Parkplatz Bolzen", "Nutzung": "Parkplatz",
         "geometry_source_url": ARCGIS, "geometry_source_id": "src_gos", "xy": (21_500, 100)}])
    return {"bs_parkhaeuser": bs, "wob_parkhaeuser": wob, "region_parkhaeuser": region, "gos_parking_locations": gos,
            "wob_parkplaetze": _point_layer([
                {"facility_id": f"WOB_PARK_{number}", "name": "Parkplatz", "tariff_rule_ids": "[]", "xy": (10_000, 500 + number)}
                for number in range(3)])}


def _write_package(directory: Path, layers: dict, rules: list, facilities: list) -> str:
    """Write the package zip the way the owner supplied it and return its SHA-256."""
    directory.mkdir(parents=True, exist_ok=True)
    gpkg = directory / "Regional_Parkdaten.gpkg"
    for layer, frame in layers.items():
        frame.to_file(gpkg, layer=layer, driver="GPKG")
    path = directory / f"{PACKAGE}.zip"
    sources = [{"source_id": "src_pulp", "url": PULP, "retrieval_dates": ["2026-10-07"]},
               {"source_id": "src_wob", "url": GEOVIEWER, "retrieval_dates": ["2026-10-02", "2026-10-07"]},
               {"source_id": "src_gos", "url": ARCGIS, "retrieval_dates": ["2026-10-01", "2026-10-02"]}]
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(gpkg, f"{PACKAGE}/daten/Regional_Parkdaten.gpkg")
        archive.writestr(f"{PACKAGE}/daten/tariff_rules.json", json.dumps({"schema_version": "2.0", "rules": rules}))
        archive.writestr(f"{PACKAGE}/daten/facilities.json", json.dumps(facilities))
        archive.writestr(f"{PACKAGE}/daten/sources.json", json.dumps(sources))
    gpkg.unlink()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _specs() -> tuple:
    """Garage specifications of the synthetic package: one per structure the step reads."""
    return (
        {"garage_id": "bs_a", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_A"),
         "facility": "BS_A", "rate": "a-2", "first": "a-1", "cap": "a-3", "window": "rate",
         "other_tiers": ("a-4", "a-5"), "comment": "Day and night tier."},
        {"garage_id": "bs_b", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_B"),
         "facility": "BS_B", "rate": "b-2", "first_equals_rate": "b-1", "cap": "b-3", "window": None,
         "ignored": {"b-4": "a lost ticket"}, "comment": ""},
        {"garage_id": "wob_x", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_X"),
         "attributes": ("region_parkhaeuser", ("facility_id", "WOB_X")), "facility": "WOB_X", "rate": "x-1", "cap": "x-2",
         "window": None, "other_tiers": ("x-3",), "comment": ""},
        {"garage_id": "gs_a", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_A"),
         "facility": "GS_A", "bands": ("g-1", "g-2"), "cap": "g-3", "window": None, "comment": ""},
        {"garage_id": "gs_b", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_B"),
         "facility": "GS_B", "rate": "h-1", "cap": "h-2", "window": "rate", "comment": ""},
        {"garage_id": "gs_c", "town": "gs", "layer": "gos_parking_locations", "feature": ("facility_id", "GOS_POINT_4"),
         "facility": "GOS_POINT_4", "reason": "no_published_tariff", "reason_text": "no rule", "evidence": (),
         "comment": ""},
        {"garage_id": "bs_t", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_T"),
         "facility": "BS_T", "tiers": ("t-1", "t-2", "t-3"), "cap": "t-4", "other_caps": ("t-6",), "comment": ""},
    )


def _spec(garage_id: str) -> dict:
    return next(spec for spec in _specs() if spec["garage_id"] == garage_id)


def _without(*garage_ids) -> tuple:
    return tuple(spec for spec in _specs() if spec["garage_id"] not in garage_ids)


MONTHLY = (
    {"record_id": "monthly_bs_a_tag", "rule": "a-m1", "garage_id": "bs_a", "decision": "used"},
    {"record_id": "monthly_bs_a_tag_nacht", "rule": "a-m2", "garage_id": "bs_a", "decision": "not_used",
     "reason": "not_the_cheapest"},
    {"record_id": "monthly_wob_station", "rule": "s-m1", "garage_id": None, "municipality_ags": WOB,
     "subject": "Parkdeck Bahnhof Dauerparken", "decision": "not_used", "reason": "garage_not_listed",
     "why": "the garage is a station BahnPark and no garage of the dataset"},
)
CANDIDATES = (
    {"record_id": "candidate_bs_additional", "town": "bs", "subject": "Parkhaus Lange Strasse Sued",
     "facility": "BS_ADDITIONAL_1", "reason": "no_coordinates", "note": "no coordinates in the package"},
    {"record_id": "candidate_wob_station", "town": "wob", "subject": "Parkdeck Bahnhof", "facility": "WOB_STATION",
     "reason": "station_bahnpark", "note": "a station car park of a private operator (rulings R-4b-4 and R-4b-9)"},
    {"record_id": "candidate_bs_brochure", "town": "bs", "subject": "A garage of a brochure", "facility": None,
     "reason": "no_coordinates", "evidence": "city brochure of December 2024", "note": "no facility record, no coordinates"},
    {"record_id": "candidate_wf_dauerparker", "town": "wf", "subject": "A lot of long-term renters", "facility": None,
     "reason": "dauerparker_only", "evidence": "operator page of the lots", "note": "for the moment long-term renters only"},
)
DIRECTORY = {"Parkplatz Markthalle": ("bga_zone", "BgA car park"), "Parkplatz Werder": ("zone_street_product", "zone 1")}


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    directory = tmp_path_factory.mktemp("municipal_2026-10-07")
    return directory, _write_package(directory, _layers(), _rules(), _facilities())


@pytest.fixture()
def step(garages_step, monkeypatch):
    """The step with the synthetic specifications in place of the real ones."""
    monkeypatch.setattr(garages_step.specs, "GARAGE_SPECS", _specs())
    monkeypatch.setattr(garages_step.specs, "MONTHLY_PRODUCTS", MONTHLY)
    monkeypatch.setattr(garages_step.specs, "PACKAGE_CANDIDATES", CANDIDATES)
    monkeypatch.setattr(garages_step.specs, "DIRECTORY_DECISIONS", DIRECTORY)
    return garages_step


@pytest.fixture()
def inputs(step, package):
    directory, sha256 = package
    return step.load_garage_inputs(directory, expected_sha256=sha256)


# --------------------------------------------------------------------------- the package is verified before it is read


def test_a_changed_or_missing_package_is_refused(step, package, tmp_path):
    directory, sha256 = package
    with pytest.raises(SystemExit, match="SHA-256"):
        step.load_garage_inputs(directory, expected_sha256="0" * 64)
    with pytest.raises(SystemExit, match="missing: copy the owner's package"):
        step.load_garage_inputs(tmp_path)
    loaded = step.load_garage_inputs(directory, expected_sha256=sha256)
    assert set(loaded["layers"]) == set(step.GARAGE_LAYERS) and loaded["file"]["sha256"] == sha256
    assert len(loaded["facilities"]) == len(_facilities()) and len(loaded["wob_lots"]) == 3


def test_a_facility_record_twice_in_the_package_is_refused(step, tmp_path):
    directory = tmp_path / "twice"
    sha256 = _write_package(directory, _layers(), _rules(), _facilities() + [_facilities()[0]])
    with pytest.raises(SystemExit, match="facility id BS_A twice"):
        step.load_garage_inputs(directory, expected_sha256=sha256)


# --------------------------------------------------------------------------- reading the rules by their roles


def test_a_first_period_a_rate_a_cap_and_a_window_are_read_from_their_rules(step, inputs):
    encoded = step.encode_tariff(_specs()[0], inputs["rules"])
    assert encoded["values"] == {"garage_hourly_rate_eur": 1.2, "garage_billing_unit_min": 60,
                                 "garage_first_period_min": 60, "garage_first_period_eur": 0.6,
                                 "garage_first_period_start_h": 7.0, "garage_first_period_end_h": 18.0,
                                 "garage_daily_cap_eur": 9.6, "garage_fee_start_h": 7.0, "garage_fee_end_h": 18.0,
                                 "tariff_tiers": None, "tariff_duration_bands": None}
    assert encoded["assumptions"] == ["P3"] and encoded["rounding_stated"] and encoded["window_stated"]
    assert encoded["rule_ids"] == ["a-1", "a-2", "a-3"]
    # the first period belongs to the clock window its rule states (ruling R-4b-12): the sentence says so
    assert encoded["sentence"] == ("0.60 EUR for the first 60 min (arrival 07:00-18:00), then 1.20 EUR per started 60 min, "
                                   "at most 9.60 EUR per day, charged 07:00-18:00")


def test_a_first_period_equal_to_one_billing_unit_is_the_rate_alone(step, inputs):
    encoded = step.encode_tariff(_specs()[1], inputs["rules"])
    assert encoded["values"]["garage_hourly_rate_eur"] == 1.5 and encoded["values"]["garage_first_period_min"] is None
    assert encoded["values"]["garage_first_period_eur"] is None and encoded["values"]["garage_daily_cap_eur"] == 12.0
    # no rounding stated and no charging times: the assumptions P4 and P5, never a silent default
    assert encoded["assumptions"] == ["P4", "P5"] and not encoded["rounding_stated"] and not encoded["window_stated"]
    assert (encoded["values"]["garage_fee_start_h"], encoded["values"]["garage_fee_end_h"]) == (0.0, 24.0)


def test_a_window_that_only_a_rule_that_is_not_preferred_states_is_not_taken(step, inputs):
    # x-3 states 21:00-06:30 but is not preferred for current use: no window is inferred from it (ruling R-4b-8), so the
    # window is 0-24 h by ASSUMPTION P5 and the night rule is a tier that the model does not charge (ASSUMPTION P3)
    encoded = step.encode_tariff(_spec("wob_x"), inputs["rules"])
    assert (encoded["values"]["garage_fee_start_h"], encoded["values"]["garage_fee_end_h"]) == (0.0, 24.0)
    assert encoded["values"]["garage_daily_cap_eur"] == 9.0 and encoded["assumptions"] == ["P3", "P5"]
    assert encoded["rule_ids"] == ["x-1", "x-2"] and not encoded["window_stated"] and encoded["rounding_stated"]
    assert encoded["readings"] == [("cap", "x-2", "day_definition_unspecified")]


@pytest.mark.parametrize("garage_id, rule_id, role", [
    ("bs_a", "a-2", "rate"), ("bs_a", "a-1", "first period"), ("bs_a", "a-3", "cap"), ("bs_b", "b-1", "first unit"),
    ("bs_t", "t-2", "tier"), ("bs_t", "t-4", "cap"), ("gs_a", "g-1", "band"), ("gs_a", "g-2", "band"),
    ("gs_a", "g-3", "cap")])
def test_a_rule_that_is_not_preferred_never_sets_a_value_in_any_role(step, inputs, garage_id, rule_id, role):
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    step.encode_tariff(_spec(garage_id), rules)  # the unchanged rules are accepted
    rules[rule_id]["preferred_for_current_use"] = False
    with pytest.raises(SystemExit, match=f"garage {garage_id}: the rule {rule_id} is named as the {role}, but the package "
                                         "does not mark it preferred_for_current_use"):
        step.encode_tariff(_spec(garage_id), rules)
    rules[rule_id]["preferred_for_current_use"] = None  # a flag that is not true is not a flag
    with pytest.raises(SystemExit, match=r"\(flag None, status"):
        step.encode_tariff(_spec(garage_id), rules)


def test_the_dataset_step_refuses_a_rule_that_is_not_preferred_and_a_monthly_product_that_is_not_preferred(step, inputs):
    inputs["rules"]["a-3"]["preferred_for_current_use"] = False
    with pytest.raises(SystemExit, match="the rule a-3 is named as the cap, but the package does not mark it"):
        step.build_garages(inputs)
    inputs["rules"]["a-3"]["preferred_for_current_use"] = True
    inputs["rules"]["a-m1"]["preferred_for_current_use"] = False
    with pytest.raises(SystemExit, match="the rule a-m1 is named as the monthly product, but the package does not mark it"):
        step.build_garages(inputs)


def test_tiers_are_read_from_their_rules_with_a_midnight_crossing_tier_the_cap_and_the_readings(step, inputs):
    encoded = step.encode_tariff(_spec("bs_t"), inputs["rules"])
    assert encoded["values"] == {
        "garage_hourly_rate_eur": None, "garage_billing_unit_min": None, "garage_first_period_min": None,
        "garage_first_period_eur": None, "garage_first_period_start_h": None, "garage_first_period_end_h": None,
        "garage_daily_cap_eur": 5.0, "garage_fee_start_h": None, "garage_fee_end_h": None,
        "tariff_tiers": "08:00-10:00 0.30/30; 10:00-18:00 0.60/30; 18:00-02:00 0.20/30", "tariff_duration_bands": None}
    assert encoded["tiers"] == [pg.TariffTier(480, 600, 30, 0.30), pg.TariffTier(600, 1080, 30, 0.60),
                                pg.TariffTier(1080, 120, 30, 0.20)] and encoded["tiers"][2].crosses_midnight
    assert encoded["assumptions"] == ["P6", "P7"] and encoded["rule_ids"] == ["t-1", "t-2", "t-3", "t-4"]
    assert encoded["window_stated"] and encoded["rounding_stated"]
    # the morning tier states no days: read as Monday to Friday; the cap is stated per calendar day: a maximum per stay
    assert encoded["readings"] == [("cap_calendar", "t-4", "calendar_day"), ("days", "t-1")]
    assert encoded["sentence"] == ("tiers per started 30 min, the tier in force at the unit's start: 08:00-10:00 0.30 EUR, "
                                   "10:00-18:00 0.60 EUR, 18:00-02:00 0.20 EUR, at most 5.00 EUR per day; free outside "
                                   "the tiers")
    # a tier that is listed in another order is sorted; a rounding that is not stated is ASSUMPTION P4 for the tiers too
    reordered = {**_spec("bs_t"), "tiers": ("t-3", "t-1", "t-2")}
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["t-1"]["rounding"] = None
    again = step.encode_tariff(reordered, rules)
    assert again["values"]["tariff_tiers"] == encoded["values"]["tariff_tiers"] and again["assumptions"] == ["P4", "P6", "P7"]


def test_the_tiered_form_refuses_what_the_tiers_cannot_express(step, inputs):
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    with pytest.raises(SystemExit, match="names exactly one of 'rate'"):
        step.encode_tariff({**_spec("bs_t"), "rate": "a-2"}, rules)
    with pytest.raises(SystemExit, match="names exactly one of 'rate'"):
        step.encode_tariff({**_spec("bs_a"), "rate": None}, rules)
    with pytest.raises(SystemExit, match="a tiered specification has no window"):
        step.encode_tariff({**_spec("bs_t"), "window": "rate"}, rules)
    with pytest.raises(SystemExit, match="first_equals_rate belongs to the single-window form"):
        step.encode_tariff({**_spec("bs_t"), "first_equals_rate": "b-1"}, rules)
    with pytest.raises(SystemExit, match="names exactly one of 'rate'"):
        step.encode_tariff({**_spec("bs_t"), "bands": ("g-1", "g-2")}, rules)  # tiers and bands exclude each other
    with pytest.raises(SystemExit, match="is no rate rule"):
        step.encode_tariff({**_spec("bs_t"), "tiers": ("t-1", "t-5")}, rules)
    changed = {key: dict(value) for key, value in rules.items()}
    changed["t-2"]["billing_unit_minutes"] = 60
    with pytest.raises(SystemExit, match=r"different billing units \[30, 60\] min"):
        step.encode_tariff(_spec("bs_t"), changed)
    changed = {key: dict(value) for key, value in rules.items()}
    changed["t-1"]["time_window"] = {"from": None, "to": None, "days_raw": None}
    with pytest.raises(SystemExit, match="the tier rule t-1 states no clock window"):
        step.encode_tariff(_spec("bs_t"), changed)
    changed = {key: dict(value) for key, value in rules.items()}
    changed["t-2"]["charging_times"] = _times("09:00", "18:00")  # overlaps the morning tier 08:00-10:00
    with pytest.raises(SystemExit, match="make no valid tiers"):
        step.encode_tariff(_spec("bs_t"), changed)
    changed = {key: dict(value) for key, value in rules.items()}
    changed["t-2"]["amount_eur"] = 0.605
    with pytest.raises(SystemExit, match="not a whole number of cents"):
        step.encode_tariff(_spec("bs_t"), changed)


def test_a_first_period_of_a_tiered_garage_must_be_followed_directly_by_a_tier(step, inputs):
    spec = {**_spec("bs_t"), "first": "t-5"}
    with pytest.raises(SystemExit, match=r"the first period ends at 30 min but the tier rules start at \[0\] min"):
        step.encode_tariff(spec, inputs["rules"])
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["t-2"]["elapsed_from_minutes"] = 30
    encoded = step.encode_tariff(spec, rules)
    assert (encoded["values"]["garage_first_period_min"], encoded["values"]["garage_first_period_eur"]) == (30, 0.5)
    assert encoded["rule_ids"][0] == "t-5" and encoded["sentence"].startswith("0.50 EUR for the first 30 min, then tiers per")
    # t-5 states no clock window: the first period is tied to none (empty columns), the whole day
    assert encoded["values"]["garage_first_period_start_h"] is None and encoded["values"]["garage_first_period_end_h"] is None


def test_the_first_period_carries_the_clock_window_of_its_rule_and_a_midnight_crossing_window_is_refused(step, inputs):
    # ruling R-4b-12: a first period that the source ties to a clock window carries it; the days are read as for a tier
    spec = {**_spec("bs_t"), "first": "t-5"}
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["t-2"]["elapsed_from_minutes"] = 30
    rules["t-5"]["time_window"] = {"from": "06:00", "to": "00:00", "days_raw": None}
    encoded = step.encode_tariff(spec, rules)
    assert (encoded["values"]["garage_first_period_start_h"], encoded["values"]["garage_first_period_end_h"]) == (6.0, 24.0)
    assert ("days", "t-5") in encoded["readings"]  # the window states no days: read as Monday to Friday, named
    assert encoded["sentence"].startswith("0.50 EUR for the first 30 min (arrival 06:00-24:00), then tiers per")
    rules["t-5"]["time_window"] = {"from": "06:00", "to": "00:00", "days_raw": "Mo-Su"}
    assert ("days", "t-5") not in step.encode_tariff(spec, rules)["readings"]
    # charging times of the weekdays are a window too (the Eiermarkt shape: a-1 states 07:00-18:00)
    single = step.encode_tariff(_spec("bs_a"), inputs["rules"])
    assert (single["values"]["garage_first_period_start_h"], single["values"]["garage_first_period_end_h"]) == (7.0, 18.0)
    assert single["readings"] == []
    rules["t-5"]["time_window"] = {"from": "22:00", "to": "06:00", "days_raw": None}
    with pytest.raises(SystemExit, match="rule t-5: the window 22:00-06:00 crosses midnight"):
        step.encode_tariff(spec, rules)


def _band_rules() -> dict:
    """Rules of a made-up garage F that cover every band kind: free, duration_total, increment (the Designer Outlets
    shape), a published day total from a duration (the BRAWO shape) and a 24-hour price."""
    rules = [
        _rule("f-1", "free", "F", 0.0, start=0, end=20),
        _rule("f-2", "duration_total", "F", 1.0, start=20, end=120),
        _rule("f-3", "increment", "F", 0.5, unit=60, rounding="started_unit", start=120, end=240),
        _rule("f-4", "increment", "F", 1.5, unit=60, rounding="started_unit", start=240, end=420),
        _rule("f-5", "increment", "F", 5.0, unit=60, rounding="started_unit", start=420),
        _rule("f-6", "published_day_total_from_duration", "F", 15.0, start=360, period="day_definition_unspecified"),
        _rule("f-7", "daily_rate_published", "F", 15.0, cap=15.0, period="24_hours"),
        _rule("f-8", "increment", "F", 1.5, unit=60, rounding="started_unit", start=0, end=360),
        _rule("f-9", "conditional_free", "F", 0.0, end=90, period="retailer_validation"),
    ]
    return {rule["rule_id"]: rule for rule in rules}


OUTLETS_BANDS = "0-20 free; 20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60"
F_SPEC = {"garage_id": "f", "bands": ("f-1", "f-2", "f-3", "f-4", "f-5"), "window": None}


def test_every_rule_type_of_a_band_is_mapped_to_its_kind_and_a_stated_rounding_replaces_assumption_p4(step):
    encoded = step.encode_tariff(F_SPEC, _band_rules())
    assert encoded["values"] == {
        "garage_hourly_rate_eur": None, "garage_billing_unit_min": None, "garage_first_period_min": None,
        "garage_first_period_eur": None, "garage_first_period_start_h": None, "garage_first_period_end_h": None,
        "garage_daily_cap_eur": None, "garage_fee_start_h": 0.0, "garage_fee_end_h": 24.0, "tariff_tiers": None,
        "tariff_duration_bands": OUTLETS_BANDS}
    # free -> free, duration_total -> total, increment -> increment; every increment states a started unit, so no P4
    assert encoded["assumptions"] == ["P5", "P8"] and encoded["rounding_stated"] and not encoded["window_stated"]
    assert encoded["rule_ids"] == ["f-1", "f-2", "f-3", "f-4", "f-5"] and encoded["tiers"] == []
    assert encoded["readings"] == []
    assert encoded["sentence"] == f"bands over the stay duration in minutes: {OUTLETS_BANDS}, charged 00:00-24:00"
    # the bands are sorted by their start whatever the order of the specification
    shuffled = step.encode_tariff({**F_SPEC, "bands": ("f-5", "f-3", "f-1", "f-4", "f-2")}, _band_rules())
    assert shuffled["values"]["tariff_duration_bands"] == OUTLETS_BANDS and shuffled["rule_ids"] == encoded["rule_ids"]
    # the text of the encoding is a valid schedule that evaluates to the anchors of the rules
    parsed = pg.parse_duration_bands(encoded["values"]["tariff_duration_bands"])
    assert pg.duration_band_price_eur(parsed, 20) == 0.0 and pg.duration_band_price_eur(parsed, 120) == 1.0


def test_a_published_day_total_and_a_24_hour_price_are_the_band_and_the_day_cap(step):
    spec = {"garage_id": "f", "bands": ("f-8", "f-6"), "window": None}
    encoded = step.encode_tariff(spec, _band_rules())
    assert encoded["values"]["tariff_duration_bands"] == "0-360 1.50/60; 360- total 15.00"
    assert encoded["values"]["garage_daily_cap_eur"] is None and encoded["assumptions"] == ["P5", "P8"]
    # a day total whose day boundary is not stated is read as a maximum per stay, named like the cap of a cap rule
    assert encoded["readings"] == [("cap", "f-6", "day_definition_unspecified")]
    # a daily_rate_published rule is the day cap of a banded garage (no reading: its period is stated)
    capped = step.encode_tariff({**spec, "cap": "f-7"}, _band_rules())
    assert capped["values"]["garage_daily_cap_eur"] == 15.0 and capped["rule_ids"] == ["f-8", "f-6", "f-7"]
    assert capped["readings"] == [("cap", "f-6", "day_definition_unspecified")]
    assert capped["sentence"] == ("bands over the stay duration in minutes: 0-360 1.50/60; 360- total 15.00, at most 15.00 EUR "
                                  "per day, charged 00:00-24:00")


def test_a_24_hour_total_that_spans_the_day_is_a_valid_cap_and_any_other_duration_total_is_not(step, inputs):
    encoded = step.encode_tariff(_spec("gs_a"), inputs["rules"])
    assert encoded["values"]["garage_daily_cap_eur"] == 25.0 and encoded["readings"] == []
    assert encoded["values"]["tariff_duration_bands"] == "0-180 1.50/60; 180- 0.80/30"
    assert encoded["assumptions"] == ["P4", "P5", "P8"] and encoded["rule_ids"] == ["g-1", "g-2", "g-3"]
    assert encoded["sentence"] == ("bands over the stay duration in minutes: 0-180 1.50/60; 180- 0.80/30, at most 25.00 EUR "
                                   "per day, charged 00:00-24:00")
    with pytest.raises(SystemExit, match="rule f-2: type duration_total is no cap rule"):
        step.encode_tariff({**F_SPEC, "cap": "f-2"}, _band_rules())  # 20-120 min is no 24-hour price
    shorter = _band_rules()
    shorter["f-2"]["elapsed_from_minutes"] = 0  # 0-120 min starts at the arrival but is not the whole day either
    with pytest.raises(SystemExit, match="rule f-2: type duration_total is no cap rule"):
        step.encode_tariff({**F_SPEC, "bands": ("f-8", "f-6"), "cap": "f-2"}, shorter)
    whole_day = _band_rules()
    whole_day["f-2"].update({"elapsed_from_minutes": None, "elapsed_to_minutes": 1440})  # a stated start is optional
    assert step.encode_tariff({**F_SPEC, "bands": ("f-8", "f-6"), "cap": "f-2"}, whole_day)["values"][
        "garage_daily_cap_eur"] == 1.0


def test_the_banded_form_takes_a_window_from_a_stated_rule_and_refuses_what_the_bands_cannot_express(step):
    rules = _band_rules()
    with pytest.raises(SystemExit, match="which states none"):
        step.encode_tariff({**F_SPEC, "window": "rate"}, rules)
    rules["f-1"]["time_window"] = {"from": "06:00", "to": "21:30", "days_raw": "Mo-Su"}
    windowed = step.encode_tariff({**F_SPEC, "window": "rate"}, rules)
    assert (windowed["values"]["garage_fee_start_h"], windowed["values"]["garage_fee_end_h"]) == (6.0, 21.5)
    assert windowed["assumptions"] == ["P8"] and windowed["window_stated"]
    stated = step.encode_tariff({**F_SPEC, "window": ("stated", "08:00", "20:00", "q")}, _band_rules())
    assert (stated["values"]["garage_fee_start_h"], stated["values"]["garage_fee_end_h"]) == (8.0, 20.0)
    assert stated["window_quotation"] == "q"
    for changes, message in (
            ({"first": "f-2"}, "a banded specification has no first period"),
            ({"first_equals_rate": "f-3"}, "a banded specification has no first period"),
            ({"bands": ("f-1", "f-7")}, "rule f-7 of type daily_rate_published is no band rule"),
            ({"bands": ("f-1", "f-9")}, "rule f-9 of type conditional_free is no band rule"),
            ({"bands": ("f-1", "f-3", "f-4", "f-5")}, "make no valid bands: gap or overlap"),
            ({"bands": ("f-2", "f-3", "f-4", "f-5")}, "make no valid bands: band '20-120 total 1.00': the first band must "
                                                      "start at 0 min")):
        with pytest.raises(SystemExit, match=f"garage f: .*{message}"):
            step.encode_tariff({**F_SPEC, **changes}, _band_rules())
    for rule_id, field, value, message in (
            ("f-3", "rounding", "pro_rata", "rule f-3 states the rounding 'pro_rata'"),
            ("f-2", "amount_eur", 1.005, "not a whole number of cents"),
            ("f-2", "amount_eur", None, "rule f-2 needs a positive amount"),
            ("f-3", "billing_unit_minutes", None, "needs a positive amount and a whole billing unit"),
            ("f-1", "amount_eur", 0.5, "free rule f-1 states the amount 0.5")):
        changed = _band_rules()
        changed[rule_id][field] = value
        with pytest.raises(SystemExit, match=message):
            step.encode_tariff(F_SPEC, changed)


def test_an_increment_band_without_a_stated_rounding_is_assumption_p4_and_an_all_total_schedule_needs_none(step):
    rules = _band_rules()
    rules["f-4"]["rounding"] = None
    rules["f-3"]["rounding"] = "unspecified"
    encoded = step.encode_tariff(F_SPEC, rules)
    assert encoded["assumptions"] == ["P4", "P5", "P8"] and not encoded["rounding_stated"]
    totals = {"garage_id": "f", "bands": ("f-1", "f-2"), "window": None}
    assert step.encode_tariff(totals, _band_rules())["assumptions"] == ["P5", "P8"]  # a total and a free band round nothing


def test_the_bands_of_a_garage_are_named_in_its_notes_with_assumption_p8(step, inputs):
    row = step.build_garage(inputs, _spec("gs_a"))
    assert row["assumptions"] == "P4;P5;P8" and row["tariff_duration_bands"] == "0-180 1.50/60; 180- 0.80/30"
    assert "ASSUMPTION P8: " in row["notes"] and "ASSUMPTION P4: " in row["notes"] and "ASSUMPTION P5: " in row["notes"]
    assert pd.isna(row["tariff_tiers"]) and pd.isna(row["garage_hourly_rate_eur"])


def test_a_published_day_total_counts_as_a_cap_of_the_garage_and_the_banded_flag_is_a_fact(step, inputs):
    # a garage with a day total from a duration (BRAWO shape) and no cap column counts as capped with its reading named
    inputs["rules"].update(_band_rules())
    inputs["facilities"]["GS_A"]["tariff_rule_ids"] = ["f-8", "f-6", "f-7"]
    row = step.build_garage(inputs, {**_spec("gs_a"), "bands": ("f-8", "f-6"), "cap": None})
    assert row["tariff_duration_bands"] == "0-360 1.50/60; 360- total 15.00" and pd.isna(row["garage_daily_cap_eur"])
    assert row["_facts"]["banded"] and row["_facts"]["cap"] and not row["_facts"]["tiered"]
    assert row["_facts"]["readings"] == [("cap", "f-6", "day_definition_unspecified")]
    assert "Reading: the day boundary of the cap f-6 is not stated (cap period 'day_definition_unspecified')" in row["notes"]
    plain = step.build_garage(inputs, {**_spec("gs_a"), "bands": ("f-8", "f-6"), "cap": "f-7"})
    assert plain["_facts"]["cap"] and plain["garage_daily_cap_eur"] == 15.0


def test_a_stated_window_needs_its_quotation_and_replaces_assumption_p5(step, inputs):
    quotation = "Oeffnungszeit: 24 Stunden"
    spec = {**_spec("bs_b"), "window": ("stated", "00:00", "24:00", quotation)}
    encoded = step.encode_tariff(spec, inputs["rules"])
    assert (encoded["values"]["garage_fee_start_h"], encoded["values"]["garage_fee_end_h"]) == (0.0, 24.0)
    assert encoded["assumptions"] == ["P4"] and encoded["window_stated"] and encoded["window_quotation"] == quotation
    row = step.build_garage(inputs, spec)
    assert f"Charging times stated by the source: {quotation}." in row["notes"] and "ASSUMPTION P5" not in row["notes"]
    assert row["assumptions"] == "P4"
    for bad in (("stated", "06:00", "06:00", "q"), ("stated", "00:00", "24:00", ""), ("stated", "20:00", "06:00", "q")):
        with pytest.raises(SystemExit, match="needs 0 <= start < end <= 24 and the quotation"):
            step.encode_tariff({**spec, "window": bad}, inputs["rules"])


def test_the_two_readings_are_named_and_counted(step, inputs):
    rules = inputs["rules"]
    assert step.cap_boundary_unspecified(rules["b-3"]) and step.cap_boundary_unspecified(rules["h-2"])  # unspecified, none
    assert not step.cap_boundary_unspecified(rules["a-3"]) and not step.cap_boundary_unspecified(rules["t-4"])
    assert step.window_days_stated(rules["a-2"]) and step.window_days_stated(rules["h-1"])
    assert not step.window_days_stated(rules["t-1"]) and not step.window_days_stated(rules["b-2"])
    assert step.encode_tariff(_spec("bs_a"), rules)["readings"] == []
    assert step.encode_tariff(_spec("bs_b"), rules)["readings"] == [("cap", "b-3", "unspecified_day")]
    assert step.encode_tariff(_spec("gs_b"), rules)["readings"] == [("cap", "h-2", None)]
    changed = {key: dict(value) for key, value in rules.items()}
    changed["b-2"]["time_window"] = {"from": "06:00", "to": "20:00", "days_raw": None}
    undated = step.encode_tariff({**_spec("bs_b"), "window": "rate"}, changed)
    assert undated["readings"] == [("cap", "b-3", "unspecified_day"), ("days", "b-2")]
    notes = {garage: row["notes"] for garage, row in step.build_garages(inputs).set_index("garage_id").iterrows()}
    assert "Reading: the day boundary of the cap b-3 is not stated (cap period 'unspecified_day'), so it is read as a " \
           "maximum per stay." in notes["bs_b"]
    assert "Reading: the window of t-1 states no days, so it is read as Monday to Friday." in notes["bs_t"]
    assert "Reading" not in notes["bs_a"]


def test_a_time_window_with_the_days_of_the_weekday_is_a_stated_window(step, inputs):
    # 08:00 to 00:00 on Mo-Sa except holidays is 8 to 24 h; the rounding is stated, so neither P4 nor P5 applies
    encoded = step.encode_tariff(_specs()[4], inputs["rules"])
    assert (encoded["values"]["garage_fee_start_h"], encoded["values"]["garage_fee_end_h"]) == (8.0, 24.0)
    assert encoded["assumptions"] == []


def test_a_rule_that_is_no_rate_and_a_rule_the_package_does_not_hold_are_refused(step, inputs):
    with pytest.raises(SystemExit, match="rule a-5 of type night_cap is no rate rule"):
        step.encode_tariff({**_specs()[0], "rate": "a-5"}, inputs["rules"])
    with pytest.raises(SystemExit, match="garage bs_a: the specification names the rule no-such-rule, which the package "
                                         "does not hold"):
        step.encode_tariff({**_specs()[0], "cap": "no-such-rule"}, inputs["rules"])


def test_a_rate_that_starts_after_zero_needs_a_first_period(step, inputs):
    spec = dict(_specs()[0])
    spec.update({"first": None})
    with pytest.raises(SystemExit, match="starts at 60 min and no first period"):
        step.encode_tariff(spec, inputs["rules"])


def test_a_first_period_must_end_where_the_rate_starts(step, inputs):
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["a-1"]["elapsed_to_minutes"] = 30
    with pytest.raises(SystemExit, match="first period ends at 30 min but the rate starts at 60 min"):
        step.encode_tariff(_specs()[0], rules)
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["a-1"]["rule_type"] = "lost_ticket_fee"
    with pytest.raises(SystemExit, match="is no first period"):
        step.encode_tariff(_specs()[0], rules)


def test_the_first_unit_rule_must_be_the_price_of_one_unit_of_the_rate(step, inputs):
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["b-1"]["amount_eur"] = 1.00
    with pytest.raises(SystemExit, match="is not the price of one billing unit"):
        step.encode_tariff(_specs()[1], rules)
    # a first rule that covers half a unit at the price of a whole one is a first period of its own, not the rate alone
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["b-1"]["elapsed_to_minutes"] = 30
    with pytest.raises(SystemExit, match="is not the price of one billing unit"):
        step.encode_tariff(_specs()[1], rules)


def test_a_cap_that_is_no_cap_a_missing_window_and_a_night_window_are_refused(step, inputs):
    spec = dict(_specs()[0])
    with pytest.raises(SystemExit, match="is no cap rule"):
        step.encode_tariff({**spec, "cap": "b-4"}, inputs["rules"])
    with pytest.raises(SystemExit, match="which states none"):
        step.encode_tariff({**spec, "rate": "x-1", "first": None, "other_tiers": (), "cap": None, "window": "rate"},
                           inputs["rules"])
    with pytest.raises(SystemExit, match="crosses midnight"):
        step.encode_tariff({**spec, "rate": "a-4", "first": None, "cap": None, "window": "rate"}, inputs["rules"])
    with pytest.raises(SystemExit, match="unknown window role"):
        step.encode_tariff({**spec, "window": "rule"}, inputs["rules"])


def test_a_rate_without_a_positive_amount_and_whole_unit_is_refused(step, inputs):
    rules = {key: dict(value) for key, value in inputs["rules"].items()}
    rules["a-2"]["billing_unit_minutes"] = None
    with pytest.raises(SystemExit, match="needs a positive amount and a whole billing unit"):
        step.encode_tariff(_specs()[0], rules)


def test_the_weekday_window_is_read_from_the_charging_times_or_the_time_window(step):
    rule = _rule("w", "increment", "F", 1.0, unit=60, times=_times("10:00", "18:00", days=("monday", "tuesday", "wednesday",
                                                                                           "thursday", "friday", "saturday")))
    assert step.rule_window(rule) == (10.0, 18.0)
    assert step.rule_window(_rule("w", "increment", "F", 1.0, unit=60, times=_times("00:00", "24:00"))) == (0.0, 24.0)
    assert step.rule_window(_rule("w", "increment", "F", 1.0, unit=60,
                                  window={"from": "06:00", "to": "00:00", "days_raw": None})) == (6.0, 24.0)
    assert step.rule_window(_rule("w", "increment", "F", 1.0, unit=60)) is None
    uneven = _times("10:00", "18:00")
    uneven["wednesday"] = [{"start": "09:00", "end": "18:00", "crosses_midnight": False}]
    with pytest.raises(SystemExit, match="different charging intervals"):
        step.rule_window(_rule("w", "increment", "F", 1.0, unit=60, times=uneven))
    missing = _times("10:00", "18:00", days=("monday", "tuesday", "wednesday", "thursday"))
    with pytest.raises(SystemExit, match="do not all state exactly one charging interval"):
        step.rule_window(_rule("w", "increment", "F", 1.0, unit=60, times=missing))
    with pytest.raises(SystemExit, match="days 'Su' are none of"):
        step.rule_window(_rule("w", "increment", "F", 1.0, unit=60, window={"from": "08:00", "to": "20:00", "days_raw": "Su"}))
    with pytest.raises(SystemExit, match="crosses midnight"):
        step.rule_window(_rule("w", "increment", "F", 1.0, unit=60, window={"from": "20:00", "to": "06:00", "days_raw": None}))


def test_a_night_window_is_read_only_for_a_tier(step):
    night = _rule("n", "increment", "F", 1.0, unit=60, times=NIGHT)
    with pytest.raises(SystemExit, match="crosses midnight, a night window is no fee window of the single-window form"):
        step.rule_window(night)
    assert step.rule_window(night, allow_midnight_crossing=True) == (18.0, 7.0)
    stated = _rule("n", "increment", "F", 1.0, unit=60, window={"from": "20:00", "to": "06:00", "days_raw": None})
    assert step.rule_window(stated, allow_midnight_crossing=True) == (20.0, 6.0)
    assert step.rule_window(_rule("n", "increment", "F", 1.0, unit=60), allow_midnight_crossing=True) is None


def test_rules_are_described_with_their_numbers_days_and_windows(step, inputs):
    rules = inputs["rules"]
    assert step.describe_rule(rules["a-4"]) == "a-4: 1.00 EUR per started 60 min Mo-Su 18:00-07:00"
    assert step.describe_rule(rules["a-5"]) == "a-5: 6.00 EUR at most (night_18_07) Mo-Su 18:00-07:00"
    assert step.describe_rule(rules["g-1"]) == "g-1: 1.50 EUR per 60 min for elapsed 0-180 min (elapsed_first_3_hours)"
    assert step.describe_rule(rules["h-1"]) == "h-1: 1.00 EUR per started 60 min 08:00-00:00 (Mo-Sa_except_public_holidays)"
    partial = _rule("p", "increment", "F", 0.3, unit=30, rounding="started_unit",
                    times={**_times("08:00", "22:00", days=("sunday",)), "public_holidays": [
                        {"start": "08:00", "end": "22:00", "crosses_midnight": False}]})
    assert step.describe_window(partial) == "Su,PH 08:00-22:00"
    week = _rule("p", "increment", "F", 0.3, unit=30, times=_times("10:00", "18:00", days=("monday", "tuesday", "wednesday",
                                                                                          "thursday", "friday", "saturday")))
    assert step.describe_window(week) == "Mo-Sa 10:00-18:00"
    # the mark is derived from the package's flag, never written by hand
    assert step.describe_rule(rules["x-3"]) == ("x-3: unresolved_night_tariff 1.00 EUR 21:00-06:30 "
                                                "[not preferred for current use]")
    assert "[not preferred" not in step.describe_rule(rules["x-1"])


# --------------------------------------------------------------------------- the dataset


def test_the_dataset_rows_carry_the_values_the_provenance_and_the_assumptions(step, inputs):
    frame = step.build_garages(inputs)
    pg.validate_garages(frame)
    assert list(frame["garage_id"]) == ["bs_a", "bs_b", "wob_x", "gs_a", "gs_b", "gs_c", "bs_t"]
    rows = frame.set_index("garage_id")
    bs_a = rows.loc["bs_a"]
    assert (bs_a["garage_hourly_rate_eur"], bs_a["garage_first_period_min"], bs_a["garage_first_period_eur"],
            bs_a["garage_daily_cap_eur"]) == (1.2, 60, 0.6, 9.6)
    assert bs_a["priced"] and bs_a["assumptions"] == "P3" and bs_a["tariff_rule_ids"] == "a-1;a-2;a-3"
    assert bs_a["source_url"] == "https://op.example/tariff" and bs_a["source_date"] == "2026-10-07"
    assert (bs_a["capacity_reported"], bs_a["capacity_scope"]) == (500, "reported_in_PULP")
    assert bs_a["geometry_method"] == "pulp_feed_point" and bs_a["geometry_source_url"] == PULP
    assert bs_a["package_sha256"] == inputs["file"]["sha256"] and bs_a["municipality_ags"] == BS
    assert bs_a["monthly_eur"] == 50.0 and bs_a["monthly_source_url"] == "https://op.example/tariff"
    assert bs_a["monthly_product"].startswith("Dauerparker Tag: 50.00 EUR per month; no minimum contract stated; waitlist")
    # the notes say what is encoded, name the assumption, list the tier that is not charged and the other capacity figure
    notes = bs_a["notes"]
    assert "0.60 EUR for the first 60 min (arrival 07:00-18:00), then 1.20 EUR per started 60 min" in notes
    assert "ASSUMPTION P3" in notes
    assert "Priced from the package rules a-1, a-2, a-3 (all marked preferred_for_current_use in the package)" in notes
    assert "a-4: 1.00 EUR per started 60 min Mo-Su 18:00-07:00" in notes and "operator_total 677" in notes
    assert "Monthly product a-m1" in notes
    bs_b = rows.loc["bs_b"]
    assert bs_b["assumptions"] == "P4;P5" and pd.isna(bs_b["garage_first_period_min"]) and pd.isna(bs_b["monthly_eur"])
    assert "ASSUMPTION P4" in bs_b["notes"] and "ASSUMPTION P5" in bs_b["notes"]
    assert "b-4: lost ticket 10.00 EUR (a lost ticket)" in bs_b["notes"]
    # a rule that is not preferred and used for nothing is named, derived from the flag
    assert "Not preferred for current use and not used: b-5." in bs_b["notes"]
    wob = rows.loc["wob_x"]
    assert wob["operator"] == "Saba" and wob["geometry_method"] == "municipal_point" and wob["geometry_source_url"] == GEOVIEWER
    assert wob["source_date"] == "2026-10-07" and wob["assumptions"] == "P3;P5" and wob["tariff_rule_ids"] == "x-1;x-2"
    # the night rule that is not preferred is a tier that is not charged (P3), marked in the note, and no window comes from it
    assert ("ASSUMPTION P3: a night tariff that is no per-unit rate of the preferred rules is not charged by the model: "
            "x-3: unresolved_night_tariff 1.00 EUR 21:00-06:30 [not preferred for current use].") in wob["notes"]
    assert "Not preferred for current use and not used" not in wob["notes"] and "ASSUMPTION P5" in wob["notes"]
    tiered = rows.loc["bs_t"]
    assert tiered["tariff_tiers"] == "08:00-10:00 0.30/30; 10:00-18:00 0.60/30; 18:00-02:00 0.20/30"
    assert tiered["priced"] and tiered["assumptions"] == "P6;P7" and tiered["tariff_rule_ids"] == "t-1;t-2;t-3;t-4"
    assert all(pd.isna(tiered[column]) for column in ("garage_hourly_rate_eur", "garage_billing_unit_min",
                                                      "garage_fee_start_h", "garage_fee_end_h"))
    assert tiered["garage_daily_cap_eur"] == 5.0
    assert "ASSUMPTION P6: units are counted from arrival and each started unit costs the rate of the tier in force at the " \
           "unit's start" in tiered["notes"]
    assert "an arrival outside the window pays the tiers from the arrival" in tiered["notes"]
    assert "ASSUMPTION P7: the day cap column holds one cap and applies to the whole stay; not applied: t-6: 2.00 EUR at " \
           "most (night_18_02)." in tiered["notes"]
    assert "Rules from another page than source_url: t-3 (https://op.example/evening)." in tiered["notes"]
    assert pd.isna(rows.loc["bs_a", "tariff_tiers"])
    gs_a = rows.loc["gs_a"]
    # the banded form: the bands text, the 24-hour price as the day cap, the fee window 0-24 h (P5), no rate and no unit
    assert gs_a["priced"] and pd.isna(gs_a["not_priced_reason"]) and gs_a["tariff_rule_ids"] == "g-1;g-2;g-3"
    assert gs_a["tariff_duration_bands"] == "0-180 1.50/60; 180- 0.80/30" and gs_a["garage_daily_cap_eur"] == 25.0
    assert pd.isna(gs_a["garage_hourly_rate_eur"]) and pd.isna(gs_a["garage_billing_unit_min"])
    assert pd.isna(gs_a["tariff_tiers"]) and (gs_a["garage_fee_start_h"], gs_a["garage_fee_end_h"]) == (0.0, 24.0)
    assert gs_a["assumptions"] == "P4;P5;P8"
    assert ("ASSUMPTION P8: a duration schedule is read cumulatively over the elapsed duration d of the stay: a total band "
            "sets the price of the stay to its amount") in gs_a["notes"]
    assert ("Priced from the package rules g-1, g-2, g-3 (all marked preferred_for_current_use in the package): bands over "
            "the stay duration in minutes: 0-180 1.50/60; 180- 0.80/30, at most 25.00 EUR per day") in gs_a["notes"]
    # the first-period window of the single-window form is the clock window of its rule
    assert (bs_a["garage_first_period_start_h"], bs_a["garage_first_period_end_h"]) == (7.0, 18.0)
    assert pd.isna(tiered["garage_first_period_start_h"]) and pd.isna(bs_b["garage_first_period_start_h"])
    # the twin of a garage in another layer is named with its distance, never hidden
    assert "second point of this garage in gos_parking_locations (coordinates differ by 55 m)" in rows.loc["gs_b", "notes"]
    gs_c = rows.loc["gs_c"]
    assert gs_c["not_priced_reason"] == "no_published_tariff" and pd.isna(gs_c["tariff_rule_ids"])
    assert gs_c["source_url"] == ARCGIS and gs_c["source_date"] == "2026-10-02"  # the retrieval of the geometry source
    assert gs_c["geometry_method"] == "arcgis_service_point_2018"


def test_the_rates_of_the_assumptions_are_printed(step, inputs, capsys):
    step.build_garages(inputs)
    out = capsys.readouterr().out
    assert "[garages] 7 garages: priced 6 (85.7 %), not priced 1 (no_published_tariff 1)" in out
    for line in ("ASSUMPTION P3: 2/6 (33.3 %)", "ASSUMPTION P4: 2/6 (33.3 %)", "ASSUMPTION P5: 3/6 (50.0 %)",
                 "ASSUMPTION P6: 1/6 (16.7 %)", "ASSUMPTION P7: 1/6 (16.7 %)", "ASSUMPTION P8: 1/6 (16.7 %)"):
        assert f"priced garages resting on {line}" in out, line
    assert ("rounding stated by the source 4/6, ASSUMPTION P4 2/6; charging times stated 3/6, ASSUMPTION P5 3/6; tiered 1/6; "
            "banded 1/6") in out
    assert "monthly product on 1 garages" in out
    # the union rates: 5 of 6 rest on at least one assumption (above the warning threshold), 3 of 6 on P4 or P5
    assert ("priced garages resting on at least one assumption: 5/6 (83.3 %); WARNING: more than 75 % of the priced "
            "garages") in out
    assert "priced garages resting on ASSUMPTION P4 or P5: 3/6 (50.0 %)\n" in out
    # the two readings: three of the six capped garages have no stated day boundary, one window states no days
    assert ("reading: a day cap whose day boundary is not stated is read as a maximum per stay: 3/6 garages with a cap or a "
            "day total; a window without stated days is read as Monday to Friday: 1 garage(s)") in out
    # the layer accounting: the Wolfsburg and Goslar garages exist in two layers, the twin is skipped and counted
    assert "[garages] gos_parking_locations: 3 features, 2 garages, 1 used, 1 twin(s)" in out
    assert "[garages] region_parkhaeuser: 3 features, 3 garages, 3 used, 0 twin(s)" in out
    assert "[garages] wob_parkhaeuser: 2 features, 2 garages, 1 used, 0 twin(s) of a garage that another layer gives (same " \
           "facility id, skipped), 1 decided as candidate(s)" in out


def test_a_share_of_one_assumption_above_the_warning_threshold_is_a_warning(step, inputs, capsys, monkeypatch):
    step.build_garages(inputs)
    assert "WARNING: more than 75 % of the priced garages rest on it" not in capsys.readouterr().out
    monkeypatch.setattr(step, "ASSUMPTION_WARNING_SHARE", 0.3)
    step.build_garages(inputs)
    out = capsys.readouterr().out
    assert "ASSUMPTION P3: 2/6 (33.3 %); WARNING: more than 30 % of the priced garages rest on it" in out
    assert "ASSUMPTION P6: 1/6 (16.7 %) - " in out


def test_a_garage_that_no_specification_covers_stops_the_step(step, inputs, monkeypatch):
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", _without("gs_c"))  # the Goslar Parkhaus without a tariff dropped
    with pytest.raises(SystemExit, match=r"gos_parking_locations: garage feature\(s\) \['GOS_POINT_4'\] are covered by no"):
        step.build_garages(inputs)
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", _specs()[1:])  # bs_a dropped
    with pytest.raises(SystemExit, match=r"bs_parkhaeuser: garage feature\(s\) \['BS_A'\] are covered by no"):
        step.build_garages(inputs)


def test_two_specifications_must_not_use_one_feature_or_one_id(step, inputs, monkeypatch):
    twin = dict(_specs()[1], garage_id="bs_b2")
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", _specs() + (twin,))
    with pytest.raises(SystemExit, match="two garage specifications use the same feature"):
        step.build_garages(inputs)
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", (_specs()[0],) + _specs())
    with pytest.raises(SystemExit, match="share a garage_id"):
        step.build_garages(inputs)


def test_a_feature_that_is_not_unique_stops_the_step(step, inputs, monkeypatch):
    spec = dict(_specs()[0], feature=("facility_id", "BS_NOWHERE"))
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", (spec,) + _specs()[1:])
    with pytest.raises(SystemExit, match=r"bs_parkhaeuser: 0 feature\(s\) with facility_id = 'BS_NOWHERE'"):
        step.build_garages(inputs)


def test_a_rule_that_is_not_a_rule_of_the_facility_stops_the_step(step, inputs, monkeypatch):
    spec = dict(_specs()[1], cap="a-3")  # a rule of another facility
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", (_specs()[0], spec) + _specs()[2:])
    with pytest.raises(SystemExit, match=r"the rules \['a-3'\] are not in the tariff rules of the package facility BS_B"):
        step.build_garages(inputs)
    spec = dict(_specs()[1], cap="no-such-rule")
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", (_specs()[0], spec) + _specs()[2:])
    with pytest.raises(SystemExit, match="names the rule no-such-rule, which the package does not hold"):
        step.build_garages(inputs)


def test_an_unknown_reason_code_stops_the_step(step, inputs, monkeypatch):
    spec = dict(_specs()[3], reason="too_hard")
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", _specs()[:3] + (spec,) + _specs()[4:])
    with pytest.raises(SystemExit, match="unknown reason 'too_hard'"):
        step.build_garages(inputs)


def test_the_monthly_product_must_be_the_cheapest_of_the_garage(step, inputs, monkeypatch):
    dearer = ({**MONTHLY[0], "rule": "a-m2"}, {**MONTHLY[1], "rule": "a-m1"})
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", dearer)
    with pytest.raises(SystemExit, match="is dearer than a-m1"):
        step.build_garages(inputs)
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", (MONTHLY[0], {**MONTHLY[1], "decision": "used", "reason": ""}))
    with pytest.raises(SystemExit, match="2 used monthly products"):
        step.build_garages(inputs)


def test_the_operator_is_taken_from_a_page_that_names_it_and_the_note_says_so(step, inputs):
    spec = {**_spec("bs_b"), "operator": "Op Example"}  # the primary source of bs_b is https://op.example/tariff
    row = step.build_garage(inputs, spec)
    assert row["operator"] == "Op Example"
    assert "Operator Op Example, named by its own page https://op.example/tariff (the primary source)." in row["notes"]
    with pytest.raises(SystemExit, match="the operator 'Contipark' is not the owner of the page https://op.example/tariff"):
        step.build_garage(inputs, {**spec, "operator": "Contipark"})
    assert step._operator_named_by_page("Park und Tank", "https://www.parkundtank.de/x", "g") == "Park und Tank"
    # an explicit None says that the package's text is no operator
    assert step.build_garage(inputs, {**_spec("wob_x"), "operator": None})["operator"] is None
    assert step.build_garage(inputs, _spec("wob_x"))["operator"] == "Saba"


def test_a_reported_capacity_never_gets_a_guessed_scope(step):
    with pytest.raises(SystemExit, match="the capacity 100 has no capacity_scope and no capacity observation with that "
                                         "value; the scope of a reported capacity is never guessed"):
        step._capacity({"facility_id": "F", "capacity": 100, "capacity_scope": None,
                        "capacity_observations": [{"value": 90, "scope": "operator_total"}]})
    assert step._capacity({"facility_id": "F", "capacity": 500, "capacity_scope": None, "capacity_observations": [
        {"value": 500, "scope": "reported_in_PULP"}, {"value": 677, "scope": "operator_total", "source_url": "https://x"}]}
    ) == (500, "reported_in_PULP", ["operator_total 677 (https://x)"])
    assert step._capacity({"facility_id": "F", "capacity": None, "capacity_observations": []}) == (None, None, [])


def test_the_rounding_census_is_printed_and_cited_by_assumption_p4(step, package, capsys):
    directory, sha256 = package
    loaded = step.load_garage_inputs(directory, expected_sha256=sha256)
    # 13 preferred rate rules of the facilities that have a point in a garage layer; the rule that is not preferred (b-5) and
    # the facility without a point (BS_ADDITIONAL_1) are not counted; 8 state a rounding, 8 of those say started unit
    assert loaded["rounding_census"] == {"rate_rules": 13, "stated": 8, "started_unit": 8}
    assert ("rounding census (the basis of ASSUMPTION P4): of 13 preferred garage rate rules 8 state their rounding, 8 of "
            "them as started unit") in capsys.readouterr().out
    notes = step.build_garage(loaded, _spec("bs_b"))["notes"]
    assert "ASSUMPTION P4: the rounding of the rate is not stated, so it is billed per started unit." in notes
    assert "Ein angegebener Stundensatz beweist noch keine Aufrundung je Stunde" in notes
    assert "8 of 13 state their rounding, and 8 of those 8 say started unit" in notes


# --------------------------------------------------------------------------- positions


def _municipalities() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"ags": [BS, WOB, GS]}, geometry=[box(X0 - 1000, Y0 - 1000, X0 + 2000, Y0 + 1000),
                                                              box(X0 + 9_000, Y0 - 1000, X0 + 12_000, Y0 + 1000),
                                                              box(X0 + 19_000, Y0 - 1000, X0 + 23_000, Y0 + 1000)],
                            crs=METRIC_CRS).set_index("ags")


def test_every_garage_must_lie_inside_its_municipality_and_no_two_may_coincide(step, inputs, capsys):
    frame = step.build_garages(inputs)
    step.check_positions(frame, _municipalities())
    assert "position check: 7/7 garages inside their own municipality, no two garages within 1 m" in capsys.readouterr().out
    moved = frame.copy()
    moved.loc[moved["garage_id"] == "gs_c", "geometry"] = Point(X0 + 5000, Y0)
    with pytest.raises(SystemExit, match=r"garages outside their municipality: gs_c \(03153017, \d+ m outside\)"):
        step.check_positions(moved, _municipalities())
    doubled = frame.copy()
    doubled.loc[doubled["garage_id"] == "bs_b", "geometry"] = doubled.loc[doubled["garage_id"] == "bs_a", "geometry"].iloc[0]
    with pytest.raises(SystemExit, match=r"less than 1 m apart.*bs_a/bs_b"):
        step.check_positions(doubled, _municipalities())


# --------------------------------------------------------------------------- the QA table


def _directory(tmp_path, step, monkeypatch, names=("Parkplatz Markthalle", "Parkplatz Werder")) -> Path:
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [10.52, 52.26]},
                 "properties": {"name": name, "id": f"id{number}", "mapsightIconId": "parkflaeche",
                                "description": "<p>Tarife:</p><p>Parkzone 1 0,90 \u20ac / 30 Min.</p>"}}
                for number, name in enumerate(names)]
    path = tmp_path / "directory.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    monkeypatch.setattr(step, "DIRECTORY_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    return path


def test_the_qa_table_lists_every_garage_product_and_candidate_and_validates(step, inputs, tmp_path, monkeypatch):
    frame = step.build_garages(inputs)
    directory = step.load_directory(_directory(tmp_path, step, monkeypatch))
    assert [entry["name"] for entry in directory] == ["Parkplatz Markthalle", "Parkplatz Werder"]
    assert directory[0]["text"] == "Parkzone 1 0,90 EUR / 30 Min."  # the euro sign has an ASCII form, the markup is gone
    rows = step.qa_rows(inputs, frame, directory)
    table = pd.DataFrame(rows)
    assert list(table.columns) == list(pq.GARAGE_QA_COLUMNS)
    assert (table["record_type"] == "garage").sum() == 7 and (table["record_type"] == "monthly_product").sum() == 3
    # two directory entries, four package candidates (one with a facility record, one a station, two with an evidence only),
    # the lots
    assert (table["record_type"] == "candidate").sum() == 2 + 4 + 1
    lots = table[table["record_id"] == "candidate_wob_car_parks"].iloc[0]
    assert lots["count"] == "3" and lots["reason_code"] == "no_published_tariff"
    used = table[table["decision"] == "used"].iloc[0]
    assert used["garage_id"] == "bs_a" and used["amount_eur"] == "50.00"
    assert table[table["record_id"] == "monthly_bs_a_tag_nacht"].iloc[0]["reason_code"] == "not_the_cheapest"
    # a product of a garage that is no garage of the dataset: no garage id, the town of the product, the reason and its why
    station = table[table["record_id"] == "monthly_wob_station"].iloc[0]
    assert station["garage_id"] == "" and station["municipality_ags"] == WOB and station["reason_code"] == "garage_not_listed"
    assert station["amount_eur"] == "100.00" and station["note"].endswith(
        "; the garage is a station BahnPark and no garage of the dataset")
    assert station["subject"] == "Parkdeck Bahnhof Dauerparken" and station["evidence"] == "s-m1"
    # candidates: a station BahnPark with its package rules, a candidate with an evidence and no facility record
    bahnpark = table[table["record_id"] == "candidate_wob_station"].iloc[0]
    assert bahnpark["reason_code"] == "station_bahnpark" and bahnpark["evidence"] == "facilities.json WOB_STATION"
    assert "package rules: s-1: 1.00 EUR per 60 min; s-m1: monthly_product 100.00 EUR" in bahnpark["note"]
    brochure = table[table["record_id"] == "candidate_bs_brochure"].iloc[0]
    assert brochure["evidence"] == "city brochure of December 2024" and "package rules" not in brochure["note"]
    assert table[table["record_id"] == "candidate_wf_dauerparker"].iloc[0]["reason_code"] == "dauerparker_only"
    assert table[table["record_id"] == "candidate_bs_directory_parkplatz_markthalle"].iloc[0]["reason_code"] == "bga_zone"
    pq.validate_garage_qa(table, frame)
    assert pq.qa_coverage(table) == {
        "monthly_used": 1, "monthly_not_used": 2,
        "monthly_not_used_by_reason": {"garage_not_listed": 1, "not_the_cheapest": 1}, "candidates": 9,
        "candidates_by_reason": {"bga_zone": 1, "dauerparker_only": 1, "no_coordinates": 2, "no_published_tariff": 3,
                                 "station_bahnpark": 1, "zone_street_product": 1}}


def test_a_candidate_decision_never_hides_a_garage_and_a_candidate_without_a_record_needs_its_evidence(
        step, inputs, tmp_path, monkeypatch):
    # a garage of a layer that no specification and no candidate decision covers stops the step
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", tuple(c for c in CANDIDATES if c["facility"] != "WOB_STATION"))
    with pytest.raises(SystemExit, match=r"wob_parkhaeuser: garage feature\(s\) \['WOB_STATION'\] are covered by no"):
        step.build_garages(inputs)
    # a facility is a garage or a candidate, never both
    both = {"record_id": "c", "town": "bs", "subject": "x", "facility": "BS_A", "reason": "no_coordinates", "note": "n"}
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", CANDIDATES + (both,))
    with pytest.raises(SystemExit, match=r"facility\(ies\) \['BS_A'\] are both a garage of the dataset and a candidate"):
        step.build_garages(inputs)
    # 'no coordinates' must be true: the facility of a candidate that has a point in a garage layer is no such candidate
    wrong = dict(CANDIDATES[1], reason="no_coordinates")
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", (CANDIDATES[0], wrong) + CANDIDATES[2:])
    with pytest.raises(SystemExit, match="the reason is no_coordinates but the facility WOB_STATION has a feature in a "
                                         "garage layer"):
        step.build_garages(inputs)
    # a candidate without a facility record states the evidence it rests on
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", CANDIDATES)
    frame = step.build_garages(inputs)
    bare = {"record_id": "candidate_bare", "town": "bs", "subject": "x", "facility": None, "reason": "no_coordinates",
            "note": "n"}
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", CANDIDATES + (bare,))
    with pytest.raises(SystemExit, match="candidate candidate_bare: a candidate without a facility record needs its evidence"):
        step.qa_rows(inputs, frame, step.load_directory(_directory(tmp_path, step, monkeypatch)))


def test_a_changed_or_unknown_car_park_directory_is_refused(step, tmp_path, monkeypatch):
    path = _directory(tmp_path, step, monkeypatch)
    step.load_directory(path)
    path.write_text(path.read_text(encoding="utf-8").replace("Markthalle", "Marktplatz"), encoding="utf-8")
    with pytest.raises(SystemExit, match="a changed directory is never read"):
        step.load_directory(path)
    with pytest.raises(SystemExit, match="missing"):
        step.load_directory(tmp_path / "absent.geojson")
    # an entry without a decision stops the step even when the file matches its hash
    other = _directory(tmp_path, step, monkeypatch, names=("Parkplatz Markthalle", "Parkplatz Werder", "Parkplatz Neu"))
    with pytest.raises(SystemExit, match=r"entries without a decision \['Parkplatz Neu'\]"):
        step.load_directory(other)
    short = _directory(tmp_path, step, monkeypatch, names=("Parkplatz Markthalle",))
    with pytest.raises(SystemExit, match=r"decisions without an entry \['Parkplatz Werder'\]"):
        step.load_directory(short)


def test_a_wolfsburg_car_park_with_a_tariff_needs_a_decision(step, inputs, tmp_path, monkeypatch):
    frame = step.build_garages(inputs)
    inputs["wob_lots"].loc[0, "tariff_rule_ids"] = '["WOB_X_1"]'
    with pytest.raises(SystemExit, match="1 car park\\(s\\) carry tariff rules"):
        step.qa_rows(inputs, frame, step.load_directory(_directory(tmp_path, step, monkeypatch)))


def test_the_files_are_written_ascii_with_every_column_defined_and_reload_clean(step, inputs, tmp_path, monkeypatch):
    frame = step.build_garages(inputs)
    path, qa_path = tmp_path / "garages.geojson", tmp_path / "qa.csv"
    pg.write_garages(frame, path, members=step.dataset_members())
    step.write_garage_qa(qa_path, step.qa_rows(inputs, frame, step.load_directory(_directory(tmp_path, step, monkeypatch))))
    assert path.read_text(encoding="utf-8").isascii() and qa_path.read_text(encoding="utf-8").isascii()
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document["documentation"]["columns"]) == set(pg.DATASET_COLUMNS)
    assert "ODbL 1.0" in document["license"] and "OpenStreetMap" in document["attribution"]
    header = [line for line in qa_path.read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    for column in pq.GARAGE_QA_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column
    loaded = pg.load_garages(path)
    pg.validate_garages(loaded)
    pq.validate_garage_qa(pq.load_garage_qa(qa_path), loaded)


def test_a_glossary_that_misses_a_column_stops_the_writers(step, monkeypatch):
    monkeypatch.setattr(step, "COLUMN_GLOSSARY", {key: "x" for key in list(step.COLUMN_GLOSSARY)[:-1]})
    with pytest.raises(SystemExit, match="COLUMN_GLOSSARY and DATASET_COLUMNS differ"):
        step.dataset_members()
    monkeypatch.setattr(step, "QA_COLUMN_GLOSSARY", {key: "x" for key in list(step.QA_COLUMN_GLOSSARY)[:-1]})
    with pytest.raises(SystemExit, match="QA_COLUMN_GLOSSARY and GARAGE_QA_COLUMNS differ"):
        step.write_garage_qa("unused.csv", [])


def test_two_runs_of_the_step_write_identical_bytes(step, package, tmp_path, monkeypatch):
    directory, sha256 = package
    written = []
    for run in ("first", "second"):
        (tmp_path / run).mkdir()
        inputs = step.load_garage_inputs(directory, expected_sha256=sha256)
        frame = step.build_garages(inputs)
        rows = step.qa_rows(inputs, frame, step.load_directory(_directory(tmp_path / run, step, monkeypatch)))
        pg.write_garages(frame, tmp_path / run / "garages.geojson", members=step.dataset_members())
        step.write_garage_qa(tmp_path / run / "qa.csv", rows)
        written.append((tmp_path / run / "garages.geojson").read_bytes() + (tmp_path / run / "qa.csv").read_bytes())
    assert written[0] == written[1]


# --------------------------------------------------------------------------- the committed dataset

#: SHA-256 of the owner's packages of 2026-10-07 that the garages cite: the regional package (every row), the supplement package
#: (spec E12) and the follow-up package (spec E13) where they touch a row, in that order, ';'-joined.
REGIONAL_PACKAGE_SHA256 = "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7"
SUPPLEMENT_PACKAGE_SHA256 = "76d2651433e05a4d0d0a75ba352fd17f99b329555eb3bb4525c34990383978fa"
FOLLOWUP_PACKAGE_SHA256 = "3bbaff93fb8b26d6cdfb187c7e0bf746f099997c1c7fd04a961fcae7d37329d8"
GARAGES_PATH = COMMITTED_PARKING_DIR / "parking_garages_2026.geojson"
GARAGES_QA_PATH = COMMITTED_PARKING_DIR / "parking_garages_2026_qa.csv"
TARIFFS_PATH = COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv"
WF, GF, HE, PE, SZ = "03158037", "03151009", "03154028", "03157006", "03102000"

class Priced(NamedTuple):
    """A priced garage as the sources state it (written by hand, not read back from the dataset): the municipality, the
    single window (hourly rate in EUR, billing unit in minutes, fee window start and end in decimal hours; all None for a tiered
    or banded garage except the fee window of a banded one), the first period (minutes, EUR and its clock window), the day cap in
    EUR, the time-of-day tiers or the duration bands (one form per garage), the monthly product in EUR, the reported capacity
    and the assumptions."""

    ags: str
    rate: object
    unit: object
    first_min: object
    first_eur: object
    cap: object
    start: object
    end: object
    tiers: object
    monthly: object
    capacity: object
    assumptions: object
    first_window: object = None
    bands: object = None


#: The 35 priced garages as the sources state them (operator pages, the PULP feed, the Geoviewer, the package's preferred
#: tariff rules, the supplement and follow-up packages of 2026-10-07 under the owner's decisions of specs E12 and E13). A garage
#: has ONE form: the single window (rate, unit, start, end) OR tiers OR bands (with the fee window set). The assumptions: P3 a
#: night tariff that is no per-unit rate is not charged (no garage rests on it any more), P4 a rate without a stated rounding is
#: billed per started unit, P5 the fee window 0-24 h where no preferred rule states charging times, P6 the pricing of tiers
#: (units counted from arrival, the tier in force at the unit's start; a first period inside its clock window), P7 caps other
#: than the day cap are not applied, P8 the cumulative reading of duration bands, P10 a free period is a grace period, P11 the
#: best available secondary evidence where no operator tariff is published (at Groepern: the monthly product only).
EIERMARKT_TIERS = "07:00-18:00 1.20/60; 18:00-07:00 1.00/60"
RATHAUS_TIERS = "00:00-06:00 0.50/60; 06:00-24:00 1.20/60"
PEINE_BANDS = "0-30 total 0.20; 30-60 total 0.80; 60-300 0.40/30; 300-1440 total 4.00"
OUTLETS_BANDS_TEXT = "0-20 free; 20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60"
AIRPORT_BANDS = "0-15 free; 15-60 total 1.50; 60- 1.50/60"
SUEDKOPF_BANDS = "0-30 free; 30-60 total 1.00; 60- 1.00/60"
ACHTERMANN_BANDS = "0-180 1.50/60; 180-570 0.80/30; 570-720 total 15.00; 720- 1.00/60"
POSTSTRASSE_TIERS = "06:30-21:00 2.00/60; 21:00-06:30 1.00/60"
PRICED_GARAGES = {
    "bs_eiermarkt": Priced(BS, None, None, 60, 0.60, 9.60, None, None, EIERMARKT_TIERS, None, 500, "P6;P7",
                           first_window=(7.0, 18.0)),
    "bs_forschungsflughafen": Priced(BS, None, None, None, None, 18.00, 0.0, 24.0, None, None, None, "P5;P8;P10",
                                     bands=AIRPORT_BANDS),
    "bs_lange_strasse_nord": Priced(BS, 1.50, 60, None, None, 10.00, 0.0, 24.0, None, None, 150, "P4;P5"),
    "bs_lange_strasse_sued": Priced(BS, 1.00, 30, 60, 1.00, 10.00, 0.0, 24.0, None, None, None, "P4;P5"),
    "bs_magni": Priced(BS, 1.20, 60, None, None, 9.60, 0.0, 24.0, None, None, None, "P5"),
    "bs_packhof": Priced(BS, 1.20, 60, None, None, 9.60, 0.0, 24.0, None, None, 954, "P5"),
    "bs_ring_center": Priced(BS, None, None, None, None, 15.00, 0.0, 24.0, None, None, None, "P5;P8",
                             bands="0-120 1.50/60; 120- 2.00/60"),
    "bs_schloss": Priced(BS, 2.00, 60, None, None, 20.00, 0.0, 24.0, None, None, 1300, "P4;P5"),
    "bs_schuetzenstrasse": Priced(BS, 2.00, 60, None, None, 15.00, 0.0, 24.0, None, None, 167, "P4;P5"),
    "bs_steinstrasse": Priced(BS, 1.80, 60, None, None, 18.00, 0.0, 24.0, None, None, None, "P4;P5"),
    "bs_wallstrasse": Priced(BS, 2.90, 60, None, None, 19.00, 0.0, 24.0, None, None, 455, None),
    "bs_wilhelmstrasse": Priced(BS, 1.20, 60, None, None, 8.00, 0.0, 24.0, None, None, 530, "P4;P5"),
    "wob_suedkopf": Priced(WOB, None, None, None, None, 5.00, 0.0, 24.0, None, None, None, "P4;P5;P8;P10;P11",
                           bands=SUEDKOPF_BANDS),
    "wob_rathaus": Priced(WOB, None, None, 60, 1.10, 6.00, None, None, RATHAUS_TIERS, 50.0, 732, "P4;P6;P7",
                          first_window=(6.0, 24.0)),
    "wob_poststrasse": Priced(WOB, None, None, None, None, 9.00, None, None, POSTSTRASSE_TIERS, None, 956, "P4;P6;P7"),
    "wob_congresspark": Priced(WOB, 1.00, 60, None, None, 6.00, 0.0, 24.0, None, None, 767, "P5"),
    "wob_rothenfelder": Priced(WOB, None, None, None, None, 12.00, None, None,
                               "08:00-19:00 1.50/60; 19:00-08:00 1.00/60", None, 470, "P6;P7"),
    "wob_schillerstrasse": Priced(WOB, None, None, None, None, 15.00, 0.0, 24.0, None, None, None, "P4;P5;P8",
                                  bands="0-120 1.00/60; 120- 1.50/60"),
    "wob_city_galerie": Priced(WOB, 1.50, 60, None, None, 15.00, 0.0, 24.0, None, 98.0, 800, "P5"),
    "wob_designer_outlets": Priced(WOB, None, None, None, None, None, 0.0, 24.0, None, 55.0, 1000, "P4;P5;P8",
                                   bands=OUTLETS_BANDS_TEXT),
    "wob_phaeno": Priced(WOB, None, None, 60, 1.10, 7.00, None, None, RATHAUS_TIERS, 60.0, 408, "P4;P6;P7",
                         first_window=(6.0, 24.0)),
    "wf_schulwall": Priced(WF, None, None, None, None, None, None, None,
                           "08:00-10:00 0.30/30; 10:00-18:00 0.60/30; 18:00-22:00 0.30/30; 22:00-08:00 0.10/30", None, 189,
                           "P6"),
    "wf_rosenwall": Priced(WF, None, None, None, None, None, None, None,
                           "08:00-10:00 0.30/30; 10:00-18:00 0.60/30; 18:00-23:00 0.30/30; 23:00-08:00 0.10/30", 95.0, 172,
                           "P6"),
    "gf_hindenburgstrasse": Priced(GF, 1.00, 60, None, None, 13.00, 0.0, 24.0, None, None, 280, "P5"),
    "he_marktpassage": Priced(HE, 0.80, 60, None, None, None, 6.0, 20.0, None, None, 60, "P4"),
    "he_edelhoefe": Priced(HE, 1.20, 30, None, None, 8.00, 0.0, 24.0, None, None, 17, "P4"),
    "he_stobenstrasse": Priced(HE, 0.80, 60, None, None, None, 7.0, 19.0, None, None, 64, "P4"),
    "he_groepern_tiefgarage": Priced(HE, 0.30, 60, 60, 0.70, None, 0.0, 24.0, None, 33.0, 118, "P4;P11"),
    "pe_werderstrasse": Priced(PE, None, None, None, None, None, 0.0, 24.0, None, 48.0, 160, "P4;P5;P8", bands=PEINE_BANDS),
    "pe_wallstrasse": Priced(PE, None, None, None, None, None, 0.0, 24.0, None, 48.0, 190, "P4;P5;P8", bands=PEINE_BANDS),
    "sz_brawo_carree": Priced(SZ, None, None, None, None, None, 0.0, 24.0, None, None, 570, "P5;P8",
                              bands="0-360 1.50/60; 360- total 15.00"),
    "gs_achtermann": Priced(GS, None, None, None, None, 25.00, 0.0, 24.0, None, None, None, "P4;P5;P8;P11",
                            bands=ACHTERMANN_BANDS),
    "gs_ca": Priced(GS, None, None, None, None, 25.00, 0.0, 24.0, None, None, 250, "P4;P5;P8",
                    bands="0-180 1.50/60; 180- 0.80/30"),
    "gs_galeria": Priced(GS, 1.50, 60, None, None, 15.00, 0.0, 24.0, None, 39.0, 193, "P4;P5"),
    "gs_charley_jacob_strasse": Priced(GS, 1.00, 60, None, None, None, 0.0, 24.0, None, None, 58, "P4;P5;P11"),
}
#: The garages whose window of 0-24 h is stated by the source (no assumption P5): the operator page of Contipark and the
#: city brochures (the ordinance of 2018 states opening hours only).
STATED_FULL_DAY_WINDOWS = ("bs_wallstrasse", "he_edelhoefe", "he_groepern_tiefgarage")
#: The garages with a value from secondary evidence (ASSUMPTION P11, spec E13 and ruling R-4b2-9): the tariff of Achtermann and
#: Charley-Jacob-Strasse, the day maximum of the Suedkopf-Center, the monthly product of the Groepern garage.
P11_GARAGES = ("wob_suedkopf", "gs_achtermann", "gs_charley_jacob_strasse", "he_groepern_tiefgarage")
#: Which packages touch which rows (the SHA-256 list of ``package_sha256``): the ten garages of the supplement, five of them also
#: of the follow-up package.
TOUCHED_BY_SUPPLEMENT_ONLY = ("bs_lange_strasse_sued", "bs_steinstrasse", "he_stobenstrasse", "he_groepern_tiefgarage",
                              "wob_poststrasse")
TOUCHED_BY_BOTH = ("bs_forschungsflughafen", "wob_suedkopf", "gs_achtermann", "gs_galeria", "gs_charley_jacob_strasse")
TARIFF_VALUE_COLUMNS = ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_first_period_min",
                        "garage_first_period_eur", "garage_first_period_start_h", "garage_first_period_end_h",
                        "garage_daily_cap_eur", "garage_fee_start_h", "garage_fee_end_h", "tariff_tiers",
                        "tariff_duration_bands")


def _committed_garages() -> pd.DataFrame:
    frame = pg.load_garages(GARAGES_PATH)
    pg.validate_garages(frame)
    return frame.set_index("garage_id")


def _plain(value):
    """A cell as a plain Python value: None for an empty cell, a number for a number."""
    if value is None or value is pd.NA or (isinstance(value, float) and value != value):
        return None
    return value.item() if hasattr(value, "item") else value


def test_the_committed_dataset_lists_exactly_the_garages_the_sources_name_and_prices_every_one():
    garages = _committed_garages()
    assert len(garages) == 35 and len(PRICED_GARAGES) == 35
    # the station car park of DB BahnPark is no garage of the dataset (a QA candidate, ruling R-4b-9)
    assert "wob_hauptbahnhof" not in garages.index
    assert set(garages.index) == set(PRICED_GARAGES)
    assert bool(garages["priced"].all()) and garages["not_priced_reason"].isna().all()  # spec E13: every garage is priced
    assert garages.crs == METRIC_CRS and set(garages.geom_type) == {"Point"}
    assert list(garages.reset_index().columns[:len(pg.DATASET_COLUMNS)]) == list(pg.DATASET_COLUMNS)


@pytest.mark.parametrize("garage_id", sorted(PRICED_GARAGES))
def test_a_committed_priced_garage_carries_the_published_tariff(garage_id):
    expected = PRICED_GARAGES[garage_id]
    row = _committed_garages().loc[garage_id]
    assert row["municipality_ags"] == expected.ags and bool(row["priced"]) and _plain(row["not_priced_reason"]) is None
    assert (_plain(row["garage_hourly_rate_eur"]), _plain(row["garage_billing_unit_min"])) == (expected.rate, expected.unit)
    assert (_plain(row["garage_first_period_min"]), _plain(row["garage_first_period_eur"])) == (
        expected.first_min, expected.first_eur)
    window = (_plain(row["garage_first_period_start_h"]), _plain(row["garage_first_period_end_h"]))
    assert window == (expected.first_window or (None, None))
    assert (_plain(row["garage_daily_cap_eur"]), _plain(row["garage_fee_start_h"]), _plain(row["garage_fee_end_h"])) == (
        expected.cap, expected.start, expected.end)
    assert (_plain(row["tariff_tiers"]), _plain(row["tariff_duration_bands"]), _plain(row["monthly_eur"]),
            _plain(row["capacity_reported"]), _plain(row["assumptions"])) == (
        expected.tiers, expected.bands, expected.monthly, expected.capacity, expected.assumptions)
    parts = expected.assumptions.split(";") if expected.assumptions else []
    # one tariff structure per garage; a first period belongs with its clock window only where its rule states one
    assert expected.tiers is None or expected.bands is None
    assert (expected.first_window is None) or expected.first_min is not None
    # a tiered garage rests on ASSUMPTION P6, has no single window, and its tiers cover the whole day (no free hour)
    assert ("P6" in parts) == (expected.tiers is not None) and ("P8" in parts) == (expected.bands is not None)
    if expected.tiers is not None:
        assert expected.rate is None and expected.unit is None and expected.start is None and expected.end is None
        assert pg.tier_coverage_minutes(pg.parse_tariff_tiers(expected.tiers)) == pg.MINUTES_PER_DAY
    if expected.bands is not None:
        # a banded garage has no rate, unit or first period but its fee window; the text is a valid schedule
        assert expected.rate is None and expected.unit is None and expected.first_min is None
        assert (expected.start, expected.end) == (0.0, 24.0)
        assert pg.format_duration_bands(pg.parse_duration_bands(expected.bands)) == expected.bands
    # P10 is a free first band, P11 a value from secondary evidence (four garages, named below; at Groepern the monthly product)
    assert ("P10" in parts) == (garage_id in ("bs_forschungsflughafen", "wob_suedkopf"))
    assert ("P11" in parts) == (garage_id in P11_GARAGES)
    # the fee window is the garage's own: a window of 0-24 h is stated by the source (no assumption P5) or ASSUMPTION P5
    if (expected.start, expected.end) == (0.0, 24.0):
        assert garage_id in STATED_FULL_DAY_WINDOWS or "P5" in parts
        assert garage_id not in STATED_FULL_DAY_WINDOWS or "P5" not in parts


def test_the_committed_coverage_is_the_one_the_task_reports():
    coverage = pg.coverage(pg.load_garages(GARAGES_PATH))
    assert (coverage["listed"], coverage["priced"], coverage["not_priced"]) == (35, 35, 0)
    assert coverage["not_priced_by_reason"] == {}
    assert coverage["by_municipality"] == {
        BS: {"listed": 12, "priced": 12, "not_priced": 0}, SZ: {"listed": 1, "priced": 1, "not_priced": 0},
        WOB: {"listed": 9, "priced": 9, "not_priced": 0}, GF: {"listed": 1, "priced": 1, "not_priced": 0},
        GS: {"listed": 4, "priced": 4, "not_priced": 0}, HE: {"listed": 4, "priced": 4, "not_priced": 0},
        PE: {"listed": 2, "priced": 2, "not_priced": 0}, WF: {"listed": 2, "priced": 2, "not_priced": 0}}
    # the assumption rates (fallback transparency): no garage rests on P3 any more, P4 22 of 35, P5 23, P6 7, P7 5, P8 10, P10 2
    # and P11 4; the union rates 34 of 35 on at least one assumption and 30 of 35 on P4 or P5 (both above the 75 % warning
    # threshold of the curation step and the loader, which is why the loader warns), 7 garages in the tiered form and 10 in the
    # banded form, 9 monthly products at a garage
    assert coverage["priced_by_assumption"] == {"P10": 2, "P11": 4, "P4": 22, "P5": 23, "P6": 7, "P7": 5, "P8": 10}
    assert coverage["with_monthly_product"] == 9
    assert (coverage["priced_with_assumption"], coverage["priced_with_p4_or_p5"], coverage["priced_tiered"],
            coverage["priced_banded"]) == (34, 30, 7, 10)
    assigned = [values.assumptions.split(";") if values.assumptions else [] for values in PRICED_GARAGES.values()]
    counted = {key: sum(key in parts for parts in assigned) for key in ("P3", "P4", "P5", "P6", "P7", "P8", "P10", "P11")}
    assert {key: count for key, count in counted.items() if count} == coverage["priced_by_assumption"]
    assert counted["P3"] == 0
    assert sum(bool(parts) for parts in assigned) == coverage["priced_with_assumption"]
    assert sum(bool({"P4", "P5"} & set(parts)) for parts in assigned) == coverage["priced_with_p4_or_p5"]
    assert sum(values.tiers is not None for values in PRICED_GARAGES.values()) == coverage["priced_tiered"]
    assert sum(values.bands is not None for values in PRICED_GARAGES.values()) == coverage["priced_banded"]
    assert sum(bool(values.monthly) for values in PRICED_GARAGES.values()) == coverage["with_monthly_product"]


def _committed_price(garage_id: str, duration_min: float) -> float:
    """The price of a stay under the committed bands of a garage and its day cap (the reference evaluation of the loader)."""
    row = _committed_garages().loc[garage_id]
    cap = _plain(row["garage_daily_cap_eur"])
    return pg.duration_band_price_eur(pg.parse_duration_bands(row["tariff_duration_bands"]), duration_min, cap)


@pytest.mark.parametrize("garage_id", ["pe_werderstrasse", "pe_wallstrasse"])
def test_the_peine_schedule_reproduces_the_published_anchors_and_the_band_edges(garage_id):
    # published (Stadtwerke Peine): 0.20 EUR for the first 30 min, 0.80 EUR up to 1 h, then 0.40 EUR per half hour up to 5 h,
    # 4.00 EUR in total for 5 to 24 h
    expected = {1: 0.20, 30: 0.20, 31: 0.80, 60: 0.80, 61: 1.20, 90: 1.20, 91: 1.60, 120: 1.60, 240: 3.20, 270: 3.60,
                299: 4.00, 300: 4.00, 301: 4.00, 1440: 4.00}
    for duration, price in expected.items():
        assert _committed_price(garage_id, duration) == pytest.approx(price, abs=1e-9), (garage_id, duration)
    # the last half-hour increment at the 5 h edge lands exactly on the published 5-to-24 h total: no jump at 300 min
    assert _committed_price(garage_id, 300) == _committed_price(garage_id, 301)
    with pytest.raises(ValueError, match="beyond the last band, which ends at 1440 min"):
        _committed_price(garage_id, 1441)


def test_the_designer_outlets_schedule_reproduces_the_published_anchors_and_the_band_edges():
    # published: free for 20 min, 1.00 EUR up to 2 h, 0.50 EUR per hour up to 4 h, 1.50 EUR per hour up to 7 h, 5.00 EUR per
    # hour from the 7th hour (no day cap)
    expected = {20: 0.0, 21: 1.00, 120: 1.00, 121: 1.50, 180: 1.50, 181: 2.00, 240: 2.00, 241: 3.50, 300: 3.50, 301: 5.00,
                420: 6.50, 421: 11.50, 480: 11.50, 481: 16.50}
    for duration, price in expected.items():
        assert _committed_price("wob_designer_outlets", duration) == pytest.approx(price, abs=1e-9), duration
    assert _plain(_committed_garages().loc["wob_designer_outlets", "garage_daily_cap_eur"]) is None


def test_the_brawo_schedule_jumps_to_the_published_day_total_after_the_sixth_hour():
    # published: 1.50 EUR per started hour for the first 6 h, from the 7th started hour a day total of 15.00 EUR
    for duration, price in {1: 1.50, 60: 1.50, 61: 3.00, 359: 9.00, 360: 9.00, 361: 15.00, 600: 15.00, 1440: 15.00}.items():
        assert _committed_price("sz_brawo_carree", duration) == pytest.approx(price, abs=1e-9), duration


def test_the_goslar_ca_schedule_is_capped_at_the_published_24_hour_price():
    # published (tourism page, not confirmed by the operator): 1.50 EUR per hour for the first 3 h, then 0.80 EUR per half
    # hour, 25.00 EUR for 24 h: 4.50 + 26 * 0.80 = 25.30 at 16 h, so the cap acts from there
    for duration, price in {60: 1.50, 180: 4.50, 181: 5.30, 210: 5.30, 211: 6.10, 930: 24.50, 931: 25.00, 960: 25.00,
                            1440: 25.00}.items():
        assert _committed_price("gs_ca", duration) == pytest.approx(price, abs=1e-9), duration


#: The price points of the directory table of Goslar Achtermann (minutes, EUR) as the follow-up package states them (source F05,
#: parkito.ch, undated): written here by hand, independent of the curation step.
ACHTERMANN_TABLE = ((60, 1.50), (120, 3.00), (180, 4.50), (210, 5.30), (240, 6.10), (270, 6.90), (300, 7.70), (330, 8.50),
                    (360, 9.30), (390, 10.10), (420, 10.90), (450, 11.70), (480, 12.50), (510, 13.30), (540, 14.10),
                    (570, 14.90), (720, 15.00), (1440, 25.00))


@pytest.mark.parametrize("minutes, eur", ACHTERMANN_TABLE)
def test_the_achtermann_schedule_reproduces_every_price_point_of_the_directory_table(minutes, eur):
    assert _committed_price("gs_achtermann", minutes) == pytest.approx(eur, abs=1e-9), minutes


def test_the_achtermann_schedule_between_the_points_the_cap_and_the_further_hour():
    # between the points: 0.80 EUR per started half hour from 3 h to 9.5 h, the 12 h price from 9.5 h, 1.00 EUR per further
    # started hour after 12 h, the 24 h price 25.00 EUR as the cap (15.00 + 12 * 1.00 = 27.00 at 24 h without it)
    for duration, price in {1: 1.50, 181: 5.30, 211: 6.10, 571: 15.00, 719: 15.00, 721: 16.00, 780: 16.00, 781: 17.00,
                            1260: 24.00, 1261: 25.00, 1440: 25.00}.items():
        assert _committed_price("gs_achtermann", duration) == pytest.approx(price, abs=1e-9), duration
    bands = pg.parse_duration_bands(_committed_garages().loc["gs_achtermann", "tariff_duration_bands"])
    assert pg.duration_band_price_eur(bands, 1440) == pytest.approx(27.00, abs=1e-9)


def test_the_forschungsflughafen_and_the_suedkopf_center_bill_from_the_arrival_once_the_grace_period_is_over():
    # ASSUMPTION P10: a stay not longer than the free period costs 0, a longer stay is billed from the arrival (no deduction)
    for duration, price in {1: 0.0, 15: 0.0, 16: 1.50, 60: 1.50, 61: 3.00, 120: 3.00, 121: 4.50, 600: 15.00, 601: 16.50,
                            661: 18.00, 1440: 18.00}.items():
        assert _committed_price("bs_forschungsflughafen", duration) == pytest.approx(price, abs=1e-9), duration
    for duration, price in {1: 0.0, 30: 0.0, 31: 1.00, 60: 1.00, 61: 2.00, 240: 4.00, 300: 5.00, 301: 5.00, 1440: 5.00}.items():
        assert _committed_price("wob_suedkopf", duration) == pytest.approx(price, abs=1e-9), duration


def test_the_lange_strasse_sued_first_period_and_rate_give_the_prices_of_the_published_bands():
    # published: the first 60 min 1.00 EUR in total, then 0.50 EUR per 30 min; the first period plus rate form of the dataset
    # must give exactly the prices of the bands '0-60 total 1.00; 60- 0.50/30'
    row = _committed_garages().loc["bs_lange_strasse_sued"]
    assert (row["garage_first_period_min"], row["garage_first_period_eur"], row["garage_billing_unit_min"]) == (60, 1.0, 30)
    bands = pg.parse_duration_bands("0-60 total 1.00; 60- 0.50/30")
    rate_per_unit = row["garage_hourly_rate_eur"] * row["garage_billing_unit_min"] / 60.0
    for duration in range(1, 1441):
        form = row["garage_first_period_eur"] + (math.ceil((duration - 60) / 30) * rate_per_unit if duration > 60 else 0.0)
        assert min(form, row["garage_daily_cap_eur"]) == pytest.approx(
            pg.duration_band_price_eur(bands, duration, row["garage_daily_cap_eur"]), abs=1e-9), duration


def test_the_braunschweig_ring_center_and_the_schillerstrasse_schedules_reproduce_the_published_hours():
    # Ring-Center: 1.50 EUR for each of the first two started hours, then 2.00 EUR per started hour, day rate 15.00 EUR
    for duration, price in {60: 1.50, 61: 3.00, 120: 3.00, 121: 5.00, 180: 5.00, 181: 7.00, 420: 13.00, 421: 15.00, 480: 15.00,
                            481: 15.00, 1440: 15.00}.items():
        assert _committed_price("bs_ring_center", duration) == pytest.approx(price, abs=1e-9), duration
    # Wolfsburg Schillerstrasse: 1.00 EUR per started hour for the first 2 h, then 1.50 EUR per hour, day maximum 15.00 EUR
    for duration, price in {60: 1.00, 120: 2.00, 121: 3.50, 181: 5.00, 600: 14.00, 601: 15.00, 1440: 15.00}.items():
        assert _committed_price("wob_schillerstrasse", duration) == pytest.approx(price, abs=1e-9), duration


def test_the_ring_center_identity_check_is_recorded_and_the_forschungsflughafen_is_matched_by_its_legacy_key():
    garages = _committed_garages()
    ring = garages.loc["bs_ring_center"]
    prefix = "26057844400587384de83e9d214300551ccc28"
    assert ring["tariff_rule_ids"] == f"{prefix}-19;{prefix}-20;{prefix}-21" and ring["package_facility_id"] == "BS_None"
    for phrase in ("IDENTITY CHECKED", "NAMED 'Parkhaus Forschungsflughafen'", "mislabel", "Parkhaus Ring-Center",
                   "'1. und 2. angef. Std.: 1,50 EUR'", "BS_SOURCE_4781292017fb49b091cc3066a17483fafbca28"):
        assert phrase in ring["notes"], phrase
    # the Forschungsflughafen keeps its own rules: the free 15 min (-5) and the supplement's rate and cap, never the Ring-Center's
    airport = garages.loc["bs_forschungsflughafen"]
    assert airport["package_facility_id"] == "BS_SOURCE_4781292017fb49b091cc3066a17483fafbca28"  # the legacy key, never BS_None
    assert airport["tariff_rule_ids"] == ("4781292017fb49b091cc3066a17483fafbca28-5;BS_FORSCHUNGSFLUGHAFEN_REGULAR;"
                                          "BS_FORSCHUNGSFLUGHAFEN_REGULAR:cap")
    assert prefix not in airport["tariff_rule_ids"] and "BS_None" not in airport["package_facility_id"]


def test_the_retailer_conditions_of_brawo_are_named_and_not_used_and_the_ordinance_is_cited_as_opening_hours_only():
    garages = _committed_garages()
    brawo = garages.loc["sz_brawo_carree", "notes"]
    for rule in ("SZ_BRAWO_R03_178", "SZ_BRAWO_R04_179"):
        assert rule in brawo
    assert "customer condition: free for 90 min for customers of Kaufland or dm with a retailer validation" in brawo
    assert "customer condition: free for 60 min for customers of MediaMarkt or ABC-Schuhcenter" in brawo
    assert _plain(garages.loc["sz_brawo_carree", "tariff_rule_ids"]) == "SZ_BRAWO_R01_176;SZ_BRAWO_R02_177"
    assert "Reading: the day boundary of the cap SZ_BRAWO_R02_177 is not stated" in brawo
    edelhoefe = garages.loc["he_edelhoefe", "notes"]
    assert "'Oeffnungszeit: 24 Stunden' in its column of fee-liable times" in edelhoefe
    assert "states only the opening hours of the garage (geoeffnet 00.00 bis 24.00), which are no charging hours" in edelhoefe
    assert "opens the garage from 00.00 to 24.00" not in edelhoefe


def test_exactly_three_garages_tie_their_first_period_to_a_clock_window():
    garages = _committed_garages()
    with_window = garages[garages["garage_first_period_start_h"].notna()]
    assert sorted(with_window.index) == ["bs_eiermarkt", "wob_phaeno", "wob_rathaus"]
    windows = {garage: (_plain(row["garage_first_period_start_h"]), _plain(row["garage_first_period_end_h"]))
               for garage, row in with_window.iterrows()}
    # Eiermarkt rule -35 states 07:00-18:00, the Rathaus and Phaeno first-hour rules 06:00-00:00 (read as 6 to 24 h)
    assert windows == {"bs_eiermarkt": (7.0, 18.0), "wob_rathaus": (6.0, 24.0), "wob_phaeno": (6.0, 24.0)}
    # P6 as amended: the first period is charged once when the arrival lies inside its window, the tiers then run on
    for garage in windows:
        assert "an arrival outside the window pays the tiers from the arrival" in garages.loc[garage, "notes"]
    # the two first periods of the supplement (Lange Strasse Sued, Groepern) are tied to no window
    assert sorted(garages.index[garages["garage_first_period_min"].notna()]) == [
        "bs_eiermarkt", "bs_lange_strasse_sued", "he_groepern_tiefgarage", "wob_phaeno", "wob_rathaus"]


def test_no_garage_is_left_unpriced_the_reason_vocabulary_stays_and_the_forms_are_counted():
    # the reasons stay in the vocabulary of the loader and the QA table (a later garage may need one); none is used now
    assert set(pg.NOT_PRICED_REASONS) == {"no_published_tariff", "free_period", "incomplete_tariff", "conflicting_sources"}
    garages = _committed_garages()
    assert bool(garages["priced"].all())
    assert garages["tariff_duration_bands"].notna().sum() == 10 and garages["tariff_tiers"].notna().sum() == 7


def test_the_four_garages_without_coordinates_are_listed_at_the_main_points_of_the_supplement():
    garages = _committed_garages().to_crs("EPSG:4326")
    # entrances.geojson of the supplement (longitude, latitude): two OSM-mapped entrances in Braunschweig, the mapped entrance of
    # the Groepern garage and the DERIVED access point of the Stobenstrasse deck (not a confirmed barrier point)
    expected = {"bs_lange_strasse_sued": (10.518131, 52.2661908, "osm_mapped_parking_entrance", "310231874"),
                "bs_steinstrasse": (10.5173585, 52.2611457, "osm_mapped_parking_entrance", "443311265"),
                "he_stobenstrasse": (11.0075514, 52.2299095, "derived_access_point_on_osm_node", "3001883721"),
                "he_groepern_tiefgarage": (11.0063757, 52.2297395, "osm_mapped_parking_entrance", "771725640")}
    for garage, (longitude, latitude, method, node) in expected.items():
        row = garages.loc[garage]
        assert (round(row.geometry.x, 7), round(row.geometry.y, 7)) == (longitude, latitude), garage
        assert row["geometry_method"] == method and row["geometry_source_url"] == f"https://www.openstreetmap.org/node/{node}"
    # the alternatives (reference points) never became garages: the dataset has the four main points only
    assert garages.loc[list(expected), "geometry"].map(lambda point: point.geom_type).eq("Point").all()
    assert (garages.loc["he_stobenstrasse", "capacity_reported"], garages.loc["he_groepern_tiefgarage", "capacity_reported"]) == (
        64, 118)
    assert garages.loc["he_groepern_tiefgarage", "package_facility_id"] == "HE_GROEPERN_TG_118"  # never HE_GROEPERN_STRASSE
    assert "derived access point" in garages.loc["he_stobenstrasse", "notes"]
    assert "no confirmed barrier or portal point" in garages.loc["he_stobenstrasse", "notes"]
    assert "OSM-mapped entrance (node 310231874" in garages.loc["bs_lange_strasse_sued", "notes"]
    assert garages.loc["bs_steinstrasse", "operator"] == "AGR Parking UG"
    assert "Betreiber: AGR Parking UG" in garages.loc["bs_steinstrasse", "notes"]
    brochure = garages.loc["he_groepern_tiefgarage", "notes"]
    assert "Oeffnungszeit: 24 Stunden" in brochure and "no unit for 'jede weitere 0,30'" in brochure


def test_the_groepern_notes_name_the_directory_the_conflicting_variant_and_the_decision_on_its_monthly_product():
    row = _committed_garages().loc["he_groepern_tiefgarage"]
    notes = row["notes"]
    # the unit of "jede weitere" rests on the undated directory, the brochure (official, dated) is the encoded tariff
    for phrase in ("HE_PARKINGLIST", "0.30 Euro / jede weitere Stunde", "so the unit is one hour", "CONFLICTING variant",
                   "1.20 EUR for 2 hours against the 1.00 EUR that the encoded brochure tariff gives", "outranks the undated",
                   "HE_GROEPERN_TG_118_DIRECTORY_TWO_HOURS", "ASSUMPTION P11", "the monthly product only", "outdated"):
        assert phrase in notes, phrase
    # the encoded tariff is the brochure's: 0.70 EUR for the first hour, 0.30 EUR per further started hour, so 1.00 EUR at 2 h
    first, rate = row["garage_first_period_eur"], row["garage_hourly_rate_eur"]
    assert (first, rate, row["garage_first_period_min"], row["garage_billing_unit_min"]) == (0.70, 0.30, 60, 60)
    assert first + math.ceil((120 - 60) / row["garage_billing_unit_min"]) * rate == pytest.approx(1.00)
    assert row["tariff_rule_ids"] == "HE_GROEPERN_TG_118_BROCHURE_FIRST;HE_GROEPERN_TG_118_BROCHURE_NEXT"
    # the directory's monthly product (operator Stadt Helmstedt, undated) is used: the only product of the garage
    assert (row["monthly_eur"], row["monthly_source_url"]) == (
        33.0, "https://www.parkinglist.de/parkplatz/helmstedt/tiefgarage-groepern-helmstedt-8251")
    assert "Betreiber: Stadt Helmstedt" in row["monthly_product"] and "undated" in row["monthly_product"]


def test_charley_jacob_strasse_keeps_the_archived_municipal_point_and_names_it():
    row = _committed_garages().to_crs("EPSG:4326").loc["gs_charley_jacob_strasse"]
    # the archived municipal point of 2018 (the follow-up package states it as 10.43004320553744, 51.90652479995978)
    assert (round(row.geometry.x, 7), round(row.geometry.y, 7)) == (10.4300432, 51.9065248)
    assert row["geometry_method"] == "archived_municipal_point_2018"
    assert (row["capacity_reported"], row["capacity_scope"]) == (58, "secondary_directory_total")
    for phrase in ("budget of 2026", "546-01", "outside core working hours", "not modelled", "no day cap", "ASSUMPTION P11",
                   "overridden by the owner (ruling R-4b2-8, spec E13)"):
        assert phrase in row["notes"], phrase
    assert pd.isna(row["garage_daily_cap_eur"]) and row["operator"] is None


def test_charley_jacob_strasse_states_the_p11_consistency_check_against_the_other_goslar_garages():
    garages = _committed_garages()
    notes = garages.loc["gs_charley_jacob_strasse", "notes"]
    for phrase in ("Consistency check against the garages of the same town", "BELOW the 1.50 EUR per hour of Parkhaus bei C&A, "
                   "GALERIA and Achtermann", "municipal office garage", "a lower municipal tariff is plausible",
                   "result of the check: kept"):
        assert phrase in notes, phrase
    # the figures of the statement are the committed ones: 1.00 EUR per hour here, 1.50 EUR for the first hour at the three others
    assert garages.loc["gs_charley_jacob_strasse", "garage_hourly_rate_eur"] == 1.00
    assert garages.loc["gs_galeria", "garage_hourly_rate_eur"] == 1.50
    for garage in ("gs_ca", "gs_achtermann"):
        assert _committed_price(garage, 60) == pytest.approx(1.50, abs=1e-9), garage
    assert garages.loc["gs_charley_jacob_strasse", "municipality_ags"] == garages.loc["gs_ca", "municipality_ags"] == GS


def test_achtermann_leaves_the_operator_empty_and_names_the_candidate_the_hotel_price_and_the_consistency_with_the_ca():
    row = _committed_garages().loc["gs_achtermann"]
    assert row["operator"] is None
    for phrase in ("Tessner Verwaltungs GmbH", "hotel", "C&A", "ASSUMPTION P11", "undated", "overridden by the owner",
                   "EVERY price point"):
        assert phrase in row["notes"], phrase


def test_the_galeria_cap_variants_the_period_reading_and_the_monthly_decision_are_named():
    row = _committed_garages().loc["gs_galeria"]
    notes = row["notes"]
    for phrase in ("GS_GALERIA_DIRECT_OPERATOR", "8.00 EUR", "CONFLICTING variant", "15.00 EUR per calendar day",
                   "stated per calendar day", "a maximum per stay", "publicly purchasable", "Dauerstellplatz"):
        assert phrase in notes, phrase
    assert (row["monthly_eur"], row["garage_daily_cap_eur"]) == (39.0, 15.0)


def test_the_poststrasse_night_tier_names_both_unit_sources_and_the_cap_recheck():
    notes = _committed_garages().loc["wob_poststrasse", "notes"]
    for phrase in ("sabaparking.com, which states '1 EUR' without a unit", "city page wolfsburg.de only", "ASSUMPTION P4",
                   "ASSUMPTION P7", "WOB_POST_R04_151", "10.00 EUR", "WOB_POST_R03_150", "states no charging times; it applies "
                   "at every time of day that the other tiers do not cover (06:30-21:00)"):
        assert phrase in notes, phrase
    assert "ASSUMPTION P3" not in notes


def test_the_forschungsflughafen_notes_name_the_visible_block_the_hidden_templates_and_the_week_product():
    notes = _committed_garages().loc["bs_forschungsflughafen", "notes"]
    for phrase in ("18.00 EUR", "CSS-hidden", "15.00 EUR", "99.00 EUR", "ASSUMPTION P10", "mh-parkservice.com/faq-wissen",
                   "no statement of this site", "open_with_general_operator_indication", "reports the capacity 650"):
        assert phrase in notes, phrase


def test_every_committed_garage_cites_its_packages_and_a_source_and_every_assumption_is_named_in_its_notes():
    garages = _committed_garages()
    regional = REGIONAL_PACKAGE_SHA256
    both, three = f"{regional};{SUPPLEMENT_PACKAGE_SHA256}", f"{regional};{SUPPLEMENT_PACKAGE_SHA256};{FOLLOWUP_PACKAGE_SHA256}"
    assert set(garages["package_sha256"]) == {regional, both, three}
    assert sorted(garages.index[garages["package_sha256"] == both]) == sorted(TOUCHED_BY_SUPPLEMENT_ONLY)
    assert sorted(garages.index[garages["package_sha256"] == three]) == sorted(TOUCHED_BY_BOTH)
    assert (garages["package_sha256"] == regional).sum() == 35 - 10  # the other 25 garages cite the regional package only
    for column in ("source_url", "geometry_source_url"):
        assert garages[column].str.startswith("https://").all(), column
    assert garages["source_date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all() and garages["geometry_method"].notna().all()
    for garage_id, row in garages.iterrows():
        for assumption in (_plain(row["assumptions"]) or "").split(";"):
            if assumption:
                assert f"ASSUMPTION {assumption}" in row["notes"], garage_id
        assert _plain(row["tariff_rule_ids"]), garage_id
        # derived from the package flag of every rule that sets a value (ruling R-4b-8): a rule that is not preferred for
        # current use would be listed with its flag instead; the supplement, follow-up and brochure rules name the owner
        # decision or the quotation that releases them
        notes = row["notes"]
        assert ("(all marked preferred_for_current_use in the package)" in notes
                or "each is used as the owner decision that released it states" in notes
                or "quotations of the city brochure text, checked against it" in notes), garage_id
        # a night tariff that is no per-unit rate is stated and not charged (ASSUMPTION P3): no garage rests on it any more
        assert "P3" not in (_plain(row["assumptions"]) or "").split(";"), garage_id
    # the tariff of the Braunschweig garage Eiermarkt, quoted: first hour 0.60, then 07:00-18:00 1.20 and 18:00-07:00 1.00 per
    # hour, 9.60 a day; the night cap and the 24-hour maximum are named and not applied (ASSUMPTION P7)
    eiermarkt = garages.loc["bs_eiermarkt", "notes"]
    assert ("0.60 EUR for the first 60 min (arrival 07:00-18:00), then tiers per started 60 min, the tier in force at the "
            "unit's start: 07:00-18:00 1.20 EUR, 18:00-07:00 1.00 EUR, at most 9.60 EUR per day.") in eiermarkt
    assert "15.60 EUR at most (24_hours)" in eiermarkt and "6.00 EUR at most (night_18_07) Mo-Su 18:00-07:00" in eiermarkt
    # the operators that the page of the source names (never taken from a page that does not name them)
    assert {garage: garages.loc[garage, "operator"] for garage in ("bs_eiermarkt", "bs_wallstrasse", "bs_magni", "bs_packhof")} \
        == {"bs_eiermarkt": "Contipark", "bs_wallstrasse": "Contipark", "bs_magni": "Park und Tank",
            "bs_packhof": "Park und Tank"}
    # the two stated 24 h windows quote their sources; the garages that rest on P4 cite the package's caution and its census
    assert "Charging times stated by the source:" in garages.loc["he_edelhoefe", "notes"]
    assert "Oeffnungszeit: 24 Stunden" in garages.loc["he_edelhoefe", "notes"]
    assert "25 of 54 state their rounding, and 25 of those 25 say started unit" in garages.loc["he_edelhoefe", "notes"]
    assert "Ein angegebener Stundensatz beweist noch keine Aufrundung je Stunde" in garages.loc["he_edelhoefe", "notes"]
    # the tiered Wolfenbuettel garages name ASSUMPTION P6
    for garage in ("wf_schulwall", "wf_rosenwall"):
        assert "ASSUMPTION P6: units are counted from arrival and each started unit costs the rate of the tier in force at " \
               "the unit's start" in garages.loc[garage, "notes"]


def test_the_opening_hours_in_the_notes_do_not_run_a_day_name_into_a_clock_time():
    # the opening hours of the package in the P5 note (the monthly product texts are verbatim quotes and are not touched)
    stated = 0
    for garage_id, notes in _committed_garages()["notes"].items():
        for hours in re.findall(r"no charging hours: ([^)]*)\)", notes):
            stated += 1
            assert not re.search(r"[A-Za-z]\d{1,2}:\d{2}", hours), (garage_id, hours)
    assert stated >= 10  # the garages without stated charging times that state opening hours
    # the opening hours of the Suedkopf-Center, as the package writes them without a space and the notes show them
    assert "Mo-Fr 06:00-22:00; Sa 06:00-21:00; Su/holidays closed" in _committed_garages().loc["wob_suedkopf", "notes"]


def test_the_committed_files_are_ascii_documented_and_every_row_carries_a_tariff():
    for path in (GARAGES_PATH, GARAGES_QA_PATH):
        assert path.read_bytes().isascii(), path.name
    document = json.loads(GARAGES_PATH.read_text(encoding="utf-8"))
    assert document["type"] == "FeatureCollection" and len(document["features"]) == 35
    assert "ODbL 1.0" in document["license"] and "OpenStreetMap" in document["attribution"]
    assert "Parkhaus_Ergaenzungen_2026-10-07.zip" in document["attribution"] and "Parkhaus_Nachrecherche_2026-10-07.zip" in (
        document["attribution"])
    assert set(document["documentation"]["columns"]) == set(pg.DATASET_COLUMNS)
    for feature in document["features"]:
        assert feature["geometry"]["type"] == "Point" and list(feature["properties"]) == list(pg.DATASET_COLUMNS)
    header = [line for line in GARAGES_QA_PATH.read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    for column in pq.GARAGE_QA_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column


def test_the_committed_qa_table_accounts_for_every_garage_product_and_candidate():
    garages = pg.load_garages(GARAGES_PATH)
    qa = pq.load_garage_qa(GARAGES_QA_PATH)
    pq.validate_garage_qa(qa, garages, pz.load_tariffs(TARIFFS_PATH))
    assert qa["record_type"].value_counts().to_dict() == {"garage": 35, "monthly_product": 23, "candidate": 15}
    assert len(qa) == 73
    coverage = pq.qa_coverage(qa)
    # 23 monthly or 30-day products: 11 used (nine at a garage, the zone Ib ticket and the TU member ticket), 12 recorded and
    # not used with a reason (spec Amendment D2, ruling R-D2-a); the product of the station BahnPark is not used because that
    # garage is no garage of the dataset (garage_not_listed); the 99.00 EUR seven-day product of the Forschungsflughafen is a
    # second product of another duration (not_monthly_or_30_day)
    assert (coverage["monthly_used"], coverage["monthly_not_used"]) == (11, 12)
    assert coverage["monthly_not_used_by_reason"] == {
        "capacity_limited_permits": 1, "garage_not_listed": 1, "no_coordinates": 1, "no_fixed_price": 1,
        "not_monthly_or_30_day": 2, "not_the_cheapest": 3, "outdated_source": 2, "restricted_customer_group": 1}
    # the candidates that are no garage of the dataset (counts of package or directory entries, with the reason): the four
    # garages without coordinates are garages now, only the Clausthal-Zellerfeld garage has none
    assert coverage["candidates"] == 38 and coverage["candidates_by_reason"] == {
        "bga_zone": 2, "customer_regime": 1, "dauerparker_only": 2, "no_coordinates": 1, "no_published_tariff": 24,
        "outside_source_list": 1, "station_bahnpark": 3, "zone_street_product": 4}
    rows = qa.set_index("record_id")
    # the station car park of DB BahnPark (R-4b-9) is a candidate with its monthly product recorded and not used
    assert rows.loc["candidate_wob_hauptbahnhof", "reason_code"] == "station_bahnpark"
    assert rows.loc["monthly_wob_hauptbahnhof_24h", "reason_code"] == "garage_not_listed"
    assert rows.loc["monthly_wob_hauptbahnhof_24h", "garage_id"] == ""
    # the two lots of Wolfenbuettel with long-term renters only, and the one garage without coordinates left (Clausthal)
    for record in ("candidate_wf_parkpalette_karlstrasse", "candidate_wf_neue_strasse"):
        assert rows.loc[record, "reason_code"] == "dauerparker_only" and rows.loc[record, "municipality_ags"] == WF
    assert rows.loc["candidate_cl_tiefgarage_rathaus", "reason_code"] == "no_coordinates"
    for record in ("candidate_bs_lange_strasse_sued", "candidate_bs_steinstrasse", "candidate_he_stobenstrasse",
                   "candidate_he_groepern_tiefgarage"):
        assert record not in rows.index, record
    # the airport's seven-day product and the Galeria monthly product
    week = rows.loc["monthly_bs_forschungsflughafen_7_day"]
    assert (week["decision"], week["reason_code"], week["amount_eur"], week["garage_id"]) == (
        "not_used", "not_monthly_or_30_day", "99.00", "bs_forschungsflughafen")
    galeria = rows.loc["monthly_gs_galeria_dauerstellplatz"]
    assert (galeria["decision"], galeria["amount_eur"], galeria["garage_id"]) == ("used", "39.00", "gs_galeria")
    # the monthly product of the Groepern garage (ruling R-4b2-9): used under ASSUMPTION P11, the decision and its basis stated
    groepern = rows.loc["monthly_he_groepern_tiefgarage"]
    assert (groepern["decision"], groepern["amount_eur"], groepern["garage_id"], groepern["evidence"]) == (
        "used", "33.00", "he_groepern_tiefgarage", "HE_GROEPERN_TG_118_DIRECTORY_MONTHLY")
    for phrase in ("HE_PARKINGLIST", "Betreiber: Stadt Helmstedt", "ruling R-4b2-9", "ASSUMPTION P11", "may be outdated"):
        assert phrase in groepern["note"], phrase
    assert "1.20 EUR at 2 h" in rows.loc["garage_he_groepern_tiefgarage", "note"]
    assert "8.00" in rows.loc["garage_gs_galeria", "note"] and "conflicting variant" in rows.loc["garage_gs_galeria", "note"]
    used = qa[(qa["record_type"] == "monthly_product") & (qa["decision"] == "used")]
    assert sorted(used.loc[used["garage_id"] != "", "garage_id"]) == sorted(
        garage_id for garage_id, values in PRICED_GARAGES.items() if values.monthly)
    zone_products = used[used["zone_ids"] != ""]
    assert set(";".join(zone_products["zone_ids"]).split(";")) == {
        "bs_zone_ib", "tu_zentralcampus", "tu_campus_nord", "tu_campus_ost_beethovenstrasse", "tu_campus_ost_langer_kamp",
        "tu_forschungsflughafen", "tu_campus_volkmaroder_strasse"}


def test_the_committed_files_are_reproduced_from_the_local_packages(garages_step, tmp_path):
    step = garages_step  # the real specifications, not the synthetic ones of the step fixture
    raw_sources = COMMITTED_PARKING_DIR / "raw_sources"
    regional, directory = raw_sources / "municipal_2026-10-07", raw_sources / "bs_plan_parkplaetze.geojson"
    supplement = regional / step.sup.SUPPLEMENT_FILE
    followup = regional / step.sup.FOLLOWUP_FILE
    if not all(path.is_file() for path in (regional / f"{PACKAGE}.zip", directory, supplement, followup)):
        pytest.skip("the owner's packages and the city car-park directory are gitignored and absent here")
    inputs = step.load_garage_inputs(regional, supplement_path=supplement, followup_path=followup)
    frame = step.build_garages(inputs)
    rows = step.qa_rows(inputs, frame, step.load_directory(directory))
    pg.write_garages(frame, tmp_path / "garages.geojson", members=step.dataset_members())
    step.write_garage_qa(tmp_path / "qa.csv", rows)
    # a checkout with core.autocrlf holds CRLF; the repository holds LF
    assert (tmp_path / "garages.geojson").read_bytes() == GARAGES_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert (tmp_path / "qa.csv").read_bytes() == GARAGES_QA_PATH.read_bytes().replace(b"\r\n", b"\n")
