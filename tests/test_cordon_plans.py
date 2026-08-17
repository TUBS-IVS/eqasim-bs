"""Tests for the cross-cordon plan-frame builders (ported, gate-anchored caller)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.plans import (  # noqa: E402
    assign_fixed_mode,
    build_incommuter_activities,
    build_incommuter_locations,
    build_incommuter_trips,
    extract_commute_times,
    sample_donors,
    select_commuter_donors,
    straight_line_distance_km,
)


def test_build_trips_two_legs_with_durations():
    t = build_incommuter_trips([7], depart_home_s=[28000], arrive_work_s=[30000],
                               depart_work_s=[60000], arrive_home_s=[62000])
    assert len(t) == 2
    out = t[t["trip_index"] == 0].iloc[0]
    assert out["preceding_purpose"] == "home" and out["following_purpose"] == "work"
    assert out["trip_duration"] == 2000           # 30000-28000
    assert out["activity_duration"] == 30000      # depart_work - arrive_work
    inb = t[t["trip_index"] == 1].iloc[0]
    assert inb["is_last_trip"] and pd.isna(inb["activity_duration"])


def test_build_activities_three_with_nan_boundaries():
    a = build_incommuter_activities([7], [28000], [30000], [60000], [62000])
    assert list(a["purpose"]) == ["home", "work", "home"]
    assert pd.isna(a.iloc[0]["start_time"]) and pd.isna(a.iloc[-1]["end_time"])
    assert a.iloc[1]["duration"] == 30000


def test_build_locations_home_placeholder_and_geometry():
    loc = build_incommuter_locations([7], home_x=[100.0], home_y=[200.0],
                                     work_x=[300.0], work_y=[400.0],
                                     work_location_id=["work_5"], crs="EPSG:25832")
    assert len(loc) == 3
    home = loc[loc["activity_index"] == 0].iloc[0]
    assert home["location_id"] == -1
    assert abs(home.geometry.x - 100.0) < 1e-6
    work = loc[loc["activity_index"] == 1].iloc[0]
    assert work["location_id"] == "work_5"


def test_straight_line_distance_km():
    assert abs(straight_line_distance_km(0, 0, 3000, 4000) - 5.0) < 1e-9


def test_assign_fixed_mode_deterministic():
    ref = {"b": {"car": 1.0, "pt": 0.0}}
    modes = assign_fixed_mode([12.0, 3.0], ref, band_of=lambda d: "b",
                              rng=np.random.default_rng(0))
    assert modes == ["car", "car"]


def test_extract_commute_times_picks_home_work_home():
    trips = pd.DataFrame({
        "departure_time": [28000, 60000],
        "arrival_time": [30000, 62000],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"],
    })
    assert extract_commute_times(trips) == (28000.0, 30000.0, 60000.0, 62000.0)


def test_select_and_sample_donors():
    persons = pd.DataFrame({"hts_id": [1, 2, 3], "employed": [True, True, False]})
    trips = pd.DataFrame({"hts_id": [1, 3], "following_purpose": ["work", "work"]})
    donors = select_commuter_donors(persons, trips, "hts_id")
    assert list(donors["hts_id"]) == [1]            # 2 has no work trip, 3 not employed
    sampled = sample_donors(donors, 5, np.random.default_rng(0))
    assert len(sampled) == 5 and set(sampled["hts_id"]) == {1}


def test_select_donors_raises_when_none():
    persons = pd.DataFrame({"hts_id": [1], "employed": [False]})
    trips = pd.DataFrame({"hts_id": [1], "following_purpose": ["home"]})
    with pytest.raises(ValueError):
        select_commuter_donors(persons, trips, "hts_id")


def test_assign_fixed_mode_per_agent_matches_homogeneous_single_reference():
    # A homogeneous per-agent reference list must reproduce assign_fixed_mode exactly
    # (same seed, agent-sequential rng): this is the OFF-equivalence guarantee.
    from braunschweig.data.cordon.plans import (
        assign_fixed_mode, assign_fixed_mode_per_agent)
    ref = {"<10": {"car": 0.8, "pt": 0.2}, ">=10": {"car": 0.6, "pt": 0.4}}
    band_of = lambda d: "<10" if d < 10 else ">=10"
    dist = [3.0, 12.0, 7.0, 40.0, 1.0, 25.0]

    single = assign_fixed_mode(dist, ref, band_of, np.random.default_rng(123))
    per_agent = assign_fixed_mode_per_agent(
        dist, [ref] * len(dist), band_of, np.random.default_rng(123))
    assert per_agent == single


def test_assign_fixed_mode_per_agent_uses_each_agents_own_reference():
    # Agent 0 has a car-only reference, agent 1 a pt-only reference: the draw is
    # deterministic regardless of seed because each band has a single mode.
    from braunschweig.data.cordon.plans import assign_fixed_mode_per_agent
    car_ref = {"any": {"car": 1.0}}
    pt_ref = {"any": {"pt": 1.0}}
    band_of = lambda d: "any"
    out = assign_fixed_mode_per_agent(
        [5.0, 5.0], [car_ref, pt_ref], band_of, np.random.default_rng(0))
    assert out == ["car", "pt"]


def test_assign_fixed_mode_per_agent_length_mismatch_raises():
    from braunschweig.data.cordon.plans import assign_fixed_mode_per_agent
    ref = {"any": {"car": 1.0}}
    band_of = lambda d: "any"
    with pytest.raises(ValueError):
        assign_fixed_mode_per_agent([1.0, 2.0], [ref], band_of, np.random.default_rng(0))
