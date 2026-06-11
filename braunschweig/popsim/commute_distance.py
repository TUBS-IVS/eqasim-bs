"""synpp stage: per-person work/education commute distance from popsim MiD trips.

Aliased to synthesis.population.spatial.commute_distance for the popsim_mid workflow.

The default stage (synthesis/population/spatial/commute_distance.py) joins synthetic
persons to the HTS commute distance via hts_id. In popsim_mid, each synthetic person
IS a MiD respondent, so the commute distance is taken directly from the donor's real
MiD trips. Mirroring the legacy HTS stage (data/hts/commute_distance.py), only
home<->purpose legs are considered and the FIRST such leg per person is selected --
NOT the maximum over all purpose-bound trips: MiD W_ZWECK=2 (dienstlich/business)
also maps to "work", so a single long business trip (work->work, up to ~730 km
euclidean) would otherwise dominate the commute distance and drag the workplace
assignment to the farthest candidate. Selected legs with a NaN distance are imputed
from the pool of observed leg distances (seeded draw, rate logged).

Known limitation: persons with a work/education activity but NO home<->purpose leg
at all (e.g. only work->shop->home chains, where the homebound leg starts at "shop")
simply get no row in the output. The downstream default-stage contract tolerates
that -- the legacy stage likewise imputes only within its joined frame.

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

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _per_purpose(trips: pd.DataFrame, purpose: str, *, rng: np.random.RandomState) -> pd.DataFrame:
    """First home<->purpose commute leg per person (mirrors the legacy HTS stage),
    NOT the max over all purpose-bound trips (W_ZWECK=2 dienstlich also maps to
    'work', so a single long business trip would dominate). Persons whose selected
    leg has a NaN distance are imputed from the observed leg-distance pool
    (seeded draw); the imputation rate is logged (no silent NaN)."""
    is_leg = (
        ((trips["preceding_purpose"] == "home") & (trips["following_purpose"] == purpose))
        | ((trips["preceding_purpose"] == purpose) & (trips["following_purpose"] == "home"))
    )
    legs = trips[is_leg].drop_duplicates("person_id", keep="first")
    legs = legs.rename(columns={"euclidean_distance": "commute_distance"})[
        ["person_id", "commute_distance"]
    ].copy()
    missing = legs["commute_distance"].isna()
    pool = legs.loc[~missing, "commute_distance"].to_numpy()
    n_missing = int(missing.sum())
    if n_missing and len(pool):
        legs.loc[missing, "commute_distance"] = pool[rng.randint(len(pool), size=n_missing)]
    logger.info(
        "[popsim.commute_distance] %s: %d persons with a home<->%s leg; "
        "%d (%.1f%%) had NaN distance imputed from the observed pool.",
        purpose, len(legs), purpose, n_missing,
        100.0 * n_missing / len(legs) if len(legs) else 0.0,
    )
    return legs


def run(trips: pd.DataFrame, *, random_seed: int = 0) -> dict:
    """Compute per-person commute distances for work and education purposes.

    Parameters
    ----------
    trips:
        DataFrame with at least ``person_id``, ``preceding_purpose``,
        ``following_purpose`` (values include "home", "work" and "education"),
        and ``euclidean_distance`` (straight-line distance in metres, derived
        from MiD wegkm_imp).
    random_seed:
        Seed for the imputation draw of missing leg distances.

    Returns
    -------
    dict with keys "work" and "education", each a DataFrame with columns
    ["person_id", "commute_distance"] (distance in metres, float).
    """
    rng = np.random.RandomState(random_seed)
    work = _per_purpose(trips, "work", rng=rng)
    education = _per_purpose(trips, "education", rng=rng)

    return {"work": work, "education": education}


def configure(context):
    context.config("random_seed")
    # In the popsim_mid workflow this stage is aliased to
    # synthesis.population.spatial.commute_distance and the trips stage is
    # aliased to synthesis.population.trips, so the alias here resolves to
    # the popsim trips output rather than the default HTS trips.
    context.stage("synthesis.population.trips", alias="trips")


def execute(context):
    trips = context.stage("trips")
    return run(trips, random_seed=int(context.config("random_seed")))
