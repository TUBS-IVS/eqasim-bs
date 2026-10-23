"""Timetable check of the pt legs of outside tours (eqasim-bs#465, ADR-0159).

In the 1 % rerun of the #465 fixes, 15 pt legs of non-portal outside tours of residents (14 persons) had no timetable
connection; the prepare routing replaced each by a direct walk of 8-30 km, and two of the seven residents stuck at the
end of that run walked such legs (a pt leisure tour to an external destination). Nothing checked these legs: the
portal stays and the in-commuters have their own checks. ``resolve_pt_tours`` probes every pt leg of an outside tour
without a portal leg and resolves a tour without a connection by moving it as a whole (earlier first), then by car,
then by a lift (car_passenger), and as the last resort by a lift without the passenger availability, never a walk.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.outside_tour_modes import pt_connections as P
from braunschweig.synthesis.outside_tour_modes.tours import home_based_tour_ids

H = 3600.0


def _trips(rows):
    """rows: (person_id, trip_index, preceding, following, mode, departure_h, arrival_h)."""
    frame = pd.DataFrame(rows, columns=["person_id", "trip_index", "preceding_purpose", "following_purpose", "mode",
                                        "departure_time", "arrival_time"])
    frame["departure_time"] *= H
    frame["arrival_time"] *= H
    return frame


def _availability(persons, can_car=False, can_car_passenger=False):
    return pd.DataFrame({"can_car": can_car, "can_car_passenger": can_car_passenger, "can_bicycle": False,
                         "has_car": can_car}, index=pd.Index(list(persons), name="person_id"))


def _endpoints(n):
    return pd.DataFrame({"ox": np.zeros(n), "oy": np.zeros(n), "dx": np.full(n, 30000.0), "dy": np.zeros(n)})


def _service_probe(first_s, last_s, seen=None):
    """A probe whose timetable serves every departure in [first_s, last_s]."""
    def probe(tasks):
        if seen is not None:
            seen.append(tasks.copy())
        served = tasks["departure_time"].between(first_s, last_s)
        return pd.Series(served.to_numpy(), index=tasks["identifier"].astype(str).to_numpy())
    return probe


def _resolve(trips, probe, *, outside=None, portal=None, availability=None, last_resort=P.LAST_RESORT_LIFT,
             bound_s=29 * H):
    tour_ids = home_based_tour_ids(trips)
    n = len(trips)
    return P.resolve_pt_tours(
        trips=trips, tour_ids=tour_ids,
        outside=np.ones(n, bool) if outside is None else np.asarray(outside, bool),
        portal_tour=np.zeros(n, bool) if portal is None else np.asarray(portal, bool),
        endpoints=_endpoints(n),
        availability=_availability(trips["person_id"].unique()) if availability is None else availability,
        probe=probe, shift_window_s=2 * H, shift_step_s=0.5 * H, bound_s=bound_s, last_resort=last_resort)


def test_a_served_tour_is_untouched_and_counted_as_the_primary_path():
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 20.0, 20.5)])
    seen = []
    result = _resolve(trips, _service_probe(6 * H, 21 * H, seen))
    assert result.report["actions"][P.ACTION_CONNECTION_FOUND] == 1
    assert result.report["n_tours_checked"] == 1 and result.report["n_legs_probed"] == 2
    pd.testing.assert_frame_equal(result.trips, trips)
    assert len(seen) == 1 and list(seen[0]["departure_time"]) == [18 * H, 20 * H]


def test_a_late_return_moves_the_whole_tour_earlier_first():
    # Return at 22:00 after the last service (21:00): -30 min is not enough (21:30), -1 h works (21:00).
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 22.0, 22.5)])
    result = _resolve(trips, _service_probe(6 * H, 21 * H))
    assert result.report["actions"][P.ACTION_TOUR_SHIFTED] == 1
    assert list(result.trips["departure_time"]) == [17 * H, 21 * H]
    assert list(result.trips["arrival_time"]) == [17.5 * H, 21.5 * H]
    assert list(result.trips["mode"]) == ["pt", "pt"]
    assert result.report["shift_abs_s_median"] == H


def test_a_shift_never_overlaps_or_touches_the_previous_or_next_trip_of_the_person():
    # Tour 1 (in-region, not outside) arrives home at 17:30; the outside tour leaves at 18:00 and returns at 21:30.
    # -30 min would be served (return 21:00) but leaves the home stay between the tours at zero duration, -1 h
    # overlaps tour 1, and every later offset misses the last service (21:00): the person has a car, so it drives.
    trips = _trips([(1, 0, "home", "work", "car", 8.0, 8.5), (1, 1, "work", "home", "car", 17.0, 17.5),
                    (1, 2, "home", "leisure", "pt", 18.0, 18.5), (1, 3, "leisure", "home", "pt", 21.5, 22.0)])
    outside = [False, False, True, True]
    result = _resolve(trips, _service_probe(0.0, 21 * H), outside=outside,
                      availability=_availability([1], can_car=True))
    assert result.report["actions"][P.ACTION_TOUR_TO_CAR] == 1
    assert list(result.trips["mode"]) == ["car", "car", "car", "car"]
    assert list(result.trips["departure_time"]) == list(trips["departure_time"])


def test_without_a_shift_the_chain_switches_to_car_then_lift_then_the_lift_without_availability():
    rows, persons = [], [1, 2, 3, 4]
    for person in persons:  # nothing is ever served
        rows += [(person, 0, "home", "leisure", "pt", 18.0, 18.5), (person, 1, "leisure", "home", "pt", 22.0, 22.5)]
    trips = _trips(rows)
    availability = pd.concat([_availability([1], can_car=True), _availability([2], can_car_passenger=True),
                              _availability([3]), _availability([4])])
    result = _resolve(trips, _service_probe(-1.0, -1.0), availability=availability)
    assert list(result.trips["mode"]) == ["car", "car", "car_passenger", "car_passenger",
                                          "car_passenger", "car_passenger", "car_passenger", "car_passenger"]
    counts = result.report["actions"]
    assert counts[P.ACTION_TOUR_TO_CAR] == 1 and counts[P.ACTION_TOUR_TO_CAR_PASSENGER] == 1
    assert counts[P.ACTION_TOUR_TO_CAR_PASSENGER_LIFT] == 2 and counts[P.ACTION_KEPT] == 0
    assert list(result.tour_actions["action"]) == [P.ACTION_TOUR_TO_CAR, P.ACTION_TOUR_TO_CAR_PASSENGER,
                                                   P.ACTION_TOUR_TO_CAR_PASSENGER_LIFT,
                                                   P.ACTION_TOUR_TO_CAR_PASSENGER_LIFT]


def test_the_keep_last_resort_keeps_the_tour_and_warns(caplog):
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 22.0, 22.5)])
    result = _resolve(trips, _service_probe(-1.0, -1.0), last_resort=P.LAST_RESORT_KEEP)
    assert result.report["actions"][P.ACTION_KEPT] == 1
    assert list(result.trips["mode"]) == ["pt", "pt"]
    with caplog.at_level(logging.INFO):
        P.log_report(result.report, warn_share=0.05)
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_tours_that_are_not_outside_have_no_pt_or_touch_a_portal_are_never_probed():
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 22.0, 22.5),
                    (2, 0, "home", "leisure", "car", 18.0, 18.5), (2, 1, "leisure", "home", "car", 22.0, 22.5),
                    (3, 0, "home", "outside", "pt", 18.0, 18.5), (3, 1, "outside", "home", "pt", 22.0, 22.5)])
    seen = []
    result = _resolve(trips, _service_probe(-1.0, -1.0, seen), outside=[False, False, True, True, True, True],
                      portal=[False, False, False, False, True, True])
    assert seen == [] and result.report["n_tours_checked"] == 0
    pd.testing.assert_frame_equal(result.trips, trips)


def test_only_the_pt_legs_of_a_mixed_tour_are_probed_and_the_whole_tour_switches():
    trips = _trips([(1, 0, "home", "leisure", "walk", 18.0, 18.2), (1, 1, "leisure", "other", "pt", 19.0, 19.5),
                    (1, 2, "other", "home", "pt", 22.0, 22.5)])
    seen = []
    result = _resolve(trips, _service_probe(-1.0, -1.0, seen))
    assert result.report["n_legs_probed"] == 2
    assert set(seen[0]["departure_time"]) == {19 * H, 22 * H}
    assert list(result.trips["mode"]) == ["car_passenger"] * 3


def test_a_shift_never_moves_the_last_arrival_past_the_bound():
    # Outbound at 27:00 is not served (service from 28:00); a later shift would arrive after the bound of 29 h.
    trips = _trips([(1, 0, "home", "leisure", "pt", 27.0, 27.5), (1, 1, "leisure", "home", "pt", 28.0, 28.5)])
    seen = []
    _resolve(trips, _service_probe(28 * H, 30 * H, seen))
    offsets = [tasks["departure_time"].iloc[0] - 27 * H for tasks in seen[1:]]
    assert all(trips["arrival_time"].iloc[1] + offset <= 29 * H for offset in offsets)


def test_a_late_day_is_moved_by_the_writer_rule_before_it_is_probed():
    # PR #505 review: the return leaves at 28:30 and arrives at 29:40, after the bound of 29 h. The population
    # writer moves such a day 40 min earlier (day_end_margin.late_day_shift_s), so the timetable must be asked at
    # 27:50, not at 28:30, where the only service runs. Probed at 27:50 there is no connection: the tour is lifted
    # instead of being written as connection_found and then walked at a time nobody checked.
    trips = _trips([(1, 0, "home", "leisure", "pt", 20.0, 20.5), (1, 1, "leisure", "home", "pt", 28.5, 29 + 40 / 60)])

    def probe(tasks):
        served = tasks["departure_time"].lt(21 * H) | tasks["departure_time"].between(28.4 * H, 28.6 * H)
        return pd.Series(served.to_numpy(), index=tasks["identifier"].astype(str).to_numpy())

    seen = []
    result = _resolve(trips, lambda tasks: (seen.append(tasks.copy()), probe(tasks))[1])
    assert sorted(seen[0]["departure_time"]) == [20 * H - 40 * 60, 28.5 * H - 40 * 60]
    assert result.report["actions"][P.ACTION_CONNECTION_FOUND] == 0
    assert result.report["n_persons_day_end_shifted"] == 1
    assert result.trips["arrival_time"].max() <= 29 * H


def test_the_probe_must_answer_every_task():
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 22.0, 22.5)])
    with pytest.raises(ValueError, match="no result"):
        _resolve(trips, lambda tasks: pd.Series(dtype=bool))


def test_an_invalid_last_resort_raises():
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5)])
    with pytest.raises(ValueError, match="last_resort"):
        _resolve(trips, _service_probe(0, 30 * H), last_resort="walk")


def test_the_input_row_order_and_index_are_kept():
    trips = _trips([(2, 0, "home", "leisure", "pt", 18.0, 18.5), (2, 1, "leisure", "home", "pt", 22.0, 22.5),
                    (1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 22.0, 22.5)])
    trips.index = [10, 11, 12, 13]
    ordered = trips.sort_values(["person_id", "trip_index"], kind="mergesort")
    result = _resolve(ordered, _service_probe(6 * H, 21 * H))
    assert list(result.trips.index) == [12, 13, 10, 11]
    assert result.report["actions"][P.ACTION_TOUR_SHIFTED] == 2
