"""Timetable check of the pt legs of outside tours (eqasim-bs#465, ADR-0159).

In the 1 % rerun of the #465 fixes, 15 pt legs of non-portal outside tours of residents (14 persons) had no timetable
connection; the prepare routing replaced each by a direct walk of 8-30 km, and two of the seven residents stuck at the
end of that run walked such legs (a pt leisure tour to an external destination). Nothing checked these legs: the
portal stays and the in-commuters have their own checks. ``resolve_pt_tours`` probes every pt leg of an outside tour
and, since #575 (ADR-0187, tests at the end of the file), of a tour with a portal leg, and resolves a tour without a connection by moving it as a whole (earlier first), then by car,
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


def _touches_outside(trips):
    """Per-leg ``portal_leg`` flags by the production rule (rewrite.py): a leg is a portal leg when it ends at or
    leaves the ``outside`` activity, so home -> outside -> other -> home gives [True, True, False]."""
    return (trips["following_purpose"].eq("outside") | trips["preceding_purpose"].eq("outside")).to_numpy()


def _resolve(trips, probe, *, outside=None, portal=None, portal_leg=None, availability=None,
             last_resort=P.LAST_RESORT_LIFT, bound_s=29 * H):
    """``portal_leg`` (per-leg flag, ADR-0187) is passed only when given, so tours without a portal leg stay as before."""
    tour_ids = home_based_tour_ids(trips)
    n = len(trips)
    extra = {} if portal_leg is None else {"portal_leg": np.asarray(portal_leg, bool)}
    return P.resolve_pt_tours(
        trips=trips, tour_ids=tour_ids,
        outside=np.ones(n, bool) if outside is None else np.asarray(outside, bool),
        portal_tour=np.zeros(n, bool) if portal is None else np.asarray(portal, bool),
        endpoints=_endpoints(n),
        availability=_availability(trips["person_id"].unique()) if availability is None else availability,
        probe=probe, shift_window_s=2 * H, shift_step_s=0.5 * H, bound_s=bound_s, last_resort=last_resort, **extra)


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


def test_tours_that_are_not_outside_or_have_no_pt_are_never_probed():
    trips = _trips([(1, 0, "home", "leisure", "pt", 18.0, 18.5), (1, 1, "leisure", "home", "pt", 22.0, 22.5),
                    (2, 0, "home", "leisure", "car", 18.0, 18.5), (2, 1, "leisure", "home", "car", 22.0, 22.5)])
    seen = []
    result = _resolve(trips, _service_probe(-1.0, -1.0, seen), outside=[False, False, True, True])
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


def test_without_the_day_end_rule_a_late_day_is_probed_at_its_own_times():
    # PR #505 review: with braunschweig.day_end_margin.enabled false the population writer moves no day, so the check
    # must not move one either; the late return is probed at 28:30, where the service runs.
    trips = _trips([(1, 0, "home", "leisure", "pt", 20.0, 20.5), (1, 1, "leisure", "home", "pt", 28.5, 29 + 40 / 60)])
    seen = []
    result = P.resolve_pt_tours(
        trips=trips, tour_ids=home_based_tour_ids(trips), outside=np.ones(2, bool), portal_tour=np.zeros(2, bool),
        endpoints=_endpoints(2), availability=_availability([1]), shift_window_s=2 * H, shift_step_s=0.5 * H,
        bound_s=30 * H, probe=_service_probe(0.0, 28.6 * H, seen), day_end_shift=False)
    assert sorted(seen[0]["departure_time"]) == [20 * H, 28.5 * H]
    assert result.report["n_persons_day_end_shifted"] == 0
    assert result.report["actions"][P.ACTION_CONNECTION_FOUND] == 1


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


# --- Tours with a portal leg (eqasim-bs#575, ADR-0187) -------------------------------------------------------------
# API assumed: keyword ``portal_leg`` (bool per leg); action "pt_legs_to_car_passenger_lift"; report["n_legs_lifted"]
# and report["by_portal_class"][class] with the keys n_tours_checked, n_legs_probed, n_legs_lifted, actions
# (class in "none", "road", "rail"); tour_actions columns "portal_class" and "n_legs_lifted".

LIFT_LEGS = "pt_legs_to_car_passenger_lift"


def _served_at(*hours, seen=None):
    """A probe whose timetable serves exactly the given departure hours."""
    served_s = [h * H for h in hours]

    def probe(tasks):
        if seen is not None:
            seen.append(tasks.copy())
        return pd.Series(tasks["departure_time"].isin(served_s).to_numpy(),
                         index=tasks["identifier"].astype(str).to_numpy())
    return probe


def test_a_rail_portal_tour_probes_its_portal_and_its_other_pt_legs():
    trips = _trips([(1, 0, "home", "outside", "pt", 18.0, 18.5), (1, 1, "outside", "other", "pt", 19.0, 19.5),
                    (1, 2, "other", "home", "pt", 22.0, 22.5)])
    seen = []
    result = _resolve(trips, _service_probe(6 * H, 23 * H, seen), portal=[True] * 3,
                      portal_leg=_touches_outside(trips))
    assert sorted(seen[0]["departure_time"]) == [18 * H, 19 * H, 22 * H]
    assert result.report["n_tours_checked"] == 1 and result.report["n_legs_probed"] == 3
    assert result.report["actions"][P.ACTION_CONNECTION_FOUND] == 1
    pd.testing.assert_frame_equal(result.trips, trips)


def test_a_portal_return_leg_not_served_at_its_time_shifts_the_whole_tour_including_portal_legs():
    # The return leg gate -> real destination leaves at 22:00, the last service is 21:00: the whole tour moves 1 h.
    trips = _trips([(1, 0, "home", "outside", "pt", 18.0, 18.5), (1, 1, "outside", "home", "pt", 22.0, 22.5)])
    result = _resolve(trips, _service_probe(6 * H, 21 * H), portal=[True, True], portal_leg=[True, True])
    assert result.report["actions"][P.ACTION_TOUR_SHIFTED] == 1
    assert list(result.trips["departure_time"]) == [17 * H, 21 * H]
    assert list(result.trips["arrival_time"]) == [17.5 * H, 21.5 * H]
    assert list(result.trips["mode"]) == ["pt", "pt"]


def test_a_rail_portal_tour_lifts_only_its_unserved_pt_legs_and_keeps_the_rail_gate():
    trips = _trips([(1, 0, "home", "outside", "pt", 18.0, 18.5), (1, 1, "outside", "other", "pt", 19.0, 19.5),
                    (1, 2, "other", "home", "pt", 22.0, 22.5)])
    flags = dict(portal=[True] * 3, portal_leg=_touches_outside(trips))
    # Car availability is no reason to switch the whole tour: that would break the rail gate.
    result = _resolve(trips, _served_at(18, 22), availability=_availability([1], can_car=True), **flags)
    assert list(result.trips["mode"]) == ["pt", "car_passenger", "pt"]
    assert list(result.trips["departure_time"]) == list(trips["departure_time"])
    assert result.report["actions"][LIFT_LEGS] == 1 and result.report["n_legs_lifted"] == 1
    assert result.report["n_portal_legs_lifted"] == 1  # the lifted leg is the portal return leg gate -> other
    assert result.report["actions"][P.ACTION_TOUR_TO_CAR] == 0
    assert list(result.tour_actions["action"]) == [LIFT_LEGS]
    assert list(result.tour_actions["n_legs_lifted"]) == [1] and list(result.tour_actions["portal_class"]) == ["rail"]
    kept = _resolve(trips, _served_at(18, 22), last_resort=P.LAST_RESORT_KEEP, **flags)
    assert kept.report["actions"][P.ACTION_KEPT] == 1 and kept.report["actions"][LIFT_LEGS] == 0
    assert list(kept.trips["mode"]) == ["pt", "pt", "pt"]
    assert kept.report["n_legs_lifted"] == 0 and kept.report["n_portal_legs_lifted"] == 0


def test_a_road_gate_portal_tour_follows_the_whole_tour_chain():
    rows = []
    for person in (1, 2, 3):  # portal legs by car, the last pt leg is never served
        rows += [(person, 0, "home", "outside", "car", 18.0, 18.5), (person, 1, "outside", "other", "car", 19.0, 19.5),
                 (person, 2, "other", "home", "pt", 22.0, 22.5)]
    trips = _trips(rows)
    availability = pd.concat([_availability([1], can_car=True), _availability([2], can_car_passenger=True),
                              _availability([3])])
    result = _resolve(trips, _service_probe(-1.0, -1.0), portal=[True] * 9, portal_leg=_touches_outside(trips),
                      availability=availability)
    assert list(result.trips["mode"]) == ["car"] * 3 + ["car_passenger"] * 6
    assert list(result.tour_actions["action"]) == [P.ACTION_TOUR_TO_CAR, P.ACTION_TOUR_TO_CAR_PASSENGER,
                                                   P.ACTION_TOUR_TO_CAR_PASSENGER_LIFT]
    assert set(result.tour_actions["portal_class"]) == {"road"} and result.report["n_legs_lifted"] == 0


def test_the_report_gives_the_rates_per_portal_class():
    trips = _trips([(1, 0, "home", "leisure", "pt", 8.0, 8.5), (1, 1, "leisure", "home", "pt", 9.0, 9.5),
                    (2, 0, "home", "outside", "car", 10.0, 10.5), (2, 1, "outside", "other", "car", 11.0, 11.5),
                    (2, 2, "other", "home", "pt", 12.0, 12.5),
                    (3, 0, "home", "outside", "pt", 15.0, 15.5), (3, 1, "outside", "other", "pt", 16.0, 16.5),
                    (3, 2, "other", "home", "pt", 17.0, 17.5)])
    result = _resolve(trips, _served_at(8, 9, 15, 17), portal=[False] * 2 + [True] * 6,
                      portal_leg=_touches_outside(trips),
                      availability=pd.concat([_availability([1]), _availability([2], can_car=True),
                                              _availability([3])]))
    by_class = result.report["by_portal_class"]
    assert set(by_class) == {"none", "road", "rail"}
    assert (by_class["none"]["n_tours_checked"], by_class["none"]["n_legs_probed"]) == (1, 2)
    assert (by_class["road"]["n_tours_checked"], by_class["road"]["n_legs_probed"]) == (1, 1)
    assert (by_class["rail"]["n_tours_checked"], by_class["rail"]["n_legs_probed"]) == (1, 3)
    assert by_class["none"]["actions"][P.ACTION_CONNECTION_FOUND] == 1
    assert by_class["road"]["actions"][P.ACTION_TOUR_TO_CAR] == 1
    assert by_class["rail"]["actions"][LIFT_LEGS] == 1 and by_class["rail"]["n_legs_lifted"] == 1
    assert result.report["n_tours_checked"] == 3 and result.report["n_legs_lifted"] == 1
    assert result.report["n_portal_legs_lifted"] == 1
    assert [by_class[c]["n_portal_legs_lifted"] for c in ("none", "road", "rail")] == [0, 0, 1]
    assert list(result.tour_actions["portal_class"]) == ["none", "road", "rail"]
    assert list(result.tour_actions["n_legs_lifted"]) == [0, 0, 1]


def test_a_portal_leg_flag_of_the_wrong_length_raises():
    trips = _trips([(1, 0, "home", "outside", "pt", 18.0, 18.5), (1, 1, "outside", "home", "pt", 22.0, 22.5)])
    with pytest.raises(ValueError, match="portal_leg"):
        _resolve(trips, _service_probe(0, 30 * H), portal=[True, True], portal_leg=[True])


def test_portal_tours_without_the_per_leg_flag_raise():
    trips = _trips([(1, 0, "home", "outside", "pt", 18.0, 18.5), (1, 1, "outside", "home", "pt", 22.0, 22.5)])
    with pytest.raises(ValueError, match="portal_leg"):
        _resolve(trips, _service_probe(0, 30 * H), portal=[True, True])


def test_a_portal_tour_without_a_pt_leg_is_never_probed():
    trips = _trips([(1, 0, "home", "outside", "car", 18.0, 18.5), (1, 1, "outside", "home", "car", 22.0, 22.5)])
    seen = []
    result = _resolve(trips, _service_probe(-1.0, -1.0, seen), portal=[True, True], portal_leg=[True, True])
    assert seen == [] and result.report["n_tours_checked"] == 0
    pd.testing.assert_frame_equal(result.trips, trips)


def test_a_tour_with_a_road_and_a_rail_portal_leg_is_a_rail_tour():
    trips = _trips([(1, 0, "home", "outside", "car", 18.0, 18.5), (1, 1, "outside", "other", "pt", 19.0, 19.5),
                    (1, 2, "other", "home", "pt", 22.0, 22.5)])
    result = _resolve(trips, _served_at(22), portal=[True] * 3, portal_leg=_touches_outside(trips),
                      availability=_availability([1], can_car=True))
    assert list(result.tour_actions["portal_class"]) == ["rail"]
    assert list(result.trips["mode"]) == ["car", "car_passenger", "pt"]


def test_a_rail_tour_whose_only_unserved_leg_is_a_portal_leg_lifts_just_that_leg():
    trips = _trips([(1, 0, "home", "outside", "pt", 18.0, 18.5), (1, 1, "outside", "other", "pt", 19.0, 19.5),
                    (1, 2, "other", "home", "pt", 22.0, 22.5)])
    result = _resolve(trips, _served_at(19, 22), portal=[True] * 3, portal_leg=_touches_outside(trips))
    assert list(result.trips["mode"]) == ["car_passenger", "pt", "pt"]
    assert result.report["n_legs_lifted"] == 1 and result.report["actions"][LIFT_LEGS] == 1
    assert result.report["n_portal_legs_lifted"] == 1
    assert result.report["by_portal_class"]["rail"]["n_portal_legs_lifted"] == 1
    assert result.report["by_portal_class"]["none"]["n_portal_legs_lifted"] == 0


def test_a_portal_class_above_the_warn_share_warns_although_the_total_is_below_it(caplog):
    def part(n, **counts):
        actions = {a: 0 for a in P.ACTIONS}
        actions.update(counts)
        return {"n_tours_checked": n, "n_legs_probed": n, "n_legs_lifted": counts.get(LIFT_LEGS, 0),
                "n_portal_legs_lifted": 0, "actions": actions}
    none = part(98, connection_found=98)
    rail = part(2, connection_found=1, pt_legs_to_car_passenger_lift=1)
    actions = {a: none["actions"][a] + rail["actions"][a] for a in P.ACTIONS}
    report = {"n_tours_checked": 100, "n_legs_probed": 100, "n_legs_lifted": 1, "n_portal_legs_lifted": 0,
              "actions": actions,
              "by_portal_class": {"none": none, "road": part(0), "rail": rail}, "shift_abs_s_median": None}
    with caplog.at_level(logging.INFO):
        P.log_report(report, warn_share=0.05)
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "rail" in r.getMessage()]
