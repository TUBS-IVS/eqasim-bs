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
from braunschweig.population import schema

# Age (completed years) from which a person counts as an adult for car availability.
ADULT_AGE = 18

_HOUSEHOLD_ATTRS = [
    "economic_status", "household_income", "household_income_eur",
    "number_of_cars", "number_of_bicycles",
]


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
    persons = attributes.map_has_pt_subscription(persons)

    donor_hh = attributes.map_number_of_bicycles(
        attributes.map_number_of_cars(
            attributes.map_household_income(
                attributes.map_household_income_eur(
                    attributes.map_economic_status(mid_households)
                )
            )
        )
    )
    persons = persons.merge(
        donor_hh[[donor_col, *_HOUSEHOLD_ATTRS]],
        on=donor_col, how="left", suffixes=("", "_hh"),
    )
    persons["number_of_cars"] = persons["number_of_cars"].fillna(0).astype(int)
    persons["number_of_bicycles"] = persons["number_of_bicycles"].fillna(0).astype(int)

    persons["car_availability"] = _household_availability(
        persons, count_col="number_of_cars", adults_only=True,
        derive=attributes.derive_car_availability,
    )
    persons["bicycle_availability"] = _household_availability(
        persons, count_col="number_of_bicycles", adults_only=False,
        derive=attributes.derive_bicycle_availability,
    )

    schema.validate_person_columns(persons.columns)
    return persons


def _household_availability(
    persons: pd.DataFrame,
    *,
    count_col: str,
    adults_only: bool,
    derive,
) -> pd.Series:
    """Derive a per-household availability {none, some, all} and broadcast to persons.

    ``count_col`` is the per-household vehicle count (cars / bicycles); the demand
    side is the adult members (cars) or all members (bicycles).
    """
    if adults_only:
        members = (persons["age"] >= ADULT_AGE).groupby(persons["household_id"]).sum()
    else:
        members = persons.groupby("household_id").size()
    counts = persons.groupby("household_id")[count_col].first()
    availability = {
        household_id: derive(int(counts[household_id]), int(members[household_id]))
        for household_id in counts.index
    }
    return persons["household_id"].map(availability)
