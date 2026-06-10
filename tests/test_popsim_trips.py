"""Tests for mapping MiD Wege (trips) to eqasim activity chains (Phase 5g.4).

Codes grounded in the MiD 2023 codebook (Wege sheet): W_ZWECK (purpose), hvm_imp
(imputed main mode; handbook Kap. 4.2). Tiny synthetic data only.
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


def test_map_mode_uses_hvm_imp_and_rejects_unknown():
    import pandas as pd, pytest
    from braunschweig.popsim import trips
    out = trips.map_mode(pd.DataFrame({"hvm_imp": [1, 2, 3, 4, 5]}))
    assert out["mode"].tolist() == ["walk", "bicycle", "car_passenger", "car", "pt"]
    with pytest.raises(ValueError, match="unmapped"):
        trips.map_mode(pd.DataFrame({"hvm_imp": [99]}))


def test_mode_bicycle_for_hvm_imp_2():
    """MiD hvm_imp=2 (Fahrrad) must map to canonical eqasim mode 'bicycle'."""
    wege = pd.DataFrame({"hvm_imp": [2]})
    out = trips.map_mode(wege)
    assert out["mode"].iloc[0] == "bicycle"


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
            "hvm_imp": [4, 4, 1, 5],
        }
    )
    out = trips.expand_persons_to_trips(persons, wege)
    # person A_1_0_1 (donor 1,1) has 2 trips; A_1_0_2 (donor 1,2) has 1; B_2_0_1 has 1.
    counts = out.groupby("person_id").size().to_dict()
    assert counts == {"A_1_0_1": 2, "A_1_0_2": 1, "B_2_0_1": 1}
    assert "purpose" in out.columns and "mode" in out.columns
    # trip_id from expand_persons_to_trips is the string traceability key <person_id>_<W_ID>.
    assert out["trip_id"].is_unique


def test_expand_persons_to_trips_person_without_wege_is_dropped():
    persons = pd.DataFrame(
        {"household_id": ["A_1_0"], "person_id": ["A_1_0_9"], "H_ID": [1], "P_ID": [9]}
    )
    wege = pd.DataFrame({"H_ID": [1], "P_ID": [1], "W_ID": [1], "W_ZWECK": [1], "hvm_imp": [4]})
    out = trips.expand_persons_to_trips(persons, wege)
    assert len(out) == 0


def test_mid_time_seconds_from_hours_minutes():
    wege = pd.DataFrame({"W_SZS": [8, 17], "W_SZM": [30, 5]})
    out = trips.mid_time_seconds(wege, "W_SZS", "W_SZM")
    assert list(out) == [8 * 3600 + 30 * 60, 17 * 3600 + 5 * 60]


def _make_two_trip_persons_and_wege():
    """Helper: one synthetic person with two sequential trips (work then home)."""
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1", "A_1_0_1"],
        "H_ID": [1, 1], "P_ID": [1, 1],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0],
        "W_AZS": [8, 17], "W_AZM": [30, 20],
        "wegkm": [12.0, 12.0],
        # extras (carried through):
        "W_ZWDF": [None, None], "W_ANZBEGL": [0, 1], "W_BEGL_HH": [2, 1],
    })
    return persons, wege


def test_build_trip_table_eqasim_schema_plus_extras():
    persons, wege = _make_two_trip_persons_and_wege()
    out = trips.build_trip_table(persons, wege)

    # eqasim trip-schema columns present (including trip_index added by rework):
    for col in ["person_id", "trip_id", "departure_time", "arrival_time",
                "trip_duration", "activity_duration", "preceding_purpose",
                "following_purpose", "is_first_trip", "is_last_trip", "mode",
                "trip_index"]:
        assert col in out.columns, f"column '{col}' missing from build_trip_table output"

    # trip_key holds the old string traceability ID (e.g. "A_1_0_1_1").
    assert "trip_key" in out.columns
    assert out["trip_key"].iloc[0] == "A_1_0_1_1"

    # trip_id is now an integer (global 0..n-1), still unique.
    assert pd.api.types.is_integer_dtype(out["trip_id"]), "trip_id must be integer after rework"
    assert out["trip_id"].is_unique

    first = out.iloc[0]
    assert first["departure_time"] == 8 * 3600
    assert first["arrival_time"] == 8 * 3600 + 30 * 60
    assert first["trip_duration"] == 30 * 60
    assert first["mode"] == "car"
    # preceding_purpose of trip 1 (diary starts at home) -> home; following = work.
    assert first["following_purpose"] == "work"
    assert first["preceding_purpose"] == "home"
    assert bool(first["is_first_trip"]) is True
    assert bool(out.iloc[-1]["is_last_trip"]) is True
    # extra MiD info carried:
    assert "wegkm" in out.columns and "W_ANZBEGL" in out.columns


def test_build_trip_table_has_integer_trip_index():
    """trip_index must be a per-person 0-based sequential integer."""
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1", "A_1_0_1", "B_2_0_1"],
        "H_ID": [1, 1, 2], "P_ID": [1, 1, 2],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 2],
        "P_ID": [1, 1, 2, 2, 2],
        "W_ID": [1, 2, 1, 2, 3],
        "W_ZWECK": [1, 8, 4, 7, 8], "hvm_imp": [4, 4, 5, 1, 4],
        "W_SZS": [8, 17, 9, 12, 18], "W_SZM": [0, 0, 0, 30, 0],
        "W_AZS": [8, 17, 9, 12, 18], "W_AZM": [30, 20, 45, 50, 30],
        "wegkm": [12.0, 12.0, 5.0, 3.0, 8.0],
    })
    out = trips.build_trip_table(persons, wege)
    for pid, grp in out.groupby("person_id"):
        indices = sorted(grp["trip_index"].tolist())
        expected = list(range(len(grp)))
        assert indices == expected, (
            f"person {pid}: trip_index {indices} != expected 0..{len(grp)-1}"
        )


def test_build_trip_table_midnight_repair():
    """A trip departing near midnight and arriving after midnight must be repaired.

    We encode: departure at 23:30, arrival at 00:30 (next day), which appears
    in MiD as W_AZS=0, W_AZM=30 (raw seconds = 1800) while departure is
    23*3600+30*60 = 84600.  This yields arrival_time < departure_time before
    fix_trip_times; hts.fix_trip_times must shift arrival by +24h so that
    arrival_time >= departure_time.
    """
    persons = pd.DataFrame({
        "person_id": ["X_1_0_1"],
        "H_ID": [10], "P_ID": [5],
    })
    # Two-trip chain: first trip (work commute 08:00–08:30), second trip crosses midnight.
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [5, 5], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        # trip 1: 08:00 -> 08:30
        # trip 2: 23:30 -> 00:30 (midnight crossing; arrival coded as 00:30)
        "W_SZS": [8, 23], "W_SZM": [0, 30],
        "W_AZS": [8, 0], "W_AZM": [30, 30],
        "wegkm": [12.0, 12.0],
    })
    out = trips.build_trip_table(persons, wege)
    trip2 = out[out["trip_index"] == 1].iloc[0]
    # After fix_trip_times, arrival_time must be >= departure_time.
    assert trip2["arrival_time"] >= trip2["departure_time"], (
        f"Midnight crossing not repaired: departure={trip2['departure_time']}, "
        f"arrival={trip2['arrival_time']}"
    )
    # The repair must have pushed arrival past midnight (>= 24h mark).
    assert trip2["arrival_time"] >= 24 * 3600, (
        f"Arrival should be pushed past midnight (>=86400s), got {trip2['arrival_time']}"
    )


def test_build_validated_trip_table_returns_report():
    persons = pd.DataFrame({"person_id": ["A_1_0_1", "A_1_0_1"], "H_ID": [1, 1], "P_ID": [1, 1]})
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0], "W_AZS": [8, 17], "W_AZM": [30, 20],
    })
    table, report = trips.build_validated_trip_table(persons, wege, require_home_closure=True)
    assert "departure_time" in table.columns
    assert hasattr(report, "is_valid")
