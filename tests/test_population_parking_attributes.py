"""Parking attributes in the MATSim plans (zone-based parking costs, issue #436).

Covered here, and why in this shape:

* **The vendored writer** (``matsim.scenario.population``) emits the activity attributes
  ``parkingZone`` (java.lang.String) and ``parkingFree`` (java.lang.Boolean, only ever ``true``)
  and the person attribute ``residentParkingZone`` (java.lang.String) from the OPTIONAL frame
  columns ``parking_zone`` / ``parking_free`` / ``resident_parking_zone``. A missing zone writes
  NO attribute, never the literal "nan"/"None"; a malformed value raises. Asserted through the
  real ``add_person`` / ``prepare_frames`` / ``write_population`` path on synthetic frames.
* **OFF and LEGACY byte identity.** Frames without the three columns must give exactly the plans
  of the writer before this change, with the legacy ring (``enable_urban_parking``) off AND on
  (the ring's activity attribute now shares one attribute dict with the parking attributes). The
  two literals at the end of this module were generated from base commit 1fe22664 -- the
  unchanged writer -- not from this writer, so a refactor that moves one byte fails here.
* **The regional wrapper** (``braunschweig.matsim.scenario.population``): the flag declarations,
  the mutual exclusion with the legacy ring, and ``execute`` with a STUB
  ``braunschweig.parking.attach`` module (the real module is built and tested by a parallel task,
  ``tests/test_parking_attach.py``). The stub proves the wrapper hands the attach functions the
  frames AFTER the cordon in-commuter merge and that the columns they return reach the plans.
* **The cache token** names ``braunschweig.parking.attach`` through the deferred-import hashing
  that ``braunschweig.matsim.simulation.prepare`` uses for its cordon helpers.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib
import inspect
import io
import logging
import sys
import types
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.scenario.population as pop  # noqa: E402
import matsim.writers as writers  # noqa: E402
from braunschweig.matsim.scenario import population as POP  # noqa: E402

CRS = "EPSG:25832"
ATTACH_MODULE_NAME = "braunschweig.parking.attach"
ZONES_STAGE_NAME = "braunschweig.parking.zones_stage"
INCOMMUTER_ID = 900001
RANDOM_SEED = 1234

#: Activity tuple order with both optional parking fields, in their declared order.
ZONE_AND_FREE_FIELDS = pop.ACTIVITY_FIELDS + ["parking_zone", "parking_free"]


# --------------------------------------------------------------------------- writer helpers

def _person_values(**overrides):
    """One person row over PERSON_FIELDS (plus any optional column given as an override)."""
    values = {
        "person_id": 1, "household_income": "3000", "car_availability": "all",
        "bicycle_availability": "none", "census_household_id": 1, "census_person_id": 1,
        "household_id": 1, "has_license": True, "has_pt_subscription": False, "hts_id": 1,
        "hts_household_id": 1, "age": 40, "employed": "yes", "sex": "male",
        "high_income": False, "is_urban_resident": False, "pt_subscription_type": "never_pt",
        "household_income_eur": 4321.0,
    }
    values.update(overrides)
    return values


def _write_one_person(activity_rows, activity_fields=None, person_fields=None,
                      person_overrides=None, enable_urban_parking=False):
    """Write ONE person through the real ``add_person`` and return the XML text.

    Pattern of ``tests/test_population_income_attribute.py::_write_one_person``, extended with the
    ``activity_fields`` argument: every dict in ``activity_rows`` is turned into a tuple over
    ``activity_fields`` (default ``pop.ACTIVITY_FIELDS``) and consecutive activities are joined by
    a walk leg. ``activity_fields`` reaches ``add_person`` only when given, so the default call is
    exactly the legacy one.
    """
    fields = pop.ACTIVITY_FIELDS if activity_fields is None else activity_fields
    tuple_person_fields = pop.PERSON_FIELDS if person_fields is None else person_fields
    person_values = _person_values(**(person_overrides or {}))
    person = tuple(person_values[field] for field in tuple_person_fields)
    activities = []
    for row in activity_rows:
        values = {"person_id": 1, "start_time": np.nan, "end_time": np.nan, "purpose": "home",
                  "geometry": Point(605000.0, 5790000.0), "location_id": -1}
        values.update(row)
        activities.append(tuple(values[field] for field in fields))
    trips = [tuple({"person_id": 1, "mode": "walk", "departure_time": 30000.0 + 3600.0 * index,
                    "travel_time": 600.0}[field] for field in pop.TRIP_FIELDS)
             for index in range(len(activities) - 1)]
    optional_arguments = {} if activity_fields is None else {"activity_fields": activity_fields}

    buffer = io.BytesIO()
    writer = writers.PopulationWriter(buffer)
    writer.start_population()
    pop.add_person(writer, person, activities, trips, [],
                   enable_urban_parking=enable_urban_parking, write_income_eur=False,
                   person_fields=tuple_person_fields, **optional_arguments)
    writer.end_population()
    return buffer.getvalue().decode("utf-8")


def _parse(xml_text):
    return ElementTree.fromstring(xml_text.encode("utf-8"))


def _person_element(root, person_id):
    person = root.find(f"person[@id='{person_id}']")
    assert person is not None, f"person {person_id} missing from the plans"
    return person


def _person_attributes(root, person_id):
    return {attribute.get("name"): (attribute.get("class"), attribute.text)
            for attribute in _person_element(root, person_id).findall("attributes/attribute")}


def _activity_attributes(root, person_id):
    """Per activity of the person's plan: attribute name -> (Java class, text)."""
    return [{attribute.get("name"): (attribute.get("class"), attribute.text)
             for attribute in activity.findall("attributes/attribute")}
            for activity in _person_element(root, person_id).findall("plan/activity")]


class _Progress:
    def update(self):
        pass


class _WriterContext:
    """The two context members ``write_population`` touches."""

    def config(self, name):
        return {"remode_carless_car_legs": False}[name]

    @contextlib.contextmanager
    def progress(self, **_kwargs):
        yield _Progress()


def _two_person_frames():
    """Raw writer input (``prepare_frames``) for two residents, without any parking column.

    Person 1 drives home -> work -> home; the home lies about 11.5 km from Braunschweig Hbf, the
    work place inside the legacy 8 km ring, so the LEGACY literal carries both isParis values.
    Person 2 walks home -> shop -> home entirely inside the ring and owns no car.
    """
    persons = pd.DataFrame([
        _person_values(person_id=1, household_id=10, census_household_id=10, census_person_id=1,
                       hts_id=101, hts_household_id=10),
        _person_values(person_id=2, household_id=11, census_household_id=11, census_person_id=2,
                       hts_id=102, hts_household_id=11, car_availability="none", age=67,
                       sex="female", employed="no", household_income="1500",
                       household_income_eur=1650.0),
    ])[pop.PERSON_FIELDS]
    activities = pd.DataFrame({
        "person_id": [1, 1, 1, 2, 2, 2],
        "activity_index": [0, 1, 2, 0, 1, 2],
        "purpose": ["home", "work", "home", "home", "shop", "home"],
        "start_time": [np.nan, 28800.0, 61200.0, np.nan, 33300.0, 37200.0],
        "end_time": [27000.0, 59400.0, np.nan, 32400.0, 36000.0, np.nan],
    })
    locations = gpd.GeoDataFrame({
        "person_id": [1, 1, 1, 2, 2, 2],
        "activity_index": [0, 1, 2, 0, 1, 2],
        "location_id": [-1, "work_77", -1, -1, "sec_5", -1],
        "geometry": [Point(600000.0, 5780000.0), Point(605500.0, 5790300.0),
                     Point(600000.0, 5780000.0), Point(606000.0, 5791000.0),
                     Point(605100.0, 5790200.0), Point(606000.0, 5791000.0)],
    }, crs=CRS)
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2],
        "trip_index": [0, 1, 0, 1],
        "mode": ["car", "car", "walk", "walk"],
        "departure_time": [27000.0, 59400.0, 32400.0, 36000.0],
        "arrival_time": [28800.0, 61200.0, 33300.0, 37200.0],
    })
    vehicles = pd.DataFrame({
        "owner_id": [1, 1, 2],
        "vehicle_id": ["1:car", "1:car_passenger", "2:car_passenger"],
        "mode": ["car", "car_passenger", "car_passenger"],
    })
    return persons, activities, locations, trips, vehicles


def _write_frames(directory, persons, activities, locations, trips, vehicles,
                  enable_urban_parking=False):
    """``prepare_frames`` + ``write_population`` into ``directory``; the decompressed XML text."""
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "population.xml.gz"
    frames = pop.prepare_frames(persons, activities, locations, trips, vehicles)
    pop.write_population(str(output), *frames, enable_urban_parking=enable_urban_parking,
                         context=_WriterContext())
    with gzip.open(output, "rb") as handle:
        return handle.read().decode("utf-8")


# --------------------------------------------------------------------------- writer: add_person

def test_zone_and_free_parking_are_written_as_activity_attributes():
    xml = _write_one_person(
        [{"purpose": "work", "parking_zone": "bs_zone_ia", "parking_free": True}],
        activity_fields=ZONE_AND_FREE_FIELDS)
    assert '<attribute name="parkingZone" class="java.lang.String">bs_zone_ia</attribute>' in xml
    assert '<attribute name="parkingFree" class="java.lang.Boolean">true</attribute>' in xml


def test_parking_free_false_writes_no_free_attribute():
    xml = _write_one_person(
        [{"purpose": "work", "parking_zone": "bs_zone_ia", "parking_free": False}],
        activity_fields=ZONE_AND_FREE_FIELDS)
    assert '<attribute name="parkingZone" class="java.lang.String">bs_zone_ia</attribute>' in xml
    assert "parkingFree" not in xml


def test_numpy_bool_true_is_written_like_python_true():
    xml = _write_one_person(
        [{"purpose": "work", "parking_zone": "bs_zone_ia", "parking_free": np.bool_(True)}],
        activity_fields=ZONE_AND_FREE_FIELDS)
    assert '<attribute name="parkingFree" class="java.lang.Boolean">true</attribute>' in xml


@pytest.mark.parametrize("missing", [None, np.nan, pd.NA], ids=["none", "nan", "pd_na"])
def test_missing_parking_zone_writes_no_zone_attribute_and_no_empty_block(missing):
    xml = _write_one_person(
        [{"purpose": "shop", "parking_zone": missing, "parking_free": False}],
        activity_fields=ZONE_AND_FREE_FIELDS)
    assert "parkingZone" not in xml
    # Not an empty <attributes/> block either: the activity is the bare element of the legacy
    # writer.
    [activity] = _parse(xml).iter("activity")
    assert len(activity) == 0


def test_zone_column_without_the_free_column_writes_only_the_zone():
    xml = _write_one_person([{"purpose": "shop", "parking_zone": "fx_sz"}],
                            activity_fields=pop.ACTIVITY_FIELDS + ["parking_zone"])
    assert _activity_attributes(_parse(xml), 1) == [
        {"parkingZone": ("java.lang.String", "fx_sz")}]


def test_resident_parking_zone_is_written_as_a_person_attribute():
    xml = _write_one_person([{}], person_fields=pop.PERSON_FIELDS + ["resident_parking_zone"],
                            person_overrides={"resident_parking_zone": "bs_res_a"})
    assert ('<attribute name="residentParkingZone" class="java.lang.String">bs_res_a</attribute>'
            in xml)


@pytest.mark.parametrize("missing", [None, np.nan, pd.NA], ids=["none", "nan", "pd_na"])
def test_missing_resident_parking_zone_writes_no_attribute(missing):
    xml = _write_one_person([{}], person_fields=pop.PERSON_FIELDS + ["resident_parking_zone"],
                            person_overrides={"resident_parking_zone": missing})
    assert "residentParkingZone" not in xml
    assert _person_attributes(_parse(xml), 1) == _person_attributes(
        _parse(_write_one_person([{}])), 1)


@pytest.mark.parametrize("column, value", [
    ("parking_zone", 5),
    ("parking_zone", 3.0),
    ("parking_zone", ""),
    ("parking_free", "true"),
    ("parking_free", 1),
    ("parking_free", None),
    ("parking_free", np.nan),
], ids=["zone_int", "zone_float", "zone_empty", "free_string", "free_int", "free_none",
        "free_nan"])
def test_malformed_activity_parking_values_raise(column, value):
    """A zone id must be a non-empty string; the free-parking draw assigns a boolean to EVERY
    activity, so a missing or non-boolean parking_free means rows bypassed the draw."""
    row = {"purpose": "work", "parking_zone": "fx_bs_ia", "parking_free": False}
    row[column] = value
    with pytest.raises(ValueError, match=column):
        _write_one_person([row], activity_fields=ZONE_AND_FREE_FIELDS)


def test_malformed_resident_parking_zone_raises():
    with pytest.raises(ValueError, match="resident_parking_zone"):
        _write_one_person([{}], person_fields=pop.PERSON_FIELDS + ["resident_parking_zone"],
                          person_overrides={"resident_parking_zone": 7})


def test_default_add_person_call_writes_no_parking_attribute():
    """Existing callers pass no ``activity_fields``: the legacy tuple is read unchanged and every
    activity stays the bare element."""
    xml = _write_one_person([{"purpose": "home"}, {"purpose": "work"}])
    assert "parking" not in xml.lower()
    assert all(len(activity) == 0 for activity in _parse(xml).iter("activity"))


# --------------------------------------------------------------------------- writer: field lists

def test_effective_activity_fields_without_parking_columns_is_the_legacy_list():
    frame = pd.DataFrame({field: [0] for field in pop.ACTIVITY_FIELDS + ["activity_index"]})
    assert pop.effective_activity_fields(frame) == pop.ACTIVITY_FIELDS


def test_effective_activity_fields_appends_present_optional_fields_in_declared_order():
    assert pop.OPTIONAL_ACTIVITY_FIELDS == ["parking_zone", "parking_free"]
    columns = ["parking_free"] + pop.ACTIVITY_FIELDS + ["activity_index", "parking_zone"]
    frame = pd.DataFrame({field: [0] for field in columns})
    assert pop.effective_activity_fields(frame) == ZONE_AND_FREE_FIELDS


def test_resident_parking_zone_is_an_optional_person_field():
    assert "resident_parking_zone" in pop.OPTIONAL_PERSON_FIELDS
    frame = pd.DataFrame({field: [0] for field in pop.PERSON_FIELDS + ["resident_parking_zone"]})
    assert pop.effective_person_fields(frame) == pop.PERSON_FIELDS + ["resident_parking_zone"]


# --------------------------------------------------------------------------- writer: whole frames

def test_write_population_carries_the_parking_columns_to_the_plans(tmp_path):
    persons, activities, locations, trips, vehicles = _two_person_frames()
    activities = activities.assign(
        parking_zone=pd.Series(["fx_res_a", "fx_bs_ia", "fx_res_a", np.nan, "fx_bs_ia", np.nan],
                               dtype=object),
        parking_free=[False, True, False, False, False, False])
    persons = persons.assign(resident_parking_zone=pd.Series(["fx_res_a", np.nan], dtype=object))

    root = _parse(_write_frames(tmp_path, persons, activities, locations, trips, vehicles))

    assert _person_attributes(root, 1)["residentParkingZone"] == ("java.lang.String", "fx_res_a")
    assert "residentParkingZone" not in _person_attributes(root, 2)
    assert _activity_attributes(root, 1) == [
        {"parkingZone": ("java.lang.String", "fx_res_a")},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"),
         "parkingFree": ("java.lang.Boolean", "true")},
        {"parkingZone": ("java.lang.String", "fx_res_a")},
    ]
    assert _activity_attributes(root, 2) == [
        {}, {"parkingZone": ("java.lang.String", "fx_bs_ia")}, {}]


def test_frames_without_parking_columns_match_the_prechange_writer_bytes(tmp_path):
    """OFF (parking_zones_enabled false, legacy ring off): identical to base 1fe22664."""
    xml = _write_frames(tmp_path, *_two_person_frames(), enable_urban_parking=False)
    assert xml == PRECHANGE_OFF_XML


def test_legacy_ring_frames_match_the_prechange_writer_bytes(tmp_path):
    """LEGACY (legacy ring on, no parking columns): identical to base 1fe22664, although the
    isParis activity attribute now goes through the shared activity-attribute dict."""
    xml = _write_frames(tmp_path, *_two_person_frames(), enable_urban_parking=True)
    assert xml == PRECHANGE_LEGACY_XML


def test_off_plans_carry_no_parking_attribute_and_no_empty_activity_attributes(tmp_path):
    root = _parse(_write_frames(tmp_path, *_two_person_frames(), enable_urban_parking=False))
    names = [attribute.get("name") for attribute in root.iter("attribute")]
    assert "householdId" in names  # guard: the scan looks at real attributes
    assert not [name for name in names if "parking" in name.lower()]
    assert "isParis" not in names
    for activity in root.iter("activity"):
        for block in activity.findall("attributes"):
            assert len(block.findall("attribute")) > 0, "empty <attributes> under an activity"
        assert len(activity) == 0, "an OFF activity must be the bare element"


def test_all_missing_parking_values_write_the_same_bytes_as_absent_columns(tmp_path):
    """The golden string is generated here from the same persons BEFORE the columns are added;
    columns that are present but carry no zone and no free parking must not change one byte."""
    persons, activities, locations, trips, vehicles = _two_person_frames()
    golden = _write_frames(tmp_path / "absent", persons, activities, locations, trips, vehicles)

    activities = activities.assign(
        parking_zone=pd.Series([np.nan] * len(activities), dtype=object), parking_free=False)
    persons = persons.assign(
        resident_parking_zone=pd.Series([np.nan] * len(persons), dtype=object))
    assert _write_frames(tmp_path / "missing", persons, activities, locations, trips,
                         vehicles) == golden


# --------------------------------------------------------------------------- wrapper: configure

class _ConfigureRecorder:
    """synpp ConfigurationContext stand-in: remembers declared defaults, returns overrides.

    Same contract as ``tests/test_commute_day_consumers.py::_ConfigureRecorder``: a later
    single-argument read returns the configured value (or the declared default), so the
    ``if context.config(key):`` branches of ``configure`` are taken exactly as under synpp.
    """

    def __init__(self, config=None):
        self.stages = []
        self.config_keys = {}
        self._config = config or {}

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        if name not in self.config_keys:
            self.config_keys[name] = default
        if name in self._config:
            return self._config[name]
        return self.config_keys[name]


#: Every other wrapper feature off, so each test sees only the parking declarations it varies.
_OTHER_FEATURES_OFF = {"cordon_enabled": False, "commute_day_state_enabled": False,
                       "day_absence_enabled": False}


def _declare(**config):
    recorder = _ConfigureRecorder(config=dict(_OTHER_FEATURES_OFF, **config))
    POP.configure(recorder)
    return recorder


def test_configure_rejects_zone_parking_combined_with_the_legacy_ring():
    with pytest.raises(ValueError) as error:
        _declare(parking_zones_enabled=True, enable_urban_parking=True)
    assert "parking_zones_enabled" in str(error.value)
    assert "enable_urban_parking" in str(error.value)


def test_configure_declares_the_zones_stage_the_shift_and_the_seed_when_enabled():
    recorder = _declare(parking_zones_enabled=True)
    assert recorder.config_keys["parking_zones_enabled"] is False  # the declared default
    assert ZONES_STAGE_NAME in recorder.stages
    assert recorder.config_keys["parking_workplace_free_share_shift"] == 0.0
    assert "random_seed" in recorder.config_keys


def test_configure_off_declares_neither_the_zones_stage_nor_the_shift():
    recorder = _declare()
    assert recorder.config_keys["parking_zones_enabled"] is False
    assert ZONES_STAGE_NAME not in recorder.stages
    assert "parking_workplace_free_share_shift" not in recorder.config_keys
    assert "random_seed" not in recorder.config_keys


def test_configure_accepts_the_legacy_ring_alone():
    recorder = _declare(enable_urban_parking=True)
    assert ZONES_STAGE_NAME not in recorder.stages


@pytest.mark.parametrize("shift", [1.5, -1.01, float("nan"), "0.1", True])
def test_configure_rejects_a_free_share_shift_outside_minus_one_to_one(shift):
    with pytest.raises(ValueError, match="parking_workplace_free_share_shift"):
        _declare(parking_zones_enabled=True, parking_workplace_free_share_shift=shift)


# --------------------------------------------------------------------------- wrapper: execute

class _ExecuteContext:
    """synpp ExecuteContext stand-in refusing any stage or key ``configure`` did not declare."""

    def __init__(self, declared, stages, config, path):
        self._declared = declared
        self._stages = stages
        self._config = config
        self._path = path

    def stage(self, name):
        assert name in self._declared.stages, f"stage '{name}' was not declared in configure()"
        return self._stages[name]

    def config(self, name):
        assert name in self._declared.config_keys, f"config '{name}' was not declared"
        if name in self._config:
            return self._config[name]
        return self._declared.config_keys[name]

    def path(self):
        return str(self._path)

    @contextlib.contextmanager
    def progress(self, **_kwargs):
        yield _Progress()


def _parking_release():
    """The zones-stage output the wrapper forwards (keys of braunschweig.parking.zones_stage)."""
    return {
        "zones": gpd.GeoDataFrame({
            "zone_id": ["fx_bs_ia", "fx_res_a"],
            "geometry": [box(604900.0, 5790000.0, 605400.0, 5790500.0),
                         box(603000.0, 5791000.0, 603500.0, 5791500.0)],
        }, crs=CRS),
        "tariffs": pd.DataFrame({"zone_id": ["fx_bs_ia", "fx_res_a"],
                                 "zone_type": ["street_paid", "resident_zone"],
                                 "workplace_class": ["bs_zentrum", "bs_innenbereich"]}),
        "workplace_shares": pd.DataFrame({"level": ["class", "class"],
                                          "workplace_class": ["bs_zentrum", "bs_innenbereich"],
                                          "share_free_total": [0.4, 0.6]}),
        "coverage_register": pd.DataFrame({"ags": [], "status": []}),
        "sources": [],
    }


def _wrapper_stages(incommuter_work):
    """Stage outputs for two residents and one SvB in-commuter (no student in-commuters).

    Resident 1 lives in the resident zone fx_res_a and works in fx_bs_ia; resident 2 lives
    outside every zone and shops in fx_bs_ia. The in-commuter lives far outside the cordon and
    works at ``incommuter_work``.
    """
    residents = pd.DataFrame([
        _person_values(person_id=1, household_id=1, census_household_id=1, census_person_id=1,
                       hts_id=11, hts_household_id=1),
        _person_values(person_id=2, household_id=2, census_household_id=2, census_person_id=2,
                       hts_id=12, hts_household_id=2, car_availability="none", sex="female"),
    ])
    activities = pd.DataFrame({
        "person_id": [1, 1, 1, 2, 2, 2],
        "activity_index": [0, 1, 2, 0, 1, 2],
        "purpose": ["home", "work", "home", "home", "shop", "home"],
        "start_time": [np.nan, 28800.0, 61200.0, np.nan, 36000.0, 43200.0],
        "end_time": [27000.0, 59400.0, np.nan, 34200.0, 41400.0, np.nan],
    })
    locations = gpd.GeoDataFrame({
        "person_id": [1, 1, 1, 2, 2, 2],
        "activity_index": [0, 1, 2, 0, 1, 2],
        "location_id": [-1, "work_1", -1, -1, "sec_2", -1],
        "geometry": [Point(603250.0, 5791250.0), Point(605100.0, 5790200.0),
                     Point(603250.0, 5791250.0), Point(607000.0, 5792000.0),
                     Point(605200.0, 5790300.0), Point(607000.0, 5792000.0)],
    }, crs=CRS)
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "mode": ["car", "car", "walk", "walk"],
        "departure_time": [27000.0, 59400.0, 34200.0, 41400.0],
        "arrival_time": [28800.0, 61200.0, 36000.0, 43200.0],
    })
    vehicles = pd.DataFrame({
        "owner_id": [1, 1, 2],
        "vehicle_id": ["1:car", "1:car_passenger", "2:car_passenger"],
        "mode": ["car", "car_passenger", "car_passenger"],
    })
    incommuter = {
        "persons": pd.DataFrame([_person_values(
            person_id=INCOMMUTER_ID, household_id=INCOMMUTER_ID,
            census_household_id=INCOMMUTER_ID, census_person_id=INCOMMUTER_ID,
            hts_id=INCOMMUTER_ID, hts_household_id=INCOMMUTER_ID)]),
        "activities": pd.DataFrame({
            "person_id": [INCOMMUTER_ID] * 3, "activity_index": [0, 1, 2],
            "purpose": ["home", "work", "home"],
            "start_time": [np.nan, 28800.0, 63000.0], "end_time": [25200.0, 57600.0, np.nan],
        }),
        "locations": gpd.GeoDataFrame({
            "person_id": [INCOMMUTER_ID] * 3, "activity_index": [0, 1, 2],
            "location_id": [-1, "work_900001", -1],
            "geometry": [Point(590000.0, 5770000.0), incommuter_work,
                         Point(590000.0, 5770000.0)],
        }, crs=CRS),
        "trips": pd.DataFrame({
            "person_id": [INCOMMUTER_ID] * 2, "trip_index": [0, 1], "mode": ["car", "car"],
            "departure_time": [25200.0, 57600.0], "arrival_time": [28800.0, 63000.0],
        }),
        "vehicles": pd.DataFrame({
            "owner_id": [INCOMMUTER_ID] * 2,
            "vehicle_id": ["900001:car", "900001:car_passenger"],
            "mode": ["car", "car_passenger"],
        }),
    }
    # The zero-column frames a skipped student in-commuter stage returns.
    no_students = {name: pd.DataFrame()
                   for name in ("persons", "activities", "locations", "trips", "vehicles")}
    return {
        "synthesis.population.enriched": residents,
        "synthesis.population.spatial.locations": locations,
        "synthesis.vehicles.vehicles": (pd.DataFrame(), vehicles),
        "synthesis.population.trips.final": trips,
        "synthesis.population.activities.final": activities,
        "braunschweig.synthesis.incommuters": incommuter,
        "braunschweig.synthesis.student_incommuters": no_students,
        ZONES_STAGE_NAME: _parking_release(),
    }


def _wrapper_context(tmp_path, parking_enabled=True, incommuter_work=Point(605150.0, 5790250.0),
                     shift=0.25):
    config = dict(_OTHER_FEATURES_OFF, cordon_enabled=True, enable_urban_parking=False,
                  write_income_eur=False, parking_zones_enabled=parking_enabled)
    if parking_enabled:
        config.update(parking_workplace_free_share_shift=shift, random_seed=RANDOM_SEED)
    declared = _ConfigureRecorder(config=config)
    POP.configure(declared)
    return _ExecuteContext(declared, _wrapper_stages(incommuter_work), config, tmp_path)


def _stub_attach_module(calls, drop_a_row=False):
    """A stand-in for ``braunschweig.parking.attach`` with the interface of the real module.

    Zones are assigned by point-in-polygon on the release polygons, the resident zone from home
    activities in ``resident_zone`` zones, and -- deterministic, unlike the real draw -- every
    work/education activity in a street_paid or resident_zone zone is marked free. Every call
    records its arguments in ``calls``. ``drop_a_row`` breaks the row-preserving contract.
    """
    module = types.ModuleType(ATTACH_MODULE_NAME)

    def attach_parking_zones(activities, locations, zones):
        calls.setdefault("attach_parking_zones", []).append(
            {"activities": activities.copy(), "locations": locations.copy(), "zones": zones})
        merged = activities.merge(locations[["person_id", "activity_index", "geometry"]],
                                  on=["person_id", "activity_index"], how="left")
        zone_ids = [next((zone_id for zone_id, polygon in zip(zones["zone_id"], zones.geometry)
                          if polygon.contains(point)), np.nan)
                    for point in merged["geometry"]]
        result = activities.copy()
        result["parking_zone"] = pd.Series(zone_ids, index=result.index, dtype=object)
        return result.iloc[:-1] if drop_a_row else result

    def attach_resident_zones(persons, activities, tariffs):
        calls.setdefault("attach_resident_zones", []).append(
            {"persons": persons.copy(), "activities": activities.copy(), "tariffs": tariffs})
        resident_zone_ids = set(tariffs.loc[tariffs["zone_type"] == "resident_zone", "zone_id"])
        homes = activities[(activities["purpose"] == "home")
                           & activities["parking_zone"].isin(resident_zone_ids)]
        home_zone = homes.drop_duplicates("person_id").set_index("person_id")["parking_zone"]
        result = persons.copy()
        result["resident_parking_zone"] = result["person_id"].map(home_zone).astype(object)
        return result

    def draw_parking_free(activities, tariffs, workplace_shares, random_seed, shift=0.0):
        calls.setdefault("draw_parking_free", []).append(
            {"activities": activities.copy(), "tariffs": tariffs,
             "workplace_shares": workplace_shares, "random_seed": random_seed, "shift": shift})
        zone_type = activities["parking_zone"].map(tariffs.set_index("zone_id")["zone_type"])
        eligible = (activities["purpose"].isin(("work", "education"))
                    & zone_type.isin(("street_paid", "resident_zone")))
        result = activities.copy()
        result["parking_free"] = eligible.to_numpy(dtype=bool)
        return result

    module.attach_parking_zones = attach_parking_zones
    module.attach_resident_zones = attach_resident_zones
    module.draw_parking_free = draw_parking_free
    return module


def _inject_attach(monkeypatch, module):
    """Make both import forms of ``braunschweig.parking.attach`` resolve to ``module``.

    ``sys.modules`` alone is not enough once the REAL module has been imported by another test in
    the same session: the attribute on the parent package then wins over ``sys.modules`` (CPython
    ``IMPORT_FROM``), so the stub is bound there as well. monkeypatch restores both.
    """
    import braunschweig.parking as parking_package

    monkeypatch.setitem(sys.modules, ATTACH_MODULE_NAME, module)
    monkeypatch.setattr(parking_package, "attach", module, raising=False)


def _read_plans(tmp_path):
    with gzip.open(tmp_path / "population.xml.gz", "rb") as handle:
        return ElementTree.fromstring(handle.read())


def test_incommuter_activity_gets_zone_attribute(tmp_path, monkeypatch, caplog):
    calls = {}
    _inject_attach(monkeypatch, _stub_attach_module(calls))
    context = _wrapper_context(tmp_path)
    release = context._stages[ZONES_STAGE_NAME]

    with caplog.at_level(logging.INFO, logger=POP.__name__):
        POP.execute(context)

    # The attach functions saw the frames AFTER the cordon in-commuter merge ...
    [zones_call] = calls["attach_parking_zones"]
    assert set(zones_call["activities"]["person_id"]) == {1, 2, INCOMMUTER_ID}
    assert len(zones_call["activities"]) == 9
    assert set(zones_call["locations"]["person_id"]) == {1, 2, INCOMMUTER_ID}
    assert zones_call["zones"] is release["zones"]
    [resident_call] = calls["attach_resident_zones"]
    assert set(resident_call["persons"]["person_id"]) == {1, 2, INCOMMUTER_ID}
    assert "parking_zone" in resident_call["activities"].columns
    assert resident_call["tariffs"] is release["tariffs"]
    [draw_call] = calls["draw_parking_free"]
    assert "parking_zone" in draw_call["activities"].columns
    assert draw_call["tariffs"] is release["tariffs"]
    assert draw_call["workplace_shares"] is release["workplace_shares"]
    assert draw_call["random_seed"] == RANDOM_SEED
    assert isinstance(draw_call["random_seed"], int)
    assert draw_call["shift"] == 0.25

    # ... and the columns they returned reach the plans, the in-commuter's included.
    root = _read_plans(tmp_path)
    assert _activity_attributes(root, INCOMMUTER_ID) == [
        {},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"),
         "parkingFree": ("java.lang.Boolean", "true")},
        {},
    ]
    assert "residentParkingZone" not in _person_attributes(root, INCOMMUTER_ID)
    assert _person_attributes(root, 1)["residentParkingZone"] == ("java.lang.String", "fx_res_a")
    assert _activity_attributes(root, 1) == [
        {"parkingZone": ("java.lang.String", "fx_res_a")},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"),
         "parkingFree": ("java.lang.Boolean", "true")},
        {"parkingZone": ("java.lang.String", "fx_res_a")},
    ]
    assert _activity_attributes(root, 2) == [
        {}, {"parkingZone": ("java.lang.String", "fx_bs_ia")}, {}]

    # One coverage line for the whole population (fallback transparency).
    coverage = [record.getMessage() for record in caplog.records
                if record.getMessage().startswith("[parking population]")
                and "parkingZone on" in record.getMessage()]
    assert len(coverage) == 1
    assert "5/9 activities" in coverage[0]
    assert "in-commuter activities with a parkingZone: 1/3" in coverage[0]
    assert "parkingFree=true on 2 activities" in coverage[0]
    assert "1/3 persons" in coverage[0]


def test_execute_off_never_touches_the_attach_module(tmp_path, monkeypatch):
    module = types.ModuleType(ATTACH_MODULE_NAME)

    def refuse(*_args, **_kwargs):
        raise AssertionError("attach must not be called with parking_zones_enabled false")

    module.attach_parking_zones = module.attach_resident_zones = refuse
    module.draw_parking_free = refuse
    _inject_attach(monkeypatch, module)

    POP.execute(_wrapper_context(tmp_path, parking_enabled=False))

    root = _read_plans(tmp_path)
    names = [attribute.get("name") for attribute in root.iter("attribute")]
    assert not [name for name in names if "parking" in name.lower()]
    assert all(len(activity) == 0 for activity in root.iter("activity"))


def test_execute_rejects_an_attach_result_that_changes_the_row_count(tmp_path, monkeypatch):
    _inject_attach(monkeypatch, _stub_attach_module({}, drop_a_row=True))
    with pytest.raises(ValueError, match="attach_parking_zones returned 8 rows for 9"):
        POP.execute(_wrapper_context(tmp_path))


def test_execute_warns_when_no_incommuter_activity_carries_a_zone(tmp_path, monkeypatch, caplog):
    """In-commuters present, residents zoned, not one in-commuter activity zoned: the signature of
    a broken in-commuter locations join, surfaced loudly rather than written silently."""
    _inject_attach(monkeypatch, _stub_attach_module({}))
    context = _wrapper_context(tmp_path, incommuter_work=Point(595000.0, 5775000.0))
    with caplog.at_level(logging.WARNING, logger=POP.__name__):
        POP.execute(context)
    assert any(record.levelno == logging.WARNING and "in-commuter" in record.getMessage()
               for record in caplog.records)


# --------------------------------------------------------------------------- wrapper: validate

def test_validate_covers_the_attach_module_by_name():
    assert ATTACH_MODULE_NAME in POP._DEFERRED_HELPER_MODULE_NAMES


def test_validate_folds_a_present_deferred_module_into_the_token(monkeypatch):
    # braunschweig.parking.cost stands in for a present deferred helper module.
    monkeypatch.setattr(POP, "_DEFERRED_HELPER_MODULE_NAMES", ())
    without = POP.validate(None)
    monkeypatch.setattr(POP, "_DEFERRED_HELPER_MODULE_NAMES", ("braunschweig.parking.cost",))
    with_module = POP.validate(None)

    expected = hashlib.md5()
    for module in POP._HELPER_MODULES:
        expected.update(inspect.getsource(module).encode("utf-8"))
    expected.update(inspect.getsource(
        importlib.import_module("braunschweig.parking.cost")).encode("utf-8"))
    assert with_module == expected.hexdigest()
    assert with_module != without


def test_validate_raises_when_a_present_deferred_module_cannot_be_hashed(monkeypatch):
    monkeypatch.setattr(POP, "_DEFERRED_HELPER_MODULE_NAMES", ("braunschweig.parking.cost",))
    real_getsource = inspect.getsource

    def getsource(target):
        if getattr(target, "__name__", None) == "braunschweig.parking.cost":
            raise OSError("source not available")
        return real_getsource(target)

    monkeypatch.setattr(inspect, "getsource", getsource)
    with pytest.raises(RuntimeError, match="braunschweig.parking.cost"):
        POP.validate(None)


def test_validate_skips_an_absent_deferred_module_with_a_warning(monkeypatch, caplog):
    """TEMPORARY: pins the find_spec guard that lets this branch run before
    braunschweig.parking.attach exists. Delete this test together with the guard in the final
    wave (issue #436); a missing deferred module must then raise like in
    braunschweig.matsim.simulation.prepare."""
    absent = "braunschweig.parking.module_absent_for_the_guard_test"
    monkeypatch.setattr(POP, "_DEFERRED_HELPER_MODULE_NAMES", ())
    without = POP.validate(None)
    monkeypatch.setattr(POP, "_DEFERRED_HELPER_MODULE_NAMES", (absent,))
    with caplog.at_level(logging.WARNING, logger=POP.__name__):
        assert POP.validate(None) == without
    assert any(record.levelno == logging.WARNING and absent in record.getMessage()
               for record in caplog.records)


# --------------------------------------------------------------------------- pre-change literals
#
# Generated from base commit 1fe22664 (the writer BEFORE the parking attributes) by running
# _write_frames(..., *_two_person_frames(), enable_urban_parking=False/True) on that commit.
# Never regenerate them from the current writer: that would turn the byte-identity pin into a
# tautology.

PRECHANGE_OFF_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">
<population>
  <person id="1">
    <attributes>
      <attribute name="householdId" class="java.lang.Integer">10</attribute>
      <attribute name="householdIncome" class="java.lang.String">3000</attribute>
      <attribute name="highIncome" class="java.lang.Boolean">False</attribute>
      <attribute name="carAvailability" class="java.lang.String">all</attribute>
      <attribute name="bicycleAvailability" class="java.lang.String">none</attribute>
      <attribute name="censusHouseholdId" class="java.lang.Long">10</attribute>
      <attribute name="censusPersonId" class="java.lang.Long">1</attribute>
      <attribute name="htsHouseholdId" class="java.lang.Long">10</attribute>
      <attribute name="htsPersonId" class="java.lang.Long">101</attribute>
      <attribute name="hasPtSubscription" class="java.lang.Boolean">False</attribute>
      <attribute name="ptSubscriptionType" class="java.lang.String">never_pt</attribute>
      <attribute name="hasLicense" class="java.lang.String">yes</attribute>
      <attribute name="age" class="java.lang.Integer">40</attribute>
      <attribute name="employed" class="java.lang.String">yes</attribute>
      <attribute name="sex" class="java.lang.String">m</attribute>
      <attribute name="vehicles" class="org.matsim.vehicles.PersonVehicles">{"car":"1:car","car_passenger":"1:car_passenger"}</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="home" x="600000.000000" y="5780000.000000" facility="home_10" end_time="07:30:00" />
      <leg mode="car" dep_time="07:30:00" trav_time="00:30:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">car</attribute>
      </attributes>
      </leg>
      <activity type="work" x="605500.000000" y="5790300.000000" facility="work_77" start_time="08:00:00" end_time="16:30:00" />
      <leg mode="car" dep_time="16:30:00" trav_time="00:30:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">car</attribute>
      </attributes>
      </leg>
      <activity type="home" x="600000.000000" y="5780000.000000" facility="home_10" start_time="17:00:00" />
    </plan>
  </person>
  <person id="2">
    <attributes>
      <attribute name="householdId" class="java.lang.Integer">11</attribute>
      <attribute name="householdIncome" class="java.lang.String">1500</attribute>
      <attribute name="highIncome" class="java.lang.Boolean">False</attribute>
      <attribute name="carAvailability" class="java.lang.String">none</attribute>
      <attribute name="bicycleAvailability" class="java.lang.String">none</attribute>
      <attribute name="censusHouseholdId" class="java.lang.Long">11</attribute>
      <attribute name="censusPersonId" class="java.lang.Long">2</attribute>
      <attribute name="htsHouseholdId" class="java.lang.Long">11</attribute>
      <attribute name="htsPersonId" class="java.lang.Long">102</attribute>
      <attribute name="hasPtSubscription" class="java.lang.Boolean">False</attribute>
      <attribute name="ptSubscriptionType" class="java.lang.String">never_pt</attribute>
      <attribute name="hasLicense" class="java.lang.String">yes</attribute>
      <attribute name="age" class="java.lang.Integer">67</attribute>
      <attribute name="employed" class="java.lang.String">no</attribute>
      <attribute name="sex" class="java.lang.String">f</attribute>
      <attribute name="vehicles" class="org.matsim.vehicles.PersonVehicles">{"car_passenger":"2:car_passenger"}</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="home" x="606000.000000" y="5791000.000000" facility="home_11" end_time="09:00:00" />
      <leg mode="walk" dep_time="09:00:00" trav_time="00:15:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">walk</attribute>
      </attributes>
      </leg>
      <activity type="shop" x="605100.000000" y="5790200.000000" facility="sec_5" start_time="09:15:00" end_time="10:00:00" />
      <leg mode="walk" dep_time="10:00:00" trav_time="00:20:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">walk</attribute>
      </attributes>
      </leg>
      <activity type="home" x="606000.000000" y="5791000.000000" facility="home_11" start_time="10:20:00" />
    </plan>
  </person>
</population>
"""

PRECHANGE_LEGACY_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">
<population>
  <person id="1">
    <attributes>
      <attribute name="householdId" class="java.lang.Integer">10</attribute>
      <attribute name="householdIncome" class="java.lang.String">3000</attribute>
      <attribute name="highIncome" class="java.lang.Boolean">False</attribute>
      <attribute name="isParis" class="java.lang.Boolean">False</attribute>
      <attribute name="carAvailability" class="java.lang.String">all</attribute>
      <attribute name="bicycleAvailability" class="java.lang.String">none</attribute>
      <attribute name="censusHouseholdId" class="java.lang.Long">10</attribute>
      <attribute name="censusPersonId" class="java.lang.Long">1</attribute>
      <attribute name="htsHouseholdId" class="java.lang.Long">10</attribute>
      <attribute name="htsPersonId" class="java.lang.Long">101</attribute>
      <attribute name="hasPtSubscription" class="java.lang.Boolean">False</attribute>
      <attribute name="ptSubscriptionType" class="java.lang.String">never_pt</attribute>
      <attribute name="hasLicense" class="java.lang.String">yes</attribute>
      <attribute name="age" class="java.lang.Integer">40</attribute>
      <attribute name="employed" class="java.lang.String">yes</attribute>
      <attribute name="sex" class="java.lang.String">m</attribute>
      <attribute name="vehicles" class="org.matsim.vehicles.PersonVehicles">{"car":"1:car","car_passenger":"1:car_passenger"}</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="home" x="600000.000000" y="5780000.000000" facility="home_10" end_time="07:30:00" >
        <attributes>
          <attribute name="isParis" class="java.lang.Boolean">false</attribute>
        </attributes>
      </activity>
      <leg mode="car" dep_time="07:30:00" trav_time="00:30:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">car</attribute>
      </attributes>
      </leg>
      <activity type="work" x="605500.000000" y="5790300.000000" facility="work_77" start_time="08:00:00" end_time="16:30:00" >
        <attributes>
          <attribute name="isParis" class="java.lang.Boolean">true</attribute>
        </attributes>
      </activity>
      <leg mode="car" dep_time="16:30:00" trav_time="00:30:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">car</attribute>
      </attributes>
      </leg>
      <activity type="home" x="600000.000000" y="5780000.000000" facility="home_10" start_time="17:00:00" >
        <attributes>
          <attribute name="isParis" class="java.lang.Boolean">false</attribute>
        </attributes>
      </activity>
    </plan>
  </person>
  <person id="2">
    <attributes>
      <attribute name="householdId" class="java.lang.Integer">11</attribute>
      <attribute name="householdIncome" class="java.lang.String">1500</attribute>
      <attribute name="highIncome" class="java.lang.Boolean">False</attribute>
      <attribute name="isParis" class="java.lang.Boolean">False</attribute>
      <attribute name="carAvailability" class="java.lang.String">none</attribute>
      <attribute name="bicycleAvailability" class="java.lang.String">none</attribute>
      <attribute name="censusHouseholdId" class="java.lang.Long">11</attribute>
      <attribute name="censusPersonId" class="java.lang.Long">2</attribute>
      <attribute name="htsHouseholdId" class="java.lang.Long">11</attribute>
      <attribute name="htsPersonId" class="java.lang.Long">102</attribute>
      <attribute name="hasPtSubscription" class="java.lang.Boolean">False</attribute>
      <attribute name="ptSubscriptionType" class="java.lang.String">never_pt</attribute>
      <attribute name="hasLicense" class="java.lang.String">yes</attribute>
      <attribute name="age" class="java.lang.Integer">67</attribute>
      <attribute name="employed" class="java.lang.String">no</attribute>
      <attribute name="sex" class="java.lang.String">f</attribute>
      <attribute name="vehicles" class="org.matsim.vehicles.PersonVehicles">{"car_passenger":"2:car_passenger"}</attribute>
    </attributes>
    <plan selected="yes">
      <activity type="home" x="606000.000000" y="5791000.000000" facility="home_11" end_time="09:00:00" >
        <attributes>
          <attribute name="isParis" class="java.lang.Boolean">true</attribute>
        </attributes>
      </activity>
      <leg mode="walk" dep_time="09:00:00" trav_time="00:15:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">walk</attribute>
      </attributes>
      </leg>
      <activity type="shop" x="605100.000000" y="5790200.000000" facility="sec_5" start_time="09:15:00" end_time="10:00:00" >
        <attributes>
          <attribute name="isParis" class="java.lang.Boolean">true</attribute>
        </attributes>
      </activity>
      <leg mode="walk" dep_time="10:00:00" trav_time="00:20:00" >
      <attributes>
        <attribute name="routingMode" class="java.lang.String">walk</attribute>
      </attributes>
      </leg>
      <activity type="home" x="606000.000000" y="5791000.000000" facility="home_11" start_time="10:20:00" >
        <attributes>
          <attribute name="isParis" class="java.lang.Boolean">true</attribute>
        </attributes>
      </activity>
    </plan>
  </person>
</population>
"""
