"""Tests for braunschweig.analysis.fleet_filter.fleet_vehicles.

Pins the single source of truth that isolates the household FLEET vehicles from
the eqasim per-person ROUTING vehicles that coexist (both mode=='car') in
vehicles.csv -- the mix that produced ~49% misleading nan in fleet_evaluation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis import fleet_filter as FF  # noqa: E402


def _sample():
    # 3 fleet cars (household_id set, brand present) + 2 routing cars
    # (household_id null, brand nan) + 1 car_passenger.
    return pd.DataFrame({
        "mode":         ["car", "car", "car", "car", "car", "car_passenger"],
        "household_id": [10, 11, 12, None, None, 13],
        "owner_id":     [1, 2, 3, 40, 41, 5],
        "brand":        ["VW", "BMW", "OPEL", None, None, None],
        "powertrain":   ["petrol", "diesel", "bev", None, None, None],
    })


def test_keeps_only_household_fleet_cars():
    out = FF.fleet_vehicles(_sample(), context="t")
    assert len(out) == 3
    assert set(out["household_id"]) == {10, 11, 12}
    assert out["brand"].notna().all()          # no nan brand survives
    assert out["powertrain"].notna().all()


def test_idempotent():
    once = FF.fleet_vehicles(_sample())
    twice = FF.fleet_vehicles(once)
    assert len(once) == len(twice) == 3


def test_none_and_empty_pass_through():
    assert FF.fleet_vehicles(None) is None
    empty = pd.DataFrame({"mode": [], "household_id": []})
    assert len(FF.fleet_vehicles(empty)) == 0


def test_missing_household_id_returns_all_cars():
    # No household_id column -> cannot exclude routing; returns all car rows.
    df = pd.DataFrame({"mode": ["car", "car", "car_passenger"], "brand": ["VW", None, None]})
    out = FF.fleet_vehicles(df)
    assert len(out) == 2  # both car rows kept (car_passenger dropped by mode filter)


def test_missing_mode_column_skips_mode_filter():
    df = pd.DataFrame({"household_id": [1, None], "brand": ["VW", None]})
    out = FF.fleet_vehicles(df)
    assert len(out) == 1  # household_id filter still applied
    assert out["household_id"].iloc[0] == 1
