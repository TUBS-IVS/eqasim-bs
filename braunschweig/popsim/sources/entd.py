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
extracted out of it (issue #267 split). Every extracted name is re-exported
here so external imports of ``braunschweig.popsim.sources.entd`` keep working
unchanged. The submodules extracted so far:

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
imports, the re-export blocks above, and EntdSource as a thin delegating
class.

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

# numpy (np) is no longer called directly here (its only call site moved into
# entd_trips.py, issue #267 split, Task 5) but stays imported for
# module-namespace parity, the same rationale as the other "no longer called
# directly" imports below.
import numpy as np  # noqa: F401  (namespace parity, see above)
import pandas as pd

# _AGE_RANGE_BINS, _AGE_RANGE_LABELS and _household_availability are no longer
# called directly here (their only call sites moved into entd_attributes.py,
# issue #267 split, Task 4) but stay imported for module-namespace parity, the
# same rationale as the braunschweig.popsim.seed import block below. ADULT_AGE
# was already unused before this split (kept for the same reason).
from braunschweig.popsim.assembly import (  # noqa: F401  (namespace parity, see above)
    _AGE_RANGE_BINS,
    _AGE_RANGE_LABELS,
    _household_availability,
    ADULT_AGE,
)
# derive_car_availability / derive_bicycle_availability: same rationale --
# their only call sites moved into entd_attributes.py (Task 4).
from braunschweig.popsim.attributes import (  # noqa: F401  (namespace parity, see above)
    derive_car_availability,
    derive_bicycle_availability,
)
# _income_module: only used (ENTD_INCOME_CLASS_MIDPOINT_EUR) inside the moved
# map_person_attributes body; kept imported for module-namespace parity.
from braunschweig.popsim import income as _income_module  # noqa: F401  (namespace parity)
# SeedColumns is still used here as a method return-type annotation.
# CompletenessReport, filter_complete_households and select_seed_columns are no
# longer called directly here (their only call sites moved into entd_seed.py,
# issue #267 split, Task 3) but stay imported for module-namespace parity: they
# were already accessible as braunschweig.popsim.sources.entd.<name> before the
# split and the parity gate pins that.
from braunschweig.popsim.seed import (  # noqa: F401  (namespace parity, see above)
    CompletenessReport,
    SeedColumns,
    filter_complete_households,
    select_seed_columns,
)
# cell_urban_class_from_rs7 and entd_urban_class are no longer called directly
# here (their only call sites moved into entd_donor.py, issue #267 split,
# Task 6) but stay imported for module-namespace parity, the same rationale
# as the other "no longer called directly" imports above.
from braunschweig.popsim.stratum import (  # noqa: F401  (namespace parity, see above)
    cell_urban_class_from_rs7,
    entd_urban_class,
)
# CONTRACT and apply_per_person_jitter are no longer called directly here
# (their only call site moved into entd_trips.py, issue #267 split, Task 5)
# but stay imported for module-namespace parity, the same rationale as above.
from braunschweig.popsim.trips_stage import (  # noqa: F401  (namespace parity, see above)
    CONTRACT,
    apply_per_person_jitter,
)

# Legacy EUR-class -> 5-class economic status map (the status_from_hhtype=False
# fallback semantics). No circular import: braunschweig.synthesis never imports
# from braunschweig.popsim (verified), and this package already imports from the
# shared synthesis tree (synthesis.population.matched below). No longer called
# directly here (its only call site moved into entd_attributes.py, issue #267
# split, Task 4); kept imported for module-namespace parity.
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
# _cls, _h4_class, _label, _pt_cats and _valid_income_labels are deliberately
# NOT re-exported (controller ruling, issue #267 item C): they are import-time
# FOR-LOOP scratch variables inside entd_vocabulary.py's vocabulary-drift
# guards (Python leaks loop variables to module scope), not API -- they only
# ever showed up in dir(entd) as a re-export artifact of the original
# monolithic module. No consumer in braunschweig/, tests/ or scripts/
# references any of them (verified). check_namespace.py's ACCIDENTAL_BASELINE_NAMES
# documents this removal so the parity gate does not treat their absence as a
# regression.
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

        ENTD columns are already in eqasim canonical names; no day-of-week
        completeness filter is needed.
        """
        return _seed_columns()

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
        return _built_seed_columns()

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
        return _build_seed(households, persons)

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
        return _load_donor(data_dir, injected=injected)

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
        return _map_person_attributes(persons, households, rng=rng)

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
        return _donor_stratum(seed_households)

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
        return _build_trips(
            persons,
            donor_trips,
            random_seed=random_seed,
            escort_purpose=escort_purpose,
            escort_passive_education=escort_passive_education,
        )
