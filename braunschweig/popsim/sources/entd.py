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
from braunschweig.popsim.seed import SeedColumns
from braunschweig.popsim.stratum import cell_urban_class_from_rs7, entd_urban_class
from braunschweig.popsim.trips_stage import CONTRACT, apply_per_person_jitter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed column mapping for ENTD (canonical column names from cleaned.py)
# ---------------------------------------------------------------------------

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

# high_income threshold: income_class >= 13 (>=10000 EUR/mo, the top ENTD band).
# This is the ENTD equivalent of the MiD "over_7000" class which sets high_income.
ENTD_HIGH_INCOME_CLASS = 13

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
            logger.info(
                "[EntdSource] using injected donor frames: "
                "%d households, %d persons, %d trips",
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
        logger.info(
            "[EntdSource] loaded donor: %d households, %d persons, %d trips from %s",
            len(households), len(persons), len(trips), data_dir,
        )
        return households, persons, trips

    def map_person_attributes(
        self,
        persons: pd.DataFrame,
        households: pd.DataFrame,
        *,
        rng=None,
    ) -> pd.DataFrame:
        """Map ENTD canonical columns to the eqasim synthesis schema.

        Parameters
        ----------
        persons:
            ENTD persons frame from ``load_donor`` (or the popsim_open expand
            output, which carries the same ENTD-canonical columns).  Must include
            ``person_id``, ``household_id``, and every column in
            ``_DIRECT_PERSON_COLS``.
        households:
            ENTD household frame from ``load_donor``.  Must include
            ``household_id``, ``household_size``, ``number_of_cars``,
            ``number_of_bicycles``, and ``income_class``.
        rng:
            Not used for ENTD (all attributes are directly available); accepted
            for interface compatibility with ``PopsimSource.map_person_attributes``.

        Returns
        -------
        pd.DataFrame
            persons frame extended with all required schema columns except
            ``is_urban_resident`` (added later by ``build_persons`` from the
            home commune).

        Notes
        -----
        - ``source_person_id`` and ``source_household_id`` equal the raw ENTD
          ids (open data, no pseudonymisation).
        - ``weight = 1.0`` (the popsim_open frame is already expanded).
        - ``household_income_eur`` is set to the raw ENTD income-class midpoint
          (``ENTD_INCOME_CLASS_MIDPOINT_EUR[income_class]``).  The INKAR
          per-Kreis scaling is applied by ``build_persons`` AFTER this mapper
          returns, so all sources use the same shared scaling step.
        - ``high_income`` is a placeholder (set to False here); ``build_persons``
          overwrites it with ``household_income_eur >= 5000 EUR`` after INKAR
          scaling.
        """
        out = persons.copy()

        # --- Direct copy: columns already canonical in ENTD cleaned output ---
        # Verify required direct columns are present (fail-fast).
        _require_columns(out, _DIRECT_PERSON_COLS, table_name="ENTD persons")
        _require_columns(households, _HH_JOIN_COLS, table_name="ENTD households")

        # --- Join household attributes onto persons ---
        hh_attrs = households[_HH_JOIN_COLS].copy()
        # Rename household_id to avoid collision with the persons household_id column
        # (both frames carry household_id as the join key -- pandas merge on= handles it).
        out = out.merge(
            hh_attrs.rename(columns={"household_id": "_hh_join_key"}),
            left_on="household_id",
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
        out["source_person_id"] = out["person_id"]
        out["source_household_id"] = out["household_id"]

        # --- weight = 1.0 (popsim_open frame is already expanded, one row per person) ---
        out["weight"] = 1.0

        logger.info(
            "[EntdSource] map_person_attributes: %d persons mapped. "
            "car_availability none/some/all: %s; "
            "pt_subscription_type subscriber/non: %d/%d.",
            len(out),
            dict(out["car_availability"].value_counts()),
            int(out["has_pt_subscription"].sum()),
            int((~out["has_pt_subscription"]).sum()),
        )

        return out

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

        n_persons_with_trips = trips["synthetic_person_id"].nunique()
        n_persons_total = len(persons)
        n_persons_without_trips = n_persons_total - n_persons_with_trips
        logger.info(
            "[EntdSource] build_trips: %d trips for %d/%d synthetic persons "
            "(%.1f%% have donor trips); %d persons without trips.",
            len(trips), n_persons_with_trips, n_persons_total,
            100.0 * n_persons_with_trips / max(n_persons_total, 1),
            n_persons_without_trips,
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
