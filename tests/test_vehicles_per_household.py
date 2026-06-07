"""Tests for the per-household vehicle generation (Task F5 / spec A4).

``braunschweig.synthesis.vehicles.cars.household`` emits exactly the household's
``number_of_cars`` vehicles (MiD H7), assigns each to a licensed adult
(highest-need first), builds the car frame F4
(:func:`braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet`) needs --
``economic_status, kreis_ags5, gemeinde, raumtyp`` -- and attaches the full KBA
vehicle spec to each vehicle.

The scientific assertions:

  * vehicle count per household == ``number_of_cars`` (including 0 and the 3+
    tail);
  * every vehicle owner is a licensed adult (except the logged zero-licensed
    edge);
  * 0-car households produce no vehicle;
  * the produced car frame carries the F4-required columns and
    :func:`sample_fleet` runs on it;
  * ``vehicles_method:"default"`` reproduces the legacy one-car-per-person frame
    byte-identically (same ids and order).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.vehicles.cars import household as hh  # noqa: E402

DATA_PATH = str(DATA)


# --------------------------------------------------------------------------- #
# Synthetic enriched persons + home commune frame.
# --------------------------------------------------------------------------- #
def _make_persons() -> pd.DataFrame:
    """A small synthetic enriched-population frame with varied car counts.

    One row per person; ``number_of_cars`` is the household-level MiD H7 value
    broadcast to every member (the household method aggregates it back per
    household). The households cover the edge cases: 0 cars, 1 car with several
    licensed adults, more cars than licensed adults, the 3+ tail, and a
    household with cars but no licensed adult.
    """
    records = [
        # hh 1: 2 cars, 2 licensed adults + 1 child.
        {"household_id": 1, "person_id": 101, "age": 45, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 1, "person_id": 102, "age": 43, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 1, "person_id": 103, "age": 12, "has_license": False,
         "number_of_cars": 2, "economic_status": "high"},
        # hh 2: 0 cars, 1 licensed adult -> no vehicle.
        {"household_id": 2, "person_id": 201, "age": 30, "has_license": True,
         "number_of_cars": 0, "economic_status": "low"},
        # hh 3: 3 cars but only 1 licensed adult -> round-robin to the adult.
        {"household_id": 3, "person_id": 301, "age": 50, "has_license": True,
         "number_of_cars": 3, "economic_status": "very_high"},
        {"household_id": 3, "person_id": 302, "age": 48, "has_license": False,
         "number_of_cars": 3, "economic_status": "very_high"},
        # hh 4: 1 car, no licensed adult -> assigned to the oldest adult, logged.
        {"household_id": 4, "person_id": 401, "age": 70, "has_license": False,
         "number_of_cars": 1, "economic_status": "medium"},
        {"household_id": 4, "person_id": 402, "age": 65, "has_license": False,
         "number_of_cars": 1, "economic_status": "medium"},
        # hh 5: 1 car, 1 licensed adult.
        {"household_id": 5, "person_id": 501, "age": 38, "has_license": True,
         "number_of_cars": 1, "economic_status": "medium"},
    ]
    return pd.DataFrame.from_records(records)


def _make_homes() -> pd.DataFrame:
    """Home commune (12-digit ARS) per household, inside the ZGB scope."""
    return pd.DataFrame.from_records([
        {"household_id": 1, "commune_id": "031010000000"},  # Braunschweig
        {"household_id": 2, "commune_id": "031020000000"},  # Salzgitter
        {"household_id": 3, "commune_id": "031530000000"},  # Goslar Kreis
        {"household_id": 4, "commune_id": "031540000000"},  # Helmstedt Kreis
        {"household_id": 5, "commune_id": "031010000000"},  # Braunschweig
    ])


def _make_regiostar() -> pd.DataFrame:
    """Minimal RegioStaR reference (commune_id -> name + RS7) for the homes."""
    return pd.DataFrame.from_records([
        {"commune_id": "03101000", "ars5": "03101", "name": "Braunschweig, Stadt",
         "regiostar7": 72},
        {"commune_id": "03102000", "ars5": "03102", "name": "Salzgitter",
         "regiostar7": 73},
        {"commune_id": "03153000", "ars5": "03153", "name": "Goslar",
         "regiostar7": 76},
        {"commune_id": "03154000", "ars5": "03154", "name": "Helmstedt",
         "regiostar7": 76},
    ])


# --------------------------------------------------------------------------- #
# Car-frame construction (the input F4 / sample_fleet expects).
# --------------------------------------------------------------------------- #
def test_car_frame_has_f4_columns_and_counts():
    df_cars = hh.build_household_car_frame(
        _make_persons(), _make_homes(), _make_regiostar())
    required = {"economic_status", "kreis_ags5", "gemeinde", "raumtyp",
                "household_id", "owner_id"}
    assert required.issubset(df_cars.columns)
    # Total cars == sum of household number_of_cars (2 + 0 + 3 + 1 + 1 = 7).
    assert len(df_cars) == 7


def test_vehicle_count_per_household_matches_number_of_cars():
    df_cars = hh.build_household_car_frame(
        _make_persons(), _make_homes(), _make_regiostar())
    counts = df_cars.groupby("household_id").size().to_dict()
    assert counts.get(1) == 2
    assert 2 not in counts          # 0-car household produces no vehicle
    assert counts.get(3) == 3       # 3+ household
    assert counts.get(4) == 1
    assert counts.get(5) == 1


def test_zero_car_household_produces_no_vehicle():
    df_cars = hh.build_household_car_frame(
        _make_persons(), _make_homes(), _make_regiostar())
    assert (df_cars["household_id"] == 2).sum() == 0


def test_owners_are_licensed_adults_except_zero_licensed_edge():
    persons = _make_persons()
    df_cars = hh.build_household_car_frame(
        persons, _make_homes(), _make_regiostar())
    licensed_adults = set(
        persons.loc[persons["has_license"] & (persons["age"] >= hh.ADULT_AGE),
                    "person_id"])
    # hh 4 has cars but no licensed adult -> owner is the oldest adult (401).
    edge_owners = {401}
    for owner in df_cars["owner_id"]:
        assert owner in licensed_adults or owner in edge_owners, owner


def test_kreis_gemeinde_raumtyp_derivation():
    df_cars = hh.build_household_car_frame(
        _make_persons(), _make_homes(), _make_regiostar())
    hh1 = df_cars[df_cars["household_id"] == 1].iloc[0]
    assert hh1["kreis_ags5"] == "03101"
    assert hh1["gemeinde"] == "BRAUNSCHWEIG, STADT"  # upper-cased name
    assert int(hh1["raumtyp"]) == 72


def test_extra_cars_round_robin_to_licensed_adults():
    # hh 3: 3 cars, 1 licensed adult -> all 3 owned by that adult (round-robin).
    df_cars = hh.build_household_car_frame(
        _make_persons(), _make_homes(), _make_regiostar())
    hh3 = df_cars[df_cars["household_id"] == 3]
    assert len(hh3) == 3
    assert set(hh3["owner_id"]) == {301}


def test_sample_fleet_runs_on_built_frame():
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    df_cars = hh.build_household_car_frame(
        _make_persons(), _make_homes(), _make_regiostar())
    df_spec, df_types = fs.sample_fleet(df_cars, DATA_PATH, random_seed=1)
    assert len(df_spec) == len(df_cars)
    for col in ("segment", "powertrain", "euro_class", "type_id",
                "hbefa_cat", "hbefa_tech", "hbefa_size", "hbefa_emission"):
        assert col in df_spec.columns
    assert df_types["type_id"].is_unique


def test_vehicle_ids_are_unique():
    persons = _make_persons()
    df_vehicles = hh.assign_vehicle_ids(
        hh.build_household_car_frame(persons, _make_homes(), _make_regiostar()))
    assert df_vehicles["vehicle_id"].is_unique
    # owner with multiple cars gets distinct ids (hh 3 owner 301 has 3 cars).
    owner_301 = df_vehicles[df_vehicles["owner_id"] == 301]
    assert owner_301["vehicle_id"].is_unique
    assert len(owner_301) == 3


# --------------------------------------------------------------------------- #
# OFF equivalence: vehicles_method:"default" -> legacy one-car-per-person.
# --------------------------------------------------------------------------- #
def test_default_method_is_legacy_byte_identical():
    """The legacy default path is one car per person, id ``<person>:car``.

    The household module must NOT change that path; this reproduces the legacy
    frame construction (synthesis/vehicles/cars/default.py) and asserts the
    household module's helper for the OFF case yields the same ids/order.
    """
    persons = _make_persons()

    legacy = persons[["person_id"]].copy().rename(
        columns={"person_id": "owner_id"})
    legacy["mode"] = "car"
    legacy["vehicle_id"] = legacy["owner_id"].astype(str) + ":car"
    legacy["type_id"] = "default_car"

    produced = hh.legacy_one_car_per_person(persons)[
        ["owner_id", "mode", "vehicle_id", "type_id"]]
    pd.testing.assert_frame_equal(
        produced.reset_index(drop=True), legacy.reset_index(drop=True))
