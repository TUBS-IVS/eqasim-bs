"""Per-cell targets for households with a minor, from the census 1 km family table.

Issue #659, owner decision D-fam (arm 7b). The four 100 m census family-type controls
count a "child" of any age, while the MiD seed can only see age: its households are
classed by members under 18 (:func:`braunschweig.popsim.seed.derive_hh_minor_class`,
column ``hh_minor_class``). This module turns the census 1 km table
``Typ_der_Kernfamilie_nach_Kindern`` (families by type and children under 18) into two
per-cell household targets with the same under-18 definition:

- :data:`ONE_ADULT_TARGET`  (seed class ``one_adult``): lone fathers + lone mothers
  with at least one child under 18 (:data:`LONE_PARENT_SOURCES`);
- :data:`MULTI_ADULT_TARGET` (seed class ``multi_adult``): married couples, registered
  partnerships and cohabiting couples with at least one child under 18
  (:data:`COUPLE_SOURCES`).

Method. For each 1 km square the share of each class is the class sum divided by the
households of the same census table (:data:`FAMILY_TABLE_HOUSEHOLDS`). A 100 m cell's
target is its own household total times its square's share, so the targets follow the
cells' household totals and the census only supplies the composition.

Fallback (logged, counted, never silent). A square that has households in the cells but
no households in the family table (count 0, typically census secrecy, or the square is
absent from the table) has no share of its own. It takes the share of its Kreis: the
class sum over the Kreis's squares that do have family-table households (and households
in the cells), divided by their family-table households. Each square belongs to ONE
Kreis, the Kreis holding most of its cell households (ties: the smallest Kreis code),
because 1 km squares straddle Kreis borders while the census table is per square. The
report counts the fallback squares and their households; above
``fallback_warn_household_rate`` of all cell households the rate is logged as a warning.
A square with family-table households but no family with a minor is a true 0, not a
fallback.

ASSUMPTION: one family with a child under 18 = one household with a minor, and a lone
parent = the household's one adult. Known blind spots of this mapping:

- a household holding two families (e.g. a lone parent living with her parents) is
  counted twice by the family table and once by the seed;
- minors living without a parent (with grandparents, in foster care) are in a seed
  household with a minor but in no census family with a child;
- a lone parent living with a further adult (a lodger, an adult child, a grandparent) is
  ``one_adult`` in the census and ``multi_adult`` in the seed; this error is one-sided,
  so the one-adult target is an upper bound of the seed class and the multi-adult
  target a lower bound.

Pure module: no I/O. The caller loads the prepared 1 km parquet (columns
:data:`SQUARE_COLUMNS`, cleaned by :func:`braunschweig.popsim.prepared_cells.clean_col_name`,
which turns '-' into '_') and the model's 100 m cells.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

#: Families with at least one child under 18 headed by a lone parent (seed ``one_adult``).
LONE_PARENT_SOURCES = (
    "Vater_mind_1Kind_unter18_Typ_der_Kernfamilie_nach_Kindern_1km_Gitter",
    "Mutter_mind_1Kind_unter18_Typ_der_Kernfamilie_nach_Kindern_1km_Gitter",
)
#: Families with at least one child under 18 headed by a couple (seed ``multi_adult``).
COUPLE_SOURCES = (
    "Ehep_mind_1Kind_unter18_Typ_der_Kernfamilie_nach_Kindern_1km_Gitter",
    "EingetrLP_mind_1Kind_unter18_Typ_der_Kernfamilie_nach_Kindern_1km_Gitter",
    "NichtehelLG_mind_1Kind_unter18_Typ_der_Kernfamilie_nach_Kindern_1km_Gitter",
)
#: Households of the census family table; the denominator of the square shares.
FAMILY_TABLE_HOUSEHOLDS = "Insgesamt_Haushalte_Typ_priv_HH_Familie_1km_Gitter"
#: Raw square id of the 1 km parquet; equals the model's 1 km parent ids
#: (e.g. ``CRS3035RES1000mN3187000E4345000``).
SQUARE_ID_COLUMN = "GITTER_ID_1km"

#: Target column names (households, float) written onto the cells.
ONE_ADULT_TARGET = "HH_MINOR_ONE_ADULT_agg"
MULTI_ADULT_TARGET = "HH_MINOR_MULTI_ADULT_agg"

#: Columns the loader must read from the prepared 1 km parquet.
SQUARE_COLUMNS = (SQUARE_ID_COLUMN,) + LONE_PARENT_SOURCES + COUPLE_SOURCES + (
    FAMILY_TABLE_HOUSEHOLDS,)

#: Default warning bound: share of all cell households whose square takes the Kreis share.
DEFAULT_FALLBACK_WARN_HOUSEHOLD_RATE = 0.05


def _require_columns(frame: pd.DataFrame, required, frame_name: str) -> None:
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(
            f"minor_child_targets: {frame_name} lacks column(s) {missing}; "
            f"available: {list(frame.columns)}"
        )


def _validate_squares(squares: pd.DataFrame) -> None:
    _require_columns(squares, SQUARE_COLUMNS, "squares")
    count_columns = list(SQUARE_COLUMNS[1:])
    nan_columns = [c for c in count_columns if squares[c].isna().any()]
    if nan_columns:
        # The prepared 1 km file has no NaN in these columns; a NaN means a wrong load
        # (e.g. a join that lost rows), not census secrecy.
        raise ValueError(
            f"minor_child_targets: squares hold NaN in {nan_columns}; the prepared 1 km "
            "family table has none, so the load is wrong")
    negative_columns = [c for c in count_columns if (squares[c] < 0).any()]
    if negative_columns:
        raise ValueError(
            f"minor_child_targets: squares hold negative counts in {negative_columns}")
    duplicated = squares[SQUARE_ID_COLUMN][squares[SQUARE_ID_COLUMN].duplicated()]
    if len(duplicated):
        raise ValueError(
            f"minor_child_targets: duplicated square ids, e.g. {duplicated.iloc[:3].tolist()}")


def _majority_kreis(cells: pd.DataFrame, parent_col: str, kreis_col: str,
                    household_total_col: str) -> pd.Series:
    """Kreis of each square: the one holding most of its cell households, ties to the
    smallest Kreis code."""
    per_kreis = (cells.groupby([parent_col, kreis_col])[household_total_col].sum()
                 .rename("_households").reset_index())
    per_kreis = per_kreis.sort_values(
        [parent_col, "_households", kreis_col], ascending=[True, False, True])
    return per_kreis.drop_duplicates(parent_col).set_index(parent_col)[kreis_col]


def per_cell_targets(
    cells: pd.DataFrame,
    squares: pd.DataFrame,
    *,
    parent_col: str,
    kreis_col: str,
    household_total_col: str,
    fallback_warn_household_rate: float = DEFAULT_FALLBACK_WARN_HOUSEHOLD_RATE,
) -> tuple[pd.DataFrame, dict]:
    """Return the cells with the two minor-child household targets and a report.

    Parameters
    ----------
    cells:
        One row per 100 m cell with its 1 km parent id (``parent_col``), its Kreis
        code (``kreis_col``) and its household total (``household_total_col``).
    squares:
        One row per 1 km square with :data:`SQUARE_COLUMNS` (counts of families and
        households of the census table ``Typ_der_Kernfamilie_nach_Kindern``).
    fallback_warn_household_rate:
        Share (0..1) of all cell households on the Kreis fallback above which the rate
        is logged as a warning.

    Returns
    -------
    (targets, report)
        ``targets`` is a copy of ``cells`` with float columns :data:`ONE_ADULT_TARGET`
        and :data:`MULTI_ADULT_TARGET` (households). ``report`` holds ``squares_total``
        (squares with cell households), ``squares_primary``,
        ``squares_on_kreis_fallback`` (of which ``squares_fallback_absent_from_table``
        and ``squares_fallback_zero_table_households``), ``households_total``,
        ``households_on_kreis_fallback``, ``kreis_fallback_household_rate`` and
        ``squares_with_class_sum_above_households``.

    Raises
    ------
    ValueError
        On a missing column, NaN or negative counts, duplicated square ids, negative or
        NaN cell households, or a fallback square whose Kreis has no square with
        family-table households (no defensible share).
    """
    _require_columns(cells, (parent_col, kreis_col, household_total_col), "cells")
    _validate_squares(squares)
    cell_households = pd.to_numeric(cells[household_total_col], errors="coerce")
    if cell_households.isna().any() or (cell_households < 0).any():
        raise ValueError(
            f"minor_child_targets: cells column {household_total_col!r} holds NaN or "
            "negative household totals")

    table = squares.set_index(SQUARE_ID_COLUMN)
    lone_parents = table[list(LONE_PARENT_SOURCES)].sum(axis=1).astype(float)
    couples = table[list(COUPLE_SOURCES)].sum(axis=1).astype(float)
    table_households = table[FAMILY_TABLE_HOUSEHOLDS].astype(float)

    work = cells[[parent_col, kreis_col]].copy()
    work["_households"] = cell_households.astype(float)
    square_households = work.groupby(parent_col)["_households"].sum()
    square_kreis = _majority_kreis(work, parent_col, kreis_col, "_households")

    occupied = square_households.index[square_households > 0]
    has_table_households = table_households.index[table_households > 0]
    primary = occupied.intersection(has_table_households)
    fallback = occupied.difference(has_table_households)

    one_share = pd.Series(0.0, index=square_households.index)
    multi_share = pd.Series(0.0, index=square_households.index)
    one_share.loc[primary] = (lone_parents[primary] / table_households[primary]).to_numpy()
    multi_share.loc[primary] = (couples[primary] / table_households[primary]).to_numpy()

    if len(fallback):
        base = pd.DataFrame({
            "kreis": square_kreis[primary].to_numpy(),
            "lone": lone_parents[primary].to_numpy(),
            "couples": couples[primary].to_numpy(),
            "households": table_households[primary].to_numpy(),
        }).groupby("kreis").sum()
        fallback_kreis = square_kreis[fallback]
        without_base = sorted(set(fallback_kreis) - set(base.index))
        if without_base:
            raise ValueError(
                "minor_child_targets: squares without family-table households lie in "
                f"Kreis {without_base}, which has no square with family-table households "
                "to take a share from")
        kreis_one_share = base["lone"] / base["households"]
        kreis_multi_share = base["couples"] / base["households"]
        one_share.loc[fallback] = kreis_one_share.loc[fallback_kreis].to_numpy()
        multi_share.loc[fallback] = kreis_multi_share.loc[fallback_kreis].to_numpy()

    targets = cells.copy()
    targets[ONE_ADULT_TARGET] = (
        work["_households"] * work[parent_col].map(one_share)).astype(float)
    targets[MULTI_ADULT_TARGET] = (
        work["_households"] * work[parent_col].map(multi_share)).astype(float)

    households_total = float(square_households.sum())
    households_fallback = float(square_households[fallback].sum())
    rate = households_fallback / households_total if households_total else 0.0
    above_households = int(((lone_parents + couples) > table_households)[
        table_households.index.intersection(primary)].sum())
    report = {
        "squares_total": int(len(occupied)),
        "squares_primary": int(len(primary)),
        "squares_on_kreis_fallback": int(len(fallback)),
        "squares_fallback_absent_from_table": int(len(fallback.difference(table.index))),
        "squares_fallback_zero_table_households": int(len(fallback.intersection(table.index))),
        "households_total": households_total,
        "households_on_kreis_fallback": households_fallback,
        "kreis_fallback_household_rate": rate,
        "squares_with_class_sum_above_households": above_households,
    }

    message = (
        "[minor_child_targets] squares primary %d/%d, Kreis fallback %d "
        "(households %.0f/%.0f = %.1f%%)"
    )
    args = (len(primary), len(occupied), len(fallback), households_fallback,
            households_total, 100.0 * rate)
    if rate > fallback_warn_household_rate:
        logger.warning(message + "; above the %.1f%% warning bound", *args,
                       100.0 * fallback_warn_household_rate)
    else:
        logger.info(message, *args)
    if above_households:
        # Families with a minor outnumber the table's households: the share exceeds 1
        # and a cell target exceeds the cell's households. Kept (census counts), but loud.
        logger.warning(
            "[minor_child_targets] %d squares count more families with a minor than "
            "family-table households", above_households)
    return targets, report
