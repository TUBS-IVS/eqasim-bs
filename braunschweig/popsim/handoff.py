"""Cell -> building handoff for the PopulationSim workflows.

After PopulationSim expands households per 100 m cell, each household must be
placed in a real building. This ports the popsimprep notebook Step 5: for every
cell, distribute its households round-robin across the buildings in that cell,
preferring buildings flagged ``has_home`` and falling back to all buildings in
the cell when none are flagged. Households in cells with no buildings are
"orphans" and are reported (never silently dropped). The building stock is the
``buildings_with_households_zgb.gpkg`` (fields ``cell_id`` = ZENSUS100m,
``has_home``, ...); the assignment logic here works on any DataFrame with those
columns, so it is testable without the 3 GB GeoPackage.

Following the project no-silent-fallback rule, the has_home-vs-fallback cell rate
and the orphan-household rate are logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

logger = logging.getLogger(__name__)


def households_by_cell(
    combined: pd.DataFrame,
    *,
    cell_col: str = "ZENSUS100m",
    hh_id_col: str = "H_ID",
) -> dict[str, list]:
    """Group the merged PopulationSim output into ``{cell_id: [H_ID, ...]}``."""
    return {
        str(cell): group[hh_id_col].tolist()
        for cell, group in combined.groupby(cell_col, sort=False)
    }


@dataclass(frozen=True)
class AssignmentReport:
    """Outcome of the cell -> building assignment.

    Attributes
    ----------
    n_cells_with_households:
        Cells that have at least one household to place.
    n_cells_assigned:
        Cells whose households were placed in ``has_home`` buildings (primary).
    n_cells_fallback:
        Cells with buildings but none flagged ``has_home`` (placed in all
        buildings of the cell -- the fallback path).
    n_cells_orphan:
        Cells with households but NO buildings (households could not be placed).
    n_households_assigned / n_households_orphan:
        Households placed vs. left unplaced (orphans).
    orphan_household_rate:
        ``n_households_orphan / (assigned + orphan)`` (0..1).
    """

    n_cells_with_households: int
    n_cells_assigned: int
    n_cells_fallback: int
    n_cells_orphan: int
    n_households_assigned: int
    n_households_orphan: int
    orphan_household_rate: float


def assign_households_to_buildings(
    hh_by_cell: Mapping[str, Sequence],
    buildings: pd.DataFrame,
    *,
    cell_col: str = "cell_id",
    has_home_col: str = "has_home",
    hh_ids_col: str = "HH_IDs",
    id_separator: str = ";",
) -> tuple[pd.DataFrame, AssignmentReport]:
    """Distribute each cell's households across the cell's buildings.

    For each cell with households: select the ``has_home`` buildings (primary) or,
    if none, all buildings in the cell (fallback). Households are assigned
    round-robin (``household i -> target building i % n``) and written into the
    ``hh_ids_col`` column as a ``id_separator``-joined string per building.

    Parameters
    ----------
    hh_by_cell:
        ``{cell_id: [household_id, ...]}`` (see :func:`households_by_cell`).
    buildings:
        Building frame; must carry ``cell_col`` and ``has_home_col``.

    Returns
    -------
    tuple[pandas.DataFrame, AssignmentReport]
        A copy of ``buildings`` with the filled ``hh_ids_col`` column, and the
        assignment report.

    Raises
    ------
    ValueError
        If the required columns are absent (fail-fast).
    """
    missing = [c for c in (cell_col, has_home_col) if c not in buildings.columns]
    if missing:
        raise ValueError(
            f"buildings frame is missing required column(s) {missing}; available: "
            f"{list(buildings.columns)}."
        )

    result = buildings.copy()
    result[hh_ids_col] = None

    has_home_mask = result[has_home_col].fillna(False).astype(bool)
    cell_to_rows: dict[str, list] = {
        str(cell): group.index.tolist()
        for cell, group in result.groupby(cell_col, sort=False)
    }

    n_cells_assigned = 0
    n_cells_fallback = 0
    n_cells_orphan = 0
    n_households_assigned = 0
    n_households_orphan = 0

    for cell_id, household_ids in hh_by_cell.items():
        cell_id = str(cell_id)
        building_rows = cell_to_rows.get(cell_id)
        if not building_rows:
            n_cells_orphan += 1
            n_households_orphan += len(household_ids)
            continue

        home_rows = [idx for idx in building_rows if has_home_mask[idx]]
        if home_rows:
            target_rows = home_rows
            n_cells_assigned += 1
        else:
            target_rows = building_rows
            n_cells_fallback += 1

        n_targets = len(target_rows)
        buckets: dict[int, list] = {idx: [] for idx in target_rows}
        for i, household_id in enumerate(household_ids):
            buckets[target_rows[i % n_targets]].append(household_id)
        for idx, ids in buckets.items():
            if ids:
                result.at[idx, hh_ids_col] = id_separator.join(str(h) for h in ids)
        n_households_assigned += len(household_ids)

    n_cells_with_households = len(hh_by_cell)
    total_households = n_households_assigned + n_households_orphan
    orphan_rate = (n_households_orphan / total_households) if total_households else 0.0

    report = AssignmentReport(
        n_cells_with_households=n_cells_with_households,
        n_cells_assigned=n_cells_assigned,
        n_cells_fallback=n_cells_fallback,
        n_cells_orphan=n_cells_orphan,
        n_households_assigned=n_households_assigned,
        n_households_orphan=n_households_orphan,
        orphan_household_rate=orphan_rate,
    )

    log = logger.warning if (n_cells_fallback or n_cells_orphan) else logger.info
    log(
        "[popsim.handoff] cells with households %d: %d primary (has_home), "
        "%d fallback (no has_home), %d orphan (no buildings); households %d placed, "
        "%d orphan (%.3f%%)",
        n_cells_with_households, n_cells_assigned, n_cells_fallback, n_cells_orphan,
        n_households_assigned, n_households_orphan, orphan_rate * 100.0,
    )
    return result, report
