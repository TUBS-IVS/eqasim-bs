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

Module layout
-------------
This module is the facade for the ENTD donor source: ``EntdSource`` (the
public class) stays here, while sibling modules in this package hold helpers
extracted out of it (issue #267 split). Two different relationships exist
between this facade and its siblings, and only the first is a "re-export" in
the strict sense:

- **Re-exports** (``entd_vocabulary``, ``entd_schema``, ``entd_diary_matching``):
  these siblings hold constants and helpers that already had bare
  module-level names (or, for the chain-matching helpers, are themselves
  re-exported from ``braunschweig.popsim.chain_matching``). Every one of
  these names is imported here UNDER ITS ORIGINAL NAME (each import block
  marked ``# noqa: F401 (re-exports)``), so external imports of
  ``braunschweig.popsim.sources.entd`` keep resolving those exact names
  unchanged; ``check_namespace.py`` pins this.
- **Delegation targets** (``entd_seed``, ``entd_attributes``, ``entd_trips``,
  ``entd_donor``): these siblings hold the eight ``EntdSource`` method
  bodies (``seed_columns``, ``built_seed_columns``, ``build_seed``,
  ``map_person_attributes``, ``build_trips``, ``load_donor``,
  ``donor_stratum``, ``cell_stratum``). Those functions were ONLY ever
  class-method bodies before this split -- never bare module-level names --
  so there is nothing of theirs to re-export: each is imported here under a
  private leading-underscore alias (e.g. ``build_trips as _build_trips``)
  purely as the internal target of the matching ``EntdSource`` method's
  one-line delegation. ``EntdSource`` remains the sole public entry point
  for all eight; ``dir(entd)`` never exposed (and still does not expose) a
  bare ``build_trips``, ``map_person_attributes``, etc.

The submodules extracted so far:

    entd_vocabulary   seed column mapping (``ENTD_SEED_COLUMNS`` /
                      ``ENTD_BUILT_SEED_COLUMNS``), the income_class -> MiD
                      household_income label lookup and its H4
                      economic-status bridge, the high_income threshold, the
                      detour-factor alias, the PT-ticket defaults, and the
                      direct-copy / household-join column lists
    entd_schema       column-presence validation (``_require_columns``) and
                      the ENTD -> MiD donor demographic schema rename
                      (``entd_persons_to_donor_schema``)
    entd_diary_matching  diary-donor chain matching for trip-less synthetic
                      persons (``_derive_chain_matching_frame``,
                      ``_match_trip_less_persons_to_diary_donors``);
                      distinct from ``braunschweig.popsim.chain_matching``,
                      which only holds the shared age-bin edges and
                      minimum-observations helper (see that module's
                      docstring)
    entd_seed         the ENTD -> MiD PopulationSim seed-schema mapping
                      (``seed_columns``, ``built_seed_columns``,
                      ``build_seed``)
    entd_attributes   the ENTD -> eqasim synthesis-schema person-attribute
                      mapping (``map_person_attributes``, ~300 lines: the
                      largest ENTD mapper)
    entd_trips        the ENTD -> synthesis.population.trips CONTRACT frame
                      builder (``build_trips``, ~260 lines: direct donor-trip
                      join plus diary-donor chain matching for non-diary
                      persons)
    entd_donor        donor loading (``load_donor``) and the two donor
                      stratum-derivation helpers (``donor_stratum``,
                      ``cell_stratum``)

This completes the ENTD source split (issue #267): every EntdSource method
body has moved to a sibling; this facade now holds only the docstring,
imports (the re-export blocks and the delegation-alias import blocks
described above), and EntdSource as a thin delegating class.

Namespace-parity imports
-------------------------
A separate group of imports below (``numpy``; ``assembly``'s
``_AGE_RANGE_BINS``/``_AGE_RANGE_LABELS``/``_household_availability``;
``attributes``' ``derive_car_availability``/``derive_bicycle_availability``;
the ``income`` module alias; ``seed``'s ``CompletenessReport``/
``filter_complete_households``/``select_seed_columns``; ``stratum``'s
``cell_urban_class_from_rs7``/``entd_urban_class``; ``trips_stage``'s
``CONTRACT``/``apply_per_person_jitter``; and ``enriched``'s
``ECONOMIC_STATUS_BY_INCOME_CLASS``) are no longer CALLED directly in this
file -- their only call sites moved into whichever sibling now owns the
corresponding method body. They stay imported here (each marked
``# noqa: F401``) purely so ``dir(braunschweig.popsim.sources.entd)`` -- and
therefore anything introspecting or ``from``-importing these names off this
facade -- keeps seeing exactly the same module-level names as before the
split. Each import block below carries only a short pointer back to this
paragraph plus its own moved-to location; this paragraph is the one place
that states the rationale.

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

# np: moved to entd_trips.py (Task 5). Namespace-parity import, see docstring.
import numpy as np  # noqa: F401  (namespace parity, see above)
import pandas as pd

# Moved to entd_attributes.py (Task 4); namespace-parity import, see
# docstring. ADULT_AGE was already unused before this split.
from braunschweig.popsim.assembly import (  # noqa: F401  (namespace parity, see above)
    _AGE_RANGE_BINS,
    _AGE_RANGE_LABELS,
    _household_availability,
    ADULT_AGE,
)
# Moved to entd_attributes.py (Task 4); namespace-parity import, see docstring.
from braunschweig.popsim.attributes import (  # noqa: F401  (namespace parity, see above)
    derive_car_availability,
    derive_bicycle_availability,
)
# Moved to entd_attributes.py (Task 4); namespace-parity import, see docstring.
from braunschweig.popsim import income as _income_module  # noqa: F401  (namespace parity)
# SeedColumns is still used here as a method return-type annotation. The other
# three names moved to entd_seed.py (Task 3); namespace-parity import, see
# docstring.
from braunschweig.popsim.seed import (  # noqa: F401  (namespace parity, see above)
    CompletenessReport,
    SeedColumns,
    filter_complete_households,
    select_seed_columns,
)
# Moved to entd_donor.py (Task 6); namespace-parity import, see docstring.
from braunschweig.popsim.stratum import (  # noqa: F401  (namespace parity, see above)
    cell_urban_class_from_rs7,
    entd_urban_class,
)
# Moved to entd_trips.py (Task 5); namespace-parity import, see docstring.
from braunschweig.popsim.trips_stage import (  # noqa: F401  (namespace parity, see above)
    CONTRACT,
    apply_per_person_jitter,
)

# Legacy EUR-class -> 5-class economic status map (the status_from_hhtype=False
# fallback semantics). No circular import: braunschweig.synthesis never imports
# from braunschweig.popsim (verified), and this package already imports from the
# shared synthesis tree (synthesis.population.matched below). Moved to
# entd_attributes.py (Task 4); namespace-parity import, see docstring.
from braunschweig.synthesis.population.enriched import (  # noqa: F401  (namespace parity, see above)
    ECONOMIC_STATUS_BY_INCOME_CLASS,
)

# Sibling modules of this package (issue #267 split): the vocabulary lookups
# and the schema helpers below were extracted verbatim out of this module and
# are re-exported here so external imports of this facade keep working
# unchanged (see the "Module layout" docstring section above). INCOME_CLASS_BY_GROUP
# and PT_TICKET_CATEGORIES are not part of this module's real API either, but ARE
# re-exported for namespace-parity/backward-compatibility, not because callers
# should use them.
#
# _cls, _label, _h4_class, _pt_cats and _valid_income_labels are deliberately
# NOT re-exported (controller ruling, issue #267 item C); they are not API and
# split two ways:
#   - _cls, _label, _h4_class are genuinely leaked FOR-LOOP variables inside
#     entd_vocabulary.py's vocabulary-drift guards (Python leaks loop variables
#     to module scope);
#   - _pt_cats and _valid_income_labels are ordinary module-level assignments
#     that feed an import-time guard, not loop variables.
# In the original monolithic module these were genuine module-level names
# defined directly in entd.py; only after the extraction into entd_vocabulary.py
# did they become names this facade would otherwise re-export. No consumer in
# braunschweig/, tests/ or scripts/ references any of them (verified).
# check_namespace.py's ACCIDENTAL_BASELINE_NAMES documents this removal so the
# parity gate does not treat their absence as a regression.
from braunschweig.popsim.sources.entd_vocabulary import (  # noqa: F401  (re-exports)
    ENTD_BUILT_SEED_COLUMNS,
    ENTD_DETOUR_FACTOR,
    ENTD_HIGH_INCOME_CLASS,
    ENTD_SEED_COLUMNS,
    INCOME_CLASS_BY_GROUP,
    PT_TICKET_CATEGORIES,
    _DIRECT_PERSON_COLS,
    _ENTD_INCOME_CLASS_TO_LABEL,
    _H4_INCOME_CLASS_BY_MID_LABEL,
    _HH_JOIN_COLS,
    _PT_TYPE_NONE,
    _PT_TYPE_SUBSCRIBER,
)
from braunschweig.popsim.sources.entd_schema import (  # noqa: F401  (re-exports)
    _require_columns,
    entd_persons_to_donor_schema,
)

# The diary-donor chain-matching helpers below were extracted verbatim into
# entd_diary_matching.py (issue #267 split, Task 2). CHAIN_MATCHING_AGE_BOUNDARIES
# and CHAIN_MATCHING_MINIMUM_OBSERVATIONS are re-exported for backward
# compatibility of the module-level names (see the identity check in
# tests/test_popsim_matched_fallback.py); household_size_class and match_donors
# are re-exported unused within this facade (only entd_diary_matching calls them
# directly) for the same namespace-parity reason.
from braunschweig.popsim.sources.entd_diary_matching import (  # noqa: F401  (re-exports)
    CHAIN_MATCHING_AGE_BOUNDARIES,
    CHAIN_MATCHING_COLUMNS,
    CHAIN_MATCHING_MINIMUM_OBSERVATIONS,
    _CHAIN_MATCHING_SOURCE_COLUMN,
    _derive_chain_matching_frame,
    _match_trip_less_persons_to_diary_donors,
    derive_age_class,
    effective_minimum_observations,
    household_size_class,
    match_donors,
)

# The seed-building helpers below were extracted verbatim into entd_seed.py
# (issue #267 split, Task 3). EntdSource.seed_columns, .built_seed_columns and
# .build_seed are one-line delegations to these module-level functions.
from braunschweig.popsim.sources.entd_seed import (
    build_seed as _build_seed,
    built_seed_columns as _built_seed_columns,
    seed_columns as _seed_columns,
)

# The person-attribute mapper below was extracted verbatim into
# entd_attributes.py (issue #267 split, Task 4). EntdSource.map_person_attributes
# is a one-line delegation to this module-level function.
from braunschweig.popsim.sources.entd_attributes import (
    map_person_attributes as _map_person_attributes,
)

# The trip-building helper below was extracted verbatim into entd_trips.py
# (issue #267 split, Task 5). EntdSource.build_trips is a one-line delegation
# to this module-level function.
from braunschweig.popsim.sources.entd_trips import (
    build_trips as _build_trips,
)

# The donor loading and stratum-derivation helpers below were extracted
# verbatim into entd_donor.py (issue #267 split, Task 6). EntdSource.load_donor,
# .donor_stratum and .cell_stratum are one-line delegations to these
# module-level functions.
from braunschweig.popsim.sources.entd_donor import (
    load_donor as _load_donor,
    donor_stratum as _donor_stratum,
    cell_stratum as _cell_stratum,
)

logger = logging.getLogger(__name__)


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

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_seed.seed_columns`; this
        method's ``__doc__`` is overwritten with that full text below so
        ``help()`` still shows it (issue #295 -- single documentation copy).
        """
        return _seed_columns()

    def built_seed_columns(self) -> SeedColumns:
        """Return the column schema of the seed frames produced by :meth:`build_seed`.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_seed.built_seed_columns`;
        this method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _built_seed_columns()

    def build_seed(
        self,
        households: pd.DataFrame,
        persons: pd.DataFrame,
    ) -> tuple:
        """Build a PopulationSim seed in MiD control schema from ENTD donor frames.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_seed.build_seed`; this
        method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _build_seed(households, persons)

    def load_donor(
        self,
        data_dir: Union[str, Path],
        *,
        injected: Optional[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Load (households, persons, trips) from ENTD parquet files or injected frames.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_donor.load_donor`; this
        method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _load_donor(data_dir, injected=injected)

    def map_person_attributes(
        self,
        persons: pd.DataFrame,
        households: pd.DataFrame,
        *,
        rng=None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Map ENTD canonical columns to the eqasim synthesis schema.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_attributes.map_person_attributes`;
        this method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _map_person_attributes(persons, households, rng=rng)

    def donor_stratum(self, seed_households: pd.DataFrame) -> pd.Series:
        """Return the per-household stratum label for donor stratification.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_donor.donor_stratum`; this
        method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _donor_stratum(seed_households)

    def cell_stratum(self, cells: pd.DataFrame) -> pd.Series:
        """Return the per-100m-cell stratum label for donor stratification.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_donor.cell_stratum`; this
        method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _cell_stratum(cells)

    def build_trips(
        self,
        persons: pd.DataFrame,
        donor_trips: pd.DataFrame,
        *,
        random_seed: int,
        escort_purpose: bool = False,
        escort_passive_education: bool = False,
    ) -> pd.DataFrame:
        """Build the synthesis.population.trips contract DataFrame from ENTD trips.

        Full contract on the delegated implementation,
        :func:`braunschweig.popsim.sources.entd_trips.build_trips`; this
        method's ``__doc__`` is overwritten with that full text below
        (issue #295 -- single documentation copy).
        """
        return _build_trips(
            persons,
            donor_trips,
            random_seed=random_seed,
            escort_purpose=escort_purpose,
            escort_passive_education=escort_passive_education,
        )


# Forward each delegating method's full docstring from the sibling
# implementation it calls (issue #295, cleanup of the #287 split). Before this
# change, every EntdSource method above carried its OWN full copy of its
# delegate's docstring -- verified byte-identical to the delegate's -- purely
# so ``help()`` on the facade still showed complete documentation. That made
# every description exist twice; the two would inevitably drift (the #267
# programme found exactly this pattern repeatedly). The one-line summaries
# kept on the methods above are for readers of THIS source file; the single
# full copy now lives only on the delegate (see each sibling module for its
# docstring), and this assignment forwards it onto the public method's
# ``__doc__`` so ``help(EntdSource.<method>)`` / ``inspect.getdoc(...)``
# still returns the complete text. This is necessary because
# ``inspect.getdoc`` follows CLASS INHERITANCE (a subclass without its own
# docstring inherits a base class's) but does NOT follow a delegation call
# inside a method body -- without the explicit assignment below, the public
# adapter's runtime documentation would silently shrink to the one-liners
# above, which is the empty-``help()`` regression this issue explicitly
# guards against.
EntdSource.seed_columns.__doc__ = _seed_columns.__doc__
EntdSource.built_seed_columns.__doc__ = _built_seed_columns.__doc__
EntdSource.build_seed.__doc__ = _build_seed.__doc__
EntdSource.load_donor.__doc__ = _load_donor.__doc__
EntdSource.map_person_attributes.__doc__ = _map_person_attributes.__doc__
EntdSource.donor_stratum.__doc__ = _donor_stratum.__doc__
EntdSource.cell_stratum.__doc__ = _cell_stratum.__doc__
EntdSource.build_trips.__doc__ = _build_trips.__doc__
