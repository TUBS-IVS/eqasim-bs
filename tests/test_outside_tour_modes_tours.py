import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.synthesis.outside_tour_modes import tours as T

CRS = "EPSG:25832"


def _trips(rows):
    columns = ["person_id", "trip_index", "preceding_purpose", "following_purpose", "mode"]
    return pd.DataFrame(rows, columns=columns)


def _locations(rows):
    frame = pd.DataFrame(rows, columns=["person_id", "activity_index", "x", "y"])
    return gpd.GeoDataFrame(frame[["person_id", "activity_index"]],
                            geometry=[Point(x, y) for x, y in zip(frame["x"], frame["y"])], crs=CRS)


def test_require_sorted_raises_on_an_unsorted_frame():
    with pytest.raises(ValueError, match="person_id, trip_index"):
        T.require_sorted(_trips([(1, 1, "work", "home", "car"), (1, 0, "home", "work", "car")]))


def test_home_based_tour_ids_split_at_every_home_departure():
    trips = _trips([
        (1, 0, "home", "work", "car"), (1, 1, "work", "shop", "car"), (1, 2, "shop", "home", "car"),
        (1, 3, "home", "leisure", "walk"), (1, 4, "leisure", "home", "walk"),
        (2, 0, "home", "education", "bicycle"), (2, 1, "education", "home", "bicycle"),
    ])
    assert T.home_based_tour_ids(trips).tolist() == [0, 0, 0, 1, 1, 0, 0]


def test_home_based_tour_ids_opens_a_tour_at_the_chain_start():
    # A chain that starts away from home (first preceding purpose is work) still belongs to tour 0,
    # and the next home departure opens tour 1.
    trips = _trips([(3, 0, "work", "home", "pt"), (3, 1, "home", "shop", "walk"), (3, 2, "shop", "home", "walk")])
    assert T.home_based_tour_ids(trips).tolist() == [0, 1, 1]


def test_home_based_tour_ids_reset_per_person_after_a_person_with_several_tours():
    # Person 1 ends on tour 2; person 2 starts away from home (tour 0, then 1); person 3 starts at home.
    trips = _trips([
        (1, 0, "home", "work", "car"), (1, 1, "work", "home", "car"),
        (1, 2, "home", "shop", "car"), (1, 3, "shop", "home", "car"),
        (1, 4, "home", "leisure", "car"),
        (2, 0, "work", "home", "pt"), (2, 1, "home", "shop", "pt"), (2, 2, "shop", "home", "pt"),
        (3, 0, "home", "work", "walk"), (3, 1, "work", "home", "walk"),
        (4, 0, "shop", "leisure", "walk"), (4, 1, "leisure", "work", "walk"),
    ])
    assert T.home_based_tour_ids(trips).tolist() == [0, 0, 1, 1, 2, 0, 1, 1, 0, 0, 0, 0]


def test_home_based_tour_ids_handle_empty_and_single_leg_frames():
    assert T.home_based_tour_ids(_trips([])).tolist() == []
    assert T.home_based_tour_ids(_trips([(1, 0, "home", "work", "car")])).tolist() == [0]


def test_leg_endpoints_join_origin_and_destination_coordinates():
    trips = _trips([(1, 0, "home", "work", "car"), (1, 1, "work", "home", "car")])
    locations = _locations([(1, 0, 0.0, 0.0), (1, 1, 3000.0, 4000.0), (1, 2, 0.0, 0.0)])
    endpoints = T.leg_endpoints(trips, locations)
    assert endpoints["ox"].tolist() == [0.0, 3000.0] and endpoints["dx"].tolist() == [3000.0, 0.0]
    assert T.leg_distance_m(endpoints).tolist() == [5000.0, 5000.0]


def test_leg_endpoints_raise_on_a_missing_location():
    trips = _trips([(1, 0, "home", "work", "car"), (1, 1, "work", "home", "car")])
    locations = _locations([(1, 0, 0.0, 0.0), (1, 1, 3000.0, 4000.0)])   # activity 2 missing
    with pytest.raises(ValueError, match="person_id 1"):
        T.leg_endpoints(trips, locations)


def test_leg_endpoints_raise_on_duplicated_location_rows():
    trips = _trips([(1, 0, "home", "work", "car")])
    locations = _locations([(1, 0, 0.0, 0.0), (1, 1, 1.0, 1.0), (1, 1, 2.0, 2.0)])
    with pytest.raises(ValueError, match="duplicated"):
        T.leg_endpoints(trips, locations)


def test_destination_outside_tests_the_destination_against_the_polygon():
    trips = _trips([(1, 0, "home", "work", "car"), (1, 1, "work", "home", "car")])
    locations = _locations([(1, 0, 100.0, 100.0), (1, 1, 5000.0, 100.0), (1, 2, 100.0, 100.0)])
    outside = T.destination_outside(T.leg_endpoints(trips, locations), box(0.0, 0.0, 1000.0, 1000.0))
    assert outside.tolist() == [True, False]


def test_outside_tour_flags_mark_every_leg_of_a_tour_with_an_outside_destination():
    trips = _trips([
        (1, 0, "home", "work", "car"), (1, 1, "work", "shop", "car"), (1, 2, "shop", "home", "car"),
        (1, 3, "home", "leisure", "walk"), (1, 4, "leisure", "home", "walk"),
    ])
    tour_ids = T.home_based_tour_ids(trips)
    dest_outside = np.array([True, False, False, False, False])
    assert T.outside_tour_flags(trips, tour_ids, dest_outside).tolist() == [True, True, True, False, False]


def test_outside_tour_flags_use_the_purpose_for_portal_gates():
    # A portal gate lies inside the extent (no point is outside) but its purpose is "outside".
    trips = _trips([(1, 0, "home", "outside", "car"), (1, 1, "outside", "home", "car"),
                    (1, 2, "home", "shop", "walk"), (1, 3, "shop", "home", "walk")])
    tour_ids = T.home_based_tour_ids(trips)
    flags = T.outside_tour_flags(trips, tour_ids, np.zeros(4, dtype=bool))
    assert flags.tolist() == [True, True, False, False]


def test_outside_tour_flags_do_not_leak_across_persons_with_equal_tour_ids():
    # Both persons have tour 0; only person 1 goes outside.
    trips = _trips([(1, 0, "home", "work", "car"), (1, 1, "work", "home", "car"),
                    (2, 0, "home", "work", "car"), (2, 1, "work", "home", "car")])
    flags = T.outside_tour_flags(trips, T.home_based_tour_ids(trips), np.array([True, False, False, False]))
    assert flags.tolist() == [True, True, False, False]
