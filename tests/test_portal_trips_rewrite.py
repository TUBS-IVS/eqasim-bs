# tests/test_portal_trips_rewrite.py
"""The rewritten trip table: outside stays at the gate, fixed modes, diary re-entry, recomputed chain columns."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.portal_trips import rewrite as rw

CRS = "EPSG:25832"


def _chain():
    # home -(walk 1 km)-> shop -(car 90 km)-> leisure -(car 5 km)-> other -(car 95 km)-> home
    rows = [
        (1, 0, "home", "shop", "walk", 1000.0, 8 * 3600.0, 8.25 * 3600.0),
        (1, 1, "shop", "leisure", "car", 90000.0, 9 * 3600.0, 10.5 * 3600.0),
        (1, 2, "leisure", "other", "car", 5000.0, 14 * 3600.0, 14.25 * 3600.0),
        (1, 3, "other", "home", "car", 95000.0, 17 * 3600.0, 18.5 * 3600.0),
    ]
    trips = pd.DataFrame(rows, columns=["person_id", "trip_index", "preceding_purpose", "following_purpose",
                                        "mode", "euclidean_distance", "departure_time", "arrival_time"])
    trips["trip_key"] = ["1_1", "1_2", "1_3", "1_closure"]
    trips["is_synthetic_closure"] = [False, False, False, True]
    return rw.recompute_chain_columns(trips)


def _inputs(trips):
    stays = pd.DataFrame({"person_id": [1], "outbound_trip_index": [1], "return_trip_index": [3.0], "n_removed_legs": [1]})
    gate_rows = pd.DataFrame({"gate_id": ["gate_e"], "kind": ["road"], "x": [40000.0], "y": [0.0]})
    # Outbound arrival (R32), derived from the diary: the outbound leg departs 09:00 and reports 90 min for 90 km;
    # origin (0, 0) -> gate (40 km, 0) is 40 km inside, so share = 40/90 = 4/9 and
    # arrival = 09:00 + 4/9 * 5400 s = 32400 + 2400 = 34800 s (09:40).
    times = pd.DataFrame({"t_reentry": [17 * 3600.0 + 1800.0], "share_out": [0.5], "inside_return_duration": [2700.0],
                          "clamped": [False], "share_capped": [False], "has_return": [True],
                          "outbound_arrival_time": [34800.0]})
    modes = pd.DataFrame({"mode": ["car"], "outbound_mode": ["car"], "return_mode": ["car"],
                          "substituted_from": [None], "substitution_reason": [None], "return_mode_differs": [False]})
    origin_xy = np.array([[0.0, 0.0]])
    return stays, gate_rows, times, modes, origin_xy


def test_recompute_chain_columns_sets_index_flags_and_durations():
    trips = _chain()
    assert trips["trip_index"].tolist() == [0, 1, 2, 3]
    assert trips["is_first_trip"].tolist() == [True, False, False, False]
    assert trips["is_last_trip"].tolist() == [False, False, False, True]
    assert trips["trip_duration"].tolist() == [900.0, 5400.0, 900.0, 5400.0]
    assert trips["activity_duration"].tolist()[:3] == [2700.0, 12600.0, 9900.0]
    assert np.isnan(trips["activity_duration"].iloc[3])


def test_rewrite_builds_one_outside_stay_with_fixed_mode_and_diary_reentry():
    trips = _chain()
    out, anchors = rw.rewrite_trips(trips, *_inputs(trips), crs=CRS)
    assert out["trip_index"].tolist() == [0, 1, 2]
    assert out["following_purpose"].tolist() == ["shop", "outside", "home"]
    assert out["preceding_purpose"].tolist() == ["home", "shop", "outside"]
    assert out["mode"].tolist() == ["walk", "car", "car"]
    assert out[rw.PORTAL_LEG_COLUMN].tolist() == [False, True, True]
    assert out["departure_time"].tolist() == [8 * 3600.0, 9 * 3600.0, 17 * 3600.0 + 1800.0]
    # the outbound leg arrives at the gate after its inside share of the reported duration (R32), not at the
    # original diary arrival 10:30 that belongs to the far destination
    assert out["arrival_time"].tolist()[1] == 34800.0
    assert out["trip_duration"].tolist()[1] == 2400.0
    # the gate activity lasts from the outbound arrival to the re-entry departure
    assert out["activity_duration"].tolist()[1] == 17 * 3600.0 + 1800.0 - 34800.0
    assert out["arrival_time"].tolist()[2] == 17 * 3600.0 + 1800.0 + 2700.0
    # the inside part only: origin proxy (home) -> gate, gate -> home
    assert out["euclidean_distance"].tolist()[1:] == [40000.0, 40000.0]
    assert out["is_last_trip"].tolist() == [False, False, True]
    assert out["trip_key"].tolist() == ["1_1", "1_2", "1_closure"]
    assert list(anchors.columns) == rw.ANCHOR_COLUMNS
    assert anchors.iloc[0][["person_id", "activity_index", "gate_id", "kind", "mode"]].tolist() == [1, 2, "gate_e", "road", "car"]
    assert anchors.geometry.iloc[0].coords[0] == (40000.0, 0.0)


def test_rewrite_uses_home_as_origin_proxy_when_the_leg_leaves_a_secondary_activity():
    trips = _chain()
    stays, gate_rows, times, modes, origin_xy = _inputs(trips)
    out, _ = rw.rewrite_trips(trips, stays, gate_rows, times, modes, origin_xy, crs=CRS)
    # the outbound leg leaves the (unplaced) shop; its inside distance is measured from the proxy origin
    assert out.loc[1, "euclidean_distance"] == 40000.0


def test_rewrite_handles_a_stay_without_return():
    trips = _chain().iloc[:2].copy()
    trips = rw.recompute_chain_columns(trips)
    stays = pd.DataFrame({"person_id": [1], "outbound_trip_index": [1], "return_trip_index": [np.nan], "n_removed_legs": [0]})
    gate_rows = pd.DataFrame({"gate_id": ["gate_e"], "kind": ["road"], "x": [40000.0], "y": [0.0]})
    times = pd.DataFrame({"t_reentry": [np.nan], "share_out": [np.nan], "inside_return_duration": [np.nan],
                          "clamped": [False], "share_capped": [False], "has_return": [False],
                          "outbound_arrival_time": [34800.0]})
    modes = pd.DataFrame({"mode": ["car"], "outbound_mode": ["car"], "return_mode": [None],
                          "substituted_from": [None], "substitution_reason": [None], "return_mode_differs": [False]})
    out, anchors = rw.rewrite_trips(trips, stays, gate_rows, times, modes, np.array([[0.0, 0.0]]), crs=CRS)
    assert out["following_purpose"].tolist() == ["shop", "outside"] and out["is_last_trip"].tolist() == [False, True]
    assert len(anchors) == 1 and anchors.loc[0, "activity_index"] == 2


def test_rewrite_two_stays_in_one_chain():
    rows = [(1, 0, "home", "work", "car", 90000.0, 7 * 3600.0, 8 * 3600.0),
            (1, 1, "work", "home", "car", 90000.0, 12 * 3600.0, 13 * 3600.0),
            (1, 2, "home", "leisure", "car", 70000.0, 15 * 3600.0, 16 * 3600.0),
            (1, 3, "leisure", "home", "car", 70000.0, 20 * 3600.0, 21 * 3600.0)]
    trips = rw.recompute_chain_columns(pd.DataFrame(rows, columns=[
        "person_id", "trip_index", "preceding_purpose", "following_purpose", "mode", "euclidean_distance",
        "departure_time", "arrival_time"]))
    stays = pd.DataFrame({"person_id": [1, 1], "outbound_trip_index": [0, 2], "return_trip_index": [1.0, 3.0],
                          "n_removed_legs": [0, 0]})
    gate_rows = pd.DataFrame({"gate_id": ["g1", "g2"], "kind": ["road", "road"], "x": [40000.0, -40000.0], "y": [0.0, 0.0]})
    # Outbound arrivals: 07:00 + 40/90 * 3600 s = 25200 + 1600 = 26800 s; 15:00 + 40/70 * 3600 s = 54000 + 2057.142857
    # = 56057.142857 s (distances in km, durations 1 h each).
    times = pd.DataFrame({"t_reentry": [12.5 * 3600.0, 20.5 * 3600.0], "share_out": [0.5, 0.5],
                          "inside_return_duration": [1800.0, 1800.0], "clamped": [False, False],
                          "share_capped": [False, False], "has_return": [True, True],
                          "outbound_arrival_time": [26800.0, 54000.0 + 3600.0 * 40.0 / 70.0]})
    modes = pd.DataFrame({"mode": ["car", "car"], "outbound_mode": ["car", "car"], "return_mode": ["car", "car"],
                          "substituted_from": [None, None], "substitution_reason": [None, None],
                          "return_mode_differs": [False, False]})
    out, anchors = rw.rewrite_trips(trips, stays, gate_rows, times, modes, np.zeros((2, 2)), crs=CRS)
    assert out["following_purpose"].tolist() == ["outside", "home", "outside", "home"]
    assert anchors["activity_index"].tolist() == [1, 3]
    assert out[rw.PORTAL_LEG_COLUMN].all()
    # plan times stay monotonic: departure <= arrival of each leg and arrival <= next departure
    assert out["arrival_time"].tolist()[0] == 26800.0
    assert (out["trip_duration"] >= 0).all() and (out["activity_duration"].dropna() >= 0).all()


def test_rewrite_without_stays_returns_an_equal_frame_with_the_flag_column():
    trips = _chain()
    out, anchors = rw.rewrite_trips(trips, pd.DataFrame(columns=["person_id", "outbound_trip_index", "return_trip_index", "n_removed_legs"]),
                                    pd.DataFrame(columns=["gate_id", "kind", "x", "y"]),
                                    pd.DataFrame(columns=["t_reentry", "share_out", "inside_return_duration", "clamped", "share_capped", "has_return",
                                                          "outbound_arrival_time"]),
                                    pd.DataFrame(columns=["mode", "outbound_mode", "return_mode", "substituted_from", "substitution_reason", "return_mode_differs"]),
                                    np.zeros((0, 2)), crs=CRS)
    pd.testing.assert_frame_equal(out.drop(columns=[rw.PORTAL_LEG_COLUMN]), trips)
    assert not out[rw.PORTAL_LEG_COLUMN].any() and len(anchors) == 0


def _multi_person_inputs():
    """Four persons whose stays remove [2, 1, 0, 3] legs; chain lengths are 5, 3, 4 and 5 trips."""
    removed = [2, 1, 0, 3]
    outbound = [1, 0, 1, 0]
    n_trips = [5, 3, 4, 5]
    rows = []
    for person, count in enumerate(n_trips, start=1):
        purposes = ["home"] + [f"p{person}_{i}" for i in range(1, count)] + ["home"]
        for index in range(count):
            rows.append((person, index, purposes[index], purposes[index + 1], "walk", 1000.0,
                         index * 3600.0, index * 3600.0 + 600.0))
    trips = rw.recompute_chain_columns(pd.DataFrame(rows, columns=[
        "person_id", "trip_index", "preceding_purpose", "following_purpose", "mode", "euclidean_distance",
        "departure_time", "arrival_time"]))
    stays = pd.DataFrame({"person_id": [1, 2, 3, 4], "outbound_trip_index": outbound,
                          "return_trip_index": [float(o + r + 1) for o, r in zip(outbound, removed)],
                          "n_removed_legs": removed})
    gate_rows = pd.DataFrame({"gate_id": [f"g{i}" for i in range(4)], "kind": ["road"] * 4,
                              "x": [40000.0] * 4, "y": [0.0] * 4})
    # Outbound legs depart at index * 3600 s and last 600 s; the arrival at the gate is any value in between.
    outbound_arrival = [(outbound_index * 3600.0) + 300.0 for outbound_index in outbound]
    times = pd.DataFrame({"t_reentry": [20 * 3600.0] * 4, "share_out": [0.5] * 4,
                          "inside_return_duration": [1800.0] * 4, "clamped": [False] * 4,
                          "share_capped": [False] * 4, "has_return": [True] * 4,
                          "outbound_arrival_time": outbound_arrival})
    modes = pd.DataFrame({"mode": ["car"] * 4, "outbound_mode": ["car"] * 4, "return_mode": ["car"] * 4,
                          "substituted_from": [None] * 4, "substitution_reason": [None] * 4,
                          "return_mode_differs": [False] * 4})
    return trips, stays, gate_rows, times, modes, np.zeros((4, 2))


def test_rewrite_offsets_with_different_numbers_of_removed_legs_per_person():
    trips, stays, gate_rows, times, modes, origin_xy = _multi_person_inputs()
    out, anchors = rw.rewrite_trips(trips, stays, gate_rows, times, modes, origin_xy, crs=CRS)
    kept = {person: group for person, group in out.groupby("person_id")}
    assert {person: len(group) for person, group in kept.items()} == {1: 3, 2: 2, 3: 4, 4: 2}
    for person, group in kept.items():
        assert group["trip_index"].tolist() == list(range(len(group)))
    assert kept[1]["following_purpose"].tolist() == ["p1_1", "outside", "home"]
    assert kept[1]["preceding_purpose"].tolist() == ["home", "p1_1", "outside"]
    assert kept[2]["following_purpose"].tolist() == ["outside", "home"]
    assert kept[3]["following_purpose"].tolist() == ["p3_1", "outside", "p3_3", "home"]
    assert kept[4]["following_purpose"].tolist() == ["outside", "home"]
    assert out[rw.PORTAL_LEG_COLUMN].groupby(out["person_id"]).sum().tolist() == [2, 2, 2, 2]
    assert anchors["person_id"].tolist() == [1, 2, 3, 4]
    assert anchors["activity_index"].tolist() == [2, 1, 2, 1]


def test_rewrite_is_independent_of_the_input_row_and_stay_order():
    trips, stays, gate_rows, times, modes, origin_xy = _multi_person_inputs()
    expected_out, expected_anchors = rw.rewrite_trips(trips, stays, gate_rows, times, modes, origin_xy, crs=CRS)
    order = [3, 1, 0, 2]
    shuffled_trips = trips.sample(frac=1.0, random_state=7)
    out, anchors = rw.rewrite_trips(
        shuffled_trips, stays.iloc[order].reset_index(drop=True), gate_rows.iloc[order].reset_index(drop=True),
        times.iloc[order].reset_index(drop=True), modes.iloc[order].reset_index(drop=True), origin_xy[order], crs=CRS)
    pd.testing.assert_frame_equal(out, expected_out)
    pd.testing.assert_frame_equal(
        pd.DataFrame(anchors.drop(columns="geometry")).sort_values("person_id").reset_index(drop=True),
        pd.DataFrame(expected_anchors.drop(columns="geometry")).sort_values("person_id").reset_index(drop=True))


def test_rewrite_rejects_a_trip_table_with_duplicate_person_and_trip_index():
    trips = _chain()
    with pytest.raises(ValueError, match="not unique"):
        rw.rewrite_trips(pd.concat([trips, trips.iloc[[1]]], ignore_index=True), *_inputs(trips), crs=CRS)


def test_rewrite_rejects_stay_legs_that_are_missing_from_the_trip_table():
    trips = _chain()
    stays, gate_rows, times, modes, origin_xy = _inputs(trips)
    stays["outbound_trip_index"] = [99]
    with pytest.raises(ValueError, match="not in the trip table"):
        rw.rewrite_trips(trips, stays, gate_rows, times, modes, origin_xy, crs=CRS)


def test_rewrite_rejects_per_stay_inputs_that_are_not_row_aligned():
    trips = _chain()
    stays, gate_rows, times, modes, origin_xy = _inputs(trips)
    with pytest.raises(ValueError, match="row-aligned"):
        rw.rewrite_trips(trips, stays, gate_rows, pd.concat([times, times], ignore_index=True), modes, origin_xy,
                         crs=CRS)


def test_rewrite_rejects_a_stay_whose_outbound_leg_is_removed_by_another_stay_of_the_person():
    trips = _chain()
    # stay A removes legs 1 and 2; stay B departs at leg 1, which does not survive.
    stays = pd.DataFrame({"person_id": [1, 1], "outbound_trip_index": [0, 1], "return_trip_index": [3.0, 3.0],
                          "n_removed_legs": [2, 1]})
    gate_rows = pd.DataFrame({"gate_id": ["g1", "g2"], "kind": ["road", "road"], "x": [40000.0] * 2, "y": [0.0] * 2})
    times = pd.DataFrame({"t_reentry": [17 * 3600.0] * 2, "share_out": [0.5] * 2, "inside_return_duration": [900.0] * 2,
                          "clamped": [False] * 2, "share_capped": [False] * 2, "has_return": [True] * 2,
                          "outbound_arrival_time": [9.5 * 3600.0] * 2})
    modes = pd.DataFrame({"mode": ["car"] * 2, "outbound_mode": ["car"] * 2, "return_mode": ["car"] * 2,
                          "substituted_from": [None] * 2, "substitution_reason": [None] * 2,
                          "return_mode_differs": [False] * 2})
    with pytest.raises(ValueError, match="person_id=1"):
        rw.rewrite_trips(trips, stays, gate_rows, times, modes, np.zeros((2, 2)), crs=CRS)
