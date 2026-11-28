"""The student level table as the PopulationSim stage reads it (design A2 of the student layer, ADR-0211).

The table ``eqasim-data/data/braunschweig/popsim/student_level_table.csv`` is built by
``scripts/build_student_balance.py``; its rules and assumptions are owned by ADR-0210 (Decision 5)
and :mod:`braunschweig.calibration.student_level_table` and are not repeated here. It is the ONLY
input of A2: per Kreis, sex and age group the students living there and, next to each, the students
the Zensus counts as employed (the level times the regional share ``omega`` of the header, A12).

This module owns the table's schema (its columns, sexes and age groups) and the mapping of its four
age groups onto the employment-grid age groups (A13), because the PopulationSim stage reads them and
its cache token must cover them; the calibration module that writes the table imports them from
here. Keeping the owner on this side keeps the SrV calibration modules out of the stage's token.

``employed_students_by_grid_group`` gives the persons
:func:`braunschweig.popsim.employment_grid.per_cell_employment_targets` subtracts from the employed
target of the grid groups 16 to 29 and 30 to 39.

ASSUMPTION (year mix): these persons are the model-year level (the enrollment semester carried to the
model year) times the census-year ``omega``, while the Zensus 2022 employed persons they are subtracted
from are of the census year; the subtraction mixes the two years (ADR-0210 Decision 5, ADR-0211).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from braunschweig.data.csv_header import read_header_value
from braunschweig.popsim.zensus_employment_age import AGE_GROUPS

logger = logging.getLogger(__name__)

_LOG_TAG = "[student level]"

#: Path of the level table relative to ``data_path``.
STUDENT_LEVEL_TABLE_RELPATH = "braunschweig/popsim/student_level_table.csv"
#: Header key of the regional share of students counted as employed (A12).
OMEGA_HEADER_KEY = "omega"

# The four age groups of the level (A13) and the two of them that make up the ages 20 to 29.
AGE_LABELS = ("18_19", "20_24", "25_29", "30plus")
# The age groups of the level that go to the grid group holding GRID_AGE_OF_YOUNG_STUDENTS.
YOUNG_AGE_LABELS = ("18_19", "20_24", "25_29")
SEXES = ("female", "male")
# Level sex label -> sex prefix of the employment-grid columns (EMPLOYED_{M,F}_<group>_agg).
SEX_TO_GRID = {"female": "F", "male": "M"}
# A13: the age used to pick the grid group of the students aged 18 to 29 and the group of those
# aged 30 or older.
GRID_AGE_OF_YOUNG_STUDENTS = 20
GRID_GROUP_OLD_STUDENTS = "30_39"

LEVEL_COLUMNS = ["code", "sex", "age_group", "students_persons",
                 "students_counted_employed_persons"]
# ``students_counted_employed_persons`` must equal ``students_persons * omega`` within this
# relative tolerance (the table is written with ten significant digits).
COUNTED_EMPLOYED_RELATIVE_TOLERANCE = 1e-6


def _grid_group_of_age(age: int) -> str:
    for group, low, high in AGE_GROUPS:
        if low <= age <= high:
            return group
    raise ValueError("no employment-grid age group contains age %d (groups %s)"
                     % (age, [group for group, _, _ in AGE_GROUPS]))


def grid_group_by_level_age() -> dict[str, str]:
    """``{level age group: employment-grid age group}`` (A13), derived, never typed.

    ``18_19``, ``20_24`` and ``25_29`` go to the grid group that holds
    :data:`GRID_AGE_OF_YOUNG_STUDENTS`; ``30plus`` goes to :data:`GRID_GROUP_OLD_STUDENTS`, which must
    be a group of ``zensus_employment_age.AGE_GROUPS`` (``ValueError`` otherwise).
    """
    groups = [group for group, _, _ in AGE_GROUPS]
    if GRID_GROUP_OLD_STUDENTS not in groups:
        raise ValueError("GRID_GROUP_OLD_STUDENTS %r is no employment-grid age group %s"
                         % (GRID_GROUP_OLD_STUDENTS, groups))
    young = _grid_group_of_age(GRID_AGE_OF_YOUNG_STUDENTS)
    old_labels = [label for label in AGE_LABELS if label not in YOUNG_AGE_LABELS]
    return {**{label: young for label in YOUNG_AGE_LABELS},
            **{label: GRID_GROUP_OLD_STUDENTS for label in old_labels}}


class StudentLevel(NamedTuple):
    """The level table (columns :data:`LEVEL_COLUMNS`, ``code`` as text) and its ``omega``."""
    table: pd.DataFrame
    omega: float


def read_student_level_table(data_path, expected_kreise: Sequence[str]) -> StudentLevel:
    """Read and check the committed level table under ``data_path``; no fallback.

    Raises ``ValueError`` on a missing file (naming the path and the switch
    ``KEY_STUDENT_EMPLOYMENT_CONCEPT``), a missing column, an unknown sex or age group, a cell of
    ``expected_kreise`` x :data:`SEXES` x :data:`AGE_LABELS` that is missing or present more than
    once, a missing or non-5-digit Kreis ``code``, a non-finite or negative count, ``omega`` absent or outside (0, 1), or
    ``students_counted_employed_persons`` differing from ``students_persons * omega`` by more than
    :data:`COUNTED_EMPLOYED_RELATIVE_TOLERANCE` (relative). Rows of further Kreise are kept.
    """
    # Deferred: the stage package imports this module, so a module-level import would be circular.
    from braunschweig.popsim.stage.config_keys import KEY_STUDENT_EMPLOYMENT_CONCEPT

    path = Path(data_path) / STUDENT_LEVEL_TABLE_RELPATH
    if not path.is_file():
        raise ValueError(
            f"{KEY_STUDENT_EMPLOYMENT_CONCEPT} is on but the student level table {path} does not "
            "exist. It is the only input of the concept (ADR-0211); restore the committed file "
            f"(scripts/build_student_balance.py writes it) or switch {KEY_STUDENT_EMPLOYMENT_CONCEPT} off.")
    try:
        omega = float(read_header_value(path, OMEGA_HEADER_KEY))
    except ValueError as error:
        raise ValueError(f"{_LOG_TAG} {path.name}: no usable omega in the header ({error})") from error
    if not (math.isfinite(omega) and 0.0 < omega < 1.0):
        raise ValueError(f"{_LOG_TAG} {path.name}: omega={omega!r} lies outside (0, 1)")

    table = pd.read_csv(path, comment="#", dtype={"code": str, "sex": str, "age_group": str})
    missing = [column for column in LEVEL_COLUMNS if column not in table.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} {path.name}: missing column(s) {missing}; found "
                         f"{list(table.columns)}")
    table = table[LEVEL_COLUMNS].copy()
    bad_codes = table["code"].isna() | ~table["code"].astype(str).str.fullmatch(r"\d{5}")
    if bad_codes.any():
        raise ValueError(f"{_LOG_TAG} {path.name}: {int(bad_codes.sum())} row(s) without a 5-digit "
                         f"Kreis code: {sorted(map(str, table.loc[bad_codes, 'code'].unique()))[:12]}")
    unknown_sexes = sorted(set(table["sex"]) - set(SEXES))
    unknown_ages = sorted(set(table["age_group"]) - set(AGE_LABELS))
    if unknown_sexes or unknown_ages:
        raise ValueError(f"{_LOG_TAG} {path.name}: unknown sex {unknown_sexes} / age group "
                         f"{unknown_ages}; expected {list(SEXES)} / {list(AGE_LABELS)}")
    for column in ("students_persons", "students_counted_employed_persons"):
        values = pd.to_numeric(table[column], errors="coerce")
        if not (np.isfinite(values) & (values >= 0)).all():
            raise ValueError(f"{_LOG_TAG} {path.name}: non-finite or negative {column}")
        table[column] = values.astype(float)

    counts = table.groupby(["code", "sex", "age_group"]).size()
    duplicated = [key for key, n in counts.items() if n > 1]
    if duplicated:
        raise ValueError(f"{_LOG_TAG} {path.name}: cell(s) present more than once: {duplicated}")
    absent = [(kreis, sex, age) for kreis in expected_kreise for sex in SEXES for age in AGE_LABELS
              if (str(kreis), sex, age) not in counts.index]
    if absent:
        raise ValueError(f"{_LOG_TAG} {path.name}: {len(absent)} (Kreis, sex, age group) cell(s) of "
                         f"the run's Kreise are missing: {absent[:12]}")

    expected = table["students_persons"] * omega
    off = ~np.isclose(table["students_counted_employed_persons"], expected,
                      rtol=COUNTED_EMPLOYED_RELATIVE_TOLERANCE, atol=0.0)
    if off.any():
        rows = table.loc[off, ["code", "sex", "age_group"]].itertuples(index=False, name=None)
        raise ValueError(f"{_LOG_TAG} {path.name}: students_counted_employed_persons differs from "
                         f"students_persons x omega ({omega}) in {int(off.sum())} cell(s): "
                         f"{list(rows)[:12]}")
    logger.info("%s %s: %d cells, %d Kreise, omega %.6g, %.1f students, %.1f counted as employed",
                _LOG_TAG, path, len(table), table["code"].nunique(), omega,
                float(table["students_persons"].sum()),
                float(table["students_counted_employed_persons"].sum()))
    return StudentLevel(table=table.reset_index(drop=True), omega=omega)


def employed_students_by_grid_group(level: StudentLevel) -> dict[tuple[str, str, str], float]:
    """Students counted as employed, keyed ``(Kreis, "M"|"F", grid group)``; groups 16_29 and 30_39.

    The four age groups of the level are summed onto the grid groups of
    :func:`grid_group_by_level_age` (A13). Every Kreis and sex of the table gets both groups.
    """
    grid_of = grid_group_by_level_age()
    mapped = level.table.assign(grid_group=level.table["age_group"].map(grid_of),
                                grid_sex=level.table["sex"].map(SEX_TO_GRID))
    sums = mapped.groupby(["code", "grid_sex", "grid_group"])["students_counted_employed_persons"].sum()
    return {(str(code), sex, group): float(value) for (code, sex, group), value in sums.items()}
