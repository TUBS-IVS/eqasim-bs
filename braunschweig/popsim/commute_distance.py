"""synpp stage: per-person work/education commute distance from popsim MiD trips.

Aliased to synthesis.population.spatial.commute_distance for the popsim_mid workflow.

The default stage (synthesis/population/spatial/commute_distance.py) joins synthetic
persons to the HTS commute distance via hts_id. In popsim_mid, each synthetic person
IS a MiD respondent, so the commute distance is taken directly from the donor's real
MiD trip length (euclidean_distance on the longest work/education trip per person).

Output contract matches the default stage exactly:
    {"work": DataFrame[person_id, commute_distance],
     "education": DataFrame[person_id, commute_distance]}

This is the two-column subset that synthesis/population/spatial/primary/locations.py
consumes via a left-merge on person_id (lines 104-105). The hts_id column present in
the default stage output is not used downstream and is intentionally omitted here.

euclidean_distance is in metres (wegkm_imp * 1000 / 1.3 ENTD detour factor),
consistent with the units expected by the gravity-based location assignment.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _per_purpose(trips: pd.DataFrame, purpose: str) -> pd.DataFrame:
    """Return the maximum euclidean_distance work/education trip per person.

    Taking the maximum rather than the mean or first matches the intent of
    the default HTS commute-distance stage, which records the actual commute
    trip to the primary workplace/school. When a person has multiple trips of
    the same purpose (e.g. went to work twice), the longest trip is the
    better proxy for the home-to-destination straight-line distance.
    """
    filtered = trips[trips["following_purpose"] == purpose]
    dist = (
        filtered
        .groupby("person_id")["euclidean_distance"]
        .max()
        .reset_index()
    )
    return dist.rename(columns={"euclidean_distance": "commute_distance"})[
        ["person_id", "commute_distance"]
    ]


def run(trips: pd.DataFrame) -> dict:
    """Compute per-person commute distances for work and education purposes.

    Parameters
    ----------
    trips:
        DataFrame with at least ``person_id``, ``following_purpose``
        (values include "work" and "education"), and ``euclidean_distance``
        (straight-line distance in metres, derived from MiD wegkm_imp).

    Returns
    -------
    dict with keys "work" and "education", each a DataFrame with columns
    ["person_id", "commute_distance"] (distance in metres, float).
    """
    work = _per_purpose(trips, "work")
    education = _per_purpose(trips, "education")

    logger.info(
        "[popsim.commute_distance] work commute distances: %d persons; "
        "education commute distances: %d persons",
        len(work),
        len(education),
    )

    return {"work": work, "education": education}


def configure(context):
    # In the popsim_mid workflow this stage is aliased to
    # synthesis.population.spatial.commute_distance and the trips stage is
    # aliased to synthesis.population.trips, so the alias here resolves to
    # the popsim trips output rather than the default HTS trips.
    context.stage("synthesis.population.trips", alias="trips")


def execute(context):
    trips = context.stage("trips")
    return run(trips)
