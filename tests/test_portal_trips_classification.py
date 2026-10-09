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


# --- Displacement upper bound for non-primary legs (ADR-0141, #442) ---------------------------------------------
# The donor's reported leg distance is a straight-line ESTIMATE (route length / detour factor 1.3). For a
# non-primary leg the diary offers a second estimate of the destination's distance from home: way out (last
# home departure up to the leg's origin) plus way back (the leg's destination up to the next home arrival). A leg
# whose own estimate exceeds it is inconsistent at estimate level; the classification distance takes the smaller
# estimate, min(reported, bound). A bound needs BOTH sides known, otherwise the reported distance stays.

_NO_PRIMARY = {"work": _xy([], []), "education": _xy([], [])}
_THRESHOLD_M = 45000.0


def _bound_frame(rows):
    trips = _trips(rows)
    return trips, cls.classification_distance_frame(trips, _home_xy(), _NO_PRIMARY)


def _flags(trips):
    return cls.classify_portal_legs(trips, _home_xy(), _NO_PRIMARY, _THRESHOLD_M).tolist()


def test_displacement_bound_round_trip_with_a_short_way_home_is_not_portal():
    trips, frame = _bound_frame([(1, 0, "home", "leisure", "car", 60000.0),
                                 (1, 1, "leisure", "home", "car", 5000.0)])
    assert frame["displacement_bound_m"].iloc[0] == 5000.0              # 0 (starts at home) + 5 km way back
    assert frame["classification_distance_m"].tolist() == [5000.0, 5000.0]
    assert frame["bound_applied"].tolist() == [True, False]
    assert frame["bound_unknown"].tolist() == [False, False]
    assert _flags(trips) == [False, False]


def test_displacement_bound_keeps_a_consistent_far_trip_portal():
    trips, frame = _bound_frame([(1, 0, "home", "leisure", "car", 60000.0),
                                 (1, 1, "leisure", "home", "car", 60000.0)])
    assert frame["classification_distance_m"].tolist() == [60000.0, 60000.0]   # bound 60 km is not smaller
    assert frame["bound_applied"].tolist() == [False, False]
    assert _flags(trips) == [True, False]


def test_displacement_bound_is_unknown_when_the_way_back_is_the_nan_home_closure():
    trips, frame = _bound_frame([(1, 0, "home", "leisure", "car", 60000.0),
                                 (1, 1, "leisure", "home", "car", np.nan)])
    assert np.isnan(frame["displacement_bound_m"].iloc[0])
    assert frame["classification_distance_m"].iloc[0] == 60000.0         # no silent guess
    assert frame["bound_applied"].tolist() == [False, False]
    assert frame["bound_unknown"].tolist() == [True, False]              # the home-bound leg is not counted
    assert _flags(trips) == [True, False]


def test_displacement_bound_adds_the_way_out_through_a_secondary_activity():
    trips, frame = _bound_frame([(1, 0, "home", "shop", "car", 10000.0),
                                 (1, 1, "shop", "leisure", "car", 60000.0),
                                 (1, 2, "leisure", "home", "car", 55000.0)])
    # shop -> leisure: way out 10 km + way back 55 km = 65 km; min(60, 65) = 60 km -> unchanged, portal.
    assert frame["displacement_bound_m"].iloc[1] == 65000.0
    assert frame["classification_distance_m"].tolist() == [10000.0, 60000.0, 55000.0]
    assert frame["bound_applied"].tolist() == [False, False, False]
    assert _flags(trips) == [False, True, False]


def test_displacement_bound_shrinks_a_long_second_leg_of_a_short_tour():
    trips, frame = _bound_frame([(1, 0, "home", "shop", "car", 10000.0),
                                 (1, 1, "shop", "leisure", "car", 60000.0),
                                 (1, 2, "leisure", "home", "car", 12000.0)])
    # shop -> leisure: 10 + 12 = 22 km < 60 km reported -> bounded, not portal.
    assert frame["classification_distance_m"].tolist()[1] == 22000.0
    assert frame["bound_applied"].tolist() == [False, True, False]      # leg 0: 0 + 72 km is not below 10 km
    assert _flags(trips) == [False, False, False]


def test_displacement_bound_sums_never_cross_a_home_arrival_or_a_person():
    trips, frame = _bound_frame([(1, 0, "home", "leisure", "car", 90000.0),
                                 (1, 1, "leisure", "home", "car", 2000.0),
                                 (1, 2, "home", "other", "car", 80000.0),
                                 (1, 3, "other", "home", "car", 70000.0),
                                 (2, 0, "home", "leisure", "car", 50000.0),
                                 (2, 1, "leisure", "home", "car", 1000.0)])
    # Each tour sees only its own way back: 2 km, 70 km (not 72 km), and 1 km for the next person.
    assert frame["classification_distance_m"].tolist() == [2000.0, 2000.0, 70000.0, 70000.0, 1000.0, 1000.0]
    assert frame["bound_applied"].tolist() == [True, False, True, False, True, False]
    assert _flags(trips) == [False, False, True, False, False, False]


def test_displacement_bound_does_not_change_a_work_leg_assigned_distance():
    trips = _trips([(1, 0, "home", "work", "car", 5000.0),
                    (1, 1, "work", "home", "car", 5000.0)])
    frame = cls.classification_distance_frame(trips, _home_xy(), {"work": _xy([1], [80000.0]),
                                                                 "education": _xy([], [])})
    assert frame["classification_distance_m"].tolist() == [80000.0, 5000.0]     # not cut to the 5 km way home
    assert frame["bound_applied"].tolist() == [False, False]
    assert frame["bound_unknown"].tolist() == [False, False]                    # primary legs are not counted
    assert np.isnan(frame["displacement_bound_m"].iloc[0])


def test_displacement_bound_through_a_work_activity_uses_the_reported_distance_of_the_work_leg():
    trips, frame = _bound_frame([(1, 0, "home", "work", "car", 20000.0),
                                 (1, 1, "work", "leisure", "car", 70000.0),
                                 (1, 2, "leisure", "home", "car", 25000.0)])
    # work -> leisure: way out = the reported 20 km home -> work leg, way back = 25 km -> bound 45 km.
    assert frame["displacement_bound_m"].iloc[1] == 45000.0
    assert frame["classification_distance_m"].iloc[1] == 45000.0
    assert bool(frame["bound_applied"].iloc[1])


def test_displacement_bound_needs_both_sides_a_chain_not_starting_at_home_is_unbounded():
    # The first tour starts at a non-home activity: the way out of its legs is unknown, so they stay unbounded
    # although the way back is known; the second (complete) tour is bounded normally.
    trips, frame = _bound_frame([(1, 0, "other", "leisure", "car", 60000.0),
                                 (1, 1, "leisure", "home", "car", 3000.0),
                                 (1, 2, "home", "shop", "car", 60000.0),
                                 (1, 3, "shop", "home", "car", 4000.0)])
    assert np.isnan(frame["displacement_bound_m"].iloc[0])
    assert frame["classification_distance_m"].tolist() == [60000.0, 3000.0, 4000.0, 4000.0]
    assert frame["bound_applied"].tolist() == [False, False, True, False]
    assert frame["bound_unknown"].tolist() == [True, False, False, False]
    assert _flags(trips) == [True, False, False, False]


def test_displacement_bound_needs_the_way_back_a_chain_not_reaching_home_is_unbounded():
    trips, frame = _bound_frame([(1, 0, "home", "leisure", "car", 60000.0),
                                 (1, 1, "leisure", "shop", "car", 3000.0)])
    assert frame["classification_distance_m"].tolist() == [60000.0, 3000.0]
    assert frame["bound_applied"].tolist() == [False, False]
    assert frame["bound_unknown"].tolist() == [True, True]


def test_displacement_bound_unknown_way_out_leg_blocks_the_bound():
    trips, frame = _bound_frame([(1, 0, "home", "shop", "car", np.nan),
                                 (1, 1, "shop", "leisure", "car", 60000.0),
                                 (1, 2, "leisure", "home", "car", 3000.0)])
    # Leg 0 has a known bound (0 + 63 km) but no reported distance of its own, so it stays NaN and never portal;
    # leg 1 has an unknown way out (the NaN leg), leg 2 is the home-bound leg.
    assert frame["bound_unknown"].tolist() == [False, True, False]
    assert frame["bound_applied"].tolist() == [False, False, False]
    assert np.isnan(frame["classification_distance_m"].iloc[0])
    assert frame["classification_distance_m"].iloc[1] == 60000.0


def test_displacement_bound_column_set_and_dtypes():
    trips, frame = _bound_frame([(1, 0, "home", "leisure", "car", 60000.0),
                                 (1, 1, "leisure", "home", "car", 5000.0)])
    assert list(frame.columns) == ["classification_distance_m", "used_reported_distance",
                                   "displacement_bound_m", "bound_applied", "bound_unknown",
                                   "non_primary", "reported_distance_missing"]
    for column in ("bound_applied", "bound_unknown", "non_primary", "reported_distance_missing"):
        assert frame[column].dtype == bool
    assert frame.index.equals(trips.index)


def _reference_displacement_bound_m(trips):
    """Straightforward per-leg walk of the bound definition (slow; the oracle for the vectorised version)."""
    bounds = np.full(len(trips), np.nan)
    for _, group in trips.groupby("person_id", sort=False):
        rows = group.index.to_numpy()
        leaves = (group["preceding_purpose"] == "home").to_numpy()
        arrives = (group["following_purpose"] == "home").to_numpy()
        distance = group["euclidean_distance"].to_numpy(dtype=float)
        n = len(rows)
        for i in range(n):
            if arrives[i]:
                continue
            known, way_out, j = True, 0.0, i
            while not leaves[j]:
                if j == 0 or arrives[j - 1]:
                    known = False
                    break
                j -= 1
                way_out += distance[j]
            way_back, k = 0.0, i
            while known and not arrives[k]:
                if k + 1 >= n or leaves[k + 1]:
                    known = False
                    break
                k += 1
                way_back += distance[k]
            if known and np.isfinite(way_out + way_back):
                bounds[rows[i]] = way_out + way_back
    return bounds


def test_displacement_bound_matches_the_per_leg_reference_on_random_chains():
    rng = np.random.default_rng(20261009)
    purposes = ["home", "work", "education", "shop", "leisure", "other"]
    rows = []
    for person_id in range(1, 301):
        n_legs = int(rng.integers(1, 9))
        # Mostly well-formed home tours with random breaks (missing home, NaN distances) to hit every branch.
        previous = "home" if rng.random() < 0.85 else str(rng.choice(purposes[1:]))
        for trip_index in range(n_legs):
            following = str(rng.choice(purposes, p=[0.3, 0.1, 0.05, 0.2, 0.2, 0.15]))
            distance = np.nan if rng.random() < 0.1 else float(rng.uniform(100.0, 90000.0))   # non-integer metres
            rows.append((person_id, trip_index, previous, following, "car", distance))
            previous = following if rng.random() < 0.93 else str(rng.choice(purposes))
    trips = _trips(rows)
    expected = _reference_displacement_bound_m(trips)
    actual = cls.displacement_bound_m(trips)
    assert np.isfinite(expected).sum() > 100 and np.isnan(expected).sum() > 100    # both branches are exercised
    np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))             # identical NaN pattern
    finite = np.isfinite(expected)
    np.testing.assert_allclose(actual[finite], expected[finite], rtol=1e-9, atol=1e-6)


def test_displacement_bound_of_an_empty_table_is_empty():
    assert cls.displacement_bound_m(_trips([]).astype({"euclidean_distance": float})).shape == (0,)


def test_displacement_bound_marks_the_non_primary_legs_and_the_legs_without_own_distance():
    trips, frame = _bound_frame([(1, 0, "home", "shop", "car", np.nan),
                                 (1, 1, "shop", "home", "car", 3000.0),
                                 (1, 2, "home", "work", "car", 7000.0),
                                 (1, 3, "work", "home", "car", 7000.0)])
    # non-primary = neither work/education nor the way home; a NaN own distance is a separate diagnostic.
    assert frame["non_primary"].tolist() == [True, False, False, False]
    assert frame["reported_distance_missing"].tolist() == [True, False, False, False]
    # Leg 0 has a known bound (0 + 3 km) but no own distance to tighten: neither applied nor unknown.
    assert frame["displacement_bound_m"].iloc[0] == 3000.0
    assert frame["bound_applied"].tolist() == [False, False, False, False]
    assert frame["bound_unknown"].tolist() == [False, False, False, False]
    assert np.isnan(frame["classification_distance_m"].iloc[0])


def test_displacement_bound_rejects_trips_not_sorted_by_person_and_trip_index():
    unsorted_persons = _trips([(2, 0, "home", "leisure", "car", 1000.0), (2, 1, "leisure", "home", "car", 1000.0),
                               (1, 0, "home", "leisure", "car", 1000.0), (1, 1, "leisure", "home", "car", 1000.0)])
    unsorted_persons = pd.concat([unsorted_persons.iloc[2:], unsorted_persons.iloc[:2]], ignore_index=True)
    with pytest.raises(ValueError, match="sorted by person_id, trip_index"):
        cls.displacement_bound_m(unsorted_persons)
    unsorted_legs = _trips([(1, 0, "home", "leisure", "car", 1000.0), (1, 1, "leisure", "home", "car", 1000.0)])
    unsorted_legs = unsorted_legs.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="sorted by person_id, trip_index"):
        cls.classification_distance_frame(unsorted_legs, _home_xy(), _NO_PRIMARY)
