"""ENTD trip building: the synthesis.population.trips CONTRACT frame from ENTD donor trips.

:func:`build_trips` joins synthetic persons onto their donor's ENTD trip chain
via ``source_person_id``.  ENTD records a travel diary for only ONE selected
person per household, so ~60% of the synthetic persons have no direct donor
trips; those non-diary persons are matched to a diary donor (a donor that has
trips) via the legacy hierarchical-relaxation statistical matching in
:mod:`braunschweig.popsim.sources.entd_diary_matching` and inherit that
donor's chain.  All coverage and mobility rates are logged (CLAUDE.md
no-silent-fallback).

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports the name so external imports of the facade module are
unaffected. ``EntdSource.build_trips`` is a one-line delegation to the
module-level function here (``EntdSource`` has no instance state, so ``self``
carried nothing the moved body needed).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.sources.entd_diary_matching import (
    _match_trip_less_persons_to_diary_donors,
)
from braunschweig.popsim.sources.entd_schema import _require_columns
from braunschweig.popsim.sources.entd_vocabulary import ENTD_DETOUR_FACTOR
from braunschweig.popsim.trips_stage import CONTRACT, apply_per_person_jitter

# Logger name string identical to the facade's (braunschweig.popsim.sources.entd)
# so log records emitted from here are indistinguishable from records emitted
# before the extraction; logging.getLogger caches by name, so this returns the
# SAME logger object as the facade's `logging.getLogger(__name__)`.
logger = logging.getLogger("braunschweig.popsim.sources.entd")


def build_trips(
    persons: pd.DataFrame,
    donor_trips: pd.DataFrame,
    *,
    random_seed: int,
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
) -> pd.DataFrame:
    """Build the synthesis.population.trips contract DataFrame from ENTD trips.

    ENTD trips are already in canonical eqasim schema (mode/purpose/time
    columns all present from ``data.hts.entd.cleaned``), so no mode or
    purpose re-mapping is needed.  The join is keyed by
    ``source_person_id`` on the synthetic persons frame (set by
    ``map_person_attributes`` to the ENTD ``person_id``).

    ENTD records a travel diary only for ONE selected person per household,
    so the direct join covers only ~40% of the synthetic persons.  Persons
    WITHOUT donor trips are matched to a DIARY donor (a donor that has
    trips) via the legacy hierarchical-relaxation statistical matching
    (``synthesis.population.matched.match_donors``) on
    ``CHAIN_MATCHING_COLUMNS`` and inherit that donor's full chain; those
    trip rows carry the diary donor as ``chain_donor_id`` while
    ``source_person_id`` keeps the person's attribute donor.  Persons that
    cannot be matched even at full relaxation stay trip-less (eqasim
    stay-home convention); all rates are logged.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` (synthetic integer),
        ``household_id`` (synthetic), and ``source_person_id`` (ENTD
        ``person_id``, the donor key).
    donor_trips:
        ENTD trip table from ``load_donor``.  Must carry ``person_id``
        (the ENTD donor person key, matching ``source_person_id``), all
        CONTRACT columns, and ``euclidean_distance``.
    random_seed:
        Integer seed for the per-person departure-time jitter.
    escort_purpose:
        map MiD W_ZWECK {6, 13} to the dedicated 'escort' purpose (issue #201).
        NOT supported for ENTD; passing True raises ``NotImplementedError``
        (the ENTD donor has no W_ZWECK escort coding).
    escort_passive_education:
        map the passive escort leg (MiD W_ZWECK 13) to 'education' instead
        of 'escort' (issue #256). NOT supported for ENTD, for the same
        reason as ``escort_purpose``; passing True raises
        ``NotImplementedError``.

    Returns
    -------
    pd.DataFrame
        One row per (synthetic person, donor trip), columns: the 11-column
        synthesis.population.trips CONTRACT + ``trip_index`` +
        ``euclidean_distance`` + remaining ENTD trip extras.
        Global ``trip_id`` is reassigned as a sequential integer.

    Raises
    ------
    NotImplementedError
        If ``escort_purpose`` or ``escort_passive_education`` is True (the
        ENTD donor has no W_ZWECK escort coding; disable both for
        popsim_open runs).
    """
    if escort_purpose:
        raise NotImplementedError(
            "[popsim.sources.entd] escort_purpose=True is not supported for the "
            "ENTD donor (no W_ZWECK escort coding); disable escort_purpose for "
            "popsim_open runs."
        )
    if escort_passive_education:
        raise NotImplementedError(
            "[popsim.sources.entd] escort_passive_education=True is not supported "
            "for the ENTD donor (no W_ZWECK escort coding); disable "
            "escort_passive_education for popsim_open runs."
        )
    _require_columns(persons, ["person_id", "source_person_id"], table_name="persons")
    _require_columns(donor_trips, ["person_id"], table_name="donor_trips")

    # Join synthetic persons onto donor trips via source_person_id == donor person_id.
    # Each synthetic person inherits the donor person's full trip chain.
    trips = donor_trips.merge(
        persons[["person_id", "source_person_id"]].rename(
            columns={"person_id": "synthetic_person_id"}
        ),
        left_on="person_id",
        right_on="source_person_id",
        how="inner",
    )

    persons_with_direct_trips = set(trips["synthetic_person_id"].unique())
    n_persons_with_trips = len(persons_with_direct_trips)
    n_persons_total = len(persons)
    n_persons_without_trips = n_persons_total - n_persons_with_trips
    logger.info(
        "[EntdSource] build_trips: %d trips for %d/%d synthetic persons "
        "(%.1f%% have donor trips); %d persons without trips.",
        len(trips), n_persons_with_trips, n_persons_total,
        100.0 * n_persons_with_trips / max(n_persons_total, 1),
        n_persons_without_trips,
    )

    # --- Diary-donor chain matching for non-diary persons -----------------
    # ENTD diaries cover only ONE selected person per household, so most
    # synthetic persons have no direct donor trips. Match the NON-DIARY
    # members (is_kish=False, no trips) to a diary donor via the legacy
    # statistical matching and copy that chain. The donor pool includes the
    # genuinely IMMOBILE diary respondents, so a person matched to one of
    # them inherits ZERO trips (the inner merge below finds no rows for the
    # immobile donor's hts_id) and correctly stays home -- this reproduces
    # the diary population's mobile:immobile mix instead of forcing 100%
    # mobility.
    n_persons_matched_total = 0       # non-diary persons matched to a diary donor
    n_persons_matched_with_trips = 0  # ... of which matched to a MOBILE donor (got trips)
    if n_persons_without_trips > 0:
        df_chain = _match_trip_less_persons_to_diary_donors(
            persons,
            persons_with_direct_trips,
            random_seed=random_seed,
        )
        if len(df_chain) > 0:
            n_persons_matched_total = df_chain["person_id"].nunique()
            matched_trips = donor_trips.merge(
                df_chain.rename(columns={"person_id": "synthetic_person_id"}),
                left_on="person_id",
                right_on="hts_id",
                how="inner",
            )
            # Persons matched to an IMMOBILE diary donor have no rows here
            # (the immobile donor's hts_id has no trips) -> they stay home.
            n_persons_matched_with_trips = (
                matched_trips["synthetic_person_id"].nunique()
                if len(matched_trips) > 0 else 0
            )
            # Traceability: the HTS chain provenance of these rows differs
            # from the person's attribute donor (source_person_id). The
            # diary donor whose chain was copied is recorded per trip row
            # as chain_donor_id; the persons frame itself is NOT modified.
            matched_trips["chain_donor_id"] = matched_trips["hts_id"]
            matched_trips = matched_trips.drop(columns=["hts_id"])
            matched_trips = matched_trips.merge(
                persons[["person_id", "source_person_id"]].rename(
                    columns={"person_id": "synthetic_person_id"}
                ),
                on="synthetic_person_id",
                how="left",
            )
            trips = pd.concat([trips, matched_trips], ignore_index=True, sort=False)
            logger.info(
                "[EntdSource] build_trips: chain matching attached %d trips to "
                "%d non-diary persons (%d matched to a mobile donor, %d matched "
                "to an immobile donor -> stay home); their HTS chain provenance "
                "(chain_donor_id) differs from the attribute donor "
                "(source_person_id).",
                len(matched_trips), n_persons_matched_total,
                n_persons_matched_with_trips,
                n_persons_matched_total - n_persons_matched_with_trips,
            )

    # Mobility breakdown (CLAUDE.md no-silent-fallback: all rates logged).
    # A person is MOBILE iff it ends up with at least one trip:
    #   direct-mobile (own diary chain) + matched-to-mobile (got a chain).
    # A person stays HOME iff it has no trips. The stay-home group splits
    # into:
    #   - genuinely-immobile diary respondents (is_kish=True with 0 direct
    #     trips; never a match target -> trip-less by themselves);
    #   - matched-to-immobile non-diary persons (matched to an immobile
    #     diary donor -> inherited an empty chain);
    #   - unmatchable non-diary persons left trip-less (logged separately by
    #     the matching helper).
    n_persons_matched_to_immobile = (
        n_persons_matched_total - n_persons_matched_with_trips
    )
    n_persons_mobile = n_persons_with_trips + n_persons_matched_with_trips
    n_persons_stay_home = n_persons_total - n_persons_mobile
    # Genuinely-immobile diary respondents = persons with is_kish=True that
    # had no direct trips (they are never match targets). When is_kish is
    # absent (legacy fallback path) this category does not exist.
    if "is_kish" in persons.columns:
        is_diary = persons["is_kish"].fillna(False).astype(bool)
        n_persons_genuinely_immobile_diary = int(
            (is_diary & ~persons["person_id"].isin(persons_with_direct_trips)).sum()
        )
    else:
        n_persons_genuinely_immobile_diary = 0
    n_persons_unmatched_non_diary = (
        n_persons_stay_home
        - n_persons_matched_to_immobile
        - n_persons_genuinely_immobile_diary
    )
    logger.info(
        "[EntdSource] build_trips coverage: direct-mobile %d/%d (%.1f%%), "
        "matched-to-mobile %d (%.1f%%), matched-to-immobile (stay home) %d "
        "(%.1f%%), genuinely-immobile diary (stay home) %d (%.1f%%), "
        "unmatched non-diary (stay home) %d (%.1f%%); resulting mobility "
        "share %.1f%% mobile / %.1f%% stay home.",
        n_persons_with_trips, n_persons_total,
        100.0 * n_persons_with_trips / max(n_persons_total, 1),
        n_persons_matched_with_trips,
        100.0 * n_persons_matched_with_trips / max(n_persons_total, 1),
        n_persons_matched_to_immobile,
        100.0 * n_persons_matched_to_immobile / max(n_persons_total, 1),
        n_persons_genuinely_immobile_diary,
        100.0 * n_persons_genuinely_immobile_diary / max(n_persons_total, 1),
        max(0, n_persons_unmatched_non_diary),
        100.0 * max(0, n_persons_unmatched_non_diary) / max(n_persons_total, 1),
        100.0 * n_persons_mobile / max(n_persons_total, 1),
        100.0 * n_persons_stay_home / max(n_persons_total, 1),
    )

    # Replace the ENTD person_id with the synthetic person_id.
    trips = trips.drop(columns=["person_id"]).rename(
        columns={"synthetic_person_id": "person_id"}
    )

    # Sort by (person_id, departure_time) for stable trip ordering.
    trips = trips.sort_values(["person_id", "departure_time"]).reset_index(drop=True)

    # Reassign global integer trip_id.
    trips["trip_id"] = np.arange(len(trips), dtype=np.int64)

    # trip_index: 0-based cumulative trip count per synthetic person.
    trips["trip_index"] = trips.groupby("person_id").cumcount()

    # Apply per-person departure-time jitter using the shared helper from
    # trips_stage (identical formula to synthesis/population/trips.py).
    trips = apply_per_person_jitter(trips, random_seed=random_seed)

    # euclidean_distance: the eqasim ENTD reweighted stage derives it as
    # routed_distance / 1.3 (data/hts/entd/reweighted.py:28; the same 1.3 detour
    # factor the MiD path uses, wegkm_imp/1.3). The full-composition donor
    # (data.hts.entd.filtered) carries routed_distance but NOT euclidean_distance
    # -- that column is only added by the reweighted stage, which we deliberately
    # bypass for the seed (it collapses households to one person). So derive it
    # here. Downstream (commute_distance, secondary distance distributions) needs it.
    if "euclidean_distance" not in trips.columns:
        if "routed_distance" not in trips.columns:
            raise ValueError(
                "[EntdSource.build_trips] donor trips have neither "
                "'euclidean_distance' nor 'routed_distance'; cannot derive the "
                "Euclidean trip distance the downstream stages require."
            )
        trips["euclidean_distance"] = trips["routed_distance"] / ENTD_DETOUR_FACTOR

    # Build final column order: CONTRACT first, then euclidean_distance + extras.
    extras_ordered = [
        c for c in ("euclidean_distance",)
        if c in trips.columns
    ]
    remaining = [
        c for c in trips.columns
        if c not in CONTRACT and c not in extras_ordered
    ]
    return trips[CONTRACT + extras_ordered + remaining]
