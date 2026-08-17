"""Tests for the GTFS demand-responsive (Flexo) route exclusion filter.

Background (issue #200): the ZGB GTFS feed carries the demand-responsive service
"Flexo" of the Regionalverband Grossraum Braunschweig as a single route that is
rolled out as thousands of placeholder trips. Imported unchanged, these overstate
scheduled PT supply. ``data.gtfs.utils.filter_routes`` removes configured routes
(by ``route_short_name`` pattern and/or agency) from a GTFS feed at preprocessing
time, cascading the removal to trips, stop_times and frequencies, and logs exactly
how many entries were removed per rule (no silent fallback).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import data.gtfs.utils as gtfs  # noqa: E402


def _synthetic_feed():
    """Minimal GTFS feed with one normal bus route and one 'Flexo' route.

    Route "1"  -> agency 10, normal scheduled bus, 2 trips.
    Route "99" -> agency 10, demand-responsive placeholder named 'Flexo', 3 trips.
    """
    agency = pd.DataFrame.from_records([
        dict(agency_id="10", agency_name="Tarifverb Region Braunschweig"),
        dict(agency_id="20", agency_name="Some Other Operator"),
    ])
    routes = pd.DataFrame.from_records([
        dict(route_id="1", agency_id="10", route_short_name="1",
             route_long_name="City bus", route_type="3"),
        dict(route_id="99", agency_id="10", route_short_name="Flexo",
             route_long_name="", route_type="3"),
        dict(route_id="7", agency_id="20", route_short_name="7",
             route_long_name="Regional bus", route_type="3"),
    ])
    trips = pd.DataFrame.from_records([
        dict(route_id="1", service_id="s1", trip_id="t_norm_1"),
        dict(route_id="1", service_id="s1", trip_id="t_norm_2"),
        dict(route_id="99", service_id="s1", trip_id="t_flexo_1"),
        dict(route_id="99", service_id="s1", trip_id="t_flexo_2"),
        dict(route_id="99", service_id="s1", trip_id="t_flexo_3"),
        dict(route_id="7", service_id="s1", trip_id="t_other_1"),
    ])
    stop_times = pd.DataFrame.from_records([
        dict(trip_id="t_norm_1", stop_id="A", stop_sequence="1"),
        dict(trip_id="t_norm_1", stop_id="B", stop_sequence="2"),
        dict(trip_id="t_norm_2", stop_id="B", stop_sequence="1"),
        dict(trip_id="t_norm_2", stop_id="A", stop_sequence="2"),
        dict(trip_id="t_flexo_1", stop_id="A", stop_sequence="1"),
        dict(trip_id="t_flexo_1", stop_id="C", stop_sequence="2"),
        dict(trip_id="t_flexo_2", stop_id="C", stop_sequence="1"),
        dict(trip_id="t_flexo_2", stop_id="D", stop_sequence="2"),
        dict(trip_id="t_flexo_3", stop_id="D", stop_sequence="1"),
        dict(trip_id="t_flexo_3", stop_id="A", stop_sequence="2"),
        dict(trip_id="t_other_1", stop_id="A", stop_sequence="1"),
        dict(trip_id="t_other_1", stop_id="B", stop_sequence="2"),
    ])
    frequencies = pd.DataFrame.from_records([
        dict(trip_id="t_flexo_1", start_time="06:00:00", end_time="20:00:00", headway_secs="3600"),
        dict(trip_id="t_norm_1", start_time="06:00:00", end_time="20:00:00", headway_secs="1200"),
    ])
    return dict(agency=agency, routes=routes, trips=trips,
                stop_times=stop_times, frequencies=frequencies)


def test_filter_removes_flexo_route_by_short_name_pattern():
    feed = _synthetic_feed()

    filtered = gtfs.filter_routes(
        feed, excluded_route_short_name_patterns=["^Flexo$"])

    # The Flexo route and all its dependent entries are gone ...
    assert "99" not in set(filtered["routes"]["route_id"])
    assert not filtered["trips"]["trip_id"].str.startswith("t_flexo").any()
    assert not filtered["stop_times"]["trip_id"].str.startswith("t_flexo").any()
    assert "t_flexo_1" not in set(filtered["frequencies"]["trip_id"])

    # ... while the normal routes are untouched.
    assert set(filtered["routes"]["route_id"]) == {"1", "7"}
    assert set(filtered["trips"]["trip_id"]) == {"t_norm_1", "t_norm_2", "t_other_1"}
    assert len(filtered["stop_times"]) == 6
    assert set(filtered["frequencies"]["trip_id"]) == {"t_norm_1"}


def test_filter_removes_by_agency_id():
    feed = _synthetic_feed()

    filtered = gtfs.filter_routes(feed, excluded_agency_ids=["20"])

    assert set(filtered["routes"]["route_id"]) == {"1", "99"}
    assert "t_other_1" not in set(filtered["trips"]["trip_id"])


def test_empty_patterns_is_noop():
    feed = _synthetic_feed()

    filtered = gtfs.filter_routes(feed)

    assert set(filtered["routes"]["route_id"]) == {"1", "99", "7"}
    assert len(filtered["trips"]) == 6
    assert len(filtered["stop_times"]) == 12


def test_input_feed_not_mutated():
    feed = _synthetic_feed()
    gtfs.filter_routes(feed, excluded_route_short_name_patterns=["^Flexo$"])

    # The original feed still contains the Flexo route (filter works on a copy).
    assert "99" in set(feed["routes"]["route_id"])
    assert len(feed["trips"]) == 6
