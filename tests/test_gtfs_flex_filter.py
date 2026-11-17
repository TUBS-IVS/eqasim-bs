"""Tests for the GTFS-Flex (on-demand) trip filter ``data.gtfs.utils.filter_flex_trips``.

Port of upstream eqasim-france #569 with two deviations (see the function docstring):
whole trips are dropped instead of single stop_times rows, and the counts are logged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import data.gtfs.utils as gtfs  # noqa: E402


def _feed(with_window_columns = True):
    """One scheduled trip (t_sched), one pure flex trip (t_flex), one mixed trip (t_mixed)."""
    routes = pd.DataFrame.from_records([
        dict(route_id = "1", agency_id = "10", route_short_name = "1", route_type = "3"),
        dict(route_id = "2", agency_id = "10", route_short_name = "Flexo", route_type = "3"),
    ])
    trips = pd.DataFrame.from_records([
        dict(route_id = "1", service_id = "s1", trip_id = "t_sched"),
        dict(route_id = "2", service_id = "s1", trip_id = "t_flex"),
        dict(route_id = "2", service_id = "s1", trip_id = "t_mixed"),
    ])
    stop_times = pd.DataFrame.from_records([
        dict(trip_id = "t_sched", stop_id = "A", stop_sequence = 1, arrival_time = "08:00:00"),
        dict(trip_id = "t_sched", stop_id = "B", stop_sequence = 2, arrival_time = "08:10:00"),
        dict(trip_id = "t_flex", stop_id = "A", stop_sequence = 1, arrival_time = np.nan),
        dict(trip_id = "t_flex", stop_id = "C", stop_sequence = 2, arrival_time = np.nan),
        dict(trip_id = "t_mixed", stop_id = "B", stop_sequence = 1, arrival_time = "09:00:00"),
        dict(trip_id = "t_mixed", stop_id = "C", stop_sequence = 2, arrival_time = np.nan),
    ])
    if with_window_columns:
        stop_times["start_pickup_drop_off_window"] = [np.nan, np.nan, "06:00:00", "06:00:00", np.nan, "09:00:00"]
        stop_times["end_pickup_drop_off_window"] = [np.nan, np.nan, "20:00:00", "20:00:00", np.nan, "10:00:00"]
    frequencies = pd.DataFrame.from_records([
        dict(trip_id = "t_flex", start_time = "06:00:00", end_time = "20:00:00", headway_secs = 3600),
        dict(trip_id = "t_sched", start_time = "06:00:00", end_time = "20:00:00", headway_secs = 1200),
    ])
    return dict(routes = routes, trips = trips, stop_times = stop_times, frequencies = frequencies)


def test_flex_and_mixed_trips_are_removed_whole_and_counted(capsys):
    feed = _feed()
    filtered = gtfs.filter_flex_trips(feed)

    # A trip with any booking-window row goes completely (no truncated mixed trip).
    assert set(filtered["trips"]["trip_id"]) == {"t_sched"}
    assert list(filtered["stop_times"]["arrival_time"]) == ["08:00:00", "08:10:00"]
    assert set(filtered["frequencies"]["trip_id"]) == {"t_sched"}
    # Routes stay for the reference cleanup in cut_feed.
    assert set(filtered["routes"]["route_id"]) == {"1", "2"}
    # The removal is logged with counts (no silent filter): 2 of 3 trips, 4 of 6 stop_times.
    out = capsys.readouterr().out
    assert "2/3" in out and "4/6" in out
    # The input feed is not mutated.
    assert len(feed["trips"]) == 3 and len(feed["stop_times"]) == 6


def test_feed_without_window_columns_is_unchanged():
    feed = _feed(with_window_columns = False)
    filtered = gtfs.filter_flex_trips(feed)

    for slot in feed:
        pd.testing.assert_frame_equal(filtered[slot], feed[slot])


def test_flexo_name_filter_still_matches_after_flex_filter(capsys):
    """Order used in data.gtfs.cleaned: flex filter first, Flexo name filter as second net."""
    feed = gtfs.filter_flex_trips(_feed())
    feed = gtfs.filter_routes(feed, excluded_route_short_name_patterns = ["^Flexo$"])

    assert set(feed["routes"]["route_id"]) == {"1"}
    assert "no routes matched" not in capsys.readouterr().out
