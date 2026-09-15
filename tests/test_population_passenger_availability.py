"""MATSim export of the optional MiD passenger-availability attribute."""
from __future__ import annotations

import collections
import contextlib
import gzip
import io
import sys
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import pytest
import shapely.geometry as geo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matsim.scenario import population as pop  # noqa: E402
import matsim.writers as writers  # noqa: E402


LEGACY_XML_FIXTURE = Path(__file__).with_name("fixtures") / (
    "population_passenger_availability_legacy.xml")


class _StubWriter:
    def __init__(self):
        self.attributes = {}
        self.types = {}

    def start_person(self, *args, **kwargs): pass
    def start_attributes(self): pass
    def end_attributes(self): pass
    def end_person(self, *args, **kwargs): pass
    def start_plan(self, *args, **kwargs): pass
    def end_plan(self, *args, **kwargs): pass
    def add_attribute(self, key, java_type, value):
        self.attributes[key] = value
        self.types[key] = java_type
    def yes_no(self, value): return "yes" if value else "no"
    def location(self, *args, **kwargs): return None
    def add_activity(self, *args, **kwargs): pass
    def add_leg(self, *args, **kwargs): pass


def _home_activity():
    values = {field: 0 for field in pop.ACTIVITY_FIELDS}
    values.update(person_id=1, purpose="home", start_time=float("nan"),
                  end_time=float("nan"), location_id=-1, geometry=geo.Point(0, 0))
    return tuple(values[field] for field in pop.ACTIVITY_FIELDS)


def _person(fields, **overrides):
    values = {field: 0 for field in fields}
    values.update(
        person_id=1, household_id=1, household_income="2600-3000", sex="female",
        employed="yes", high_income=False, is_urban_resident=False,
        has_pt_subscription=False, has_license=True, pt_subscription_type="never_pt",
        household_income_eur=3000.0,
    )
    values.update(overrides)
    return tuple(values[field] for field in fields)


def test_writer_emits_valid_passenger_availability_as_java_string():
    fields = pop.PERSON_FIELDS + ["car_passenger_availability"]
    writer = _StubWriter()
    pop.add_person(writer, _person(fields, car_passenger_availability="some"),
                   [_home_activity()], [], [], person_fields=fields)
    assert writer.attributes["carPassengerAvailability"] == "some"
    assert writer.types["carPassengerAvailability"] == "java.lang.String"


@pytest.mark.parametrize("value", ["unknown", "ALL", 1, ["some"]])
def test_writer_rejects_malformed_present_passenger_availability(value):
    fields = pop.PERSON_FIELDS + ["car_passenger_availability"]
    with pytest.raises(ValueError, match="car_passenger_availability"):
        pop.add_person(_StubWriter(), _person(fields, car_passenger_availability=value),
                       [_home_activity()], [], [], person_fields=fields)


@pytest.mark.parametrize("missing_value", [None, float("nan"), pd.NA])
def test_writer_omits_missing_passenger_availability_and_counts_it(missing_value):
    fields = pop.PERSON_FIELDS + ["car_passenger_availability"]
    counter = collections.Counter()
    writer = _StubWriter()
    pop.add_person(writer, _person(fields, car_passenger_availability=missing_value),
                   [_home_activity()], [], [], person_fields=fields,
                   passenger_availability_omission_counter=counter)
    assert "carPassengerAvailability" not in writer.attributes
    assert counter["persons_without_passenger_availability"] == 1


class _Progress:
    def update(self): pass


class _Context:
    def config(self, key):
        return {"remode_carless_car_legs": False}[key]

    @contextlib.contextmanager
    def progress(self, **kwargs):
        yield _Progress()


def _frames(values):
    person_ids = list(range(1, len(values) + 1))
    persons = pd.DataFrame({field: [0] * len(values) for field in pop.PERSON_FIELDS})
    persons["person_id"] = person_ids
    persons["household_id"] = person_ids
    persons["household_income"] = "2600-3000"
    persons["sex"] = "female"
    persons["employed"] = "yes"
    persons["high_income"] = False
    persons["has_license"] = True
    persons["has_pt_subscription"] = False
    persons["pt_subscription_type"] = "never_pt"
    persons["car_passenger_availability"] = values
    persons = persons[pop.effective_person_fields(persons)]
    activities = pd.DataFrame({
        "person_id": person_ids, "start_time": [float("nan")] * len(values),
        "end_time": [float("nan")] * len(values), "purpose": ["home"] * len(values),
        "geometry": [geo.Point(0, 0)] * len(values), "location_id": [-1] * len(values),
    })
    return (persons, activities, pd.DataFrame({field: [] for field in pop.TRIP_FIELDS}),
            pd.DataFrame({field: [] for field in pop.VEHICLE_FIELDS}))


def test_population_writer_logs_aggregate_passenger_omission_rate(tmp_path, caplog):
    frames = _frames(["all", float("nan")])
    output = tmp_path / "population.xml.gz"
    with caplog.at_level("INFO", logger=pop.__name__):
        pop.write_population(str(output), *frames, enable_urban_parking=False, context=_Context())
    with gzip.open(output, "rb") as handle:
        root = ElementTree.parse(handle).getroot()
    attrs = {
        person.get("id"): {attr.get("name"): attr.text for attr in person.findall("./attributes/attribute")}
        for person in root.findall("person")
    }
    assert attrs["1"]["carPassengerAvailability"] == "all"
    assert "carPassengerAvailability" not in attrs["2"]
    assert any("passenger availability omitted for 1/2 persons" in record.getMessage()
               for record in caplog.records)


def test_absent_passenger_column_keeps_legacy_writer_field_order():
    assert pop.effective_person_fields(pd.DataFrame({field: [0] for field in pop.PERSON_FIELDS})) == pop.PERSON_FIELDS


def test_population_writer_without_passenger_column_matches_prechange_xml_bytes(tmp_path):
    """The committed literal was generated from base 1502e88e, not this writer."""
    persons, activities, trips, vehicles = _frames(["some"])
    persons = persons.drop(columns=["car_passenger_availability"])
    output = tmp_path / "population.xml.gz"

    pop.write_population(str(output), persons, activities, trips, vehicles,
                         enable_urban_parking=False, context=_Context())

    with gzip.open(output, "rb") as handle:
        actual = handle.read()
    assert actual == LEGACY_XML_FIXTURE.read_bytes()
