"""Owner-household targets limited to the dwellings the Zensus owner quote covers (issue #657).

The Zensus 2022 owner quote (share of owner-occupied dwellings) covers only OCCUPIED dwellings
in residential buildings WITHOUT dormitories. The upstream tenure columns apply that quote to
ALL households of a cell, so the households of a dormitory (or a home) in a cell become owner
targets that PopulationSim cannot meet. This module caps the owner households of a cell at the
number of dwellings of the cell::

    D = dwellings of the cell (NaN read as 0)
    if households - D >= min_households_outside:
        owner  = owner * min(households, D) / households
        renter = households - owner

Every other cell is returned unchanged. ``households - D >= min_households_outside`` means that
at least that many households live outside any dwelling; the threshold keeps small, suppressed
or rounded cells (where ``households - D`` is noise) out of the rule.

ASSUMPTION (owner decision 2026-11-18): ``D`` is the rooms-table dwelling count
(``control_spec.OWNER_QUOTE_DWELLING_COLUMN``), a SUPERSET of the dwellings of the owner quote
(all dwellings in buildings with living space, vacant ones included), so the rule can only
under-correct, never over-correct. The full reasoning is in the ADR of issue #657.

The result is float; the integerisation within the 1 km parent runs on it afterwards. Cells
whose dwelling count is NaN are counted as 0 and reported (``cells_with_nan_dwelling_count``);
a negative count raises. Only ``owner_col`` and ``renter_col`` of changed cells differ from the
input, which is never mutated.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.fallback_rates import log_fallback_rate

logger = logging.getLogger(__name__)

#: Stage tag of every log line of this module.
LOG_TAG = "popsim.tenure_targets"

#: Default for ``min_households_outside``: households outside any dwelling needed to apply the rule.
DEFAULT_MIN_HOUSEHOLDS_OUTSIDE = 10.0


def _count_column(frame: pd.DataFrame, column: str) -> np.ndarray:
    """Return ``frame[column]`` as a float64 vector, NaN (Zensus suppression) read as 0."""
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype="float64")
    values = np.nan_to_num(values, nan=0.0)
    if (values < 0).any():
        raise ValueError(
            f"[{LOG_TAG}] {int((values < 0).sum())} cell(s) carry a negative value in column "
            f"{column!r}; counts cannot be negative. Check the prepared-cell source."
        )
    return values


def limit_owner_households_to_dwellings(
    cells: pd.DataFrame,
    *,
    owner_col: str,
    renter_col: str,
    household_total_col: str,
    dwelling_col: str,
    min_households_outside: float = DEFAULT_MIN_HOUSEHOLDS_OUTSIDE,
) -> tuple[pd.DataFrame, dict]:
    """Limit the owner households of every cell to the dwellings of the cell.

    Args:
        cells: 100 m cells carrying the four columns below.
        owner_col: owner households per cell (changed cells only).
        renter_col: renter households per cell (changed cells only).
        household_total_col: all households per cell.
        dwelling_col: dwellings per cell; NaN counts as 0 and is reported.
        min_households_outside: a cell is changed only if ``households - dwellings`` is at
            least this many households (> 0).

    Returns:
        ``(result, report)``: ``result`` is a copy of ``cells`` with FLOAT ``owner_col`` and
        ``renter_col``; ``report`` holds ``cells_seen``, ``cells_changed``,
        ``households_moved_owner_to_renter``, ``cells_with_nan_dwelling_count`` and
        ``min_households_outside``.

    Raises:
        ValueError: ``min_households_outside`` is not positive, or a count is negative.
        KeyError: a required column is absent.
    """
    if not min_households_outside > 0:
        raise ValueError(
            f"[{LOG_TAG}] min_households_outside must be > 0, got {min_households_outside!r}")
    required = [owner_col, renter_col, household_total_col, dwelling_col]
    missing = [c for c in required if c not in cells.columns]
    if missing:
        raise KeyError(f"[{LOG_TAG}] required column(s) absent from the cells frame: {missing}")

    households = _count_column(cells, household_total_col)
    owners = _count_column(cells, owner_col)
    renters = _count_column(cells, renter_col)
    dwellings = _count_column(cells, dwelling_col)
    nan_dwellings = pd.to_numeric(cells[dwelling_col], errors="coerce").isna().to_numpy()

    changed = (households - dwellings) >= min_households_outside
    # ``changed`` implies households > 0 because min_households_outside > 0 and dwellings >= 0.
    safe_households = np.where(changed, households, 1.0)
    # Unchanged cells keep their input values exactly (a NaN stays NaN); only changed cells differ.
    owner_input = pd.to_numeric(cells[owner_col], errors="coerce").to_numpy(dtype="float64")
    renter_input = pd.to_numeric(cells[renter_col], errors="coerce").to_numpy(dtype="float64")
    new_owners = np.where(
        changed, owners * np.minimum(households, dwellings) / safe_households, owner_input)
    new_renters = np.where(changed, households - new_owners, renter_input)

    moved = float((owners[changed] - new_owners[changed]).sum())
    n_cells = len(cells)
    n_changed = int(changed.sum())
    n_nan = int(nan_dwellings.sum())
    total_households = float(households.sum())

    # Rate of the rule: the primary path is the owner quote as given, the changed cells are the
    # corrected ones (rate rule: docs/codebase/notes/popsim-fallback-rates.md).
    log_fallback_rate(
        logger, f"{LOG_TAG}.cells", n_changed, n_cells,
        what="cells whose owner households were limited to the dwellings of the cell",
    )
    logger.info(
        "[%s] changed %d of %d cells (%.2f%%); moved %.0f of %.0f households from owner to "
        "renter (%.2f%%); cells with a NaN dwelling count (read as 0): %d; threshold %.1f "
        "households outside any dwelling",
        LOG_TAG, n_changed, n_cells, 100.0 * n_changed / n_cells if n_cells else 0.0,
        moved, total_households, 100.0 * moved / total_households if total_households else 0.0,
        n_nan, min_households_outside,
    )
    if n_nan:
        logger.warning(
            "[%s] %d cell(s) have a NaN dwelling count, read as 0 dwellings (the rule applies to "
            "them when they carry at least %.1f households)",
            LOG_TAG, n_nan, min_households_outside,
        )

    result = cells.copy()
    result[owner_col] = new_owners.astype("float64")
    result[renter_col] = new_renters.astype("float64")
    report = {
        "cells_seen": n_cells,
        "cells_changed": n_changed,
        "households_moved_owner_to_renter": moved,
        "cells_with_nan_dwelling_count": n_nan,
        "min_households_outside": float(min_households_outside),
    }
    return result, report
