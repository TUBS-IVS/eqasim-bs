"""Parking attributes in the MATSim plans (zone-based parking costs, issue #436).

Covered here, and why in this shape:

* **The vendored writer** (``matsim.scenario.population``) emits the activity attributes
  ``parkingZone`` (java.lang.String), ``parkingFree`` (java.lang.Boolean, only ever ``true``) and
  ``parkingDistrict`` (java.lang.String) and the person attributes ``residentParkingZone`` and
  ``residentParkingDistrict`` (java.lang.String) from the OPTIONAL frame columns ``parking_zone`` /
  ``parking_free`` / ``parking_district`` / ``resident_parking_zone`` / ``resident_parking_district``
  (the district columns are the resident parking districts of spec Amendment C3, a second layer).
  A missing zone or district writes NO attribute, never the literal "nan"/"None"; a malformed value
  raises. Asserted through the real ``add_person`` / ``prepare_frames`` / ``write_population`` path
  on synthetic frames.
* **OFF and LEGACY byte identity.** Frames without the parking columns must give exactly the plans
  of the writer before this change, with the legacy ring (``enable_urban_parking``) off AND on
  (the ring's activity attribute now shares one attribute dict with the parking attributes). The
  two literals at the end of this module were generated from base commit 1fe22664 -- the
  unchanged writer -- not from this writer, so a refactor that moves one byte fails here.
* **The plain writer refuses the flag.** A pipeline that runs ``matsim.scenario.population`` itself
  (no alias to the wrapper, e.g. simple_ipf_open) attaches no parking attribute, so its
  ``configure`` rejects ``parking_zones_enabled`` and names the alias; with the flag off it only
  declares the key. Asserted with synpp's real ``ConfigurationContext``.
* **The regional wrapper** (``braunschweig.matsim.scenario.population``): the flag declarations,
  the mutual exclusion with the legacy ring, and ``execute`` with a STUB
  ``braunschweig.parking.attach`` module, which proves the wrapper hands the five attach functions the
  frames AFTER the cordon in-commuter merge and that the columns they return reach the plans; and
  ``execute`` once with the REAL module on the fixture release of ``tests/fixtures/parking`` (the
  attach functions themselves are tested in ``tests/test_parking_attach.py``).
* **The cache token** names ``braunschweig.parking.attach`` and ``braunschweig.parking.zones`` (whose
  ``assign_zones`` the attach module calls) through the deferred-import hashing that
  ``braunschweig.matsim.simulation.prepare`` uses for its cordon helpers; a deferred module that is
  absent or cannot be read raises.
"""
from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib
import inspect
import io
import logging
import math
import sys
import types
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, box
from synpp.pipeline import ConfigurationContext

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.scenario.population as pop  # noqa: E402
import matsim.writers as writers  # noqa: E402
from braunschweig.matsim.scenario import population as POP  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "parking"
CRS = "EPSG:25832"
ATTACH_MODULE_NAME = "braunschweig.parking.attach"
ZONES_STAGE_NAME = "braunschweig.parking.zones_stage"
INCOMMUTER_ID = 900001
RANDOM_SEED = 1234

#: Activity tuple order with both optional parking fields, in their declared order.
ZONE_AND_FREE_FIELDS = pop.ACTIVITY_FIELDS + ["parking_zone", "parking_free"]
#: ... and with the resident district of spec Amendment C3 as well.
ALL_PARKING_FIELDS = ZONE_AND_FREE_FIELDS + ["parking_district"]


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


@pytest.mark.parametrize("text", ["nan", "None", "<NA>"])
@pytest.mark.parametrize("column", ["parking_zone", "resident_parking_zone"])
def test_the_text_form_of_a_missing_zone_raises_instead_of_becoming_a_zone_id(column, text):
    """str(np.nan), str(None) and str(pd.NA): what a broken upstream join (e.g. astype(str) over missing zones)
    leaves in the column. Written as a zone id, it would name a zone the tariff model does not have."""
    with pytest.raises(ValueError, match=f"{column}.*missing value"):
        if column == "parking_zone":
            _write_one_person([{"purpose": "work", "parking_zone": text, "parking_free": False}],
                              activity_fields=ZONE_AND_FREE_FIELDS)
        else:
            _write_one_person([{}], person_fields=pop.PERSON_FIELDS + [column], person_overrides={column: text})


def test_the_parking_district_is_written_as_an_activity_attribute_next_to_the_zone():
    xml = _write_one_person(
        [{"purpose": "work", "parking_zone": "bs_zone_ia", "parking_free": True, "parking_district": "bs_district_a"}],
        activity_fields=ALL_PARKING_FIELDS)
    assert '<attribute name="parkingDistrict" class="java.lang.String">bs_district_a</attribute>' in xml
    # a district without a zone is an ordinary state: the layers are independent
    xml = _write_one_person([{"purpose": "shop", "parking_zone": None, "parking_free": False,
                              "parking_district": "gs_district_c"}], activity_fields=ALL_PARKING_FIELDS)
    assert _activity_attributes(_parse(xml), 1) == [{"parkingDistrict": ("java.lang.String", "gs_district_c")}]


@pytest.mark.parametrize("missing", [None, np.nan, pd.NA], ids=["none", "nan", "pd_na"])
def test_missing_parking_district_writes_no_district_attribute_and_no_empty_block(missing):
    xml = _write_one_person([{"purpose": "shop", "parking_zone": None, "parking_free": False,
                              "parking_district": missing}], activity_fields=ALL_PARKING_FIELDS)
    assert "parkingDistrict" not in xml
    [activity] = _parse(xml).iter("activity")
    assert len(activity) == 0


def test_resident_parking_district_is_written_as_a_person_attribute():
    xml = _write_one_person([{}], person_fields=pop.PERSON_FIELDS + ["resident_parking_district"],
                            person_overrides={"resident_parking_district": "bs_district_b"})
    assert ('<attribute name="residentParkingDistrict" class="java.lang.String">bs_district_b</attribute>' in xml)


@pytest.mark.parametrize("missing", [None, np.nan, pd.NA], ids=["none", "nan", "pd_na"])
def test_missing_resident_parking_district_writes_no_attribute(missing):
    xml = _write_one_person([{}], person_fields=pop.PERSON_FIELDS + ["resident_parking_district"],
                            person_overrides={"resident_parking_district": missing})
    assert "residentParkingDistrict" not in xml
    assert _person_attributes(_parse(xml), 1) == _person_attributes(_parse(_write_one_person([{}])), 1)


@pytest.mark.parametrize("value", [5, 3.0, "", "nan", "None", "<NA>"], ids=["int", "float", "empty", "nan_text",
                                                                              "none_text", "na_text"])
@pytest.mark.parametrize("column", ["parking_district", "resident_parking_district"])
def test_a_malformed_district_id_raises_instead_of_becoming_a_district_id(column, value):
    """A district id is a non-empty string of the district layer; a number, an empty text or the text form of a missing
    value (str(np.nan), str(None), str(pd.NA): what a broken join leaves) would name a district the layer does not
    have, and the Java side would reject it at the first priced stay."""
    with pytest.raises(ValueError, match=column):
        if column == "parking_district":
            _write_one_person([{"purpose": "work", "parking_zone": None, "parking_free": False,
                                "parking_district": value}], activity_fields=ALL_PARKING_FIELDS)
        else:
            _write_one_person([{}], person_fields=pop.PERSON_FIELDS + [column], person_overrides={column: value})


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
    assert pop.OPTIONAL_ACTIVITY_FIELDS == ["parking_zone", "parking_free", "parking_district"]
    columns = ["parking_district", "parking_free"] + pop.ACTIVITY_FIELDS + ["activity_index", "parking_zone"]
    frame = pd.DataFrame({field: [0] for field in columns})
    assert pop.effective_activity_fields(frame) == ALL_PARKING_FIELDS
    # the columns of one layer do not pull in the other's
    only_zone = pd.DataFrame({field: [0] for field in pop.ACTIVITY_FIELDS + ["parking_zone"]})
    assert pop.effective_activity_fields(only_zone) == pop.ACTIVITY_FIELDS + ["parking_zone"]


def test_the_resident_parking_columns_are_optional_person_fields():
    assert "resident_parking_zone" in pop.OPTIONAL_PERSON_FIELDS
    assert "resident_parking_district" in pop.OPTIONAL_PERSON_FIELDS
    columns = pop.PERSON_FIELDS + ["resident_parking_district", "resident_parking_zone"]
    frame = pd.DataFrame({field: [0] for field in columns})
    assert pop.effective_person_fields(frame) == pop.PERSON_FIELDS + ["resident_parking_zone",
                                                                      "resident_parking_district"]


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


def test_write_population_carries_the_district_columns_to_the_plans(tmp_path):
    """Both layers on the same frames: a district without a zone and a zone without a district are ordinary."""
    persons, activities, locations, trips, vehicles = _two_person_frames()
    activities = activities.assign(
        parking_zone=pd.Series(["fx_res_a", "fx_bs_ia", "fx_res_a", np.nan, "fx_bs_ia", np.nan], dtype=object),
        parking_free=[False, True, False, False, False, False],
        parking_district=pd.Series(["fx_district_a", np.nan, "fx_district_a", np.nan, "fx_district_b", np.nan],
                                   dtype=object))
    persons = persons.assign(resident_parking_zone=pd.Series(["fx_res_a", np.nan], dtype=object),
                             resident_parking_district=pd.Series(["fx_district_a", np.nan], dtype=object))

    root = _parse(_write_frames(tmp_path, persons, activities, locations, trips, vehicles))

    assert _person_attributes(root, 1)["residentParkingDistrict"] == ("java.lang.String", "fx_district_a")
    assert "residentParkingDistrict" not in _person_attributes(root, 2)
    assert _activity_attributes(root, 1) == [
        {"parkingZone": ("java.lang.String", "fx_res_a"), "parkingDistrict": ("java.lang.String", "fx_district_a")},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"), "parkingFree": ("java.lang.Boolean", "true")},
        {"parkingZone": ("java.lang.String", "fx_res_a"), "parkingDistrict": ("java.lang.String", "fx_district_a")},
    ]
    assert _activity_attributes(root, 2) == [
        {}, {"parkingZone": ("java.lang.String", "fx_bs_ia"), "parkingDistrict": ("java.lang.String", "fx_district_b")},
        {}]


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
        parking_zone=pd.Series([np.nan] * len(activities), dtype=object), parking_free=False,
        parking_district=pd.Series([np.nan] * len(activities), dtype=object))
    persons = persons.assign(
        resident_parking_zone=pd.Series([np.nan] * len(persons), dtype=object),
        resident_parking_district=pd.Series([np.nan] * len(persons), dtype=object))
    assert _write_frames(tmp_path / "missing", persons, activities, locations, trips,
                         vehicles) == golden


# --------------------------------------------------------------------------- plain writer: configure

def test_plain_writer_rejects_parking_zones_at_configure_time():
    """Without the wrapper no plan element would carry a parkingZone, while the preparation still
    writes the tariff model and the braunschweigParking module: the run would price no stay."""
    with pytest.raises(ValueError) as error:
        pop.configure(ConfigurationContext({"parking_zones_enabled": True}))
    assert "parking_zones_enabled" in str(error.value)
    assert "matsim.scenario.population: braunschweig.matsim.scenario.population" in str(error.value)


@pytest.mark.parametrize("values", [{}, {"parking_zones_enabled": False}], ids=["absent", "false"])
def test_plain_writer_only_declares_the_flag_when_it_is_off(values):
    context = ConfigurationContext(values)
    pop.configure(context)
    assert context.required_config["parking_zones_enabled"] is False


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


def test_configure_declares_the_proxy_mapping_and_the_campus_share_with_the_off_defaults_when_enabled():
    recorder = _declare(parking_zones_enabled=True)
    assert recorder.config_keys["parking_free_share_proxy_classes"] == {}
    assert recorder.config_keys["parking_campus_free_share"] == 0.0


def test_the_wrapper_defaults_without_the_two_options_pass_the_v1_draw_to_the_attach_module(tmp_path, monkeypatch):
    calls = {}
    _inject_attach(monkeypatch, _stub_attach_module(calls))
    POP.execute(_wrapper_context(tmp_path))
    [draw_call] = calls["draw_parking_free"]
    assert draw_call["proxy_classes"] == {} and draw_call["campus_free_share"] == 0.0


@pytest.mark.parametrize("mapping", [{"03103": "bs_zentrum"}, {}, {"03103": "bs_zentrum", "03102": "bs_zentrum"}],
                         ids=["wolfsburg", "empty", "two_classes"])
def test_configure_accepts_a_valid_proxy_mapping(mapping):
    recorder = _declare(parking_zones_enabled=True, parking_free_share_proxy_classes=mapping)
    assert ZONES_STAGE_NAME in recorder.stages


@pytest.mark.parametrize("mapping", [None, "03103", {3103: "bs_zentrum"}, {"03103": "03103"},
                                     {"03103": "bs_zentrum", "bs_zentrum": "03102"}],
                         ids=["none", "text", "int_key", "self_mapping", "chain"])
def test_configure_rejects_an_invalid_proxy_mapping(mapping):
    with pytest.raises(ValueError, match="parking_free_share_proxy_classes"):
        _declare(parking_zones_enabled=True, parking_free_share_proxy_classes=mapping)


@pytest.mark.parametrize("share", [0.0, 0.2, 1.0, 0, 1])
def test_configure_accepts_a_campus_free_share_in_the_closed_unit_interval(share):
    assert ZONES_STAGE_NAME in _declare(parking_zones_enabled=True, parking_campus_free_share=share).stages


@pytest.mark.parametrize("share", [-0.1, 1.1, 20, float("nan"), "0.2", True, None], ids=str)
def test_configure_rejects_a_campus_free_share_outside_zero_to_one(share):
    with pytest.raises(ValueError, match="parking_campus_free_share"):
        _declare(parking_zones_enabled=True, parking_campus_free_share=share)


def test_configure_off_does_not_validate_or_declare_the_two_options():
    # The OFF path (the legacy ring and every fixture configuration) must not even look at them.
    recorder = _declare(parking_free_share_proxy_classes="not a mapping", parking_campus_free_share=7)
    assert "parking_free_share_proxy_classes" not in recorder.config_keys
    assert "parking_campus_free_share" not in recorder.config_keys


def test_configure_off_declares_neither_the_zones_stage_nor_the_shift():
    recorder = _declare()
    assert recorder.config_keys["parking_zones_enabled"] is False
    assert ZONES_STAGE_NAME not in recorder.stages
    assert "parking_workplace_free_share_shift" not in recorder.config_keys
    assert "random_seed" not in recorder.config_keys


def test_configure_accepts_the_legacy_ring_alone():
    recorder = _declare(enable_urban_parking=True)
    assert ZONES_STAGE_NAME not in recorder.stages


@pytest.mark.parametrize("shift", [-1.0, 1.0, -1, 1], ids=["minus_one", "plus_one", "minus_one_int", "plus_one_int"])
def test_configure_accepts_the_free_share_shift_boundaries(shift):
    # The valid range is the closed interval [-1, 1]: both ends are legitimate sensitivity arms.
    recorder = _declare(parking_zones_enabled=True, parking_workplace_free_share_shift=shift)
    assert ZONES_STAGE_NAME in recorder.stages


@pytest.mark.parametrize("shift", [math.nextafter(1.0, 2.0), math.nextafter(-1.0, -2.0), 1.5, -1.01, float("nan"),
                                   "0.1", True],
                         ids=["just_above_one", "just_below_minus_one", "1.5", "-1.01", "nan", "text", "bool"])
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
        # The resident districts (spec Amendment C3) overlay the zones: fx_district_a covers the home of resident 1
        # and the work places of resident 1 and of the in-commuter, fx_district_b the home and the shop of resident 2.
        "districts": gpd.GeoDataFrame({
            "district_id": ["fx_district_a", "fx_district_b"],
            "geometry": [MultiPolygon([box(602900.0, 5790900.0, 603600.0, 5791600.0),
                                       box(605050.0, 5790150.0, 605170.0, 5790270.0)]),
                         MultiPolygon([box(606900.0, 5791900.0, 607100.0, 5792100.0),
                                       box(605180.0, 5790280.0, 605220.0, 5790320.0)])],
        }, crs=CRS),
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
    # Resident 1 drives to work and walks home: only the trip INTO the work activity is a car trip, which makes the
    # pairing of a trip with the activity it leads to observable in the own-district coverage line.
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "mode": ["car", "walk", "walk", "walk"],
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
                     shift=0.25, proxy_classes=None, campus_free_share=None):
    config = dict(_OTHER_FEATURES_OFF, cordon_enabled=True, enable_urban_parking=False,
                  write_income_eur=False, parking_zones_enabled=parking_enabled)
    if parking_enabled:
        config.update(parking_workplace_free_share_shift=shift, random_seed=RANDOM_SEED)
        # Left out of the configuration unless given: the wrapper then runs on its code defaults (both options off).
        if proxy_classes is not None:
            config["parking_free_share_proxy_classes"] = proxy_classes
        if campus_free_share is not None:
            config["parking_campus_free_share"] = campus_free_share
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

    def draw_parking_free(activities, tariffs, workplace_shares, random_seed, shift=0.0, proxy_classes=None,
                          campus_free_share=0.0):
        calls.setdefault("draw_parking_free", []).append(
            {"activities": activities.copy(), "tariffs": tariffs,
             "workplace_shares": workplace_shares, "random_seed": random_seed, "shift": shift,
             "proxy_classes": proxy_classes, "campus_free_share": campus_free_share})
        zone_type = activities["parking_zone"].map(tariffs.set_index("zone_id")["zone_type"])
        eligible = (activities["purpose"].isin(("work", "education"))
                    & zone_type.isin(("street_paid", "resident_zone")))
        result = activities.copy()
        result["parking_free"] = eligible.to_numpy(dtype=bool)
        return result

    def attach_parking_districts(activities, locations, districts):
        calls.setdefault("attach_parking_districts", []).append(
            {"activities": activities.copy(), "locations": locations.copy(), "districts": districts})
        merged = activities.merge(locations[["person_id", "activity_index", "geometry"]],
                                  on=["person_id", "activity_index"], how="left")
        district_ids = [next((district_id for district_id, polygon in zip(districts["district_id"], districts.geometry)
                              if polygon.contains(point)), np.nan) for point in merged["geometry"]]
        result = activities.copy()
        result["parking_district"] = pd.Series(district_ids, index=result.index, dtype=object)
        return result

    def attach_resident_districts(persons, activities):
        calls.setdefault("attach_resident_districts", []).append(
            {"persons": persons.copy(), "activities": activities.copy()})
        homes = activities[activities["purpose"] == "home"]
        home_district = homes.drop_duplicates("person_id").set_index("person_id")["parking_district"]
        result = persons.copy()
        result["resident_parking_district"] = result["person_id"].map(home_district).astype(object)
        return result

    module.attach_parking_zones = attach_parking_zones
    module.attach_resident_zones = attach_resident_zones
    module.draw_parking_free = draw_parking_free
    module.attach_parking_districts = attach_parking_districts
    module.attach_resident_districts = attach_resident_districts
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
    context = _wrapper_context(tmp_path, proxy_classes={"03103": "bs_zentrum"}, campus_free_share=0.2)
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
    # ... the two options of parking cost zones v2 (spec Amendment D5, D6) as configured, with the types the draw expects
    assert draw_call["proxy_classes"] == {"03103": "bs_zentrum"}
    assert draw_call["campus_free_share"] == 0.2 and isinstance(draw_call["campus_free_share"], float)
    # The resident districts (spec Amendment C3) get the same merged frames and the release's own layer.
    [districts_call] = calls["attach_parking_districts"]
    assert set(districts_call["activities"]["person_id"]) == {1, 2, INCOMMUTER_ID}
    assert len(districts_call["activities"]) == 9
    assert set(districts_call["locations"]["person_id"]) == {1, 2, INCOMMUTER_ID}
    assert districts_call["districts"] is release["districts"]
    [resident_district_call] = calls["attach_resident_districts"]
    assert set(resident_district_call["persons"]["person_id"]) == {1, 2, INCOMMUTER_ID}
    assert "parking_district" in resident_district_call["activities"].columns

    # ... and the columns they returned reach the plans, the in-commuter's included.
    root = _read_plans(tmp_path)
    district_a = ("java.lang.String", "fx_district_a")
    district_b = ("java.lang.String", "fx_district_b")
    assert _activity_attributes(root, INCOMMUTER_ID) == [
        {},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"),
         "parkingFree": ("java.lang.Boolean", "true"), "parkingDistrict": district_a},
        {},
    ]
    assert "residentParkingZone" not in _person_attributes(root, INCOMMUTER_ID)
    assert "residentParkingDistrict" not in _person_attributes(root, INCOMMUTER_ID)
    assert _person_attributes(root, 1)["residentParkingZone"] == ("java.lang.String", "fx_res_a")
    assert _person_attributes(root, 1)["residentParkingDistrict"] == district_a
    assert _person_attributes(root, 2)["residentParkingDistrict"] == district_b
    assert _activity_attributes(root, 1) == [
        {"parkingZone": ("java.lang.String", "fx_res_a"), "parkingDistrict": district_a},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"),
         "parkingFree": ("java.lang.Boolean", "true"), "parkingDistrict": district_a},
        {"parkingZone": ("java.lang.String", "fx_res_a"), "parkingDistrict": district_a},
    ]
    assert _activity_attributes(root, 2) == [
        {"parkingDistrict": district_b},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"), "parkingDistrict": district_b},
        {"parkingDistrict": district_b}]

    # One coverage line for the whole population (fallback transparency).
    coverage = [record.getMessage() for record in caplog.records
                if record.getMessage().startswith("[parking population]")
                and "parkingZone on" in record.getMessage()]
    assert len(coverage) == 1
    assert "5/9 activities" in coverage[0]
    assert "in-commuter activities with a parkingZone: 1/3" in coverage[0]
    assert "parkingFree=true on 2 activities" in coverage[0]
    assert "1/3 persons" in coverage[0]

    # The districts: one more line, with the own-district stays of assumption R2. Resident 1 works in district A (own,
    # reached by car in the initial plan), resident 2 shops in district B (own, reached on foot), the in-commuter works
    # in district A but has no resident district.
    [district_line] = [record.getMessage() for record in caplog.records
                       if record.getMessage().startswith("[parking population]")
                       and "parkingDistrict on" in record.getMessage()]
    assert "parkingDistrict on 7/9 activities (77.8%)" in district_line
    assert "in-commuter activities with a parkingDistrict: 1/3" in district_line
    assert "residentParkingDistrict on 2/3 persons (66.7%)" in district_line
    assert "2/3 non-home activities lie in the district of the person's home" in district_line
    assert "reached by car in the initial plan: 1" in district_line


def test_execute_off_never_touches_the_attach_module(tmp_path, monkeypatch):
    module = types.ModuleType(ATTACH_MODULE_NAME)

    def refuse(*_args, **_kwargs):
        raise AssertionError("attach must not be called with parking_zones_enabled false")

    module.attach_parking_zones = module.attach_resident_zones = refuse
    module.draw_parking_free = refuse
    module.attach_parking_districts = module.attach_resident_districts = refuse
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


def test_execute_rejects_a_district_result_that_changes_the_row_count(tmp_path, monkeypatch):
    module = _stub_attach_module({})
    original = module.attach_parking_districts
    module.attach_parking_districts = lambda activities, locations, districts: original(
        activities, locations, districts).iloc[:-1]
    _inject_attach(monkeypatch, module)
    with pytest.raises(ValueError, match="attach_parking_districts returned 8 rows for 9"):
        POP.execute(_wrapper_context(tmp_path))


def _relabel_first_row(function, key):
    """``function`` with the ``key`` of the first returned row replaced: same row count, changed key set."""
    def relabelled(frame, *args, **kwargs):
        result = function(frame, *args, **kwargs)
        result.iloc[0, result.columns.get_loc(key)] = 777777
        return result
    return relabelled


@pytest.mark.parametrize("producer, key", [("attach_parking_zones", "activity_index"),
                                           ("attach_parking_zones", "person_id"),
                                           ("attach_resident_zones", "person_id"),
                                           ("draw_parking_free", "activity_index"),
                                           ("attach_parking_districts", "activity_index"),
                                           ("attach_resident_districts", "person_id")])
def test_execute_rejects_an_attach_result_that_changes_a_key(tmp_path, monkeypatch, producer, key):
    """Same row count, changed keys: a duplicating plus dropping (or mismatching) join that a count check alone
    cannot see; it would attach the parking attributes to the wrong plan elements."""
    module = _stub_attach_module({})
    setattr(module, producer, _relabel_first_row(getattr(module, producer), key))
    _inject_attach(monkeypatch, module)
    with pytest.raises(ValueError, match=f"{producer} changed the key column {key!r} in 1 of "):
        POP.execute(_wrapper_context(tmp_path))


def test_execute_rejects_an_attach_result_without_the_added_column(tmp_path, monkeypatch):
    module = _stub_attach_module({})
    module.draw_parking_free = lambda activities, tariffs, workplace_shares, random_seed, **options: activities.copy()
    _inject_attach(monkeypatch, module)
    with pytest.raises(ValueError, match="draw_parking_free did not add the 'parking_free' column"):
        POP.execute(_wrapper_context(tmp_path))


@pytest.mark.parametrize("producer, column", [("attach_parking_districts", "parking_district"),
                                              ("attach_resident_districts", "resident_parking_district")])
def test_execute_rejects_a_district_result_without_the_added_column(tmp_path, monkeypatch, producer, column):
    module = _stub_attach_module({})
    original = getattr(module, producer)
    setattr(module, producer, lambda frame, *arguments: original(frame, *arguments).drop(columns=column))
    _inject_attach(monkeypatch, module)
    with pytest.raises(ValueError, match=f"{producer} did not add the '{column}' column"):
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


def _fixture_release():
    """The Task 2 fixture release of ``tests/fixtures/parking`` (pins arithmetic, not truth) with a small
    shares frame: bs_zentrum and bs_innenbereich park free with share 1.0, the other classes never."""
    shares = pd.DataFrame({
        "workplace_class": ["bs_zentrum", "bs_innenbereich", "bs_outer", "03102", "03103", "03157", "total"],
        "level": ["class"] * 6 + ["total"],
        "share_free_total": [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.9],
    })
    return {"zones": pz.load_zone_polygons(FIXTURES / "parking_zones_fixture.geojson"),
            "tariffs": pz.load_tariffs(FIXTURES / "parking_tariffs_fixture.csv"),
            "workplace_shares": shares, "coverage_register": pd.DataFrame(),
            "districts": pz.load_resident_districts(FIXTURES / "parking_resident_districts_fixture.geojson"),
            "sources": []}


def test_real_attach_module_writes_the_parking_attributes_of_the_fixture_release(tmp_path, caplog):
    """The wrapper with the REAL braunschweig.parking.attach on the fixture release: resident 1 lives in
    the resident zone fx_res_a and works in fx_bs_ia (class bs_zentrum, share 1.0); resident 2 lives
    outside every zone and shops in fx_sz; the in-commuter works on campus (fx_campus), which is zoned and drawn with
    the campus share, 0.0 here (the wrapper default), so it stays priced although its class parks free."""
    real_attach = importlib.import_module(ATTACH_MODULE_NAME)
    assert real_attach.PARKING_FREE_SEED_OFFSET == 7371  # the real module, not a stub left behind
    release = _fixture_release()
    centre = release["zones"].set_index("zone_id").geometry.centroid
    context = _wrapper_context(tmp_path, incommuter_work=centre["fx_campus"], shift=0.0)
    context._stages[ZONES_STAGE_NAME] = release
    resident_locations = context._stages["synthesis.population.spatial.locations"]
    moved = {(1, 0): centre["fx_res_a"], (1, 1): centre["fx_bs_ia"], (1, 2): centre["fx_res_a"],
             (2, 1): centre["fx_sz"]}
    context._stages["synthesis.population.spatial.locations"] = gpd.GeoDataFrame(
        resident_locations.drop(columns="geometry"),
        geometry=[moved.get((person_id, index), point) for person_id, index, point in zip(
            resident_locations["person_id"], resident_locations["activity_index"],
            resident_locations.geometry)], crs=CRS)

    with caplog.at_level(logging.INFO, logger=POP.__name__), \
            caplog.at_level(logging.INFO, logger=ATTACH_MODULE_NAME):
        POP.execute(context)

    root = _read_plans(tmp_path)
    assert _person_attributes(root, 1)["residentParkingZone"] == ("java.lang.String", "fx_res_a")
    # fx_bs_ia lies inside fx_district_a and fx_sz inside fx_district_sz_a (a second layer on top of the zones); the
    # homes and the campus lie in no district
    assert _activity_attributes(root, 1) == [
        {"parkingZone": ("java.lang.String", "fx_res_a")},
        {"parkingZone": ("java.lang.String", "fx_bs_ia"),
         "parkingFree": ("java.lang.Boolean", "true"), "parkingDistrict": ("java.lang.String", "fx_district_a")},
        {"parkingZone": ("java.lang.String", "fx_res_a")},
    ]
    assert "residentParkingZone" not in _person_attributes(root, 2)
    assert _activity_attributes(root, 2) == [
        {}, {"parkingZone": ("java.lang.String", "fx_sz"), "parkingDistrict": ("java.lang.String", "fx_district_sz_a")},
        {}]
    assert "residentParkingZone" not in _person_attributes(root, INCOMMUTER_ID)
    assert _activity_attributes(root, INCOMMUTER_ID) == [
        {}, {"parkingZone": ("java.lang.String", "fx_campus")}, {}]
    assert not [name for person in (1, 2, INCOMMUTER_ID) for name in _person_attributes(root, person)
                if name == "residentParkingDistrict"]

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("[parking] activities in zones: 5/9") for message in messages)
    [coverage] = [message for message in messages if "parkingZone on" in message]
    assert "5/9 activities" in coverage and "in-commuter activities with a parkingZone: 1/3" in coverage
    assert "parkingFree=true on 1 activities" in coverage and "1/3 persons" in coverage
    assert any(message.startswith("[parking] activities in resident districts: 2/9") for message in messages)
    assert any(message.startswith("[parking] persons with a resident parking district: 0/3") for message in messages)


def test_real_attach_module_frees_the_campus_in_commuter_with_the_campus_share_one(tmp_path):
    """Same run as above on the campus, with ``parking_campus_free_share`` 1.0 (ASSUMPTION C2 at its upper edge): the
    in-commuter who works on campus carries parkingFree; the campus zone is accepted by the plan writer."""
    importlib.import_module(ATTACH_MODULE_NAME)
    release = _fixture_release()
    centre = release["zones"].set_index("zone_id").geometry.centroid
    context = _wrapper_context(tmp_path, incommuter_work=centre["fx_campus"], shift=0.0, campus_free_share=1.0)
    context._stages[ZONES_STAGE_NAME] = release

    POP.execute(context)

    assert _activity_attributes(_read_plans(tmp_path), INCOMMUTER_ID) == [
        {}, {"parkingZone": ("java.lang.String", "fx_campus"), "parkingFree": ("java.lang.Boolean", "true")}, {}]


def test_real_attach_module_writes_the_district_attributes_of_the_fixture_release(tmp_path, caplog):
    """The wrapper with the REAL attach module and the fixture districts (spec Amendment C3). Resident 1 lives inside
    fx_district_a but in no zone, and works in fx_bs_ia, which lies in the same district (an own-district stay,
    reached by car); resident 2 lives in fx_district_b and shops in fx_sz, which lies in another district; the
    in-commuter works in fx_bs_ia (district fx_district_a) and has no resident district."""
    importlib.import_module(ATTACH_MODULE_NAME)
    release = _fixture_release()
    centre = release["zones"].set_index("zone_id").geometry.centroid
    context = _wrapper_context(tmp_path, incommuter_work=centre["fx_bs_ia"], shift=0.0)
    context._stages[ZONES_STAGE_NAME] = release
    resident_locations = context._stages["synthesis.population.spatial.locations"]
    moved = {(1, 0): Point(602950.0, 5790050.0), (1, 1): centre["fx_bs_ia"], (1, 2): Point(602950.0, 5790050.0),
             (2, 0): Point(603650.0, 5790250.0), (2, 1): centre["fx_sz"], (2, 2): Point(603650.0, 5790250.0)}
    context._stages["synthesis.population.spatial.locations"] = gpd.GeoDataFrame(
        resident_locations.drop(columns="geometry"),
        geometry=[moved.get((person_id, index), point) for person_id, index, point in zip(
            resident_locations["person_id"], resident_locations["activity_index"],
            resident_locations.geometry)], crs=CRS)

    with caplog.at_level(logging.INFO, logger=POP.__name__), \
            caplog.at_level(logging.INFO, logger=ATTACH_MODULE_NAME):
        POP.execute(context)

    root = _read_plans(tmp_path)
    district_a = ("java.lang.String", "fx_district_a")
    district_b = ("java.lang.String", "fx_district_b")
    assert _person_attributes(root, 1)["residentParkingDistrict"] == district_a
    assert _person_attributes(root, 2)["residentParkingDistrict"] == district_b
    assert "residentParkingDistrict" not in _person_attributes(root, INCOMMUTER_ID)
    assert "residentParkingZone" not in _person_attributes(root, 1)  # the home lies in a district, not in a zone
    assert [attributes.get("parkingDistrict") for attributes in _activity_attributes(root, 1)] == [
        district_a, district_a, district_a]
    assert [attributes.get("parkingDistrict") for attributes in _activity_attributes(root, 2)] == [
        district_b, ("java.lang.String", "fx_district_sz_a"), district_b]
    assert [attributes.get("parkingDistrict") for attributes in _activity_attributes(root, INCOMMUTER_ID)] == [
        None, district_a, None]

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("[parking] activities in resident districts: 7/9") for message in messages)
    assert any(message.startswith("[parking] persons with a resident parking district: 2/3") for message in messages)
    [line] = [message for message in messages if "parkingDistrict on" in message]
    assert "parkingDistrict on 7/9 activities" in line and "in-commuter activities with a parkingDistrict: 1/3" in line
    assert "residentParkingDistrict on 2/3 persons" in line
    assert "1/3 non-home activities lie in the district of the person's home" in line
    assert "reached by car in the initial plan: 1" in line


# --------------------------------------------------------------------------- wrapper: validate

def test_validate_covers_the_attach_module_by_name():
    assert ATTACH_MODULE_NAME in POP._DEFERRED_HELPER_MODULE_NAMES


def test_validate_covers_the_zone_assignment_module_by_name():
    """attach_parking_zones delegates the point-in-polygon test to braunschweig.parking.zones.assign_zones,
    so that module decides the parkingZone values as much as the attach module does."""
    assert "braunschweig.parking.zones" in POP._DEFERRED_HELPER_MODULE_NAMES


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


def test_validate_raises_when_a_deferred_module_is_absent(monkeypatch):
    """No skip for an absent deferred module (the temporary guard of the parallel-task phase is gone):
    like braunschweig.matsim.simulation.prepare, the token refuses rather than reusing a stale
    plans.xml.gz."""
    absent = "braunschweig.parking.module_absent_for_the_token_test"
    monkeypatch.setattr(POP, "_DEFERRED_HELPER_MODULE_NAMES", (absent,))
    with pytest.raises(RuntimeError, match=absent):
        POP.validate(None)


# --------------------------------------------------------------------------- pre-change literals
#
# Generated from base commit 1fe22664 (the writer BEFORE the parking attributes) by running
# _write_frames(..., *_two_person_frames(), enable_urban_parking=False/True) on that commit.
# Never regenerate them from the current writer just to make a failing pin pass: that would turn the
# byte-identity pin into a tautology.
#
# The one legitimate update is a deliberate change of the OFF or LEGACY plans themselves, i.e. a writer
# change that is not about the parking attributes (for example a new person attribute every plan gets),
# reviewed as such. Then: regenerate both literals with the call above on the commit that makes that change,
# check that the diff of the literals is exactly the intended change and carries no parking attribute,
# and replace the commit id 1fe22664 above (and in the module docstring) with that commit's id.

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
