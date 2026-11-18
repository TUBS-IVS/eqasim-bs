"""Measure the contradictions between the PopulationSim control tables of one batch (#658).

A PopulationSim batch carries control totals at three geographies: the KREIS table (targets
built from per-Kreis survey shares), the 1 km table and the 100 m cell table (Zensus grid).
Where two tables describe the same population, they can disagree: the KREIS persons of the
education controls need not add up to the persons of the 100 m age x sex bands of the same
Kreis, and a 100 m cell can hold persons but no household.  Such a disagreement cannot be
met by any weighting, so the balancer carries it as a permanent misfit.  This module only
MEASURES the disagreement; it changes no table.

Everything here is pure (no I/O).  The tables are as PopulationSim reads them: one column per
control field (control name plus geography suffix ``_KREIS``, ``_ZENSUS1km``, ``_ZENSUS100m``),
indexed by the zone id.  The ``cells`` frame may hold 100 m rows and 1 km rows together (each
column is filled only for the rows of its own geography); every column is mapped to its Kreis
through the geography crosswalk (columns ``ZENSUS100m``, ``ZENSUS1km``, ``KREIS``).

Pairs and checks whose columns are absent are left out and named in ``DataFrame.attrs["skipped"]``
and in a WARNING, never silently dropped.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.control_spec import (
    _TIER1_HH_SIZE_ENTRIES, _TIER2_BUILDING_TYPE_ENTRIES, _TIER2_TENURE_ENTRIES,
    backbone_age_bands, employment_grid_controls)
from braunschweig.popsim.ownership_grid import BIKES_CATEGORIES, CARS_CATEGORIES

logger = logging.getLogger(__name__)
_TAG = "[popsim_input_consistency]"

SUFFIX_KREIS = "_KREIS"
SUFFIX_1KM = "_ZENSUS1km"
SUFFIX_100M = "_ZENSUS100m"
HH_TOTAL_COLUMN = ("Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
                   + SUFFIX_100M)
# Amounts below this are float noise, not a contradiction.
TOLERANCE = 1e-6
RELATIONS = ("equal", "at_least", "at_most", "ratio")

_BAND_LOWER = {label: (0 if lower is None else lower) for label, lower, _ in backbone_age_bands(True)}


def _band_columns(minimum_lower: int | None = None, below: int | None = None) -> tuple:
    """Age x sex band columns (M then F) whose lower age is >= ``minimum_lower`` and < ``below``."""
    return tuple(
        f"{sex}_AGE_{label}_agg{SUFFIX_100M}" for sex in ("M", "F")
        for label, lower in _BAND_LOWER.items()
        if (minimum_lower is None or lower >= minimum_lower) and (below is None or lower < below))


ALL_BAND_COLUMNS = _band_columns()
UNDER_18_COLUMNS = _band_columns(below=18)
FROM_18_COLUMNS = _band_columns(minimum_lower=18)
FROM_15_COLUMNS = _band_columns(minimum_lower=15)
FROM_10_COLUMNS = _band_columns(minimum_lower=10)
EMPLOYED_COLUMNS = tuple(c.name + SUFFIX_100M for c in employment_grid_controls())
SIZE_COLUMNS = tuple(base + SUFFIX_100M for base, _, _ in _TIER1_HH_SIZE_ENTRIES)
TENURE_COLUMNS = tuple(base + SUFFIX_100M for base, _ in _TIER2_TENURE_ENTRIES)
BUILDING_TYPE_COLUMNS = tuple(name + SUFFIX_100M for name, _, _ in _TIER2_BUILDING_TYPE_ENTRIES)
EMPLOYMENT_LABELS = tuple(kac._EMP_STATUS_CATEGORIES)

_REGISTRY = {control.name: control for control in kac.REGISTRY}


def _kreis_columns(*entry_names: str) -> tuple:
    return tuple(column + SUFFIX_KREIS for name in entry_names
                 for column in kac.control_columns(_REGISTRY[name]))


@dataclass(frozen=True)
class KreisFinePair:
    """One KREIS quantity against the grid quantity that must agree with it.

    ``relation``: ``equal`` (kreis == fine), ``at_least`` (kreis >= fine), ``at_most``
    (kreis <= fine) or ``ratio`` (kreis / fine is reported; no bound).
    """

    label: str
    kreis_controls: tuple
    fine_controls: tuple
    relation: str

    def __post_init__(self):
        if self.relation not in RELATIONS:
            raise ValueError(f"pair {self.label}: relation {self.relation!r} not in {RELATIONS}")


def _build_pairs() -> tuple:
    education = ("education_0_5", "education_6_17")
    work = [c for c in _kreis_columns("work_by_employment") if "_employed_" in c]
    pairs = [
        KreisFinePair("persons_0_17", _kreis_columns(*education), UNDER_18_COLUMNS, "equal"),
        KreisFinePair("persons_18plus", _kreis_columns("education_18plus"), FROM_18_COLUMNS, "equal"),
        KreisFinePair("persons_all", _kreis_columns("trip_class"), ALL_BAND_COLUMNS, "equal"),
        KreisFinePair("households", _kreis_columns("economic_status"), (HH_TOTAL_COLUMN,), "equal"),
        KreisFinePair("employed", ("employed" + SUFFIX_KREIS,), EMPLOYED_COLUMNS, "equal"),
        KreisFinePair("employed_survey", tuple(work), EMPLOYED_COLUMNS, "ratio"),
        KreisFinePair("persons_14plus_low", _kreis_columns("employment_status"),
                      FROM_15_COLUMNS, "at_least"),
        KreisFinePair("persons_14plus_high", _kreis_columns("employment_status"),
                      FROM_10_COLUMNS, "at_most"),
    ]
    for prefix, entry, grid, categories in (
            ("cars", "number_of_cars", "OWN_CARS", CARS_CATEGORIES),
            ("bicycles", "number_of_bicycles", "OWN_BIKES", BIKES_CATEGORIES)):
        for category in categories:
            pairs.append(KreisFinePair(
                f"{prefix}_{category}", (f"{entry}_{category}{SUFFIX_KREIS}",),
                (f"{grid}_{category}_agg{SUFFIX_1KM}",), "equal"))
    return tuple(pairs)


KREIS_FINE_PAIRS: tuple = _build_pairs()
PAIR_COLUMNS = ["KREIS", "pair", "relation", "kreis_sum", "fine_sum", "diff", "rel_diff"]
CELL_CHECK_COLUMNS = ["check", "cells", "amount", "base"]
SUMMARY_COLUMNS = ["scope", "key", "check", "cells", "amount", "base", "share"]


def violation(relation: str, diff: float) -> float:
    """Size of the contradiction of one pair row; ``diff`` is ``kreis_sum - fine_sum``."""
    if relation == "at_least":
        return max(-diff, 0.0)
    if relation == "at_most":
        return max(diff, 0.0)
    return abs(diff)


def _crosswalk_maps(crosswalk: pd.DataFrame) -> dict:
    """Zone id -> Kreis id (strings) for the 100 m and the 1 km geography."""
    maps = {}
    for suffix, column in ((SUFFIX_100M, "ZENSUS100m"), (SUFFIX_1KM, "ZENSUS1km")):
        if column not in crosswalk.columns or "KREIS" not in crosswalk.columns:
            raise ValueError(f"crosswalk lacks the columns {column!r} and 'KREIS' "
                             f"(has {list(crosswalk.columns)})")
        frame = crosswalk[[column, "KREIS"]].astype(str).drop_duplicates(column)
        maps[suffix] = frame.set_index(column)["KREIS"]
    return maps


def _fine_sum_by_kreis(cells: pd.DataFrame, columns: tuple, maps: dict) -> pd.Series:
    suffix = SUFFIX_1KM if columns[0].endswith(SUFFIX_1KM) else SUFFIX_100M
    total = cells[list(columns)].sum(axis=1)
    kreis_of = total.index.map(maps[suffix])
    unmapped = pd.isna(kreis_of)
    lost = float(total[unmapped].abs().sum())
    if lost > TOLERANCE:
        logger.warning("%s %s: %.6g of the grid sum lies in zones missing from the crosswalk "
                       "and is not counted in any Kreis", _TAG, columns[0], lost)
    return total[~unmapped].groupby(np.asarray(kreis_of[~unmapped])).sum()


def kreis_vs_fine(kreis: pd.DataFrame, cells: pd.DataFrame, crosswalk: pd.DataFrame,
                  pairs=KREIS_FINE_PAIRS) -> pd.DataFrame:
    """Per Kreis and pair: the KREIS sum, the grid sum, their difference and relative difference.

    ``diff = kreis_sum - fine_sum``; ``rel_diff = diff / fine_sum`` (NaN for a zero grid sum),
    except for ``ratio`` pairs where ``rel_diff = kreis_sum / fine_sum`` is the ratio itself.
    A pair with an absent column is left out and named in ``.attrs["skipped"]``.
    """
    kreis = kreis.copy()
    kreis.index = kreis.index.astype(str)
    cells = cells.copy()
    cells.index = cells.index.astype(str)
    maps = _crosswalk_maps(crosswalk)
    parts, skipped = [], []
    for pair in pairs:
        missing = ([c for c in pair.kreis_controls if c not in kreis.columns]
                   + [c for c in pair.fine_controls if c not in cells.columns])
        if missing:
            skipped.append(pair.label)
            logger.debug("%s pair %s skipped, absent column(s): %s", _TAG, pair.label, missing)
            continue
        kreis_sum = kreis[list(pair.kreis_controls)].sum(axis=1)
        fine_sum = _fine_sum_by_kreis(cells, pair.fine_controls, maps).reindex(
            kreis_sum.index).fillna(0.0)
        diff = kreis_sum - fine_sum
        denominator = fine_sum.where(fine_sum != 0)
        rel = kreis_sum / denominator if pair.relation == "ratio" else diff / denominator
        parts.append(pd.DataFrame({
            "KREIS": kreis_sum.index, "pair": pair.label, "relation": pair.relation,
            "kreis_sum": kreis_sum.to_numpy(), "fine_sum": fine_sum.to_numpy(),
            "diff": diff.to_numpy(), "rel_diff": rel.to_numpy()}))
    table = (pd.concat(parts, ignore_index=True) if parts
             else pd.DataFrame(columns=PAIR_COLUMNS))
    table.attrs["skipped"] = skipped
    if skipped:
        logger.warning("%s kreis_vs_fine: %d of %d pairs skipped (absent columns): %s", _TAG,
                       len(skipped), len(list(pairs)), skipped)
    return table


def cell_contradictions(cells: pd.DataFrame) -> pd.DataFrame:
    """Contradictions inside the 100 m cells: columns ``check, cells, amount, base``.

    ``cells`` = number of cells with the contradiction, ``amount`` = persons or households
    involved, ``base`` = all persons or households of the cells judged.  Rows without a
    household total (not 100 m cells) are ignored.  The size checks judge only cells with
    households AND persons; the other cases are the two ``..._without_...`` checks.  The
    persons of a cell are the sum of its age x sex bands; a 6+ household counts 6 persons
    at least and has no maximum.
    """
    skipped = []
    if HH_TOTAL_COLUMN not in cells.columns:
        names = ["size_partition", "tenure_partition", "building_type_partition",
                 "persons_below_size_minimum", "persons_above_size_maximum",
                 "persons_without_households", "households_without_persons"]
        logger.warning("%s cell_contradictions: no household total column %s; all checks "
                       "skipped: %s", _TAG, HH_TOTAL_COLUMN, names)
        empty = pd.DataFrame(columns=CELL_CHECK_COLUMNS)
        empty.attrs["skipped"] = names
        return empty
    frame = cells[cells[HH_TOTAL_COLUMN].notna()].fillna(0.0)
    households = frame[HH_TOTAL_COLUMN]
    rows = []

    def have(columns) -> bool:
        return all(c in frame.columns for c in columns)

    def add(check, mask, amount, base):
        rows.append({"check": check, "cells": int(mask.sum()),
                     "amount": float(amount[mask].sum()), "base": float(base)})

    for check, columns in (("size_partition", SIZE_COLUMNS), ("tenure_partition", TENURE_COLUMNS),
                           ("building_type_partition", BUILDING_TYPE_COLUMNS)):
        if not have(columns):
            skipped.append(check)
            continue
        gap = (frame[list(columns)].sum(axis=1) - households).abs()
        add(check, gap > TOLERANCE, gap, households.sum())

    persons = frame[list(ALL_BAND_COLUMNS)].sum(axis=1) if have(ALL_BAND_COLUMNS) else None
    if persons is not None and have(SIZE_COLUMNS):
        sizes = frame[list(SIZE_COLUMNS)]
        weights = np.arange(1, len(SIZE_COLUMNS) + 1, dtype=float)    # last class is 6+: counts 6
        minimum = sizes.to_numpy() @ weights
        judged = (households > 0) & (persons > 0)
        below = pd.Series(np.maximum(minimum - persons.to_numpy(), 0.0), index=frame.index)
        add("persons_below_size_minimum", judged & (below > TOLERANCE), below, persons.sum())
        no_open_class = frame[SIZE_COLUMNS[-1]] == 0
        above = pd.Series(np.maximum(persons.to_numpy() - minimum, 0.0), index=frame.index)
        add("persons_above_size_maximum", judged & no_open_class & (above > TOLERANCE), above,
            persons.sum())
    else:
        skipped += ["persons_below_size_minimum", "persons_above_size_maximum"]
    if persons is not None:
        add("persons_without_households", (persons > 0) & (households <= 0), persons,
            persons.sum())
        add("households_without_persons", (households > 0) & (persons <= 0), households,
            households.sum())
    else:
        skipped += ["persons_without_households", "households_without_persons"]
    table = pd.DataFrame(rows, columns=CELL_CHECK_COLUMNS)
    table.attrs["skipped"] = skipped
    if skipped:
        logger.warning("%s cell_contradictions: checks skipped (absent columns): %s", _TAG,
                       skipped)
    return table


def summarize(kreis: pd.DataFrame, cells: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Both measurements in one long frame ``scope, key, check, cells, amount, base, share``.

    ``scope="kreis"``: one row per Kreis (``key``) and pair (``check``); ``amount`` is the
    size of the contradiction (``violation``), ``cells`` is 1 if it exceeds the tolerance,
    ``base`` the grid sum.  ``scope="cell"``: one row per cell check (``key="all"``).
    ``share = amount / base`` (NaN for a zero base).
    """
    pairs = kreis_vs_fine(kreis, cells, crosswalk)
    kreis_rows = pd.DataFrame({
        "scope": "kreis", "key": pairs["KREIS"], "check": pairs["pair"],
        "amount": [violation(r, d) for r, d in zip(pairs["relation"], pairs["diff"])],
        "base": pairs["fine_sum"]})
    kreis_rows["cells"] = (kreis_rows["amount"] > TOLERANCE).astype(int)
    cell_rows = cell_contradictions(cells)
    cell_rows.insert(0, "key", "all")
    cell_rows.insert(0, "scope", "cell")
    long = pd.concat([kreis_rows, cell_rows], ignore_index=True)
    long["share"] = long["amount"] / long["base"].where(long["base"] != 0)
    long = long[SUMMARY_COLUMNS]
    long.attrs["skipped"] = list(pairs.attrs["skipped"])
    return long
