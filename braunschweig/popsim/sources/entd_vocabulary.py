"""ENTD vocabulary: seed column mapping, income-class labels, and fixed lookups.

Groups the module-level constants that describe the ENTD donor "vocabulary" --
the seed column name mapping (pre- and post- :meth:`EntdSource.build_seed`),
the ``income_class`` (0..13) -> MiD categorical ``household_income`` label
lookup and its H4 economic-status bridge, the ``high_income`` threshold, the
detour-factor alias, the PT-ticket defaults, and the direct-copy /
household-join column lists used by :meth:`EntdSource.map_person_attributes`
and :meth:`EntdSource.build_seed`.

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports every public name here so external imports of the
facade module are unaffected. Five import-time guard locals (``_cls``,
``_label``, ``_h4_class``, ``_pt_cats``, ``_valid_income_labels``) are
deliberately not re-exported -- see ``entd.py``'s "Sibling modules" comment
and ``check_namespace.py``'s ``ACCIDENTAL_BASELINE_NAMES``. Kept separate
from ``entd_schema`` (the column-presence / donor-schema-rename helpers)
because these are static lookup tables and category vocabularies, not
behaviour.
"""

from __future__ import annotations

from typing import Optional

from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES
from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP
from braunschweig.popsim.seed import SeedColumns
from braunschweig.synthesis.population.enriched import ECONOMIC_STATUS_BY_INCOME_CLASS

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
from braunschweig.constants import ROUTED_DETOUR_FACTOR as ENTD_DETOUR_FACTOR  # noqa: F401  (re-export)

# ---------------------------------------------------------------------------
# PT ticket defaults (ENTD has no ticket-type field)
# ---------------------------------------------------------------------------

# Subscribers -> a representative flatrate category (must be in PT_TICKET_FLATRATE
# AND PT_TICKET_CATEGORIES). Non-subscribers -> never-uses.
_PT_TYPE_SUBSCRIBER = "weekly_monthly_no_subscription"
_PT_TYPE_NONE = "never_pt"

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
