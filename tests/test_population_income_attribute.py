"""Tests for Task A2 -- write the realistic continuous income to MATSim.

Two concerns are covered:

1. The MATSim person-attribute writer emits a numeric ``householdIncomeEur``
   attribute equal to the synthesised ``household_income_eur`` when the config
   flag ``write_income_eur`` is ON, and OMITS it (byte-identical legacy output)
   when the flag is OFF.
2. The cross-cordon in-commuter income is drawn from the origin-Kreis INKAR
   income level (so incomes vary across source Kreise instead of the legacy
   hardcoded constant), with a logged primary-vs-fallback rate when an origin
   Kreis is missing from the INKAR table (CLAUDE.md "Fallback transparency").
"""
from __future__ import annotations

import gzip
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.scenario.population as pop  # noqa: E402
import matsim.writers as writers  # noqa: E402
from braunschweig.synthesis.incommuters import draw_incommuter_income  # noqa: E402


def _write_one_person(person_row, write_income_eur):
    """Run the MATSim person-attribute writer on a single person row.

    Builds the PERSON_FIELDS-ordered tuple from a dict, gives the person one home
    activity (no trips/vehicles), and returns the produced XML text so the income
    attribute can be asserted on.
    """
    from shapely.geometry import Point

    defaults = {
        "person_id": 1, "household_income": "3000", "car_availability": "none",
        "bicycle_availability": "none", "census_household_id": 1,
        "census_person_id": 1, "household_id": 1, "has_license": True,
        "has_pt_subscription": False, "hts_id": 1, "hts_household_id": 1,
        "age": 40, "employed": "yes", "sex": "male", "high_income": False,
        "is_urban_resident": False, "pt_subscription_type": "never_pt",
        "household_income_eur": 4321.0,
    }
    defaults.update(person_row)
    person = tuple(defaults[field] for field in pop.PERSON_FIELDS)

    activity = tuple(
        {"person_id": 1, "start_time": float("nan"), "end_time": float("nan"),
         "purpose": "home", "geometry": Point(0.0, 0.0), "location_id": -1}[f]
        for f in pop.ACTIVITY_FIELDS)

    buffer = io.BytesIO()
    writer = writers.PopulationWriter(buffer)
    writer.start_population()
    pop.add_person(writer, person, [activity], [], [],
                   enable_urban_parking=False, write_income_eur=write_income_eur)
    writer.end_population()
    return buffer.getvalue().decode("utf-8")


def _write_person_with_car_trip(has_car_vehicle, remode, car_availability="all"):
    """Write a person with a home->work->home plan whose first leg mode is 'car',
    returning the XML. ``has_car_vehicle`` controls whether the person owns a "car"
    vehicle (the re-mode shim depends on vehicle ownership, not car_availability)."""
    from shapely.geometry import Point

    defaults = {
        "person_id": 1, "household_income": "3000", "car_availability": car_availability,
        "bicycle_availability": "none", "census_household_id": 1,
        "census_person_id": 1, "household_id": 1, "has_license": True,
        "has_pt_subscription": False, "hts_id": 1, "hts_household_id": 1,
        "age": 40, "employed": "yes", "sex": "male", "high_income": False,
        "is_urban_resident": False, "pt_subscription_type": "never_pt",
        "household_income_eur": 4321.0,
    }
    person = tuple(defaults[field] for field in pop.PERSON_FIELDS)

    def _activity(purpose):
        return tuple(
            {"person_id": 1, "start_time": float("nan"), "end_time": float("nan"),
             "purpose": purpose, "geometry": Point(0.0, 0.0), "location_id": -1}[f]
            for f in pop.ACTIVITY_FIELDS)

    trip = tuple({"person_id": 1, "mode": "car", "departure_time": 28800.0,
                  "travel_time": 600.0}[f] for f in pop.TRIP_FIELDS)

    def _vehicle(vehicle_id, mode):
        return tuple({"owner_id": 1, "vehicle_id": vehicle_id, "mode": mode}[f]
                     for f in pop.VEHICLE_FIELDS)
    vehicles = [_vehicle("1:car_passenger", "car_passenger")]
    if has_car_vehicle:
        vehicles.insert(0, _vehicle("1:car", "car"))

    buffer = io.BytesIO()
    writer = writers.PopulationWriter(buffer)
    writer.start_population()
    pop.add_person(writer, person, [_activity("home"), _activity("work")], [trip], vehicles,
                   enable_urban_parking=False, write_income_eur=False,
                   remode_carless_car_legs=remode)
    writer.end_population()
    return buffer.getvalue().decode("utf-8")


def test_car_leg_remoded_to_car_passenger_when_no_car_vehicle_and_flag_on():
    # No "car" vehicle (carless OR non-owner of a car-owning HH) -> re-moded.
    xml = _write_person_with_car_trip(has_car_vehicle=False, remode=True)
    assert 'mode="car_passenger"' in xml
    assert 'mode="car"' not in xml.replace('car_passenger', '')


def test_car_leg_kept_when_flag_off_byte_identical():
    xml = _write_person_with_car_trip(has_car_vehicle=False, remode=False)
    assert 'mode="car"' in xml.replace('car_passenger', 'X')


def test_car_owner_keeps_car_leg_with_flag_on():
    # A person who owns a "car" vehicle still drives, even with the flag on.
    xml = _write_person_with_car_trip(has_car_vehicle=True, remode=True)
    assert 'mode="car"' in xml.replace('car_passenger', 'X')


def test_household_income_eur_attribute_written_when_flag_on():
    xml = _write_one_person({"household_income_eur": 4321.0}, write_income_eur=True)
    assert 'name="householdIncomeEur"' in xml
    assert "java.lang.Double" in xml
    # numeric value equal to the input
    assert "4321" in xml


def test_household_income_eur_attribute_absent_when_flag_off():
    xml = _write_one_person({"household_income_eur": 4321.0}, write_income_eur=False)
    assert "householdIncomeEur" not in xml


def test_household_income_eur_attribute_off_is_legacy_byte_identical():
    # With the flag OFF the produced XML must be byte-identical to what the legacy
    # writer (no income attribute at all) produced -- the only difference allowed
    # is the absence of the new attribute line.
    xml_off = _write_one_person({}, write_income_eur=False)
    assert "householdIncomeEur" not in xml_off
    # the categorical placeholder string attribute is still present (unchanged)
    assert 'name="householdIncome"' in xml_off


# --- in-commuter INKAR income draw ------------------------------------------------

def _inkar_frame():
    """Two source Kreise with distinct INKAR scales + a regional mean of 1.0."""
    return pd.DataFrame({
        "ars5": ["03241", "03158"],
        "einkommen_eur": [2400.0, 1800.0],
        "scale": [1.20, 0.90],
    })


def test_incommuter_income_varies_by_origin_kreis():
    # Agents from two Kreise with different INKAR scales must NOT all get the same
    # income; the higher-scale Kreis must on average get a higher income.
    df_inkar = _inkar_frame()
    orig_ars = np.array(["03241"] * 50 + ["03158"] * 50)
    rng = np.random.default_rng(0)
    income, n_fallback = draw_incommuter_income(orig_ars, df_inkar, base_eur=3000.0,
                                                rng=rng)
    assert n_fallback == 0
    assert len(income) == len(orig_ars)
    assert len(np.unique(income)) > 1  # not all identical
    mean_high = income[:50].mean()
    mean_low = income[50:].mean()
    assert mean_high > mean_low  # higher INKAR scale -> higher income


def test_incommuter_income_falls_back_to_regional_mean_and_logs(capsys):
    # An origin Kreis absent from INKAR falls back to the regional mean (scale 1.0)
    # and the fallback count/rate is logged (CLAUDE.md no-silent-fallback).
    df_inkar = _inkar_frame()
    orig_ars = np.array(["03241", "09999", "09999"])  # 2 of 3 missing -> fallback
    rng = np.random.default_rng(0)
    income, n_fallback = draw_incommuter_income(orig_ars, df_inkar, base_eur=3000.0,
                                                rng=rng)
    assert n_fallback == 2
    assert (income > 0).all()
    out = capsys.readouterr().out
    assert "fallback" in out.lower()
    # high fallback rate (>5%) -> WARNING prefix
    assert "WARNING" in out


def test_incommuter_income_is_deterministic_given_rng():
    df_inkar = _inkar_frame()
    orig_ars = np.array(["03241", "03158", "03241"])
    a, _ = draw_incommuter_income(orig_ars, df_inkar, base_eur=3000.0,
                                  rng=np.random.default_rng(7))
    b, _ = draw_incommuter_income(orig_ars, df_inkar, base_eur=3000.0,
                                  rng=np.random.default_rng(7))
    np.testing.assert_array_equal(a, b)
