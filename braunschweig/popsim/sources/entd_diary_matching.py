"""ENTD diary-donor chain matching for trip-less synthetic persons.

ENTD records a travel diary for ONE selected person per household, while
popsim_open expands FULL households -- so only ~40% of the synthetic persons
inherit a trip chain from their attribute donor. The remaining persons are
matched against the DIARY-donor pool (donors that have trips) with the legacy
hierarchical-relaxation statistical matching
(:func:`synthesis.population.matched.match_donors`) and copy the matched
donor's chain. :func:`_derive_chain_matching_frame` builds the matching-key
columns and :func:`_match_trip_less_persons_to_diary_donors` runs the
matching itself; both are called from :meth:`EntdSource.build_trips`.

Distinct from :mod:`braunschweig.popsim.chain_matching`, which only holds the
age-bin edges and the pool-capped minimum-observations helper SHARED between
this module and the popsim_mid stage-B matched-chain replacement
(:func:`braunschweig.popsim.trips._match_unfixable`) -- that module has no
notion of an ENTD "diary donor" and does not itself call ``match_donors``.
This module is the ENTD-specific orchestration that consumes those shared
constants/helpers to build the diary-donor pool and perform the match.

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports every name here so external imports of the facade
module are unaffected.
"""

from __future__ import annotations

import logging

import pandas as pd

# Age-class bins and the minimum-observations default are shared with the
# popsim_mid stage B matched chain replacement (braunschweig.popsim.trips);
# single-sourced in braunschweig.popsim.chain_matching (re-exported here for
# backward compatibility of the module-level names).
from braunschweig.popsim.chain_matching import (  # noqa: E402 (kept near use)
    CHAIN_MATCHING_AGE_BOUNDARIES,
    CHAIN_MATCHING_MINIMUM_OBSERVATIONS,
    derive_age_class,
    effective_minimum_observations,
)

# Reusing the legacy statistical-matching machinery from the upstream-shared
# synthesis tree is established practice in this package (see
# braunschweig.popsim.stratum, which imports URBAN_TYPE_TO_URBAN_CLASS from the
# same module, and braunschweig.popsim.trips, which imports data.hts.hts).
from synthesis.population.matched import household_size_class, match_donors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Diary-donor chain matching (trip-less persons)
# ---------------------------------------------------------------------------
# ENTD records a travel diary for ONE selected person per household, while
# popsim_open expands FULL households -- so only ~40% of the synthetic persons
# inherit a trip chain from their attribute donor. The remaining persons are
# matched against the DIARY-donor pool (donors that have trips) with the legacy
# hierarchical-relaxation statistical matching (synthesis.population.matched.
# match_donors) and copy the matched donor's chain.
#
# Matching keys in priority order: match_donors relaxes keys FROM THE END of
# this list, so the order encodes priority and the FIRST key is never relaxed.
CHAIN_MATCHING_COLUMNS = [
    "sex", "age_class", "employed", "socioprofessional_class",
    "household_size_class",
]

# Continuous matching columns derived from a differently-named raw column.
_CHAIN_MATCHING_SOURCE_COLUMN = {
    "age_class": "age",
    "household_size_class": "household_size",
}


def _derive_chain_matching_frame(persons: pd.DataFrame):
    """Derive the matching-key columns for the diary-donor chain matching.

    Returns ``(frame, columns)``: a copy of ``persons`` extended with the
    derived ``age_class`` / ``household_size_class`` columns, and the list of
    matching keys that are actually available on the frame. Missing keys are
    dropped from the list with a logged warning (no silent narrowing).
    """
    out = persons.copy()
    columns = []

    for column in CHAIN_MATCHING_COLUMNS:
        source_column = _CHAIN_MATCHING_SOURCE_COLUMN.get(column, column)
        if source_column not in out.columns:
            logger.warning(
                "[EntdSource] chain matching: key '%s' dropped because column "
                "'%s' is missing on the synthetic persons frame.",
                column, source_column,
            )
            continue
        if column == "age_class":
            # Same binning as the legacy matching stage (shared helper).
            out["age_class"] = derive_age_class(out["age"])
        elif column == "household_size_class":
            # Shared 1..5(+) binning imported from the legacy matching stage.
            out["household_size_class"] = household_size_class(out["household_size"])
        columns.append(column)

    return out, columns


def _match_trip_less_persons_to_diary_donors(
    persons: pd.DataFrame,
    persons_with_trips: set,
    *,
    random_seed: int,
) -> pd.DataFrame:
    """Match non-diary synthetic persons to ENTD diary donors.

    ENTD records a travel diary for only ONE selected person per household
    (``is_kish=True``). A diary respondent is EITHER mobile (has trips) OR
    genuinely immobile (``is_kish=True`` with 0 trips). A non-diary member
    (``is_kish=False``) has NO travel information and must inherit a chain.

    Targets and pool (is_kish-aware path, preferred)
    ------------------------------------------------
    - ``targets`` = persons with ``is_kish=False`` AND no direct trips (these
      need a chain).  Genuinely-immobile diary respondents (``is_kish=True``,
      0 trips) are NOT targets: they stay trip-less by themselves.
    - ``pool`` = ALL diary respondents (``is_kish=True``), deduplicated on
      ``source_person_id``, weight 1.0 -- INCLUDING the immobile ones.  This is
      the crucial point: the pool represents the diary population's true
      mobile:immobile mix, so a target matched to an immobile donor naturally
      inherits ZERO trips (the downstream ``donor_trips.merge(... on hts_id)``
      yields no rows for an immobile donor's hts_id) and correctly stays home.
      No special handling is needed for immobility beyond including those
      donors in the pool.

    Fallback (no ``is_kish`` column on the frame)
    ---------------------------------------------
    If the persons frame carries no ``is_kish`` column (e.g. the MiD path, or
    tests with minimal fixtures), the legacy behaviour is used: ``pool`` =
    persons WITH trips, ``targets`` = persons WITHOUT trips. This is logged
    loudly (no silent fallback) because on the real ENTD path it would force
    every matched person to be mobile and erase immobility.

    The donor pool is built from the SYNTHETIC persons frame itself: every
    synthetic person carries its donor's mapped attributes, so the diary
    respondents provide the per-donor attribute rows after deduplication on
    ``source_person_id``. This guarantees an identical attribute vocabulary on
    both sides and avoids re-loading ENTD tables. Donor weight is 1.0 each (the
    frame is already expanded; after deduplication every diary donor is one
    equally-weighted pool row).

    Returns a DataFrame ``[person_id, hts_id]`` (synthetic person -> diary
    donor person_id); persons that cannot be matched are absent from the
    result (they stay trip-less, eqasim stay-home convention) and their count
    is logged loudly.
    """
    frame, columns = _derive_chain_matching_frame(persons)
    empty = pd.DataFrame(columns=["person_id", "hts_id"])

    has_trips = frame["person_id"].isin(persons_with_trips)

    if "is_kish" in frame.columns:
        # is_kish-aware path (real ENTD): the pool is ALL diary respondents
        # (mobile + genuinely immobile), so the matched non-diary persons
        # inherit the diary population's true mobile:immobile mix. A person
        # matched to an immobile diary donor inherits zero trips downstream.
        is_diary = frame["is_kish"].fillna(False).astype(bool)
        # Non-diary members without direct trips are the ones needing a chain.
        targets = frame.loc[(~is_diary) & (~has_trips)]
        pool = (
            frame.loc[is_diary]
            .drop_duplicates(subset="source_person_id", keep="first")
            .rename(columns={"source_person_id": "hts_id"})
        )
    else:
        # Legacy fallback (no is_kish on the frame, e.g. MiD path or minimal
        # test fixtures): pool = persons WITH trips. Logged loudly because on
        # the real ENTD path this would force 100% mobility (no immobile
        # diary donors in the pool) -- a high reliance on this path is a bug
        # signal (CLAUDE.md no-silent-fallback).
        logger.warning(
            "[EntdSource] chain matching: no 'is_kish' column on the synthetic "
            "persons frame; falling back to the legacy pool (persons WITH trips). "
            "On the real ENTD path this would erase immobility (matched persons "
            "are forced mobile) -- verify is_kish reached the persons frame.",
        )
        targets = frame.loc[~has_trips]
        # Note: replicates of the same donor share all donor attributes except
        # the synthetic household_size (overwritten per synthetic household by
        # map_person_attributes); keep="first" makes the pool row deterministic.
        pool = (
            frame.loc[has_trips]
            .drop_duplicates(subset="source_person_id", keep="first")
            .rename(columns={"source_person_id": "hts_id"})
        )

    pool["weight"] = 1.0

    if len(targets) == 0:
        return empty
    if len(pool) == 0:
        logger.warning(
            "[EntdSource] chain matching: diary-donor pool is EMPTY; all %d "
            "trip-less persons stay trip-less. The donor trips table is likely "
            "disconnected from the synthetic persons (check source_person_id).",
            len(targets),
        )
        return empty
    if not columns:
        logger.warning(
            "[EntdSource] chain matching: NO matching keys available on the "
            "persons frame; all %d trip-less persons stay trip-less.",
            len(targets),
        )
        return empty

    # Small pools (mini smokes / tests) may be below the legacy default of 20
    # donors per cell; the shared helper caps the minimum at half the pool size
    # (floor 1) while large real pools keep the legacy 20.
    minimum_observations = effective_minimum_observations(len(pool))

    # Feasibility pre-filter on the FIRST key: match_donors never relaxes it,
    # and a single infeasible target would raise RuntimeError for the whole
    # call. Targets whose first-key value has too few diary donors are left
    # trip-less individually (logged loudly) instead of aborting everyone.
    first_key = columns[0]
    donor_counts = pool[first_key].value_counts()
    feasible_values = set(donor_counts[donor_counts >= minimum_observations].index)
    feasible = targets[first_key].isin(feasible_values)
    n_infeasible = int((~feasible).sum())
    if n_infeasible > 0:
        logger.warning(
            "[EntdSource] chain matching: %d/%d trip-less persons are "
            "unmatchable (their '%s' value has < %d diary donors) and stay "
            "trip-less (stay-home plans). A high rate signals a broken donor "
            "pool, not a tolerable fallback.",
            n_infeasible, len(targets), first_key, minimum_observations,
        )
    targets = targets.loc[feasible]
    if len(targets) == 0:
        return empty

    try:
        return match_donors(
            targets[["person_id"] + columns],
            pool[["hts_id", "weight"] + columns],
            matching_columns=columns,
            minimum_observations=minimum_observations,
            random_seed=random_seed,
        )
    except RuntimeError:
        # Belt-and-braces: match_donors raises when a target is unmatched even
        # at full relaxation. The pre-filter above should prevent this; if it
        # fires anyway, leave ALL remaining trip-less persons without chains
        # and report it loudly rather than crashing the trips stage.
        logger.error(
            "[EntdSource] chain matching: match_donors failed at full "
            "relaxation for %d trip-less persons; they stay trip-less. "
            "Investigate the diary-donor pool composition.",
            len(targets),
        )
        return empty
