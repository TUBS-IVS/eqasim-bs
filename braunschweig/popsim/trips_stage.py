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


def apply_per_person_jitter(table: pd.DataFrame, random_seed: int) -> pd.DataFrame:
    """Apply the eqasim per-person departure-time jitter to a trips table.

    Replicates the jitter formula from ``synthesis/population/trips.py`` exactly:

      interval = min(1800.0, first_departure_time_per_person)
      offset   = random_sample * interval * 2.0 - interval
                 (one draw per person, repeated for every trip in the chain)

    Both ``departure_time`` and ``arrival_time`` are shifted by the same offset
    (preserving within-chain ordering) and rounded to integer seconds.

    This function is factored out of :func:`run` so that the ENTD donor adapter
    (``braunschweig.popsim.sources.entd.EntdSource.build_trips``) can apply the
    identical jitter without duplicating the formula.

    Parameters
    ----------
    table:
        Trip table with at least ``person_id``, ``departure_time``,
        ``arrival_time`` (numeric, seconds).  Should be sorted by
        ``(person_id, departure_time)`` before calling so that ``trip_index``
        order is preserved.
    random_seed:
        Integer seed for ``np.random.RandomState``.

    Returns
    -------
    pd.DataFrame
        The input table (modified in-place) with jittered and rounded
        departure/arrival times.  The table is returned for chaining.
    """
    random = np.random.RandomState(random_seed)

    person_order = table["person_id"].unique()

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

    per_person_raw = random.random_sample(size=(len(counts),))
    per_person_offset = per_person_raw * interval * 2.0 - interval
    offset = np.repeat(per_person_offset, counts)

    table["departure_time"] = np.round(table["departure_time"] + offset)
    table["arrival_time"] = np.round(table["arrival_time"] + offset)

    assert (table["departure_time"] >= 0.0).all(), (
        "departure_time must be non-negative after jitter; "
        "check that min(1800, first_departure) clipping is correct."
    )
    assert (table["arrival_time"] >= 0.0).all(), (
        "arrival_time must be non-negative after jitter."
    )
    return table


def run(persons: pd.DataFrame, mid_wege: pd.DataFrame, *, random_seed: int) -> pd.DataFrame:
    """Build popsim_mid trips in the synthesis.population.trips 11-column contract.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id``, ``H_ID``, ``P_ID``.
    mid_wege:
        MiD 2023 Wege with at least ``H_ID``, ``P_ID``, ``W_ID``, ``W_ZWECK``,
        ``hvm_imp``, ``W_SZS``, ``W_SZM``, ``W_AZS``, ``W_AZM``. Optional:
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
    # Delegates to the shared apply_per_person_jitter (factored so the ENTD
    # adapter can call the same formula without duplication).
    # --------------------------------------------------------------------------
    table = apply_per_person_jitter(table, random_seed=random_seed)

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
    # Donor source identifier: must match the value configured in popsim.stage
    # (default "mid" -> MidSource -> mid.load_mid_wege + trips_stage.run, byte-identical).
    context.config("braunschweig.population.popsim.source", "mid")

    source_name = context.config("braunschweig.population.popsim.source", "mid")
    if source_name == "entd":
        # popsim_open: the full-composition ENTD frames (including all trips) come
        # from data.hts.entd.FILTERED, NOT data.hts.selected (= reweighted, which
        # keeps one person per household for IPF matching). Must match the seed
        # donor used in braunschweig.popsim.stage. Alias to "hts_donor".
        context.stage("data.hts.entd.filtered", alias="hts_donor")


def execute(context):
    from braunschweig.popsim import sources

    persons = context.stage("persons")
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    # synpp's ExecuteContext.config() takes only the key; the default ("mid") is
    # registered in configure().
    source_name = context.config("braunschweig.population.popsim.source")

    source = sources.get_source(source_name)
    logger.info("[trips_stage] active donor source: %s", source.name)

    # Load donor tables through the source adapter.
    # For source="mid": reads MiD CSV files from mid_dir (byte-identical).
    # For source="entd": receives the cleaned ENTD frames from the synpp DAG
    # (registered in configure as alias "hts_donor") and injects them.
    if source_name == "entd":
        hts_hh, hts_persons, hts_trips = context.stage("hts_donor")
        _donor_households, _donor_persons, donor_trips = source.load_donor(
            mid_dir, injected=(hts_hh, hts_persons, hts_trips)
        )
    else:
        # popsim_mid (default): reads MiD CSV files directly; households and
        # persons tables are not needed here (trips only).
        _donor_households, _donor_persons, donor_trips = source.load_donor(mid_dir)

    return source.build_trips(
        persons, donor_trips, random_seed=int(context.config("random_seed"))
    )
