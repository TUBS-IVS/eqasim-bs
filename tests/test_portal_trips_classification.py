"""Which legs leave the supplied area, and how consecutive far destinations form one stay."""
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point
import geopandas as gpd

from braunschweig.synthesis.portal_trips import classification as cls


def _trips(rows):
    frame = pd.DataFrame(rows, columns=["person_id", "trip_index", "preceding_purpose",
                                        "following_purpose", "mode", "euclidean_distance"])
    return frame.sort_values(["person_id", "trip_index"]).reset_index(drop=True)


def _home_xy():
    return pd.DataFrame({"x": [0.0, 0.0], "y": [0.0, 0.0]}, index=pd.Index([1, 2], name="person_id"))


def test_person_home_xy_joins_home_coordinates_through_the_household():
    persons = pd.DataFrame({"person_id": [1, 2], "household_id": [10, 20]})
    homes = gpd.GeoDataFrame({"household_id": [10, 20]},
                             geometry=[Point(1.0, 2.0), Point(3.0, 4.0)], crs="EPSG:25832")
    out = cls.person_home_xy(persons, homes)
    assert out.loc[1].tolist() == [1.0, 2.0] and out.loc[2].tolist() == [3.0, 4.0]


def test_classification_distance_uses_the_assigned_location_for_work_and_the_reported_one_otherwise():
    trips = _trips([(1, 0, "home", "work", "car", 5000.0),
                    (1, 1, "work", "leisure", "car", 60000.0)])
    work = pd.DataFrame({"x": [80000.0], "y": [0.0]}, index=pd.Index([1], name="person_id"))
    distance = cls.classification_distance_m(trips, _home_xy(), {"work": work, "education": work.iloc[0:0]})
    assert distance.tolist() == [80000.0, 60000.0]


def test_portal_flags_is_the_one_rule_beyond_threshold_finite_and_not_home():
    distance = pd.Series([50000.0, 45000.0, 50000.0, np.nan, 10000.0], index=[5, 6, 7, 8, 9])
    purpose = pd.Series(["work", "work", "home", "leisure", "leisure"], index=[5, 6, 7, 8, 9])
    flags = cls.portal_flags(distance, purpose, 45000.0)
    assert flags.tolist() == [True, False, False, False, False]    # strict '>', home never, NaN never
    assert flags.dtype == bool and flags.index.tolist() == [5, 6, 7, 8, 9] and flags.name == "is_portal"


def test_classify_portal_legs_applies_the_threshold_and_never_flags_home_or_nan():
    trips = _trips([(1, 0, "home", "leisure", "walk", 45000.0),
                    (1, 1, "leisure", "shop", "pt", 45000.1),
                    (1, 2, "shop", "home", "pt", 90000.0),
                    (1, 3, "home", "other", "car", np.nan)])
    flags = cls.classify_portal_legs(trips, _home_xy(), {"work": pd.DataFrame(columns=["x", "y"]),
                                                          "education": pd.DataFrame(columns=["x", "y"])},
                                     threshold_m=45000.0)
    assert flags.tolist() == [False, True, False, False]


def test_find_outside_stays_merges_consecutive_far_destinations():
    trips = _trips([(1, 0, "home", "leisure", "car", 1000.0),
                    (1, 1, "leisure", "shop", "car", 90000.0),
                    (1, 2, "shop", "other", "car", 5000.0),
                    (1, 3, "other", "home", "car", 95000.0)])
    stays = cls.find_outside_stays(trips, pd.Series([False, True, True, False]))
    assert stays.to_dict("records") == [
        {"person_id": 1, "outbound_trip_index": 1, "return_trip_index": 3.0, "n_removed_legs": 1}]


def test_find_outside_stays_splits_two_runs():
    trips = _trips([(1, 0, "home", "work", "car", 90000.0),
                    (1, 1, "work", "home", "car", 90000.0),
                    (1, 2, "home", "leisure", "car", 70000.0),
                    (1, 3, "leisure", "home", "car", 70000.0)])
    stays = cls.find_outside_stays(trips, pd.Series([True, False, True, False]))
    assert stays["outbound_trip_index"].tolist() == [0, 2]
    assert stays["return_trip_index"].tolist() == [1.0, 3.0]
    assert stays["n_removed_legs"].tolist() == [0, 0]


def test_find_outside_stays_marks_a_stay_without_return():
    trips = _trips([(2, 0, "home", "leisure", "pt", 1000.0),
                    (2, 1, "leisure", "other", "pt", 120000.0)])
    stays = cls.find_outside_stays(trips, pd.Series([False, True]))
    assert len(stays) == 1
    assert np.isnan(stays.loc[0, "return_trip_index"])


def test_find_outside_stays_never_joins_runs_across_persons():
    trips = _trips([(1, 0, "home", "leisure", "car", 90000.0),
                    (2, 0, "home", "leisure", "car", 90000.0),
                    (2, 1, "leisure", "home", "car", 90000.0)])
    stays = cls.find_outside_stays(trips, pd.Series([True, True, False]))
    assert stays["person_id"].tolist() == [1, 2]
    assert np.isnan(stays.loc[0, "return_trip_index"]) and stays.loc[1, "return_trip_index"] == 1.0


def _xy(person_ids, xs, ys=None):
    return pd.DataFrame({"x": xs, "y": ys if ys is not None else [0.0] * len(xs)},
                        index=pd.Index(person_ids, name="person_id"))


def test_distance_frame_reports_no_fallback_when_every_primary_leg_has_an_assigned_location():
    trips = _trips([(1, 0, "home", "work", "car", 1.0),
                    (1, 1, "work", "home", "car", 1.0),
                    (2, 0, "home", "education", "pt", 1.0),
                    (2, 1, "education", "leisure", "pt", 3000.0)])
    frame = cls.classification_distance_frame(
        trips, _home_xy(), {"work": _xy([1], [50000.0]), "education": _xy([2], [0.0], [30000.0])})
    assert frame["used_reported_distance"].sum() == 0
    assert frame["classification_distance_m"].tolist() == [50000.0, 1.0, 30000.0, 3000.0]


def test_distance_frame_flags_a_work_leg_without_assigned_location_as_fallback():
    trips = _trips([(1, 0, "home", "work", "car", 7000.0),
                    (1, 1, "work", "leisure", "car", 2000.0)])
    empty = _xy([], [])
    frame = cls.classification_distance_frame(trips, _home_xy(), {"work": empty, "education": empty})
    assert frame["used_reported_distance"].tolist() == [True, False]
    assert frame["classification_distance_m"].tolist() == [7000.0, 2000.0]
    assert cls.classification_distance_m(trips, _home_xy(), {"work": empty, "education": empty}).tolist() \
        == [7000.0, 2000.0]


def test_distance_frame_uses_the_assigned_education_location():
    trips = _trips([(1, 0, "home", "education", "bike", 100.0)])
    frame = cls.classification_distance_frame(
        trips, _home_xy(), {"work": _xy([], []), "education": _xy([1], [3000.0], [4000.0])})
    assert frame["classification_distance_m"].tolist() == [5000.0]
    assert frame["used_reported_distance"].tolist() == [False]


def test_coordinate_tables_reject_non_unique_ids():
    persons = pd.DataFrame({"person_id": [1], "household_id": [10]})
    homes = gpd.GeoDataFrame({"household_id": [10, 10]}, geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:25832")
    with pytest.raises(ValueError, match="household_id.*10"):
        cls.person_home_xy(persons, homes)
    work = gpd.GeoDataFrame({"person_id": [5, 5]}, geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:25832")
    with pytest.raises(ValueError, match="person_id.*5"):
        cls.primary_xy(work, work.iloc[0:0])


def test_find_outside_stays_rejects_flags_of_the_wrong_length():
    trips = _trips([(1, 0, "home", "leisure", "car", 1000.0), (1, 1, "leisure", "home", "car", 1000.0)])
    with pytest.raises(ValueError, match="is_portal"):
        cls.find_outside_stays(trips, pd.Series([True]))
