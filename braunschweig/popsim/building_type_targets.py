"""Building-type control targets that count HOUSEHOLDS, not dwellings (issue #647).

The Zensus 2022 100 m grid reports the building type of the DWELLINGS in a cell (the
``*_Wohnung_Gebaeudetyp_Groesse_100m_Gitter`` columns). Summed over the three building-type
classes they exceed the household total of the cell (vacant and second dwellings, dwellings
of institutional households), so PopulationSim, whose seed counts one household per row,
cannot meet them. This module rescales the three class targets of every cell to the cell's
household total, keeping the dwelling SHARES::

    target_k = households * dwellings_k / sum_j(dwellings_j)

The result is float; the existing per-column integerisation within the 1 km parent
(``braunschweig.popsim.controls.integerize_within_parents``) runs on it afterwards and
rounds each column separately, so the integer columns of a cell may differ from the household
total by that rounding.

Fallback tiers (primary first), counted and logged per cell and per household (rate rule:
``docs/codebase/notes/popsim-fallback-rates.md``):

1. own shares: the cell has households and dwellings (primary);
2. 1 km shares: the cell has households but no dwelling count, so it takes the dwelling
   shares of its 1 km parent;
3. Kreis shares: the 1 km parent has no dwelling count either.

A cell with households and no share at any level raises; a cell without households gets 0.
The raw dwelling source columns are never modified (``ownership_grid`` reads them).

The two ASSUMPTIONS of this rescale (share rule, NaN class count) are stated in ADR-0198,
decision 5. Cells whose class vector is partly NaN are counted and logged
(``partial_nan_*`` rate lines).
"""

from __future__ import annotations

import logging
from typing import Sequence

import numpy as np
import pandas as pd

from braunschweig.popsim import control_spec, folders
from braunschweig.popsim.fallback_rates import log_fallback_rate

logger = logging.getLogger(__name__)

#: Stage tag of every log line of this module.
LOG_TAG = "popsim.building_type_targets"

#: Parent levels tried, in order, for a cell with households but no dwelling count.
DEFAULT_PARENT_COLS: tuple[str, ...] = (folders.GEO_1KM, folders.GEO_KREIS)


def _numeric_nonnegative(frame: pd.DataFrame, columns: Sequence[str], what: str) -> np.ndarray:
    """Return ``frame[columns]`` as a float64 matrix, NaN (Zensus suppression) read as 0."""
    values = frame[list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype="float64")
    values = np.nan_to_num(values, nan=0.0)
    if (values < 0).any():
        raise ValueError(
            f"[{LOG_TAG}] {int((values < 0).any(axis=1).sum())} cell(s) carry a negative {what}; "
            "counts cannot be negative. Check the prepared-cell source."
        )
    return values


def rescale_building_type_to_households(
    cells: pd.DataFrame,
    building_type_cols: Sequence[str],
    household_total_col: str = control_spec.HH_TOTAL_CENSUS_COLUMN,
    parent_cols: Sequence[str] = DEFAULT_PARENT_COLS,
) -> pd.DataFrame:
    """Rescale the building-type columns to the household total; see the ``_with_report`` variant.

    Returns:
        A copy of ``cells`` with FLOAT ``building_type_cols`` (the first value of
        ``rescale_building_type_to_households_with_report``).
    """
    return rescale_building_type_to_households_with_report(
        cells, building_type_cols, household_total_col, parent_cols)[0]


def rescale_building_type_to_households_with_report(
    cells: pd.DataFrame,
    building_type_cols: Sequence[str],
    household_total_col: str = control_spec.HH_TOTAL_CENSUS_COLUMN,
    parent_cols: Sequence[str] = DEFAULT_PARENT_COLS,
) -> tuple[pd.DataFrame, dict]:
    """Rescale the dwelling-based building-type columns of ``cells`` to the household total.

    Args:
        cells: 100 m cells carrying ``building_type_cols`` (dwelling counts per class),
            ``household_total_col`` and the ``parent_cols`` that are available.
        building_type_cols: the building-type class columns to rescale, e.g. the three
            ``building_type_*`` derived columns.
        household_total_col: the household count per cell.
        parent_cols: parent levels tried in this order for a cell with households but no
            dwelling count; a level absent from ``cells`` is skipped and named in the log.

    Returns:
        ``(result, report)``. ``result`` is a copy of ``cells`` whose ``building_type_cols`` are
        FLOAT targets ``household_total * dwellings_k / sum_k(dwellings)``; cells with a household
        total of 0 or NaN get 0. ``report`` (JSON-safe) holds ``cells_seen``,
        ``cells_with_households``, ``share_tiers`` (per tier ``own`` and per parent level used:
        ``cells`` and ``households``), ``fallback_cells`` and ``fallback_households`` (everything
        not on the ``own`` tier), ``partial_nan_cells`` and ``partial_nan_households`` (cells whose
        class vector is partly NaN, read as 0), ``target_sum_over_household_total_before`` and
        ``..._after`` (``None`` when the frame has no households) and ``levels_unavailable``.

    Raises:
        ValueError: a required column is absent, a count is negative, or a cell with
            households has no dwelling count at its own level or at any parent level.
    """
    building_type_cols = list(building_type_cols)
    required = [*building_type_cols, household_total_col]
    missing = [c for c in required if c not in cells.columns]
    if missing:
        raise ValueError(f"[{LOG_TAG}] required column(s) absent from the cells frame: {missing}")
    if not building_type_cols:
        raise ValueError(f"[{LOG_TAG}] no building-type columns given")

    levels = [c for c in parent_cols if c in cells.columns]
    unavailable = [c for c in parent_cols if c not in cells.columns]

    households = _numeric_nonnegative(cells, [household_total_col], household_total_col)[:, 0]
    dwellings = _numeric_nonnegative(cells, building_type_cols, "building-type dwelling count")
    dwelling_sum = dwellings.sum(axis=1)

    has_households = households > 0
    own = has_households & (dwelling_sum > 0)
    pending = has_households & ~own

    # Dwelling shares per row, filled tier by tier; own shares first.
    shares = np.zeros_like(dwellings)
    shares[own] = dwellings[own] / dwelling_sum[own][:, None]
    tier_counts: dict[str, int] = {"own": int(own.sum())}
    tier_households: dict[str, float] = {"own": float(households[own].sum())}

    dwelling_frame = pd.DataFrame(dwellings, index=cells.index, columns=building_type_cols)
    for level in levels:
        if not pending.any():
            break
        parent_key = cells[level]
        parent_dwellings = dwelling_frame.groupby(parent_key, sort=False).transform("sum")
        parent_sum = parent_dwellings.sum(axis=1).to_numpy(dtype="float64")
        usable = pending & (np.nan_to_num(parent_sum, nan=0.0) > 0)
        parent_values = parent_dwellings.to_numpy(dtype="float64")
        shares[usable] = parent_values[usable] / parent_sum[usable][:, None]
        tier_counts[level] = int(usable.sum())
        tier_households[level] = float(households[usable].sum())
        pending = pending & ~usable

    if pending.any():
        examples = cells.index[pending][:5].tolist()
        raise ValueError(
            f"[{LOG_TAG}] {int(pending.sum())} cell(s) with households have no dwelling count at "
            f"their own level or at any parent level {levels} (row labels e.g. {examples}); "
            "the building-type target cannot be derived. Fix the prepared-cell source."
        )

    targets = households[:, None] * shares
    targets[~has_households] = 0.0

    n_with_households = int(has_households.sum())
    n_fallback_cells = n_with_households - tier_counts["own"]
    household_total = float(households[has_households].sum())
    n_fallback_households = int(round(household_total - tier_households["own"]))
    log_fallback_rate(
        logger, f"{LOG_TAG}.cells", n_fallback_cells, n_with_households,
        what="cells without an own dwelling count took the dwelling shares of a parent level",
    )
    log_fallback_rate(
        logger, f"{LOG_TAG}.households", n_fallback_households, int(round(household_total)),
        what="households in those cells",
    )
    # Partly suppressed class vectors (some classes NaN, some reported) take the own-share
    # tier with NaN read as 0 (ASSUMPTION, ADR-0198 decision 5); counted so that this
    # assumption is never silent.
    class_nan = np.isnan(
        cells[building_type_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype="float64"))
    partly_nan = has_households & class_nan.any(axis=1) & ~class_nan.all(axis=1)
    log_fallback_rate(
        logger, f"{LOG_TAG}.partial_nan_cells", int(partly_nan.sum()), n_with_households,
        what="cells with households whose building-type vector is partly NaN (NaN read as 0)",
    )
    log_fallback_rate(
        logger, f"{LOG_TAG}.partial_nan_households", int(round(float(households[partly_nan].sum()))),
        int(round(household_total)),
        what="households in those cells",
    )
    tier_text = ", ".join(
        f"{name}: {tier_counts[name]} cells / {tier_households[name]:.0f} households"
        for name in tier_counts
    )
    all_households = float(households.sum())
    ratio_before = float(dwelling_sum.sum()) / all_households if all_households else float("nan")
    ratio_after = float(targets.sum()) / all_households if all_households else float("nan")
    logger.info(
        "[%s] shares by level (%s); target sum / household total: before %.4f (dwellings), "
        "after %.4f; levels unavailable in the frame: %s",
        LOG_TAG, tier_text, ratio_before, ratio_after, unavailable or "none",
    )

    result = cells.copy()
    for position, column in enumerate(building_type_cols):
        result[column] = targets[:, position].astype("float64")
    report = {
        "cells_seen": int(len(cells)),
        "cells_with_households": n_with_households,
        "share_tiers": {
            name: {"cells": tier_counts[name], "households": tier_households[name]}
            for name in tier_counts
        },
        "fallback_cells": n_fallback_cells,
        "fallback_households": n_fallback_households,
        "partial_nan_cells": int(partly_nan.sum()),
        "partial_nan_households": int(round(float(households[partly_nan].sum()))),
        "target_sum_over_household_total_before": None if not all_households else ratio_before,
        "target_sum_over_household_total_after": None if not all_households else ratio_after,
        "levels_unavailable": list(unavailable),
    }
    return result, report
