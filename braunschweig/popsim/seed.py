"""PopulationSim SEED provider.

PopulationSim requires a *seed* sample of households + persons (a weighted
microdata sample) that it expands to match a set of control marginals. This
module provides ONE column-mapping-parameterized seed provider so that two
workflows can share identical logic and differ only in their column names:

- ``popsim_mid``  -> the MiD 2023 raw household / person CSVs
  (column names like ``H_ID``, ``H_GEW``, ``HP_ID`` ...; see ``MID_SEED_COLUMNS``).
- ``popsim_open`` -> an open (ENTD-derived) seed with different column names.

The shape of the work is identical for both; only the column names differ.
Therefore a single provider parameterized by a :class:`SeedColumns` mapping is
used rather than two near-duplicate classes.

Scientific assumptions are documented at each step (see
:func:`filter_complete_households`). No real (restricted) microdata is embedded
in this module; the only I/O is reading the two CSV paths the caller supplies.

Units: weights (``household_weight`` / ``person_weight``) are MiD expansion
weights (dimensionless person/household counts); ``age`` is in completed years.

hh_type5 collapse (Task 8 -- Tier-1 household_type control)
------------------------------------------------------------
:func:`derive_hh_type5` collapses the 11-class MiD Haushaltstyp output of
:func:`braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype` to the
5 Zensus 'Familientyp' classes used as PopulationSim control targets.

Collapse mapping (documented; no silent choices):

  MiD 11-class key           -> hh_type5 label
  -----------------------------------------------
  single_18_29               -> einpersonen
  single_30_59               -> einpersonen
  single_60_plus             -> einpersonen
  couple_youngest_18_29      -> paar_ohne_kind
  couple_youngest_30_59      -> paar_ohne_kind
  couple_youngest_60_plus    -> paar_ohne_kind
  child_under_6              -> paar_mit_kind
  child_under_14             -> paar_mit_kind
  child_under_18             -> paar_mit_kind
  single_parent              -> alleinerziehend
  three_plus_adults          -> mehrpers_ohne_kernfamilie
  not_classifiable           -> None  (NaN in output; households dropped from this control)

Rationale for ``three_plus_adults -> mehrpers_ohne_kernfamilie``: the MiD
category 'HH mit mind. 3 Erwachsenen (ohne Kinder)' covers households with
three or more adults and no children.  This matches the Zensus 2022
'Mehrpersonenhaushalt ohne Kernfamilie' which captures multi-adult arrangements
that are not a couple+child structure.  The mapping is unambiguous.

Rationale for ``not_classifiable -> None``: the MiD 'nicht zuzuordnen' residual
is produced by :func:`map_households_to_hhtype` for households that cannot be
placed (zero adults + zero children).  These cannot be assigned to any Zensus
Familientyp class without fabricating information; they are left as NaN so
PopulationSim excludes them from the household_type controls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple, Union

import pandas as pd

logger = logging.getLogger(__name__)

# A high household drop rate during completeness filtering almost always means
# the day filter or the household linkage is broken (CLAUDE.md mandatory
# fallback-transparency rule: never drop silently, a high drop rate is a
# warning signal). Above this fraction of dropped households we log a warning.
HIGH_DROP_RATE_WARNING_THRESHOLD = 0.5


@dataclass(frozen=True)
class SeedColumns:
    """Column-name mapping for a PopulationSim seed (households + persons).

    Each field names the column in the raw seed CSV that carries the
    corresponding quantity. This indirection lets one provider serve both the
    MiD seed and the open (ENTD-derived) seed, which use different names.

    Attributes:
        household_id: Household identifier column in the *household* table.
        household_weight: Household expansion weight column.
        person_household_id: Household identifier column in the *person* table
            (the foreign key linking a person to its household).
        person_id: Person identifier column.
        person_weight: Person expansion weight column.
        age: Person age column (completed years).
        sex: Person sex column.
        day_filter_col: Optional column carrying a day-of-week / reporting-day
            flag used by the completeness filter (e.g. the MiD ``kernwo``
            core-week flag). ``None`` disables day filtering.
        day_filter_values: Optional default set of accepted ``day_filter_col``
            values. Persons whose value is NOT in this set are considered to
            have no valid reporting day (see
            :func:`filter_complete_households`).
    """

    household_id: str
    household_weight: str
    person_household_id: str
    person_id: str
    person_weight: str
    age: str
    sex: str
    day_filter_col: Optional[str] = None
    day_filter_values: Optional[Tuple[object, ...]] = None


# Canonical MiD 2023 raw seed column names. The day filter uses the MiD core-week
# flag column ``kernwo``. The exact accepted value set is left to the caller /
# workflow config (see the [TODO] in the module-level note) because it depends on
# MiD raw semantics that must not be inspected here.
MID_SEED_COLUMNS = SeedColumns(
    household_id="H_ID",
    household_weight="H_GEW",
    person_household_id="H_ID",  # persons link to their household by H_ID (not HP_ID)
    person_id="P_ID",
    person_weight="P_GEW",
    age="HP_ALTER",
    sex="HP_SEX",
    day_filter_col="kernwo",
    day_filter_values=(1, 2, 3),
)

# kernwo value sets (MiD 2023 core-week flag): single source of truth.
WEEKDAY_KERNWO = (1, 2, 3)
ALL_REPORTING_KERNWO = (1, 2, 3, 4, 5, 6, 7)


@dataclass(frozen=True)
class CompletenessReport:
    """Outcome of the complete-household filter.

    Attributes:
        n_households_in: Households present before filtering.
        n_households_complete: Households that lost zero persons to the day
            filter and were therefore kept.
        n_households_dropped: Households dropped because they lost at least one
            person to the day filter.
        completeness_rate: ``n_households_complete / n_households_in`` (0..1),
            or 0.0 when there were no input households.
        n_persons_in: Persons present before filtering.
        n_persons_kept: Persons remaining after dropping incomplete households.
    """

    n_households_in: int
    n_households_complete: int
    n_households_dropped: int
    completeness_rate: float
    n_persons_in: int
    n_persons_kept: int


def load_seed(
    households_path: Union[str, Path],
    persons_path: Union[str, Path],
    columns: SeedColumns,
    *,
    sep: str = ",",
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Read the household and person seed CSVs.

    This is the only file I/O in the module; callers (and tests) supply the
    paths. The files are validated to exist (fail-fast) before reading.

    Args:
        households_path: Path to the household seed CSV.
        persons_path: Path to the person seed CSV.
        columns: Column mapping (currently unused for parsing but kept in the
            signature so the loader can be extended with dtype hints per column
            without changing callers).
        sep: CSV field separator.

    Returns:
        ``(df_households, df_persons)``.

    Raises:
        FileNotFoundError: If either path does not exist.
    """
    households_path = Path(households_path)
    persons_path = Path(persons_path)

    if not households_path.is_file():
        raise FileNotFoundError(f"Seed household file not found: {households_path}")
    if not persons_path.is_file():
        raise FileNotFoundError(f"Seed person file not found: {persons_path}")

    df_households = pd.read_csv(households_path, sep=sep)
    df_persons = pd.read_csv(persons_path, sep=sep)

    logger.info(
        "Loaded seed: %d households (%s), %d persons (%s)",
        len(df_households),
        households_path.name,
        len(df_persons),
        persons_path.name,
    )
    return df_households, df_persons


def filter_complete_households(
    df_households: pd.DataFrame,
    df_persons: pd.DataFrame,
    columns: SeedColumns,
    *,
    day_filter_values: Optional[Iterable[object]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, CompletenessReport]:
    """Keep only households that lost zero persons to the day-of-week filter.

    Scientific assumption (from the source MiD seed notebook): a household is a
    valid seed observation only if EVERY one of its persons reported on an
    accepted reporting day. The rule is:

    1. If a day filter is active (``columns.day_filter_col`` set AND a non-empty
       value set is given), mark persons whose ``day_filter_col`` value is NOT
       in ``day_filter_values`` as "lost".
    2. A household is "complete" iff it lost ZERO persons.
    3. Keep only complete households and their persons.

    This discards partially-observed households rather than imputing the missing
    person-days, so the retained seed contains only households with a fully
    consistent within-household person set. The drop is never silent: the
    primary-vs-dropped rate is logged, and a high drop rate is warned (CLAUDE.md
    mandatory no-silent-fallback rule).

    Args:
        df_households: Household seed frame.
        df_persons: Person seed frame.
        columns: Column mapping.
        day_filter_values: Accepted ``day_filter_col`` values. ``None`` -> no day
            filtering is applied (every household is complete by definition).

    Returns:
        ``(df_households_complete, df_persons_complete, CompletenessReport)``.
    """
    n_households_in = len(df_households)
    n_persons_in = len(df_persons)

    day_filter_active = (
        columns.day_filter_col is not None
        and day_filter_values is not None
    )

    if day_filter_active:
        accepted_values = set(day_filter_values)  # type: ignore[arg-type]
        if columns.day_filter_col not in df_persons.columns:
            raise ValueError(
                f"Day filter column '{columns.day_filter_col}' is not present in "
                f"the person seed (available: {list(df_persons.columns)})."
            )

        kept_person_mask = df_persons[columns.day_filter_col].isin(accepted_values)

        # Per household: number of persons present vs number kept by the filter.
        household_ids = df_persons[columns.person_household_id]
        n_present = household_ids.groupby(household_ids).transform("size")
        n_kept = kept_person_mask.groupby(household_ids).transform("sum")
        # A household is complete iff it kept all its persons (lost zero).
        person_in_complete_household = n_kept.eq(n_present)

        df_persons_complete = df_persons[person_in_complete_household].copy()
        complete_household_ids = set(
            df_persons_complete[columns.person_household_id].unique()
        )
    else:
        df_persons_complete = df_persons.copy()
        complete_household_ids = set(
            df_persons[columns.person_household_id].unique()
        )

    df_households_complete = df_households[
        df_households[columns.household_id].isin(complete_household_ids)
    ].copy()

    n_households_complete = len(df_households_complete)
    n_households_dropped = n_households_in - n_households_complete
    completeness_rate = (
        n_households_complete / n_households_in if n_households_in > 0 else 0.0
    )

    report = CompletenessReport(
        n_households_in=n_households_in,
        n_households_complete=n_households_complete,
        n_households_dropped=n_households_dropped,
        completeness_rate=completeness_rate,
        n_persons_in=n_persons_in,
        n_persons_kept=len(df_persons_complete),
    )

    drop_rate = 1.0 - completeness_rate
    log = logger.warning if drop_rate > HIGH_DROP_RATE_WARNING_THRESHOLD else logger.info
    log(
        "Seed completeness filter (day_filter=%s): kept %d/%d households "
        "(%.1f%%), dropped %d (%.1f%%); persons %d/%d kept.",
        "on" if day_filter_active else "off",
        n_households_complete,
        n_households_in,
        100.0 * completeness_rate,
        n_households_dropped,
        100.0 * drop_rate,
        report.n_persons_kept,
        n_persons_in,
    )

    return df_households_complete, df_persons_complete, report


def _require_columns(
    df: pd.DataFrame, required: Sequence[str], *, table_name: str
) -> None:
    """Raise a clear ValueError if any required column is missing (fail-fast)."""
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"Seed {table_name} table is missing required column(s) "
            f"{missing}; available columns: {list(df.columns)}."
        )


def select_seed_columns(
    df_households: pd.DataFrame,
    df_persons: pd.DataFrame,
    columns: SeedColumns,
    *,
    extra_household_cols: Sequence[str] = (),
    extra_person_cols: Sequence[str] = (),
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Reduce both tables to the essential seed columns (plus requested extras).

    Essentials kept:
      - households: ``household_id``, ``household_weight``
      - persons: ``person_household_id``, ``person_id``, ``person_weight``,
        ``age``, ``sex``

    A constant ``STAAT`` column = 1 is added to BOTH tables. ``STAAT`` is the
    top (whole-country) seed geography in the PopulationSim crosswalk: the seed
    is a single national sample, so every record shares the same top-geography
    code 1.

    Extras (e.g. columns referenced by PopulationSim control expressions) are
    appended in order and de-duplicated against the essentials.

    Args:
        df_households: Household seed frame.
        df_persons: Person seed frame.
        columns: Column mapping.
        extra_household_cols: Additional household columns to retain.
        extra_person_cols: Additional person columns to retain.

    Returns:
        ``(df_households_selected, df_persons_selected)``.

    Raises:
        ValueError: If any essential or requested extra column is missing.
    """
    household_essentials = [columns.household_id, columns.household_weight]
    person_essentials = [
        columns.person_household_id,
        columns.person_id,
        columns.person_weight,
        columns.age,
        columns.sex,
    ]

    _require_columns(df_households, household_essentials, table_name="household")
    _require_columns(
        df_households, list(extra_household_cols), table_name="household"
    )
    _require_columns(df_persons, person_essentials, table_name="person")
    _require_columns(df_persons, list(extra_person_cols), table_name="person")

    household_keep = _unique_preserving_order(
        household_essentials, extra_household_cols
    )
    person_keep = _unique_preserving_order(person_essentials, extra_person_cols)

    df_households_selected = df_households[household_keep].copy()
    df_persons_selected = df_persons[person_keep].copy()

    # STAAT = top seed geography (single national sample -> constant 1).
    df_households_selected["STAAT"] = 1
    df_persons_selected["STAAT"] = 1

    return df_households_selected, df_persons_selected


def _unique_preserving_order(
    essentials: Sequence[str], extras: Sequence[str]
) -> list:
    """Concatenate essentials + extras, dropping duplicates, preserving order."""
    keep: list = []
    for column in list(essentials) + list(extras):
        if column not in keep:
            keep.append(column)
    return keep


def build_seed(
    households_path: Union[str, Path],
    persons_path: Union[str, Path],
    columns: SeedColumns,
    *,
    day_filter_values: Optional[Iterable[object]] = None,
    extra_household_cols: Sequence[str] = (),
    extra_person_cols: Sequence[str] = (),
    sep: str = ",",
) -> Tuple[pd.DataFrame, pd.DataFrame, CompletenessReport]:
    """End-to-end seed build: load -> filter complete households -> select columns.

    Args:
        households_path: Path to the household seed CSV.
        persons_path: Path to the person seed CSV.
        columns: Column mapping.
        day_filter_values: Accepted day-filter values. When ``None``, the value
            set declared on ``columns.day_filter_values`` is used (which may
            itself be ``None`` -> no day filtering).
        extra_household_cols: Additional household columns to retain.
        extra_person_cols: Additional person columns to retain.
        sep: CSV field separator.

    Returns:
        ``(df_households, df_persons, CompletenessReport)``.
    """
    effective_day_filter_values = (
        day_filter_values
        if day_filter_values is not None
        else columns.day_filter_values
    )

    df_households, df_persons = load_seed(
        households_path, persons_path, columns, sep=sep
    )
    df_households, df_persons, report = filter_complete_households(
        df_households,
        df_persons,
        columns,
        day_filter_values=effective_day_filter_values,
    )
    df_households, df_persons = select_seed_columns(
        df_households,
        df_persons,
        columns,
        extra_household_cols=extra_household_cols,
        extra_person_cols=extra_person_cols,
    )
    return df_households, df_persons, report


# ---------------------------------------------------------------------------
# hh_type5: Tier-1 household_type collapse (Task 8)
# ---------------------------------------------------------------------------

# Collapse of the 11-class MiD Haushaltstyp output of map_households_to_hhtype
# to the 5 Zensus 'Familientyp' classes. Keys are the canonical HHTYPE_CATEGORIES
# strings from braunschweig.data.mid.status_by_hhtype; values are the hh_type5
# labels used by the Tier-1 household_type control expressions. ``not_classifiable``
# maps to None so households with that label yield NaN in the output column (they
# cannot be placed in any Zensus Familientyp class). See the module docstring for
# the full rationale.
_MID11_TO_HH_TYPE5: dict = {
    "single_18_29":            "einpersonen",
    "single_30_59":            "einpersonen",
    "single_60_plus":          "einpersonen",
    "couple_youngest_18_29":   "paar_ohne_kind",
    "couple_youngest_30_59":   "paar_ohne_kind",
    "couple_youngest_60_plus":  "paar_ohne_kind",
    "child_under_6":           "paar_mit_kind",
    "child_under_14":          "paar_mit_kind",
    "child_under_18":          "paar_mit_kind",
    "single_parent":           "alleinerziehend",
    "three_plus_adults":       "mehrpers_ohne_kernfamilie",
    "not_classifiable":        None,
}


def derive_hh_type5(
    df_persons: "pd.DataFrame",
    household_id_col: str = "household_id",
    age_col: str = "HP_ALTER",
) -> "pd.Series":
    """Derive the per-household ``hh_type5`` label for MiD seed households.

    Applies :func:`braunschweig.data.mid.status_by_hhtype.map_households_to_hhtype`
    to ``df_persons`` (producing the 11-class MiD Haushaltstyp per person), then
    aggregates to one label per household (all persons in a household share the
    same classification), and collapses the 11 classes to the 5 Zensus
    Familientyp labels via ``_MID11_TO_HH_TYPE5``.

    Parameters
    ----------
    df_persons:
        Person frame with at least ``household_id_col`` and ``age_col`` columns.
        The ``hh_type`` column is optional (used by map_households_to_hhtype
        as a tie-breaker for single-parent detection).
    household_id_col:
        Name of the household identifier column (default ``"household_id"``).
    age_col:
        Name of the person age column (default ``"HP_ALTER"`` -- the raw MiD
        column name used at the real call site in ``load_mid_seed``).

    Returns
    -------
    pd.Series
        Per-household ``hh_type5`` label (index = household id values).
        Households classified as ``"not_classifiable"`` yield ``None``/NaN.
    """
    from braunschweig.data.mid.status_by_hhtype import map_households_to_hhtype

    # map_households_to_hhtype requires 'household_id' and 'age' columns.
    # Rename on a copy so the caller's frame is never mutated.
    rename_map = {}
    if household_id_col != "household_id":
        rename_map[household_id_col] = "household_id"
    if age_col != "age":
        rename_map[age_col] = "age"
    work = df_persons.rename(columns=rename_map).copy() if rename_map else df_persons.copy()

    per_person_mid11 = map_households_to_hhtype(work)  # pd.Series aligned to work.index

    # Aggregate to one label per household. All persons in a household share the
    # same hh_type5 key, so .first() is equivalent to any() here; keep it for
    # robustness against edge-case NaN propagation from not_classifiable households.
    work_with_key = work[["household_id"]].copy()
    work_with_key["_mid11"] = per_person_mid11.values
    per_hh_mid11 = (
        work_with_key.groupby("household_id")["_mid11"]
        .first()
    )

    # Collapse 11-class -> 5-class (None keys pass through as NaN).
    per_hh_type5 = per_hh_mid11.map(_MID11_TO_HH_TYPE5)
    per_hh_type5.name = "hh_type5"
    return per_hh_type5
