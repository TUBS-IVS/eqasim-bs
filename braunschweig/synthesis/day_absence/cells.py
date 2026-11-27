"""Cells of the day-absence draw: students by home area, other adults by age band (issue #140, ADR-0212).

ADR-0212 amends ADR-0110 (design 2026-11-25 student commuting, A3 "Away from home all day", A24, A25).
The individual stage of :func:`braunschweig.synthesis.day_absence.absence.draw_absence` runs per cell:

* ``student:<area>`` -- a person aged 18 or older whose ``P_TAET`` is the student code, by the home
  area of the household's Kreis (Braunschweig; the surrounding surveyed Kreise; Wolfsburg, which SrV
  does not survey, on the value of the region). Rate: the SrV share of the students of that area
  away from home all day (:func:`braunschweig.data.education.student_flows_inputs.away_share_by_area`).
* ``adult:<band>`` -- every other person aged 18 or older, by age band. Rate: the SrV rate measured
  among the NON-STUDENTS of the band (column ``p_absent_non_student`` of
  ``srv2023_absence_by_age_band.csv``), raw SrV weights (A24).
* ``<band>`` -- a person under 18, in the band and at the rate of the band draw (``p_absent``).

The rate of an adult band as a whole is no longer a target: it follows from the students and the
others the population holds. The household stage is unchanged (by household size).

Import boundary (Phase 4 global constraints): the home areas, the region, the student code and the
loaders come from the leaf :mod:`braunschweig.data.education.student_flows_inputs`; no
``braunschweig.popsim`` or ``srv_student_reference`` module is imported. The MiD no-answer code of
``P_TAET`` is repeated here as :data:`P_TAET_NO_ANSWER` and pinned by ``tests/test_day_absence_cells.py``.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_absence import (ABSENCE_BY_AGE_TABLE, AGE_BAND_BOUNDS, AGE_BAND_LABELS,
                                                  ALL_BAND, age_band)
from braunschweig.data.education import student_flows_inputs as flows_inputs
from braunschweig.synthesis.day_absence.absence import CELL_COLUMN, _check_reference_coverage

logger = logging.getLogger(__name__)
_LOG_TAG = "[day absence cells]"

#: Age (years) from which a person is an adult: students are adults only, persons under it keep their
#: band (design A3). Coincides with a band edge (checked below), so no band holds both.
ADULT_MIN_AGE_YEARS = 18
MINOR_BANDS = tuple(band for band in AGE_BAND_LABELS if AGE_BAND_BOUNDS[band][1] < ADULT_MIN_AGE_YEARS)
ADULT_BANDS = tuple(band for band in AGE_BAND_LABELS if AGE_BAND_BOUNDS[band][0] >= ADULT_MIN_AGE_YEARS)
if set(MINOR_BANDS) | set(ADULT_BANDS) != set(AGE_BAND_LABELS):
    raise RuntimeError(f"{_LOG_TAG} an age band straddles {ADULT_MIN_AGE_YEARS} years: {AGE_BAND_BOUNDS}")

#: Owner ``braunschweig.calibration.mid_student_reference.P_TAET_NO_ANSWER``: the MiD code of no answer.
P_TAET_NO_ANSWER = 99
STUDENT_CELL_PREFIX = "student:"
ADULT_CELL_PREFIX = "adult:"
#: Column of the SrV non-student rate (issue #140, Task 2) in ``srv2023_absence_by_age_band.csv``.
NON_STUDENT_RATE_COLUMN = "p_absent_non_student"
ALL_PERSONS_RATE_COLUMN = "p_absent"
#: Default of the stage key ``day_absence_cells_max_unreadable_p_taet_share``. ASSUMPTION: an adult
#: share above 5 % without a readable activity code is taken to signal a broken column (a guard, not
#: a target); such adults are drawn in the non-student cell of their band.
DEFAULT_MAX_UNREADABLE_P_TAET_SHARE = 0.05


def student_cell(area: str) -> str:
    return f"{STUDENT_CELL_PREFIX}{area}"


def adult_cell(band: str) -> str:
    return f"{ADULT_CELL_PREFIX}{band}"


def load_cell_reference(srv_dir: str, area_of_kreis: Mapping[str, str]) -> dict[str, float]:
    """``{cell: rate}`` for the bands under 18, the adult non-student bands and the student areas.

    The bands under 18 take ``p_absent`` and the adult bands ``p_absent_non_student`` of
    ``srv2023_absence_by_age_band.csv``; the students take
    :func:`~braunschweig.data.education.student_flows_inputs.away_share_by_area` for every area of
    ``area_of_kreis`` (the map of :func:`~braunschweig.data.education.student_flows_inputs.area_by_kreis`,
    Wolfsburg on the region's value). No fallback: a missing column, a missing, repeated or NaN band
    rate, an area without a share or a rate outside [0, 1] raises ``ValueError`` naming the cell.
    """
    path = os.path.join(srv_dir, ABSENCE_BY_AGE_TABLE)
    by_age = pd.read_csv(path, comment="#", encoding="utf-8")
    missing_columns = [c for c in ("band", ALL_PERSONS_RATE_COLUMN, NON_STUDENT_RATE_COLUMN) if c not in by_age.columns]
    if missing_columns:
        raise ValueError(f"{_LOG_TAG} {ABSENCE_BY_AGE_TABLE} lacks the column(s) {missing_columns}")
    bands = by_age[by_age["band"] != ALL_BAND].set_index("band")
    repeated = sorted(set(bands.index[bands.index.duplicated()]))
    absent_bands = sorted(set(AGE_BAND_LABELS) - set(bands.index))
    if repeated or absent_bands:
        raise ValueError(f"{_LOG_TAG} {ABSENCE_BY_AGE_TABLE}: band(s) {absent_bands} missing, {repeated} repeated")
    rates = {band: float(bands.loc[band, ALL_PERSONS_RATE_COLUMN]) for band in MINOR_BANDS}
    rates.update({adult_cell(band): float(bands.loc[band, NON_STUDENT_RATE_COLUMN]) for band in ADULT_BANDS})
    away = flows_inputs.away_share_by_area(srv_dir)
    for area in dict.fromkeys(area_of_kreis.values()):
        if area not in away:
            raise ValueError(f"{_LOG_TAG} no SrV away share of the students for home area {area!r}; "
                             f"areas with a share: {sorted(away)}")
        rates[student_cell(area)] = float(away[area])
    # NaN fails the range check, so a missing rate never reaches the draw as a silent NaN.
    _check_reference_coverage("cell rates", rates, set(rates))
    return rates


def assign_cells(persons: pd.DataFrame, area_of_kreis: Mapping[str, str], *,
                 max_unreadable_share: float = DEFAULT_MAX_UNREADABLE_P_TAET_SHARE) -> pd.Series:
    """``Series[person_id -> cell]`` (name :data:`~braunschweig.synthesis.day_absence.absence.CELL_COLUMN`).

    ``persons`` needs ``person_id``, ``age``, ``P_TAET`` and ``kreis`` (the 5-digit home Kreis). A
    person aged 18 or older with ``P_TAET == STUDENT_P_TAET`` is ``student:<area of kreis>``, any other
    adult ``adult:<band>``, a person under 18 ``<band>`` whatever the code.

    Fallback, counted and logged as a rate: an adult whose ``P_TAET`` is missing or the no-answer code
    cannot be told apart and is drawn in the non-student cell of the band; above
    ``max_unreadable_share`` of the adults it WARNS, and when no adult has a readable code it raises
    (the column is broken, the student cells would be empty). Raises as well on a missing column, a
    missing age, a code that is not a number, or a Kreis outside ``area_of_kreis`` (the region).
    """
    missing = [c for c in ("person_id", "age", "P_TAET", "kreis") if c not in persons.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} persons lack the column(s) {missing}")
    band = age_band(persons["age"]).to_numpy(dtype=object)
    no_age = pd.isna(band)
    if no_age.any():
        raise ValueError(f"{_LOG_TAG} {int(no_age.sum())} persons have no age and no cell")
    kreis = persons["kreis"].astype(object)
    outside = ~kreis.isin(list(area_of_kreis))
    if outside.any():
        raise ValueError(f"{_LOG_TAG} {int(outside.sum())} persons live in a Kreis outside the region "
                         f"{sorted(area_of_kreis)}: {sorted(set(kreis[outside].astype(str)))[:12]}")
    code = pd.to_numeric(persons["P_TAET"], errors="coerce")
    not_a_number = persons["P_TAET"].notna() & code.isna()
    if not_a_number.any():
        raise ValueError(f"{_LOG_TAG} P_TAET holds value(s) that are no code: "
                         f"{persons.loc[not_a_number, 'P_TAET'].astype(str).unique()[:5].tolist()}")
    adult = np.isin(band, ADULT_BANDS)
    unreadable = (code.isna() | (code == P_TAET_NO_ANSWER)).to_numpy()
    student = adult & (code == flows_inputs.STUDENT_P_TAET).to_numpy()

    n_adults, n_unreadable = int(adult.sum()), int((adult & unreadable).sum())
    share = n_unreadable / n_adults if n_adults else 0.0
    logger.info("%s P_TAET of the adults: primary %d/%d (%.2f%%), fallback %d (%.2f%%) without a readable "
                "code, drawn in the non-student cell of their band", _LOG_TAG, n_adults - n_unreadable,
                n_adults, 100.0 * (1.0 - share) if n_adults else 0.0, n_unreadable, 100.0 * share)
    if n_adults and n_unreadable == n_adults:
        raise ValueError(f"{_LOG_TAG} not one of the {n_adults} adults has a readable P_TAET; the column is "
                         "broken and the student cells would stay empty")
    if share > max_unreadable_share:
        logger.warning("%s %.2f%% of the adults have no readable P_TAET, above the bound %.2f%%: check the "
                       "P_TAET column of the enriched persons", _LOG_TAG, 100.0 * share, 100.0 * max_unreadable_share)

    cell = band.copy()
    cell[adult] = [adult_cell(b) for b in band[adult]]
    cell[student] = [student_cell(area_of_kreis[k]) for k in kreis.to_numpy()[student]]
    result = pd.Series(cell, index=pd.Index(persons["person_id"].to_numpy(), name="person_id"),
                       name=CELL_COLUMN, dtype=object)
    counts = result.value_counts()
    logger.info("%s %d persons in %d cells: %s", _LOG_TAG, len(result), len(counts),
                {c: int(n) for c, n in counts.sort_index().items()})
    if n_adults and not student.any():
        logger.warning("%s no adult student (P_TAET == %d) among %d adults: the student cells are empty",
                       _LOG_TAG, flows_inputs.STUDENT_P_TAET, n_adults)
    return result
