"""synpp stage: popsim_mid trips in the eqasim synthesis.population.trips contract.

Aliased to synthesis.population.trips for the popsim_mid workflow. Builds the
validated MiD trip table via braunschweig.popsim.trips.build_validated_trip_table,
applies the SAME per-person departure-time jitter as synthesis/population/trips.py
(one random offset per person, bounded by min(1800, first_departure_time), applied
identically to every trip in that person's chain), and derives euclidean_distance
from MiD wegkm_imp using the ENTD detour factor 1.3.

Per-person jitter formula (matches synthesis/population/trips.py exactly):
    interval = min(1800.0, first_departure_time_per_person)
    offset    = random_sample_per_person * interval * 2.0 - interval
                -> range [-interval, +interval)
    All times for a person are shifted by the SAME offset to preserve ordering.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim import trips as popsim_trips

logger = logging.getLogger(__name__)

# The 11-column output contract from synthesis/population/trips.py; downstream
# stages (synthesis/population/activities.py) expect exactly these column names.
CONTRACT = [
    "person_id",
    "trip_index",
    "departure_time",
    "arrival_time",
    "preceding_purpose",
    "following_purpose",
    "is_first_trip",
    "is_last_trip",
    "trip_duration",
    "activity_duration",
    "mode",
]

# ENTD straight-line detour factor: routed distance / straight-line distance.
# Source: eqasim ENTD processing (same constant as used in synthesis/population/trips.py
# and the MiD school-distance calibration).
DETOUR_FACTOR = 1.3


def run(persons: pd.DataFrame, mid_wege: pd.DataFrame, *, random_seed: int) -> pd.DataFrame:
    """Build popsim_mid trips in the synthesis.population.trips 11-column contract.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id``, ``H_ID``, ``P_ID``.
    mid_wege:
        MiD 2023 Wege with at least ``H_ID``, ``P_ID``, ``W_ID``, ``W_ZWECK``,
        ``hvm``, ``W_SZS``, ``W_SZM``, ``W_AZS``, ``W_AZM``. Optional:
        ``wegkm_imp`` (routed km; used to derive ``euclidean_distance``).
    random_seed:
        Integer seed for the per-person departure-time jitter RNG.

    Returns
    -------
    pd.DataFrame
        One row per (synthetic person, MiD trip), columns: the 11-column
        synthesis.population.trips contract + ``euclidean_distance`` (metres,
        straight-line = wegkm_imp * 1000 / DETOUR_FACTOR) when wegkm_imp is
        present, plus ``trip_key`` (traceability) and any remaining MiD extras.
    """
    table, report = popsim_trips.build_validated_trip_table(persons, mid_wege)

    logger.info(
        "[trips_stage] trip table built: %d trips for %d persons; "
        "valid=%s home_closed_rate=%.1f%%",
        len(table),
        table["person_id"].nunique(),
        report.is_valid,
        (report.home_end_closure_rate * 100.0) if hasattr(report, "home_end_closure_rate") else float("nan"),
    )

    # Sort by (person_id, trip_index) to match the ordering assumed by downstream
    # stages (synthesis/population/activities.py uses trip_index, not trip_id).
    table = table.sort_values(["person_id", "trip_index"]).reset_index(drop=True)

    # --------------------------------------------------------------------------
    # Per-person departure-time jitter.
    # Replicates synthesis/population/trips.py exactly:
    #   counts  = number of trips per person (in person_id order)
    #   interval = min(1800.0, first_departure_time) per person
    #   offset   = random_sample * interval * 2.0 - interval  (one per person)
    #   The offset is then REPEATED for every trip of that person.
    # Using the same formula (random_sample, not uniform) ensures the jitter
    # distribution is identical to the canonical HTS path.
    # --------------------------------------------------------------------------
    random = np.random.RandomState(random_seed)

    # Persons in stable order (groupby preserves order of first occurrence when
    # sorted=False is not available in older pandas; sort=False in groupby).
    person_order = table["person_id"].unique()  # first-occurrence order after sort above

    counts = (
        table[["person_id"]]
        .groupby("person_id", sort=False)
        .size()
        .reindex(person_order)
        .values
    )

    interval = (
        table[["person_id", "departure_time"]]
        .groupby("person_id", sort=False)["departure_time"]
        .min()
        .reindex(person_order)
        .values
    )
    interval = np.minimum(1800.0, interval)

    # One random draw per person; replicate the eqasim formula verbatim.
    per_person_raw = random.random_sample(size=(len(counts),))
    per_person_offset = per_person_raw * interval * 2.0 - interval

    # Expand to one offset per trip row (same offset for all trips of a person).
    offset = np.repeat(per_person_offset, counts)

    table["departure_time"] = table["departure_time"] + offset
    table["arrival_time"] = table["arrival_time"] + offset

    # Round to integer seconds, matching synthesis/population/trips.py.
    table["departure_time"] = np.round(table["departure_time"])
    table["arrival_time"] = np.round(table["arrival_time"])

    assert (table["departure_time"] >= 0.0).all(), (
        "departure_time must be non-negative after jitter; "
        "check that min(1800, first_departure) clipping is correct."
    )
    assert (table["arrival_time"] >= 0.0).all(), (
        "arrival_time must be non-negative after jitter."
    )

    # --------------------------------------------------------------------------
    # Euclidean distance from MiD wegkm_imp (routed km -> straight-line metres).
    # wegkm_imp is the MiD imputed routed trip length in kilometres; dividing by
    # the ENTD detour factor gives the straight-line distance in km; * 1000 -> m.
    # --------------------------------------------------------------------------
    if "wegkm_imp" in table.columns:
        table["euclidean_distance"] = (
            table["wegkm_imp"].astype(float) * 1000.0 / DETOUR_FACTOR
        )

    # Build final column order: CONTRACT first, then extras (euclidean_distance,
    # trip_key, and all remaining MiD columns) so downstream code that selects
    # CONTRACT columns works without needing to know about extras.
    extras_ordered = [
        c for c in ("euclidean_distance", "trip_key")
        if c in table.columns
    ]
    remaining = [
        c for c in table.columns
        if c not in CONTRACT and c not in extras_ordered
    ]
    return table[CONTRACT + extras_ordered + remaining]


def configure(context):
    # Read from synthesis.population.sampled (not the raw producer): sampled carries the
    # reassigned integer person_id and the preserved donor keys H_ID/P_ID, so the trip
    # table is built against the already-sampled and id-remapped synthetic population.
    context.stage("synthesis.population.sampled", alias="persons")
    context.config("random_seed")
    context.config("braunschweig.population.popsim.mid_dir")


def execute(context):
    from braunschweig.popsim import mid

    persons = context.stage("persons")
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    wege = mid.load_mid_wege(mid_dir)
    return run(persons, wege, random_seed=int(context.config("random_seed")))
