"""The loop marker and the diary length of a zero-length loop trip reach the plans and the trips output.

A stroll placed at the place it starts from (a loop, issue #679) makes the trip into it a zero-length
trip, and the trip back to the loop's start place too. The secondary location stage puts both diary
lengths on the loop row (tests/test_secondary_loops_stage.py); from there:

* **The day-view location join** (``braunschweig.synthesis.commute_day.spatial_locations_day``) writes
  ``is_loop_stroll`` and ``loop_trip_diary_distance_km`` on every located activity: the length of the
  trip INTO the activity, so the trip-back length lands on the activity after the loop, which must lie
  at the loop's place.
* **The vendored writer** (``matsim.scenario.population``) writes them as the activity attributes
  ``loopStroll`` and ``loopTripDiaryDistanceKm``; frames without the columns are written byte-identically.
* **The regional wrapper** gives the injected in-commuters neither attribute.
* **The trips output** (``synthesis.output``, ``<prefix>trips.csv``) carries both columns per trip, the
  values of the trip's destination activity, which the analyses read
  (``braunschweig.analysis.loop_trip_distances``).
"""
from __future__ import annotations

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
import synthesis.output as output  # noqa: E402
from braunschweig.analysis import loop_trip_distances  # noqa: E402
from braunschweig.matsim.scenario import population as POP  # noqa: E402
from braunschweig.synthesis.commute_day import spatial_locations_day as LOCATIONS  # noqa: E402
from braunschweig.synthesis.locations.secondary_chainsolvers import loops  # noqa: E402
from tests.test_incommuter_subpopulation import (  # noqa: E402
    SVB_ID, STUDENT_ID, _ConfigureRecorder, _ExecuteContext, _wrapper_stages)
from tests.test_population_stroll_attribute import (  # noqa: E402
    LOOP_KEY, SECONDARY_STAGE, _Context, _activity_attributes, _raw_frames)

FLAG, LENGTH = "is_loop_stroll", "loop_trip_diary_distance_km"


def test_the_loop_columns_have_one_name_from_the_stage_to_the_analyses():
    assert (LOCATIONS.LOOP_ORIGIN_COLUMN, LOCATIONS.LOOP_DIARY_DISTANCE_COLUMN,
            LOCATIONS.LOOP_RETURN_DIARY_DISTANCE_COLUMN) == (
        "loop_origin", loops.LOOP_DIARY_DISTANCE_COLUMN, loops.LOOP_RETURN_DIARY_DISTANCE_COLUMN)
    assert (LOCATIONS.LOOP_STROLL_COLUMN, LOCATIONS.LOOP_TRIP_DIARY_DISTANCE_COLUMN) == (FLAG, LENGTH)
    assert (pop.LOOP_STROLL_COLUMN, pop.LOOP_TRIP_DIARY_DISTANCE_COLUMN) == (FLAG, LENGTH)
    assert output.TRIP_LOOP_OUTPUT_COLUMNS == (FLAG, LENGTH)
    assert (loop_trip_distances.LOOP_FLAG_COLUMN,
            loop_trip_distances.LOOP_DIARY_DISTANCE_COLUMN) == (FLAG, LENGTH)


# --------------------------------------------------------------------------- day-view location join

WORK = Point(300.0, 0.0)


def _join(monkeypatch, secondary, *, located=None):
    """Person 1: home, work, a stroll from work (loop), back to work, home; person 2: home, a dog walk
    from home (loop), home."""
    if located is None:
        located = gpd.GeoDataFrame({
            "person_id": [1, 1, 1, 1, 1, 2, 2, 2], "activity_index": [0, 1, 2, 3, 4, 0, 1, 2],
            "location_id": [-1, "w1", "w1", "w1", -1, -1, "home_20", -1],
            "geometry": [Point(0.0, 0.0), WORK, WORK, WORK, Point(0.0, 0.0),
                         Point(9.0, 9.0), Point(9.0, 9.0), Point(9.0, 9.0)]})
    monkeypatch.setattr(LOCATIONS.base, "execute", lambda _context: located.copy())
    declared = _ConfigureRecorder({LOOP_KEY: True})
    LOCATIONS.configure(declared)
    context = _ExecuteContext(declared, {LOCATIONS.DAY_ACTIVITIES_STAGE: pd.DataFrame(),
                                         SECONDARY_STAGE: (secondary, None)}, {LOOP_KEY: True}, None)
    return LOCATIONS.execute(context)


def _secondary(back_km=6.6):
    return gpd.GeoDataFrame({
        "person_id": [1, 2], "activity_index": [2, 1], "location_id": ["w1", "home_20"],
        "is_out_and_back_stroll": [False, False], "loop_origin": ["work", "home"],
        loops.LOOP_DIARY_DISTANCE_COLUMN: [6.1, 4.7],
        loops.LOOP_RETURN_DIARY_DISTANCE_COLUMN: [back_km, np.nan],
        "geometry": [WORK, Point(9.0, 9.0)]})


def test_the_join_marks_the_loops_and_puts_each_length_on_the_activity_its_trip_leads_into(monkeypatch):
    joined = _join(monkeypatch, _secondary())

    assert joined[FLAG].dtype == bool
    assert joined[FLAG].tolist() == [False, False, True, False, False, False, True, False]
    # Into the loop (activity 2), back to work (activity 3, the trip back to the loop's place), and
    # into the dog walk; the invented trip home after it has no diary length.
    expected = [np.nan, np.nan, 6.1, 6.6, np.nan, np.nan, 4.7, np.nan]
    np.testing.assert_allclose(joined[LENGTH].to_numpy(dtype=float), expected)


def test_a_trip_back_that_does_not_end_at_the_loops_place_stops_the_join(monkeypatch):
    located = gpd.GeoDataFrame({
        "person_id": [1, 1, 1, 1, 1, 2, 2, 2], "activity_index": [0, 1, 2, 3, 4, 0, 1, 2],
        "location_id": [-1, "w1", "w1", "w2", -1, -1, "home_20", -1],
        "geometry": [Point(0.0, 0.0), WORK, WORK, Point(301.0, 0.0), Point(0.0, 0.0),
                     Point(9.0, 9.0), Point(9.0, 9.0), Point(9.0, 9.0)]})
    with pytest.raises(ValueError, match=r"1 trips back .*\(1, 2\)"):
        _join(monkeypatch, _secondary(), located=located)


@pytest.mark.parametrize("damage, message", [
    (lambda frame: frame.drop(columns=loops.LOOP_RETURN_DIARY_DISTANCE_COLUMN), "no .*loop_return"),
    (lambda frame: frame.assign(activity_index=[2, 7]), r"1 loop rows .*\(2, 7\)"),
])
def test_the_join_refuses_loop_rows_it_cannot_place(monkeypatch, damage, message):
    with pytest.raises(ValueError, match=message):
        _join(monkeypatch, damage(_secondary()))


# --------------------------------------------------------------------------- writer

def _write(tmp_path, loop_columns, name="population.xml.gz"):
    """The walker of tests/test_population_stroll_attribute.py (home, leisure, leisure, home) with the
    stroll marker off; ``loop_columns`` maps the loop column names to their values, or is None."""
    persons, activities, locations, trips, vehicles = _raw_frames([False] * 4)
    for column, values in (loop_columns or {}).items():
        locations[column] = values
    df_persons, df_activities, df_trips, df_vehicles = pop.prepare_frames(
        persons, activities, locations, trips, vehicles)
    target = tmp_path / name
    pop.write_population(str(target), df_persons, df_activities, df_trips, df_vehicles,
                         enable_urban_parking=False, context=_Context())
    with gzip.open(target, "rb") as handle:
        return handle.read()


def test_the_writer_writes_the_loop_marker_and_the_diary_length(tmp_path):
    xml = _write(tmp_path, {FLAG: [False, True, False, False], LENGTH: [np.nan, 6.1, 6.6, np.nan]})

    attributes = _activity_attributes(xml)
    assert attributes[1] == {"loopStroll": ("java.lang.Boolean", "true"),
                             "loopTripDiaryDistanceKm": ("java.lang.Double", "6.1")}
    assert attributes[2] == {"loopTripDiaryDistanceKm": ("java.lang.Double", "6.6")}
    assert attributes[0] == attributes[3] == {}


def test_frames_without_the_loop_columns_write_identical_xml(tmp_path):
    without_columns = _write(tmp_path, None, "without.xml.gz")
    no_loops = _write(tmp_path, {FLAG: [False] * 4, LENGTH: [np.nan] * 4}, "no_loops.xml.gz")
    assert b"loopStroll" not in without_columns
    assert without_columns == no_loops


@pytest.mark.parametrize("flags, lengths, column", [
    (pd.Series([False, np.nan, False, False], dtype=object), [np.nan] * 4, FLAG),
    ([False] * 4, pd.Series([np.nan, "6.1", np.nan, np.nan], dtype=object), LENGTH),
    ([False] * 4, [np.nan, -1.0, np.nan, np.nan], LENGTH),
])
def test_a_loop_value_the_join_cannot_have_written_raises(tmp_path, flags, lengths, column):
    with pytest.raises(ValueError, match=column):
        _write(tmp_path, {FLAG: flags, LENGTH: lengths})


# --------------------------------------------------------------------------- wrapper: injected agents

def test_injected_agents_get_neither_loop_attribute(tmp_path):
    stages = _wrapper_stages()
    locations = stages["synthesis.population.spatial.locations"].copy()
    locations["is_out_and_back_stroll"] = False
    # the resident's second activity stands in for a loop
    locations[FLAG] = [False, True, False]
    locations[LENGTH] = [np.nan, 2.5, np.nan]
    stages["synthesis.population.spatial.locations"] = locations
    config = {"cordon_enabled": True, "commute_day_state_enabled": False, "day_absence_enabled": False,
              "enable_urban_parking": False, "write_income_eur": False, "parking_zones_enabled": False,
              "remode_carless_car_legs": False, LOOP_KEY: True}
    declared = _ConfigureRecorder(config)
    POP.configure(declared)
    POP.execute(_ExecuteContext(declared, stages, config, tmp_path))
    with gzip.open(tmp_path / "population.xml.gz", "rb") as handle:
        root = ElementTree.fromstring(handle.read())

    def loop_attributes(person_id):
        person = root.find(f"person[@id='{person_id}']")
        return [sorted(a.get("name") for a in activity.findall("./attributes/attribute")
                       if a.get("name").startswith("loop"))
                for activity in person.findall("./plan/activity")]

    assert loop_attributes(1) == [[], ["loopStroll", "loopTripDiaryDistanceKm"], []]
    assert loop_attributes(SVB_ID) == loop_attributes(STUDENT_ID) == [[], [], []]


# --------------------------------------------------------------------------- trips output

def _output_trips():
    return pd.DataFrame({"person_id": [1, 1, 1, 2], "trip_index": [0, 1, 2, 0],
                         "following_activity_index": [1, 2, 3, 1]})


def _output_locations(**columns):
    frame = pd.DataFrame({"person_id": [1, 1, 1, 1, 2, 2], "activity_index": [0, 1, 2, 3, 0, 1]})
    for column, values in columns.items():
        frame[column] = values
    return frame


def test_the_trips_output_carries_the_loop_columns_of_each_trips_destination():
    trips = output.attach_trip_loop_columns(_output_trips(), _output_locations(**{
        FLAG: [False, False, True, False, False, False],
        LENGTH: [np.nan, np.nan, 6.1, 6.6, np.nan, np.nan]}))

    assert trips[FLAG].tolist() == [False, True, False, False]
    np.testing.assert_allclose(trips[LENGTH].to_numpy(dtype=float), [np.nan, 6.1, 6.6, np.nan])
    assert trips["trip_index"].tolist() == [0, 1, 2, 0]


def test_without_the_loop_columns_the_trips_output_is_unchanged():
    trips = _output_trips()
    pd.testing.assert_frame_equal(output.attach_trip_loop_columns(trips, _output_locations()), trips)


@pytest.mark.parametrize("locations, message", [
    (_output_locations(**{FLAG: [False] * 6}), "only"),
    (_output_locations(**{FLAG: [False] * 6, LENGTH: [np.nan] * 6}).iloc[:5], r"1 trips .*\(2, 0\)"),
])
def test_the_trips_output_refuses_incomplete_loop_columns(locations, message):
    with pytest.raises(ValueError, match=message):
        output.attach_trip_loop_columns(_output_trips(), locations)
