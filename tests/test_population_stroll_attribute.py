"""The out-and-back stroll marker reaches the MATSim plans as ``outAndBackStroll`` (issue #679).

The secondary location stage marks out-and-back stroll activities with the bool column
``is_out_and_back_stroll`` (only while ``secondary_loop_zero_displacement`` is on). The Java trip
constraint of the stroll rule reads the activity attribute ``outAndBackStroll``
(java.lang.Boolean ``true``). Covered here:

* **The day-view location join** (``braunschweig.synthesis.commute_day.spatial_locations_day``)
  carries the column from the secondary stage onto every located activity (False elsewhere), raises
  when a marked row matches no activity, and returns the frame unchanged with the switch off.
* **The vendored writer** (``matsim.scenario.population``) writes the attribute only where True;
  frames without the column are written byte-identically to frames whose column is all False.
* **The regional wrapper** (``braunschweig.matsim.scenario.population``): injected in-commuters,
  whose location rows carry no column, get no attribute.
"""
from __future__ import annotations

import contextlib
import gzip
import sys
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.scenario.population as pop  # noqa: E402
from braunschweig.matsim.scenario import population as POP  # noqa: E402
from braunschweig.popsim.stage import config_keys  # noqa: E402
from braunschweig.synthesis.commute_day import spatial_locations_day as LOCATIONS  # noqa: E402
from braunschweig.synthesis.locations import secondary_chainsolvers  # noqa: E402
from tests.test_incommuter_subpopulation import (  # noqa: E402
    SVB_ID, STUDENT_ID, _ConfigureRecorder, _ExecuteContext, _person_values, _wrapper_stages)

COLUMN = "is_out_and_back_stroll"
ATTRIBUTE = '<attribute name="outAndBackStroll" class="java.lang.Boolean">true</attribute>'
SECONDARY_STAGE = "synthesis.population.spatial.secondary.locations"
LOOP_KEY = config_keys.KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT


def test_column_and_attribute_names_match_their_producer_and_the_java_rule():
    assert (secondary_chainsolvers.OUT_AND_BACK_STROLL_COLUMN == LOCATIONS.OUT_AND_BACK_STROLL_COLUMN
            == pop.OUT_AND_BACK_STROLL_COLUMN == COLUMN)
    assert pop.OUT_AND_BACK_STROLL_ATTRIBUTE == "outAndBackStroll"
    assert LOCATIONS.KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT == LOOP_KEY
    assert (LOCATIONS.DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT
            is config_keys.DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT)


# --------------------------------------------------------------------------- writer

class _Context:
    @staticmethod
    def config(key):
        return {"remode_carless_car_legs": False}[key]

    @contextlib.contextmanager
    def progress(self, **_kwargs):
        class _Progress:
            def update(self):
                pass
        yield _Progress()


def _raw_frames(stroll_flags):
    """One walker home -> leisure -> leisure -> home; ``stroll_flags`` None leaves the column out."""
    persons = pd.DataFrame([_person_values()])
    activities = pd.DataFrame({
        "person_id": [1] * 4, "activity_index": [0, 1, 2, 3],
        "purpose": ["home", "leisure", "leisure", "home"],
        "start_time": [np.nan, 36000.0, 40000.0, 44000.0], "end_time": [32400.0, 39000.0, 43000.0, np.nan]})
    locations = gpd.GeoDataFrame({
        "person_id": [1] * 4, "activity_index": [0, 1, 2, 3], "location_id": [-1, "l1", "l2", -1],
        "geometry": [Point(0.0, 0.0), Point(500.0, 0.0), Point(900.0, 0.0), Point(0.0, 0.0)]})
    if stroll_flags is not None:
        locations[COLUMN] = stroll_flags
    trips = pd.DataFrame({"person_id": [1] * 3, "trip_index": [0, 1, 2], "mode": ["walk"] * 3,
                          "departure_time": [32400.0, 39000.0, 43000.0],
                          "arrival_time": [36000.0, 40000.0, 44000.0]})
    vehicles = pd.DataFrame({"owner_id": [1], "vehicle_id": ["1:car"], "mode": ["car"]})
    return persons, activities, locations, trips, vehicles


def _write(tmp_path, stroll_flags, name="population.xml.gz"):
    df_persons, df_activities, df_trips, df_vehicles = pop.prepare_frames(*_raw_frames(stroll_flags))
    output = tmp_path / name
    pop.write_population(str(output), df_persons, df_activities, df_trips, df_vehicles,
                         enable_urban_parking=False, context=_Context())
    with gzip.open(output, "rb") as handle:
        return handle.read()


def _activity_attributes(xml_bytes):
    root = ElementTree.fromstring(xml_bytes)
    return [{a.get("name"): (a.get("class"), a.text) for a in activity.findall("./attributes/attribute")}
            for activity in root.findall("./person/plan/activity")]


def test_stroll_activity_carries_the_attribute(tmp_path):
    xml = _write(tmp_path, [False, True, False, False])
    assert _activity_attributes(xml)[1] == {"outAndBackStroll": ("java.lang.Boolean", "true")}
    assert xml.decode("utf-8").count(ATTRIBUTE) == 1


def test_other_activities_carry_no_stroll_attribute(tmp_path):
    xml = _write(tmp_path, [False, True, False, False])
    attributes = _activity_attributes(xml)
    assert [attributes[i] for i in (0, 2, 3)] == [{}, {}, {}]


def test_frames_without_is_stroll_write_identical_xml(tmp_path):
    without_column = _write(tmp_path, None, "without.xml.gz")
    all_false = _write(tmp_path, [False] * 4, "all_false.xml.gz")
    assert b"outAndBackStroll" not in without_column
    assert without_column == all_false


@pytest.mark.parametrize("value", [np.nan, None, "true", 1])
def test_a_non_boolean_stroll_flag_raises(tmp_path, value):
    with pytest.raises(ValueError, match=COLUMN):
        _write(tmp_path, pd.Series([False, value, False, False], dtype=object))


# --------------------------------------------------------------------------- wrapper: injected agents

def _run_population_wrapper(tmp_path, stroll_flags, loop_switch=True):
    """Run the regional writer wrapper with the cordon on; ``stroll_flags`` None leaves the resident's
    location frame without the stroll column (the in-commuter frames never carry it)."""
    stages = _wrapper_stages()
    if stroll_flags is not None:
        locations = stages["synthesis.population.spatial.locations"].copy()
        locations[COLUMN] = stroll_flags
        stages["synthesis.population.spatial.locations"] = locations
    config = {"cordon_enabled": True, "commute_day_state_enabled": False, "day_absence_enabled": False,
              "enable_urban_parking": False, "write_income_eur": False, "parking_zones_enabled": False,
              "remode_carless_car_legs": False, LOOP_KEY: loop_switch}
    declared = _ConfigureRecorder(config)
    POP.configure(declared)
    POP.execute(_ExecuteContext(declared, stages, config, tmp_path))
    with gzip.open(tmp_path / "population.xml.gz", "rb") as handle:
        root = ElementTree.fromstring(handle.read())

    def stroll_flags_of(person_id):
        person = root.find(f"person[@id='{person_id}']")
        return [any(a.get("name") == "outAndBackStroll" for a in activity.findall("./attributes/attribute"))
                for activity in person.findall("./plan/activity")]
    return stroll_flags_of


def test_the_wrapper_pins_the_loop_switch_to_its_home():
    assert POP.KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT == LOOP_KEY
    assert POP.DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT is config_keys.DEFAULT_SECONDARY_LOOP_ZERO_DISPLACEMENT


def test_injected_agents_without_is_stroll_get_no_attribute(tmp_path):
    # the resident's work activity stands in for a stroll
    stroll_flags = _run_population_wrapper(tmp_path, [False, True, False])
    assert stroll_flags(1) == [False, True, False]
    assert stroll_flags(SVB_ID) == [False, False, False]
    assert stroll_flags(STUDENT_ID) == [False, False, False]


def test_the_switch_on_without_the_stroll_column_raises(tmp_path):
    """A pipeline whose location stage is the plain vendored join drops the marker."""
    with pytest.raises(ValueError, match=f"{LOOP_KEY}.*{COLUMN}.*location stage"):
        _run_population_wrapper(tmp_path, None)
    # with the switch off the same frames are written, without any stroll attribute
    stroll_flags = _run_population_wrapper(tmp_path, None, loop_switch=False)
    assert stroll_flags(1) == [False, False, False]


def test_zero_strolls_written_with_the_column_present_is_a_warning(tmp_path, caplog):
    with caplog.at_level("INFO", logger=pop.__name__):
        _write(tmp_path, [False] * 4)
    assert [r.levelname for r in caplog.records if "outAndBackStroll written" in r.getMessage()] == ["WARNING"]
    caplog.clear()
    with caplog.at_level("INFO", logger=pop.__name__):
        _write(tmp_path, [False, True, False, False])
    assert [r.levelname for r in caplog.records if "outAndBackStroll written" in r.getMessage()] == ["INFO"]


# --------------------------------------------------------------------------- day-view location join

def _join(monkeypatch, loop_switch, secondary):
    located = gpd.GeoDataFrame({"person_id": [1, 1, 1, 2], "activity_index": [0, 1, 2, 0],
                                "location_id": [-1, "l1", -1, -1],
                                "geometry": [Point(0.0, 0.0)] * 4})
    monkeypatch.setattr(LOCATIONS.base, "execute", lambda _context: located.copy())
    declared = _ConfigureRecorder({LOOP_KEY: loop_switch})
    LOCATIONS.configure(declared)
    assert LOOP_KEY in declared.config_keys
    context = _ExecuteContext(declared, {LOCATIONS.DAY_ACTIVITIES_STAGE: pd.DataFrame(),
                                         SECONDARY_STAGE: (secondary, None)},
                              {LOOP_KEY: loop_switch}, None)
    return located, LOCATIONS.execute(context)


def _secondary(rows):
    return pd.DataFrame(rows, columns=["person_id", "activity_index", COLUMN])


def test_the_location_join_carries_the_stroll_flag_and_false_elsewhere(monkeypatch):
    _, joined = _join(monkeypatch, True, _secondary([(1, 1, True), (2, 5, False)]))
    assert joined[COLUMN].dtype == bool
    assert joined[COLUMN].tolist() == [False, True, False, False]


def test_a_stroll_flag_without_a_located_activity_raises(monkeypatch):
    with pytest.raises(ValueError, match=r"1 .*\(2, 5\)"):
        _join(monkeypatch, True, _secondary([(1, 1, True), (2, 5, True)]))


def test_with_the_loop_switch_off_the_location_join_is_unchanged(monkeypatch):
    located, joined = _join(monkeypatch, False, _secondary([(1, 1, True)]))
    assert COLUMN not in joined.columns
    pd.testing.assert_frame_equal(pd.DataFrame(joined), pd.DataFrame(located))


@pytest.mark.parametrize("flags, level", [([True], "INFO"), ([False], "WARNING")])
def test_zero_marked_activities_in_the_location_join_is_a_warning(monkeypatch, caplog, flags, level):
    with caplog.at_level("INFO", logger=LOCATIONS.__name__):
        _join(monkeypatch, True, _secondary([(1, 1, flags[0])]))
    assert [r.levelname for r in caplog.records if "located activities" in r.getMessage()
            and COLUMN in r.getMessage()] == [level]
