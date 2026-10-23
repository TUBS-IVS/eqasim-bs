"""The day-end margin of the written plans (eqasim-bs#465, mechanism 2).

In run i465-wpe-smoke-1pct-2026-10-23 all 20 residents stuck at the simulation end in a car or as car passenger had
plans whose last trip was planned to arrive at 29.96-30.54 h, i.e. at or after the end of the simulated day (30 h):
the synthesis bounds the last arrival by the survey travel times without a margin, the routed and congested trips
take longer. ``braunschweig.matsim.scenario.day_end_margin`` moves such a day earlier AS A WHOLE (every activity
duration kept) until its last arrival leaves the margin before the end; a day that cannot move far enough (its first
departure would become negative) moves as far as it can and is counted as unresolved.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.matsim.scenario import day_end_margin as M
from braunschweig.matsim.scenario import population as POP

H = 3600.0
BOUND_S = 30 * H


def _day(person_id, times_h):
    """Activities and trips of one person: ``times_h`` = [(departure_h, arrival_h), ...] of the trips."""
    trips = pd.DataFrame({"person_id": person_id, "trip_index": range(len(times_h)),
                          "departure_time": [d * H for d, _ in times_h], "arrival_time": [a * H for _, a in times_h],
                          "mode": "car"})
    starts = [np.nan] + [a * H for _, a in times_h]
    ends = [d * H for d, _ in times_h] + [np.nan]
    activities = pd.DataFrame({"person_id": person_id, "activity_index": range(len(starts)),
                               "start_time": starts, "end_time": ends, "purpose": "x"})
    return activities, trips


def _frames(*days):
    return (pd.concat([a for a, _ in days], ignore_index=True), pd.concat([t for _, t in days], ignore_index=True))


def _durations(activities):
    return (activities["end_time"] - activities["start_time"]).to_numpy()


def test_a_day_ending_inside_the_margin_moves_earlier_as_a_whole_and_keeps_every_duration():
    # Person 1: the night-shift pattern of the stuck residents, last arrival 30.0 h. Person 2 ends at 20 h.
    activities, trips = _frames(_day(1, [(14.5, 15.0), (21.25, 21.5), (29.75, 30.0)]), _day(2, [(8.0, 8.5), (19.5, 20.0)]))

    out_activities, out_trips, report = M.shift_late_days(activities, trips, bound_s=BOUND_S, margin_s=H)

    late_a, late_t = out_activities[out_activities.person_id == 1], out_trips[out_trips.person_id == 1]
    assert late_t["arrival_time"].max() == 29.0 * H
    np.testing.assert_array_equal(late_t["departure_time"].to_numpy(), np.array([13.5, 20.25, 28.75]) * H)
    np.testing.assert_array_equal(_durations(late_a), _durations(activities[activities.person_id == 1]))
    np.testing.assert_array_equal((late_t["arrival_time"] - late_t["departure_time"]).to_numpy(),
                                  (trips[trips.person_id == 1]["arrival_time"]
                                   - trips[trips.person_id == 1]["departure_time"]).to_numpy())
    pd.testing.assert_frame_equal(out_activities[out_activities.person_id == 2], activities[activities.person_id == 2])
    pd.testing.assert_frame_equal(out_trips[out_trips.person_id == 2], trips[trips.person_id == 2])
    assert report["n_persons"] == 2 and report["n_late"] == 1 and report["n_shifted"] == 1
    assert report["n_unresolved"] == 0 and report["shift_max_s"] == H


def test_a_day_too_long_for_the_margin_moves_to_the_start_of_the_day_and_is_counted_unresolved(caplog):
    activities, trips = _frames(_day(7, [(0.5, 1.0), (29.7, 30.2)]))

    out_activities, out_trips, report = M.shift_late_days(activities, trips, bound_s=BOUND_S, margin_s=H)

    assert out_trips["departure_time"].min() == 0.0                    # moved as far as it can
    assert out_trips["arrival_time"].max() == 29.7 * H                  # still after 29 h: unresolved
    assert report["n_late"] == 1 and report["n_shifted"] == 1 and report["n_unresolved"] == 1
    with caplog.at_level(logging.INFO):
        M.log_report(report)
    assert any(r.levelname == "WARNING" and "unresolved" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("margin_s", [-1.0, BOUND_S, float("nan"), True, "3600"])
def test_an_invalid_margin_is_rejected(margin_s):
    activities, trips = _frames(_day(1, [(8.0, 9.0)]))
    with pytest.raises(ValueError, match="last_arrival_margin_s"):
        M.shift_late_days(activities, trips, bound_s=BOUND_S, margin_s=margin_s)


class _PopulationContext:
    """Stub of the synpp context of braunschweig.matsim.scenario.population: config values and stage frames."""

    def __init__(self, tmp_path, enabled):
        self._tmp_path = tmp_path
        self._config = {"enable_urban_parking": False, "write_income_eur": False,
                        POP.KEY_PARKING_ZONES_ENABLED: False, POP.KEY_COMMUTE_DAY_STATE_ENABLED: False,
                        POP.KEY_DAY_ABSENCE_ENABLED: False, "cordon_enabled": False,
                        M.KEY_ENABLED: enabled, M.KEY_LAST_ARRIVAL_MARGIN_S: H}

    def config(self, name, default=None):
        return self._config[name]

    def stage(self, name, *args, **kwargs):
        return pd.DataFrame()

    def path(self, name=None):
        return str(self._tmp_path)


@pytest.mark.parametrize("enabled", [True, False])
def test_the_population_writer_applies_the_margin_only_when_enabled(tmp_path, monkeypatch, enabled):
    activities, trips = _frames(_day(1, [(14.5, 15.0), (29.75, 30.0)]))
    raw = {"persons": pd.DataFrame({"person_id": [1]}), "activities": activities, "trips": trips,
           "locations": pd.DataFrame(), "vehicles": pd.DataFrame()}
    written = {}
    monkeypatch.setattr(POP.base, "load_raw", lambda context: {k: v.copy() for k, v in raw.items()})
    monkeypatch.setattr(POP.base, "prepare_frames",
                        lambda persons, acts, locations, trps, vehicles: (persons, acts, trps, vehicles))
    monkeypatch.setattr(POP.base, "write_population",
                        lambda path, persons, acts, trps, vehicles, *args, **kwargs: written.update(trips=trps))

    POP.execute(_PopulationContext(tmp_path, enabled))

    expected_last_arrival = 29.0 * H if enabled else 30.0 * H
    assert written["trips"]["arrival_time"].max() == expected_last_arrival


@pytest.mark.parametrize("enabled, routed_check, expected", [
    (True, True, ["--latest-last-arrival", "104400"]),   # 30 h - 3600 s
    (True, False, []),
    (False, True, []),
])
def test_the_prepare_stage_passes_the_routed_day_end_to_the_population_routing(enabled, routed_check, expected):
    """ADR-0160: the routing repeats the day-end rule with the ROUTED travel times (person 1125404 of the 1 %
    rerun of the #465 fixes ended at 29:00 by its survey times and after 30 h by its routed 132 km car trip)."""
    import matsim.simulation.prepare as prepare

    config = {"output_prefix": "x_", M.KEY_ENABLED: enabled, M.KEY_ROUTED_CHECK: routed_check,
              M.KEY_LAST_ARRIVAL_MARGIN_S: H}

    class _Context:
        def config(self, name, default=None):
            return config[name]

    arguments = prepare.population_routing_arguments(_Context(), 8)
    assert arguments[:8] == ["--config-path", "x_config.xml", "--output-path", "x_population.xml.gz",
                             "--threads", 8, "--config:plans.inputPlansFile", "prepared_population.xml.gz"]
    assert arguments[8:] == expected
