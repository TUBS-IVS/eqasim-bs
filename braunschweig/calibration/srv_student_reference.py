"""SrV 2023 aggregates of university students (issue #140, Phase 0 reference table R5).

Pure builders over already-loaded SrV person / leg / household frames (no I/O, no synpp).
``scripts/extract_srv_student_reference.py`` is the only caller that touches the local-only raw
microdata; it owns the CLI and the provenance headers of the five committed tables:

* :func:`build_student_share_table` -- per home Kreis and region: the student share of the
  adults and of the 20-29 year olds, each with its Kish effective sample size and standard
  error, and (region row only) the share of students present at an education place on the
  reporting day;
* :func:`build_education_status_table` -- who the adults with an education leg are (student,
  pupil, apprentice, employed, other) per age band;
* :func:`build_education_distance_table` -- home-to-education distance bands of adult students
  per home Kreis, with a bootstrap noise floor of the band EMD;
* :func:`build_behaviour_table` -- the candidate behaviour cells of
  :mod:`braunschweig.calibration.student_behaviour_labels`, in the adults and in the students
  universe;
* :func:`build_comparison_table` -- car availability, licence and season-ticket availability
  (``V_FK_VERFUEG``; bicycles would be ``V_RAD_VERFUEG``, not read here) of students against
  the other adults, each code as delivered.

Definitions (SrV codebook ``SrV2023_Datenkodierung_SciUse.xlsx``):

* Universe: :func:`srv_participation_universe.prepare_universe_persons` (valid
  ``GEWICHT_P_ZENSUS``, persons at home at some point on the reporting day, household Kreis in
  the ZGB; its guards raise on any universe drift). Weight ``GEWICHT_P_ZENSUS`` (ADR-0055).
  Persons AWAY FROM HOME ALL DAY (``E_ANZ_WEGE == -7``) are outside that universe, while the MiD
  side keeps them: every share here describes students who were at home at some point of the
  day. :func:`student_universe` counts the students dropped this way
  (``n_students_away_from_home``, ``n_adult_students_away_from_home``).
* Student = ``V_ERW == STUDENT_V_ERW`` (7, Student/in). A person whose ``V_ERW`` is missing or
  negative (``-8`` not surveyed, ``-10`` implausible) answered nothing about their activity: they
  are excluded from EVERY table here and counted, never classed "not a student". A non-negative
  code outside :data:`V_ERW_CODES` raises.
* Adult = ``V_ALTER >= MIN_ADULT_AGE``; a person without a valid age is in no adult universe
  (``n_missing_age`` of the universe diagnostics).
* Education leg = ``E_ZWECK_9`` in :data:`srv_participation_universe.EDUCATION_E_ZWECK_9`; work
  leg = ``E_ZWECK_9`` in :data:`srv_participation_universe.WORK_E_ZWECK_9`.
* Trip class from ``E_ANZ_WEGE`` (bins :data:`student_behaviour_labels.TRIP_CLASS_EDGES`).
* Arrival = ``V_ANKUNFT_STUNDE`` of the person's FIRST education leg, ordered by
  ``V_BEGINN_STUNDE`` and then the leg number ``WNR``. A negative or missing hour (arrival of
  that leg, or the start hour of any of the person's education legs, which makes the order
  undeterminable) excludes the student from the arrival control only, counted.
* Season ticket: flatrate = ``E_OEV_FK`` in :data:`FLATRATE_E_OEV_FK`; ``-10`` (implausible)
  excludes the student from that control only, counted; ``-8`` (not surveyed: no public
  transport use in the past 12 months) is kept as "no flatrate" -- the convention of
  ``build_ticket_groups_table`` in ``scripts/extract_srv_kreis_tables.py``, whose code sets a
  test pins these to. Any other code raises.
* Distances: :func:`srv_distance_targets.select_person_observations` with
  :data:`srv_distance_targets.EDUCATION_PURPOSES` (the convention of ADR-0102: first
  home-to-education trip, else the first education-to-home trip, GIS-routed km, weight
  ``GEWICHT_W_ZENSUS`` as that function returns it), restricted to ADULT students by
  :func:`select_student_observations`.

Every exclusion is counted in the builder's diagnostics and logged as
``primary n/N (rate), excluded m (rate)``; a step that would exclude more than
:data:`student_behaviour_labels.MAX_EXCLUSION_RATE` of its base raises, because that is a broken
code mapping, not a measurement. Shares are NaN, never 0.0, when their base is empty, and empty cells are emitted.

Phase 1 (the student layer's parameter measurement) adds three builders on the same universe:
:func:`build_parameter_table` (every parameter of :data:`student_parameters.PARAMETERS` for the
adult students at home on the reporting day, the day labels through the one function
:func:`student_parameters.person_day_labels`), :func:`away_all_day_rows` (the share of persons
away from home all day, measured on the file BEFORE the universe filter) and
:func:`build_young_adult_status_table` (student / employed / other among the 20 to 29 year olds
per Kreis). An attribute code outside its documented set raises; a documented missing code
excludes the student from that parameter only, counted and logged.

Two definitions of an education leg coexist on purpose: the parameter table's ``education_leg``
uses the harmonised purpose of the plan-structure comparison (so survey and model pass through one
function), the control definition (``E_ZWECK_9`` in :data:`EDUCATION_E_ZWECK_9`, as the behaviour
table uses it) drives the day factor and every control target.
:func:`build_parameter_table` returns the SrV share of adult students with an education leg under
both in its diagnostics, so a difference is visible and nobody mixes the two.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import (ZGB_KREISE,
                                                            bootstrap_emd_noise_floor,
                                                            kreis_from_ags,
                                                            weighted_band_shares)
from braunschweig.calibration.srv_participation_universe import (
    EDUCATION_E_ZWECK_9, HOUSEHOLD_COLUMNS, LEVEL_KREIS, LEVEL_TOTAL, REGION_CODE,
    WORK_E_ZWECK_9, persons_with_purpose, prepare_universe_persons)
from braunschweig.calibration.srv_participation_universe import \
    PERSON_COLUMNS as UNIVERSE_PERSON_COLUMNS
# The person key and the NaN-for-empty share rule are taken from the sibling builder rather than
# re-implemented, so the key format persons_with_purpose returns cannot drift from ours.
from braunschweig.calibration.srv_participation_universe import (_groups_by_kreis, _person_ids,
                                                                  _weighted_share)
from braunschweig.calibration.srv_plan_structure import (AWAY_FROM_HOME_CODE, EMPLOYED_V_ERW,
                                                         SEX_BY_V_GESCHLECHT)
from braunschweig.calibration.student_parameters import (
    BASE_OTHER_ADULTS_ALL, BASE_STUDENTS_ALL, GROUP_DAY, PARAMETER_COLUMNS, PARAMETERS,
    parameter_rows, person_day_labels)
from braunschweig.calibration.student_behaviour_labels import (
    ARRIVAL_HOUR_EDGES, BEHAVIOUR_COLUMNS, CONTROLS, MAX_EXCLUSION_RATE, MIN_ADULT_AGE,
    NOT_STUDENT, STUDENT_DISTANCE_EDGES_KM, STUDENT_DISTANCE_LABELS, TRIP_CLASS_EDGES,
    UNIVERSE_ADULTS, UNIVERSE_STUDENTS, behaviour_rows, effective_sample_size,
    share_standard_error)

logger = logging.getLogger(__name__)

_LOG_TAG = "[srv student reference]"

STUDENT_SHARE_TABLE = "srv2023_student_share_by_kreis.csv"
EDUCATION_STATUS_TABLE = "srv2023_education_status_by_age.csv"
EDUCATION_DISTANCE_TABLE = "srv2023_student_education_distance_by_kreis.csv"
BEHAVIOUR_TABLE = "srv2023_student_behaviour.csv"
COMPARISON_TABLE = "srv2023_student_comparison.csv"

# V_ERW ("Taetigkeit/Erwerbstaetigkeit"): the documented substantive codes, the codeplan list of
# scripts/extract_srv_employment_status_kreis.py (1 child not yet at school, 2 homemaker,
# 3 retired, 4 voluntary service, 5 unemployed, 6 pupil, 7 student, 8 apprenticeship, 9-11
# employed, 12 on leave, 70 other activity).
STUDENT_V_ERW = 7
PUPIL_V_ERW = 6
APPRENTICE_V_ERW = 8
V_ERW_CODES = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 70})

# E_OEV_FK season-ticket codes, the sets of build_ticket_groups_table in
# scripts/extract_srv_kreis_tables.py, whose comment holds the code meanings (50 Deutschland-
# Ticket and 3 other time ticket are flatrate; -8 not surveyed = no PT use in the past 12
# months, kept as no flatrate; -10 implausible).
FLATRATE_E_OEV_FK = frozenset({50, 3})
NO_FLATRATE_E_OEV_FK = frozenset({1, 2, 60, 70, -8})
UNREADABLE_E_OEV_FK = -10

AGE_BAND_20_29 = (20, 29)
# Age bands of the education-status table, bounds INCLUSIVE; 200 is an upper sentinel.
STATUS_AGE_BANDS = (("18_19", 18, 19), ("20_29", 20, 29), ("30plus", 30, 200))
STATUSES = ("student", "pupil", "apprentice", "employed", "other")

COMPARISON_VARIABLES = ("V_PKW_VERFUEG", "V_FUEHR_PKW", "V_FK_VERFUEG")
GROUP_STUDENT = "student"
GROUP_OTHER_ADULT = "other_adult"

EXTRA_PERSON_COLUMNS = ["E_OEV_FK", "V_PKW_VERFUEG", "V_FUEHR_PKW", "V_FK_VERFUEG"]
PERSON_COLUMNS = list(UNIVERSE_PERSON_COLUMNS) + EXTRA_PERSON_COLUMNS
LEG_COLUMNS = ["HHNR", "PNR", "WNR", "E_ZWECK_9", "V_BEGINN_STUNDE", "V_ANKUNFT_STUNDE"]
HOUSEHOLD_COLUMNS = list(HOUSEHOLD_COLUMNS)
OBSERVATION_COLUMNS = ["hhnr", "pnr", "kreis", "distance_km", "weight"]

# ---------------------------------------------------------------- Phase 1 parameter measurement
PARAMETER_TABLE = "srv2023_student_parameters.csv"
YOUNG_ADULT_STATUS_TABLE = "srv2023_young_adult_status_by_kreis.csv"

PARAMETER_PERSON_COLUMNS = ["V_GESCHLECHT", "V_FUEHR_PKW", "E_ZUGANG_PKW"]
PARAMETER_HOUSEHOLD_COLUMNS = ["HHNR", "V_ANZ_PERS", "E_HHTYP", "V_ANZ_PKW", "E_ANZ_RAD_ALLE_6"]
AWAY_PERSON_COLUMNS = ["HHNR", "PNR", "V_ALTER", "V_ERW", "E_ANZ_WEGE", "GEWICHT_P_ZENSUS"]

# Codebook SrV2023_Datenkodierung_SciUse.xlsx. V_GESCHLECHT: 1 male, 2 female (the map of
# srv_plan_structure, imported above), 3 divers and 4 "no entry in the birth register" are
# excluded from `sex` and counted; any other code raises.
SEX_EXCLUDED_V_GESCHLECHT = (3, 4)
# E_HHTYP: 1 several persons with children, 2 several persons without children, 3 alone under 65,
# 4 alone from 65; no missing code (a negative code is excluded and counted, any other raises).
HOUSEHOLD_TYPE_BY_E_HHTYP = {1: "with_children", 2: "several_adults", 3: "alone", 4: "alone"}
# V_FUEHR_PKW (asked from 17): 1 yes, 2 no; -8 not collected, -10 implausible.
LICENCE_BY_V_FUEHR_PKW = {1: "yes", 2: "no"}
LICENCE_MISSING_V_FUEHR_PKW = (-8, -10)
# E_ZUGANG_PKW (licence AND at least one car in the household): 1 yes, 2 no; -7 not computable.
CAR_ACCESS_BY_E_ZUGANG_PKW = {1: "yes", 2: "no"}
CAR_ACCESS_MISSING_E_ZUGANG_PKW = (-7,)
# V_ANZ_PKW: number of cars; -10 implausible. E_ANZ_RAD_ALLE_6: 0 to 5 (5 = five or more); -7 not
# computable.
CARS_MISSING_V_ANZ_PKW = (-10,)
BICYCLES_MISSING_E_ANZ_RAD_ALLE_6 = (-7,)
BICYCLES_MAX_E_ANZ_RAD_ALLE_6 = 5
# Four ticket groups from E_OEV_FK, the code sets of build_ticket_groups4_table in
# scripts/extract_srv_kreis_tables.py (a test pins them to it): the flatrate and not-flatrate sets
# above split into Deutschlandticket / other flatrate and never / occasional. -10 is excluded.
DEUTSCHLANDTICKET_E_OEV_FK = 50
OTHER_FLATRATE_E_OEV_FK = FLATRATE_E_OEV_FK - {DEUTSCHLANDTICKET_E_OEV_FK}
TICKET_GROUP4_NEVER_E_OEV_FK = frozenset({-8})
TICKET_GROUP4_OCCASIONAL_E_OEV_FK = NO_FLATRATE_E_OEV_FK - TICKET_GROUP4_NEVER_E_OEV_FK

# Age groups of the adult students, RIGHT-inclusive: (17, 19] = 18_19, ... ; the last is open.
AGE_GROUP_EDGES = (MIN_ADULT_AGE - 1, 19, 24, 29, float("inf"))
# A person lives with someone "18 years older" when the age gap is at least this many years.
OLDER_PERSON_GAP_YEARS = 18

YOUNG_ADULT_STATUSES = ("student", "employed", "other")
YOUNG_ADULT_STATUS_COLUMNS = ["code", "level", "status", "n_unweighted", "weight_sum", "share",
                              "n_effective", "se_share"]

SHARE_COLUMNS = ["code", "level", "n_adults", "n_students", "student_share_adults",
                 "n_effective_adults", "se_student_share_adults", "n_20_29",
                 "n_students_20_29", "student_share_20_29", "n_effective_20_29",
                 "se_student_share_20_29", "students_weighted_persons", "presence_share",
                 "n_effective_presence", "se_presence_share"]
STATUS_COLUMNS = ["age_band", "status", "n_unweighted", "share"]
DISTANCE_COLUMNS = (["code", "level", "n_persons"]
                    + ["share_%s" % label for label in STUDENT_DISTANCE_LABELS]
                    + ["emd_noise_95"])
COMPARISON_COLUMNS = ["variable", "group", "code", "n_unweighted", "share"]


# --------------------------------------------------------------------------- helpers
def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s" % (name, missing))


def _log_exclusion(step: str, n_excluded: int, n_total: int) -> None:
    """Log ``primary n/N (rate), excluded m (rate)``; raise above :data:`MAX_EXCLUSION_RATE`."""
    n_primary = n_total - n_excluded
    rate = n_excluded / n_total if n_total else float("nan")
    logger.info("%s %s: primary %d/%d (%.1f%%), excluded %d (%.1f%%)", _LOG_TAG, step,
                n_primary, n_total, 100.0 * (1.0 - rate) if n_total else float("nan"),
                n_excluded, 100.0 * rate if n_total else float("nan"))
    if n_total and rate > MAX_EXCLUSION_RATE:
        raise ValueError(
            "%s %s: %d of %d rows (%.1f%%) would be excluded, above the %.0f%% limit; this is a "
            "broken code mapping or a recoded variable, not a measurement"
            % (_LOG_TAG, step, n_excluded, n_total, 100.0 * rate, 100.0 * MAX_EXCLUSION_RATE))


def _share_with_error(weight: pd.Series, mask) -> tuple[float, float, float]:
    """``(weighted share, Kish n_effective over the base, standard error)``; NaN for no base."""
    share = _weighted_share(weight, mask)
    n_effective = effective_sample_size(weight)
    return share, n_effective, share_standard_error(share, n_effective)


def _drop_unreadable_employment(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The universe without the persons whose ``V_ERW`` could not be read (counted, logged)."""
    unreadable = universe["is_student"].isna()
    n_unreadable = int(unreadable.sum())
    _log_exclusion("readable employment status (V_ERW present and non-negative; a non-answer "
                   "is never 'not a student')", n_unreadable, len(universe))
    readable = universe[~unreadable.values].copy()
    readable["is_student"] = readable["is_student"].astype(bool)
    return readable, {"n_excluded_unreadable_employment_code": n_unreadable}


def _adults(readable: pd.DataFrame) -> pd.DataFrame:
    """Adults (``age >= MIN_ADULT_AGE``); a NaN age is never adult (``n_missing_age``)."""
    return readable[readable["age"] >= MIN_ADULT_AGE].copy()


# --------------------------------------------------------------------------- universe
def student_universe(persons: pd.DataFrame,
                     households: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The participation universe plus the person columns the student tables read.

    ``persons`` must carry :data:`PERSON_COLUMNS`, ``households`` :data:`HOUSEHOLD_COLUMNS`.
    Returns ``(universe, diagnostics)``: the frame of
    :func:`srv_participation_universe.prepare_universe_persons` joined on ``pid`` with
    ``V_ERW``, ``E_ANZ_WEGE`` (needed for the trip class; the universe guarantees it is
    non-negative), ``E_OEV_FK``, ``V_PKW_VERFUEG`` (car availability), ``V_FUEHR_PKW`` (car
    licence), ``V_FK_VERFUEG`` (season-ticket availability) (numeric)
    and ``is_student`` (nullable boolean, ``pd.NA`` where ``V_ERW`` is missing or negative).
    ``diagnostics`` are the universe diagnostics plus ``n_students_away_from_home`` and
    ``n_adult_students_away_from_home``: the students (``V_ERW == STUDENT_V_ERW``; adults
    ``V_ALTER >= MIN_ADULT_AGE``) among the persons the universe drops as away from home all day,
    measured, like ``away_from_home``, on the persons with a valid weight. The MiD side keeps
    such persons, so the count is the size of that universe gap on the student side.

    Raises ``ValueError`` on a duplicated person key or a non-negative ``V_ERW`` outside
    :data:`V_ERW_CODES`.
    """
    _require_columns(persons, PERSON_COLUMNS, "persons")
    universe, diagnostics = prepare_universe_persons(persons, households)

    # Same filter order as prepare_universe_persons: valid weight first, then away from home.
    weight = pd.to_numeric(persons["GEWICHT_P_ZENSUS"], errors="coerce")
    away = ((weight.notna() & (weight >= 0))
            & (pd.to_numeric(persons["E_ANZ_WEGE"], errors="coerce") == AWAY_FROM_HOME_CODE))
    away_student = away & (pd.to_numeric(persons["V_ERW"], errors="coerce") == STUDENT_V_ERW)
    away_adult_student = away_student & (pd.to_numeric(persons["V_ALTER"], errors="coerce")
                                         >= MIN_ADULT_AGE)
    diagnostics = dict(diagnostics, n_students_away_from_home=int(away_student.sum()),
                       n_adult_students_away_from_home=int(away_adult_student.sum()))
    logger.info("%s away from home all day (E_ANZ_WEGE %d, outside the universe; MiD keeps such "
                "persons): %d persons, of them %d students (%d adult students)", _LOG_TAG,
                AWAY_FROM_HOME_CODE, diagnostics["away_from_home"],
                diagnostics["n_students_away_from_home"],
                diagnostics["n_adult_students_away_from_home"])

    extra = persons[["HHNR", "PNR", "V_ERW", "E_ANZ_WEGE", *EXTRA_PERSON_COLUMNS]].copy()
    extra["pid"] = _person_ids(extra)
    duplicated = sorted(set(extra.loc[extra["pid"].duplicated(), "pid"]))
    if duplicated:
        raise ValueError("persons: duplicated person key HHNR_PNR %s" % duplicated[:10])
    extra = extra.drop(columns=["HHNR", "PNR"])
    for column in ["V_ERW", "E_ANZ_WEGE", *EXTRA_PERSON_COLUMNS]:
        extra[column] = pd.to_numeric(extra[column], errors="coerce")
    universe = universe.merge(extra, on="pid", how="left", validate="1:1")

    code = universe["V_ERW"]
    readable = code.notna() & (code >= 0)
    undocumented = sorted(set(code[readable & ~code.isin(sorted(V_ERW_CODES))].tolist()))
    if undocumented:
        raise ValueError("persons: column V_ERW carries code(s) %s outside the documented set %s"
                         % (undocumented, sorted(V_ERW_CODES)))
    is_student = pd.array((code == STUDENT_V_ERW).values, dtype="boolean")
    is_student[~readable.values] = pd.NA
    universe["is_student"] = is_student
    logger.info("%s universe: %d persons, %d students (V_ERW %d), %d with an unreadable V_ERW",
                _LOG_TAG, len(universe), int((code == STUDENT_V_ERW).sum()), STUDENT_V_ERW,
                int((~readable).sum()))
    return universe, diagnostics


# --------------------------------------------------------------------------- student share
def build_student_share_table(universe: pd.DataFrame,
                              legs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Student share of the adults and of the 20-29 year olds per Kreis, plus presence.

    One ``kreis`` row per :data:`srv_distance_targets.ZGB_KREISE` (Wolfsburg, not surveyed, is a
    zero row with NaN shares) and one ``total`` row coded :data:`REGION_CODE` over their union.
    Columns :data:`SHARE_COLUMNS`: unweighted counts, weighted shares, the Kish effective sample
    size of each share's base and its standard error; ``students_weighted_persons`` is the
    weight sum of the adult students. ``presence_share`` (region row only, NaN on Kreis rows) is
    the weighted share of the students of ALL ages with at least one education leg on the
    reporting day, with ``n_effective_presence`` and ``se_presence_share``.
    """
    readable, diagnostics = _drop_unreadable_employment(universe)
    education_pids, leg_diagnostics = persons_with_purpose(legs, EDUCATION_E_ZWECK_9)
    readable["has_education"] = readable["pid"].isin(education_pids)
    adults = _adults(readable)

    rows = [_share_row(LEVEL_KREIS, code, adults[adults["kreis"] == code])
            for code in ZGB_KREISE]
    region = _share_row(LEVEL_TOTAL, REGION_CODE, adults)
    students = readable[readable["is_student"]]
    region["presence_share"], region["n_effective_presence"], region["se_presence_share"] = \
        _share_with_error(students["weight"], students["has_education"])
    rows.append(region)
    table = pd.DataFrame(rows, columns=SHARE_COLUMNS)
    diagnostics.update(n_adults=int(len(adults)), **leg_diagnostics)
    logger.info("%s student share table: %d rows; %s student_share_adults=%.4f, "
                "presence_share=%.4f", _LOG_TAG, len(table), REGION_CODE,
                region["student_share_adults"], region["presence_share"])
    return table, diagnostics


def _share_row(level: str, code: str, adults: pd.DataFrame) -> dict:
    weight = adults["weight"].astype(float)
    student = adults["is_student"].values
    age_mask = ((adults["age"] >= AGE_BAND_20_29[0]) & (adults["age"] <= AGE_BAND_20_29[1])).values
    share, n_eff, se = _share_with_error(weight, student)
    share_band, n_eff_band, se_band = _share_with_error(weight[age_mask], student[age_mask])
    return {
        "code": code, "level": level,
        "n_adults": int(len(adults)), "n_students": int(student.sum()),
        "student_share_adults": share, "n_effective_adults": n_eff,
        "se_student_share_adults": se,
        "n_20_29": int(age_mask.sum()), "n_students_20_29": int(student[age_mask].sum()),
        "student_share_20_29": share_band, "n_effective_20_29": n_eff_band,
        "se_student_share_20_29": se_band,
        "students_weighted_persons": float(weight[student].sum()),
        "presence_share": float("nan"), "n_effective_presence": float("nan"),
        "se_presence_share": float("nan"),
    }


# --------------------------------------------------------------------------- education status
def build_education_status_table(universe: pd.DataFrame,
                                 legs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Activity status of the adults with an education leg, per age band, region level.

    Status precedence: ``student`` (V_ERW 7), ``pupil`` (6), ``apprentice`` (8, although 8 is in
    :data:`srv_plan_structure.EMPLOYED_V_ERW`), ``employed`` (the rest of that set), ``other``.
    Bands :data:`STATUS_AGE_BANDS`. Columns :data:`STATUS_COLUMNS`; ``share`` is weighted within
    the band, NaN for an empty band; all 15 cells are emitted.
    """
    readable, diagnostics = _drop_unreadable_employment(universe)
    education_pids, leg_diagnostics = persons_with_purpose(legs, EDUCATION_E_ZWECK_9)
    with_education = readable[readable["pid"].isin(education_pids)].copy()
    code = with_education["V_ERW"]
    with_education["status"] = np.select(
        [code == STUDENT_V_ERW, code == PUPIL_V_ERW, code == APPRENTICE_V_ERW,
         code.isin(EMPLOYED_V_ERW)], list(STATUSES[:4]), default=STATUSES[4])
    rows = []
    n_in_bands = 0
    for band, minimum, maximum in STATUS_AGE_BANDS:
        in_band = with_education[(with_education["age"] >= minimum)
                                 & (with_education["age"] <= maximum)]
        n_in_bands += len(in_band)
        for status in STATUSES:
            mask = (in_band["status"] == status).values
            rows.append({"age_band": band, "status": status, "n_unweighted": int(mask.sum()),
                         "share": _weighted_share(in_band["weight"], mask)})
    table = pd.DataFrame(rows, columns=STATUS_COLUMNS)
    diagnostics.update(n_with_education_leg=int(len(with_education)),
                       n_with_education_leg_in_no_adult_band=int(len(with_education) - n_in_bands),
                       **leg_diagnostics)
    logger.info("%s education status table: %d persons with an education leg, %d of them in an "
                "adult age band", _LOG_TAG, len(with_education), n_in_bands)
    return table, diagnostics


# --------------------------------------------------------------------------- distances
def select_student_observations(observations: pd.DataFrame,
                                universe: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Keep the :func:`srv_distance_targets.select_person_observations` rows of ADULT students.

    Adult (``age >= MIN_ADULT_AGE``, age from ``universe``) like the share, behaviour and
    comparison tables, so all student tables describe the same persons. A row whose person is
    not in ``universe`` or whose ``V_ERW`` is unreadable is excluded and counted; a readable
    non-student is simply not a student, and a student under ``MIN_ADULT_AGE`` or without a
    valid age is outside the universe of this table (counted, not an exclusion).
    """
    _require_columns(observations, ["hhnr", "pnr"], "observations")
    pid = _person_ids(pd.DataFrame({"HHNR": observations["hhnr"].values,
                                    "PNR": observations["pnr"].values},
                                   index=observations.index))
    by_pid = universe.set_index("pid")
    in_universe = pid.isin(by_pid.index).values
    mapped = pid.map(by_pid["is_student"])
    unreadable = in_universe & mapped.isna().values
    is_student = mapped.fillna(False).astype(bool).values
    is_adult = (pid.map(by_pid["age"]) >= MIN_ADULT_AGE).values
    n_total = len(observations)
    n_not_in_universe = int((~in_universe).sum())
    n_unreadable = int(unreadable.sum())
    _log_exclusion("education distance observations in the universe with a readable V_ERW",
                   n_not_in_universe + n_unreadable, n_total)
    kept = observations[is_student & is_adult].copy()
    n_not_adult = int((is_student & ~is_adult).sum())
    logger.info("%s education distance observations: %d adult students kept, %d students under "
                "%d or without a valid age outside this table's universe", _LOG_TAG, len(kept),
                n_not_adult, MIN_ADULT_AGE)
    diagnostics = {"n_observations_total": int(n_total),
                   "n_observations_not_in_universe": n_not_in_universe,
                   "n_observations_unreadable_employment_code": n_unreadable,
                   "n_observations_students_not_adult": n_not_adult,
                   "n_observations_adult_students": int(len(kept))}
    return kept, diagnostics


def build_education_distance_table(observations: pd.DataFrame, n_bootstrap: int,
                                   seed: int) -> pd.DataFrame:
    """Students' home-to-education distance band shares per Kreis and region.

    ``observations``: adult-student rows of
    :func:`srv_distance_targets.select_person_observations` (see
    :func:`select_student_observations`), with ``kreis``, ``distance_km`` and ``weight``.
    Columns :data:`DISTANCE_COLUMNS`: ``n_persons``, ``share_<label>`` over
    :data:`student_behaviour_labels.STUDENT_DISTANCE_EDGES_KM` (left-inclusive) and
    ``emd_noise_95``, the 95th percentile of the band EMD a resample of the same persons reaches
    by sampling noise alone (:func:`srv_distance_targets.bootstrap_emd_noise_floor`, seeded;
    0.0 for a single person by that function's convention). A row with no person carries NaN
    shares and a NaN noise floor. Raises ``ValueError`` on a Kreis outside the ZGB.
    """
    _require_columns(observations, ["kreis", "distance_km", "weight"], "observations")
    foreign = sorted(set(observations["kreis"]) - set(ZGB_KREISE))
    if foreign:
        raise ValueError("observations: column kreis carries code(s) %s outside %s"
                         % (foreign, list(ZGB_KREISE)))
    rows = [_distance_row(LEVEL_KREIS, code, observations[observations["kreis"] == code],
                          n_bootstrap, seed) for code in ZGB_KREISE]
    rows.append(_distance_row(LEVEL_TOTAL, REGION_CODE, observations, n_bootstrap, seed))
    return pd.DataFrame(rows, columns=DISTANCE_COLUMNS)


def _distance_row(level: str, code: str, group: pd.DataFrame, n_bootstrap: int,
                  seed: int) -> dict:
    distance = group["distance_km"].to_numpy(dtype=float)
    weight = group["weight"].to_numpy(dtype=float)
    row = {"code": code, "level": level, "n_persons": int(len(group))}
    if len(group) == 0 or not weight.sum() > 0:
        shares = np.full(len(STUDENT_DISTANCE_LABELS), np.nan)
        noise = float("nan")
    else:
        shares = weighted_band_shares(distance, weight, STUDENT_DISTANCE_EDGES_KM)
        noise = bootstrap_emd_noise_floor(distance, weight, STUDENT_DISTANCE_EDGES_KM,
                                          n_bootstrap=n_bootstrap, seed=seed)
    row.update({"share_%s" % label: float(share)
                for label, share in zip(STUDENT_DISTANCE_LABELS, shares)})
    row["emd_noise_95"] = noise
    return row


# --------------------------------------------------------------------------- behaviour
def build_behaviour_table(universe: pd.DataFrame,
                          legs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Every control of :data:`student_behaviour_labels.CONTROLS` in both universes.

    ``adults_18plus``: adults with a readable ``V_ERW``; a non-student carries
    :data:`NOT_STUDENT`, a student their label. ``students``: the adult students alone, so the
    same student labels renormalised. A student excluded from one control (unreadable arrival
    or start hour, ``E_OEV_FK == -10``) is absent from that control's base in BOTH universes and
    stays in every other control. Columns :data:`BEHAVIOUR_COLUMNS`; ``n_effective`` is the Kish
    size over exactly the rows of that control's base in that universe. Region level only.
    """
    _require_columns(legs, LEG_COLUMNS, "legs")
    readable, diagnostics = _drop_unreadable_employment(universe)
    adults = _adults(readable)
    education_pids, leg_diagnostics = persons_with_purpose(legs, EDUCATION_E_ZWECK_9)
    work_pids, _ = persons_with_purpose(legs, WORK_E_ZWECK_9)
    students = adults[adults["is_student"]].copy()
    has_education = students["pid"].isin(education_pids)

    trip_class = pd.cut(students["E_ANZ_WEGE"], TRIP_CLASS_EDGES,
                        labels=list(CONTROLS["trip_class"]))
    if trip_class.isna().any():
        raise ValueError("persons: column E_ANZ_WEGE carries value(s) %s outside the trip-class "
                         "edges %s" % (sorted(set(students.loc[trip_class.isna(), "E_ANZ_WEGE"])),
                                       TRIP_CLASS_EDGES))
    arrival, arrival_diagnostics = _arrival_labels(students, legs)
    season, season_diagnostics = _season_ticket_labels(students)
    labels = {
        "status_x_education_leg": pd.Series(np.where(has_education, "student_edu",
                                                     "student_noedu"), index=students.index),
        "trip_class": trip_class.astype(object),
        "arrival": arrival,
        "work_leg": pd.Series(np.where(students["pid"].isin(work_pids), "student_work",
                                       "student_nowork"), index=students.index),
        "season_ticket": season,
    }
    non_students = adults[~adults["is_student"]]
    rows = []
    for control, student_labels in CONTROLS.items():
        label = labels[control]
        base = students[label.notna().values]
        base_labels = label[label.notna()].values
        rows += behaviour_rows(control, UNIVERSE_STUDENTS, student_labels,
                               base["weight"].to_numpy(dtype=float), base_labels)
        adult_weight = np.concatenate([non_students["weight"].to_numpy(dtype=float),
                                       base["weight"].to_numpy(dtype=float)])
        adult_labels = np.concatenate([np.full(len(non_students), NOT_STUDENT, dtype=object),
                                       base_labels.astype(object)])
        rows += behaviour_rows(control, UNIVERSE_ADULTS, (*student_labels, NOT_STUDENT),
                               adult_weight, adult_labels)
    table = pd.DataFrame(rows, columns=BEHAVIOUR_COLUMNS)
    diagnostics.update(n_adults=int(len(adults)), n_students=int(len(students)),
                       **arrival_diagnostics, **season_diagnostics, **leg_diagnostics)
    logger.info("%s behaviour table: %d rows (%d controls x 2 universes); %d adults, %d adult "
                "students", _LOG_TAG, len(table), len(CONTROLS), len(adults), len(students))
    return table, diagnostics


def _arrival_labels(students: pd.DataFrame, legs: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Arrival label per student; NaN where the student is excluded from the arrival control."""
    purpose = pd.to_numeric(legs["E_ZWECK_9"], errors="coerce")
    education_legs = legs[purpose.isin(sorted(EDUCATION_E_ZWECK_9)).values].copy()
    education_legs["pid"] = _person_ids(education_legs)
    education_legs = education_legs[education_legs["pid"].isin(set(students["pid"]))]
    education_legs["begin"] = pd.to_numeric(education_legs["V_BEGINN_STUNDE"], errors="coerce")
    education_legs["arrival"] = pd.to_numeric(education_legs["V_ANKUNFT_STUNDE"],
                                              errors="coerce")
    education_legs["wnr"] = pd.to_numeric(education_legs["WNR"], errors="coerce")

    bad_start = education_legs["begin"].isna() | (education_legs["begin"] < 0)
    unreadable_start = set(education_legs.loc[bad_start, "pid"])
    first = (education_legs[~education_legs["pid"].isin(unreadable_start)]
             .sort_values(["pid", "begin", "wnr"], kind="stable")
             .drop_duplicates("pid", keep="first"))
    bad_arrival = first["arrival"].isna() | (first["arrival"] < 0)
    unreadable_arrival = set(first.loc[bad_arrival, "pid"])
    readable_first = first[~bad_arrival]
    too_late = readable_first[readable_first["arrival"] > ARRIVAL_HOUR_EDGES[-1]]
    if len(too_late):
        raise ValueError("legs: column V_ANKUNFT_STUNDE carries hour(s) %s above %d"
                         % (sorted(set(too_late["arrival"])), ARRIVAL_HOUR_EDGES[-1]))
    hour_label = pd.cut(readable_first["arrival"], ARRIVAL_HOUR_EDGES,
                        labels=list(CONTROLS["arrival"][:3])).astype(object)
    by_pid = dict(zip(readable_first["pid"], hour_label))

    with_education = set(education_legs["pid"])
    label = students["pid"].map(
        lambda pid: by_pid.get(pid, np.nan) if pid in with_education
        else CONTROLS["arrival"][3])
    n_start, n_arrival = len(unreadable_start), len(unreadable_arrival)
    # Base = the students WITH an education leg: only they can lose their arrival label. Over
    # all students the rate would be diluted by the absence share, and a recoded hour column
    # (every hour negative) would stay below MAX_EXCLUSION_RATE instead of raising.
    _log_exclusion("arrival control (readable start and arrival hour of the first education "
                   "leg, over the students with an education leg; start unreadable %d, arrival "
                   "unreadable %d)" % (n_start, n_arrival),
                   n_start + n_arrival, len(with_education))
    return label, {"n_students_unreadable_start_hour": n_start,
                   "n_students_unreadable_arrival_hour": n_arrival}


def _season_ticket_labels(students: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Season-ticket label per student; NaN for ``E_OEV_FK == -10`` (excluded, counted)."""
    code = students["E_OEV_FK"]
    known = FLATRATE_E_OEV_FK | NO_FLATRATE_E_OEV_FK | {UNREADABLE_E_OEV_FK}
    undocumented = code[~code.isin(sorted(known))]
    if len(undocumented):
        raise ValueError("persons: column E_OEV_FK carries code(s) %s (NaN = missing) outside "
                         "the documented set %s" % (sorted(set(undocumented.tolist()), key=str),
                                                    sorted(known)))
    label = pd.Series(np.where(code.isin(sorted(FLATRATE_E_OEV_FK)), "student_flatrate",
                               "student_no_flatrate"), index=students.index, dtype=object)
    unreadable = (code == UNREADABLE_E_OEV_FK).values
    label[unreadable] = np.nan
    n_unreadable = int(unreadable.sum())
    _log_exclusion("season-ticket control (E_OEV_FK != %d; -8 kept as no flatrate)"
                   % UNREADABLE_E_OEV_FK, n_unreadable, len(students))
    return label, {"n_students_unreadable_season_ticket": n_unreadable}


# --------------------------------------------------------------------------- comparison
def build_comparison_table(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Car availability, car licence and season-ticket availability: adult students vs others.

    Variables :data:`COMPARISON_VARIABLES`, each code as delivered (missing codes included as
    their own rows), every code seen among the adults emitted for both groups. Columns
    :data:`COMPARISON_COLUMNS`; ``share`` is weighted within the group. Raises ``ValueError``
    on a missing or non-numeric code.
    """
    readable, diagnostics = _drop_unreadable_employment(universe)
    adults = _adults(readable)
    groups = ((GROUP_STUDENT, adults[adults["is_student"]]),
              (GROUP_OTHER_ADULT, adults[~adults["is_student"]]))
    rows = []
    for variable in COMPARISON_VARIABLES:
        if adults[variable].isna().any():
            raise ValueError("persons: column %s has %d missing or non-numeric value(s) among "
                             "the adults" % (variable, int(adults[variable].isna().sum())))
        codes = sorted(int(code) for code in adults[variable].unique())
        for group_name, group in groups:
            for code in codes:
                mask = (group[variable] == code).values
                rows.append({"variable": variable, "group": group_name, "code": code,
                             "n_unweighted": int(mask.sum()),
                             "share": _weighted_share(group["weight"], mask)})
    table = pd.DataFrame(rows, columns=COMPARISON_COLUMNS)
    diagnostics.update(n_adults=int(len(adults)), n_students=int(len(groups[0][1])))
    return table, diagnostics


# --------------------------------------------------------------------------- parameter table
def _label_codes(values: pd.Series, column: str, table: dict, excluded=(),
                 exclude_negative: bool = False) -> pd.Series:
    """String labels of a coded column; NaN where the documented missing code excludes the row.

    ``table`` maps the documented codes to labels, ``excluded`` lists the documented missing
    codes (``exclude_negative`` excludes every negative code instead). A missing or non-numeric
    value is excluded too. Any other code raises ``ValueError`` naming the column and the codes.
    The labels are built as strings (never from an integer column holding NaN), so ``parameter_rows``
    never sees ``"1.0"`` for the code 1.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    mapping, unknown = {}, []
    for code in numeric.dropna().unique():
        if code in table:
            mapping[code] = table[code]
        elif code in excluded or (exclude_negative and code < 0):
            mapping[code] = np.nan
        else:
            unknown.append(code)
    if unknown:
        raise ValueError("column %s carries code(s) %s outside the documented set %s (missing "
                         "codes %s)" % (column, sorted(unknown), sorted(table),
                                        "all negative" if exclude_negative else sorted(excluded)))
    return numeric.map(mapping).astype(object)


def _car_labels(values: pd.Series) -> pd.Series:
    """``0`` / ``1`` / ``2plus`` from V_ANZ_PKW; the documented -10 and a missing value exclude."""
    numeric = pd.to_numeric(values, errors="coerce")
    undocumented = numeric[numeric.notna() & ~numeric.isin(CARS_MISSING_V_ANZ_PKW)
                           & ((numeric < 0) | (numeric != np.floor(numeric)))]
    if len(undocumented):
        raise ValueError("column V_ANZ_PKW carries value(s) %s: not a car count and not the "
                         "documented missing code %s" % (sorted(set(undocumented)),
                                                         list(CARS_MISSING_V_ANZ_PKW)))
    label = pd.Series(np.nan, index=numeric.index, dtype=object)
    label[(numeric == 0).to_numpy()] = "0"
    label[(numeric == 1).to_numpy()] = "1"
    label[(numeric >= 2).to_numpy()] = "2plus"
    return label


def _bicycle_labels(values: pd.Series) -> pd.Series:
    """``yes`` (one bicycle or more) / ``no`` from E_ANZ_RAD_ALLE_6; -7 and missing exclude."""
    table = {code: "yes" if code >= 1 else "no"
             for code in range(BICYCLES_MAX_E_ANZ_RAD_ALLE_6 + 1)}
    return _label_codes(values, "E_ANZ_RAD_ALLE_6", table,
                        excluded=BICYCLES_MISSING_E_ANZ_RAD_ALLE_6)


def _ticket_tables() -> tuple[dict, dict]:
    """Code -> label for ``season_ticket`` and for ``ticket_group4`` (``-10`` is in neither)."""
    season = {code: "flatrate" for code in FLATRATE_E_OEV_FK}
    season.update({code: "no_flatrate" for code in NO_FLATRATE_E_OEV_FK})
    group4 = {DEUTSCHLANDTICKET_E_OEV_FK: "deutschlandticket"}
    group4.update({code: "other_flatrate" for code in OTHER_FLATRATE_E_OEV_FK})
    group4.update({code: "never_pt" for code in TICKET_GROUP4_NEVER_E_OEV_FK})
    group4.update({code: "occasional_ticket" for code in TICKET_GROUP4_OCCASIONAL_E_OEV_FK})
    return season, group4


def _older_person_labels(students: pd.DataFrame, persons: pd.DataFrame) -> pd.Series:
    """``yes`` if the household holds a person at least 18 years older than the student.

    Ages come from ALL persons of the household in the raw person file (a negative or missing
    ``V_ALTER`` is unknown). ``yes`` as soon as one known age is at least
    :data:`OLDER_PERSON_GAP_YEARS` above the student's; otherwise ``no`` if every age is known, and
    NaN (excluded, counted) if some housemate's age is unknown -- the answer is then not defined.
    The student's own age is always known (adults only), so including the student in the household
    maximum changes nothing.
    """
    age = pd.to_numeric(persons["V_ALTER"], errors="coerce")
    members = pd.DataFrame({"hhnr": persons["HHNR"].astype(str).to_numpy(),
                            "age": age.where(age >= 0).to_numpy()})
    oldest = members.groupby("hhnr")["age"].max()
    n_unknown = members["age"].isna().groupby(members["hhnr"]).sum()
    older = (students["hhnr"].map(oldest) >= students["age"] + OLDER_PERSON_GAP_YEARS).to_numpy()
    unknown = (students["hhnr"].map(n_unknown) > 0).to_numpy()
    return pd.Series(np.where(older, "yes", np.where(unknown, "", "no")), index=students.index,
                     dtype=object).replace("", np.nan)


def _unique_keyed(key: pd.Series, name: str) -> None:
    duplicated = sorted(set(key[key.duplicated()]))
    if duplicated:
        raise ValueError("%s: duplicated key %s" % (name, duplicated[:10]))


def build_parameter_table(universe: pd.DataFrame, persons: pd.DataFrame,
                          households: pd.DataFrame, harmonised_persons: pd.DataFrame,
                          harmonised_trips: pd.DataFrame,
                          legs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Every parameter of :data:`student_parameters.PARAMETERS` over the SrV adult students.

    ``universe`` is :func:`student_universe`; ``persons`` / ``households`` / ``legs`` are the
    raw frames it was built from (``persons`` must also carry :data:`PARAMETER_PERSON_COLUMNS`,
    ``households`` :data:`PARAMETER_HOUSEHOLD_COLUMNS`); ``harmonised_persons`` and
    ``harmonised_trips`` are exactly what :func:`srv_plan_structure.harmonise_srv` returned for the
    same raw frames (``legs`` is only read for the control definition of an education leg, see the
    diagnostics).

    The base of every parameter except ``away_all_day`` is the adult students (``V_ERW`` 7, age
    at least :data:`MIN_ADULT_AGE`) of the universe, i.e. at home at some point of the reporting
    day; the day labels come from :func:`student_parameters.person_day_labels` on the harmonised
    frames, joined 1:1 on ``pid``. ``away_all_day`` comes from :func:`away_all_day_rows`. A
    student whose code for a parameter is a documented missing code is excluded from THAT
    parameter only; an undocumented code raises. Columns :data:`PARAMETER_COLUMNS`, every label
    of every vocabulary emitted.

    Returns ``(table, diagnostics)``. Besides the exclusion counts (``n_excluded_<parameter>``)
    the diagnostics carry the weighted SrV share of adult students with an education leg under
    both definitions: ``education_leg_share_harmonised_purpose`` (the ``education_leg`` rows of
    this table: a trip with harmonised purpose education) and
    ``education_leg_share_control_definition`` (``E_ZWECK_9`` in
    :data:`srv_participation_universe.EDUCATION_E_ZWECK_9`, the definition of the behaviour table
    and of the day factor), each over all adult students of the universe.
    """
    _require_columns(universe, ["pid", "weight", "age", "is_student", "V_FUEHR_PKW", "E_OEV_FK"],
                     "universe")
    _require_columns(persons, ["HHNR", "PNR", "V_ALTER", *PARAMETER_PERSON_COLUMNS], "persons")
    _require_columns(households, PARAMETER_HOUSEHOLD_COLUMNS, "households")
    readable, diagnostics = _drop_unreadable_employment(universe)
    students = _adults(readable)
    students = students[students["is_student"]].reset_index(drop=True)
    n_students = len(students)
    diagnostics["n_adult_students"] = n_students
    students["hhnr"] = students["pid"].str.split("_").str[0]

    person_extra = persons[["HHNR", "PNR", "V_GESCHLECHT", "E_ZUGANG_PKW"]].copy()
    person_extra["pid"] = _person_ids(person_extra)
    _unique_keyed(person_extra["pid"], "persons: person key HHNR_PNR")
    lost = sorted(set(students["pid"]) - set(person_extra["pid"]))
    if lost:
        raise ValueError("universe students missing from persons: %s" % lost[:10])
    students = students.merge(person_extra[["pid", "V_GESCHLECHT", "E_ZUGANG_PKW"]], on="pid",
                              how="left", validate="1:1")
    household_extra = households[PARAMETER_HOUSEHOLD_COLUMNS].copy()
    household_extra["hhnr"] = household_extra["HHNR"].astype(str)
    _unique_keyed(household_extra["hhnr"], "households: key HHNR")
    lost = sorted(set(students["hhnr"]) - set(household_extra["hhnr"]))
    if lost:
        raise ValueError("universe students without a household row: HHNR %s" % lost[:10])
    students = students.merge(household_extra.drop(columns=["HHNR"]), on="hhnr", how="left",
                              validate="m:1")

    season_table, group4_table = _ticket_tables()
    ticket_missing = (UNREADABLE_E_OEV_FK,)
    labels = {
        "age_group": pd.cut(students["age"], list(AGE_GROUP_EDGES),
                            labels=list(PARAMETERS["age_group"].labels)).astype(object),
        "sex": _label_codes(students["V_GESCHLECHT"], "V_GESCHLECHT", SEX_BY_V_GESCHLECHT,
                            excluded=SEX_EXCLUDED_V_GESCHLECHT, exclude_negative=True),
        "household_type": _label_codes(students["E_HHTYP"], "E_HHTYP",
                                       HOUSEHOLD_TYPE_BY_E_HHTYP, exclude_negative=True),
        "lives_with_person_18_years_older": _older_person_labels(students, persons),
        "licence": _label_codes(students["V_FUEHR_PKW"], "V_FUEHR_PKW", LICENCE_BY_V_FUEHR_PKW,
                                excluded=LICENCE_MISSING_V_FUEHR_PKW),
        "cars_in_household": _car_labels(students["V_ANZ_PKW"]),
        "car_access": _label_codes(students["E_ZUGANG_PKW"], "E_ZUGANG_PKW",
                                   CAR_ACCESS_BY_E_ZUGANG_PKW,
                                   excluded=CAR_ACCESS_MISSING_E_ZUGANG_PKW),
        "bicycle_in_household": _bicycle_labels(students["E_ANZ_RAD_ALLE_6"]),
        "season_ticket": _label_codes(students["E_OEV_FK"], "E_OEV_FK", season_table,
                                      excluded=ticket_missing),
        "ticket_group4": _label_codes(students["E_OEV_FK"], "E_OEV_FK", group4_table,
                                      excluded=ticket_missing),
    }
    for parameter, label in labels.items():
        n_excluded = int(label.isna().sum())
        diagnostics["n_excluded_%s" % parameter] = n_excluded
        _log_exclusion("%s (documented missing code excludes the student from this parameter "
                       "only)" % parameter, n_excluded, n_students)

    day_labels, _ = person_day_labels(harmonised_persons, harmonised_trips)
    lost = sorted(set(students["pid"]) - set(day_labels["pid"]))
    if lost:
        raise ValueError("adult students without a harmonised person row: %s" % lost[:10])
    day = students[["pid"]].merge(day_labels, on="pid", how="left", validate="1:1")
    for parameter in (name for name, spec in PARAMETERS.items()
                      if spec.group == GROUP_DAY and name != "away_all_day"):
        labels[parameter] = day[parameter]
    # Only these four day parameters can lose a student to a missing time; their base is the
    # mobile students (departure, arrival at home) or those with an education leg.
    mobile = (day["mobile"] == "yes").to_numpy()
    with_education = (day["education_leg"] == "yes").to_numpy()
    for parameter, in_base in (("first_departure_hour", mobile), ("last_arrival_home_hour", mobile),
                               ("time_at_education", with_education),
                               ("arrival_band", with_education)):
        n_excluded = int(day[parameter].isna().to_numpy()[in_base].sum())
        diagnostics["n_excluded_%s" % parameter] = n_excluded
        _log_exclusion("%s (a missing or negative time excludes the student from this parameter "
                       "only)" % parameter, n_excluded, int(in_base.sum()))

    weights = students["weight"]
    frames = [parameter_rows(parameter, spec.base, labels[parameter], weights)
              for parameter, spec in PARAMETERS.items() if parameter != "away_all_day"]
    away_rows, away_diagnostics = away_all_day_rows(persons, households)
    frames.append(away_rows)
    table = pd.concat(frames, ignore_index=True)[PARAMETER_COLUMNS]
    diagnostics.update({"away_all_day_%s" % key: value for key, value in away_diagnostics.items()})

    education_pids, leg_diagnostics = persons_with_purpose(legs, EDUCATION_E_ZWECK_9)
    diagnostics.update(leg_diagnostics)
    diagnostics["education_leg_share_harmonised_purpose"] = _weighted_share(
        weights, (day["education_leg"] == "yes").to_numpy())
    diagnostics["education_leg_share_control_definition"] = _weighted_share(
        weights, students["pid"].isin(education_pids).to_numpy())
    logger.info("%s parameter table: %d rows, %d adult students; education leg share of adult "
                "students: harmonised purpose %.4f, control definition (E_ZWECK_9 in %s) %.4f",
                _LOG_TAG, len(table), n_students,
                diagnostics["education_leg_share_harmonised_purpose"],
                sorted(EDUCATION_E_ZWECK_9), diagnostics["education_leg_share_control_definition"])
    return table, diagnostics


# --------------------------------------------------------------------------- away all day
def away_all_day_rows(persons: pd.DataFrame,
                      households: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The ``away_all_day`` rows for adult students and for the other adults, on the RAW file.

    The participation universe drops exactly the persons away from home all day
    (``E_ANZ_WEGE == srv_plan_structure.AWAY_FROM_HOME_CODE``), so this share cannot be measured
    on it: it is measured on the raw person file after the same weight and Kreis rules, in this
    order and each counted over the survivors of the step before: a missing or negative
    ``GEWICHT_P_ZENSUS`` (``n_excluded_invalid_weight``), a person whose household has no
    resolvable Kreis (``n_excluded_missing_kreis``; a Kreis outside the ZGB raises, as in
    :func:`srv_participation_universe.prepare_universe_persons`), an unreadable ``V_ERW``
    (``n_excluded_unreadable_employment_code``; a non-negative code outside :data:`V_ERW_CODES`
    raises). Adults: age at least :data:`MIN_ADULT_AGE`; students: ``V_ERW`` 7, the other adults
    everybody else. Returns the :data:`PARAMETER_COLUMNS` rows of base
    :data:`student_parameters.BASE_STUDENTS_ALL` and :data:`student_parameters.BASE_OTHER_ADULTS_ALL`.
    Raises ``ValueError`` for a missing column, a duplicated person key or an ``E_ANZ_WEGE`` that
    is missing or negative but not the away code (the universe guard of the participation
    tables).
    """
    _require_columns(persons, AWAY_PERSON_COLUMNS, "persons")
    _require_columns(households, ["HHNR", "AGS"], "households")
    work = persons[AWAY_PERSON_COLUMNS].copy()
    work["pid"] = _person_ids(work)
    _unique_keyed(work["pid"], "persons: person key HHNR_PNR")
    n_total = len(work)

    n_trips = pd.to_numeric(work["E_ANZ_WEGE"], errors="coerce")
    odd = work[n_trips.isna() | ((n_trips < 0) & (n_trips != AWAY_FROM_HOME_CODE))]
    if len(odd):
        raise ValueError("persons: column E_ANZ_WEGE carries %d missing or negative value(s) "
                         "other than the away code %d, e.g. %s" % (
                             len(odd), AWAY_FROM_HOME_CODE, sorted(set(odd["E_ANZ_WEGE"]))[:5]))
    work["away"] = (n_trips == AWAY_FROM_HOME_CODE).to_numpy()

    weight = pd.to_numeric(work["GEWICHT_P_ZENSUS"], errors="coerce")
    valid_weight = (weight.notna() & (weight >= 0)).to_numpy()
    n_invalid_weight = int((~valid_weight).sum())
    _log_exclusion("away-all-day share: valid GEWICHT_P_ZENSUS", n_invalid_weight, n_total)
    work = work[valid_weight].copy()
    work["weight"] = pd.to_numeric(work["GEWICHT_P_ZENSUS"]).astype(float)

    kreis_by_household = households[["HHNR", "AGS"]].copy()
    kreis_by_household["kreis"] = kreis_from_ags(kreis_by_household["AGS"])
    n_before_kreis = len(work)
    work = work.merge(kreis_by_household[["HHNR", "kreis"]], on="HHNR", how="left", validate="m:1")
    n_missing_kreis = int(work["kreis"].isna().sum())
    _log_exclusion("away-all-day share: home Kreis resolvable from the household AGS",
                   n_missing_kreis, n_before_kreis)
    work = work[work["kreis"].notna()].copy()
    outside_zgb = sorted(set(work.loc[~work["kreis"].isin(ZGB_KREISE), "kreis"]))
    if outside_zgb:
        raise ValueError("universe drift: persons resolve to a Kreis outside the surveyed ZGB "
                         "Kreise (found %s, expected a subset of %s)" % (outside_zgb,
                                                                          list(ZGB_KREISE)))

    code = pd.to_numeric(work["V_ERW"], errors="coerce")
    unreadable = (code.isna() | (code < 0)).to_numpy()
    undocumented = sorted(set(code[~unreadable & ~code.isin(sorted(V_ERW_CODES))].tolist()))
    if undocumented:
        raise ValueError("persons: column V_ERW carries code(s) %s outside the documented set %s"
                         % (undocumented, sorted(V_ERW_CODES)))
    n_unreadable = int(unreadable.sum())
    _log_exclusion("away-all-day share: readable employment status (V_ERW present and "
                   "non-negative)", n_unreadable, len(work))
    work = work[~unreadable].copy()
    work["is_student"] = (pd.to_numeric(work["V_ERW"]) == STUDENT_V_ERW).to_numpy()

    age = pd.to_numeric(work["V_ALTER"], errors="coerce")
    adult = (age >= MIN_ADULT_AGE).to_numpy()
    groups = ((BASE_STUDENTS_ALL, work[adult & work["is_student"].to_numpy()]),
              (BASE_OTHER_ADULTS_ALL, work[adult & ~work["is_student"].to_numpy()]))
    diagnostics = {"n_persons_total": int(n_total), "n_excluded_invalid_weight": n_invalid_weight,
                   "n_excluded_missing_kreis": n_missing_kreis,
                   "n_excluded_unreadable_employment_code": n_unreadable,
                   "n_missing_age": int(age.isna().sum())}
    frames = []
    for base, group in groups:
        label = pd.Series(np.where(group["away"].to_numpy(), "yes", "no"), dtype=object)
        frames.append(parameter_rows("away_all_day", base, label, group["weight"]))
        diagnostics["n_%s" % base] = int(len(group))
        diagnostics["n_%s_away" % base] = int(group["away"].sum())
    return pd.concat(frames, ignore_index=True), diagnostics


# --------------------------------------------------------------------------- young adults
def build_young_adult_status_table(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Activity status of the 20 to 29 year olds per Kreis and region (who the students' age
    peers are).

    Persons of the participation universe aged :data:`AGE_BAND_20_29` (bounds inclusive) with a
    readable ``V_ERW``; status ``student`` (``V_ERW`` 7), ``employed`` (:data:`EMPLOYED_V_ERW`,
    which includes the apprenticeship code 8) or ``other``. One block of three status rows per
    :data:`srv_distance_targets.ZGB_KREISE` (Wolfsburg, not surveyed, has no person: zero counts,
    NaN shares) and one ``total`` block coded :data:`REGION_CODE`. Columns
    :data:`YOUNG_ADULT_STATUS_COLUMNS`; ``share`` is weighted within the Kreis, ``n_effective``
    the Kish size of the Kreis's persons, ``se_share`` the standard error of the share.
    """
    readable, diagnostics = _drop_unreadable_employment(universe)
    young = readable[(readable["age"] >= AGE_BAND_20_29[0])
                     & (readable["age"] <= AGE_BAND_20_29[1])].copy()
    code = young["V_ERW"]
    young["status"] = np.select([code == STUDENT_V_ERW, code.isin(EMPLOYED_V_ERW)],
                                list(YOUNG_ADULT_STATUSES[:2]), default=YOUNG_ADULT_STATUSES[2])
    by_kreis = _groups_by_kreis(young, ZGB_KREISE, "young-adult status")
    rows = []
    for level, code_value, group in ([(LEVEL_KREIS, kreis, by_kreis[kreis]) for kreis in ZGB_KREISE]
                                     + [(LEVEL_TOTAL, REGION_CODE, young)]):
        weight = group["weight"].astype(float).to_numpy()
        for status in YOUNG_ADULT_STATUSES:
            mask = (group["status"] == status).to_numpy()
            share, n_effective, se = _share_with_error(pd.Series(weight), mask)
            rows.append({"code": code_value, "level": level, "status": status,
                         "n_unweighted": int(mask.sum()),
                         "weight_sum": float(weight[mask].sum()), "share": share,
                         "n_effective": n_effective, "se_share": se})
    table = pd.DataFrame(rows, columns=YOUNG_ADULT_STATUS_COLUMNS)
    diagnostics["n_young_adults"] = int(len(young))
    logger.info("%s young-adult status table: %d persons aged %d to %d", _LOG_TAG, len(young),
                *AGE_BAND_20_29)
    return table, diagnostics
