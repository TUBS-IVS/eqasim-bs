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


# --------------------------------------------------------------------------- #
# Task 4 — fleet_age_income_coupling config wiring.
# --------------------------------------------------------------------------- #
class _StubContext:
    """Minimal synpp configure-phase context stub.

    Records ``context.config(key, default)`` calls so tests can assert that
    ``fleet_age_income_coupling`` is registered with the correct default without
    actually importing synpp or running a pipeline.
    """

    def __init__(self):
        self._registered: dict[str, object] = {}

    def stage(self, *_args, **_kwargs):
        pass

    def config(self, key: str, default=None):
        self._registered[key] = default

    def registered(self) -> dict:
        return dict(self._registered)


def test_fleet_age_income_coupling_registered_with_default_true():
    """configure() must register fleet_age_income_coupling with default True.

    Mirrors how fleet_consistency_v2 is wired at the same site in configure().
    """
    ctx = _StubContext()
    hh.configure(ctx)
    registered = ctx.registered()
    assert "fleet_age_income_coupling" in registered, (
        "fleet_age_income_coupling must be declared in configure()"
    )
    assert registered["fleet_age_income_coupling"] is True, (
        "fleet_age_income_coupling default must be True (coupling-ON by default)"
    )


def test_fleet_age_income_coupling_off_produces_flat_age():
    """With age_income_coupling=False, car age must NOT be status-stratified.

    When the flag is False the tilt block is skipped; the age distribution
    is flat across economic_status groups (no gradient). This is the small
    faithful reproduction: it calls sample_fleet directly with the flag off
    and asserts that the age column shows no systematic difference between
    the lowest and highest status groups.
    """
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    rng = np.random.default_rng(7)
    n = 800
    statuses = ["very_low"] * n + ["very_high"] * n
    rng.shuffle(statuses)
    rows = []
    for i, status in enumerate(statuses):
        rows.append({
            "household_id": i,
            "owner_id": i,
            "economic_status": status,
            "kreis_ags5": "03101",
            "gemeinde": "BRAUNSCHWEIG, STADT",
            "raumtyp": 72,
        })
    df_cars = pd.DataFrame(rows)

    df_spec, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42,
        consistency_v2=True, age_income_coupling=False)

    mean_age = df_spec.groupby("economic_status")["age"].mean()
    age_very_low = mean_age["very_low"]
    age_very_high = mean_age["very_high"]
    # With coupling=False the gradient must be negligible (< 1 year gap).
    gap = abs(age_very_low - age_very_high)
    assert gap < 1.0, (
        f"coupling=False should produce a flat age distribution across status "
        f"groups, but very_low mean={age_very_low:.2f} vs "
        f"very_high mean={age_very_high:.2f} (gap={gap:.2f} >= 1.0)"
    )


def test_sample_fleet_receives_age_income_coupling_kwarg(monkeypatch):
    """execute() must thread age_income_coupling into sample_fleet.

    Patches fleet.sample_fleet to capture keyword arguments, then verifies
    that when fleet_age_income_coupling=False in the config the call site
    passes age_income_coupling=False (and when True -> True).
    Mirrors the consistency_v2 threading pattern.
    """
    from braunschweig.synthesis.vehicles.cars import household as hh_mod
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

    captured: list[dict] = []

    def _fake_sample_fleet(df_cars, data_path, **kwargs):
        captured.append(dict(kwargs))
        # Return a minimal valid tuple so execute() can continue past sample_fleet.
        df_spec = df_cars.copy()
        for col in ("segment", "powertrain", "euro_class", "type_id",
                    "hbefa_cat", "hbefa_tech", "hbefa_size", "hbefa_emission",
                    "age", "age_band", "brand", "model"):
            if col not in df_spec.columns:
                df_spec[col] = "x" if df_spec.empty else (
                    0 if col in ("euro_class", "age") else "x")
        df_types = pd.DataFrame([{
            "type_id": "x", "nb_seats": 4, "length": 5.0, "width": 1.0,
            "pce": 1.0, "mode": "car", "hbefa_cat": "PASSENGER_CAR",
            "hbefa_tech": "average", "hbefa_size": "average",
            "hbefa_emission": "average",
        }])
        return df_spec, df_types

    monkeypatch.setattr(hh_mod.fleet, "sample_fleet", _fake_sample_fleet)

    class _Ctx:
        """Minimal execute-phase context that serves a canned config and real stages."""
        def __init__(self, age_income_coupling: bool):
            self._coupling = age_income_coupling

        def config(self, key: str):
            defaults = {
                "data_path": DATA_PATH,
                "random_seed": 42,
                "hbefa_segment_size_map": None,
                "fleet_model_enabled": True,
                "fleet_model_brands": False,
                "fleet_hsn_tsn_attributes": False,
                "fleet_consistency_v2": True,
                "fleet_age_income_coupling": self._coupling,
                "fleet_electric_calibration": "kreis_mix_gemeinde_bev_tilt",
                "kba_fleet_paths": None,
            }
            return defaults[key]

        def stage(self, name: str):
            if name == "synthesis.population.enriched":
                return _make_persons()
            if name == "synthesis.population.spatial.home.zones":
                return _make_homes()
            if name == "braunschweig.data.bbsr.regiostar":
                return _make_regiostar()
            raise KeyError(name)

    for flag in (False, True):
        captured.clear()
        hh_mod.execute(_Ctx(age_income_coupling=flag))
        assert captured, "sample_fleet was not called"
        assert "age_income_coupling" in captured[0], (
            "execute() must pass age_income_coupling= to sample_fleet"
        )
        assert captured[0]["age_income_coupling"] == flag, (
            f"expected age_income_coupling={flag}, got {captured[0]['age_income_coupling']}"
        )
