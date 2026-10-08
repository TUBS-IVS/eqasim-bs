"""Re-entry times from the diary and the fixed portal mode checked against the person's availability."""
import numpy as np
import pandas as pd

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


def test_reentry_time_adds_the_outside_share_of_the_reported_return_duration():
    # gate 15 km from the external point on a 60 km leg: 25 % of the 90 min happen outside
    out = t.reentry_times(_stays(), _trips(), gate_xy=np.array([[45000.0, 0.0]]), point_xy=np.array([[60000.0, 0.0]]))
    assert out.loc[0, "share_out"] == 0.25
    assert out.loc[0, "t_reentry"] == 17 * 3600.0 + 0.25 * 90 * 60.0
    assert out.loc[0, "inside_return_duration"] == 0.75 * 90 * 60.0
    assert not out.loc[0, "clamped"] and not out.loc[0, "share_capped"] and out.loc[0, "has_return"]


def test_reentry_share_is_one_when_the_reported_distance_is_missing():
    trips = _trips()
    trips.loc[1, "euclidean_distance"] = np.nan
    out = t.reentry_times(_stays(), trips, np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))
    assert out.loc[0, "share_out"] == 1.0 and out.loc[0, "share_capped"]
    assert out.loc[0, "t_reentry"] == 18.5 * 3600.0 and out.loc[0, "inside_return_duration"] == 0.0


def test_reentry_time_is_clamped_to_the_outbound_departure_and_counted():
    trips = _trips()
    trips.loc[1, ["departure_time", "arrival_time"]] = [7 * 3600.0, 7.5 * 3600.0]  # inconsistent diary
    out = t.reentry_times(_stays(), trips, np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))
    assert out.loc[0, "t_reentry"] == 8 * 3600.0 and out.loc[0, "clamped"]


def test_reentry_without_return_leg_has_no_time():
    out = t.reentry_times(_stays(return_index=np.nan), _trips(), np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))
    assert not out.loc[0, "has_return"] and np.isnan(out.loc[0, "t_reentry"])


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
        t.reentry_times(_stays(return_index=7.0), _trips(), np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))


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


def test_reentry_share_is_one_when_the_reported_distance_is_zero():
    trips = _trips()
    trips.loc[1, "euclidean_distance"] = 0.0
    out = t.reentry_times(_stays(), trips, np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))
    assert out.loc[0, "share_out"] == 1.0 and out.loc[0, "share_capped"]


def test_reentry_raises_when_the_outbound_departure_is_missing():
    import pytest
    trips = _trips()
    trips.loc[0, "departure_time"] = np.nan  # would silently disable the clamp
    with pytest.raises(ValueError, match="outbound"):
        t.reentry_times(_stays(), trips, np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))


def test_reentry_without_return_leg_tolerates_a_missing_outbound_departure():
    trips = _trips()
    trips.loc[0, "departure_time"] = np.nan
    out = t.reentry_times(_stays(return_index=np.nan), trips, np.array([[45000.0, 0.0]]), np.array([[60000.0, 0.0]]))
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
