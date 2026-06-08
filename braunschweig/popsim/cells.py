"""Zensus 2022 INSPIRE grid cell handling (100 m / 1 km).

The PopulationSim workflows synthesise at the German Zensus 2022 INSPIRE grid:
100 m target cells nested under 1 km parent cells, in EPSG:3035 (LAEA Europe).
Cells are identified by their INSPIRE id string ``CRS3035RES{res}mN{north}E{east}``
where ``north``/``east`` are the south-west corner coordinates in metres; the
geometry is implicit in the id (the source parquets carry no geometry column).

This module provides the id parsing and the 100 m -> 1 km parent derivation that
the batching relies on. Consistency checks build on these (added test-first in
the same phase).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

# INSPIRE id, e.g. CRS3035RES100mN2689100E4337000 or CRS3035RES1000mN2689000E4337000.
_INSPIRE_ID_RE = re.compile(r"^CRS3035RES(?P<res>\d+)mN(?P<north>\d+)E(?P<east>\d+)$")

# 1 km in metres; a 100 m cell's parent is found by flooring its SW corner here.
_ONE_KM_M = 1000
_HUNDRED_M = 100


def parse_inspire_id(cell_id: str) -> tuple[int, int, int]:
    """Parse an INSPIRE grid id into ``(resolution_m, north_m, east_m)``.

    Parameters
    ----------
    cell_id:
        An INSPIRE id string, e.g. ``"CRS3035RES100mN2689100E4337000"``.

    Returns
    -------
    tuple[int, int, int]
        The resolution in metres and the south-west corner (north, east) in
        EPSG:3035 metres.

    Raises
    ------
    ValueError
        If ``cell_id`` is not a valid ``CRS3035RES`` grid id.
    """
    match = _INSPIRE_ID_RE.match(cell_id)
    if match is None:
        raise ValueError(f"Not a valid CRS3035 INSPIRE grid id: {cell_id!r}")
    return int(match["res"]), int(match["north"]), int(match["east"])


def derive_1km_parent_id(cell_id_100m: str) -> str:
    """Return the 1 km parent INSPIRE id of a 100 m cell.

    The parent is obtained by flooring the 100 m cell's south-west corner to the
    enclosing 1 km grid line. This matches the explicit ``GITTER_ID_1km`` column
    of the source 100 m parquet (verified equal on the full file during
    investigation), so either source is interchangeable for batching.

    Parameters
    ----------
    cell_id_100m:
        A 100 m INSPIRE id (``CRS3035RES100m...``).

    Returns
    -------
    str
        The enclosing 1 km parent id (``CRS3035RES1000m...``).

    Raises
    ------
    ValueError
        If ``cell_id_100m`` is not a 100 m cell id.
    """
    res, north, east = parse_inspire_id(cell_id_100m)
    if res != _HUNDRED_M:
        raise ValueError(
            f"derive_1km_parent_id expects a 100 m cell id, got resolution {res} m "
            f"for {cell_id_100m!r}."
        )
    parent_north = (north // _ONE_KM_M) * _ONE_KM_M
    parent_east = (east // _ONE_KM_M) * _ONE_KM_M
    return f"CRS3035RES1000mN{parent_north}E{parent_east}"


# Hard INSPIRE invariant: a 1 km cell nests at most 10x10 = 100 cells of 100 m.
MAX_CHILDREN_PER_PARENT = 100


@dataclass(frozen=True)
class NestingReport:
    """Result of a 100 m -> 1 km nesting consistency check.

    Attributes
    ----------
    n_100m_cells:
        Number of 100 m cells inspected.
    n_1km_parents_present:
        Distinct 1 km parents that exist in the 1 km table.
    n_orphan_cells / orphan_rate:
        100 m cells whose derived 1 km parent is absent from the 1 km table, and
        that count as a fraction of all 100 m cells. Orphans are reported, not a
        consistency failure -- they are handled explicitly downstream.
    max_children_per_parent:
        Largest number of 100 m children under any 1 km parent.
    parents_over_limit:
        1 km parents exceeding ``max_children`` (a violation).
    max_abs_pop_diff / n_reconciled_parents:
        Largest absolute |sum(100 m pop) - 1 km pop| over the parents present in
        both tables, and how many parents were reconciled.
    is_consistent:
        ``True`` iff no parent exceeds the child limit and the worst
        reconciliation difference is within tolerance (orphans excluded).
    """

    n_100m_cells: int
    n_1km_parents_present: int
    n_orphan_cells: int
    orphan_rate: float
    max_children_per_parent: int
    parents_over_limit: list[str]
    max_abs_pop_diff: float
    n_reconciled_parents: int
    is_consistent: bool


def check_nesting_consistency(
    df_100m: pd.DataFrame,
    df_1km: pd.DataFrame,
    *,
    id_col_100m: str = "GITTER_ID_100m",
    parent_col: str = "GITTER_ID_1km",
    id_col_1km: str = "GITTER_ID_1km",
    pop_col_100m: str = "POP_TOTAL_100m",
    pop_col_1km: str = "POP_TOTAL_1km",
    max_children: int = MAX_CHILDREN_PER_PARENT,
    pop_tolerance: float = 1.0,
    verify_derived_parent: bool = True,
) -> NestingReport:
    """Check the 100 m -> 1 km nesting of the Zensus grid; log the orphan rate.

    Verifies (a) the explicit ``GITTER_ID_1km`` parent of each 100 m cell equals
    the parent derived from its own id (data-integrity guard), (b) no 1 km parent
    has more than ``max_children`` children, and (c) the 100 m population sums up
    to the 1 km parent population within ``pop_tolerance`` for every parent
    present in both tables. Orphan 100 m cells (parent absent from the 1 km table)
    are counted and the rate is logged (fallback transparency), but do not by
    themselves make the grid inconsistent.

    Raises
    ------
    ValueError
        If ``verify_derived_parent`` and any explicit parent disagrees with the
        derived one (a corrupt grid -- fail fast rather than batch on bad ids).
    """
    n_100m = len(df_100m)
    derived_parent = df_100m[id_col_100m].map(derive_1km_parent_id)

    if verify_derived_parent and parent_col in df_100m.columns:
        disagree = df_100m[parent_col].astype(str) != derived_parent
        if disagree.any():
            example = df_100m.loc[disagree, id_col_100m].iloc[0]
            raise ValueError(
                "100 m cell explicit parent disagrees with the derived 1 km "
                f"parent (e.g. {example!r}); the grid ids are inconsistent."
            )

    # Children per parent + the child-limit check.
    children_per_parent = derived_parent.value_counts()
    max_children_seen = int(children_per_parent.max()) if len(children_per_parent) else 0
    parents_over_limit = sorted(
        children_per_parent[children_per_parent > max_children].index.tolist()
    )

    # Orphans: derived parents absent from the 1 km table.
    parents_1km = set(df_1km[id_col_1km].astype(str))
    is_orphan = ~derived_parent.isin(parents_1km)
    n_orphans = int(is_orphan.sum())
    orphan_rate = (n_orphans / n_100m) if n_100m else 0.0

    # Population reconciliation over parents present in both tables.
    sums_100m = (
        df_100m.loc[~is_orphan, pop_col_100m]
        .groupby(derived_parent[~is_orphan])
        .sum()
    )
    pop_1km = df_1km.set_index(df_1km[id_col_1km].astype(str))[pop_col_1km]
    reconciled = sums_100m.index.intersection(pop_1km.index)
    if len(reconciled):
        abs_diff = (sums_100m.loc[reconciled] - pop_1km.loc[reconciled]).abs()
        max_abs_pop_diff = float(abs_diff.max())
    else:
        max_abs_pop_diff = 0.0

    is_consistent = (not parents_over_limit) and (max_abs_pop_diff <= pop_tolerance)

    # Fallback transparency: always surface the orphan rate.
    log = logger.warning if n_orphans else logger.info
    log(
        "[popsim.cells] nesting: %d 100m cells, %d 1km parents present, "
        "orphans %d (%.3f%%), max children/parent %d, max |pop diff| %.3f",
        n_100m,
        len(parents_1km),
        n_orphans,
        orphan_rate * 100.0,
        max_children_seen,
        max_abs_pop_diff,
    )
    if parents_over_limit:
        logger.error(
            "[popsim.cells] %d 1km parents exceed %d children: %s",
            len(parents_over_limit),
            max_children,
            parents_over_limit[:5],
        )

    return NestingReport(
        n_100m_cells=n_100m,
        n_1km_parents_present=len(parents_1km),
        n_orphan_cells=n_orphans,
        orphan_rate=orphan_rate,
        max_children_per_parent=max_children_seen,
        parents_over_limit=parents_over_limit,
        max_abs_pop_diff=max_abs_pop_diff,
        n_reconciled_parents=int(len(reconciled)),
        is_consistent=is_consistent,
    )
