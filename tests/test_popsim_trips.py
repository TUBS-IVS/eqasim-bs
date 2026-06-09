"""Tests for mapping MiD Wege (trips) to eqasim activity chains (Phase 5g.4).

Codes grounded in the MiD 2023 codebook (Wege sheet): W_ZWECK (purpose), hvm
(main mode). Tiny synthetic data only.
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim import trips


def test_map_purpose_from_w_zweck():
    wege = pd.DataFrame({"W_ZWECK": [1, 2, 3, 4, 7, 8, 9, 5, 11, 12]})
    out = trips.map_purpose(wege)
    assert list(out["purpose"]) == [
        "work", "work", "education", "shop", "leisure",
        "home", "home", "other", "education", "education",
    ]


def test_map_mode_from_hvm():
    wege = pd.DataFrame({"hvm": [1, 2, 3, 4, 5, 9]})
    out = trips.map_mode(wege)
    assert list(out["mode"]) == ["walk", "bike", "car_passenger", "car", "pt", "walk"]


def test_expand_persons_to_trips_joins_donor_wege():
    # synthetic persons referencing donor (H_ID, P_ID)
    persons = pd.DataFrame(
        {
            "household_id": ["A_1_0", "A_1_0", "B_2_0"],
            "person_id": ["A_1_0_1", "A_1_0_2", "B_2_0_1"],
            "H_ID": [1, 1, 2],
            "P_ID": [1, 2, 1],
        }
    )
    wege = pd.DataFrame(
        {
            "H_ID": [1, 1, 1, 2],
            "P_ID": [1, 1, 2, 1],
            "W_ID": [1, 2, 1, 1],
            "W_ZWECK": [1, 8, 7, 4],
            "hvm": [4, 4, 1, 5],
        }
    )
    out = trips.expand_persons_to_trips(persons, wege)
    # person A_1_0_1 (donor 1,1) has 2 trips; A_1_0_2 (donor 1,2) has 1; B_2_0_1 has 1.
    counts = out.groupby("person_id").size().to_dict()
    assert counts == {"A_1_0_1": 2, "A_1_0_2": 1, "B_2_0_1": 1}
    assert "purpose" in out.columns and "mode" in out.columns
    # trip_id is unique per synthetic trip.
    assert out["trip_id"].is_unique


def test_expand_persons_to_trips_person_without_wege_is_dropped():
    persons = pd.DataFrame(
        {"household_id": ["A_1_0"], "person_id": ["A_1_0_9"], "H_ID": [1], "P_ID": [9]}
    )
    wege = pd.DataFrame({"H_ID": [1], "P_ID": [1], "W_ID": [1], "W_ZWECK": [1], "hvm": [4]})
    out = trips.expand_persons_to_trips(persons, wege)
    assert len(out) == 0


def test_mid_time_seconds_from_hours_minutes():
    wege = pd.DataFrame({"W_SZS": [8, 17], "W_SZM": [30, 5]})
    out = trips.mid_time_seconds(wege, "W_SZS", "W_SZM")
    assert list(out) == [8 * 3600 + 30 * 60, 17 * 3600 + 5 * 60]


def test_build_trip_table_eqasim_schema_plus_extras():
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1", "A_1_0_1"],
        "H_ID": [1, 1], "P_ID": [1, 1],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0],
        "W_AZS": [8, 17], "W_AZM": [30, 20],
        "wegkm": [12.0, 12.0],
        # extras (carried through):
        "W_ZWDF": [None, None], "W_ANZBEGL": [0, 1], "W_BEGL_HH": [2, 1],
    })
    out = trips.build_trip_table(persons, wege)
    # eqasim trip-schema columns present:
    for col in ["person_id", "trip_id", "departure_time", "arrival_time",
                "trip_duration", "activity_duration", "preceding_purpose",
                "following_purpose", "is_first_trip", "is_last_trip", "mode"]:
        assert col in out.columns
    first = out.iloc[0]
    assert first["departure_time"] == 8 * 3600
    assert first["arrival_time"] == 8 * 3600 + 30 * 60
    assert first["trip_duration"] == 30 * 60
    assert first["mode"] == "car"
    # preceding_purpose of trip 1 (W_ZWECK None at day start) -> home; following = work.
    assert first["following_purpose"] == "work"
    assert first["preceding_purpose"] == "home"
    assert bool(first["is_first_trip"]) is True
    assert bool(out.iloc[-1]["is_last_trip"]) is True
    # extra MiD info carried:
    assert "wegkm" in out.columns and "W_ANZBEGL" in out.columns
    # trip_id unique per synthetic trip:
    assert out["trip_id"].is_unique
