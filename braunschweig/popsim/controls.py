"""PopulationSim control marginals: hierarchical integerization.

PopulationSim controls are the per-cell marginal targets (household / population
totals, age x sex cells, ...). For the nested 100 m / 1 km geography the 100 m
control values must be integerized so that, within each 1 km parent, the integer
100 m values sum exactly to the (rounded) 1 km target. This is the hierarchical
consistency the synthesizer's sub-balancing relies on; it is achieved with the
largest-remainder (Hamilton) apportionment, which is deterministic and
total-preserving.

This replaces the inline ``smart_integerize_*`` helpers from the popsimprep
notebook (``PopSimPrep-StartHere-v2.ipynb`` Step 3).
"""

from __future__ import annotations

import math
from typing import Mapping, Optional, Sequence

import pandas as pd


def largest_remainder_round(values: Sequence[float], total: int) -> list[int]:
    """Round ``values`` to integers summing exactly to ``total`` (Hamilton).

    Each value is floored, then the leftover (``total - sum(floors)``) is
    distributed one unit at a time to the entries with the largest fractional
    remainder (ties broken by lowest index). If ``total`` is below the sum of the
    floors, units are removed from the entries with the smallest remainder.

    Parameters
    ----------
    values:
        Non-negative floats to apportion.
    total:
        The exact integer sum the result must have.

    Returns
    -------
    list[int]
        Integers, in the same order as ``values``, summing to ``total``.
    """
    n = len(values)
    if n == 0:
        return []

    floors = [math.floor(v) for v in values]
    remainders = [v - f for v, f in zip(values, floors)]
    result = list(floors)
    diff = total - sum(floors)

    if diff > 0:
        # Hand out +1 to the largest remainders first (stable on ties).
        order = sorted(range(n), key=lambda i: (-remainders[i], i))
        for i in order[:diff]:
            result[i] += 1
    elif diff < 0:
        # Take -1 from the smallest remainders first (stable on ties).
        order = sorted(range(n), key=lambda i: (remainders[i], i))
        for i in order[: -diff]:
            result[i] -= 1

    return result


def integerize_within_parents(
    df: pd.DataFrame,
    *,
    value_col: str,
    parent_col: str,
    targets: Optional[Mapping[str, int]] = None,
) -> pd.Series:
    """Integerize a 100 m control column within each 1 km parent group.

    For each parent the integer 100 m values are made to sum to the parent
    target: ``targets[parent]`` when supplied, otherwise ``round(sum(values))``.
    Row order and index are preserved, so the result can be assigned back as a
    new column.

    Parameters
    ----------
    df:
        Frame of 100 m cells with a ``value_col`` and a ``parent_col``.
    value_col:
        The fractional control column to integerize.
    parent_col:
        The 1 km parent grouping column.
    targets:
        Optional explicit per-parent integer targets (e.g. the rounded 1 km
        control). Parents absent from the mapping fall back to the rounded sum of
        their children.

    Returns
    -------
    pandas.Series
        Integer Series aligned to ``df.index``.
    """
    result = pd.Series(0, index=df.index, dtype="int64")
    for parent, group in df.groupby(parent_col, sort=False):
        values = group[value_col].tolist()
        if targets is not None and parent in targets:
            total = int(targets[parent])
        else:
            total = int(round(float(group[value_col].sum())))
        result.loc[group.index] = largest_remainder_round(values, total)
    return result
