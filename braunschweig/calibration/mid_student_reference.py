"""MiD 2023 aggregates of university students (issue #140, Phase 0 reference table R4).

Pure builders over already-loaded MiD person / trip / household frames (no I/O, no synpp).
``scripts/extract_mid_student_reference.py`` is the only caller that touches the restricted raw
microdata; it owns the CLI and the provenance headers of the four committed tables:

* :func:`build_status_by_age_table` -- who the persons with an education trip on a core weekday
  are (student, pupil, apprentice, other) per age band;
* :func:`build_distance_mode_table` -- distance band x main mode of the adult students' first
  education trip on a core weekday;
* :func:`build_share_20_29_table` -- the share of pupils and students among the 20 to 29 year
  olds, Germany and per Land (a status share, so over ALL reporting days);
* :func:`build_behaviour_seed_table` -- the candidate behaviour cells of
  :mod:`braunschweig.calibration.student_behaviour_labels` on the MiD side, the mirror of
  :func:`srv_student_reference.build_behaviour_table`.

Every builder returns ``(table, diagnostics)``.

Definitions (MiD 2023 codebook B1 as quoted in ``braunschweig/popsim/attributes.py``):

* Weight ``P_GEW`` (person weight); a missing or non-positive weight excludes the person.
* Core weekdays: ``ST_WOTAG`` in :data:`CORE_WEEKDAYS` (Tuesday to Thursday), the reporting-day
  universe of the SrV side. Every table but :func:`build_share_20_29_table` is restricted to it,
  because the others describe a reporting DAY and that one a person status.
* Public holidays (``exclude_holidays``, production value of
  ``braunschweig.population.popsim.exclude_holiday_plan_sources`` in ``configs/base_bs.yml``):
  True drops the persons whose reporting day was a public holiday at their residence
  (``feiertag == popsim.diary_plan_match.MID_HOLIDAY``) from every table restricted to the core
  weekdays, counted as ``n_holiday_reporting_day``, because the SrV reference days exclude public
  holidays and the popsim seed drops such diaries for the same reason
  (``diary_plan_match.classify_plan_sources``). ``feiertag`` must be 0 or 1; anything else
  raises. The status share of :func:`build_share_20_29_table` keeps every day, holidays included.
* Universe gap to the SrV side, not corrected here: MiD keeps persons who were away from home
  all day, the SrV universe drops them (``srv_student_reference.student_universe`` counts the
  students lost that way).
* Student = ``P_TAET == STUDENT_P_TAET``. ``P_TAET`` 99 (no answer) or a missing value answers
  nothing about the activity: the person is excluded from every table here and counted, never
  classed "not a student" or "other". A code outside the documented 1-17 raises.
* Adult = ``HP_ALTER >= MIN_ADULT_AGE``; the behaviour cells and the distance table are
  restricted to adults, like the SrV side. A person without a valid age is in no adult universe.
* Education leg / work leg = a trip whose ``W_ZWECK`` is in the education / work set of
  ``braunschweig.popsim.mid.participation.PARTICIPATION_W_ZWECK`` (the seed's sets). Its
  education set also carries the passive escort code (13) under the production flag
  ``escort_passive_education``; that leg is an escorted CHILD's own trip (``PARTICIPATION_W_ZWECK``
  escort comment), so it cannot change a result restricted to adults and is not added here. It
  does matter for the youngest age band of :func:`build_status_by_age_table`, which includes
  children: an escorted child whose only education-set leg is such a passive escort leg is NOT
  in that table (whether the plan realises it as education is a model pairing rule, not a survey
  fact), so the under-20 band describes children with a directly recorded education trip only.
* ``exclude_rbw_legs`` (production value of ``braunschweig.population.popsim.exclude_rbw_legs``
  in ``configs/base_bs.yml``): True counts only DIRECTLY recorded legs (``W_RBW == 0``), the legs
  the plan contains, exactly as ``participation.compute_has_direct_purpose_leg`` does for the
  seed. A missing or non-numeric ``W_RBW`` is read as a direct leg (0), like the seed.
* First education leg = per person the education leg with the earliest start (``W_SZS``, then
  ``W_SZM``; ties keep file order). A person whose start time is unreadable on ANY of their
  education legs (an hour outside 0-23, a minute outside 0-59, e.g. the filter code 99) makes the
  order undeterminable and is excluded from the control or table that needs the first leg,
  counted.
* Arrival = ``W_AZS`` of the first education leg (right-inclusive bins
  :data:`student_behaviour_labels.ARRIVAL_HOUR_EDGES`); 99 or a missing hour excludes the student
  from the arrival control only. The arrival control's exclusion rate is measured over the
  students WITH a diary of the day AND an education leg, the only ones that can lose a label
  there: students without a diary have already left the arrival control (see "Diary of the
  day" below) and are counted there, not in this rate.
* Diary of the day (behaviour table only): ``anzwege1`` 0 to 799 is a real trip count. The
  no-diary codes 803 / 804 (``popsim.diary_plan_match.NO_DIARY_CODES``) are an UNKNOWN day, never
  "no trip", exactly as the popsim seed reads them (``participation.compute_has_purpose_trip``,
  ``diary_plan_match``), with the one exception that diary_plan_match makes too: ``803`` with
  ``mobil != 1`` is a person who was not mobile, zero trips is what they reported, and the day
  counts as 0 trips. A person with an unknown day (students AND non-students) leaves the four
  diary-dependent controls (``status_x_education_leg``, ``trip_class``, ``arrival``,
  ``work_leg``) in BOTH universes, counted; the regional survey has a diary for every person, so
  keeping them would class a day nobody observed as "no education leg" / "no work leg". The
  season ticket does not depend on the diary and keeps them. The status and distance tables are
  built from persons WITH a recorded education leg, who have a diary by construction, so they
  need no such rule.
* Trip class from the day's trip count (bins :data:`student_behaviour_labels.TRIP_CLASS_EDGES`).
* Season ticket = the flatrate group of ``P_FKARTE`` through
  ``braunschweig.popsim.attributes.FKARTE_TO_CATEGORY`` (``deutschlandticket`` and
  ``PT_TICKET_OTHER_FLATRATE`` are flatrate). Item non-response (99, the interview-mode codes
  202 / 206, 402, a missing value) is not in that map: the student leaves the season-ticket
  control, counted. There is NO random imputation here (a measurement, not a seed).
* Distance = ``wegkm_imp`` of the first education leg, valid for ``0 < km <
  MID_TRIP_LENGTH_MAX_KM`` (the MiD filter-value bound of the commute-day code); anything else
  is counted, never banded. Mode = ``hvm_imp``, the imputed main mode the handbook mandates and
  the seed's ``trips.MODE_BY_HVM`` maps (codes 1-5; any other non-missing code raises).

LIMITATION of the behaviour table: persons without a diary of the day are ABSENT from the four
diary-dependent controls, so each of those shares rests on a reduced base (the counts are in the
committed file's ``# Exclusions:`` header line), while the SrV side has a diary for everyone.
The comparison of the two surveys therefore assumes that the persons without a diary are not a
distinct group with respect to those cells; that is not checkable from the survey itself.

Every exclusion is counted in the builder's diagnostics and logged as ``primary n/N (rate),
excluded m (rate)``; a step that would exclude more than
:data:`student_behaviour_labels.MAX_EXCLUSION_RATE` of its base raises, because that is a broken
code mapping, not a measurement. Shares are NaN, never 0.0,
when their base is empty, and empty cells are emitted.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.commute_day_state_reference import MID_TRIP_LENGTH_MAX_KM
from braunschweig.calibration.student_behaviour_labels import (
    ARRIVAL_HOUR_EDGES, BEHAVIOUR_COLUMNS, CONTROLS, MAX_EXCLUSION_RATE, MIN_ADULT_AGE,
    NOT_STUDENT, STUDENT_DISTANCE_EDGES_KM, STUDENT_DISTANCE_LABELS, TRIP_CLASS_EDGES,
    UNIVERSE_ADULTS, UNIVERSE_STUDENTS, behaviour_rows)
from braunschweig.popsim.attributes import (FKARTE_TO_CATEGORY, PT_TICKET_DEUTSCHLANDTICKET,
                                            PT_TICKET_OTHER_FLATRATE)
from braunschweig.popsim.diary_plan_match import MID_HOLIDAY, NO_DIARY_CODES
from braunschweig.popsim.mid.participation import PARTICIPATION_W_ZWECK

logger = logging.getLogger(__name__)

_LOG_TAG = "[mid student reference]"

STATUS_TABLE = "mid2023_student_status_by_age.csv"
DISTANCE_MODE_TABLE = "mid2023_student_education_distance_mode.csv"
SHARE_20_29_TABLE = "mid2023_student_share_20_29_by_land.csv"
BEHAVIOUR_TABLE = "mid2023_student_behaviour_seed.csv"

# MiD P_TAET ("Taetigkeit der Person"), MiD 2023 codebook B1 as quoted in popsim/attributes.py:
# 8 in Ausbildung, 9 Schueler/in, 10 Student/in; 1-17 are the documented substantive codes and
# 99 is "keine Angabe".
STUDENT_P_TAET = 10
PUPIL_P_TAET = 9
APPRENTICE_P_TAET = 8
P_TAET_CODES = frozenset(range(1, 18))
P_TAET_NO_ANSWER = 99

# ST_WOTAG 2-4 = Tuesday to Thursday, the reporting weekdays of the SrV side.
CORE_WEEKDAYS = (2, 3, 4)

# Production value of braunschweig.population.popsim.exclude_rbw_legs in configs/base_bs.yml;
# the extraction script reads the config and passes the value explicitly, and a test pins this
# default to it.
EXCLUDE_RBW_LEGS_DEFAULT = True
# Production value of braunschweig.population.popsim.exclude_holiday_plan_sources in
# configs/base_bs.yml; read and passed the same way, pinned by the same test.
EXCLUDE_HOLIDAYS_DEFAULT = True
# feiertag: 1 = the reporting day was a public holiday at the residence (MID_HOLIDAY), 0 = not.
HOLIDAY_COLUMN = "feiertag"
HOLIDAY_CODES = frozenset({0, MID_HOLIDAY})

# hvm_imp main-mode codes (handbook: the imputed variant; trips.MODE_BY_HVM maps the same codes).
MODE_COLUMN = "hvm_imp"
MODE_BY_HVM = {1: "walk", 2: "bike", 3: "car_passenger", 4: "car", 5: "pt"}

# W_ZWECK sets of the seed (single source: participation.PARTICIPATION_W_ZWECK).
EDUCATION_W_ZWECK = frozenset(PARTICIPATION_W_ZWECK["education"])
WORK_W_ZWECK = frozenset(PARTICIPATION_W_ZWECK["work"])

# anzwege1 codes at or above this value are MiD filter codes (803 / 804: diary not covered).
FILTER_CODE_MIN = 800
# mobil == 1: the person was mobile on the reporting day (diary_plan_match's own reading).
MOBILE_CODE = 1
# Reported clock fields: an hour of 99 (and any value outside 0-23 / minute outside 0-59) is a
# filter code; hours 24-98 are undocumented and raise.
ARRIVAL_HOUR_FILTER_CODE = 99
MAX_CLOCK_HOUR = 23
MAX_VALID_AGE_YEARS = 120

REGION_GERMANY = "DE"
# MiD BLAND codes 1-16, the sixteen Laender; emitted as zero-padded two-digit strings.
LAND_CODES = tuple(range(1, 17))

STATUS_AGE_BANDS = (("lt20", 0, 19), ("20_29", 20, 29), ("30plus", 30, 200))
STATUSES = ("student", "pupil", "apprentice", "other")
AGE_BAND_20_29 = (20, 29)

PERSON_COLUMNS = ["H_ID", "P_ID", "P_GEW", "P_TAET", "HP_ALTER"]
WEEKDAY_COLUMN = "ST_WOTAG"
BEHAVIOUR_PERSON_COLUMNS = PERSON_COLUMNS + [WEEKDAY_COLUMN, HOLIDAY_COLUMN, "anzwege1", "mobil",
                                             "P_FKARTE"]
STATUS_PERSON_COLUMNS = PERSON_COLUMNS + [WEEKDAY_COLUMN, HOLIDAY_COLUMN]
LEG_KEY_COLUMNS = ["H_ID", "P_ID", "W_ZWECK"]
FIRST_LEG_COLUMNS = ["W_SZS", "W_SZM"]
ARRIVAL_COLUMNS = ["W_AZS"]
DISTANCE_COLUMNS = ["wegkm_imp", MODE_COLUMN]
RBW_COLUMN = "W_RBW"
HOUSEHOLD_COLUMNS = ["H_ID", "BLAND"]

STATUS_COLUMNS = ["age_band", "status", "n_unweighted", "share"]
DISTANCE_MODE_COLUMNS = ["distance_band", "mode", "n_unweighted", "share_of_band",
                         "share_of_all"]
SHARE_20_29_COLUMNS = ["region", "n_unweighted", "share_pupils", "share_students",
                       "share_pupils_and_students"]


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


def _person_ids(frame: pd.DataFrame) -> pd.Series:
    """``H_ID_P_ID`` key; raises on a missing or non-numeric key part."""
    parts = {}
    for column in ("H_ID", "P_ID"):
        numeric = pd.to_numeric(frame[column], errors="coerce")
        if numeric.isna().any():
            raise ValueError("column %s has %d missing or non-numeric value(s)"
                             % (column, int(numeric.isna().sum())))
        parts[column] = numeric.astype("int64").astype(str)
    return parts["H_ID"] + "_" + parts["P_ID"]


def _weighted_share(weight: np.ndarray, mask: np.ndarray) -> float:
    total = weight.sum()
    return float(weight[mask].sum() / total) if total > 0 else float("nan")


def _prepare_persons(persons: pd.DataFrame, columns, name: str, *, core_weekdays: bool,
                     exclude_holidays: bool = False) -> tuple[pd.DataFrame, dict]:
    """Numeric person frame with a valid weight (and, if asked, on a core weekday).

    Columns: the requested ones as numbers (non-numeric -> NaN), ``pid`` and ``weight``,
    ``age`` (NaN when missing or outside 0 to :data:`MAX_VALID_AGE_YEARS`) and ``taet``.
    With ``core_weekdays`` the ``feiertag`` codes are checked and, with ``exclude_holidays``,
    the core-weekday persons reporting on a public holiday are dropped
    (``n_holiday_reporting_day``, 0 when the filter is off). Raises on a duplicated person key,
    on a ``P_TAET`` outside the documented set and on a ``feiertag`` other than 0 / 1.
    """
    _require_columns(persons, columns, name)
    frame = persons[list(columns)].copy()
    frame["pid"] = _person_ids(frame)
    duplicated = sorted(set(frame.loc[frame["pid"].duplicated(), "pid"]))
    if duplicated:
        raise ValueError("%s: duplicated person key H_ID_P_ID %s" % (name, duplicated[:10]))
    for column in columns:
        if column not in ("H_ID", "P_ID"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["weight"] = frame["P_GEW"]
    frame["taet"] = frame["P_TAET"]
    age = frame["HP_ALTER"]
    frame["age"] = age.where((age >= 0) & (age <= MAX_VALID_AGE_YEARS))

    n_total = len(frame)
    invalid_weight = ~(frame["weight"] > 0)
    _log_exclusion("valid P_GEW (positive)", int(invalid_weight.sum()), n_total)
    frame = frame[~invalid_weight.values]
    diagnostics = {"n_persons_total": int(n_total),
                   "n_excluded_invalid_weight": int(invalid_weight.sum())}
    if core_weekdays:
        on_core = frame[WEEKDAY_COLUMN].isin(CORE_WEEKDAYS)
        diagnostics["n_not_core_weekday"] = int((~on_core).sum())
        logger.info("%s core weekdays (%s in %s): %d persons kept, %d on another day",
                    _LOG_TAG, WEEKDAY_COLUMN, list(CORE_WEEKDAYS), int(on_core.sum()),
                    int((~on_core).sum()))
        frame = frame[on_core.values]
        holiday_code = frame[HOLIDAY_COLUMN]
        undocumented_holiday = sorted(set(holiday_code[~holiday_code.isin(sorted(HOLIDAY_CODES))]
                                          .tolist()), key=str)
        if undocumented_holiday:
            raise ValueError("%s: column %s carries code(s) %s (NaN = missing or non-numeric) "
                             "outside the documented set %s"
                             % (name, HOLIDAY_COLUMN, undocumented_holiday,
                                sorted(HOLIDAY_CODES)))
        holiday = (holiday_code == MID_HOLIDAY).to_numpy() & exclude_holidays
        _log_exclusion("reporting day not a public holiday (%s != %d; exclude_holidays=%s)"
                       % (HOLIDAY_COLUMN, MID_HOLIDAY, exclude_holidays),
                       int(holiday.sum()), len(frame))
        diagnostics["n_holiday_reporting_day"] = int(holiday.sum())
        frame = frame[~holiday]
    code = frame["taet"]
    undocumented = sorted(set(code[code.notna() & ~code.isin(sorted(P_TAET_CODES))
                                   & (code != P_TAET_NO_ANSWER)].tolist()))
    if undocumented:
        raise ValueError("%s: column P_TAET carries code(s) %s outside the documented set %s "
                         "and %d" % (name, undocumented, sorted(P_TAET_CODES), P_TAET_NO_ANSWER))
    return frame.copy(), diagnostics


def _drop_unreadable_activity(frame: pd.DataFrame, step_base: str) -> tuple[pd.DataFrame, dict]:
    """Drop persons whose ``P_TAET`` is missing or the no-answer code (counted, logged)."""
    unreadable = frame["taet"].isna() | (frame["taet"] == P_TAET_NO_ANSWER)
    n_unreadable = int(unreadable.sum())
    _log_exclusion("readable P_TAET over %s (99 / missing is never 'not a student')"
                   % step_base, n_unreadable, len(frame))
    return frame[~unreadable.values].copy(), {"n_excluded_unreadable_p_taet": n_unreadable}


def _direct_legs(trips: pd.DataFrame, codes, extra_columns, *,
                 exclude_rbw_legs: bool) -> tuple[pd.DataFrame, dict]:
    """The legs with ``W_ZWECK`` in ``codes`` that the plan contains, with their person key."""
    required = LEG_KEY_COLUMNS + list(extra_columns) + ([RBW_COLUMN] if exclude_rbw_legs else [])
    _require_columns(trips, required, "trips")
    zweck = pd.to_numeric(trips["W_ZWECK"], errors="coerce")
    legs = trips.loc[zweck.isin(sorted(codes)).values, required].copy()
    n_rbw = 0
    if exclude_rbw_legs:
        rbw = pd.to_numeric(legs[RBW_COLUMN], errors="coerce").fillna(0)
        direct = (rbw == 0).values
        n_rbw = int((~direct).sum())
        legs = legs[direct]
    legs["pid"] = _person_ids(legs)
    return legs, {"n_legs_of_purpose": int(len(legs) + n_rbw), "n_legs_dropped_rbw": n_rbw}


def _first_education_legs(trips: pd.DataFrame, pids, extra_columns, *,
                          exclude_rbw_legs: bool) -> tuple[pd.DataFrame, set, set]:
    """``(first leg per person, pids with unreadable start, pids with an education leg)``."""
    legs, _ = _direct_legs(trips, EDUCATION_W_ZWECK, FIRST_LEG_COLUMNS + list(extra_columns),
                           exclude_rbw_legs=exclude_rbw_legs)
    legs = legs[legs["pid"].isin(set(pids))].copy()
    legs["start_hour"] = pd.to_numeric(legs["W_SZS"], errors="coerce")
    legs["start_minute"] = pd.to_numeric(legs["W_SZM"], errors="coerce")
    readable = legs["start_hour"].between(0, 23) & legs["start_minute"].between(0, 59)
    unreadable_start = set(legs.loc[~readable, "pid"])
    kept = legs[~legs["pid"].isin(unreadable_start)]
    first = (kept.sort_values(["pid", "start_hour", "start_minute"], kind="stable")
             .drop_duplicates("pid", keep="first"))
    return first, unreadable_start, set(legs["pid"])


def _age_band(age: pd.Series) -> pd.Series:
    band = pd.Series(np.nan, index=age.index, dtype=object)
    for label, minimum, maximum in STATUS_AGE_BANDS:
        band[((age >= minimum) & (age <= maximum)).values] = label
    return band


# --------------------------------------------------------------------------- status by age
def build_status_by_age_table(persons: pd.DataFrame, trips: pd.DataFrame, *,
                              exclude_rbw_legs: bool = EXCLUDE_RBW_LEGS_DEFAULT,
                              exclude_holidays: bool = EXCLUDE_HOLIDAYS_DEFAULT
                              ) -> tuple[pd.DataFrame, dict]:
    """Activity status of the persons with an education trip on a core weekday, per age band.

    Status: ``student`` (P_TAET 10), ``pupil`` (9), ``apprentice`` (8), ``other`` (the rest).
    Bands :data:`STATUS_AGE_BANDS` (bounds inclusive, ``lt20`` from age 0). Columns
    :data:`STATUS_COLUMNS`; ``share`` is weighted within the band, NaN for an empty band; all 12
    cells are emitted. A person without a valid age is in no band (counted); with
    ``exclude_holidays`` a holiday reporting day is outside the table (counted).
    """
    people, diagnostics = _prepare_persons(persons, STATUS_PERSON_COLUMNS, "persons",
                                           core_weekdays=True, exclude_holidays=exclude_holidays)
    legs, _ = _direct_legs(trips, EDUCATION_W_ZWECK, [], exclude_rbw_legs=exclude_rbw_legs)
    people = people[people["pid"].isin(set(legs["pid"])).values]
    diagnostics["n_with_education_trip"] = int(len(people))
    people, unreadable = _drop_unreadable_activity(people, "the persons with an education trip")
    diagnostics.update(unreadable)
    no_age = people["age"].isna()
    diagnostics["n_with_education_trip_without_valid_age"] = int(no_age.sum())
    people = people[~no_age.values].copy()
    taet = people["taet"]
    people["status"] = np.select(
        [taet == STUDENT_P_TAET, taet == PUPIL_P_TAET, taet == APPRENTICE_P_TAET],
        list(STATUSES[:3]), default=STATUSES[3])
    people["band"] = _age_band(people["age"])
    rows = []
    for band, _, _ in STATUS_AGE_BANDS:
        in_band = people[people["band"] == band]
        weight = in_band["weight"].to_numpy(dtype=float)
        for status in STATUSES:
            mask = (in_band["status"] == status).to_numpy()
            rows.append({"age_band": band, "status": status, "n_unweighted": int(mask.sum()),
                         "share": _weighted_share(weight, mask)})
    table = pd.DataFrame(rows, columns=STATUS_COLUMNS)
    logger.info("%s status table: %d persons with an education trip in %d age bands",
                _LOG_TAG, len(people), len(STATUS_AGE_BANDS))
    return table, diagnostics


# --------------------------------------------------------------------------- distance x mode
def build_distance_mode_table(persons: pd.DataFrame, trips: pd.DataFrame, *,
                              exclude_rbw_legs: bool = EXCLUDE_RBW_LEGS_DEFAULT,
                              exclude_holidays: bool = EXCLUDE_HOLIDAYS_DEFAULT
                              ) -> tuple[pd.DataFrame, dict]:
    """Distance band x main mode of the adult students' first education trip, core weekdays.

    Bands :data:`student_behaviour_labels.STUDENT_DISTANCE_LABELS` (left-inclusive, the SrV
    edges), modes :data:`MODE_BY_HVM`. Columns :data:`DISTANCE_MODE_COLUMNS`: ``share_of_band``
    is weighted within the band (NaN for an empty band), ``share_of_all`` over all banded
    students; all 30 cells are emitted. Students under :data:`MIN_ADULT_AGE` or without a valid
    age are outside the universe (counted), like the SrV distance table; with
    ``exclude_holidays`` so is a holiday reporting day (counted).
    """
    people, diagnostics = _prepare_persons(persons, STATUS_PERSON_COLUMNS, "persons",
                                           core_weekdays=True, exclude_holidays=exclude_holidays)
    people, unreadable = _drop_unreadable_activity(people, "the core-weekday persons")
    diagnostics.update(unreadable)
    students = people[people["taet"] == STUDENT_P_TAET]
    adult = (students["age"] >= MIN_ADULT_AGE).to_numpy()
    diagnostics["n_students_not_adult"] = int((~adult).sum())
    students = students[adult]
    first, unreadable_start, with_education = _first_education_legs(
        trips, students["pid"], DISTANCE_COLUMNS, exclude_rbw_legs=exclude_rbw_legs)
    n_with_education = len(with_education)
    _log_exclusion("readable start time of the education legs, over the adult students with an "
                   "education leg", len(unreadable_start), n_with_education)
    # Students without an education leg are outside this table by definition (a student who did
    # not go to the education place that day has no education trip), counted explicitly.
    diagnostics.update(n_adult_students=int(len(students)),
                       n_adult_students_without_education_leg=int(len(students)
                                                                  - n_with_education),
                       n_students_with_education_leg=int(n_with_education),
                       n_students_unreadable_start_time=len(unreadable_start))

    km = pd.to_numeric(first["wegkm_imp"], errors="coerce")
    valid_length = (km > 0) & (km < MID_TRIP_LENGTH_MAX_KM)
    mode = pd.to_numeric(first[MODE_COLUMN], errors="coerce")
    undocumented = sorted(set(mode[mode.notna() & ~mode.isin(sorted(MODE_BY_HVM))].tolist()))
    if undocumented:
        raise ValueError("trips: column %s carries code(s) %s outside the documented set %s"
                         % (MODE_COLUMN, undocumented, sorted(MODE_BY_HVM)))
    _log_exclusion("valid education trip length (0 < wegkm_imp < %.0f km)"
                   % MID_TRIP_LENGTH_MAX_KM, int((~valid_length).sum()), len(first))
    _log_exclusion("readable main mode (%s)" % MODE_COLUMN,
                   int((valid_length & mode.isna()).sum()), int(valid_length.sum()))
    banded = first[(valid_length & mode.notna()).to_numpy()].copy()
    diagnostics["n_students_invalid_education_length"] = int((~valid_length).sum())
    diagnostics["n_students_missing_mode"] = int((valid_length & mode.isna()).sum())
    banded["km"] = km[banded.index]
    banded["mode_label"] = mode[banded.index].astype(int).map(MODE_BY_HVM)
    banded["band"] = pd.cut(banded["km"], list(STUDENT_DISTANCE_EDGES_KM),
                            labels=list(STUDENT_DISTANCE_LABELS), right=False).astype(object)
    banded = banded.merge(students[["pid", "weight"]], on="pid", how="left", validate="1:1")

    weight = banded["weight"].to_numpy(dtype=float)
    rows = []
    for label in STUDENT_DISTANCE_LABELS:
        in_band = (banded["band"] == label).to_numpy()
        for mode_label in MODE_BY_HVM.values():
            cell = in_band & (banded["mode_label"] == mode_label).to_numpy()
            rows.append({"distance_band": label, "mode": mode_label,
                         "n_unweighted": int(cell.sum()),
                         "share_of_band": _weighted_share(weight[in_band], cell[in_band]),
                         "share_of_all": _weighted_share(weight, cell)})
    table = pd.DataFrame(rows, columns=DISTANCE_MODE_COLUMNS)
    logger.info("%s distance x mode table: %d of %d adult students with an education leg banded",
                _LOG_TAG, len(banded), n_with_education)
    return table, diagnostics


# --------------------------------------------------------------------------- share 20-29
def build_share_20_29_table(persons: pd.DataFrame,
                            households: pd.DataFrame | None = None
                            ) -> tuple[pd.DataFrame, dict]:
    """Pupil and student shares of the 20 to 29 year olds, Germany and per Land.

    A status share, so over ALL reporting days (no ``ST_WOTAG`` filter). ``BLAND`` is taken from
    ``persons`` when it carries the column, else joined from ``households`` on ``H_ID``; with
    neither, or with a code outside the 16 Laender, the call raises. Rows: :data:`REGION_GERMANY`
    first, then one row per Land (zero-padded two-digit ``BLAND``), empty Laender emitted with
    NaN shares. Columns :data:`SHARE_20_29_COLUMNS`; shares are weighted over the persons aged
    20 to 29 with a readable ``P_TAET``.
    """
    people, diagnostics = _prepare_persons(persons, PERSON_COLUMNS, "persons",
                                           core_weekdays=False)
    if "BLAND" in persons.columns:
        land = pd.to_numeric(persons.set_index(_person_ids(persons))["BLAND"], errors="coerce")
        people["land"] = land.reindex(people["pid"]).to_numpy()
    else:
        if households is None:
            raise ValueError("persons carry no column BLAND and no households frame was given")
        _require_columns(households, HOUSEHOLD_COLUMNS, "households")
        if households["H_ID"].duplicated().any():
            raise ValueError("households: duplicated H_ID")
        by_household = pd.to_numeric(households.set_index("H_ID")["BLAND"], errors="coerce")
        people["land"] = people["H_ID"].map(by_household).to_numpy()
    invalid_land = ~people["land"].isin(LAND_CODES)
    if invalid_land.any():
        raise ValueError("persons: column BLAND carries missing or undocumented code(s) %s "
                         "(documented: %s)" % (sorted(set(people.loc[invalid_land, "land"]
                                                          .tolist()), key=str), list(LAND_CODES)))
    people, unreadable = _drop_unreadable_activity(people, "all persons")
    diagnostics.update(unreadable)
    in_band = people["age"].between(*AGE_BAND_20_29)
    diagnostics["n_aged_20_29"] = int(in_band.sum())
    band = people[in_band.to_numpy()]

    def _row(region: str, group: pd.DataFrame) -> dict:
        weight = group["weight"].to_numpy(dtype=float)
        pupil = (group["taet"] == PUPIL_P_TAET).to_numpy()
        student = (group["taet"] == STUDENT_P_TAET).to_numpy()
        return {"region": region, "n_unweighted": int(len(group)),
                "share_pupils": _weighted_share(weight, pupil),
                "share_students": _weighted_share(weight, student),
                "share_pupils_and_students": _weighted_share(weight, pupil | student)}

    rows = [_row(REGION_GERMANY, band)]
    rows += [_row("%02d" % code, band[band["land"] == code]) for code in LAND_CODES]
    table = pd.DataFrame(rows, columns=SHARE_20_29_COLUMNS)
    logger.info("%s share 20-29 table: %d persons aged %d-%d, %d Laender", _LOG_TAG, len(band),
                AGE_BAND_20_29[0], AGE_BAND_20_29[1], len(LAND_CODES))
    return table, diagnostics


# --------------------------------------------------------------------------- behaviour
def build_behaviour_seed_table(persons: pd.DataFrame, trips: pd.DataFrame, *,
                               exclude_rbw_legs: bool = EXCLUDE_RBW_LEGS_DEFAULT,
                               exclude_holidays: bool = EXCLUDE_HOLIDAYS_DEFAULT
                               ) -> tuple[pd.DataFrame, dict]:
    """Every control of :data:`student_behaviour_labels.CONTROLS` in both universes.

    The MiD mirror of :func:`srv_student_reference.build_behaviour_table`: the same labels, the
    same two universes (``adults_18plus`` over the adults on a core weekday with a readable
    ``P_TAET``, a non-student carrying :data:`NOT_STUDENT`; ``students`` the adult students
    alone) and the same column set :data:`BEHAVIOUR_COLUMNS`. A student excluded from one
    control (unreadable trip count, arrival hour or start time, an unmapped ``P_FKARTE``) is
    absent from that control's base in BOTH universes and stays in every other control.
    ``status_x_education_leg`` and ``work_leg`` use the seed's education / work leg sets.
    ``n_effective`` is the Kish size over exactly the rows of the control's base. With
    ``exclude_holidays`` a person reporting on a public holiday is in neither universe (counted).
    """
    people, diagnostics = _prepare_persons(persons, BEHAVIOUR_PERSON_COLUMNS, "persons",
                                           core_weekdays=True, exclude_holidays=exclude_holidays)
    people, unreadable = _drop_unreadable_activity(people, "the core-weekday persons")
    diagnostics.update(unreadable)
    no_age = people["age"].isna()
    diagnostics["n_missing_age"] = int(no_age.sum())
    adults = people[(people["age"] >= MIN_ADULT_AGE).to_numpy()]
    students = adults[adults["taet"] == STUDENT_P_TAET].copy()
    non_students = adults[adults["taet"] != STUDENT_P_TAET].copy()

    education_legs, leg_diagnostics = _direct_legs(trips, EDUCATION_W_ZWECK, [],
                                                   exclude_rbw_legs=exclude_rbw_legs)
    work_legs, work_leg_diagnostics = _direct_legs(trips, WORK_W_ZWECK, [],
                                                   exclude_rbw_legs=exclude_rbw_legs)

    # A person without a diary of the day has an UNKNOWN day, not an empty one: they leave every
    # diary-dependent control (students and non-students alike, both universes), as the popsim
    # seed treats them (item non-response, never "no trip"). Only the season ticket, a fact that
    # does not depend on the diary, keeps them.
    students["trip_count"], students_immobile = _trip_counts(students)
    non_students["trip_count"], non_students_immobile = _trip_counts(non_students)
    student_has_diary = students["trip_count"].notna()
    non_student_has_diary = non_students["trip_count"].notna().to_numpy()
    n_students_no_diary = int((~student_has_diary).sum())
    n_non_students_no_diary = int((~non_student_has_diary).sum())
    _log_exclusion("diary of the reporting day (anzwege1 0-%d, or 803 with mobil != 1 = a "
                   "genuine stay-at-home day), adult students" % (FILTER_CODE_MIN - 1),
                   n_students_no_diary, len(students))
    _log_exclusion("diary of the reporting day, adult non-students", n_non_students_no_diary,
                   len(non_students))

    has_education = students["pid"].isin(set(education_legs["pid"]))
    has_work = students["pid"].isin(set(work_legs["pid"]))
    trip_class = _trip_class_labels(students)
    arrival, arrival_diagnostics = _arrival_labels(students[student_has_diary.to_numpy()], trips,
                                                   exclude_rbw_legs)
    season, season_diagnostics = _season_ticket_labels(students)
    labels = {
        "status_x_education_leg": pd.Series(np.where(has_education, "student_edu",
                                                     "student_noedu"),
                                            index=students.index, dtype=object).where(
                                                student_has_diary),
        "trip_class": trip_class,
        "arrival": arrival.reindex(students.index),
        "work_leg": pd.Series(np.where(has_work, "student_work", "student_nowork"),
                              index=students.index, dtype=object).where(student_has_diary),
        "season_ticket": season,
    }
    rows = []
    for control, student_labels in CONTROLS.items():
        label = labels[control]
        base = students[label.notna().to_numpy()]
        base_labels = label[label.notna()].to_numpy(dtype=object)
        student_weight = base["weight"].to_numpy(dtype=float)
        rows += behaviour_rows(control, UNIVERSE_STUDENTS, student_labels, student_weight,
                               base_labels)
        other = (non_students if control == "season_ticket"
                 else non_students[non_student_has_diary])
        adult_weight = np.concatenate([other["weight"].to_numpy(dtype=float), student_weight])
        adult_labels = np.concatenate([np.full(len(other), NOT_STUDENT, dtype=object),
                                       base_labels])
        rows += behaviour_rows(control, UNIVERSE_ADULTS, (*student_labels, NOT_STUDENT),
                               adult_weight, adult_labels)
    table = pd.DataFrame(rows, columns=BEHAVIOUR_COLUMNS)
    diagnostics.update(n_adults=int(len(adults)), n_students=int(len(students)),
                       n_students_without_diary=n_students_no_diary,
                       n_non_students_without_diary=n_non_students_no_diary,
                       n_students_diary_immobile_zero_trips=int(students_immobile.sum()),
                       n_non_students_diary_immobile_zero_trips=int(non_students_immobile.sum()),
                       **arrival_diagnostics, **season_diagnostics,
                       n_education_legs_dropped_rbw=leg_diagnostics["n_legs_dropped_rbw"],
                       n_work_legs_dropped_rbw=work_leg_diagnostics["n_legs_dropped_rbw"])
    logger.info("%s behaviour table: %d rows (%d controls x 2 universes); %d adults, %d adult "
                "students", _LOG_TAG, len(table), len(CONTROLS), len(adults), len(students))
    return table, diagnostics


def _trip_counts(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """``(trips on the reporting day, genuine stay-at-home flag)``; NaN count = no diary.

    The count is ``anzwege1`` when it is a real count (0 to :data:`FILTER_CODE_MIN` - 1). The
    no-diary codes (``popsim.diary_plan_match.NO_DIARY_CODES``, 803 / 804) are an UNKNOWN day,
    except ``803`` with ``mobil != 1``: the person was not mobile, zero trips is what they
    reported and ``diary_plan_match`` keeps that day (``REASON_KEEP_IMMOBILE``); it counts as 0.
    """
    count = frame["anzwege1"]
    valid = count.notna() & (count >= 0) & (count < FILTER_CODE_MIN)
    immobile = ((count == NO_DIARY_CODES[0]) & (frame["mobil"] != MOBILE_CODE)).to_numpy()
    trips_on_day = count.where(valid)
    trips_on_day[immobile] = 0
    return trips_on_day, pd.Series(immobile, index=frame.index)


def _trip_class_labels(students: pd.DataFrame) -> pd.Series:
    """Trip-class label per student; NaN for a student without a diary of the day."""
    return pd.cut(students["trip_count"], TRIP_CLASS_EDGES,
                  labels=list(CONTROLS["trip_class"])).astype(object)


def _arrival_labels(students: pd.DataFrame, trips: pd.DataFrame,
                    exclude_rbw_legs: bool) -> tuple[pd.Series, dict]:
    """Arrival label per student; NaN where the student is excluded from the arrival control."""
    first, unreadable_start, with_education = _first_education_legs(
        trips, students["pid"], ARRIVAL_COLUMNS, exclude_rbw_legs=exclude_rbw_legs)
    hour = pd.to_numeric(first["W_AZS"], errors="coerce")
    undocumented = hour[hour.notna() & (hour != ARRIVAL_HOUR_FILTER_CODE)
                        & (hour > MAX_CLOCK_HOUR)]
    if len(undocumented):
        raise ValueError("trips: column W_AZS carries hour(s) %s above %d other than the filter "
                         "code %d" % (sorted(set(undocumented.tolist())), MAX_CLOCK_HOUR,
                                      ARRIVAL_HOUR_FILTER_CODE))
    bad_arrival = hour.isna() | (hour < 0) | (hour == ARRIVAL_HOUR_FILTER_CODE)
    unreadable_arrival = set(first.loc[bad_arrival.to_numpy(), "pid"])
    readable = first[~bad_arrival.to_numpy()]
    hour_label = pd.cut(hour[~bad_arrival], list(ARRIVAL_HOUR_EDGES),
                        labels=list(CONTROLS["arrival"][:3])).astype(object)
    by_pid = dict(zip(readable["pid"], hour_label))
    label = students["pid"].map(
        lambda pid: by_pid.get(pid, np.nan) if pid in with_education
        else CONTROLS["arrival"][3]).astype(object)
    n_start, n_arrival = len(unreadable_start), len(unreadable_arrival)
    # Base = the students WITH an education leg: only they can lose their arrival label. Over all
    # students the rate would be diluted by the absence share (the SrV side's convention).
    _log_exclusion("arrival control (readable start time and arrival hour of the first education "
                   "leg, over the students with an education leg; start unreadable %d, arrival "
                   "unreadable %d)" % (n_start, n_arrival), n_start + n_arrival,
                   len(with_education))
    return label, {"n_students_unreadable_start_time": n_start,
                   "n_students_unreadable_arrival_hour": n_arrival}


def _season_ticket_labels(students: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Season-ticket label per student; NaN where ``P_FKARTE`` is not in the ticket map."""
    categories = [FKARTE_TO_CATEGORY.get(int(code)) if pd.notna(code) and float(code).is_integer()
                  else None for code in students["P_FKARTE"]]
    flatrate = [category == PT_TICKET_DEUTSCHLANDTICKET or category in PT_TICKET_OTHER_FLATRATE
                for category in categories]
    label = pd.Series(np.where(flatrate, "student_flatrate", "student_no_flatrate"),
                      index=students.index, dtype=object)
    unreadable = np.array([category is None for category in categories], dtype=bool)
    label[unreadable] = np.nan
    n_unreadable = int(unreadable.sum())
    _log_exclusion("season-ticket control (P_FKARTE in FKARTE_TO_CATEGORY; item non-response "
                   "excluded, never 'no flatrate'; 8 = never PT is no flatrate)",
                   n_unreadable, len(students))
    return label, {"n_students_unreadable_season_ticket": n_unreadable}
