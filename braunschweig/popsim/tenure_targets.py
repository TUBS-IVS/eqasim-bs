"""Owner-household targets limited to the dwellings the Zensus owner quote covers (issue #657).

The Zensus 2022 owner quote (share of owner-occupied dwellings) covers only OCCUPIED dwellings
in residential buildings WITHOUT dormitories. The upstream tenure columns apply that quote to
ALL households of a cell, so the households of a dormitory (or a home) in a cell become owner
targets that PopulationSim cannot meet. This module caps the owner households of a cell at the
number of dwellings of the cell::

    D = dwellings of the cell (NaN read as 0)
    if households - D >= min_households_outside:
        owner  = min(owner / households, 1) * min(households, D)
        renter = households - owner

The owner share is clipped at 1 because the household total is the harmonised ``_adj`` column
and the upstream owner count is not, so the owner count can exceed the total where the two
harmonisations differ (the clip guards that case); such changed cells are counted
(``cells_with_owner_above_household_total``).
Every other cell is returned unchanged. ``households - D >= min_households_outside`` means that
at least that many households live outside any dwelling; the threshold keeps small, suppressed
or rounded cells (where ``households - D`` is noise) out of the rule.

ASSUMPTION (issue #657): ``D`` is the rooms-table dwelling count
(``control_spec.OWNER_QUOTE_DWELLING_COLUMN``), a SUPERSET of the dwellings of the owner quote
(all dwellings in buildings with living space, vacant ones included), so the rule can only
under-correct, never over-correct. The full reasoning is in the ADR of issue #657.

The result is float; the integerisation within the 1 km parent runs on it afterwards. Cells
whose dwelling count is NaN are counted as 0 and reported (``cells_with_nan_dwelling_count``,
a fallback rate: primary = cells with a published dwelling count); cells with a NaN owner or
renter count are read as 0 and reported the same way (``cells_with_nan_tenure_count``); a
negative count raises. Only ``owner_col`` and ``renter_col`` of changed cells differ from the
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


def cells_outside_dwellings(
    cells: pd.DataFrame,
    *,
    household_total_col: str,
    dwelling_col: str,
    min_households_outside: float = DEFAULT_MIN_HOUSEHOLDS_OUTSIDE,
) -> pd.Series:
    """The cells the rule selects: ``households - dwellings >= min_households_outside``.

    The one place that defines the selection; ``limit_owner_households_to_dwellings`` uses it, and
    measurements of the rule take their changed-cell mask from it. NaN counts are read as 0 (as the rule
    reads them).

    Returns:
        A bool Series on ``cells.index``.

    Raises:
        ValueError: ``min_households_outside`` is not positive, or a count is negative.
        KeyError: a required column is absent.
    """
    if not min_households_outside > 0:
        raise ValueError(
            f"[{LOG_TAG}] min_households_outside must be > 0, got {min_households_outside!r}")
    missing = [c for c in (household_total_col, dwelling_col) if c not in cells.columns]
    if missing:
        raise KeyError(f"[{LOG_TAG}] required column(s) absent from the cells frame: {missing}")
    households = _count_column(cells, household_total_col)
    dwellings = _count_column(cells, dwelling_col)
    return pd.Series((households - dwellings) >= min_households_outside, index=cells.index)


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
        ``households_moved_owner_to_renter`` (per changed cell the owner count clipped at the
        household total minus the new owner count), ``cells_with_nan_dwelling_count`` (all cells),
        ``cells_changed_with_nan_dwelling_count`` (changed cells whose NaN dwelling count was read
        as 0, i.e. the rule fired because of the fallback), ``cells_with_nan_tenure_count``,
        ``cells_with_owner_above_household_total`` (changed cells) and ``min_households_outside``.

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
    # Unchanged cells keep their input values exactly (a NaN stays NaN); only changed cells differ.
    owner_input = pd.to_numeric(cells[owner_col], errors="coerce").to_numpy(dtype="float64")
    renter_input = pd.to_numeric(cells[renter_col], errors="coerce").to_numpy(dtype="float64")
    nan_dwellings = pd.to_numeric(cells[dwelling_col], errors="coerce").isna().to_numpy()
    nan_tenure = np.isnan(owner_input) | np.isnan(renter_input)

    changed = cells_outside_dwellings(
        cells, household_total_col=household_total_col, dwelling_col=dwelling_col,
        min_households_outside=min_households_outside).to_numpy()
    # ``changed`` implies households > 0 because min_households_outside > 0 and dwellings >= 0.
    safe_households = np.where(changed, households, 1.0)
    # The upstream owner count can exceed the harmonised household total by rounding; the owner
    # share is clipped at 1 so that owner + renter stays the household total.
    owner_above_total = changed & (owners > households)
    owner_share = np.minimum(owners / safe_households, 1.0)
    new_owners = np.where(changed, owner_share * np.minimum(households, dwellings), owner_input)
    new_renters = np.where(changed, households - new_owners, renter_input)

    # The clipped owner count (<= households) is what the new count is derived from, so a clipped
    # cell never reports more moved households than it has.
    moved = float((np.minimum(owners, households)[changed] - new_owners[changed]).sum())
    n_cells = len(cells)
    n_changed = int(changed.sum())
    n_nan_dwelling = int(nan_dwellings.sum())
    n_changed_nan_dwelling = int((changed & nan_dwellings).sum())
    n_nan_tenure = int(nan_tenure.sum())
    n_clipped = int(owner_above_total.sum())
    total_households = float(households.sum())

    # The changed cells are the intended effect of the rule, not a fallback: plain INFO line.
    logger.info(
        "[%s] changed %d of %d cells (%.2f%%); moved %.0f of %.0f households from owner to "
        "renter (%.2f%%); owner count above the household total (clipped) in %d changed cell(s); "
        "threshold %.1f households outside any dwelling",
        LOG_TAG, n_changed, n_cells, 100.0 * n_changed / n_cells if n_cells else 0.0,
        moved, total_households, 100.0 * moved / total_households if total_households else 0.0,
        n_clipped, min_households_outside,
    )
    # The real fallbacks: a NaN count is read as 0 (rate rule:
    # docs/codebase/notes/popsim-fallback-rates.md). Primary = the cell has a published count.
    log_fallback_rate(
        logger, f"{LOG_TAG}.dwelling_count", n_nan_dwelling, n_cells,
        what="cells without a published dwelling count (read as 0 dwellings)",
    )
    log_fallback_rate(
        logger, f"{LOG_TAG}.tenure_count", n_nan_tenure, n_cells,
        what="cells with a NaN owner or renter count (read as 0)",
    )

    result = cells.copy()
    result[owner_col] = new_owners.astype("float64")
    result[renter_col] = new_renters.astype("float64")
    report = {
        "cells_seen": n_cells,
        "cells_changed": n_changed,
        "households_moved_owner_to_renter": moved,
        "cells_with_nan_dwelling_count": n_nan_dwelling,
        "cells_changed_with_nan_dwelling_count": n_changed_nan_dwelling,
        "cells_with_nan_tenure_count": n_nan_tenure,
        "cells_with_owner_above_household_total": n_clipped,
        "min_households_outside": float(min_households_outside),
    }
    return result, report
