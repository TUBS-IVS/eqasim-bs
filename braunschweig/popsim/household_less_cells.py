"""Move the persons of household-less 100 m cells to the household cells of their square (#659).

Problem: in the integer 100 m control tables some cells carry persons (age x sex bands) but
0 households. PopulationSim drops a sub-zone without households (``empty_sub_zones``) but
keeps its persons in the parent totals, so the person controls of that square can no longer
be met.

Decision (owner, issue #659, "D-cells"): the persons of a household-less cell with fewer than
``max_persons`` persons (default 20) move to the household cells of the same 1 km square, in
proportion to their households. ASSUMPTION: the households of such a cell are suppressed in the
published Zensus table and are counted in the neighbouring cells, so their persons belong to
the neighbours' households. This is an assumption, not a measured fact. "0 households" means
0 after the integerisation of the household totals: a cell whose published household count is 0
(or suppressed) and a cell whose positive float households round to 0 within its square are both
donors (ASSUMPTION: a household share below the rounding of its square is treated like a
suppressed one); the report counts the two kinds separately. Cells with
``max_persons`` or more persons and no household stay unchanged and are reported (likely
institutions); so do donors in a square without any household cell.

Every person column keeps its 1 km total up to float rounding; the caller
(``mid.control_cells.build_control_totals``) passes the rounded pre-move parent totals to the
integerisation (``controls.integerize_within_parents``), so the integer 1 km totals are exactly
those without the move.

What the caller passes (the stage wiring of #659): ``person_cols`` are the 22 age x sex band
columns plus the ten ``EMPLOYED_*_agg`` columns, ``threshold_cols`` the 22 age band columns,
``households`` the INTEGER household total per cell.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def move_persons_to_household_cells(
    frame: pd.DataFrame,
    *,
    parent_col: str,
    households: pd.Series,
    person_cols: Sequence[str],
    threshold_cols: Sequence[str] | None = None,
    max_persons: float = 20.0,
    published_households: pd.Series | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Return a copy of ``frame`` with household-less donor cells emptied, and a report.

    A donor is a cell with 0 households whose persons over ``threshold_cols`` (default
    ``person_cols``) are > 0 and < ``max_persons``. Each ``person_cols`` value of a donor is added
    to the cells of the same ``parent_col`` group that have households, in proportion to their
    households, and set to 0 in the donor. Donors in a group without a household cell and cells
    with ``max_persons`` or more persons stay unchanged and are counted.

    ``households`` (the integer totals that decide who is household-less) and
    ``published_households`` (the float totals before the integerisation; optional) are aligned to
    ``frame.index``. Pure: no I/O, ``frame`` is not mutated.

    The report keys: ``donor_cells``, ``persons_moved`` (over the threshold columns),
    ``large_cells``, ``large_cell_persons``, ``stuck_cells`` (donor cells in a group without a
    household cell; they keep their persons), ``parents_without_households`` (the groups holding
    them) and ``persons_in_parents_without_households``. With ``published_households`` also
    ``donor_cells_published_zero`` and ``donor_cells_rounded_to_zero`` (moved donors whose
    published households are 0, resp. positive but rounded to 0).
    """
    person_cols = list(person_cols)
    threshold_cols = person_cols if threshold_cols is None else list(threshold_cols)
    missing = [c for c in {parent_col, *person_cols, *threshold_cols} if c not in frame.columns]
    if missing:
        raise ValueError(f"frame has no column(s) {sorted(missing)}")
    if not households.index.equals(frame.index):
        raise ValueError("households must be aligned to the frame index")
    if frame[parent_col].isna().any():
        raise ValueError(f"parent column {parent_col!r} has missing values")
    if (households < 0).any() or households.isna().any():
        raise ValueError("households must be non-negative and complete")
    if published_households is not None and not published_households.index.equals(frame.index):
        raise ValueError("published_households must be aligned to the frame index")
    if max_persons <= 0:
        raise ValueError(f"max_persons must be positive, got {max_persons}")

    parent = frame[parent_col]
    hh = households.astype("float64")
    threshold_persons = frame[threshold_cols].sum(axis=1)
    has_persons = (hh == 0) & (threshold_persons > 0)
    large = has_persons & (threshold_persons >= max_persons)
    small = has_persons & ~large
    parent_hh = hh.groupby(parent).transform("sum")
    movable = small & (parent_hh > 0)
    stuck = small & (parent_hh == 0)

    # Share of each household cell in the households of its square (0 elsewhere).
    share = (hh / parent_hh.where(parent_hh > 0)).fillna(0.0).where(hh > 0, 0.0)

    result = frame.copy()
    for col in person_cols:
        values = frame[col].astype("float64")
        donated = values.where(movable, 0.0).groupby(parent).transform("sum")
        result[col] = values.where(~movable, 0.0) + donated * share

    report = {
        "donor_cells": int(movable.sum()),
        "persons_moved": float(threshold_persons[movable].sum()),
        "large_cells": int(large.sum()),
        "large_cell_persons": float(threshold_persons[large].sum()),
        "stuck_cells": int(stuck.sum()),
        "parents_without_households": int(parent[stuck].nunique()),
        "persons_in_parents_without_households": float(threshold_persons[stuck].sum()),
    }
    if published_households is not None:
        published_zero = published_households.fillna(0.0) <= 0
        report["donor_cells_published_zero"] = int((movable & published_zero).sum())
        report["donor_cells_rounded_to_zero"] = int((movable & ~published_zero).sum())
    return result, report
