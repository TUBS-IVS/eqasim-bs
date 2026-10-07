"""Garage dataset of the regional evidence package of 2026-10-07 (parking cost zones v2, spec Amendment E1, issue #436).

The owner's package is gitignored, so the curation step ``regional_garages.py`` is pinned on a synthetic package written the
way the owner supplied it (a zip holding ``<name>/daten/Regional_Parkdaten.gpkg`` in EPSG:25832, ``facilities.json``,
``tariff_rules.json`` and ``sources.json``) with small garage specifications of its own: the roles of the rules (a first
period, a rate, a day cap, the fee window), the assumptions P3 to P5 and their counts, a garage that cannot be priced, the
twin of a garage in another layer, the monthly products and the candidates that are no garage. The committed dataset is pinned
independently at the end (``COMMITTED_GARAGES``: the values as read from the sources, not from the code) and reproduced from
the local package where it exists.
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
          source="https://op.example/tariff", retrieved="2026-10-07", product=None) -> dict:
    return {"rule_id": rule_id, "facility_id": facility, "rule_type": rule_type, "amount_eur": amount,
            "billing_unit_minutes": unit, "rounding": rounding, "elapsed_from_minutes": start, "elapsed_to_minutes": end,
            "max_stay_minutes": None, "daily_cap_eur": cap, "cap_period": period,
            "charging_times": times or {day: None for day in ("monday", "tuesday", "wednesday", "thursday", "friday",
                                                               "saturday", "sunday", "public_holidays")},
            "monthly_price_eur": monthly, "minimum_contract_months": contract, "conditions": conditions,
            "source_url": source, "retrieved_at": retrieved, "status": status, "preferred_for_current_use": True,
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
        # WOB_X: a day tariff whose window is the complement of the stated night window
        _rule("x-1", "increment", "WOB_X", 2.00, unit=60, rounding="started_unit"),
        _rule("x-2", "published_day_tariff", "WOB_X", 9.0, period="day_definition_unspecified"),
        _rule("x-3", "unresolved_night_tariff", "WOB_X", 1.0, window={"from": "21:00", "to": "06:30", "days_raw": None}),
        # GS_A: a rate that changes after 3 h
        _rule("g-1", "increment", "GS_A", 1.50, unit=60, start=0, end=180, period="elapsed_first_3_hours"),
        _rule("g-2", "increment", "GS_A", 0.80, unit=30, start=180),
        # GS_B: one rate, all day, in a window that is stated as a time window with the days of the model's weekday
        _rule("h-1", "increment", "GS_B", 1.00, unit=60, rounding="started_unit",
              window={"from": "08:00", "to": "00:00", "days_raw": "Mo-Sa_except_public_holidays"}),
        _rule("h-2", "cap", "GS_B", 8.0, cap=8.0),
    ]


def _facilities() -> list:
    def record(facility_id, name, rules, capacity=None, scope=None, observations=None, attributes=None):
        return {"facility_id": facility_id, "name": name, "tariff_rule_ids": rules, "capacity": capacity,
                "capacity_scope": scope, "capacity_observations": observations or [], "attributes": attributes or {}}

    return [
        record("BS_A", "Parkhaus A", ["a-1", "a-2", "a-3", "a-4", "a-5", "a-m1", "a-m2"], 500, None,
               [{"value": 500, "scope": "reported_in_PULP", "source_url": PULP},
                {"value": 677, "scope": "operator_total", "source_url": "https://op.example/a"}]),
        record("BS_B", "Parkhaus B", ["b-1", "b-2", "b-3", "b-4"]),
        record("WOB_X", "Parkhaus X", ["x-1", "x-2", "x-3"], 956, "total", attributes={"operator": "Saba"}),
        record("GS_A", "Parkhaus Ga", ["g-1", "g-2"]),
        record("GS_B", "Parkhaus Gb", ["h-1", "h-2"], 250, "secondary_directory_total"),
        record("GOS_POINT_4", "Parkhaus Charley", []),
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
         "xy": (500, 100), **base}])
    wob_base = {"geometry_source_url": GEOVIEWER, "geometry_source_id": "src_wob", "source_url": GEOVIEWER,
                "retrieved_at_utc": "2026-10-07T05:24:49+00:00"}
    wob = _point_layer([{"facility_id": "WOB_X", "name": "Parkhaus X", "xy": (10_000, 100), **wob_base}])
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
         "window": ("night_complement", "x-3"), "other_tiers": ("x-3",), "comment": ""},
        {"garage_id": "gs_a", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_A"),
         "facility": "GS_A", "reason": "banded_tariff", "reason_text": "the rate changes after 3 h",
         "evidence": ("g-1", "g-2"), "comment": ""},
        {"garage_id": "gs_b", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_B"),
         "facility": "GS_B", "rate": "h-1", "cap": "h-2", "window": "rate", "comment": ""},
        {"garage_id": "gs_c", "town": "gs", "layer": "gos_parking_locations", "feature": ("facility_id", "GOS_POINT_4"),
         "facility": "GOS_POINT_4", "reason": "no_published_tariff", "reason_text": "no rule", "evidence": (),
         "comment": ""},
    )


MONTHLY = (
    {"record_id": "monthly_bs_a_tag", "rule": "a-m1", "garage_id": "bs_a", "decision": "used"},
    {"record_id": "monthly_bs_a_tag_nacht", "rule": "a-m2", "garage_id": "bs_a", "decision": "not_used",
     "reason": "not_the_cheapest"},
)
CANDIDATES = (("candidate_bs_additional", "bs", "Parkhaus Lange Strasse Sued", "BS_ADDITIONAL_1", "no_coordinates",
               "no coordinates in the package"),)
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
                                 "garage_daily_cap_eur": 9.6, "garage_fee_start_h": 7.0, "garage_fee_end_h": 18.0}
    assert encoded["assumptions"] == ["P3"] and encoded["rounding_stated"] and encoded["window_stated"]
    assert encoded["rule_ids"] == ["a-1", "a-2", "a-3"]
    assert encoded["sentence"] == ("0.60 EUR for the first 60 min, then 1.20 EUR per started 60 min, at most 9.60 EUR per "
                                   "day, charged 07:00-18:00")


def test_a_first_period_equal_to_one_billing_unit_is_the_rate_alone(step, inputs):
    encoded = step.encode_tariff(_specs()[1], inputs["rules"])
    assert encoded["values"]["garage_hourly_rate_eur"] == 1.5 and encoded["values"]["garage_first_period_min"] is None
    assert encoded["values"]["garage_first_period_eur"] is None and encoded["values"]["garage_daily_cap_eur"] == 12.0
    # no rounding stated and no charging times: the assumptions P4 and P5, never a silent default
    assert encoded["assumptions"] == ["P4", "P5"] and not encoded["rounding_stated"] and not encoded["window_stated"]
    assert (encoded["values"]["garage_fee_start_h"], encoded["values"]["garage_fee_end_h"]) == (0.0, 24.0)


def test_the_day_window_can_be_the_complement_of_a_stated_night_window(step, inputs):
    encoded = step.encode_tariff(_specs()[2], inputs["rules"])
    assert (encoded["values"]["garage_fee_start_h"], encoded["values"]["garage_fee_end_h"]) == (6.5, 21.0)
    assert encoded["values"]["garage_daily_cap_eur"] == 9.0 and "P3" in encoded["assumptions"]
    assert encoded["rule_ids"] == ["x-1", "x-2", "x-3"]


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


def test_the_night_complement_needs_a_window_that_crosses_midnight(step):
    assert step.night_complement(_rule("n", "x", "F", window={"from": "21:00", "to": "06:30", "days_raw": None})) == (6.5, 21.0)
    with pytest.raises(SystemExit, match="does not cross midnight"):
        step.night_complement(_rule("n", "x", "F", window={"from": "06:00", "to": "21:00", "days_raw": None}))
    with pytest.raises(SystemExit, match="no clock window"):
        step.night_complement(_rule("n", "x", "F"))


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


# --------------------------------------------------------------------------- the dataset


def test_the_dataset_rows_carry_the_values_the_provenance_and_the_assumptions(step, inputs):
    frame = step.build_garages(inputs)
    pg.validate_garages(frame)
    assert list(frame["garage_id"]) == ["bs_a", "bs_b", "wob_x", "gs_a", "gs_b", "gs_c"]
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
    assert "0.60 EUR for the first 60 min, then 1.20 EUR per started 60 min" in notes and "ASSUMPTION P3" in notes
    assert "a-4: 1.00 EUR per started 60 min Mo-Su 18:00-07:00" in notes and "operator_total 677" in notes
    assert "Monthly product a-m1" in notes
    bs_b = rows.loc["bs_b"]
    assert bs_b["assumptions"] == "P4;P5" and pd.isna(bs_b["garage_first_period_min"]) and pd.isna(bs_b["monthly_eur"])
    assert "ASSUMPTION P4" in bs_b["notes"] and "ASSUMPTION P5" in bs_b["notes"]
    assert "b-4: lost ticket 10.00 EUR (a lost ticket)" in bs_b["notes"]
    wob = rows.loc["wob_x"]
    assert wob["operator"] == "Saba" and wob["geometry_method"] == "municipal_point" and wob["geometry_source_url"] == GEOVIEWER
    assert wob["source_date"] == "2026-10-07"
    gs_a = rows.loc["gs_a"]
    assert not gs_a["priced"] and gs_a["not_priced_reason"] == "banded_tariff" and gs_a["tariff_rule_ids"] == "g-1;g-2"
    assert gs_a["garage_hourly_rate_eur"] != gs_a["garage_hourly_rate_eur"]  # NaN: no value on an unpriced garage
    assert "Not priced (banded_tariff): the rate changes after 3 h" in gs_a["notes"]
    assert "g-2: 0.80 EUR per 30 min for elapsed 180- min" in gs_a["notes"]
    # the twin of a garage in another layer is named with its distance, never hidden
    assert "second point of this garage in gos_parking_locations (coordinates differ by 55 m)" in rows.loc["gs_b", "notes"]
    gs_c = rows.loc["gs_c"]
    assert gs_c["not_priced_reason"] == "no_published_tariff" and pd.isna(gs_c["tariff_rule_ids"])
    assert gs_c["source_url"] == ARCGIS and gs_c["source_date"] == "2026-10-02"  # the retrieval of the geometry source
    assert gs_c["geometry_method"] == "arcgis_service_point_2018"


def test_the_rates_of_the_assumptions_are_printed(step, inputs, capsys):
    step.build_garages(inputs)
    out = capsys.readouterr().out
    assert "[garages] 6 garages: priced 4 (66.7 %), not priced 2 (banded_tariff 1, no_published_tariff 1)" in out
    assert "ASSUMPTION P3: 2/4 (50.0 %)" in out and "ASSUMPTION P4: 1/4 (25.0 %)" in out and "ASSUMPTION P5: 1/4 (25.0 %)" in out
    assert "rounding stated by the source 3/4, ASSUMPTION P4 1/4; charging times stated 3/4, ASSUMPTION P5 1/4" in out
    assert "monthly product on 1 garages" in out
    # the layer accounting: the Wolfsburg and Goslar garages exist in two layers, the twin is skipped and counted
    assert "[garages] gos_parking_locations: 3 features, 2 garages, 1 used, 1 twin(s)" in out
    assert "[garages] region_parkhaeuser: 3 features, 3 garages, 3 used, 0 twin(s)" in out


def test_a_garage_that_no_specification_covers_stops_the_step(step, inputs, monkeypatch):
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", _specs()[:5])  # gs_c (the Goslar Parkhaus without a tariff) dropped
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


# --------------------------------------------------------------------------- positions


def _municipalities() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"ags": [BS, WOB, GS]}, geometry=[box(X0 - 1000, Y0 - 1000, X0 + 2000, Y0 + 1000),
                                                              box(X0 + 9_000, Y0 - 1000, X0 + 12_000, Y0 + 1000),
                                                              box(X0 + 19_000, Y0 - 1000, X0 + 23_000, Y0 + 1000)],
                            crs=METRIC_CRS).set_index("ags")


def test_every_garage_must_lie_inside_its_municipality_and_no_two_may_coincide(step, inputs, capsys):
    frame = step.build_garages(inputs)
    step.check_positions(frame, _municipalities())
    assert "position check: 6/6 garages inside their own municipality, no two garages within 1 m" in capsys.readouterr().out
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
                                "description": "<p>Tarife:</p><p>Parkzone 1 0,90 € / 30 Min.</p>"}}
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
    assert (table["record_type"] == "garage").sum() == 6 and (table["record_type"] == "monthly_product").sum() == 2
    assert (table["record_type"] == "candidate").sum() == 2 + 1 + 1  # two directory entries, one package candidate, the lots
    lots = table[table["record_id"] == "candidate_wob_car_parks"].iloc[0]
    assert lots["count"] == "3" and lots["reason_code"] == "no_published_tariff"
    used = table[table["decision"] == "used"].iloc[0]
    assert used["garage_id"] == "bs_a" and used["amount_eur"] == "50.00"
    assert table[table["record_id"] == "monthly_bs_a_tag_nacht"].iloc[0]["reason_code"] == "not_the_cheapest"
    assert table[table["record_id"] == "candidate_bs_directory_parkplatz_markthalle"].iloc[0]["reason_code"] == "bga_zone"
    pq.validate_garage_qa(table, frame)
    assert pq.qa_coverage(table) == {"monthly_used": 1, "monthly_not_used": 1,
                                     "monthly_not_used_by_reason": {"not_the_cheapest": 1}, "candidates": 6,
                                     "candidates_by_reason": {"bga_zone": 1, "no_coordinates": 1, "no_published_tariff": 3,
                                                              "zone_street_product": 1}}


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
