"""Routing of persons with an education activity by student status (issue #140, Phase 4, design B1).

Pure functions, no synpp, no stage. ``braunschweig.synthesis.locations.education_gravity`` calls them when
``braunschweig.students.flows_enabled`` is ON; with the switch OFF only :func:`age_to_level` (the age bands that
always lived in the stage) is used and the routing below does not run.

Routing table (design B1; ``P_TAET`` is the survey status: 8 apprentice, 9 pupil, 10 student)::

    student, age 18 or older                        -> ``university``
    student under 18 (rare, counted)                -> age band
    not a student, under 20                         -> age band (the upper-secondary band is split later)
    apprentice or pupil (P_TAET 8 or 9), 20 or older -> ``bbs_adult``
    everyone else, 20 or older                      -> ``adult_education``

Import boundary (Phase 4 global constraints): the only project import is the leaf
``braunschweig.data.education.student_flows_inputs`` (the student status code), never a calibration or
PopulationSim module.
"""
from __future__ import annotations

import logging

import pandas as pd

from braunschweig.data.education.student_flows_inputs import STUDENT_P_TAET

logger = logging.getLogger(__name__)

_LOG_TAG = "[education routing]"

#: School age bands ``(level, first age, last age)``, inclusive; the upper-secondary band is split into
#: ``oberstufe`` and ``bbs`` by the stage. Everyone older than the last band is ``university`` (OFF rule).
SCHOOL_BANDS = [
    ("kindergarten", 0, 5),
    ("grundschule", 6, 9),
    ("sekundar_1", 10, 15),
    ("upper_secondary", 16, 19),
    ("university", 20, 200),
]
#: Level of the persons routed to the adult-education rule (ADR-0157, #465).
ADULT_EDUCATION = "adult_education"
#: Level of apprentices and pupils aged 20 or older (vocational schools, drawn separately from the pupils).
BBS_ADULT = "bbs_adult"
UNIVERSITY = "university"

#: Student status of the persons routed by this module: ``P_TAET`` 10 comes from the leaf module
#: (pinned against ``popsim.attributes.STUDENT_TAET``); 8 and 9 are repeated here (MiD 2023 Codeplan B1,
#: Personen, P_TAET: 8 in Ausbildung, 9 Schueler/in) and pinned against ``popsim.attributes.STUDIES_TAET`` by
#: ``tests/test_education_routing.py``.
APPRENTICE_P_TAET = 8
PUPIL_P_TAET = 9
VOCATIONAL_P_TAET = (APPRENTICE_P_TAET, PUPIL_P_TAET)
#: First age at which a person is routed by the adult rules (the band edge of ``university`` in ``SCHOOL_BANDS``).
ADULT_MIN_AGE = 20
#: First age at which a student goes to the university level.
STUDENT_UNIVERSITY_MIN_AGE = 18

TAET_COLUMN = "P_TAET"


def age_to_level(age):
    """The level of the age band of ``age`` (today's rule of the education stage)."""
    for level, lo, hi in SCHOOL_BANDS:
        if lo <= age <= hi:
            return level
    return UNIVERSITY


def student_status(persons: pd.DataFrame, *, warn_share: float = 0.0) -> tuple[pd.Series, dict]:
    """``(Series[bool] is_student, counts)`` of ``persons`` (same index).

    PRIMARY: ``P_TAET == STUDENT_P_TAET`` when the column exists; a missing or non-numeric value counts as no
    student and is counted as ``taet_missing``. FALLBACK (column absent, e.g. the legacy IPF population):
    ``studies & ~employed & age >= 20``, counted and logged as ``"[education routing] student status: primary
    n/N (x%), fallback m (y%)"``; a WARNING is logged when the fallback share exceeds ``warn_share``
    (``braunschweig.students.status_fallback_warn_share``, default 0.0, ASSUMPTION: any fallback in the
    popsim_mid pipeline, whose persons carry ``P_TAET``, is a defect). Raises ``ValueError`` when ``P_TAET`` and
    one of ``studies``, ``employed``, ``age`` are absent: no status can be derived.

    ``counts`` holds ``n``, ``primary``, ``fallback``, ``taet_missing``, ``students``.
    """
    n = len(persons)
    taet_missing = 0
    if TAET_COLUMN in persons.columns:
        taet = pd.to_numeric(persons[TAET_COLUMN], errors="coerce")
        taet_missing = int(taet.isna().sum())
        is_student = (taet == STUDENT_P_TAET)
        primary, fallback = n, 0
    else:
        missing = [c for c in ("studies", "employed", "age") if c not in persons.columns]
        if missing:
            raise ValueError(f"{_LOG_TAG} no {TAET_COLUMN} and no column(s) {missing} to derive the student "
                             f"status from; found {list(persons.columns)}")
        is_student = (persons["studies"].astype(bool) & ~persons["employed"].astype(bool)
                      & (persons["age"] >= ADULT_MIN_AGE))
        primary, fallback = 0, n
    is_student = is_student.astype(bool)
    share = (fallback / n) if n else 0.0
    message = "%s student status: primary %d/%d (%.1f%%), fallback %d (%.1f%%); %d students, %d without a %s value" % (
        _LOG_TAG, primary, n, 100.0 * primary / n if n else 0.0, fallback, 100.0 * share,
        int(is_student.sum()), taet_missing, TAET_COLUMN)
    if share > warn_share:
        logger.warning("WARNING: %s (fallback share above %.3f: the population carries no %s)",
                       message, warn_share, TAET_COLUMN)
    else:
        logger.info(message)
    return is_student, {"n": n, "primary": primary, "fallback": fallback, "taet_missing": taet_missing,
                        "students": int(is_student.sum())}


def route_education_level(age: pd.Series, is_student: pd.Series, p_taet: pd.Series | None = None) -> pd.Series:
    """The routed level of every person (``Series[str]``, index of ``age``), per the table in the module docstring.

    ``p_taet`` is ``None`` or has only missing values when the population carries no ``P_TAET`` (legacy
    pipeline): nobody is then sent to ``bbs_adult``.
    """
    level = age.map(age_to_level)
    adult = age >= ADULT_MIN_AGE
    student_university = is_student & (age >= STUDENT_UNIVERSITY_MIN_AGE)
    if p_taet is None:
        vocational_status = pd.Series(False, index=age.index)
    else:
        vocational_status = pd.to_numeric(p_taet, errors="coerce").isin(VOCATIONAL_P_TAET)
    vocational = adult & ~student_university & vocational_status
    adult_education = adult & ~student_university & ~vocational_status
    level = level.mask(student_university, UNIVERSITY)
    level = level.mask(vocational, BBS_ADULT)
    level = level.mask(adult_education, ADULT_EDUCATION)
    return level


def routing_summary(age: pd.Series, is_student: pd.Series, routed: pd.Series) -> dict:
    """Counts for the log: persons per routed level, students under 18, non-student adults per adult rule.

    ``non_student_adults`` are the persons aged 20 or older who are no student: the OFF rule sends all of them
    to the university level (or, from ``education_adult_min_age``, to the adult rule with university buildings);
    ``to_bbs_adult`` and ``to_adult_education`` split them by their routed level.
    """
    non_student_adults = (age >= ADULT_MIN_AGE) & ~is_student
    return {
        "per_level": {str(k): int(v) for k, v in routed.value_counts().sort_index().items()},
        "students_under_18": int((is_student & (age < STUDENT_UNIVERSITY_MIN_AGE)).sum()),
        "non_student_adults": int(non_student_adults.sum()),
        "to_bbs_adult": int((non_student_adults & (routed == BBS_ADULT)).sum()),
        "to_adult_education": int((non_student_adults & (routed == ADULT_EDUCATION)).sum()),
    }
