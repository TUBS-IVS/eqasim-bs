"""Re-entry times from the diary and the fixed portal mode checked against the person's availability."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.portal_trips import modes as m
from braunschweig.synthesis.portal_trips import timing as t


def _trips():
    # home 08:00 -> far leisure (arrive 09:30), back 17:00 -> home 18:30 (reported 90 min, 60 km)
    return pd.DataFrame({
        "person_id": [1, 1], "trip_index": [0, 1],
        "departure_time": [8 * 3600.0, 17 * 3600.0], "arrival_time": [9.5 * 3600.0, 18.5 * 3600.0],
        "euclidean_distance": [60000.0, 60000.0], "mode": ["car", "car"],
    })


def _stays(return_index=1.0):
    return pd.DataFrame({"person_id": [1], "outbound_trip_index": [0],
                         "return_trip_index": [return_index], "n_removed_legs": [0]})


ORIGIN = np.array([[0.0, 0.0]])
GATE = np.array([[45000.0, 0.0]])
POINT = np.array([[60000.0, 0.0]])


def test_reentry_time_splits_the_diary_duration_by_the_synthetic_geometry():
    # origin (0, 0), gate (45 km, 0), point (60 km, 0): outside 15 km, return proxy = origin -> inside 45 km, so
    # share_out = 15 / (15 + 45) = 0.25 of the 90 min (5400 s) return leg happen outside.
    out = t.reentry_times(_stays(), _trips(), GATE, POINT, ORIGIN)
    assert list(out.columns) == t.TIMING_COLUMNS
    assert out.loc[0, "share_out"] == 0.25
    assert out.loc[0, "t_reentry"] == 17 * 3600.0 + 0.25 * 90 * 60.0
    assert out.loc[0, "inside_return_duration"] == 0.75 * 90 * 60.0
    assert not out.loc[0, "clamped"] and not out.loc[0, "share_degenerate"] and out.loc[0, "has_return"]


def test_reentry_share_ignores_a_donor_reported_distance_far_below_the_synthetic_geometry():
    # origin (0, 0), gate (40 km, 0), point (100 km, 0): outside 60 km, inside 40 km -> share_out = 60 / 100 = 0.6.
    # The donor reports only 13.8 km (a different trip); the old rule capped the share at 1 (inside time 0).
    trips = _trips()
    trips.loc[1, "euclidean_distance"] = 13800.0
    trips.loc[1, ["departure_time", "arrival_time"]] = [17 * 3600.0, 18 * 3600.0]       # duration 3600 s
    out = t.reentry_times(_stays(), trips, np.array([[40000.0, 0.0]]), np.array([[100000.0, 0.0]]), ORIGIN)
    assert out.loc[0, "share_out"] == pytest.approx(0.6)
    assert out.loc[0, "inside_return_duration"] == pytest.approx(1440.0)
    assert out.loc[0, "t_reentry"] == pytest.approx(17 * 3600.0 + 2160.0)
    assert not out.loc[0, "share_degenerate"]


@pytest.mark.parametrize("reported", [np.nan, 0.0, 5.0])
def test_reentry_share_is_independent_of_the_reported_distance(reported):
    trips = _trips()
    trips.loc[1, "euclidean_distance"] = reported
    out = t.reentry_times(_stays(), trips, GATE, POINT, ORIGIN)
    assert out.loc[0, "share_out"] == 0.25 and not out.loc[0, "share_degenerate"]
    assert out.loc[0, "t_reentry"] == 17 * 3600.0 + 0.25 * 90 * 60.0


def test_reentry_share_is_one_half_and_counted_when_both_distances_are_zero():
    # The point and the return proxy both sit at the gate: nothing to split by, so share 0.5, flagged.
    out = t.reentry_times(_stays(), _trips(), GATE, GATE, GATE)
    assert out.loc[0, "share_out"] == 0.5 and out.loc[0, "share_degenerate"]
    assert out.loc[0, "t_reentry"] == 17 * 3600.0 + 0.5 * 90 * 60.0


def test_reentry_time_is_clamped_to_the_outbound_departure_and_counted():
    trips = _trips()
    trips.loc[1, ["departure_time", "arrival_time"]] = [7 * 3600.0, 7.5 * 3600.0]  # inconsistent diary
    out = t.reentry_times(_stays(), trips, GATE, POINT, ORIGIN)
    assert out.loc[0, "t_reentry"] == 8 * 3600.0 and out.loc[0, "clamped"]


def test_outbound_arrival_is_the_inside_share_of_the_diary_outbound_duration():
    # Outbound leg departs 08:00, arrives 09:30 (5400 s). Origin (0, 0) -> gate (45 km, 0) = 45 km inside,
    # gate -> point (60 km, 0) = 15 km outside: share_in = 45 / 60 = 0.75, arrival = 28800 + 0.75 * 5400 = 32850 s.
    out = t.outbound_arrivals(_stays(), _trips(), ORIGIN, GATE, POINT)
    assert list(out.columns) == t.OUTBOUND_COLUMNS
    assert out.loc[0, "outbound_share"] == 0.75
    assert out.loc[0, "outbound_arrival_time"] == 32850.0
    assert not out.loc[0, "outbound_share_degenerate"]


def test_outbound_share_uses_the_synthetic_geometry_not_the_reported_distance():
    # origin (0, 0), gate (40 km, 0), point (100 km, 0): share_in = 40 / (40 + 60) = 0.4 although the donor
    # reports 13.8 km (the old rule capped this at 1, i.e. the leg kept its whole duration).
    trips = _trips()
    trips.loc[0, "euclidean_distance"] = 13800.0
    out = t.outbound_arrivals(_stays(), trips, ORIGIN, np.array([[40000.0, 0.0]]), np.array([[100000.0, 0.0]]))
    assert out.loc[0, "outbound_share"] == pytest.approx(0.4)
    assert out.loc[0, "outbound_arrival_time"] == pytest.approx(8 * 3600.0 + 0.4 * 5400.0)


@pytest.mark.parametrize("reported", [np.nan, 0.0])
def test_outbound_share_is_independent_of_a_missing_reported_distance(reported):
    trips = _trips()
    trips.loc[0, "euclidean_distance"] = reported
    out = t.outbound_arrivals(_stays(), trips, ORIGIN, GATE, POINT)
    assert out.loc[0, "outbound_share"] == 0.75 and out.loc[0, "outbound_arrival_time"] == 32850.0
    assert not out.loc[0, "outbound_share_degenerate"]


def test_outbound_share_is_one_half_and_counted_when_origin_gate_and_point_coincide():
    out = t.outbound_arrivals(_stays(), _trips(), GATE, GATE, GATE)
    assert out.loc[0, "outbound_share"] == 0.5 and out.loc[0, "outbound_share_degenerate"]
    assert out.loc[0, "outbound_arrival_time"] == 8 * 3600.0 + 0.5 * 5400.0


def test_shares_stay_within_zero_and_one_on_random_geometry():
    rng = np.random.default_rng(42)
    n = 500
    trips = pd.DataFrame({
        "person_id": np.repeat(np.arange(n), 2), "trip_index": np.tile([0, 1], n),
        "departure_time": np.tile([8 * 3600.0, 17 * 3600.0], n), "arrival_time": np.tile([9 * 3600.0, 18 * 3600.0], n),
        "euclidean_distance": rng.uniform(0.0, 200000.0, 2 * n), "mode": "car"})
    stays = pd.DataFrame({"person_id": np.arange(n), "outbound_trip_index": 0, "return_trip_index": 1.0,
                          "n_removed_legs": 0})
    origin, gate, point = (rng.uniform(-100000.0, 100000.0, (n, 2)) for _ in range(3))
    outbound = t.outbound_arrivals(stays, trips, origin, gate, point)
    times = t.reentry_times(stays, trips, gate, point, origin,
                            outbound_arrival_time=outbound["outbound_arrival_time"].to_numpy())
    for share in (outbound["outbound_share"], times["share_out"]):
        assert share.between(0.0, 1.0).all()
    assert (times["inside_return_duration"] >= 0.0).all()
    assert not outbound["outbound_share_degenerate"].any() and not times["share_degenerate"].any()


def test_timing_rejects_non_finite_coordinates_instead_of_calling_them_degenerate():
    nan_point = np.array([[np.nan, 0.0]])
    with pytest.raises(ValueError, match="non-finite"):
        t.outbound_arrivals(_stays(), _trips(), ORIGIN, GATE, nan_point)
    with pytest.raises(ValueError, match="non-finite"):
        t.reentry_times(_stays(), _trips(), GATE, nan_point, ORIGIN)


def test_outbound_arrival_raises_when_the_outbound_times_are_not_available():
    trips = _trips()
    trips.loc[0, "arrival_time"] = np.nan
    with pytest.raises(ValueError, match="inconsistent"):
        t.outbound_arrivals(_stays(), trips, ORIGIN, GATE, POINT)


def test_reentry_is_clamped_up_to_the_arrival_at_the_gate_when_that_is_given():
    # Inconsistent diary: the return leg departs 07:00, before the outbound leg even arrives at the gate (08:30).
    # The gate activity must not end before it starts, so the clamp lower bound is the outbound arrival.
    trips = _trips()
    trips.loc[1, ["departure_time", "arrival_time"]] = [7 * 3600.0, 7.5 * 3600.0]
    out = t.reentry_times(_stays(), trips, GATE, POINT, ORIGIN, outbound_arrival_time=np.array([8.5 * 3600.0]))
    assert out.loc[0, "t_reentry"] == 8.5 * 3600.0 and out.loc[0, "clamped"]
    assert out.loc[0, "inside_return_duration"] == 0.0


def test_reentry_without_return_leg_has_no_time_and_is_not_counted_degenerate():
    out = t.reentry_times(_stays(return_index=np.nan), _trips(), GATE, GATE, GATE)
    assert not out.loc[0, "has_return"] and np.isnan(out.loc[0, "t_reentry"])
    assert not out.loc[0, "share_degenerate"] and np.isnan(out.loc[0, "share_out"])


def _availability(car="all", licence=True, bicycle="all", passenger="some"):
    persons = pd.DataFrame({"person_id": [1], "car_availability": [car], "has_license": [licence],
                            "bicycle_availability": [bicycle], "car_passenger_availability": [passenger]})
    return m.mode_availability(persons)


def test_mode_availability_mirrors_the_java_mode_availability():
    assert _availability().loc[1].tolist() == [True, True, True, True]
    assert not bool(_availability(car="none").loc[1, "can_car"])
    assert not bool(_availability(licence=False).loc[1, "can_car"])
    assert not bool(_availability(bicycle="none").loc[1, "can_bicycle"])
    assert not bool(_availability(passenger="none").loc[1, "can_car_passenger"])


def test_mode_availability_without_the_passenger_column_falls_back_to_car_availability():
    persons = pd.DataFrame({"person_id": [1, 2], "car_availability": ["none", "some"],
                            "has_license": [False, False], "bicycle_availability": ["all", "all"]})
    out = m.mode_availability(persons)
    assert out["can_car_passenger"].tolist() == [False, True]


def test_portal_modes_keep_the_outbound_mode_and_count_a_differing_return_mode():
    trips = _trips()
    trips.loc[1, "mode"] = "car_passenger"
    out = m.portal_modes(_stays(), trips, _availability())
    assert out.loc[0, "mode"] == "car" and out.loc[0, "return_mode_differs"]
    assert out.loc[0, "substituted_from"] is None and out.loc[0, "substitution_reason"] is None


def test_portal_modes_substitute_in_the_fixed_order_with_the_reason():
    out = m.portal_modes(_stays(), _trips(), _availability(car="none", passenger="none"))
    assert out.loc[0, "mode"] == "pt"
    assert out.loc[0, "substituted_from"] == "car" and out.loc[0, "substitution_reason"] == "no_car_availability"
    out = m.portal_modes(_stays(), _trips(), _availability(licence=False, passenger="some"))
    assert out.loc[0, "mode"] == "car_passenger" and out.loc[0, "substitution_reason"] == "no_driving_licence"


def test_reentry_raises_when_the_return_leg_is_missing_from_the_trips_table():
    import pytest
    with pytest.raises(ValueError, match="inconsistent"):
        t.reentry_times(_stays(return_index=7.0), _trips(), GATE, POINT, ORIGIN)


def test_portal_modes_handle_several_stays_in_order_with_bicycle_and_walk():
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2, 3, 3], "trip_index": [0, 1, 0, 1, 0, 1],
        "departure_time": 0.0, "arrival_time": 0.0, "euclidean_distance": 1.0,
        "mode": ["bicycle", "bicycle", "walk", "walk", "car_passenger", "pt"],
    })
    stays = pd.DataFrame({"person_id": [1, 2, 3], "outbound_trip_index": [0, 0, 0],
                          "return_trip_index": [1.0, np.nan, 1.0], "n_removed_legs": 0})
    persons = pd.DataFrame({"person_id": [1, 2, 3], "car_availability": ["none", "none", "none"],
                            "has_license": [False, False, False], "bicycle_availability": ["none", "none", "none"],
                            "car_passenger_availability": ["none", "none", "none"]})
    out = m.portal_modes(stays, trips, m.mode_availability(persons))
    assert out["mode"].tolist() == ["pt", "walk", "pt"]
    assert out["substitution_reason"].tolist() == ["no_bicycle_availability", None, "no_car_passenger_availability"]
    assert out["return_mode"].tolist() == ["bicycle", None, "pt"]
    assert out["return_mode_differs"].tolist() == [False, False, True]


def test_portal_modes_raise_for_a_person_without_an_availability_row():
    import pytest
    availability = _availability()
    availability.index = [99]
    with pytest.raises(ValueError, match="no availability row"):
        m.portal_modes(_stays(), _trips(), availability)


def test_reentry_raises_when_the_outbound_departure_is_missing():
    import pytest
    trips = _trips()
    trips.loc[0, "departure_time"] = np.nan  # would silently disable the clamp
    with pytest.raises(ValueError, match="outbound"):
        t.reentry_times(_stays(), trips, GATE, POINT, ORIGIN)


def test_reentry_without_return_leg_tolerates_a_missing_outbound_departure():
    trips = _trips()
    trips.loc[0, "departure_time"] = np.nan
    out = t.reentry_times(_stays(return_index=np.nan), trips, GATE, POINT, ORIGIN)
    assert not out.loc[0, "has_return"]


def test_portal_modes_raise_when_the_return_trip_is_missing_from_the_trips_table():
    import pytest
    with pytest.raises(ValueError, match=r"person_id 1.*return.*7"):
        m.portal_modes(_stays(return_index=7.0), _trips(), _availability())


def test_mode_availability_warns_once_when_the_passenger_column_is_absent(caplog):
    import logging
    persons = pd.DataFrame({"person_id": [1, 2], "car_availability": ["none", "some"],
                            "has_license": [False, False], "bicycle_availability": ["all", "all"]})
    with caplog.at_level(logging.WARNING, logger=m.logger.name):
        m.mode_availability(persons)
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "car_passenger_availability" in warnings[0].getMessage() and "2 persons" in warnings[0].getMessage()


def test_mode_availability_does_not_warn_when_the_passenger_column_is_present(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger=m.logger.name):
        _availability()
    assert not [record for record in caplog.records if record.levelno == logging.WARNING]
