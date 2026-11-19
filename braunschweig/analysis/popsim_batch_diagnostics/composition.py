"""Household composition of the synthetic population of one batch, with census counterparts.

The table is used to judge pre-registered gates on the share of households with minors and on
the share of single-adult households among them, so every definition is fixed here:

* A synthetic household inherits the members of its seed household (``H_ID`` joined to
  ``data/seed_persons.csv``); one row of the expanded output is one synthetic household.
* An adult is a person aged ``ADULT_MIN_AGE`` or more (``braunschweig.data.mid.status_by_hhtype``),
  a minor is younger.  ``HP_ALTER`` is the age.
* ``one_adult_share_among_minor_households`` = households with a minor and exactly one adult /
  households with a minor.  A minor household without any adult is counted in the denominator and
  in ``minor_households_without_adult``; nothing is dropped silently.
* ``share_persons_<band>_with_one_adult`` = persons of the age band living in a household with
  exactly one adult / persons of the age band (synthetic persons, copies counted).
* Census side (observed data, Zensus 2022 family-type table on the 1 km grid): sums over the
  distinct 1 km squares of the batch.  The denominator ``census_family_households``
  (``Insgesamt_Haushalte_Typ_priv_HH_Familie``) is ALL private households of the family-type
  table, i.e. the same universe as all synthetic households, not only family households.
  ``census_families_with_minor`` counts families, so it equals households with a minor only if
  every such household holds one family.  The census persons under 18 come from the batch's own
  100 m age-band targets (NaN, logged, when they cannot be derived) and include minors living
  outside any family, which the family table does not count: the column
  ``census_persons_0_17_per_family_with_minor`` is therefore an UPPER BOUND (minors outside
  families are in the numerator only).
* Input checks (no silent bias): a used seed household with a missing or negative age, a missing
  census value in a used square, a used square listed twice with different values, and a square
  of the batch that is absent from the census frame all raise.  A zero denominator gives NaN and
  a logged warning.
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

from braunschweig.data.mid.status_by_hhtype import ADULT_MIN_AGE
from braunschweig.popsim.minor_child_targets import (
    COUPLE_SOURCES as COUPLE_FAMILY_COLUMNS,
    FAMILY_TABLE_HOUSEHOLDS as FAMILY_HOUSEHOLDS_COLUMN,
    LONE_PARENT_SOURCES as LONE_PARENT_COLUMNS,
    SQUARE_ID_COLUMN,
)

logger = logging.getLogger(__name__)
_TAG = "[popsim_batch_diagnostics]"

# The census column names are owned by braunschweig.popsim.minor_child_targets (imported above).
CENSUS_SQUARE_COLUMNS = (SQUARE_ID_COLUMN, FAMILY_HOUSEHOLDS_COLUMN, *LONE_PARENT_COLUMNS,
                         *COUPLE_FAMILY_COLUMNS)

CENSUS_SOURCE = (
    "observed census data (Zensus 2022 family-type table, 1 km); the household denominator is "
    "ALL private households of that table, the same universe as all synthetic households; "
    "census_persons_0_17_per_family_with_minor is an upper bound (minors outside families are "
    "in the numerator only); family-to-household blind spots: see ADR-0204")

_BAND_0_9 = (0, 9)
_BAND_10_14 = (10, 14)
_AGE_COLUMN = "HP_ALTER"
# The production 100 m bands carry the aggregation suffix (``M_AGE_0_9_agg_ZENSUS100m``).
_BAND_COLUMN = re.compile(r"^[MF]_AGE_(\d+)_(\d+)(?:_agg)?_ZENSUS100m$")


def _persons_per_household(batch) -> pd.DataFrame:
    """Counts of adults, minors and the two child bands per seed household id."""
    id_col = batch.household_id_col
    persons = batch.seed_persons
    age = persons[_AGE_COLUMN]
    frame = pd.DataFrame({
        id_col: persons[id_col],
        "adults": age >= ADULT_MIN_AGE,
        "minors": age < ADULT_MIN_AGE,
        "p_0_9": age.between(*_BAND_0_9),
        "p_10_14": age.between(*_BAND_10_14)})
    counts = frame.groupby(id_col).sum().astype("int64")
    return counts.reindex(batch.seed_households[id_col], fill_value=0)


def _census_minors_under_18(batch) -> float:
    """Persons under 18 of the batch's 100 m age-band targets, NaN when not derivable."""
    path = batch.path / "data" / "control_totals_ZENSUS100m.csv"
    if not path.is_file():
        logger.warning("%s composition %s: %s is absent; census persons under 18 not derived",
                       _TAG, batch.name, path.name)
        return float("nan")
    totals = pd.read_csv(path)
    total = 0.0
    found = False
    for column in totals.columns:
        match = _BAND_COLUMN.match(column)
        if not match:
            continue
        low, high = int(match.group(1)), int(match.group(2))
        if low < ADULT_MIN_AGE <= high:
            logger.warning("%s composition %s: age band %s straddles %d; census persons under "
                           "18 not derivable", _TAG, batch.name, column, ADULT_MIN_AGE)
            return float("nan")
        if high < ADULT_MIN_AGE:
            total += float(totals[column].sum())
            found = True
    if not found:
        logger.warning("%s composition %s: no 100 m age-band target below %d in %s; census "
                       "persons under 18 not derived", _TAG, batch.name, ADULT_MIN_AGE, path.name)
        return float("nan")
    return total


def _share(numerator: float, denominator: float, what: str) -> float:
    """``numerator / denominator``; NaN with a warning when the denominator is zero."""
    if not denominator:
        logger.warning("%s composition %s: zero denominator, value is NaN", _TAG, what)
        return float("nan")
    return numerator / denominator


def _require_valid_ages(batch) -> None:
    """Raise when a person of a used seed household has a missing or negative age."""
    id_col = batch.household_id_col
    persons = batch.seed_persons
    used = persons[id_col].isin(batch.expanded[id_col])
    age = pd.to_numeric(persons.loc[used, _AGE_COLUMN], errors="coerce")
    invalid = int((age.isna() | (age < 0)).sum())
    if invalid:
        raise ValueError(f"batch {batch.name}: {invalid} person(s) of the used seed households "
                         f"have a missing or negative {_AGE_COLUMN}; the adult/minor split "
                         "would be biased")


def household_composition(batch, squares: pd.DataFrame | None) -> pd.DataFrame:
    """One row: composition of the batch's synthetic households, plus census columns if given.

    ``squares`` holds the census columns of ``CENSUS_SQUARE_COLUMNS`` (cleaned names, one row
    per 1 km square).  A seed household without persons, or a synthetic household whose seed
    household is unknown, raises ``ValueError``; so does a person of a used seed household with
    a missing or negative age.
    """
    id_col = batch.household_id_col
    counts = _persons_per_household(batch)
    empty = counts.index[(counts["adults"] + counts["minors"]) == 0]
    if len(empty):
        raise ValueError(f"batch {batch.name}: seed household(s) {list(empty[:10])} have no "
                         "persons in seed_persons.csv")
    ids = batch.expanded[id_col]
    unknown = sorted(set(ids) - set(counts.index))
    if unknown:
        raise ValueError(f"batch {batch.name}: synthetic household(s) with unknown seed id "
                         f"{unknown[:10]}")
    _require_valid_ages(batch)
    members = counts.loc[ids.to_numpy()]
    n_households = len(members)
    minor_hh = members["minors"] > 0
    n_minor_hh = int(minor_hh.sum())
    one_adult = members["adults"] == 1

    def share(what: str, numerator: float, denominator: float) -> float:
        return _share(numerator, denominator, f"{batch.name} {what}")

    row = {
        "batch": batch.name,
        "households": n_households,
        "households_with_minor": n_minor_hh,
        "share_households_with_minor": share("households with a minor", n_minor_hh, n_households),
        "one_adult_share_among_minor_households": share(
            "one-adult share among minor households", int((minor_hh & one_adult).sum()),
            n_minor_hh),
        "persons_0_17_per_minor_household": share(
            "persons 0-17 per minor household", float(members["minors"].sum()), n_minor_hh),
        "share_persons_0_9_with_one_adult": share(
            "persons 0-9 with one adult", float(members.loc[one_adult, "p_0_9"].sum()),
            float(members["p_0_9"].sum())),
        "share_persons_10_14_with_one_adult": share(
            "persons 10-14 with one adult", float(members.loc[one_adult, "p_10_14"].sum()),
            float(members["p_10_14"].sum())),
        "minor_households_without_adult": int((minor_hh & (members["adults"] == 0)).sum()),
    }
    if squares is not None:
        row.update(_census_columns(batch, squares))
    logger.info("%s composition %s: %d households, %d with a minor (%.1f%%), %d minor "
                "household(s) without an adult", _TAG, batch.name, n_households, n_minor_hh,
                100 * row["share_households_with_minor"], row["minor_households_without_adult"])
    return pd.DataFrame([row])


def _census_columns(batch, squares: pd.DataFrame) -> dict:
    missing_columns = [c for c in CENSUS_SQUARE_COLUMNS if c not in squares.columns]
    if missing_columns:
        raise ValueError(f"squares lacks column(s) {missing_columns}")
    wanted = pd.Index(batch.expanded["ZENSUS1km"].astype(str).unique())
    census = squares.assign(**{SQUARE_ID_COLUMN: squares[SQUARE_ID_COLUMN].astype(str)})
    absent = wanted.difference(pd.Index(census[SQUARE_ID_COLUMN]))
    if len(absent):
        raise ValueError(f"batch {batch.name}: {len(absent)} 1 km square(s) missing from the "
                         f"census frame, e.g. {list(absent[:5])}")
    used = census[census[SQUARE_ID_COLUMN].isin(wanted)]
    counts = list(CENSUS_SQUARE_COLUMNS[1:])
    nan_columns = [c for c in counts if used[c].isna().any()]
    if nan_columns:
        # The prepared 1 km file has no missing value in these columns: a NaN means a wrong load.
        raise ValueError(f"batch {batch.name}: census value(s) missing in {nan_columns} for a "
                         "used 1 km square; the prepared 1 km file has none, so the load is wrong")
    listed_twice = used[used[SQUARE_ID_COLUMN].duplicated(keep=False)]
    if len(listed_twice):
        distinct = listed_twice.drop_duplicates()
        conflicting = distinct[SQUARE_ID_COLUMN][distinct[SQUARE_ID_COLUMN].duplicated()]
        if len(conflicting):
            raise ValueError(f"batch {batch.name}: {conflicting.nunique()} 1 km square(s) listed "
                             f"twice with different census values, e.g. {list(conflicting[:5])}")
        logger.info("%s composition %s: %d used square(s) listed more than once with identical "
                    "values; one row each kept", _TAG, batch.name,
                    listed_twice[SQUARE_ID_COLUMN].nunique())
    chosen = used.drop_duplicates(SQUARE_ID_COLUMN)

    def share(what: str, numerator: float, denominator: float) -> float:
        return _share(numerator, denominator, f"{batch.name} {what}")

    family_households = float(chosen[FAMILY_HOUSEHOLDS_COLUMN].sum())
    lone = float(chosen[list(LONE_PARENT_COLUMNS)].sum().sum())
    with_minor = lone + float(chosen[list(COUPLE_FAMILY_COLUMNS)].sum().sum())
    minors = _census_minors_under_18(batch)
    persons_per_family = float("nan")
    if not np.isnan(minors):
        persons_per_family = share("census persons 0-17 per family with a minor", minors,
                                   with_minor)
    return {
        "census_family_households": family_households,
        "census_families_with_minor": with_minor,
        "census_share_families_with_minor": share("census share of families with a minor",
                                                  with_minor, family_households),
        "census_lone_parent_share_among_families_with_minor": share(
            "census lone-parent share among families with a minor", lone, with_minor),
        "census_persons_0_17_per_family_with_minor": persons_per_family,
        "census_source": CENSUS_SOURCE,
    }
