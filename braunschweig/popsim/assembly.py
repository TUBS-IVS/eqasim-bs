"""Assemble the popsim_mid persons frame from the expanded donor population.

Composes the small building blocks (``expand`` + ``attributes``) into one persons
frame: each synthetic household is expanded to its MiD donor persons, demographics
and person attributes are mapped, the MiD donor household attributes are joined,
and car availability is derived per synthetic household from cars vs. adults.

This is the harmonisation core of popsim_mid; PT subscription, bicycle
availability and the activity chains (``braunschweig.popsim.trips``) + home
coordinates (``braunschweig.popsim.handoff``) are layered on top.
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim import attributes
from braunschweig.popsim import expand

# Age (completed years) from which a person counts as an adult for car availability.
ADULT_AGE = 18

_HOUSEHOLD_ATTRS = ["economic_status", "household_income_eur", "number_of_cars"]


def build_persons(
    merged_households: pd.DataFrame,
    mid_households: pd.DataFrame,
    mid_persons: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
) -> pd.DataFrame:
    """Build the synthetic persons frame with demographics + attributes.

    Parameters
    ----------
    merged_households:
        Merged PopulationSim output (one row per synthetic household, donor
        ``H_ID`` + cell).
    mid_households / mid_persons:
        The MiD donor household / person tables.

    Returns
    -------
    pandas.DataFrame
        One row per synthetic person, with ``household_id`` / ``person_id``, the
        cell, demographics (``age`` / ``sex``), person attributes (``employed`` /
        ``has_license``), the joined household attributes (``economic_status`` /
        ``household_income_eur`` / ``number_of_cars``) and the derived
        ``car_availability``.
    """
    households = expand.assign_synthetic_household_ids(
        merged_households, donor_col=donor_col
    )
    persons = expand.expand_to_persons(households, mid_persons, donor_col=donor_col)
    persons = expand.map_demographics(persons)
    persons = attributes.map_employed(persons)
    persons = attributes.map_has_license(persons)

    donor_hh = attributes.map_number_of_cars(
        attributes.map_household_income_eur(
            attributes.map_economic_status(mid_households)
        )
    )
    persons = persons.merge(
        donor_hh[[donor_col, *_HOUSEHOLD_ATTRS]],
        on=donor_col, how="left", suffixes=("", "_hh"),
    )
    persons["number_of_cars"] = persons["number_of_cars"].fillna(0).astype(int)

    persons["car_availability"] = _car_availability_per_household(persons)
    return persons


def _car_availability_per_household(persons: pd.DataFrame) -> pd.Series:
    """Derive car availability per synthetic household (cars vs. adult members)."""
    is_adult = persons["age"] >= ADULT_AGE
    n_adults = is_adult.groupby(persons["household_id"]).sum()
    n_cars = persons.groupby("household_id")["number_of_cars"].first()
    availability = {
        household_id: attributes.derive_car_availability(
            int(n_cars[household_id]), int(n_adults[household_id])
        )
        for household_id in n_cars.index
    }
    return persons["household_id"].map(availability)
