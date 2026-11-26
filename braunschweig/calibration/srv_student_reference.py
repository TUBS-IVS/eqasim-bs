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

Two more Phase 1 builders serve the post-MATSim analysis and the education-place reference:
:func:`build_post_matsim_reference` (main-mode shares of all and of the education legs, mean daily
distance and travel time of the adult students, standard errors from the person bootstrap
:func:`bootstrap_standard_error`; the daily totals exist only for persons at home with only valid
trips, see :data:`DAILY_TOTAL_SUB_UNIVERSE`) and :func:`build_destination_reference` (the Kreis group
of the first education leg's destination per home group, no distances).

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
    WORK_E_ZWECK_9, persons_with_purpose, prepare_universe_persons, require_average_weekday)
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
    bootstrap_share_noise_floor, parameter_rows, person_day_labels)
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
POST_MATSIM_TABLE = "srv2023_student_post_matsim_reference.csv"
DESTINATION_TABLE = "srv2023_student_education_destination.csv"

# Post-MATSim comparison values. E_HVM_5 (main mode of a trip): 1 walk, 2 bicycle, 3 car driver,
# 4 car passenger, 5 public transport; -7 not computable (a negative or missing code is excluded
# and counted, a non-negative code outside the set raises).
MODE_BY_E_HVM_5 = {1: "walk", 2: "bicycle", 3: "car_driver", 4: "car_passenger", 5: "pt"}
QUANTITY_MODE_ALL_LEGS = "main_mode_all_legs"
QUANTITY_MODE_EDUCATION_LEGS = "main_mode_education_legs"
QUANTITY_DAILY_DISTANCE = "daily_distance_km_mean"
QUANTITY_DAILY_TRAVEL_TIME = "daily_travel_time_min_mean"
MEAN_LABEL = "mean"
POST_MATSIM_COLUMNS = ["quantity", "label", "n_unweighted", "weight_sum", "value", "se_value"]
POST_MATSIM_PERSON_COLUMNS = ["HHNR", "PNR", "E_VERKEHRSLEIST", "E_DAUER_GESAMT"]
POST_MATSIM_LEG_COLUMNS = ["HHNR", "PNR", "E_ZWECK_9", "E_HVM_5"]
NOT_COMPUTABLE_DAILY_TOTAL = -7
# E_VERKEHRSLEIST (km) and E_DAUER_GESAMT (min) exist only for persons at home on the reporting
# day whose trips are ALL valid (below 100 km, duration and length known); -7 otherwise. The
# consumer of the table states this sub-universe in the CSV header.
DAILY_TOTAL_SUB_UNIVERSE = ("adult students at home on the reporting day with only valid trips "
                            "(below 100 km, duration and length known); persons with another "
                            "day carry -7 and are excluded")

# Education-destination reference (home group x destination group).
HOME_GROUP_BRAUNSCHWEIG = "braunschweig"
HOME_GROUP_SITE = "other_site_kreis"
HOME_GROUP_NO_SITE = "no_site_kreis"
HOME_GROUP_ALL = "region"
HOME_GROUPS = (HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_SITE, HOME_GROUP_NO_SITE, HOME_GROUP_ALL)
DEST_OWN = "own_kreis"
DEST_SITE_PREFIX = "site_kreis_"
DEST_OTHER_REGION = "other_region_kreis"
DEST_REGION_HANNOVER = "region_hannover"
DEST_LK_HILDESHEIM = "landkreis_hildesheim"
DEST_OTHER_OUTSIDE = "other_outside"
BRAUNSCHWEIG_KREIS = "03101"
REGION_HANNOVER_ARS5 = "03241"
LANDKREIS_HILDESHEIM_ARS5 = "03254"
DESTINATION_LEG_COLUMNS = ["HHNR", "PNR", "WNR", "E_ZWECK_9", "V_BEGINN_STUNDE",
                           "V_BEGINN_MINUTE", "V_ZIEL_AGS"]
DESTINATION_COLUMNS = ["home_group", "destination_group", "n_persons_group", "n_unweighted",
                       "weight_sum", "share", "n_effective", "se_share", "noise_floor_tvd_95"]

# Raw columns the parameter table reads on top of the student universe: the person columns come
# from the raw person file (V_FUEHR_PKW and E_OEV_FK are read from the universe, which
# student_universe fills from the same file), the household columns from the raw household file.
PARAMETER_PERSON_COLUMNS = ["V_GESCHLECHT", "E_ZUGANG_PKW"]
PARAMETER_HOUSEHOLD_COLUMNS = ["HHNR", "E_HHTYP", "V_ANZ_PKW", "E_ANZ_RAD_ALLE_6"]
# The away-all-day share is measured on the raw person file; MITTL_WERKTAG is the universe guard.
AWAY_PERSON_COLUMNS = ["HHNR", "PNR", "V_ALTER", "V_ERW", "E_ANZ_WEGE", "GEWICHT_P_ZENSUS",
                       "MITTL_WERKTAG"]

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


def _log_rate(step: str, kind: str, n_flagged: int, n_total: int) -> bool:
    """Log ``primary n/N (rate), <kind> m (rate)``; True if the rate is above the limit.

    The shared core of :func:`_log_exclusion` and :func:`_log_fallback`: the rate computation,
    the log line and the :data:`MAX_EXCLUSION_RATE` check. The callers raise with their own text.
    """
    rate = n_flagged / n_total if n_total else float("nan")
    logger.info("%s %s: primary %d/%d (%.1f%%), %s %d (%.1f%%)", _LOG_TAG, step,
                n_total - n_flagged, n_total, 100.0 * (1.0 - rate) if n_total else float("nan"),
                kind, n_flagged, 100.0 * rate if n_total else float("nan"))
    return bool(n_total) and rate > MAX_EXCLUSION_RATE


def _log_exclusion(step: str, n_excluded: int, n_total: int) -> None:
    """Log ``primary n/N (rate), excluded m (rate)``; raise above :data:`MAX_EXCLUSION_RATE`."""
    if _log_rate(step, "excluded", n_excluded, n_total):
        raise ValueError(
            "%s %s: %d of %d rows (%.1f%%) would be excluded, above the %.0f%% limit; this is a "
            "broken code mapping or a recoded variable, not a measurement"
            % (_LOG_TAG, step, n_excluded, n_total, 100.0 * n_excluded / n_total,
               100.0 * MAX_EXCLUSION_RATE))


def _log_fallback(step: str, n_fallback: int, n_total: int) -> None:
    """Log ``primary n/N (rate), fallback m (rate)``; raise above :data:`MAX_EXCLUSION_RATE`.

    For a path that REPLACES the primary method without dropping anything (nothing is excluded).
    A fallback rate above the limit means the primary method is broken (a recoded column), so it
    raises like a high exclusion rate does.
    """
    if _log_rate(step, "fallback", n_fallback, n_total):
        raise ValueError(
            "%s %s: the fallback served %d of %d rows (%.1f%%), above the %.0f%% limit; the "
            "primary method is broken (a recoded or missing column), not a measurement"
            % (_LOG_TAG, step, n_fallback, n_total, 100.0 * n_fallback / n_total,
               100.0 * MAX_EXCLUSION_RATE))


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
    raw frames it was built from (``persons`` must also carry :data:`PARAMETER_PERSON_COLUMNS`
    and ``V_ALTER`` of every person of a household, ``households``
    :data:`PARAMETER_HOUSEHOLD_COLUMNS`; the licence and ticket codes are read from the
    universe); ``harmonised_persons`` and
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

    A student without a valid age is no adult student and is in no base of this table. The
    universe diagnostics count ``n_missing_age`` over ALL persons, not over the students, so this
    function counts the students concerned itself (``n_students_missing_age``, over the students
    with a readable ``V_ERW``) and logs them through the exclusion-rate rule.

    Returns ``(table, diagnostics)``. Besides the exclusion counts (``n_excluded_<parameter>``,
    over ``n_adult_students``; for the four day parameters on a smaller base also
    ``n_base_<parameter>``, the mobile students or those with an education leg, so each rate can
    be recomputed from the header) the diagnostics carry the weighted SrV share of adult students
    with an education leg under both definitions:
    ``education_leg_share_harmonised_purpose`` (the ``education_leg`` rows of this table: a trip
    with harmonised purpose education) and ``education_leg_share_control_definition``
    (``E_ZWECK_9`` in :data:`srv_participation_universe.EDUCATION_E_ZWECK_9`, the definition of
    the behaviour table and of the day factor), each over all adult students of the universe.
    """
    _require_columns(universe, ["pid", "weight", "age", "is_student", "V_FUEHR_PKW", "E_OEV_FK"],
                     "universe")
    _require_columns(persons, ["HHNR", "PNR", "V_ALTER", *PARAMETER_PERSON_COLUMNS], "persons")
    _require_columns(households, PARAMETER_HOUSEHOLD_COLUMNS, "households")
    readable, diagnostics = _drop_unreadable_employment(universe)
    all_students = readable[readable["is_student"]]
    n_missing_age = int(all_students["age"].isna().sum())
    _log_exclusion("parameter table: students with a valid age (a student without an age is no "
                   "adult student)", n_missing_age, len(all_students))
    diagnostics["n_students_missing_age"] = n_missing_age
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
        # the base differs from all adult students: its size is kept so the rate is recomputable
        diagnostics["n_base_%s" % parameter] = int(in_base.sum())
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
    raises). Adults: age at least :data:`MIN_ADULT_AGE`; a person without a valid age is in
    neither base (``n_missing_age``, counted over the persons that reach this step and logged
    through the same exclusion-rate rule); students: ``V_ERW`` 7, the other adults everybody else.
    The weekday guard of the universe (``MITTL_WERKTAG`` is the average weekday, see
    :func:`srv_participation_universe.require_average_weekday`) applies to the whole file first.
    Returns the :data:`PARAMETER_COLUMNS` rows of base
    :data:`student_parameters.BASE_STUDENTS_ALL` and :data:`student_parameters.BASE_OTHER_ADULTS_ALL`.
    Raises ``ValueError`` for a missing column, a foreign ``MITTL_WERKTAG``, a duplicated person
    key or an ``E_ANZ_WEGE`` that is missing or negative but not the away code (the universe
    guards of the participation tables).
    """
    _require_columns(persons, AWAY_PERSON_COLUMNS, "persons")
    _require_columns(households, ["HHNR", "AGS"], "households")
    require_average_weekday(persons)
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

    # A person without a valid age is neither adult nor not adult: in no base, counted and logged
    # like the other exclusions (over the persons that reach this step).
    age = pd.to_numeric(work["V_ALTER"], errors="coerce")
    age = age.where(age >= 0)
    n_missing_age = int(age.isna().sum())
    _log_exclusion("away-all-day share: valid age (V_ALTER present and non-negative; no age, no "
                   "adult)", n_missing_age, len(work))
    adult = (age >= MIN_ADULT_AGE).to_numpy()
    groups = ((BASE_STUDENTS_ALL, work[adult & work["is_student"].to_numpy()]),
              (BASE_OTHER_ADULTS_ALL, work[adult & ~work["is_student"].to_numpy()]))
    diagnostics = {"n_persons_total": int(n_total), "n_excluded_invalid_weight": n_invalid_weight,
                   "n_excluded_missing_kreis": n_missing_kreis,
                   "n_excluded_unreadable_employment_code": n_unreadable,
                   "n_missing_age": n_missing_age}
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
    # a person without a valid age cannot be placed in the age band: counted and logged
    n_missing_age = int(readable["age"].isna().sum())
    _log_exclusion("young-adult status: valid age (V_ALTER present and non-negative)",
                   n_missing_age, len(readable))
    # own key: the universe diagnostics already carry n_missing_age, measured on a larger base
    diagnostics["n_young_adult_base_missing_age"] = n_missing_age
    young =readable[(readable["age"] >= AGE_BAND_20_29[0])
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


# --------------------------------------------------------------------------- bootstrap helper
def bootstrap_standard_error(values_by_person, weights, n_bootstrap: int, seed: int) -> float:
    """Standard error of a person-weighted mean over observations that cluster in persons.

    ``values_by_person`` holds one array of observations per person (one number per person for a
    daily total, the 0/1 indicators of the person's legs for a mode share) and ``weights`` the
    person weight; every observation of a person carries that weight. The statistic is
    ``sum_i w_i sum_j v_ij / sum_i w_i n_i`` (``n_i`` the observations of person ``i``). The
    estimator is a seeded PERSON bootstrap: ``n`` persons are drawn with replacement, taking their
    whole observation arrays along (legs of one person are not independent, so a leg-level
    resample would understate the error), the statistic is recomputed per draw, and the standard
    error is the sample standard deviation (``ddof=1``) of the ``n_bootstrap`` statistics. A draw
    without any observation has no statistic: it is skipped, counted and logged as a warning with
    its rate. Returns NaN for fewer than two persons. Raises ``ValueError`` for
    ``n_bootstrap < 2``, a weight that is negative or not finite, unequal lengths, or fewer than
    two usable draws.
    """
    if n_bootstrap < 2:
        raise ValueError("n_bootstrap must be >= 2 (a standard error needs two draws)")
    arrays = [np.asarray(values, dtype=float).ravel() for values in values_by_person]
    weight = np.asarray(weights, dtype=float)
    if len(arrays) != weight.size:
        raise ValueError("%d persons but %d weights" % (len(arrays), weight.size))
    if not np.isfinite(weight).all() or (weight < 0).any():
        raise ValueError("weights must be finite and >= 0")
    n_persons = len(arrays)
    if n_persons < 2:
        return float("nan")
    numerator = weight * np.array([array.sum() for array in arrays])
    denominator = weight * np.array([array.size for array in arrays], dtype=float)
    rng = np.random.default_rng(seed)
    statistics = []
    for _ in range(n_bootstrap):
        drawn = rng.integers(0, n_persons, n_persons)
        total = denominator[drawn].sum()
        if total > 0:
            statistics.append(numerator[drawn].sum() / total)
    n_skipped = n_bootstrap - len(statistics)
    if n_skipped:
        logger.warning("%s bootstrap standard error: primary %d/%d draws, skipped %d (%.1f%%) "
                       "without any weighted observation", _LOG_TAG, len(statistics), n_bootstrap,
                       n_skipped, 100.0 * n_skipped / n_bootstrap)
    if len(statistics) < 2:
        raise ValueError("bootstrap standard error: fewer than two draws held a weighted "
                         "observation")
    return float(np.std(statistics, ddof=1))


# --------------------------------------------------------------------------- post-MATSim values
def _adult_students(universe: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    readable, diagnostics = _drop_unreadable_employment(universe)
    adults = _adults(readable)
    return adults[adults["is_student"]].reset_index(drop=True), diagnostics


def _mode_rows(quantity: str, legs: pd.DataFrame, n_bootstrap: int, seed: int) -> list:
    """One row per mode label; ``legs`` holds the valid legs (``pid``, ``weight``, ``mode``)."""
    persons = list(legs.groupby("pid", sort=True))
    person_weight = [float(group["weight"].iloc[0]) for _, group in persons]
    person_modes = [group["mode"].to_numpy() for _, group in persons]
    leg_weight = legs["weight"].to_numpy(dtype=float)
    total_weight = float(leg_weight.sum())
    rows = []
    for label in MODE_BY_E_HVM_5.values():
        mask = (legs["mode"] == label).to_numpy()
        weight_sum = float(leg_weight[mask].sum())
        if total_weight > 0:
            value = weight_sum / total_weight
            se = bootstrap_standard_error([(modes == label).astype(float) for modes in person_modes],
                                          person_weight, n_bootstrap, seed)
        else:
            value = se = float("nan")
        rows.append({"quantity": quantity, "label": label, "n_unweighted": int(mask.sum()),
                     "weight_sum": weight_sum, "value": value, "se_value": se})
    return rows


def _mean_row(quantity: str, weight: pd.Series, value: pd.Series, n_bootstrap: int,
              seed: int) -> dict:
    """Weighted mean of one value per person; ``weight`` and ``value`` cover the valid persons."""
    weight_values = weight.to_numpy(dtype=float)
    total = float(weight_values.sum())
    if len(value) and total > 0:
        mean = float((weight_values * value.to_numpy(dtype=float)).sum() / total)
        se = bootstrap_standard_error([[v] for v in value.to_numpy(dtype=float)], weight_values,
                                      n_bootstrap, seed)
    else:
        mean = se = float("nan")
    return {"quantity": quantity, "label": MEAN_LABEL, "n_unweighted": int(len(value)),
            "weight_sum": total, "value": mean, "se_value": se}


def build_post_matsim_reference(universe: pd.DataFrame, persons: pd.DataFrame,
                                legs: pd.DataFrame, n_bootstrap: int,
                                seed: int) -> tuple[pd.DataFrame, dict]:
    """SrV values the post-MATSim analysis compares the model's adult students against.

    Persons: the adult students (``V_ERW`` 7, age at least :data:`MIN_ADULT_AGE`) of
    ``universe`` (:func:`student_universe`), weight ``GEWICHT_P_ZENSUS``. ``persons`` is the raw
    person frame (:data:`POST_MATSIM_PERSON_COLUMNS`, joined 1:1 on the person key), ``legs`` the
    raw leg frame (:data:`POST_MATSIM_LEG_COLUMNS`). Columns :data:`POST_MATSIM_COLUMNS`:

    * ``main_mode_all_legs`` / ``main_mode_education_legs``: per mode of :data:`MODE_BY_E_HVM_5`
      the weighted share of the legs (every leg of an adult student, or only its education legs,
      ``E_ZWECK_9`` in :data:`EDUCATION_E_ZWECK_9`), each leg weighted with its PERSON's weight;
      ``n_unweighted`` counts legs, ``weight_sum`` is the weight carried by those legs. A leg
      with a negative or missing ``E_HVM_5`` is excluded from the base, counted and logged; a
      non-negative code outside the documented set raises.
    * ``daily_distance_km_mean`` / ``daily_travel_time_min_mean`` (``E_VERKEHRSLEIST`` in km,
      ``E_DAUER_GESAMT`` in min), label ``mean``: the weighted mean over the adult students with a
      non-negative value; ``n_unweighted`` counts persons. SUB-UNIVERSE
      (:data:`DAILY_TOTAL_SUB_UNIVERSE`, which the CSV header states; the diagnostics hold counts
      only):
      the codebook defines both totals only for persons at home on the reporting day with ONLY
      valid trips (below 100 km, duration and length known); every other person carries ``-7``
      (:data:`NOT_COMPUTABLE_DAILY_TOTAL`). The base of these two quantities IS that
      sub-universe, a documented definition and not a fallback: the ``-7`` count and rate are
      logged and returned in the diagnostics (for the CSV header), and a rate above
      :data:`student_behaviour_labels.MAX_EXCLUSION_RATE` is a logged WARNING, not an error,
      so a broken column stays visible. Any other negative code is undocumented and raises; a
      missing value is excluded and counted under the strict exclusion-rate rule (raises above
      the limit).

    ``se_value`` comes from :func:`bootstrap_standard_error` (a seeded person bootstrap, because
    legs cluster within persons; persons without an observation of the quantity are not part of
    its base); NaN where the base is empty or holds one person. A quantity with an empty base
    carries NaN, never 0.0. Diagnostics: ``n_adult_students``, ``n_legs_all`` /
    ``n_excluded_mode_all_legs``, ``n_legs_education`` / ``n_excluded_mode_education_legs``,
    ``n_not_computable_daily_distance`` / ``n_not_computable_daily_travel_time`` (the ``-7``
    count of the sub-universe rule below), ``n_missing_*`` and ``n_base_*`` of both.
    """
    if n_bootstrap < 2:
        raise ValueError("n_bootstrap must be >= 2 (a standard error needs two draws)")
    _require_columns(persons, POST_MATSIM_PERSON_COLUMNS, "persons")
    _require_columns(legs, POST_MATSIM_LEG_COLUMNS, "legs")
    students, diagnostics = _adult_students(universe)
    diagnostics["n_adult_students"] = int(len(students))

    totals = persons[POST_MATSIM_PERSON_COLUMNS].copy()
    totals["pid"] = _person_ids(totals)
    _unique_keyed(totals["pid"], "persons: person key HHNR_PNR")
    lost = sorted(set(students["pid"]) - set(totals["pid"]))
    if lost:
        raise ValueError("universe students missing from persons: %s" % lost[:10])
    students = students.merge(totals[["pid", "E_VERKEHRSLEIST", "E_DAUER_GESAMT"]], on="pid",
                              how="left", validate="1:1")

    work = legs[POST_MATSIM_LEG_COLUMNS].copy()
    work["pid"] = _person_ids(work)
    work = work[work["pid"].isin(set(students["pid"]))]
    work = work.merge(students[["pid", "weight"]], on="pid", how="left", validate="m:1")
    work["mode_code"] = pd.to_numeric(work["E_HVM_5"], errors="coerce")
    undocumented = sorted(set(work.loc[(work["mode_code"] >= 0)
                                       & ~work["mode_code"].isin(sorted(MODE_BY_E_HVM_5)),
                                       "mode_code"].tolist()))
    if undocumented:
        raise ValueError("legs: column E_HVM_5 carries code(s) %s outside the documented set %s "
                         "(negative or missing codes are excluded)"
                         % (undocumented, sorted(MODE_BY_E_HVM_5)))
    work["mode"] = work["mode_code"].map(MODE_BY_E_HVM_5)
    purpose = pd.to_numeric(work["E_ZWECK_9"], errors="coerce")
    is_education = purpose.isin(sorted(EDUCATION_E_ZWECK_9)).to_numpy()

    rows = []
    for quantity, key, frame in ((QUANTITY_MODE_ALL_LEGS, "all", work),
                                 (QUANTITY_MODE_EDUCATION_LEGS, "education",
                                  work[is_education])):
        valid = frame[frame["mode"].notna()]
        n_excluded = int(len(frame) - len(valid))
        _log_exclusion("%s (E_HVM_5 readable, non-negative; legs of adult students)" % quantity,
                       n_excluded, len(frame))
        diagnostics["n_legs_%s" % key] = int(len(frame))
        diagnostics["n_excluded_mode_%s_legs" % key] = n_excluded
        rows += _mode_rows(quantity, valid, n_bootstrap, seed)

    for quantity, column, key in ((QUANTITY_DAILY_DISTANCE, "E_VERKEHRSLEIST", "daily_distance"),
                                  (QUANTITY_DAILY_TRAVEL_TIME, "E_DAUER_GESAMT",
                                   "daily_travel_time")):
        value = pd.to_numeric(students[column], errors="coerce")
        # -7 is the codebook's structural "not computable" code: the definition of the
        # sub-universe, not a data defect. Any other negative code is undocumented and raises.
        other_negative = sorted(set(value[(value < 0) & (value != NOT_COMPUTABLE_DAILY_TOTAL)]))
        if other_negative:
            raise ValueError("persons: column %s carries negative code(s) %s; only %d (not "
                             "computable) is documented" % (column, other_negative,
                                                            NOT_COMPUTABLE_DAILY_TOTAL))
        # A missing value is no documented code: a broken column, so the strict rule applies.
        _log_exclusion("%s (%s present)" % (quantity, column), int(value.isna().sum()),
                       len(students))
        n_not_computable = int((value == NOT_COMPUTABLE_DAILY_TOTAL).sum())
        rate = n_not_computable / len(students) if len(students) else float("nan")
        logger.info("%s %s sub-universe (%s): in the base %d/%d (%.1f%%), not computable "
                    "(%d) %d (%.1f%%)", _LOG_TAG, quantity, DAILY_TOTAL_SUB_UNIVERSE,
                    len(students) - n_not_computable, len(students), 100.0 * (1.0 - rate),
                    NOT_COMPUTABLE_DAILY_TOTAL, n_not_computable, 100.0 * rate)
        if rate > MAX_EXCLUSION_RATE:
            logger.warning("%s %s: %.1f%% of the adult students carry the not-computable code "
                           "%d, above %.0f%%: expected only if few students have exclusively "
                           "valid trips; otherwise check the column", _LOG_TAG, quantity,
                           100.0 * rate, NOT_COMPUTABLE_DAILY_TOTAL,
                           100.0 * MAX_EXCLUSION_RATE)
        diagnostics["n_not_computable_%s" % key] = n_not_computable
        diagnostics["n_missing_%s" % key] = int(value.isna().sum())
        valid = (value >= 0).to_numpy()
        diagnostics["n_base_%s" % key] = int(valid.sum())
        rows.append(_mean_row(quantity, students.loc[valid, "weight"], value[valid], n_bootstrap,
                              seed))
    table = pd.DataFrame(rows, columns=POST_MATSIM_COLUMNS)
    logger.info("%s post-MATSim reference: %d rows, %d adult students, %d legs", _LOG_TAG,
                len(table), len(students), len(work))
    return table, diagnostics


# --------------------------------------------------------------------------- education destination
def _validate_site_kreise(site_kreise) -> frozenset:
    sites = frozenset(site_kreise)
    if BRAUNSCHWEIG_KREIS not in sites:
        raise ValueError("site_kreise %s must contain %s (the Braunschweig home group is "
                         "defined by it)" % (sorted(sites), BRAUNSCHWEIG_KREIS))
    outside = sorted(sites - set(ZGB_KREISE))
    if outside:
        raise ValueError("site_kreise %s lie outside the region Kreise %s"
                         % (outside, list(ZGB_KREISE)))
    return sites


def _destination_vocabulary(site_kreise: frozenset, home_group: str) -> list:
    """Destination groups of a home group; the Braunschweig group has no 'site' 03101."""
    sites = sorted(site_kreise - ({BRAUNSCHWEIG_KREIS} if home_group == HOME_GROUP_BRAUNSCHWEIG
                                  else set()))
    return [DEST_OWN, *(DEST_SITE_PREFIX + kreis for kreis in sites), DEST_OTHER_REGION,
            DEST_REGION_HANNOVER, DEST_LK_HILDESHEIM, DEST_OTHER_OUTSIDE]


def _destination_group(destination: str, home: str, site_kreise: frozenset) -> str:
    if destination == home:
        return DEST_OWN
    if destination in site_kreise:
        return DEST_SITE_PREFIX + destination
    if destination in ZGB_KREISE:
        return DEST_OTHER_REGION
    if destination == REGION_HANNOVER_ARS5:
        return DEST_REGION_HANNOVER
    if destination == LANDKREIS_HILDESHEIM_ARS5:
        return DEST_LK_HILDESHEIM
    return DEST_OTHER_OUTSIDE


def _first_education_legs(education_legs: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """The first education leg per person, ordered by start hour, start minute, then ``WNR``.

    A person with a negative or missing start hour or minute on ANY education leg has no
    determinable time order: that person is ordered by ``WNR`` alone (a fallback, counted and
    logged as a rate, never dropped). Returns ``(first legs, number of such persons)``.
    """
    legs = education_legs.copy()
    for column, name in (("V_BEGINN_STUNDE", "hour"), ("V_BEGINN_MINUTE", "minute"),
                         ("WNR", "wnr")):
        legs[name] = pd.to_numeric(legs[column], errors="coerce")
    if legs["wnr"].isna().any():
        raise ValueError("legs: column WNR is missing or non-numeric on %d education leg(s) of "
                         "adult students; the leg order is undeterminable"
                         % int(legs["wnr"].isna().sum()))
    bad_time = (legs["hour"].isna() | (legs["hour"] < 0) | legs["minute"].isna()
                | (legs["minute"] < 0))
    fallback = set(legs.loc[bad_time, "pid"])
    by_number = legs["pid"].isin(fallback).to_numpy()
    legs["hour"] = legs["hour"].where(~by_number, 0)
    legs["minute"] = legs["minute"].where(~by_number, 0)
    first = (legs.sort_values(["pid", "hour", "minute", "wnr"], kind="stable")
             .drop_duplicates("pid", keep="first"))
    _log_fallback("first education leg ordered by start hour and minute (fallback: leg number "
                  "WNR where a start hour or minute is negative or missing; no person is "
                  "dropped)", len(fallback), len(first))
    return first, len(fallback)


def build_destination_reference(universe: pd.DataFrame, legs: pd.DataFrame,
                                site_kreise: frozenset, n_bootstrap: int,
                                seed: int) -> tuple[pd.DataFrame, dict]:
    """Where the SrV adult students go for education, per home group (no distances).

    Persons: the adult students of ``universe`` with at least one education leg
    (``E_ZWECK_9`` in :data:`EDUCATION_E_ZWECK_9`). Destination: ``V_ZIEL_AGS`` of the FIRST
    education leg (ordered by :func:`_first_education_legs`: start hour, start minute, ``WNR``;
    the Phase 0 arrival control orders by hour and ``WNR`` only and is unchanged), zero-padded to
    eight digits (a number loses its leading zero), the first five digits the Kreis; a missing or
    ``<= 0`` code is unknown, excluded and counted (``n_excluded_unknown_destination``).

    ``site_kreise`` are the region Kreise with a local site; it must contain ``03101`` and lie
    inside :data:`srv_distance_targets.ZGB_KREISE` (``ValueError`` otherwise). Home group from
    the household Kreis: :data:`HOME_GROUP_BRAUNSCHWEIG`, :data:`HOME_GROUP_SITE` (another site
    Kreis), :data:`HOME_GROUP_NO_SITE`, and :data:`HOME_GROUP_ALL` over all three. Destination
    group: :data:`DEST_OWN` (the person's own Kreis), ``site_kreis_<ars5>`` (a site Kreis other
    than the own one), :data:`DEST_OTHER_REGION` (another region Kreis), Region Hannover
    (:data:`REGION_HANNOVER_ARS5`), Landkreis Hildesheim (:data:`LANDKREIS_HILDESHEIM_ARS5`),
    :data:`DEST_OTHER_OUTSIDE`. Every group of a home group's vocabulary is emitted, an empty one
    with ``n_unweighted`` 0 and a NaN share (a cell can be empty by construction, e.g. a person
    never goes to their own Kreis as a ``site_kreis_``).

    The cells are POOLED across the home Kreise of a group: with more than one site Kreis,
    :data:`HOME_GROUP_SITE` and :data:`HOME_GROUP_ALL` mix persons of different home Kreise, so
    ``own_kreis`` is "the person's own Kreis" whichever it is, and ``site_kreis_<ars5>`` is empty
    for the persons living in ``<ars5>`` (their trips there are ``own_kreis``). Read these cells
    as shares of a pooled group, never per Kreis.

    Columns :data:`DESTINATION_COLUMNS`: ``n_persons_group`` is the persons of the home group
    (the share's base), ``share`` the weighted share (``GEWICHT_P_ZENSUS``), ``n_effective`` the
    Kish size of the home group's base, ``se_share`` its standard error, and
    ``noise_floor_tvd_95`` the 95th percentile of the total-variation distance a person
    bootstrap of the home group reaches by sampling noise alone
    (:func:`student_parameters.bootstrap_share_noise_floor`, seeded, over the home group's
    groups; NaN for an empty home group or one whose weights sum to zero, counted as
    ``n_home_groups_zero_weight`` and logged; 0.0 for a single person).
    """
    sites = _validate_site_kreise(site_kreise)
    if n_bootstrap < 2:
        raise ValueError("n_bootstrap must be >= 2 (a standard error needs two draws)")
    _require_columns(legs, DESTINATION_LEG_COLUMNS, "legs")
    students, diagnostics = _adult_students(universe)

    purpose = pd.to_numeric(legs["E_ZWECK_9"], errors="coerce")
    education = legs[purpose.isin(sorted(EDUCATION_E_ZWECK_9)).to_numpy()].copy()
    education["pid"] = _person_ids(education)
    education = education[education["pid"].isin(set(students["pid"]))]
    first, n_by_leg_number = _first_education_legs(education)
    n_with_education = len(first)
    diagnostics.update(n_adult_students=int(len(students)),
                       n_adult_students_with_education_leg=int(n_with_education),
                       n_adult_students_without_education_leg=int(len(students) - n_with_education),
                       n_persons_ordered_by_leg_number=int(n_by_leg_number))

    destination = pd.to_numeric(first["V_ZIEL_AGS"], errors="coerce")
    unknown = (destination.isna() | (destination <= 0)).to_numpy()
    n_unknown = int(unknown.sum())
    _log_exclusion("first education leg with a known destination (unknown destination: "
                   "V_ZIEL_AGS missing or <= 0)", n_unknown, n_with_education)
    diagnostics["n_excluded_unknown_destination"] = n_unknown
    known = first[~unknown].copy()
    ags = destination[~unknown].astype("int64")
    if (ags > 99999999).any():
        raise ValueError("legs: column V_ZIEL_AGS carries value(s) %s with more than 8 digits"
                         % sorted(set(ags[ags > 99999999]))[:5])
    known["destination_kreis"] = ags.astype(str).str.zfill(8).str[:5]
    persons = students[["pid", "kreis", "weight"]].merge(
        known[["pid", "destination_kreis"]], on="pid", how="inner", validate="1:1")
    persons["destination_group"] = [
        _destination_group(dest, home, sites)
        for dest, home in zip(persons["destination_kreis"], persons["kreis"])]
    persons["home_group"] = np.where(
        persons["kreis"] == BRAUNSCHWEIG_KREIS, HOME_GROUP_BRAUNSCHWEIG,
        np.where(persons["kreis"].isin(sorted(sites)), HOME_GROUP_SITE, HOME_GROUP_NO_SITE))

    rows = []
    n_zero_weight_groups = 0
    for home_group in HOME_GROUPS:
        group = persons if home_group == HOME_GROUP_ALL else persons[
            persons["home_group"] == home_group]
        vocabulary = _destination_vocabulary(sites, home_group)
        unexpected = sorted(set(group["destination_group"]) - set(vocabulary))
        if unexpected:
            raise ValueError("destination group(s) %s are outside the vocabulary %s of home "
                             "group %s" % (unexpected, vocabulary, home_group))
        weight = group["weight"].to_numpy(dtype=float)
        n_effective = effective_sample_size(weight)
        # An empty home group, or one whose weights sum to zero, has no defined shares and no
        # bootstrap (the helper raises on a zero weight total): its floor is NaN, counted.
        if len(group) and weight.sum() > 0:
            noise = bootstrap_share_noise_floor(group["destination_group"], weight, vocabulary,
                                                n_bootstrap, seed)
        else:
            noise = float("nan")
            if len(group):
                n_zero_weight_groups += 1
                logger.warning("%s home group %s: %d persons with a zero weight sum, shares and "
                               "noise floor are NaN", _LOG_TAG, home_group, len(group))
        diagnostics["n_persons_%s" % home_group] = int(len(group))
        for label in vocabulary:
            mask = (group["destination_group"] == label).to_numpy()
            share = _weighted_share(weight, mask)
            rows.append({"home_group": home_group, "destination_group": label,
                         "n_persons_group": int(len(group)), "n_unweighted": int(mask.sum()),
                         "weight_sum": float(weight[mask].sum()), "share": share,
                         "n_effective": n_effective,
                         "se_share": share_standard_error(share, n_effective),
                         "noise_floor_tvd_95": noise})
    diagnostics["n_home_groups_zero_weight"] = n_zero_weight_groups
    table = pd.DataFrame(rows, columns=DESTINATION_COLUMNS)
    logger.info("%s education destination reference: %d rows, %d persons with a known "
                "destination", _LOG_TAG, len(table), len(persons))
    return table, diagnostics
