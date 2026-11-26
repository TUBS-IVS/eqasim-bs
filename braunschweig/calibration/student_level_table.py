"""Student level table, omega and the level gate against SrV (issue #140, Phase 2).

Pure builders over already-read tables (no I/O, no synpp); the CLI of the balance owns the files,
the provenance headers and the sha256 of every input. The students living in each Kreis
(``students_living_persons`` of ``by_home_kreis`` in :mod:`braunschweig.data.education.
student_od_table`, the balance anchored on official enrollment) are the LEVEL. SrV is the
cross-check, not the level: :func:`gate_table` shows where the two differ by more than
:data:`student_control_evidence.MIN_DIFFERENCE_STANDARD_ERRORS` SrV standard errors and
:func:`gate_triggered` says whether the owner has to decide on it;
:func:`gate_triggered_area_consistent` applies the same rule to the whole-Kreis comparison of the
students aged 20 to 29 (:data:`SRV_COVERAGE_NOTE`).

Assumptions (numbers follow the design of issue #140; every one is an ASSUMPTION, not a sourced
value):

* A9 -- a level cell is the Kreis level times the regional age share times the regional sex share.
  Age and sex are taken as independent because the SrV parameter table has no joint cell.
* A11 -- the SrV student count is a count of students at home on the reporting day. Students away
  all day are added with ``count / (1 - away_all_day_share)`` (the share of the SrV students
  away from home all day, pooled over the seven surveyed Kreise, applied to every Kreis).
* A12 -- ``omega``, the share of the students that the Zensus counts as employed:
  ``1 - Z / S``. ``Z`` = the Zensus count of the "pupils and students without paid work" of the
  eight Kreise, aged 20 to 29, times the MiD ratio of students to pupils and students for Germany
  (the Zensus group contains pupils, MiD separates them); ``S`` = the students living in the eight
  Kreise times the SrV share of the ages 20 to 29 among the adult students. Both sides describe
  the students aged 20 to 29. Omega is one number for all Kreise.
* A13 -- the grid group of the employment target for students aged 18 to 29 is the group of
  :data:`braunschweig.popsim.zensus_employment_age.AGE_GROUPS` that contains age 20 (``16_29``);
  students aged 30 or older go to ``30_39``.
* A16 -- the balance counts enrolled students; the design assumes their number equals the number
  of students by main activity (the survey status). Two known errors pull in opposite directions:
  enrolled persons whose main activity is a job (in the balance, not students in the survey) and
  students at institutions outside the table (students in the survey, not in the balance).

The level table carries no day factor: it counts heads, never days.

Eight Kreise versus seven: omega and the level table cover all eight ZGB Kreise (the Zensus and the
balance both cover Wolfsburg, 03103). The gate compares with SrV, which did not survey
Wolfsburg, so its pooled row (:data:`student_control_evidence.REGION_CODE`) is the seven surveyed
Kreise, and Wolfsburg's SrV side is NaN, never dropped.

The standard error of the SrV count is ``srv_students_incl_away_persons *
se_student_share_adults / student_share_adults``: the relative error of the student share. The
away share has its own sampling error, which this ignores (the header written by the CLI says so).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.calibration.srv_student_reference import BRAUNSCHWEIG_KREIS
from braunschweig.calibration.student_control_evidence import (
    CENSUS_AGE_GROUP, MID_GERMANY_CODE, MIN_DIFFERENCE_STANDARD_ERRORS, REGION_CODE)
# Read-only: the age-class mapping of the PopulationSim employment grid has no public name. It must
# stay the single owner of "which Regionaltabelle class is in which grid group"; the module is part
# of the cache token of that stage and is never edited from here. The CLI reads the Regionaltabelle
# file with ``read_kreis_employed_by_age`` of the same module and passes the frame in.
from braunschweig.popsim.zensus_employment_age import AGE_GROUPS, _KREIS_CLASS_TO_GROUP

logger = logging.getLogger(__name__)

_LOG_TAG = "[student level table]"

# The two gate rows that decide whether the owner has to rule on the level (design A2).
_TRIGGER_CODES = (BRAUNSCHWEIG_KREIS, REGION_CODE)

# Age labels of the SrV parameter table (row ``who,age_group,students_present``) and the two level
# age groups they fold into. 18_19 + 20_24 + 25_29 = 18_29.
AGE_LABELS = ("18_19", "20_24", "25_29", "30plus")
AGE_LABELS_20_29 = ("20_24", "25_29")
LEVEL_AGE_GROUPS = {"18_29": ("18_19", "20_24", "25_29"), "30plus": ("30plus",)}
SEXES = ("female", "male")
# Level sex label -> sex code of the Zensus Regionaltabelle frame (M / F).
_SEX_TO_ZENSUS = {"female": "F", "male": "M"}
# A13: the age used to pick the grid group of the students aged 18 to 29 and the group of those
# aged 30 or older.
GRID_AGE_OF_YOUNG_STUDENTS = 20
GRID_GROUP_OLD_STUDENTS = "30_39"
# A share vector must sum to 1 within this absolute tolerance.
SHARE_SUM_TOLERANCE = 1e-6

LEVEL_COLUMNS = ["code", "sex", "age_group", "students_persons",
                 "students_counted_employed_persons"]
PRECHECK_COLUMNS = ["code", "sex", "grid_age_group", "zensus_employed_persons",
                    "students_counted_employed_persons", "remainder_persons"]
GATE_COLUMNS = [
    "code", "balance_students_persons", "srv_students_weighted_persons", "srv_away_all_day_share",
    "srv_students_incl_away_persons", "srv_se_students_incl_away_persons", "difference_persons",
    "difference_in_srv_se", "beyond_two_se", "balance_students_20_29_persons",
    "srv_estimate_students_20_29_persons", "srv_se_estimate_students_20_29_persons",
    "difference_20_29_persons", "difference_20_29_in_srv_se", "beyond_two_se_20_29",
    "census_estimate_students_20_29_persons", "model_today_students_persons"]
# What each SrV side of the gate covers. The CLI writes it into the header of the gate table (this
# module is the one owner of the wording).
SRV_COVERAGE_NOTE = (
    "Two cross-checks with opposite blind spots: srv_students_weighted_persons counts only the surveyed "
    "municipalities of a Kreis (gate_triggered); srv_estimate_students_20_29_persons covers the whole Kreis "
    "(SrV student share 20-29 times the Zensus population 20-29) but applies the share of the surveyed "
    "municipalities to all of them (gate_triggered_area_consistent).")
# Note on one gate column. The CLI task writes it into the header of the gate table (this module
# is the one owner of the wording).
AWAY_SHARE_COLUMN_NOTE = (
    "srv_away_all_day_share is ONE value, the share of the SrV students away from home all day "
    "pooled over the seven surveyed Kreise (A11), repeated on every surveyed row; it is not a "
    "per-Kreis measurement.")


# --------------------------------------------------------------------------- helpers
def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s: missing required column(s) %s" % (name, missing))


def _unique_rows(frame: pd.DataFrame, key: str, name: str) -> pd.DataFrame:
    duplicated = sorted(set(frame.loc[frame[key].duplicated(keep=False), key].astype(str)))
    if duplicated:
        raise ValueError("%s: duplicated %s value(s) %s" % (name, key, duplicated[:10]))
    return frame.set_index(key)


def _checked_shares(shares: pd.Series, labels: Sequence[str], name: str) -> pd.Series:
    """``shares`` indexed by exactly ``labels``, finite, non-negative, summing to 1."""
    if not isinstance(shares, pd.Series):
        raise ValueError("%s: expected a pandas Series indexed by %s, got %s"
                         % (name, list(labels), type(shares).__name__))
    missing = [label for label in labels if label not in shares.index]
    extra = [label for label in shares.index if label not in labels]
    if missing or extra:
        raise ValueError("%s: labels %s missing, %s unexpected (expected %s)"
                         % (name, missing, extra, list(labels)))
    values = shares.loc[list(labels)].astype(float)
    if not (np.isfinite(values) & (values >= 0)).all():
        raise ValueError("%s: non-finite or negative share(s) %s" % (name, values.to_dict()))
    if abs(float(values.sum()) - 1.0) > SHARE_SUM_TOLERANCE:
        raise ValueError("%s: shares sum to %.10g, expected 1 (%s)"
                         % (name, float(values.sum()), values.to_dict()))
    return values


def _checked_living(students_living: pd.Series) -> pd.Series:
    if not isinstance(students_living, pd.Series):
        raise ValueError("students_living: expected a pandas Series indexed by Kreis code, got %s"
                         % type(students_living).__name__)
    if students_living.index.has_duplicates:
        raise ValueError("students_living: duplicated Kreis code(s) %s"
                         % sorted(set(students_living.index[students_living.index.duplicated()])))
    values = students_living.astype(float)
    bad = values[~(np.isfinite(values) & (values >= 0))]
    if len(bad):
        raise ValueError("students_living: non-finite or negative value(s) %s" % bad.to_dict())
    return values


def _checked_omega(omega_value: float) -> float:
    if not (np.isfinite(omega_value) and 0.0 < omega_value < 1.0):
        raise ValueError("omega %r is outside (0, 1): the students counted as employed would be "
                         "negative or all of them" % (omega_value,))
    return float(omega_value)


# --------------------------------------------------------------------------- omega
def omega(students_living: pd.Series, zensus_students: pd.DataFrame, mid_shares: pd.DataFrame,
          age_shares: pd.Series) -> tuple[float, dict]:
    """``(omega, parts)`` with ``omega = 1 - Z / S`` (A12), the share counted as employed.

    ``students_living``: students living in each Kreis, indexed by Kreis code, covering exactly the
    eight :data:`srv_distance_targets.ZGB_KREISE`. ``zensus_students``: the Zensus student table
    (``region``, ``age_group``, ``students_nonemployed_persons``; the eight Kreise at
    :data:`student_control_evidence.CENSUS_AGE_GROUP`; other regions are ignored).
    ``mid_shares``: ``mid2023_student_share_20_29_by_land.csv`` (``region``, ``share_students``,
    ``share_pupils_and_students``; the row :data:`mid_student_reference.REGION_GERMANY` is used).
    ``age_shares``: the SrV share of each age group among the adult students, indexed by
    :data:`AGE_LABELS`, summing to 1.

    ``Z`` = the Zensus count of the eight Kreise times ``share_students /
    share_pupils_and_students`` (MiD, Germany); ``S`` = the sum of ``students_living`` over the
    eight Kreise times the share of ``20_24`` + ``25_29``. ``parts`` carries ``Z``, ``S``, the two
    ratios (``mid_students_to_pupils_and_students_ratio``,
    ``srv_age_share_20_29_among_adult_students``) and the two counts they apply to. Raises
    ``ValueError`` on a missing or duplicated Kreis, column or region, a wrong age group, a negative
    or non-finite input, or an omega outside (0, 1) (naming Z and S).
    """
    living = _checked_living(students_living)
    missing = [code for code in ZGB_KREISE if code not in living.index]
    extra = sorted(set(living.index) - set(ZGB_KREISE))
    if missing or extra:
        raise ValueError("omega: students_living misses Kreis(e) %s and holds %s outside the ZGB; "
                         "omega covers exactly the eight Kreise %s" % (missing, extra,
                                                                      list(ZGB_KREISE)))
    shares = _checked_shares(age_shares, AGE_LABELS, "omega age_shares")
    _require_columns(zensus_students, ["region", "age_group", "students_nonemployed_persons"],
                     "omega zensus_students")
    _require_columns(mid_shares, ["region", "share_students", "share_pupils_and_students"],
                     "omega mid_shares")
    zensus = _unique_rows(zensus_students, "region", "omega zensus_students")
    mid = _unique_rows(mid_shares, "region", "omega mid_shares")
    absent = [code for code in ZGB_KREISE if code not in zensus.index]
    if absent:
        raise ValueError("omega zensus_students: region(s) %s missing" % absent)
    if MID_GERMANY_CODE not in mid.index:
        raise ValueError("omega mid_shares: region %r missing" % MID_GERMANY_CODE)
    used = zensus.loc[list(ZGB_KREISE)]
    wrong_age = sorted(set(used["age_group"]) - {CENSUS_AGE_GROUP})
    if wrong_age:
        raise ValueError("omega zensus_students: age_group %s, expected %r for the eight Kreise"
                         % (wrong_age, CENSUS_AGE_GROUP))
    counts = pd.to_numeric(used["students_nonemployed_persons"], errors="coerce")
    if not (np.isfinite(counts) & (counts >= 0)).all():
        raise ValueError("omega zensus_students: non-finite or negative "
                         "students_nonemployed_persons %s" % counts[~(counts >= 0)].to_dict())

    students = float(mid.loc[MID_GERMANY_CODE, "share_students"])
    pupils_and_students = float(mid.loc[MID_GERMANY_CODE, "share_pupils_and_students"])
    if not (0.0 < students <= pupils_and_students):
        raise ValueError("omega mid_shares: region %s has share_students %r and "
                         "share_pupils_and_students %r; need 0 < students <= pupils and students"
                         % (MID_GERMANY_CODE, students, pupils_and_students))
    ratio = students / pupils_and_students

    share_20_29 = float(shares.loc[list(AGE_LABELS_20_29)].sum())
    zensus_total = float(counts.sum())
    living_total = float(living.loc[list(ZGB_KREISE)].sum())
    z_value = zensus_total * ratio
    s_value = living_total * share_20_29
    if not (z_value >= 0 and s_value > 0):
        raise ValueError("omega: Z = %r and S = %r; S must be positive (students living %r, "
                         "SrV share of the ages 20 to 29 %r)"
                         % (z_value, s_value, living_total, share_20_29))
    value = 1.0 - z_value / s_value
    if not 0.0 < value < 1.0:
        raise ValueError("omega = %.6g is outside (0, 1): Z = %.6g (Zensus %.6g x MiD ratio %.6g), "
                         "S = %.6g (students living %.6g x SrV share 20-29 %.6g)"
                         % (value, z_value, zensus_total, ratio, s_value, living_total,
                            share_20_29))
    logger.info("%s omega = %.4f = 1 - Z / S, Z = %.1f (Zensus %.1f x MiD ratio %.4f), S = %.1f "
                "(students living %.1f x SrV share 20-29 %.4f), over the %d Kreise %s", _LOG_TAG,
                value, z_value, zensus_total, ratio, s_value, living_total, share_20_29,
                len(ZGB_KREISE), list(ZGB_KREISE))
    return float(value), {
        "Z": z_value, "S": s_value, "mid_students_to_pupils_and_students_ratio": ratio,
        "srv_age_share_20_29_among_adult_students": share_20_29,
        "zensus_students_nonemployed_persons": zensus_total,
        "students_living_persons": living_total}


# --------------------------------------------------------------------------- level table
def level_table(students_living: pd.Series, age_shares: pd.Series, sex_shares: pd.Series,
                omega_value: float) -> pd.DataFrame:
    """The student level by Kreis, sex and age group, with the share counted as employed (A9).

    ``students_living``: students living per Kreis (index = Kreis code, in the order kept).
    ``age_shares``: SrV shares by :data:`AGE_LABELS`; ``sex_shares``: SrV shares by :data:`SEXES`;
    both sum to 1. Age groups ``18_29`` (``18_19`` + ``20_24`` + ``25_29``) and ``30plus``. A cell
    is the Kreis level times its age share times its sex share (ASSUMPTION: age and sex
    independent, the SrV parameter table has no joint cell), so the cells of a Kreis sum to its
    level. ``students_counted_employed_persons = students_persons * omega_value``. Columns
    :data:`LEVEL_COLUMNS`; rows ordered by Kreis, then sex, then age group. Raises ``ValueError`` on
    shares that are not complete or do not sum to 1, a negative or non-finite level, or an omega
    outside (0, 1).
    """
    living = _checked_living(students_living)
    ages = _checked_shares(age_shares, AGE_LABELS, "level_table age_shares")
    sexes = _checked_shares(sex_shares, SEXES, "level_table sex_shares")
    value = _checked_omega(omega_value)
    group_share = {group: float(ages.loc[list(labels)].sum())
                   for group, labels in LEVEL_AGE_GROUPS.items()}
    rows = []
    for code, level in living.items():
        for sex in SEXES:
            for group, share in group_share.items():
                persons = float(level) * share * float(sexes.loc[sex])
                rows.append((str(code), sex, group, persons, persons * value))
    table = pd.DataFrame(rows, columns=LEVEL_COLUMNS)
    logger.info("%s level table: %d Kreise, %d cells, %.1f students, %.1f counted as employed "
                "(omega %.4f; age and sex taken as independent, A9)", _LOG_TAG, len(living),
                len(table), float(table["students_persons"].sum()),
                float(table["students_counted_employed_persons"].sum()), value)
    return table


# --------------------------------------------------------------------------- employed pre-check
def _grid_group_of_age(age: int) -> str:
    for group, low, high in AGE_GROUPS:
        if low <= age <= high:
            return group
    raise ValueError("no employment-grid age group contains age %d (groups %s)"
                     % (age, [group for group, _, _ in AGE_GROUPS]))


def employed_target_precheck(level: pd.DataFrame, zensus_employed: pd.DataFrame) -> pd.DataFrame:
    """Zensus employed persons against the students counted as employed, per Kreis, sex, grid group.

    ``level``: :func:`level_table` output. ``zensus_employed``: the Regionaltabelle frame of
    :func:`braunschweig.popsim.zensus_employment_age.read_kreis_employed_by_age` (``region``,
    ``age_class``, ``sex`` M/F, ``employed_persons``). A level cell goes to the grid group that
    holds age 20 for ``18_29`` and to ``30_39`` for ``30plus`` (A13); the Zensus employed count of a
    grid group is the sum of the classes that group takes in the employment grid. Columns
    :data:`PRECHECK_COLUMNS`; ``remainder_persons`` = Zensus employed - students counted as
    employed, the room left for the employed persons that are not students. A negative remainder
    raises (design A2: the employment target cannot hold the students). Raises ``ValueError`` too
    on a missing column, a Kreis or sex without Zensus rows, an unknown age class or sex, or a
    non-finite or negative count.
    """
    _require_columns(level, LEVEL_COLUMNS, "employed pre-check level")
    _require_columns(zensus_employed, ["region", "age_class", "sex", "employed_persons"],
                     "employed pre-check zensus_employed")
    unknown_classes = sorted(set(zensus_employed["age_class"]) - set(_KREIS_CLASS_TO_GROUP))
    unknown_sexes = sorted(set(zensus_employed["sex"]) - set(_SEX_TO_ZENSUS.values()))
    if unknown_classes or unknown_sexes:
        raise ValueError("employed pre-check zensus_employed: unknown age classes %s / sexes %s; "
                         "expected %s and %s" % (unknown_classes, unknown_sexes,
                                                 list(_KREIS_CLASS_TO_GROUP),
                                                 list(_SEX_TO_ZENSUS.values())))
    counts = pd.to_numeric(zensus_employed["employed_persons"], errors="coerce")
    if not (np.isfinite(counts) & (counts >= 0)).all():
        raise ValueError("employed pre-check zensus_employed: non-finite or negative "
                         "employed_persons")
    unknown_age_groups = sorted(set(level["age_group"]) - set(LEVEL_AGE_GROUPS))
    unknown_level_sexes = sorted(set(level["sex"]) - set(SEXES))
    if unknown_age_groups or unknown_level_sexes:
        raise ValueError("employed pre-check level: age_group %s / sex %s outside %s / %s"
                         % (unknown_age_groups, unknown_level_sexes, list(LEVEL_AGE_GROUPS),
                            list(SEXES)))
    grid_of = {"18_29": _grid_group_of_age(GRID_AGE_OF_YOUNG_STUDENTS),
               "30plus": GRID_GROUP_OLD_STUDENTS}
    zensus = zensus_employed.assign(
        employed_persons=counts, grid_age_group=zensus_employed["age_class"].map(
            _KREIS_CLASS_TO_GROUP))
    mapped = level.assign(grid_age_group=level["age_group"].map(grid_of))
    counted = mapped.groupby(["code", "sex", "grid_age_group"], sort=False)[
        "students_counted_employed_persons"].sum()

    rows = []
    for (code, sex, group), counted_persons in counted.items():
        part = zensus[(zensus["region"] == code) & (zensus["sex"] == _SEX_TO_ZENSUS[sex])]
        if part.empty:
            raise ValueError("employed pre-check: no Zensus employed rows for Kreis %s, sex %s"
                             % (code, _SEX_TO_ZENSUS[sex]))
        employed = float(part.loc[part["grid_age_group"] == group, "employed_persons"].sum())
        rows.append((code, sex, group, employed, float(counted_persons),
                     employed - float(counted_persons)))
    table = pd.DataFrame(rows, columns=PRECHECK_COLUMNS)
    negative = table[table["remainder_persons"] < 0]
    if len(negative):
        raise ValueError(
            "employed pre-check: the students counted as employed exceed the Zensus employed "
            "persons in %d cell(s) (design A2): %s" % (len(negative), [
                (row.code, row.sex, row.grid_age_group, row.zensus_employed_persons,
                 row.students_counted_employed_persons) for row in negative.itertuples()]))
    logger.info("%s employed pre-check: %d cells, smallest remainder %.1f persons, all >= 0",
                _LOG_TAG, len(table), float(table["remainder_persons"].min()))
    return table


# --------------------------------------------------------------------------- gate against SrV
def gate_table(level_gate: pd.DataFrame, srv_shares: pd.DataFrame, away_share: float,
               age_shares: pd.Series, model_today: pd.Series) -> pd.DataFrame:
    """The balance next to the SrV student count per Kreis and pooled; SrV is the cross-check only.

    ``level_gate``: :func:`student_control_evidence.build_level_gate` output called with
    ``residents_implied`` (the eight Kreise and the pooled :data:`REGION_CODE` row of the seven
    surveyed ones; its ``residents_implied_by_enrollment_persons`` must be filled).
    ``srv_shares``: ``srv2023_student_share_by_kreis.csv`` (``code``, ``se_student_share_adults``).
    ``away_share``: the share of the SrV students away from home all day, in [0, 1). ``age_shares``:
    the SrV age shares by :data:`AGE_LABELS`. ``model_today``: the students per ``code`` the model
    holds today (a Series indexed by every ``code`` of ``level_gate``, the pooled one included).

    Columns :data:`GATE_COLUMNS`. ``balance_students_persons`` is the level gate's
    ``residents_implied_by_enrollment_persons``. ``srv_students_incl_away_persons`` =
    ``srv_students_weighted_persons / (1 - away_share)`` (A11, the students at home on the reporting
    day plus those away all day); its standard error is that value times ``se_student_share_adults
    / student_share_adults`` (the away share's own error is ignored). ``difference_in_srv_se`` =
    ``(balance - SrV) / standard error``, ``beyond_two_se`` when its absolute value exceeds
    :data:`student_control_evidence.MIN_DIFFERENCE_STANDARD_ERRORS` (False where not evaluable).
    ``srv_away_all_day_share`` is ONE value pooled over the seven surveyed Kreise (A11), repeated
    on every surveyed row, not a per-Kreis measurement (:data:`AWAY_SHARE_COLUMN_NOTE` is the
    sentence for the output header). ``balance_students_20_29_persons`` = balance times the SrV
    share of the ages 20 to 29; the two 20-29 estimates are the level gate's columns, unchanged.

    The area-consistent comparison (ages 20 to 29): ``srv_se_estimate_students_20_29_persons`` =
    ``srv_se_student_share_20_29 * census_population_20_29_persons``; ``difference_20_29_persons`` =
    ``balance_students_20_29_persons - srv_estimate_students_20_29_persons``,
    ``difference_20_29_in_srv_se`` that difference over the standard error (NaN where it is not
    positive), ``beyond_two_se_20_29`` as ``beyond_two_se``. The two SrV sides have opposite blind
    spots (:data:`SRV_COVERAGE_NOTE`): ``srv_students_weighted_persons`` covers only the surveyed
    municipalities of a Kreis; the 20-29 estimate covers the whole Kreis (Zensus population) but
    applies the share of the surveyed municipalities to all. Both are cross-checks;
    :func:`gate_triggered` reads the first, :func:`gate_triggered_area_consistent` the second.

    An unsurveyed Kreis (Wolfsburg, no SrV share) keeps its row with NaN on the SrV side. Raises
    ``ValueError`` on a missing column or code, an unfilled or negative balance, an away share
    outside [0, 1), a surveyed row without a positive student share or a finite standard error, a
    missing or non-finite model value, or a surveyed Braunschweig or pooled row whose standard
    error is exactly 0 (a row that decides the trigger cannot be evaluated). A surveyed row of
    another Kreis with standard error 0 gets NaN ``difference_in_srv_se`` and ``beyond_two_se``
    False, counted and warned about.
    """
    gate_inputs = ["code", "residents_implied_by_enrollment_persons",
                   "srv_students_weighted_persons", "srv_student_share_adults",
                   "srv_estimate_students_20_29_persons", "srv_se_student_share_20_29",
                   "census_population_20_29_persons", "census_estimate_students_20_29_persons"]
    _require_columns(level_gate, gate_inputs, "gate level_gate")
    _require_columns(srv_shares, ["code", "se_student_share_adults"], "gate srv_shares")
    expected = [*ZGB_KREISE, REGION_CODE]
    codes = list(level_gate["code"])
    if sorted(codes) != sorted(expected):
        raise ValueError("gate level_gate: codes %s, expected exactly %s" % (codes, expected))
    if not np.isfinite(away_share) or not (0.0 <= away_share < 1.0):
        raise ValueError("gate: away_share %r is outside [0, 1)" % (away_share,))
    shares = _checked_shares(age_shares, AGE_LABELS, "gate age_shares")
    share_20_29 = float(shares.loc[list(AGE_LABELS_20_29)].sum())
    se_by_code = _unique_rows(srv_shares, "code", "gate srv_shares")["se_student_share_adults"]
    absent = [code for code in codes if code not in se_by_code.index]
    if absent:
        raise ValueError("gate srv_shares: code(s) %s missing" % absent)
    model = pd.Series(model_today, dtype=float)
    absent_model = [code for code in codes if code not in model.index]
    if absent_model:
        raise ValueError("gate model_today: code(s) %s missing" % absent_model)
    if not np.isfinite(model.loc[codes]).all():
        raise ValueError("gate model_today: non-finite value(s) %s"
                         % model.loc[codes][~np.isfinite(model.loc[codes])].to_dict())

    frame = level_gate.reset_index(drop=True)
    balance = frame["residents_implied_by_enrollment_persons"].astype(float)
    if not (np.isfinite(balance) & (balance >= 0)).all():
        raise ValueError("gate level_gate: residents_implied_by_enrollment_persons is NaN or "
                         "negative for %s; call build_level_gate with residents_implied"
                         % list(frame.loc[~(balance >= 0), "code"]))
    share_adults = frame["srv_student_share_adults"].astype(float)
    surveyed = share_adults.notna()
    se_share = frame["code"].map(se_by_code).astype(float)
    bad = frame[surveyed & ~((share_adults > 0) & np.isfinite(se_share) & (se_share >= 0))]
    if len(bad):
        raise ValueError("gate: surveyed row(s) %s without a positive student share or a finite "
                         "standard error (share %s, se %s)"
                         % (list(bad["code"]), share_adults[bad.index].to_dict(),
                            se_share[bad.index].to_dict()))

    weighted = frame["srv_students_weighted_persons"].astype(float).where(surveyed)
    incl_away = weighted / (1.0 - away_share)
    se = incl_away * se_share / share_adults
    difference = balance - incl_away
    zero_se = surveyed & (se == 0)
    decisive = [code for code in frame.loc[zero_se, "code"] if code in _TRIGGER_CODES]
    if decisive:
        raise ValueError("gate: surveyed row(s) %s have a standard error of exactly 0 (SrV count "
                         "%s, se of the student share %s), so the trigger row cannot be evaluated"
                         % (decisive, weighted[zero_se].to_dict(), se_share[zero_se].to_dict()))
    if zero_se.any():
        logger.warning("%s gate: %d surveyed row(s) %s have a standard error of exactly 0; their "
                       "difference in SrV standard errors is NaN and counts as not beyond %.1f",
                       _LOG_TAG, int(zero_se.sum()), list(frame.loc[zero_se, "code"]),
                       MIN_DIFFERENCE_STANDARD_ERRORS)
    in_se = difference / se.where(se > 0)
    # The area-consistent comparison of the students aged 20 to 29 (whole Kreis on the SrV side).
    balance_20_29 = balance * share_20_29
    estimate_20_29 = frame["srv_estimate_students_20_29_persons"].astype(float).where(surveyed)
    se_20_29 = (frame["srv_se_student_share_20_29"].astype(float)
                * frame["census_population_20_29_persons"].astype(float)).where(surveyed)
    difference_20_29 = balance_20_29 - estimate_20_29
    in_se_20_29 = difference_20_29 / se_20_29.where(se_20_29 > 0)
    out = pd.DataFrame({
        "code": frame["code"], "balance_students_persons": balance,
        "srv_students_weighted_persons": weighted,
        "srv_away_all_day_share": pd.Series(away_share, index=frame.index).where(surveyed),
        "srv_students_incl_away_persons": incl_away, "srv_se_students_incl_away_persons": se,
        "difference_persons": difference, "difference_in_srv_se": in_se,
        "beyond_two_se": in_se.abs() > MIN_DIFFERENCE_STANDARD_ERRORS,
        "balance_students_20_29_persons": balance_20_29,
        "srv_estimate_students_20_29_persons": frame["srv_estimate_students_20_29_persons"],
        "srv_se_estimate_students_20_29_persons": se_20_29, "difference_20_29_persons": difference_20_29,
        "difference_20_29_in_srv_se": in_se_20_29,
        "beyond_two_se_20_29": in_se_20_29.abs() > MIN_DIFFERENCE_STANDARD_ERRORS,
        "census_estimate_students_20_29_persons": frame["census_estimate_students_20_29_persons"],
        "model_today_students_persons": frame["code"].map(model).astype(float)},
        columns=GATE_COLUMNS)

    n_evaluable = int(in_se.notna().sum())
    n_beyond = int(out["beyond_two_se"].sum())
    logger.info("%s gate: SrV difference evaluable in %d/%d rows (%d without SrV or a positive "
                "standard error), beyond %.1f SrV standard errors in %d %s", _LOG_TAG, n_evaluable,
                len(out), len(out) - n_evaluable, MIN_DIFFERENCE_STANDARD_ERRORS, n_beyond,
                list(out.loc[out["beyond_two_se"], "code"]))
    return out


def gate_triggered(gate: pd.DataFrame) -> bool:
    """True when the Braunschweig row or the pooled row of a :func:`gate_table` is beyond two SrV
    standard errors (design A2): the owner decides on the level then. Other Kreise do not trigger.
    Raises ``ValueError`` when a column or either row is missing.
    """
    return _triggered(gate, "beyond_two_se")


def gate_triggered_area_consistent(gate: pd.DataFrame) -> bool:
    """True when the Braunschweig row or the pooled row of a :func:`gate_table` is beyond two SrV
    standard errors on the comparison of the students aged 20 to 29 (``beyond_two_se_20_29``: the
    SrV side covers the whole Kreis, see :data:`SRV_COVERAGE_NOTE`). A cross-check next to
    :func:`gate_triggered`, which stays the design's rule. Other Kreise do not trigger. Raises
    ``ValueError`` when a column or either row is missing.
    """
    return _triggered(gate, "beyond_two_se_20_29")


def _triggered(gate: pd.DataFrame, flag: str) -> bool:
    _require_columns(gate, ["code", flag], "gate")
    keyed = _unique_rows(gate, "code", "gate")
    needed = list(_TRIGGER_CODES)
    absent = [code for code in needed if code not in keyed.index]
    if absent:
        raise ValueError("gate: row(s) %s missing; the trigger reads %s" % (absent, needed))
    triggered = bool(keyed.loc[needed, flag].astype(bool).any())
    logger.info("%s gate triggered on %s: %s (rows %s)", _LOG_TAG, flag, triggered, needed)
    return triggered
