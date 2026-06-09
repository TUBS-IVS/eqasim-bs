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

import numpy as np
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.popsim import attributes
from braunschweig.popsim import expand
from braunschweig.population import schema

# Column name of the 12-digit ARS key that the cells parquet carries and that
# stage.py joins onto the merged PopulationSim output before calling build_persons.
# The name is spelled with one 's' ("Schlussel") to match the parquet source column.
ARS_COLUMN = "RegionalSchlussel_ARS"


def derive_zone_ids(df: pd.DataFrame, *, ars_col: str = ARS_COLUMN) -> pd.DataFrame:
    """Derive the three spatial zone IDs from the 12-digit ARS column.

    Replicates the format used by the default IPF producer (braunschweig.ipf.attributed
    lines 814-816 and braunschweig.ipf.prepare line 126):

    - ``commune_id``    = 8-digit AGS string (ARS[0:5] + ARS[9:12]), e.g. "03101000".
                          Source: braunschweig.data.bbsr.regiostar.ars_to_ags8.
    - ``departement_id``= first 5 chars of commune_id = 5-digit Kreis string, e.g. "03101".
                          Source: ipf/prepare.py line 126 (commune_id[:5]).
    - ``iris_id``       = commune_id + "0000" stored as category, e.g. "031010000000".
                          Source: ipf/attributed.py lines 815-816. For Germany there are
                          no sub-commune IRIS zones; the "0000" suffix is the eqasim
                          placeholder that the spatial pipeline propagates downstream.

    Parameters
    ----------
    df:
        Frame that carries ``ars_col`` (the 12-digit ARS from the Zensus cell parquet).
    ars_col:
        Name of the ARS column in ``df``.

    Returns
    -------
    pandas.DataFrame
        A copy of ``df`` with three new columns: ``commune_id``, ``departement_id``,
        ``iris_id``.

    Raises
    ------
    KeyError
        If ``ars_col`` is not present in ``df`` (fail-fast: a missing ARS column
        means stage.py did not join it; the spatial home.zones stage would crash
        with a less informative KeyError otherwise).
    """
    if ars_col not in df.columns:
        raise KeyError(
            f"[popsim.assembly] ARS column {ars_col!r} not found in persons frame. "
            "stage.py must join the cells ARS onto merge_report.combined before "
            "calling build_persons (fix for spatial home.zones KeyError D1)."
        )
    out = df.copy()
    # commune_id: 8-digit AGS derived from the 12-digit ARS by dropping the
    # Verbandsgemeinde block (bytes 5-8). An 8-digit input is returned unchanged by
    # ars_to_ags8, so the derivation is idempotent if the ARS column is already AGS.
    out["commune_id"] = out[ars_col].astype(str).map(ars_to_ags8)
    # departement_id: 5-digit Kreis prefix of the AGS.  Matches ipf/prepare.py:126
    # (df_population["commune_id"].str[:5]).
    out["departement_id"] = out["commune_id"].str[:5]
    # iris_id: commune_id + "0000".  Matches ipf/attributed.py lines 815-816.
    # Germany has no sub-commune IRIS zones; "0000" is the eqasim placeholder.
    out["iris_id"] = (out["commune_id"] + "0000").astype("category")
    return out

# age_range bins and labels — MUST match synthesis/population/enriched.py lines
# 110-114 exactly so both population workflows produce the same categorical values
# consumed by the spatial (education / gravity) stages. The bins correspond to:
#   (-1, 10] = primary_school (age <= 10)
#   (10, 14] = middle_school  (age 11-14)
#   (14, 17] = high_school    (age 15-17)
#   (17, inf) = higher_education (age >= 18, the default in enriched.py)
_AGE_RANGE_BINS: list = [-1, 10, 14, 17, np.inf]
_AGE_RANGE_LABELS: list[str] = [
    "primary_school", "middle_school", "high_school", "higher_education"
]

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
    rng=None,
) -> pd.DataFrame:
    """Build the synthetic persons frame with demographics + attributes.

    Parameters
    ----------
    merged_households:
        Merged PopulationSim output (one row per synthetic household, donor
        ``H_ID`` + cell).
    mid_households / mid_persons:
        The MiD donor household / person tables.
    rng:
        Random state for stochastic attribute imputation (employment, licence,
        PT subscription). Defaults to ``np.random.RandomState(0)`` for backward
        compatibility; the calling stage should pass the pipeline's seeded rng.

    Returns
    -------
    pandas.DataFrame
        One row per synthetic person, with ``household_id`` / ``person_id``, the
        cell, demographics (``age`` / ``sex``), person attributes (``employed`` /
        ``has_license``), the joined household attributes (``economic_status`` /
        ``household_income_eur`` / ``number_of_cars``), the derived
        ``car_availability``, and the schema-gap columns (``age_range``,
        ``high_income``, ``household_size``, ``is_urban_resident``,
        ``pt_subscription_type``, ``socioprofessional_class``,
        ``source_person_id``, ``source_household_id``).
    """
    rng = rng if rng is not None else np.random.RandomState(0)
    households = expand.assign_synthetic_household_ids(
        merged_households, donor_col=donor_col
    )
    persons = expand.expand_to_persons(households, mid_persons, donor_col=donor_col)
    persons = expand.map_demographics(persons)

    # Derive commune_id, departement_id, iris_id from the 12-digit ARS column
    # (joined by stage.py from the cells parquet onto the merged households).
    # Format matches the default IPF producer exactly -- see derive_zone_ids docstring.
    persons = derive_zone_ids(persons)

    persons = attributes.map_employed(persons, rng=rng)
    persons = attributes.map_has_license(persons, rng=rng)
    persons = attributes.map_has_pt_subscription(persons, rng=rng)

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

    # --- Schema-gap columns (integration spec Section 5.1) -------------------
    # age_range: matches synthesis/population/enriched.py lines 110-114 exactly.
    # (-1,10] = primary_school; (10,14] = middle_school; (14,17] = high_school;
    # (17,inf) = higher_education (the default in enriched.py, age >= 18).
    persons["age_range"] = pd.cut(
        persons["age"],
        bins=_AGE_RANGE_BINS,
        labels=_AGE_RANGE_LABELS,
    )

    # high_income: convenience flag for income-elastic analyses.
    persons["high_income"] = persons["household_income"] == "over_7000"

    # household_size: number of persons in the synthetic household.
    persons["household_size"] = (
        persons.groupby("household_id")["person_id"].transform("size")
    )

    # is_urban_resident: True when the person lives inside the Braunschweig core
    # city (the inside_braunschweig flag is added by the handoff stage; not yet
    # available here, so the column is provisionally set to False).
    if "inside_braunschweig" in persons.columns:
        persons["is_urban_resident"] = persons["inside_braunschweig"]
    else:
        persons["is_urban_resident"] = False

    # provenance IDs: preserve the MiD donor keys so every synthetic person is
    # traceable to the survey respondent whose trips were used.
    persons["source_person_id"] = persons["P_ID"].astype("string")
    persons["source_household_id"] = persons[donor_col].astype("string")

    persons = attributes.map_socioprofessional_class(persons)
    persons = attributes.map_pt_subscription_type(persons, rng=rng)

    # weight = 1.0: popsim_mid produces an already-expanded population (each row
    # is one synthetic person, no stochastic rounding needed). synthesis.population.sampled
    # requires this column; it uses floor(weight) + Bernoulli(frac) to replicate households,
    # so weight=1.0 means every synthetic household is replicated exactly once before the
    # sampling_rate selection, matching the behaviour of braunschweig.ipf.attributed.
    persons["weight"] = 1.0

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
