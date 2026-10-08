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
    times = pd.DataFrame({"t_reentry": [17 * 3600.0 + 1800.0], "share_out": [0.5], "inside_return_duration": [2700.0],
                          "clamped": [False], "share_capped": [False], "has_return": [True]})
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
                          "clamped": [False], "share_capped": [False], "has_return": [False]})
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
    times = pd.DataFrame({"t_reentry": [12.5 * 3600.0, 20.5 * 3600.0], "share_out": [0.5, 0.5],
                          "inside_return_duration": [1800.0, 1800.0], "clamped": [False, False],
                          "share_capped": [False, False], "has_return": [True, True]})
    modes = pd.DataFrame({"mode": ["car", "car"], "outbound_mode": ["car", "car"], "return_mode": ["car", "car"],
                          "substituted_from": [None, None], "substitution_reason": [None, None],
                          "return_mode_differs": [False, False]})
    out, anchors = rw.rewrite_trips(trips, stays, gate_rows, times, modes, np.zeros((2, 2)), crs=CRS)
    assert out["following_purpose"].tolist() == ["outside", "home", "outside", "home"]
    assert anchors["activity_index"].tolist() == [1, 3]
    assert out[rw.PORTAL_LEG_COLUMN].all()


def test_rewrite_without_stays_returns_an_equal_frame_with_the_flag_column():
    trips = _chain()
    out, anchors = rw.rewrite_trips(trips, pd.DataFrame(columns=["person_id", "outbound_trip_index", "return_trip_index", "n_removed_legs"]),
                                    pd.DataFrame(columns=["gate_id", "kind", "x", "y"]),
                                    pd.DataFrame(columns=["t_reentry", "share_out", "inside_return_duration", "clamped", "share_capped", "has_return"]),
                                    pd.DataFrame(columns=["mode", "outbound_mode", "return_mode", "substituted_from", "substitution_reason", "return_mode_differs"]),
                                    np.zeros((0, 2)), crs=CRS)
    pd.testing.assert_frame_equal(out.drop(columns=[rw.PORTAL_LEG_COLUMN]), trips)
    assert not out[rw.PORTAL_LEG_COLUMN].any() and len(anchors) == 0
