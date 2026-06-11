"""EntdSource: the ENTD donor-source adapter (popsim_open Phase 2).

The French ENTD (Enquete Nationale Transports et Deplacements) is an open
household travel survey.  After eqasim's ``data.hts.entd.cleaned`` stage the
persons / households / trips tables already carry canonical eqasim schema
column names (``person_id``, ``household_id``, ``age``, ``sex``, ``employed``,
``has_license``, ``has_pt_subscription``, ``mode``, ``following_purpose``, …),
so the attribute-mapping step is mostly direct column copies with a small
number of derivations.

Differences from MiD (design notes)
--------------------------------------
- **No pseudonymisation**: ENTD is open data, so ``source_person_id`` and
  ``source_household_id`` are set directly to the canonical ENTD integer ids
  (no sequential surrogate mapping is needed).
- **No day-of-week filter**: ``ENTD_SEED_COLUMNS.day_filter_col = None``.
  The cleaned ENTD stage already retains only reference-day weekday trips.
- **No ticket-type field**: ENTD does not record *which* PT ticket type a
  subscriber holds.  ``pt_subscription_type`` is therefore defaulted:
  subscribers -> ``"wochen_monat_ohne_abo"`` (a flatrate category, consistent
  with ``has_pt_subscription = True``); non-subscribers -> ``"fahre_nie"``.
  Both values are in ``PT_TICKET_CATEGORIES`` (CLAUDE.md MANDATORY guard).
- **income_class -> household_income label**: ENTD income_class is a 0..13
  integer band (French survey bands; see ``data/hts/entd/cleaned.py``
  ``INCOME_CLASS_BOUNDS``).  The mapping to eqasim/MiD categorical labels is an
  APPROXIMATION because the ENTD bands are defined for French incomes (euros)
  and the MiD bands are German.  The band boundaries are similar but not
  identical.  The mapping is:

  ENTD class | ENTD EUR range     | MiD label
  -----------|--------------------|------------------
   0         | <400               | under_500
   1         | 400-600            | under_500
   2         | 600-800            | 500_900
   3         | 800-1000           | 900_1500
   4         | 1000-1200          | 900_1500
   5         | 1200-1500          | 900_1500
   6         | 1500-1800          | 1500_2000
   7         | 1800-2000          | 1500_2000
   8         | 2000-2500          | 2000_2600
   9         | 2500-3000          | 2600_3000
  10         | 3000-4000          | 3000_3600
  11         | 4000-6000          | 4000_4600
  12         | 6000-10000         | 6000_6600
  13         | >=10000            | over_7000

  ``high_income = income_class >= 13`` (top ENTD band, >= 10000 EUR/mo).

- **Trips join**: ENTD trips are already in canonical eqasim schema
  (mode/purpose/time columns all present from ``data.hts.entd.cleaned``), so
  no mode or purpose re-mapping is needed.  ``euclidean_distance`` is carried
  directly from the ENTD table (cleaned.py derives it from routed_distance).
  The trip join is keyed by the ENTD ``person_id`` (= ``source_person_id`` on
  the synthetic persons frame after ``map_person_attributes``).

Load strategy
-------------
``load_donor`` accepts an optional ``(households, persons, trips)`` triple
injected by the popsim_open stage (when the real ENTD synpp stage has already
run and produced the cleaned frames).  If the frames are not injected a
``data_dir`` path is expected containing ``entd_households.parquet``,
``entd_persons.parquet``, ``entd_trips.parquet`` (pre-exported by the popsim_open
pipeline; see the popsim_open stage for the export step).  Tests always inject
the frames directly and never touch the filesystem.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES
from braunschweig.popsim.assembly import (
    _AGE_RANGE_BINS,
    _AGE_RANGE_LABELS,
    _household_availability,
    ADULT_AGE,
)
from braunschweig.popsim.attributes import (
    derive_car_availability,
    derive_bicycle_availability,
    INCOME_CLASS_BY_GROUP,
)
from braunschweig.popsim import income as _income_module
from braunschweig.popsim.seed import (
    CompletenessReport,
    SeedColumns,
    filter_complete_households,
    select_seed_columns,
)
from braunschweig.popsim.stratum import cell_urban_class_from_rs7, entd_urban_class
from braunschweig.popsim.trips_stage import CONTRACT, apply_per_person_jitter

# Legacy EUR-class -> 5-class economic status map (the status_from_hhtype=False
# fallback semantics). No circular import: braunschweig.synthesis never imports
# from braunschweig.popsim (verified), and this package already imports from the
# shared synthesis tree (synthesis.population.matched below).
from braunschweig.synthesis.population.enriched import ECONOMIC_STATUS_BY_INCOME_CLASS

# Reusing the legacy statistical-matching machinery from the upstream-shared
# synthesis tree is established practice in this package (see
# braunschweig.popsim.stratum, which imports URBAN_TYPE_TO_URBAN_CLASS from the
# same module, and braunschweig.popsim.trips, which imports data.hts.hts).
from synthesis.population.matched import household_size_class, match_donors

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed column mapping for ENTD (canonical column names from cleaned.py)
# ---------------------------------------------------------------------------

# ENTD_SEED_COLUMNS describes the ENTD canonical column names BEFORE build_seed
# transforms them.  This is used in tests and by seed_columns().
# Note: build_seed() RENAMES these to MiD names (H_ID, H_GEW, HP_ALTER, HP_SEX,
# P_GEW, P_ID) so the produced seed frames carry MiD-schema column names.
# filter_seed_to_stratum (mid.py) and expand_to_persons (expand.py) operate on
# the post-build_seed frames, so they need the MiD schema names.
# ENTD_BUILT_SEED_COLUMNS describes those post-build_seed MiD-schema names.
ENTD_SEED_COLUMNS = SeedColumns(
    household_id="household_id",
    household_weight="household_weight",
    person_household_id="household_id",
    person_id="person_id",
    person_weight="person_weight",
    age="age",
    sex="sex",
    # ENTD has no day-of-week completeness filter: cleaned.py already retains
    # only reference-day weekday trips.  Every household is always complete.
    day_filter_col=None,
    day_filter_values=None,
)

# ENTD_BUILT_SEED_COLUMNS describes the column schema of the seed frames
# produced by EntdSource.build_seed().  build_seed() renames ENTD canonical
# column names to the MiD seed names (H_ID, H_GEW, P_ID, P_GEW, HP_ALTER,
# HP_SEX), so the downstream PopulationSim orchestration (expand_to_persons,
# filter_seed_to_stratum) can use the same MiD-centric code for both sources.
# This constant is used by filter_seed_to_stratum (mid.py) via the
# EntdSource.built_seed_columns() method to discover the correct join column
# names on the post-build_seed frames.
ENTD_BUILT_SEED_COLUMNS = SeedColumns(
    household_id="H_ID",
    household_weight="H_GEW",
    person_household_id="H_ID",
    person_id="P_ID",
    person_weight="P_GEW",
    age="HP_ALTER",
    sex="HP_SEX",
    day_filter_col=None,
    day_filter_values=None,
)

# ---------------------------------------------------------------------------
# ENTD income_class (0..13) -> MiD categorical household_income label
#
# APPROXIMATION: ENTD bands are French survey bands; MiD bands are German.
# The EUR ranges are similar but not identical (see module docstring table).
# Classes -1 (missing/not reported in the raw ENTD) -> None (kept as NaN;
# downstream can override or leave as missing).
# ---------------------------------------------------------------------------
_ENTD_INCOME_CLASS_TO_LABEL: dict[int, Optional[str]] = {
    -1: None,             # missing code in ENTD cleaned.py
    0:  "under_500",      # <400 EUR -> closest MiD class: under_500
    1:  "under_500",      # 400-600  -> under_500 (ENTD splits MiD's first band)
    2:  "500_900",        # 600-800  -> 500_900
    3:  "900_1500",       # 800-1000 -> 900_1500
    4:  "900_1500",       # 1000-1200-> 900_1500
    5:  "900_1500",       # 1200-1500-> 900_1500 (ENTD class ends at 1500, MiD at 1500)
    6:  "1500_2000",      # 1500-1800-> 1500_2000
    7:  "1500_2000",      # 1800-2000-> 1500_2000
    8:  "2000_2600",      # 2000-2500-> 2000_2600
    9:  "2600_3000",      # 2500-3000-> 2600_3000
    10: "3000_3600",      # 3000-4000-> 3000_3600 (MiD splits this further, use lower)
    11: "4000_4600",      # 4000-6000-> 4000_4600 (wide ENTD band; use lower bound)
    12: "6000_6600",      # 6000-10000-> 6000_6600 (MiD splits; use lower bound)
    13: "over_7000",      # >=10000  -> over_7000
}

# Validate that every non-None mapped label is in INCOME_CLASS_BY_GROUP.
_valid_income_labels = set(INCOME_CLASS_BY_GROUP.values())
for _cls, _label in _ENTD_INCOME_CLASS_TO_LABEL.items():
    if _label is not None and _label not in _valid_income_labels:
        raise AssertionError(
            f"[EntdSource] ENTD income class {_cls} -> {_label!r} is not in "
            f"INCOME_CLASS_BY_GROUP. Fix _ENTD_INCOME_CLASS_TO_LABEL."
        )

# ---------------------------------------------------------------------------
# Economic status bridge: MiD income label -> legacy H4 EUR-class key
#
# APPROXIMATION: ENTD has no native economic-status field, so popsim_open derives
# ``economic_status`` from the (ENTD -> MiD-mapped) categorical ``household_income``
# via the legacy inverse map ECONOMIC_STATUS_BY_INCOME_CLASS (exactly the
# ``status_from_hhtype=False`` fallback semantics; the MiD Bayes hhtype x region
# machinery is NOT applied). The legacy map is keyed by the five H4 quintile
# EUR-class labels ("0-500", "1500-2000", "2600-3000", "3600-4500", "5000+"),
# while the ENTD mapper emits the MiD 15-class labels (INCOME_CLASS_BY_GROUP),
# so an explicit bridge is required. Each MiD label is assigned to the H4
# quintile class whose representative EUR band contains (or is nearest to) the
# MiD band: very_low <900, low 900-2000, medium 2000-3600, high 3600-5000,
# very_high >=5000. The partition is monotone in EUR and contains each H4
# representative band inside its assigned group. Unmapped non-NaN labels raise
# (vocabulary-drift guard, no silent NaN).
# ---------------------------------------------------------------------------
_H4_INCOME_CLASS_BY_MID_LABEL: dict[str, str] = {
    "under_500": "0-500",       # very_low
    "500_900":   "0-500",       # very_low
    "900_1500":  "1500-2000",   # low
    "1500_2000": "1500-2000",   # low
    "2000_2600": "2600-3000",   # medium
    "2600_3000": "2600-3000",   # medium
    "3000_3600": "2600-3000",   # medium
    "3600_4000": "3600-4500",   # high
    "4000_4600": "3600-4500",   # high
    "4600_5000": "3600-4500",   # high
    "5000_5600": "5000+",       # very_high
    "5600_6000": "5000+",       # very_high
    "6000_6600": "5000+",       # very_high
    "6600_7000": "5000+",       # very_high
    "over_7000": "5000+",       # very_high
}

# Import-time vocabulary-drift guards: the bridge must cover EVERY MiD income
# label and every bridge target must be a legacy H4 EUR-class key.
for _label in _valid_income_labels:
    if _label not in _H4_INCOME_CLASS_BY_MID_LABEL:
        raise AssertionError(
            f"[EntdSource] MiD income label {_label!r} (INCOME_CLASS_BY_GROUP) is "
            f"missing from _H4_INCOME_CLASS_BY_MID_LABEL. Extend the bridge."
        )
for _label, _h4_class in _H4_INCOME_CLASS_BY_MID_LABEL.items():
    if _h4_class not in ECONOMIC_STATUS_BY_INCOME_CLASS:
        raise AssertionError(
            f"[EntdSource] bridge target {_h4_class!r} (for MiD label {_label!r}) is "
            f"not a key of ECONOMIC_STATUS_BY_INCOME_CLASS. Fix _H4_INCOME_CLASS_BY_MID_LABEL."
        )

# high_income threshold: income_class >= 13 (>=10000 EUR/mo, the top ENTD band).
# This is the ENTD equivalent of the MiD "over_7000" class which sets high_income.
ENTD_HIGH_INCOME_CLASS = 13

# Detour factor converting a routed (network) distance to a straight-line
# (Euclidean) distance. Matches data/hts/entd/reweighted.py:28
# (euclidean_distance = routed_distance / 1.3) and the MiD path (wegkm_imp / 1.3).
# Canonical project-wide constant (braunschweig.constants); alias kept.
from braunschweig.constants import ROUTED_DETOUR_FACTOR as ENTD_DETOUR_FACTOR

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

# PT ticket defaults (ENTD has no ticket-type field).
# Subscribers -> a representative flatrate category (must be in PT_TICKET_FLATRATE
# AND PT_TICKET_CATEGORIES). Non-subscribers -> never-uses.
_PT_TYPE_SUBSCRIBER = "wochen_monat_ohne_abo"
_PT_TYPE_NONE = "fahre_nie"

# Validate both constants at import time.
_pt_cats = set(PT_TICKET_CATEGORIES)
assert _PT_TYPE_SUBSCRIBER in _pt_cats, (
    f"[EntdSource] _PT_TYPE_SUBSCRIBER={_PT_TYPE_SUBSCRIBER!r} not in PT_TICKET_CATEGORIES"
)
assert _PT_TYPE_NONE in _pt_cats, (
    f"[EntdSource] _PT_TYPE_NONE={_PT_TYPE_NONE!r} not in PT_TICKET_CATEGORIES"
)

# ENTD person columns that are copied directly to the output (no transformation).
_DIRECT_PERSON_COLS = [
    "age", "sex", "employed", "studies",
    "has_license", "has_pt_subscription",
    "socioprofessional_class",
]

# ENTD household columns that are joined per person.
# ``urban_type`` is included here (Phase 4A plumbing) so that Phase 4B
# donor stratification can use the ENTD household's UU2010 urban/rural class
# as a matching key, comparable with the MiD-side RegioStaR-7 class.
_HH_JOIN_COLS = [
    "household_id", "household_size",
    "number_of_cars", "number_of_bicycles",
    "income_class",
    "urban_type",   # Phase 4A: UU2010 urban/rural class for donor stratification
]


class EntdSource:
    """Donor-source adapter for the ENTD (Enquete Nationale Transports et Deplacements).

    ENTD is the French national household travel survey, available as open data.
    After eqasim's ``data.hts.entd.cleaned`` stage the tables already carry
    canonical eqasim column names, so this adapter is mostly a direct pass-through
    with three derivations (car_availability, bicycle_availability, age_range) and
    two defaults (pt_subscription_type, household_income from income_class).

    No pseudonymisation is applied because ENTD is open data; the raw
    ``person_id`` / ``household_id`` are used directly as provenance ids.
    """

    name: str = "entd"

    def seed_columns(self) -> SeedColumns:
        """Return the ENTD seed column mapping.

        ENTD columns are already in eqasim canonical names; no day-of-week
        completeness filter is needed.
        """
        return ENTD_SEED_COLUMNS

    def built_seed_columns(self) -> SeedColumns:
        """Return the column schema of the seed frames produced by :meth:`build_seed`.

        :meth:`build_seed` renames ENTD canonical column names to the MiD seed
        names (``H_ID``, ``H_GEW``, ``P_ID``, ``P_GEW``, ``HP_ALTER``,
        ``HP_SEX``).  Downstream code that operates on the POST-build_seed frames
        (e.g. :func:`braunschweig.popsim.mid.filter_seed_to_stratum`) must use
        these MiD-schema column names to discover the household join key, not the
        ENTD canonical names from :meth:`seed_columns`.

        Returns
        -------
        SeedColumns
            :data:`ENTD_BUILT_SEED_COLUMNS` (``household_id="H_ID"``, etc.).
        """
        return ENTD_BUILT_SEED_COLUMNS

    def build_seed(
        self,
        households: pd.DataFrame,
        persons: pd.DataFrame,
    ) -> tuple:
        """Build a PopulationSim seed in MiD control schema from ENTD donor frames.

        The PopulationSim control spec (popsimprep/_prep3_controls.csv) and the
        downstream expand/map_demographics pipeline all expect MiD column names:
        ``H_ID``, ``H_GEW``, ``P_ID``, ``HP_ID``, ``P_GEW``, ``HP_ALTER``,
        ``HP_SEX`` (1=male, 2=female), ``STAAT``.  This method transforms the ENTD
        canonical column names to that schema once, at the stage boundary, so the
        entire proven downstream (seed build, expand, map_demographics) runs
        unchanged.

        The ENTD person attributes (``employed``, ``has_license``,
        ``has_pt_subscription``, ``socioprofessional_class``, ``urban_type``, …)
        are retained on the transformed persons frame so that
        :meth:`map_person_attributes` can access them after expand.

        Design constraints
        ------------------
        - ``HP_SEX`` must be 1 (male) or 2 (female); any unmapped value raises
          :class:`ValueError` immediately (fail-fast guard).
        - ``HP_ID``: unique per-person integer derived as
          ``household_id * _HP_ID_SCALE + person_id`` (where ``_HP_ID_SCALE``
          is 10^ceil(log10(max(person_id)+1)) rounded up to the next power of 10
          to avoid collisions across households).  If a collision is detected
          ``np.arange`` fallback sequential ids are used and a warning is logged
          (per-run; very large surveys could overflow int64 for this formula, but
          the ENTD donor has ~14 000 persons so the scale is safe).
        - Every ENTD household is considered "complete" (no day-of-week filter;
          ``ENTD_SEED_COLUMNS.day_filter_col = None``), so
          :func:`braunschweig.popsim.seed.filter_complete_households` is called
          with the no-op path (drop rate 0 %, completeness_rate 1.0).
        - :func:`braunschweig.popsim.seed.select_seed_columns` is then called to
          add ``STAAT = 1`` and keep only the columns the control spec needs (plus
          the ENTD attribute extras retained for :meth:`map_person_attributes`).

        Parameters
        ----------
        households:
            ENTD household frame from :meth:`load_donor` or injected by the stage.
            Must carry ``household_id``, ``household_weight``, and ``urban_type``
            (Phase 4A donor stratification key).
        persons:
            ENTD person frame from :meth:`load_donor` or injected by the stage.
            Must carry ``household_id`` (foreign key), ``person_id``, ``person_weight``,
            ``age``, and ``sex`` (``"male"``/``"female"``).

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame, CompletenessReport]
            ``(seed_households, seed_persons, report)`` where:

            - ``seed_households`` has columns: ``H_ID``, ``H_GEW``, ``urban_type``,
              ``STAAT``.
            - ``seed_persons`` has columns: ``H_ID``, ``P_ID``, ``HP_ID``,
              ``P_GEW``, ``HP_ALTER``, ``HP_SEX``, ``STAAT``,
              plus all ENTD attribute columns retained for downstream mapping
              (``employed``, ``studies``, ``has_license``, ``has_pt_subscription``,
              ``socioprofessional_class``, and any other columns present).
            - ``report`` is a :class:`braunschweig.popsim.seed.CompletenessReport`
              with ``completeness_rate = 1.0`` (no day filter for ENTD).

        Raises
        ------
        ValueError
            If ``sex`` contains values other than ``"male"`` or ``"female"``
            (fail-fast: an unmapped sex value would silently produce NaN in
            ``HP_SEX``, breaking the PopulationSim sex-margin controls).
        KeyError
            If required columns are absent from either frame.
        """
        _require_columns(households, ["household_id", "household_weight"], table_name="ENTD households")
        _require_columns(
            persons,
            ["household_id", "person_id", "person_weight", "age", "sex"],
            table_name="ENTD persons",
        )

        # Defensive guard (no-silent-fallback): the PopulationSim seed must carry the
        # FULL household composition. If the donor is accidentally the eqasim
        # person-matching frame (data.hts.selected -> data.hts.entd.reweighted keeps
        # ~1 person/household for IPF matching), the synthetic households would all
        # be 1-person. Warn loudly on a near-1 persons/household mean so the wiring
        # mistake is observable instead of producing a silently wrong population.
        n_hh_in = households["household_id"].nunique()
        mean_pph = len(persons) / max(n_hh_in, 1)
        if mean_pph < 1.2:
            logger.warning(
                "[EntdSource.build_seed] donor has only %.2f persons/household "
                "(%d persons / %d households) -- this looks like the reweighted "
                "person-matching frame, NOT the full composition. The PopulationSim "
                "seed must come from data.hts.entd.filtered (multi-person households), "
                "or every synthetic household will have exactly one person.",
                mean_pph, len(persons), n_hh_in,
            )

        # --- Validate and map sex -> HP_SEX (1=male, 2=female) ----------------
        # This is a fail-fast guard: an unmapped value would silently produce NaN
        # in HP_SEX and break the PopulationSim sex-margin controls.
        sex_map = {"male": 1, "female": 2}
        unmapped_sex = set(persons["sex"].unique()) - set(sex_map)
        if unmapped_sex:
            raise ValueError(
                f"[EntdSource.build_seed] persons 'sex' column contains unmapped "
                f"value(s) {unmapped_sex!r}. Only 'male' and 'female' are accepted. "
                f"Fix the ENTD person frame before building the PopulationSim seed."
            )

        # --- Rename household columns to MiD seed schema ----------------------
        hh_seed = households.copy()
        hh_seed = hh_seed.rename(columns={
            "household_id": "H_ID",
            "household_weight": "H_GEW",
        })

        # --- Rename person columns to MiD seed schema (retain ENTD attrs) -----
        p_seed = persons.copy()
        p_seed = p_seed.rename(columns={
            "household_id": "H_ID",
            "person_id": "P_ID",
            "person_weight": "P_GEW",
            "age": "HP_ALTER",
        })
        p_seed["HP_SEX"] = p_seed["sex"].map(sex_map)
        # Retain original sex string for downstream map_demographics (eqasim uses
        # the "sex" column; expand.map_demographics re-derives it from HP_SEX).
        # HP_SEX is the PopulationSim-visible column; sex stays as an extra.

        # --- Build HP_ID: unique integer per person ---------------------------
        # HP_ID is the PopulationSim person id (must be a unique integer).
        # Formula: H_ID * scale + P_ID (avoids collisions within each household's
        # P_ID range when household ids are large ENTD integers).
        # Scale = smallest power of 10 > max(P_ID), so ids don't overlap across
        # households.  The ENTD donor (~14k persons) is small; overflow is impossible.
        max_pid = int(p_seed["P_ID"].max()) if len(p_seed) > 0 else 1
        scale = 1
        while scale <= max_pid:
            scale *= 10
        hp_id_candidate = p_seed["H_ID"].astype(np.int64) * scale + p_seed["P_ID"].astype(np.int64)
        if hp_id_candidate.duplicated().any():
            logger.warning(
                "[EntdSource.build_seed] HP_ID formula H_ID*%d+P_ID produced "
                "%d duplicate(s); falling back to sequential arange(1..n).",
                scale,
                int(hp_id_candidate.duplicated().sum()),
            )
            p_seed["HP_ID"] = np.arange(1, len(p_seed) + 1, dtype=np.int64)
        else:
            p_seed["HP_ID"] = hp_id_candidate

        # --- Apply completeness filter (no-op: ENTD has no day-of-week filter) -
        # Using the standard filter_complete_households with the ENTD column mapping
        # (day_filter_col=None -> every household is "complete").  This produces a
        # CompletenessReport with completeness_rate=1.0 and drop_rate=0.0.
        # We use a temporary SeedColumns with H_ID/H_GEW/P_ID/P_GEW/HP_ALTER/HP_SEX
        # column names (the renamed frame) so the filter runs correctly.
        _mid_like_cols = SeedColumns(
            household_id="H_ID",
            household_weight="H_GEW",
            person_household_id="H_ID",
            person_id="P_ID",
            person_weight="P_GEW",
            age="HP_ALTER",
            sex="HP_SEX",
            day_filter_col=None,
            day_filter_values=None,
        )
        hh_seed, p_seed, report = filter_complete_households(
            hh_seed, p_seed, _mid_like_cols, day_filter_values=None
        )

        # --- select_seed_columns: add STAAT=1, keep essentials + extras -------
        # Extra household columns: urban_type (Phase 4B donor stratification).
        # Extra person columns: all ENTD attribute columns present on the renamed
        # frame (minus the essentials already selected, minus HP_SEX which is added
        # separately in the extra_person_cols so it stays).
        # We retain all ENTD-origin columns so map_person_attributes can use them
        # directly without another join -- this is the key design decision.
        hh_extra = [c for c in ("urban_type", "RegioStaR7") if c in hh_seed.columns]
        # Person extras: HP_ID + every ENTD attribute column (after rename, these
        # include employed, studies, has_license, has_pt_subscription,
        # socioprofessional_class, sex (original string), number_of_trips,
        # trip_weight, departement_id, and anything else the ENTD cleaned stage
        # produces).  Essentials are H_ID, P_ID, P_GEW, HP_ALTER, HP_SEX.
        essential_person_cols = {"H_ID", "P_ID", "P_GEW", "HP_ALTER", "HP_SEX"}
        p_extra = ["HP_ID"] + [
            c for c in p_seed.columns
            if c not in essential_person_cols and c not in ("HP_ID", "STAAT")
        ]

        hh_seed, p_seed = select_seed_columns(
            hh_seed, p_seed, _mid_like_cols,
            extra_household_cols=hh_extra,
            extra_person_cols=p_extra,
        )

        logger.info(
            "[EntdSource.build_seed] seed built: %d households, %d persons "
            "(completeness_rate=%.3f). "
            "Person columns: %s.",
            len(hh_seed), len(p_seed), report.completeness_rate,
            list(p_seed.columns),
        )
        return hh_seed, p_seed, report

    def load_donor(
        self,
        data_dir: Union[str, Path],
        *,
        injected: Optional[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load (households, persons, trips) from ENTD parquet files or injected frames.

        Parameters
        ----------
        data_dir:
            Directory containing ``entd_households.parquet``, ``entd_persons.parquet``,
            and ``entd_trips.parquet``.  Ignored when ``injected`` is provided.
        injected:
            Optional pre-loaded ``(households, persons, trips)`` tuple.  When provided
            the filesystem is not accessed.  The popsim_open stage injects the frames
            directly (they are produced by the upstream ``data.hts.entd.cleaned``
            synpp stage).  Tests always use injection.

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
            ``(households, persons, trips)`` donor tables.

        Raises
        ------
        FileNotFoundError
            If a parquet file is absent (only when not injected).
        """
        if injected is not None:
            households, persons, trips = injected
            persons = entd_persons_to_donor_schema(persons)
            logger.info(
                "[EntdSource] using injected donor frames: "
                "%d households, %d persons, %d trips (persons mapped to donor schema "
                "H_ID/P_ID/HP_ALTER/HP_SEX)",
                len(households), len(persons), len(trips),
            )
            return households, persons, trips

        data_dir = Path(data_dir)
        hh_path = data_dir / "entd_households.parquet"
        p_path = data_dir / "entd_persons.parquet"
        t_path = data_dir / "entd_trips.parquet"
        for path in (hh_path, p_path, t_path):
            if not path.is_file():
                raise FileNotFoundError(
                    f"[EntdSource] ENTD parquet file not found: {path}. "
                    "Run the popsim_open export step or pass injected frames."
                )
        households = pd.read_parquet(hh_path)
        persons = pd.read_parquet(p_path)
        trips = pd.read_parquet(t_path)
        persons = entd_persons_to_donor_schema(persons)
        logger.info(
            "[EntdSource] loaded donor: %d households, %d persons, %d trips from %s "
            "(persons mapped to donor schema H_ID/P_ID/HP_ALTER/HP_SEX)",
            len(households), len(persons), len(trips), data_dir,
        )
        return households, persons, trips

    def map_person_attributes(
        self,
        persons: pd.DataFrame,
        households: pd.DataFrame,
        *,
        rng=None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Map ENTD canonical columns to the eqasim synthesis schema.

        This mapper is called by :func:`braunschweig.popsim.assembly.build_persons`
        AFTER :func:`braunschweig.popsim.expand.expand_to_persons` and
        :func:`braunschweig.popsim.expand.map_demographics` have been applied.

        After ``build_seed`` transforms the ENTD frames to MiD schema and
        ``expand_to_persons`` joins on ``H_ID``, the expanded persons frame carries
        ``H_ID`` as the donor household key (the same integer that was passed to
        PopulationSim as the seed household id).  The ``household_id`` column at
        this point is the SYNTHETIC id (``<cell>_<H_ID>_<occurrence>``), not the
        ENTD donor household id.

        The ENTD household join therefore uses ``H_ID`` as the join key on both
        sides.  The ``_HH_JOIN_COLS`` list maps ``household_id -> H_ID`` via a
        rename so the merge key is unambiguous.

        Parameters
        ----------
        persons:
            Expanded persons frame after ``expand_to_persons`` + ``map_demographics``
            + ``derive_zone_ids`` (i.e. the frame produced inside
            :func:`braunschweig.popsim.assembly.build_persons`).  Carries ``H_ID``
            (donor household key, populated by ``build_seed`` rename) and ``P_ID``
            (donor person key); also carries the ENTD attribute columns retained
            by ``build_seed.select_seed_columns`` (``employed``, ``studies``,
            ``has_license``, ``has_pt_subscription``, ``socioprofessional_class``,
            etc.).
        households:
            ENTD household frame from ``load_donor`` (original ENTD canonical
            schema: ``household_id``, ``household_size``, ``number_of_cars``,
            ``number_of_bicycles``, ``income_class``).
        rng:
            Not used for ENTD (all attributes are directly available); accepted
            for interface compatibility with ``PopsimSource.map_person_attributes``.

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame]
            ``(persons, pseudonym_map)`` per the unified mapper contract.
            ``persons`` is the frame extended with all required schema columns
            except ``is_urban_resident`` (added later by ``build_persons`` from
            the home commune).  ``pseudonym_map`` is EMPTY (columns
            ``[source_person_id, source_household_id, H_ID, P_ID]``) because
            ENTD is open data and no surrogate ids are assigned; it is explicit
            so ``build_persons`` can enforce the tuple contract instead of
            silently substituting an empty map.

        Notes
        -----
        - ``source_person_id`` is set to ``P_ID`` (the ENTD donor person integer id).
        - ``source_household_id`` is set to ``H_ID`` (the ENTD donor household integer id).
        - Both are raw ENTD ids (open data, no pseudonymisation).
        - ``weight = 1.0`` (the popsim_open frame is already expanded).
        - ``household_income_eur`` is set to the raw ENTD income-class midpoint.
          The INKAR per-Kreis scaling is applied by ``build_persons`` AFTER this
          mapper returns, so all sources use the same shared scaling step.
        - ``high_income`` is a placeholder (set to False here); ``build_persons``
          overwrites it with ``household_income_eur >= 5000 EUR`` after INKAR scaling.
        - ``economic_status`` is an APPROXIMATION: ENTD has no native status
          field, so it is derived from the categorical ``household_income`` via
          ``_H4_INCOME_CLASS_BY_MID_LABEL`` + ``ECONOMIC_STATUS_BY_INCOME_CLASS``
          (legacy ``status_from_hhtype=False`` semantics); NaN income -> NaN status.
        """
        out = persons.copy()

        # --- Determine the donor household join key ----------------------------
        # After build_seed + expand_to_persons the donor household key is "H_ID"
        # (the ENTD household_id renamed to the MiD seed schema name).  The
        # synthetic household_id is a different column ("ZENSUS100m_H_ID_occ").
        # The ENTD households frame still carries the original "household_id" name,
        # so we must translate: join expanded persons.H_ID onto households.household_id.
        if "H_ID" in out.columns:
            # Post-build_seed path: H_ID is the donor key (populated by rename in build_seed).
            persons_donor_key = "H_ID"
            hh_donor_key = "household_id"
        else:
            # Fallback: direct ENTD injection without build_seed (e.g. direct test call
            # where persons still carry household_id as the ENTD canonical name).
            # This path is only used by tests that call map_person_attributes directly
            # with the raw ENTD persons frame (pre-expand), not the post-expand frame.
            persons_donor_key = "household_id"
            hh_donor_key = "household_id"

        # --- Direct copy: columns already canonical in ENTD cleaned output ---
        # Verify required direct columns are present (fail-fast).
        _require_columns(out, _DIRECT_PERSON_COLS, table_name="ENTD persons")
        _require_columns(households, _HH_JOIN_COLS, table_name="ENTD households")

        # --- Join household attributes onto persons ---
        hh_attrs = households[_HH_JOIN_COLS].copy()
        # Rename the household join key on the households side to a neutral name
        # to avoid column name collisions on the output frame.
        hh_attrs = hh_attrs.rename(columns={hh_donor_key: "_hh_join_key"})
        out = out.merge(
            hh_attrs,
            left_on=persons_donor_key,
            right_on="_hh_join_key",
            how="left",
            suffixes=("", "_hh"),
        ).drop(columns=["_hh_join_key"])

        n_unmatched = int(out["household_size"].isna().sum())
        if n_unmatched > 0:
            logger.warning(
                "[EntdSource] map_person_attributes: %d/%d persons have no matching "
                "household after join (primary household_id merge failed). "
                "Check that persons.household_id keys exist in households.",
                n_unmatched, len(out),
            )

        out["number_of_cars"] = out["number_of_cars"].fillna(0).astype(int)
        out["number_of_bicycles"] = out["number_of_bicycles"].fillna(0).astype(int)
        out["household_size"] = out["household_size"].fillna(0).astype(int)

        # --- car_availability and bicycle_availability ---
        # car_availability: cars vs. adults (age >= 18) per household.
        out["car_availability"] = _household_availability(
            out, count_col="number_of_cars", adults_only=True,
            derive=derive_car_availability,
        )
        # bicycle_availability: bicycles vs. all household members.
        out["bicycle_availability"] = _household_availability(
            out, count_col="number_of_bicycles", adults_only=False,
            derive=derive_bicycle_availability,
        )

        # --- age_range ---
        # Matches assembly._AGE_RANGE_BINS / _AGE_RANGE_LABELS exactly so that
        # the ENTD and MiD workflows produce the same categorical age bands.
        out["age_range"] = pd.cut(
            out["age"],
            bins=_AGE_RANGE_BINS,
            labels=_AGE_RANGE_LABELS,
        )

        # --- household_income (categorical label from ENTD income_class) ---
        # Approximation: ENTD income bands are French-survey bounds; MiD bands
        # are German. The mapping is documented in the module docstring table.
        out["household_income"] = (
            out["income_class"].map(_ENTD_INCOME_CLASS_TO_LABEL)
        )
        n_income_missing = int(out["household_income"].isna().sum())
        if n_income_missing > 0:
            logger.info(
                "[EntdSource] map_person_attributes: %d/%d persons have "
                "household_income=NaN (income_class -1 or unmapped). "
                "Primary income mapping rate: %.1f%%.",
                n_income_missing, len(out),
                100.0 * (len(out) - n_income_missing) / max(len(out), 1),
            )

        # --- economic_status (APPROXIMATION: derived from the income class) ---
        # ENTD has no native economic-status field. Derive the 5-class BMDV
        # status from the MiD-mapped household_income label via the bridge +
        # the legacy inverse map (status_from_hhtype=False fallback semantics;
        # the MiD Bayes hhtype x region machinery is NOT applied). NaN income
        # (ENTD income_class -1) stays NaN -- economic_status is schema-optional
        # and a missing income must not invent a status.
        status = (
            out["household_income"]
            .map(_H4_INCOME_CLASS_BY_MID_LABEL)
            .map(ECONOMIC_STATUS_BY_INCOME_CLASS)
        )
        unmapped_income = out["household_income"].notna() & status.isna()
        if unmapped_income.any():
            unmapped_labels = sorted(out.loc[unmapped_income, "household_income"].unique())
            raise ValueError(
                f"[EntdSource] map_person_attributes: household_income label(s) "
                f"{unmapped_labels!r} have no economic_status mapping "
                f"(_H4_INCOME_CLASS_BY_MID_LABEL / ECONOMIC_STATUS_BY_INCOME_CLASS). "
                f"The income vocabulary drifted; extend the bridge instead of "
                f"silently producing NaN."
            )
        out["economic_status"] = status
        n_status_missing = int(status.isna().sum())
        logger.info(
            "[EntdSource] map_person_attributes: economic_status derived from the "
            "income class (APPROXIMATION, no native ENTD status) for %d/%d persons "
            "(%.1f%%); %d (%.1f%%) stay NaN (missing ENTD income).",
            len(out) - n_status_missing, len(out),
            100.0 * (len(out) - n_status_missing) / max(len(out), 1),
            n_status_missing,
            100.0 * n_status_missing / max(len(out), 1),
        )

        # --- household_income_eur (raw ENTD midpoint; INKAR scaling applied later) ---
        # Set household_income_eur to the raw ENTD income-class midpoint.
        # build_persons applies INKAR per-Kreis scaling AFTER this mapper returns
        # (for all sources), so this value is overwritten there with
        # midpoint * INKAR_scale[Kreis].  The high_income flag is also set by
        # build_persons to the unified rule (eur >= 5000 EUR).
        out["household_income_eur"] = pd.to_numeric(
            out["income_class"].map(_income_module.ENTD_INCOME_CLASS_MIDPOINT_EUR),
            errors="coerce",
        )

        # --- high_income (placeholder; overwritten by build_persons after INKAR) ---
        # Set a placeholder here so the column exists for schema validation.
        # build_persons overwrites this with household_income_eur >= 5000 after
        # the INKAR scaling step.
        out["high_income"] = out["income_class"] >= ENTD_HIGH_INCOME_CLASS

        # --- pt_subscription_type (default: no ticket-type field in ENTD) ---
        # Subscribers get a representative flatrate ticket type;
        # non-subscribers get "fahre_nie" (structurally absent from PT).
        # Use pd.Series then cast to pandas StringDtype (np.astype("string")
        # is not supported in older NumPy versions).
        out["pt_subscription_type"] = pd.Series(
            np.where(
                out["has_pt_subscription"].astype(bool),
                _PT_TYPE_SUBSCRIBER,
                _PT_TYPE_NONE,
            ),
            index=out.index,
        ).astype("string")

        # --- Provenance IDs (ENTD is open data, no pseudonymisation needed) ---
        # After build_seed + expand_to_persons:
        #   - H_ID  = ENTD donor household_id (renamed in build_seed)
        #   - P_ID  = ENTD donor person_id    (renamed in build_seed)
        # In the direct-test path (no build_seed), persons still carry the
        # original ENTD "person_id" and "household_id" column names.
        if "H_ID" in out.columns and "P_ID" in out.columns:
            out["source_person_id"] = out["P_ID"]
            out["source_household_id"] = out["H_ID"]
        else:
            # Fallback for tests that call map_person_attributes directly with
            # the raw ENTD persons frame (pre-expand, pre-build_seed).
            out["source_person_id"] = out["person_id"]
            out["source_household_id"] = out[persons_donor_key]

        # --- weight = 1.0 (popsim_open frame is already expanded, one row per person) ---
        out["weight"] = 1.0

        # --- household_size: number of persons in each SYNTHETIC household ----
        # Replicates map_mid_person_attributes: size is the count of persons
        # sharing the same synthetic household_id (set by assign_synthetic_household_ids).
        # Note: the "household_size" from the ENTD households table (donor HH size)
        # was joined above and may be present as "household_size" from the merge.
        # We OVERWRITE it with the synthetic household size because PopulationSim
        # may replicate one donor household multiple times, changing the effective
        # size.  The "household_size" column on the output must reflect the
        # synthetic household, not the original donor.
        if "household_id" in out.columns:
            out["household_size"] = (
                out.groupby("household_id")["person_id"].transform("size")
            )
        elif "household_size" not in out.columns:
            # If no household_id column (unusual path), keep the joined value if present.
            # Logged so the caller can investigate.
            logger.warning(
                "[EntdSource] map_person_attributes: 'household_id' not found in persons; "
                "household_size may reflect donor household size (not synthetic size)."
            )

        # --- is_urban_resident: True when person lives in Braunschweig city ----
        # Replicates map_mid_person_attributes exactly (see assembly.py line ~310):
        #   is_urban_resident = (departement_id == "03101")
        # ``departement_id`` is derived by derive_zone_ids (assembly.build_persons)
        # from the 12-digit ARS before the mapper is called.  For persons outside
        # the ZGB area the column is still present (derive_zone_ids is unconditional).
        _BS_KREIS5 = "03101"
        if "departement_id" in out.columns:
            out["is_urban_resident"] = out["departement_id"] == _BS_KREIS5
        else:
            # derive_zone_ids was not called (direct-test path without ARS column).
            # Set a placeholder so schema validation doesn't crash; callers that
            # need a correct value must ensure derive_zone_ids runs first.
            out["is_urban_resident"] = False
            logger.warning(
                "[EntdSource] map_person_attributes: 'departement_id' not found; "
                "is_urban_resident set to False (placeholder). "
                "Ensure derive_zone_ids ran before calling map_person_attributes."
            )

        logger.info(
            "[EntdSource] map_person_attributes: %d persons mapped. "
            "car_availability none/some/all: %s; "
            "pt_subscription_type subscriber/non: %d/%d.",
            len(out),
            dict(out["car_availability"].value_counts()),
            int(out["has_pt_subscription"].sum()),
            int((~out["has_pt_subscription"]).sum()),
        )

        # Unified mapper contract: return (persons, pseudonym_map). ENTD is open
        # data, so no surrogates are assigned and the map is empty -- but it is
        # returned EXPLICITLY so build_persons can enforce the tuple contract
        # (a pseudonymisation-required source can never silently lose its map).
        empty_pseudonym_map = pd.DataFrame(
            columns=["source_person_id", "source_household_id", "H_ID", "P_ID"]
        )
        return out, empty_pseudonym_map

    def donor_stratum(self, seed_households: pd.DataFrame) -> pd.Series:
        """Return the per-household stratum label for donor stratification.

        For ENTD, the stratum is the RegioStaR-2 binary label (``"urban"`` /
        ``"rural"``), derived from ``urban_type`` via
        :func:`braunschweig.popsim.stratum.entd_urban_class`.  ENTD donors do not
        carry a native RegioStaR-7 code; the UU2010 ``urban_type`` field is the
        closest available urbanity indicator and collapses cleanly to the RS2
        binary (see CLAUDE.md and ``braunschweig.popsim.stratum`` module docs).

        Parameters
        ----------
        seed_households:
            ENTD seed household frame.  Must carry ``urban_type`` (the UU2010
            category string, loaded into households via ``_HH_JOIN_COLS``).

        Returns
        -------
        pd.Series
            RS2 label string (``"urban"`` or ``"rural"``) per household row,
            same index as ``seed_households``.

        Raises
        ------
        KeyError
            If ``urban_type`` is absent from ``seed_households``.
        """
        return seed_households["urban_type"].map(entd_urban_class)

    def cell_stratum(self, cells: pd.DataFrame) -> pd.Series:
        """Return the per-100m-cell stratum label for donor stratification.

        For ENTD, the cell stratum is the RS2 binary label derived from the
        cell's ``RegioStaR7`` code via
        :func:`braunschweig.popsim.stratum.cell_urban_class_from_rs7`.  This
        gives the same label space as :meth:`donor_stratum` so cells and donors
        can be matched by a simple equality check.

        Parameters
        ----------
        cells:
            Cells frame.  Must carry ``RegioStaR7``.

        Returns
        -------
        pd.Series
            RS2 label string per cell row, same index as ``cells``.

        Raises
        ------
        KeyError
            If ``RegioStaR7`` is absent from ``cells``.
        ValueError
            If a RS7 code is outside 71-77 (delegates to
            :func:`braunschweig.data.bbsr.regiostar.regiostar2_label`).
        """
        return cells["RegioStaR7"].map(cell_urban_class_from_rs7)

    def build_trips(
        self,
        persons: pd.DataFrame,
        donor_trips: pd.DataFrame,
        *,
        random_seed: int,
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

        Returns
        -------
        pd.DataFrame
            One row per (synthetic person, donor trip), columns: the 11-column
            synthesis.population.trips CONTRACT + ``trip_index`` +
            ``euclidean_distance`` + remaining ENTD trip extras.
            Global ``trip_id`` is reassigned as a sequential integer.
        """
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_columns(df: pd.DataFrame, required: list, *, table_name: str) -> None:
    """Raise a clear ValueError if any required column is missing (fail-fast)."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"[EntdSource] {table_name} is missing required column(s) {missing}; "
            f"available: {list(df.columns)}."
        )


def entd_persons_to_donor_schema(persons: pd.DataFrame) -> pd.DataFrame:
    """Rename ENTD persons to the MiD donor demographic schema used by expand.

    The PopulationSim output (``combined``) carries ``H_ID`` -- the ENTD
    household_id that ``EntdSource.build_seed`` wrote as the seed household key.
    ``braunschweig.popsim.expand.expand_to_persons`` joins the donor persons onto
    that output by ``H_ID`` and ``expand.map_demographics`` reads ``HP_ALTER`` /
    ``HP_SEX``. So the DONOR persons that ``assembly.build_persons`` expands must
    use the same names (``H_ID``, ``P_ID``, ``HP_ALTER``, ``HP_SEX``), symmetric
    with ``MidSource.load_donor`` (whose MiD persons carry those names natively).
    All ENTD attribute columns (``employed``, ``has_license``, …) are retained so
    ``EntdSource.map_person_attributes`` can read them after expand.

    This is the donor-side counterpart of the seed transform in ``build_seed``;
    without it ``expand_to_persons`` raises ``KeyError: 'H_ID'`` because the raw
    ENTD donor still carries ``household_id`` / ``person_id`` / ``age`` / ``sex``.
    """
    out = persons.rename(columns={
        "household_id": "H_ID",
        "person_id": "P_ID",
        "age": "HP_ALTER",
    })
    sex_map = {"male": 1, "female": 2}
    unmapped = set(out["sex"].unique()) - set(sex_map)
    if unmapped:
        raise ValueError(
            f"[EntdSource] donor persons 'sex' has unmapped value(s) {unmapped!r}; "
            "only 'male'/'female' are accepted."
        )
    out["HP_SEX"] = out["sex"].map(sex_map)
    return out
