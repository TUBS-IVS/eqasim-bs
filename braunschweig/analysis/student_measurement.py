"""Model side of the student measurement and its comparison with SrV 2023 (#140, Phase 1).

Pure functions over already-loaded frames (no synpp context, no I/O); the synpp analysis stage
of this measurement supplies the frames and writes the tables.

:func:`model_student_frame` puts every adult model student into the cells of
:data:`braunschweig.calibration.student_parameters.PARAMETERS`, and :func:`compare_parameters`
sets those cells against the committed SrV parameter table
(``srv2023_student_parameters.csv``, built by
:func:`braunschweig.calibration.srv_student_reference.build_parameter_table`). A difference is a
measurement in SrV standard errors, not a validation verdict.

Four further tables put the student level and the plans in context (definitions in their
docstrings): :func:`student_level_table` (student shares per Kreis against
``srv2023_student_share_by_kreis.csv``), :func:`young_adult_status_table` (student / employed /
other among the 20 to 29 year olds against ``srv2023_young_adult_status_by_kreis.csv``, next to
the Zensus 2022 employed count the employment grid reads), :func:`student_work_class_table` (the
students' work-control class) and :func:`plan_source_table` (own diary or re-drawn plan, the
match path of the completed-donor step and the donor's activity).

Universes (the same on both sides)
----------------------------------
* Student = ``P_TAET == mid_student_reference.STUDENT_P_TAET`` (10) of the enriched population;
  adult = ``age >= student_behaviour_labels.MIN_ADULT_AGE``. ``P_TAET`` 99 or missing answers
  nothing about the activity: such a person is never a student, counted.
* ``present`` = not in :func:`braunschweig.synthesis.day_absence.absence.absent_person_ids`, the
  model's counterpart of the SrV participation universe (persons at home at some point of the
  reporting day). Every who, mobility-tool and day share is over PRESENT students (the mobile
  and education bases of ``PARAMETERS`` are subsets of them); ``away_all_day`` is over ALL
  students, as SrV measures it on the file before the universe filter. The
  ``other_adults_all`` cells are SrV-only (model columns NaN).
* Only the seven SrV-surveyed Kreise (:data:`SURVEYED_KREISE`: ``ZGB_KREISE`` minus
  ``WOLFSBURG_KREIS``) enter the comparison; Wolfsburg students and students without a home
  Kreis stay in the student frame and are counted, but change no comparison row.
* Model counts are sample counts at the run's sampling rate, unweighted (every model person
  carries weight 1, so ``model_share`` is a plain count share).

Label definitions, model side (SrV side: ``srv_student_reference.build_parameter_table``)
------------------------------------------------------------------------------------------
* ``age_group``: ``age`` cut by ``srv_student_reference.AGE_GROUP_EDGES`` (SrV ``V_ALTER``).
* ``sex``: ``sex`` (``female`` / ``male``); SrV ``V_GESCHLECHT`` 2 female / 1 male. Any other
  model value raises (the synthetic population carries no third code).
* ``household_type``: ``alone`` = the household has one member; ``with_children`` = a member is
  younger than ``MIN_ADULT_AGE``; else ``several_adults``. Members are the rows of the enriched
  frame with the student's ``household_id``. DEFINITION GAP: SrV reads the derived ``E_HHTYP``
  (several persons with / without "Kind(er)", alone); the model has no such variable, so a SrV
  household whose "child" is defined otherwise than "younger than 18" can fall into another
  cell. A household with a member of unknown age and no known minor is excluded, counted.
* ``lives_with_person_18_years_older``: a household member is at least
  ``srv_student_reference.OLDER_PERSON_GAP_YEARS`` older than the student (ages of all members,
  as on the SrV side); unknown if a member's age is unknown and no known age decides it.
* ``licence``: ``has_license`` (SrV ``V_FUEHR_PKW``).
* ``cars_in_household``: ``number_of_cars`` as ``0`` / ``1`` / ``2plus`` (SrV ``V_ANZ_PKW``).
* ``car_access``: ``has_license`` and ``number_of_cars >= 1`` (SrV ``E_ZUGANG_PKW``, licence AND
  at least one car in the household).
* ``bicycle_in_household``: ``number_of_bicycles >= 1`` (SrV ``E_ANZ_RAD_ALLE_6 >= 1``).
* ``ticket_group4``: ``pt_ticket_group4`` of the EXISTING mapping
  :func:`braunschweig.popsim.attributes.map_pt_ticket_group` applied to ``pt_subscription_type``
  (the four groups of the population's ticket control; never a second mapping). SrV maps
  ``E_OEV_FK`` onto the same four labels; ``never_pt`` is the MiD "fahre nie" answer on the model
  side and ``E_OEV_FK == -8`` (no public transport use in the past 12 months) on the SrV side.
* ``season_ticket``: ``flatrate`` = the groups that mapping calls flat-rate
  (:func:`flatrate_ticket_groups4`); it must agree with ``has_pt_subscription`` for every
  student (both derive from ``PT_TICKET_FLATRATE``), a disagreement raises.
* Day labels: :func:`braunschweig.analysis.plan_structure.harmonise_model` on the students and
  their trips, then :func:`braunschweig.calibration.student_parameters.person_day_labels`, the one
  function the SrV side uses too.

Model frames join on ``person_id`` / ``household_id`` (never on row order); a trip or absence row
of an unknown person, a duplicated ``person_id`` or an adult student without an absence row
raises, naming the count. Students without a home Kreis are counted and the function raises
above the bound the caller passes (the stage's configured unmatched-home share).
"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
import pandas as pd

from braunschweig.analysis import plan_structure as P
from braunschweig.calibration import student_parameters as SP
from braunschweig.calibration.mid_student_reference import (APPRENTICE_P_TAET, P_TAET_CODES,
                                                            P_TAET_NO_ANSWER, PUPIL_P_TAET,
                                                            STUDENT_P_TAET)
from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS, ZGB_KREISE
from braunschweig.calibration.srv_participation_universe import (LEVEL_KREIS, LEVEL_TOTAL,
                                                                 REGION_CODE)
from braunschweig.calibration.srv_student_reference import (AGE_BAND_20_29, AGE_GROUP_EDGES,
                                                            AREA_WOLFSBURG, EDUCATION_AGE_RANGES,
                                                            GROUP_OTHER_ADULT, GROUP_STUDENT,
                                                            HOME_AREAS, OLDER_PERSON_GAP_YEARS,
                                                            SHARE_COLUMNS,
                                                            YOUNG_ADULT_STATUS_COLUMNS,
                                                            YOUNG_ADULT_STATUSES, area_of_kreis)
from braunschweig.calibration.srv_student_reference import \
    EDUCATION_LEG_BY_STATUS_COLUMNS as SRV_EDUCATION_LEG_BY_STATUS_COLUMNS
from braunschweig.calibration.student_behaviour_labels import MAX_EXCLUSION_RATE, MIN_ADULT_AGE
from braunschweig.calibration.student_control_evidence import (MIN_DIFFERENCE_STANDARD_ERRORS,
                                                               MIN_RESPONDENTS_PER_CELL)
from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES, PT_TICKET_FLATRATE
from braunschweig.popsim.attributes import (EMPLOYED_EMPLOYMENT_STATUS_CLASSES, EMPLOYED_TAET,
                                            map_pt_ticket_group)
from braunschweig.popsim.diary_plan_match import (REASON_KEEP, REASON_KEEP_IMMOBILE,
                                                  REASONS_REMAP)
from braunschweig.popsim.zensus_employment_age import KREIS_AGE_CLASSES
from braunschweig.synthesis.day_absence.absence import STATES, absent_person_ids

LOG = logging.getLogger(__name__)
_LOG_TAG = "[student measurement]"

#: Columns required on the enriched person frame (``synthesis.population.enriched``; for
#: popsim_mid the ``braunschweig.popsim.enriched_adapter`` output).
MODEL_PERSON_COLUMNS = ("person_id", "household_id", "age", "sex", "P_TAET", "employed",
                        "employment_status", "has_license", "number_of_cars",
                        "number_of_bicycles", "has_pt_subscription", "pt_subscription_type")
#: Columns required on the absence frame (``absence_stage`` output ``["absence"]``); the state
#: itself is read only through ``absent_person_ids``.
ABSENCE_COLUMNS = ("person_id", "day_absence_state")

#: The Kreise SrV 2023 surveys: the ZGB minus Wolfsburg.
SURVEYED_KREISE = tuple(kreis for kreis in ZGB_KREISE if kreis != WOLFSBURG_KREIS)

ATTRIBUTE_PARAMETERS = tuple(name for name, spec in SP.PARAMETERS.items()
                             if spec.group in (SP.GROUP_WHO, SP.GROUP_TOOLS))
#: Day parameters labelled per person; ``away_all_day`` follows from ``present``.
DAY_PARAMETERS = tuple(name for name, spec in SP.PARAMETERS.items()
                       if spec.group == SP.GROUP_DAY and name != "away_all_day")
MODEL_STUDENT_COLUMNS = ["person_id", "kreis", "present", *ATTRIBUTE_PARAMETERS,
                         *DAY_PARAMETERS]

#: Bases the model side does not measure: the comparison is about students.
MODEL_UNMEASURED_BASES = (SP.BASE_OTHER_ADULTS_ALL,)


@lru_cache(maxsize=None)
def flatrate_ticket_groups4() -> tuple:
    """The ``pt_ticket_group4`` labels :func:`map_pt_ticket_group` gives a flat-rate ticket.

    Derived by running the owner's mapping on every flat-rate subscription type of
    ``PT_TICKET_FLATRATE`` (the single owner of "which ticket grants unlimited rides"), so the
    set follows the mapping and is never restated here. Sorted, computed once.

    The mapping logs its group counts as "percentages of all persons"; on these made-up rows
    that line would read like a population figure in the run log, so the mapping's logger is
    held at WARNING during the derivation (its level restored afterwards).
    """
    flatrate_types = pd.DataFrame({"pt_subscription_type": sorted(PT_TICKET_FLATRATE)})
    mapping_logger = logging.getLogger(map_pt_ticket_group.__module__)
    previous_level = mapping_logger.level
    mapping_logger.setLevel(logging.WARNING)
    try:
        mapped = map_pt_ticket_group(flatrate_types)
    finally:
        mapping_logger.setLevel(previous_level)
    return tuple(sorted(mapped["pt_ticket_group4"].unique()))


#: Day parameters that can lose a student to a missing or negative time, with the
#: ``person_day_labels`` diagnostics key of their base.
_DAY_EXCLUSION_BASES = (("first_departure_hour", "n_mobile"),
                        ("last_arrival_home_hour", "n_mobile"),
                        ("time_at_education", "n_with_education_leg"),
                        ("arrival_band", "n_with_education_leg"))

COMPARISON_COLUMNS = ["group", "parameter", "base", "label", "model_n_base", "model_n",
                      "model_share", "srv_n_unweighted", "srv_share", "srv_n_effective",
                      "srv_se_share", "difference_pp", "difference_in_se", "srv_cell_ok",
                      "beyond_two_se"]

_KEY = ["group", "parameter", "base", "label"]


# --------------------------------------------------------------------------- helpers
def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s frame is missing required column(s) %s" % (name, missing))


def _require_unique_person_ids(frame: pd.DataFrame, name: str) -> None:
    duplicated = frame.loc[frame["person_id"].duplicated(), "person_id"]
    if len(duplicated):
        raise ValueError("%s: %d duplicated person_id row(s), e.g. %s"
                         % (name, len(duplicated), sorted(duplicated.unique().tolist())[:5]))


def _require_known_person_ids(ids: pd.Series, known: set, name: str) -> None:
    orphans = ids[~ids.isin(known)]
    if len(orphans):
        raise ValueError("%s: %d row(s) carry a person_id that is not in the persons frame, "
                         "e.g. %s" % (name, len(orphans), sorted(orphans.unique().tolist())[:5]))


def _rate(count: int, total: int) -> float:
    return 100.0 * count / total if total else float("nan")


def _log_exclusion(step: str, n_excluded: int, n_total: int) -> None:
    """Log ``primary n/N (rate), excluded m (rate)``; raise above ``MAX_EXCLUSION_RATE``."""
    (LOG.warning if n_excluded else LOG.info)(
        "%s %s: primary %d/%d (%.1f%%), excluded %d (%.1f%%)", _LOG_TAG, step,
        n_total - n_excluded, n_total, _rate(n_total - n_excluded, n_total), n_excluded,
        _rate(n_excluded, n_total))
    if n_total and n_excluded / n_total > MAX_EXCLUSION_RATE:
        raise ValueError("%s %s: %d of %d (%.1f%%) excluded, above the %.0f%% limit; this is a "
                         "broken input, not a measurement" % (
                             _LOG_TAG, step, n_excluded, n_total, _rate(n_excluded, n_total),
                             100.0 * MAX_EXCLUSION_RATE))


def _flags(frame: pd.DataFrame, column: str) -> pd.Series:
    """A boolean column; a missing or non-boolean value raises (the model has no missing code)."""
    values = frame[column]
    bad = values.isna() | ~values.isin([True, False])
    if bad.any():
        raise ValueError("persons: column %s carries %d missing or non-boolean value(s), e.g. %s"
                         % (column, int(bad.sum()), values[bad].astype(str).unique()[:5].tolist()))
    return values.astype(bool)


def _counts(frame: pd.DataFrame, column: str, name: str = "persons") -> pd.Series:
    """A count column; a missing, negative or fractional value raises."""
    numeric = pd.to_numeric(frame[column], errors="coerce")
    bad = numeric.isna() | (numeric < 0) | (numeric != np.floor(numeric))
    if bad.any():
        raise ValueError("%s: column %s carries %d value(s) that are not a count, e.g. %s"
                         % (name, column, int(bad.sum()),
                            frame.loc[bad, column].astype(str).unique()[:5].tolist()))
    return numeric


def _yes_no(flag) -> np.ndarray:
    return np.where(np.asarray(flag, dtype=bool), "yes", "no").astype(object)


# --------------------------------------------------------------------------- model students
def _p_taet_codes(frame: pd.DataFrame, name: str = "persons") -> tuple[pd.Series, pd.Series]:
    """``(code, unreadable)`` of the ``P_TAET`` column; a code outside the documented set raises.

    ``unreadable`` marks ``P_TAET_NO_ANSWER`` (99) and missing values: they answer nothing about
    the activity, so the caller excludes and counts them (never "not a student").
    """
    raw = frame["P_TAET"]
    code = pd.to_numeric(raw, errors="coerce")
    non_numeric = raw.notna() & code.isna()
    undocumented = sorted(set(code[code.notna() & ~code.isin(sorted(P_TAET_CODES))
                                   & (code != P_TAET_NO_ANSWER)].tolist()))
    if non_numeric.any() or undocumented:
        raise ValueError("%s: column P_TAET carries code(s) %s outside the documented set "
                         "%s and %d" % (name, undocumented
                                        + raw[non_numeric].astype(str).tolist()[:5],
                                        sorted(P_TAET_CODES), P_TAET_NO_ANSWER))
    return code, code.isna() | (code == P_TAET_NO_ANSWER)


def _adult_students(persons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The adult students of ``persons`` and the counts of the persons who cannot be one."""
    code, unreadable = _p_taet_codes(persons)
    n_unreadable = int(unreadable.sum())
    _log_exclusion("readable P_TAET (99 / missing is never 'not a student')", n_unreadable,
                   len(persons))
    is_student = (code == STUDENT_P_TAET).to_numpy()
    age = pd.to_numeric(persons["age"], errors="coerce")
    n_students = int(is_student.sum())
    n_missing_age = int(age[is_student].isna().sum())
    _log_exclusion("students with a known age (no age, no adult)", n_missing_age, n_students)
    adult = is_student & (age >= MIN_ADULT_AGE).to_numpy()
    students = persons.loc[adult].reset_index(drop=True)
    if students.empty:
        raise ValueError("persons: no adult student (P_TAET == %d, age >= %d) among %d persons; "
                         "the measurement needs the popsim_mid P_TAET column"
                         % (STUDENT_P_TAET, MIN_ADULT_AGE, len(persons)))
    return students, {"n_persons": int(len(persons)), "n_unreadable_p_taet": n_unreadable,
                      "n_students": n_students, "n_students_missing_age": n_missing_age,
                      "n_adult_students": int(len(students))}


def _household_labels(students: pd.DataFrame, persons: pd.DataFrame) -> dict:
    """``household_type`` and ``lives_with_person_18_years_older`` from all household members."""
    age = pd.to_numeric(persons["age"], errors="coerce")
    members = pd.DataFrame({"household_id": persons["household_id"].to_numpy(),
                            "age": age.where(age >= 0).to_numpy()})
    by_household = members.groupby("household_id")
    households = pd.DataFrame({
        "n_members": by_household.size(),
        "oldest_age": by_household["age"].max(),
        "n_minors": (members["age"] < MIN_ADULT_AGE).groupby(members["household_id"]).sum(),
        "n_unknown_age": members["age"].isna().groupby(members["household_id"]).sum(),
    }).rename_axis("household_id").reset_index()
    joined = students[["person_id", "household_id", "age"]].merge(
        households, on="household_id", how="left", validate="m:1")
    household_type = np.select(
        [joined["n_members"] == 1, joined["n_minors"] > 0, joined["n_unknown_age"] > 0],
        ["alone", "with_children", ""], default="several_adults")
    older = joined["oldest_age"] >= pd.to_numeric(joined["age"]) + OLDER_PERSON_GAP_YEARS
    lives_with_older = np.select([older, joined["n_unknown_age"] > 0], ["yes", ""], default="no")
    return {"household_type": pd.Series(household_type, dtype=object).replace("", np.nan),
            "lives_with_person_18_years_older":
                pd.Series(lives_with_older, dtype=object).replace("", np.nan)}


def _ticket_labels(students: pd.DataFrame) -> dict:
    """``ticket_group4`` and ``season_ticket`` through the existing four-group mapping."""
    ticket_type = students["pt_subscription_type"]
    unknown = ticket_type.isna() | ~ticket_type.isin(PT_TICKET_CATEGORIES)
    if unknown.any():
        raise ValueError("persons: column pt_subscription_type carries %d value(s) outside %s, "
                         "e.g. %s" % (int(unknown.sum()), list(PT_TICKET_CATEGORIES),
                                      ticket_type[unknown].astype(str).unique()[:5].tolist()))
    group4 = map_pt_ticket_group(students[["pt_subscription_type"]])["pt_ticket_group4"]
    flatrate = group4.isin(flatrate_ticket_groups4()).to_numpy()
    disagree = flatrate != _flags(students, "has_pt_subscription").to_numpy()
    if disagree.any():
        raise ValueError("persons: has_pt_subscription disagrees with the flat-rate groups %s of "
                         "pt_subscription_type for %d adult student(s); both must derive from "
                         "PT_TICKET_FLATRATE (ADR-0087)" % (list(flatrate_ticket_groups4()),
                                                           int(disagree.sum())))
    return {"season_ticket": pd.Series(np.where(flatrate, "flatrate", "no_flatrate"),
                                       dtype=object),
            "ticket_group4": pd.Series(group4.to_numpy(), dtype=object)}


def _attribute_labels(students: pd.DataFrame, persons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The who and mobility-tool labels, one row per student in ``students`` order."""
    sex = students["sex"]
    odd_sex = sex.isna() | ~sex.isin(SP.PARAMETERS["sex"].labels)
    if odd_sex.any():
        raise ValueError("persons: column sex carries %d value(s) outside %s among the adult "
                         "students, e.g. %s" % (int(odd_sex.sum()),
                                                list(SP.PARAMETERS["sex"].labels),
                                                sex[odd_sex].astype(str).unique()[:5].tolist()))
    licence = _flags(students, "has_license").to_numpy()
    cars = _counts(students, "number_of_cars").to_numpy()
    bicycles = _counts(students, "number_of_bicycles").to_numpy()
    labels = {
        "age_group": pd.cut(pd.to_numeric(students["age"]), list(AGE_GROUP_EDGES),
                            labels=list(SP.PARAMETERS["age_group"].labels)).astype(object),
        "sex": sex.astype(object),
        **_household_labels(students, persons),
        "licence": _yes_no(licence),
        "cars_in_household": np.select([cars == 0, cars == 1], ["0", "1"],
                                       default="2plus").astype(object),
        "car_access": _yes_no(licence & (cars >= 1)),
        "bicycle_in_household": _yes_no(bicycles >= 1),
        **_ticket_labels(students),
    }
    frame = pd.DataFrame({name: np.asarray(labels[name], dtype=object)
                          for name in ATTRIBUTE_PARAMETERS})
    diagnostics = {}
    for name in ATTRIBUTE_PARAMETERS:
        n_excluded = int(frame[name].isna().sum())
        diagnostics["n_excluded_%s" % name] = n_excluded
        if name in ("household_type", "lives_with_person_18_years_older"):
            _log_exclusion("%s (a household member of unknown age leaves it undetermined)"
                           % name, n_excluded, len(frame))
    return frame, diagnostics


def _require_model_inputs(persons: pd.DataFrame, trips: pd.DataFrame,
                          homes_with_ars5: pd.DataFrame, absence: pd.DataFrame,
                          person_columns) -> None:
    """The input checks both model frames share: columns, unique and known person ids, a
    household on every person and a documented absence state on every absence row."""
    _require_columns(persons, person_columns, "persons")
    _require_columns(trips, P.MODEL_TRIP_COLUMNS, "trips")
    _require_columns(homes_with_ars5, ("household_id", P.KREIS_COLUMN), "homes")
    _require_columns(absence, ABSENCE_COLUMNS, "absence")
    _require_unique_person_ids(persons, "persons")
    if persons["household_id"].isna().any():
        raise ValueError("persons: %d row(s) without a household_id"
                         % int(persons["household_id"].isna().sum()))
    _require_unique_person_ids(absence, "absence")
    known = set(persons["person_id"])
    _require_known_person_ids(trips["person_id"], known, "trips")
    _require_known_person_ids(absence["person_id"], known, "absence")
    # absent_person_ids reads every state other than "present" as absent, so a missing or
    # unknown state would silently become an absent person.
    state = absence["day_absence_state"]
    odd_state = state.isna() | ~state.isin(STATES)
    if odd_state.any():
        raise ValueError("absence: column day_absence_state carries %d value(s) outside %s, "
                         "e.g. %s" % (int(odd_state.sum()), list(STATES),
                                      state[odd_state].astype(str).unique()[:5].tolist()))


def model_student_frame(persons: pd.DataFrame, trips: pd.DataFrame,
                        homes_with_ars5: pd.DataFrame, absence: pd.DataFrame, *,
                        max_unmatched_home_share: float) -> tuple[pd.DataFrame, dict]:
    """One row per adult model student with every label of the parameter vocabulary.

    ``persons`` is the enriched person frame (:data:`MODEL_PERSON_COLUMNS`), ``trips`` the trip
    frame of :data:`braunschweig.analysis.plan_structure.MODEL_TRIP_COLUMNS` (times in SECONDS),
    ``homes_with_ars5`` the ``spatial.assign_geographies`` output (``household_id``,
    :data:`~braunschweig.analysis.plan_structure.KREIS_COLUMN`), ``absence`` the
    ``absence_stage`` frame (:data:`ABSENCE_COLUMNS`). ``max_unmatched_home_share`` is the
    largest share of adult students without a home Kreis the caller accepts (in [0, 1]).

    Returns ``(frame, diagnostics)``: columns :data:`MODEL_STUDENT_COLUMNS` (``kreis`` NaN for a
    student without a home Kreis, ``present`` False for a student the day-absence draw marks
    away; a label NaN where the student is outside the parameter's base or excluded from it);
    diagnostics count the persons that cannot be students, the adult students per Kreis, those
    without a home Kreis, the absent ones, and every label exclusion (``n_excluded_<parameter>``,
    the day exclusions prefixed ``day_``). Definitions: module docstring.

    Raises ``ValueError`` for a missing column, a duplicated ``person_id`` in ``persons`` or
    ``absence``, a trip or absence row of an unknown person, an adult student without an
    absence row, a code outside its documented set (``day_absence_state`` outside the absence
    module's ``STATES`` included), a home Kreis outside ``ZGB_KREISE``, an exclusion (a label left
    undetermined, or a day parameter lost to a missing or negative time, over its base) above
    ``MAX_EXCLUSION_RATE``, or an unmatched-home share above ``max_unmatched_home_share``.
    """
    if not 0.0 <= float(max_unmatched_home_share) <= 1.0:
        raise ValueError("max_unmatched_home_share must be in [0, 1], got %r"
                         % max_unmatched_home_share)
    _require_model_inputs(persons, trips, homes_with_ars5, absence, MODEL_PERSON_COLUMNS)

    students, diagnostics = _adult_students(persons)
    without_absence = students.loc[~students["person_id"].isin(set(absence["person_id"])),
                                   "person_id"]
    if len(without_absence):
        raise ValueError("absence: %d adult student(s) have no row, their presence on the "
                         "reporting day is unknown (person_id e.g. %s)"
                         % (len(without_absence), sorted(without_absence.tolist())[:5]))
    absent_ids = absent_person_ids(absence)
    attributes, attribute_diagnostics = _attribute_labels(students, persons)
    diagnostics.update(attribute_diagnostics)

    # Harmonise the students only: their trips are all trips of these persons, so the n_trips
    # harmonise_model derives agrees with the trip rows person_day_labels checks.
    student_trips = trips[trips["person_id"].isin(set(students["person_id"]))]
    harmonised_persons, harmonised_trips = P.harmonise_model(
        students, student_trips, homes_with_ars5, absent_person_ids=absent_ids)
    day_labels, day_diagnostics = SP.person_day_labels(harmonised_persons, harmonised_trips)
    diagnostics.update({"day_%s" % key: value for key, value in day_diagnostics.items()})
    # person_day_labels only logs these exclusions; a time column that is mostly unreadable would
    # leave a tiny base, so the model side raises above MAX_EXCLUSION_RATE like the SrV side does.
    for parameter, base_key in _DAY_EXCLUSION_BASES:
        _log_exclusion("%s (a missing or negative time excludes the student from this parameter "
                       "only)" % parameter, day_diagnostics["n_excluded_%s" % parameter],
                       day_diagnostics[base_key])

    frame = pd.concat([students[["person_id"]], attributes], axis=1)
    frame["present"] = ~frame["person_id"].isin(absent_ids)
    frame = frame.merge(harmonised_persons[["pid", "kreis"]].rename(columns={"pid": "person_id"}),
                        on="person_id", how="left", validate="1:1")
    frame = frame.merge(day_labels.rename(columns={"pid": "person_id"}), on="person_id",
                        how="left", validate="1:1")
    if frame["education_leg"].isna().any():
        raise ValueError("the harmonised frames lost %d adult student(s): no day labels"
                         % int(frame["education_leg"].isna().sum()))

    kreis = frame["kreis"].where(frame["kreis"].isna(), frame["kreis"].astype(str))
    outside = sorted(set(kreis.dropna()) - set(ZGB_KREISE))
    if outside:
        raise ValueError("homes: column %s carries Kreis code(s) %s outside ZGB_KREISE %s"
                         % (P.KREIS_COLUMN, outside, list(ZGB_KREISE)))
    frame["kreis"] = kreis.astype(object)
    n_students = len(frame)
    n_no_kreis = int(kreis.isna().sum())
    unmatched_share = n_no_kreis / n_students
    LOG.log(logging.WARNING if n_no_kreis else logging.INFO,
            "%s home Kreis: primary %d/%d adult students (%.1f%%), no home Kreis %d (%.1f%%)",
            _LOG_TAG, n_students - n_no_kreis, n_students, _rate(n_students - n_no_kreis,
                                                                 n_students),
            n_no_kreis, _rate(n_no_kreis, n_students))
    if unmatched_share > max_unmatched_home_share:
        raise ValueError("%d/%d (%.1f%%) adult students have no home Kreis, above "
                         "max_unmatched_home_share=%s; check the VG250 archive and the "
                         "home-location / household_id join" % (
                             n_no_kreis, n_students, _rate(n_no_kreis, n_students),
                             max_unmatched_home_share))
    n_absent = int((~frame["present"]).sum())
    diagnostics.update({
        "n_adult_students_by_kreis": {str(code): int(count) for code, count
                                      in kreis.value_counts().sort_index().items()},
        "n_adult_students_no_kreis": n_no_kreis,
        "unmatched_home_share": unmatched_share,
        "max_unmatched_home_share": float(max_unmatched_home_share),
        "n_adult_students_absent": n_absent,
        "n_adult_students_present": n_students - n_absent,
    })
    LOG.info("%s %d adult students (%d present, %d away all day) of %d persons; per Kreis %s",
             _LOG_TAG, n_students, n_students - n_absent, n_absent, len(persons),
             diagnostics["n_adult_students_by_kreis"])
    return frame[MODEL_STUDENT_COLUMNS], diagnostics


# --------------------------------------------------------------------------- comparison
def _grid() -> pd.DataFrame:
    """Every (group, parameter, base, label) cell of the vocabulary, in vocabulary order."""
    rows = [(spec.group, parameter, base, label)
            for parameter, spec in SP.PARAMETERS.items()
            for base in (spec.base,) + SP.EXTRA_BASES.get(parameter, ())
            for label in spec.labels]
    return pd.DataFrame(rows, columns=_KEY)


def _validated_srv_table(srv_parameters: pd.DataFrame) -> pd.DataFrame:
    """The SrV rows keyed like :func:`_grid`; any cell outside the vocabulary raises."""
    _require_columns(srv_parameters, SP.PARAMETER_COLUMNS, "SrV parameter table")
    table = srv_parameters[SP.PARAMETER_COLUMNS].copy()
    if table[_KEY].isna().any().any():
        raise ValueError("SrV parameter table: %d row(s) with a missing group, parameter, base "
                         "or label" % int(table[_KEY].isna().any(axis=1).sum()))
    table["label"] = table["label"].astype(str)
    cells = table[_KEY].merge(_grid(), on=_KEY, how="left", indicator=True)
    outside = cells[cells["_merge"] == "left_only"]
    if len(outside):
        raise ValueError("SrV parameter table: %d row(s) outside the vocabulary of "
                         "student_parameters.PARAMETERS (group, parameter, base, label), e.g. %s"
                         % (len(outside), outside[_KEY].head(5).values.tolist()))
    duplicated = table[table.duplicated(_KEY)]
    if len(duplicated):
        raise ValueError("SrV parameter table: %d duplicated cell(s), e.g. %s"
                         % (len(duplicated), duplicated[_KEY].head(5).values.tolist()))
    counts = _counts(table, "n_unweighted", "SrV parameter table").astype("Int64")
    return pd.DataFrame({**{key: table[key] for key in _KEY},
                         "srv_n_unweighted": counts,
                         "srv_share": pd.to_numeric(table["share"]).astype(float),
                         "srv_n_effective": pd.to_numeric(table["n_effective"]).astype(float),
                         "srv_se_share": pd.to_numeric(table["se_share"]).astype(float)})


def _model_rows(model_students: pd.DataFrame, kreise=SURVEYED_KREISE) -> pd.DataFrame:
    """Model counts and shares per cell over the given Kreise (default: the seven surveyed)."""
    in_scope = model_students[model_students["kreis"].isin(kreise).to_numpy()]
    present = in_scope[in_scope["present"].to_numpy()]
    frames = []
    for parameter, spec in SP.PARAMETERS.items():
        if parameter == "away_all_day":
            base, labels = SP.BASE_STUDENTS_ALL, pd.Series(_yes_no(~in_scope["present"]))
        else:
            base, labels = spec.base, present[parameter]
        rows = SP.parameter_rows(parameter, base, labels, pd.Series(1.0, index=labels.index))
        rows["model_n_base"] = int(rows["n_unweighted"].sum())
        frames.append(rows)
    model = pd.concat(frames, ignore_index=True)
    return pd.DataFrame({**{key: model[key] for key in _KEY},
                         "model_n_base": model["model_n_base"].astype("Int64"),
                         "model_n": model["n_unweighted"].astype("Int64"),
                         "model_share": model["share"].astype(float)})


def count_missing_srv_cells(comparison: pd.DataFrame) -> int:
    """Cells of a :func:`compare_parameters` table that the SrV table did not carry.

    A missing cell is a row whose ``srv_n_unweighted`` is NA; an SrV cell that was emitted empty
    carries 0 and is not counted.
    """
    _require_columns(comparison, ["srv_n_unweighted"], "comparison")
    return int(comparison["srv_n_unweighted"].isna().sum())


def compare_parameters(model_students: pd.DataFrame,
                       srv_parameters: pd.DataFrame) -> pd.DataFrame:
    """Every cell of the parameter vocabulary, model students against the SrV table.

    ``model_students`` is :func:`model_student_frame`; ``srv_parameters`` the SrV parameter table
    (:data:`student_parameters.PARAMETER_COLUMNS`, labels as strings). The model side is
    restricted to :data:`SURVEYED_KREISE`; its bases follow the module docstring (present
    students, the mobile and education subsets through the NaN labels of ``person_day_labels``,
    all students for ``away_all_day``; the :data:`MODEL_UNMEASURED_BASES` cells keep NaN model
    columns).

    Returns :data:`COMPARISON_COLUMNS`, one row per cell of the vocabulary in vocabulary order.
    ``model_n_base`` is the number of model students in the cell's base, ``model_n`` those with
    the label, ``model_share`` their quotient (NaN on an empty base). A cell missing from the SrV
    table is emitted with NaN SrV columns; their number is the number of rows with an NA
    ``srv_n_unweighted`` (an emitted empty SrV cell carries 0), logged here and recomputed by any
    consumer through :func:`count_missing_srv_cells`.
    ``difference_pp = 100 (model_share - srv_share)``; ``difference_in_se = (model_share -
    srv_share) / srv_se_share``, NaN where the SrV standard error is 0 or NaN (a share of 0 or 1
    has no binomial spread, so no finite distance in standard errors exists);
    ``srv_cell_ok = srv_n_unweighted >= MIN_RESPONDENTS_PER_CELL`` and ``beyond_two_se =
    |difference_in_se| > MIN_DIFFERENCE_STANDARD_ERRORS``, both nullable booleans that are NA
    where their input is undefined. Raises ``ValueError`` for a missing column, a duplicated
    ``person_id``, a non-boolean ``present``, a label outside the vocabulary on either side or a
    duplicated SrV cell.
    """
    model_students = _checked_model_students(model_students)
    srv = _validated_srv_table(srv_parameters)

    kreis = model_students["kreis"]
    n_total = len(model_students)
    n_scope = int(kreis.isin(SURVEYED_KREISE).sum())
    n_wolfsburg = int((kreis == WOLFSBURG_KREIS).sum())
    n_no_kreis = int(kreis.isna().sum())
    LOG.info("%s comparison universe: %d/%d adult students (%.1f%%) live in the %d surveyed "
             "Kreise; Wolfsburg (model only) %d, no home Kreis %d, other %d", _LOG_TAG, n_scope,
             n_total, _rate(n_scope, n_total), len(SURVEYED_KREISE), n_wolfsburg, n_no_kreis,
             n_total - n_scope - n_wolfsburg - n_no_kreis)

    return _compare_cells(model_students, srv, SURVEYED_KREISE, "region")


def _checked_model_students(model_students: pd.DataFrame) -> pd.DataFrame:
    """The model student frame with a validated boolean ``present`` and labels in the vocabulary."""
    _require_columns(model_students, MODEL_STUDENT_COLUMNS, "model students")
    _require_unique_person_ids(model_students, "model students")
    present = model_students["present"]
    if present.isna().any() or not present.isin([True, False]).all():
        raise ValueError("model students: column present must be boolean without missing values")
    for parameter in ATTRIBUTE_PARAMETERS + DAY_PARAMETERS:
        labels = model_students[parameter].dropna().astype(str)
        outside = sorted(set(labels) - set(SP.PARAMETERS[parameter].labels))
        if outside:
            raise ValueError("model students: parameter %r carries label(s) %s outside the "
                             "vocabulary %s" % (parameter, outside,
                                                list(SP.PARAMETERS[parameter].labels)))
    return model_students.assign(present=present.astype(bool))


def _compare_cells(model_students: pd.DataFrame, srv: pd.DataFrame, kreise, scope: str,
                   srv_expected: bool = True) -> pd.DataFrame:
    """The comparison table of one scope: the model students of ``kreise`` against ``srv``.

    ``srv`` is :func:`_validated_srv_table` output (empty for a scope SrV does not survey, with
    ``srv_expected`` False: its NaN SrV columns are by design and not logged as missing cells).
    """
    out = _grid().merge(_model_rows(model_students, kreise), on=_KEY, how="left", validate="1:1")
    out = out.merge(srv, on=_KEY, how="left", validate="1:1")
    n_missing_srv = count_missing_srv_cells(out)
    n_unmeasured = int(out["base"].isin(MODEL_UNMEASURED_BASES).sum())
    if srv_expected:
        (LOG.warning if n_missing_srv else LOG.info)(
            "%s cells (%s): %d in the vocabulary; missing from the SrV table %d (NaN SrV "
            "columns), SrV only by design (bases %s) %d", _LOG_TAG, scope, len(out),
            n_missing_srv, list(MODEL_UNMEASURED_BASES), n_unmeasured)
    else:
        LOG.info("%s cells (%s): %d in the vocabulary; not surveyed by SrV, SrV columns NaN by "
                 "design", _LOG_TAG, scope, len(out))

    difference = out["model_share"] - out["srv_share"]
    se = out["srv_se_share"].where(out["srv_se_share"] > 0)
    out["difference_pp"] = 100.0 * difference
    out["difference_in_se"] = difference / se
    n_zero_se = int((difference.notna() & se.isna() & out["srv_se_share"].notna()).sum())
    if n_zero_se:
        LOG.info("%s %d cell(s) with a SrV standard error of 0: difference in standard errors "
                 "undefined (NaN)", _LOG_TAG, n_zero_se)
    out["srv_cell_ok"] = (out["srv_n_unweighted"] >= MIN_RESPONDENTS_PER_CELL).astype("boolean")
    beyond = (out["difference_in_se"].abs() > MIN_DIFFERENCE_STANDARD_ERRORS).astype("boolean")
    beyond[out["difference_in_se"].isna().to_numpy()] = pd.NA
    out["beyond_two_se"] = beyond
    return out[COMPARISON_COLUMNS]


def compare_parameters_by_area(model_students: pd.DataFrame,
                               srv_by_area: pd.DataFrame) -> pd.DataFrame:
    """:func:`compare_parameters` once per home area of :data:`srv_student_reference.HOME_AREAS`.

    ``srv_by_area`` is the committed ``srv2023_student_parameters_by_area.csv`` (column ``area``
    plus :data:`student_parameters.PARAMETER_COLUMNS`). The two surveyed areas (``braunschweig``,
    ``surrounding``) are compared with their SrV rows exactly as the region is; the model
    students of an area are those whose home Kreis is in ``HOME_AREAS[area]`` (the one mapping,
    never restated here). ``wolfsburg`` (:data:`srv_student_reference.AREA_WOLFSBURG`) is
    reported on the model side only: its SrV columns are NaN by design (SrV surveys no person
    there) and it never counts as a missing SrV cell. A student without a home Kreis is in no
    area, a home Kreis outside the ZGB raises (:func:`srv_student_reference.area_of_kreis`).

    Returns ``["area", *COMPARISON_COLUMNS]``: the surveyed areas in ``HOME_AREAS`` order, then
    Wolfsburg. Raises ``ValueError`` for a missing ``area`` column, a row without an area or with
    one outside ``HOME_AREAS`` (Wolfsburg included: it has no SrV rows), and for everything
    :func:`compare_parameters` raises for.
    """
    model_students = _checked_model_students(model_students)
    _require_columns(srv_by_area, ["area", *SP.PARAMETER_COLUMNS], "SrV parameter table by area")
    areas = srv_by_area["area"]
    outside = sorted(set(areas.dropna().astype(str)) - set(HOME_AREAS))
    if outside or areas.isna().any():
        raise ValueError("SrV parameter table by area: column area carries value(s) %s outside "
                         "the surveyed home areas %s" % (outside or "NaN", list(HOME_AREAS)))
    student_area = area_of_kreis(model_students["kreis"])
    LOG.info("%s comparison by area: adult students per home area %s, without a home Kreis %d",
             _LOG_TAG, student_area.value_counts().sort_index().to_dict(),
             int(student_area.isna().sum()))
    no_srv = _validated_srv_table(pd.DataFrame(columns=SP.PARAMETER_COLUMNS))
    scopes = [(area, kreise, True) for area, kreise in HOME_AREAS.items()]
    scopes.append((AREA_WOLFSBURG, (WOLFSBURG_KREIS,), False))
    frames = []
    for area, kreise, surveyed in scopes:
        srv = (_validated_srv_table(srv_by_area[(areas == area).to_numpy()]) if surveyed
               else no_srv)
        cells = _compare_cells(model_students, srv, kreise, "area " + area, srv_expected=surveyed)
        frames.append(cells.assign(area=area)[["area", *COMPARISON_COLUMNS]])
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- level and status
#: Columns required on the person frame of the level and young-adult tables: the enriched
#: persons with their home Kreis (``kreis``, five-digit string, NaN without a home Kreis) and
#: ``present`` (boolean, False for a person :func:`absent_person_ids` marks away all day).
PERSON_KREIS_COLUMNS = ("person_id", "age", "P_TAET", "employed", "kreis", "present")

#: Scope of the region row, for the CSV headers the stage writes.
REGION_SCOPE_NOTE = (
    "Region row %s = the %d SrV-surveyed Kreise %s (ZGB_KREISE minus Wolfsburg %s) on the model "
    "side; the SrV region row is built over the ZGB Kreise, of which SrV surveys no person in "
    "Wolfsburg. Wolfsburg rows are model-only with NaN SrV columns."
    % (REGION_CODE, len(SURVEYED_KREISE), list(SURVEYED_KREISE), WOLFSBURG_KREIS))

#: ``model_*`` counts and ``*_expanded_persons`` are over ALL persons (the population level);
#: ``model_present_*`` counts and every ``model_*share*`` over PRESENT persons, the universe of
#: the SrV shares (persons at home at some point of the reporting day).
LEVEL_COLUMNS = ["code", "level", "model_adults", "model_students_18_29", "model_students_30plus",
                 "model_persons_20_29", "model_students_20_29", "model_present_adults",
                 "model_present_students_adults", "model_present_persons_20_29",
                 "model_present_students_20_29", "model_student_share_adults",
                 "model_student_share_20_29", "model_students_expanded_persons",
                 "srv_student_share_adults", "srv_se_student_share_adults",
                 "srv_student_share_20_29", "srv_se_student_share_20_29",
                 "srv_students_weighted_persons", "difference_in_se_adults",
                 "difference_in_se_20_29"]
#: SrV student-share column (``SHARE_COLUMNS``) -> level-table column.
_LEVEL_SRV_COLUMNS = {"student_share_adults": "srv_student_share_adults",
                      "se_student_share_adults": "srv_se_student_share_adults",
                      "student_share_20_29": "srv_student_share_20_29",
                      "se_student_share_20_29": "srv_se_student_share_20_29",
                      "students_weighted_persons": "srv_students_weighted_persons"}

#: ``model_n`` and ``model_expanded_persons`` over ALL persons, ``model_present_n`` and
#: ``model_share`` over PRESENT persons (the SrV universe).
YOUNG_ADULT_COLUMNS = ["code", "level", "status", "model_n", "model_present_n", "model_share",
                       "model_expanded_persons", "srv_share", "srv_se_share", "difference_in_se",
                       "zensus_employed_persons"]
#: Status of a young adult that the Zensus employed count belongs to.
_EMPLOYED_STATUS = YOUNG_ADULT_STATUSES[1]

#: Columns of the Zensus employed-by-age frame as ``zensus_employment_age.
#: read_kreis_employed_by_age`` returns it.
ZENSUS_EMPLOYED_COLUMNS = ("region", "age_class", "sex", "employed_persons")
#: The Regionaltabelle age class of the 20 to 29 year olds, derived from the SrV band.
ZENSUS_AGE_CLASS_20_29 = "%d-%d" % AGE_BAND_20_29
if ZENSUS_AGE_CLASS_20_29 not in KREIS_AGE_CLASSES:
    raise RuntimeError("age class %r of AGE_BAND_20_29 is not a Zensus class %s"
                       % (ZENSUS_AGE_CLASS_20_29, list(KREIS_AGE_CLASSES)))
_ZENSUS_SEXES = ["F", "M"]

WORK_CLASS_COLUMNS = ["employed", "employment_status", "model_n", "share"]


def _require_sampling_rate(sampling_rate) -> float:
    rate = float(sampling_rate)
    if not 0.0 < rate <= 1.0:
        raise ValueError("sampling_rate must be in (0, 1], got %r" % sampling_rate)
    return rate


def _share(count: int, total: int) -> float:
    return count / total if total else float("nan")


def _difference_in_se(model: pd.Series, srv: pd.Series, se: pd.Series) -> pd.Series:
    """``(model - srv) / se``; NaN where the SrV standard error is 0 or NaN (see
    :func:`compare_parameters`)."""
    return (model - srv) / se.where(se > 0)


def _require_string_codes(values: pd.Series, name: str, column: str) -> None:
    """A code column must hold strings: a code read as a number has lost its leading zero."""
    odd = values.notna() & ~values.map(lambda value: isinstance(value, str))
    if odd.any():
        raise ValueError("%s: column %s carries %d non-string code(s), e.g. %s; read the codes "
                         "as strings (a number loses the leading zero)"
                         % (name, column, int(odd.sum()), values[odd].head(5).tolist()))


def _persons_with_kreis(persons_with_kreis: pd.DataFrame,
                        table: str) -> tuple[pd.DataFrame, dict]:
    """The adults a level or status table counts and the counts of the persons excluded.

    In this order, each exclusion counted over the persons that reached it, logged and raising
    above ``MAX_EXCLUSION_RATE``: a known age (over all persons); then the adults (age >=
    ``MIN_ADULT_AGE``, the tables' base; minors are outside it, not excluded); a readable
    ``P_TAET`` (over the adults); a home Kreis (over the adults left). A home Kreis outside
    ``ZGB_KREISE`` or not a string, or a non-boolean ``employed`` / ``present``, raises.
    """
    _require_columns(persons_with_kreis, PERSON_KREIS_COLUMNS, "persons")
    _require_unique_person_ids(persons_with_kreis, "persons")
    code, unreadable = _p_taet_codes(persons_with_kreis)
    employed = _flags(persons_with_kreis, "employed")
    present = _flags(persons_with_kreis, "present")
    kreis = persons_with_kreis["kreis"]
    _require_string_codes(kreis, "persons", "kreis")
    outside = sorted(set(kreis.dropna()) - set(ZGB_KREISE))
    if outside:
        raise ValueError("persons: column kreis carries code(s) %s outside ZGB_KREISE %s"
                         % (outside, list(ZGB_KREISE)))
    age = pd.to_numeric(persons_with_kreis["age"], errors="coerce")
    no_age = age.isna() | (age < 0)
    n_persons = len(persons_with_kreis)
    _log_exclusion("%s: known age" % table, int(no_age.sum()), n_persons)
    adult = ~no_age & (age >= MIN_ADULT_AGE)
    n_adults = int(adult.sum())
    n_unreadable = int((adult & unreadable).sum())
    _log_exclusion("%s: readable P_TAET among the adults (99 / missing answers nothing about "
                   "the activity)" % table, n_unreadable, n_adults)
    kept = adult & ~unreadable
    n_no_kreis = int((kept & kreis.isna()).sum())
    _log_exclusion("%s: home Kreis" % table, n_no_kreis, int(kept.sum()))
    kept &= kreis.notna()
    frame = pd.DataFrame({"code": code[kept].to_numpy(), "age": age[kept].to_numpy(),
                          "kreis": kreis[kept].to_numpy(dtype=object),
                          "employed": employed[kept].to_numpy(),
                          "present": present[kept].to_numpy()})
    diagnostics = {"n_persons": n_persons, "n_missing_age": int(no_age.sum()),
                   "n_adults_with_age": n_adults, "n_adults_unreadable_p_taet": n_unreadable,
                   "n_adults_no_home_kreis": n_no_kreis, "n_adults_counted": int(len(frame)),
                   "n_adults_counted_absent": int((~frame["present"]).sum())}
    return frame, diagnostics


def _scopes(frame: pd.DataFrame):
    """``(level, code, persons)`` per ZGB Kreis, then the region row over the surveyed Kreise."""
    for kreis in ZGB_KREISE:
        yield LEVEL_KREIS, kreis, frame[(frame["kreis"] == kreis).to_numpy()]
    yield LEVEL_TOTAL, REGION_CODE, frame[frame["kreis"].isin(SURVEYED_KREISE).to_numpy()]


def _validated_srv_rows(srv: pd.DataFrame, columns, key: list, name: str,
                        labels: dict | None = None) -> pd.DataFrame:
    """The SrV rows; a code that is not a string or outside the ZGB Kreise and the region, a
    value outside ``labels`` (column -> allowed values) or a duplicated ``key`` raises."""
    _require_columns(srv, columns, name)
    table = srv[list(columns)].copy()
    _require_string_codes(table["code"], name, "code")
    for column, allowed in {"code": list(ZGB_KREISE) + [REGION_CODE], **(labels or {})}.items():
        outside = sorted(set(table[column].astype(str)) - set(allowed))
        if outside or table[column].isna().any():
            raise ValueError("%s: column %s carries value(s) %s outside %s"
                             % (name, column, outside, list(allowed)))
    duplicated = table[table.duplicated(key)]
    if len(duplicated):
        raise ValueError("%s: %d duplicated row(s) on %s, e.g. %s"
                         % (name, len(duplicated), key, duplicated[key].head(5).values.tolist()))
    return table


def _missing_srv_codes(merged: pd.DataFrame, indicator: str, name: str) -> list:
    """The surveyed Kreise or region whose rows the SrV table did not carry; counted, logged."""
    missing = sorted(set(merged.loc[(merged[indicator] == "left_only")
                                    & (merged["code"] != WOLFSBURG_KREIS), "code"]))
    (LOG.warning if missing else LOG.info)(
        "%s %s: %d surveyed Kreis / region code(s) missing from the SrV table (NaN SrV columns) "
        "%s", _LOG_TAG, name, len(missing), missing)
    return missing


def student_level_table(persons_with_kreis: pd.DataFrame, srv_shares: pd.DataFrame,
                        sampling_rate: float) -> tuple[pd.DataFrame, dict]:
    """Student level per Kreis and region, model against the committed SrV student shares.

    ``persons_with_kreis`` carries :data:`PERSON_KREIS_COLUMNS` (``present`` from
    :func:`absent_person_ids`); ``srv_shares`` is ``srv2023_student_share_by_kreis.csv``
    (``srv_student_reference.SHARE_COLUMNS``, codes read as strings); ``sampling_rate`` the
    run's sampling rate in (0, 1].

    One row per ``ZGB_KREISE`` Kreis (``level`` kreis) and one :data:`REGION_CODE` row (``level``
    total) over the seven surveyed Kreise (:data:`REGION_SCOPE_NOTE`). Model persons: adults
    with a readable ``P_TAET`` and a home Kreis (exclusions: :func:`_persons_with_kreis`).
    Students are ``P_TAET == STUDENT_P_TAET``; 20 to 29 is ``AGE_BAND_20_29`` (inclusive, the
    SrV band).

    * Over ALL persons (the population level): ``model_adults``, ``model_students_18_29``,
      ``model_students_30plus``, ``model_persons_20_29``, ``model_students_20_29`` and
      ``model_students_expanded_persons`` (adult students / ``sampling_rate``).
    * Over PRESENT persons (the SrV universe; every SrV share excludes persons away from home
      all day): ``model_present_*`` and the shares ``model_student_share_adults`` /
      ``model_student_share_20_29`` (NaN on an empty base), so the difference compares one
      universe on both sides.
    * SrV columns: the SrV table's own; ``srv_students_weighted_persons`` is the weighted count
      of the adult students of the SrV universe, so it EXCLUDES the SrV students away from home
      all day (design A11: the population cross-check adds them back later, not here).
      Wolfsburg rows carry NaN there (not surveyed); a missing SrV row is counted.
    * ``difference_in_se_* = (model share - SrV share) / SrV se``, NaN where that se is 0 or NaN.

    Returns ``(table, diagnostics)``: :data:`LEVEL_COLUMNS`; the exclusion counts of
    :func:`_persons_with_kreis` and ``missing_srv_codes`` / ``n_missing_srv_rows``.
    """
    rate = _require_sampling_rate(sampling_rate)
    persons, diagnostics = _persons_with_kreis(persons_with_kreis, "student level")
    srv = _validated_srv_rows(srv_shares, SHARE_COLUMNS, ["code"], "SrV student share table")
    rows = []
    for level, code, group in _scopes(persons):
        age = group["age"]
        student = (group["code"] == STUDENT_P_TAET).to_numpy()
        young = age.between(*AGE_BAND_20_29).to_numpy()
        up_to_29 = (age <= AGE_BAND_20_29[1]).to_numpy()
        present = group["present"].to_numpy(dtype=bool)
        n_present, n_present_young = int(present.sum()), int((present & young).sum())
        n_present_students = int((present & student).sum())
        n_present_young_students = int((present & student & young).sum())
        rows.append({"code": code, "level": level, "model_adults": int(len(group)),
                     "model_students_18_29": int((student & up_to_29).sum()),
                     "model_students_30plus": int((student & ~up_to_29).sum()),
                     "model_persons_20_29": int(young.sum()),
                     "model_students_20_29": int((student & young).sum()),
                     "model_present_adults": n_present,
                     "model_present_students_adults": n_present_students,
                     "model_present_persons_20_29": n_present_young,
                     "model_present_students_20_29": n_present_young_students,
                     "model_student_share_adults": _share(n_present_students, n_present),
                     "model_student_share_20_29": _share(n_present_young_students,
                                                         n_present_young),
                     "model_students_expanded_persons": int(student.sum()) / rate})
    out = pd.DataFrame(rows).merge(
        srv[["code", *_LEVEL_SRV_COLUMNS]].rename(columns=_LEVEL_SRV_COLUMNS), on="code",
        how="left", validate="1:1", indicator="_srv")
    missing = _missing_srv_codes(out, "_srv", "student level")
    diagnostics.update(missing_srv_codes=missing, n_missing_srv_rows=len(missing))
    srv_columns = list(_LEVEL_SRV_COLUMNS.values())
    out[srv_columns] = out[srv_columns].astype(float)
    out.loc[out["code"] == WOLFSBURG_KREIS, srv_columns] = np.nan
    out["difference_in_se_adults"] = _difference_in_se(
        out["model_student_share_adults"], out["srv_student_share_adults"],
        out["srv_se_student_share_adults"])
    out["difference_in_se_20_29"] = _difference_in_se(
        out["model_student_share_20_29"], out["srv_student_share_20_29"],
        out["srv_se_student_share_20_29"])
    region = out[out["code"] == REGION_CODE].iloc[0]
    LOG.info("%s student level, region %s: %d adult students of %d adults, %.0f persons "
             "expanded at sampling rate %s; present: share %.4f", _LOG_TAG, REGION_CODE,
             region["model_students_18_29"] + region["model_students_30plus"],
             region["model_adults"], region["model_students_expanded_persons"], rate,
             region["model_student_share_adults"])
    return out[LEVEL_COLUMNS], diagnostics


def _zensus_employed_20_29(zensus_employed: pd.DataFrame) -> tuple[dict, list]:
    """Zensus 2022 employed persons aged 20 to 29, both sexes summed, per ZGB Kreis and region.

    ``zensus_employed`` is the frame ``zensus_employment_age.read_kreis_employed_by_age`` returns
    for ``KREIS_EMPLOYED_BY_AGE_FILE``. A ZGB Kreis without rows is NaN, counted, logged, and the
    missing Kreise raise above ``MAX_EXCLUSION_RATE`` of the ZGB Kreise; the region (the
    surveyed Kreise) is NaN when one of them is missing, as a partial sum would understate it.
    A region code that is not a string, a Kreis whose class rows are not exactly one per sex, or
    a count that is not a count raises. Returns ``(counts by code, missing Kreise)``.
    """
    _require_columns(zensus_employed, ZENSUS_EMPLOYED_COLUMNS, "Zensus employed-by-age")
    _require_string_codes(zensus_employed["region"], "Zensus employed-by-age", "region")
    rows = zensus_employed[zensus_employed["age_class"] == ZENSUS_AGE_CLASS_20_29]
    counts, missing = {}, []
    for kreis in ZGB_KREISE:
        kreis_rows = rows[(rows["region"] == kreis).to_numpy()]
        if kreis_rows.empty:
            missing.append(kreis)
            counts[kreis] = float("nan")
            continue
        if sorted(kreis_rows["sex"].astype(str)) != _ZENSUS_SEXES:
            raise ValueError("Zensus employed-by-age: Kreis %s, class %s carries sexes %s, "
                             "expected one row each of %s" % (kreis, ZENSUS_AGE_CLASS_20_29,
                                                              sorted(kreis_rows["sex"]),
                                                              _ZENSUS_SEXES))
        counts[kreis] = float(_counts(kreis_rows, "employed_persons",
                                      "Zensus employed-by-age").sum())
    _log_exclusion("Zensus employed %s: ZGB Kreise found (missing %s; NaN there%s)" % (
        ZENSUS_AGE_CLASS_20_29, missing,
        " and on the region row" if set(missing) & set(SURVEYED_KREISE) else ""),
        len(missing), len(ZGB_KREISE))
    counts[REGION_CODE] = (float("nan") if set(missing) & set(SURVEYED_KREISE)
                           else float(sum(counts[kreis] for kreis in SURVEYED_KREISE)))
    return counts, missing


def young_adult_status_table(persons_with_kreis: pd.DataFrame, srv_status: pd.DataFrame,
                             zensus_employed: pd.DataFrame,
                             sampling_rate: float) -> tuple[pd.DataFrame, dict]:
    """Activity status of the 20 to 29 year olds per Kreis and region: model, SrV and Zensus.

    ``persons_with_kreis`` as in :func:`student_level_table`; ``srv_status`` is
    ``srv2023_young_adult_status_by_kreis.csv`` (``srv_student_reference.
    YOUNG_ADULT_STATUS_COLUMNS``, codes as strings); ``zensus_employed`` the frame
    ``zensus_employment_age.read_kreis_employed_by_age`` returns for
    ``zensus_employment_age.KREIS_EMPLOYED_BY_AGE_FILE``; ``sampling_rate`` in (0, 1].

    Persons aged ``AGE_BAND_20_29`` (inclusive) of the persons :func:`student_level_table`
    counts. Model status, in the order of the SrV builder: ``student`` (``P_TAET ==
    STUDENT_P_TAET``), else ``employed`` (``employed`` true, which includes apprentices as SrV's
    employed codes do), else ``other``. One block of the three statuses per ZGB Kreis and one
    :data:`REGION_CODE` block over the surveyed Kreise (:data:`REGION_SCOPE_NOTE`).

    * Over ALL persons (the population level): ``model_n`` and ``model_expanded_persons`` (=
      ``model_n / sampling_rate``), and ``zensus_employed_persons``.
    * Over PRESENT persons (the SrV universe, which excludes persons away from home all day):
      ``model_present_n`` and ``model_share`` (within the block's present persons, NaN on an
      empty block).
    * SrV columns: the SrV table's ``share`` / ``se_share`` (NaN for Wolfsburg; a missing row
      counted); ``difference_in_se`` as in :func:`student_level_table`.
    * ``zensus_employed_persons`` is set on the ``employed`` rows only: the Zensus 2022 employed
      persons of the age class :data:`ZENSUS_AGE_CLASS_20_29`, both sexes summed (the region:
      the sum over the surveyed Kreise); NaN on the other rows and where the Kreis is missing
      from the Zensus frame.

    Returns ``(table, diagnostics)``: :data:`YOUNG_ADULT_COLUMNS`; the exclusion counts of
    :func:`_persons_with_kreis`, ``n_young_adults``, ``missing_srv_codes`` /
    ``n_missing_srv_rows`` and ``zensus_missing_kreise`` / ``n_zensus_missing_kreise``.
    """
    rate = _require_sampling_rate(sampling_rate)
    persons, diagnostics = _persons_with_kreis(persons_with_kreis, "young-adult status")
    srv = _validated_srv_rows(srv_status, YOUNG_ADULT_STATUS_COLUMNS, ["code", "status"],
                              "SrV young-adult status table",
                              {"status": list(YOUNG_ADULT_STATUSES)})
    zensus, zensus_missing = _zensus_employed_20_29(zensus_employed)
    young = persons[persons["age"].between(*AGE_BAND_20_29).to_numpy()]
    status = np.select([(young["code"] == STUDENT_P_TAET).to_numpy(),
                        young["employed"].to_numpy(dtype=bool)],
                       list(YOUNG_ADULT_STATUSES[:2]), default=YOUNG_ADULT_STATUSES[2])
    young = young.assign(status=status)
    rows = []
    for level, code, group in _scopes(young):
        present = group[group["present"].to_numpy(dtype=bool)]
        for label in YOUNG_ADULT_STATUSES:
            n = int((group["status"] == label).sum())
            n_present = int((present["status"] == label).sum())
            rows.append({"code": code, "level": level, "status": label, "model_n": n,
                         "model_present_n": n_present,
                         "model_share": _share(n_present, len(present)),
                         "model_expanded_persons": n / rate,
                         "zensus_employed_persons": (zensus[code] if label == _EMPLOYED_STATUS
                                                     else float("nan"))})
    out = pd.DataFrame(rows).merge(
        srv[["code", "status", "share", "se_share"]].rename(
            columns={"share": "srv_share", "se_share": "srv_se_share"}),
        on=["code", "status"], how="left", validate="1:1", indicator="_srv")
    missing = _missing_srv_codes(out, "_srv", "young-adult status")
    out[["srv_share", "srv_se_share"]] = out[["srv_share", "srv_se_share"]].astype(float)
    out.loc[out["code"] == WOLFSBURG_KREIS, ["srv_share", "srv_se_share"]] = np.nan
    out["difference_in_se"] = _difference_in_se(out["model_share"], out["srv_share"],
                                                out["srv_se_share"])
    diagnostics.update(n_young_adults=int(len(young)), missing_srv_codes=missing,
                       n_missing_srv_rows=int(((out["_srv"] == "left_only")
                                               & (out["code"] != WOLFSBURG_KREIS)).sum()),
                       zensus_missing_kreise=zensus_missing,
                       n_zensus_missing_kreise=len(zensus_missing))
    LOG.info("%s young-adult status: %d persons aged %d to %d in the surveyed Kreise (%d present)",
             _LOG_TAG, int(young["kreis"].isin(SURVEYED_KREISE).sum()), *AGE_BAND_20_29,
             int((young["kreis"].isin(SURVEYED_KREISE) & young["present"]).sum()))
    return out[YOUNG_ADULT_COLUMNS], diagnostics


def student_work_class_table(persons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Adult students by ``employed`` and ``employment_status``, all Kreise and none.

    Checks the design statement that every non-employed student falls into the not-employed
    classes of the work control (``employment_status`` outside
    ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES``): a student who breaks it, and a student
    with ``employed`` true, is reported (a row of its own, counted), never dropped. Columns
    :data:`WORK_CLASS_COLUMNS`, ``share`` over all adult students, sorted by ``employed`` and
    ``employment_status``. A missing ``employment_status`` or a non-boolean ``employed`` raises.

    Returns ``(table, diagnostics)``: the counts of ``_adult_students`` plus
    ``n_adult_students_employed`` and ``n_adult_students_not_employed_in_employed_class``.
    """
    _require_columns(persons, ("person_id", "age", "P_TAET", "employed", "employment_status"),
                     "persons")
    _require_unique_person_ids(persons, "persons")
    students, diagnostics = _adult_students(persons)
    if students["employment_status"].isna().any():
        raise ValueError("persons: %d adult student(s) without an employment_status"
                         % int(students["employment_status"].isna().sum()))
    frame = pd.DataFrame({"employed": _flags(students, "employed").to_numpy(),
                          "employment_status": students["employment_status"].astype(str)})
    table = (frame.groupby(["employed", "employment_status"]).size().rename("model_n")
             .reset_index().sort_values(["employed", "employment_status"]))
    table["share"] = table["model_n"] / len(frame)
    n_employed = int(frame["employed"].sum())
    n_breaking = int((~frame["employed"] & frame["employment_status"].isin(
        EMPLOYED_EMPLOYMENT_STATUS_CLASSES)).sum())
    (LOG.warning if n_employed or n_breaking else LOG.info)(
        "%s work class of %d adult students: employed %d (%.1f%%); not employed but in an "
        "employed class %s of the work control %d (%.1f%%)", _LOG_TAG, len(frame), n_employed,
        _rate(n_employed, len(frame)), list(EMPLOYED_EMPLOYMENT_STATUS_CLASSES), n_breaking,
        _rate(n_breaking, len(frame)))
    diagnostics.update(n_adult_students_employed=n_employed,
                       n_adult_students_not_employed_in_employed_class=n_breaking)
    return table[WORK_CLASS_COLUMNS].reset_index(drop=True), diagnostics


# --------------------------------------------------------------------------- education leg
#: Columns of :func:`adult_education_leg_frame`.
ADULT_EDUCATION_LEG_COLUMNS = ["person_id", "kreis", "age", "student", "present",
                               "education_leg"]
#: Columns the adult frame needs on the enriched persons (a subset of ``MODEL_PERSON_COLUMNS``).
ADULT_PERSON_COLUMNS = (*P.MODEL_PERSON_COLUMNS, "P_TAET")
#: ``model_*`` counts are over the PRESENT adults of the row (the SrV universe); the SrV columns
#: are the committed table's, ``srv_weight_sum`` the weight of the SrV persons WITH a leg.
EDUCATION_LEG_BY_STATUS_COLUMNS = [
    "code", "level", "group", "age_range", "model_n_present", "model_n_present_with_leg",
    "model_share", "model_persons_with_leg_expanded", "srv_n_unweighted", "srv_share",
    "srv_se_share", "srv_weight_sum", "difference_in_se"]
_EDUCATION_SRV_COLUMNS = {"n_unweighted": "srv_n_unweighted", "share": "srv_share",
                          "se_share": "srv_se_share", "weight_sum": "srv_weight_sum"}


def adult_education_leg_frame(persons: pd.DataFrame, trips: pd.DataFrame,
                              homes_with_ars5: pd.DataFrame,
                              absence: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per adult model person with the education leg of the reporting day.

    Inputs as for :func:`model_student_frame` (``persons`` needs :data:`ADULT_PERSON_COLUMNS`
    only; the student attribute columns are not read). Adult = ``age >= MIN_ADULT_AGE`` with a
    readable ``P_TAET`` (99 / missing answers nothing about the activity: excluded, counted,
    never "not a student"); ``student`` = ``P_TAET == STUDENT_P_TAET``. ``education_leg`` comes
    from the SAME harmonisation and :func:`student_parameters.person_day_labels` as the student
    frame (``education_leg == "yes"``), so the students' share here and in
    :func:`compare_parameters` is one definition. ``present`` is False for a person
    :func:`absent_person_ids` marks away all day; every adult needs an absence row.

    Columns :data:`ADULT_EDUCATION_LEG_COLUMNS` (``education_leg``, ``student`` and ``present``
    are booleans, ``kreis`` the five-digit home Kreis or NaN). Adults without a home Kreis stay
    in the frame (counted, logged, in no table row). Vectorised throughout: no per-person loop
    (a 100 % run has about a million adults).

    Raises ``ValueError`` for a missing column, a duplicated ``person_id``, a trip or absence
    row of an unknown person, an adult without an absence row, a code outside its documented
    set, a home Kreis outside ``ZGB_KREISE`` or an exclusion above ``MAX_EXCLUSION_RATE``.
    """
    _require_model_inputs(persons, trips, homes_with_ars5, absence, ADULT_PERSON_COLUMNS)
    code, unreadable = _p_taet_codes(persons)
    age = pd.to_numeric(persons["age"], errors="coerce")
    no_age = age.isna() | (age < 0)
    n_persons = len(persons)
    _log_exclusion("adult education leg: known age (no age, no adult)", int(no_age.sum()),
                   n_persons)
    adult = (~no_age & (age >= MIN_ADULT_AGE)).to_numpy()
    n_adults_with_age = int(adult.sum())
    n_unreadable = int((adult & unreadable.to_numpy()).sum())
    _log_exclusion("adult education leg: readable P_TAET among the adults (99 / missing "
                   "answers nothing about the activity)", n_unreadable, n_adults_with_age)
    keep = adult & ~unreadable.to_numpy()
    adults = persons.loc[keep].reset_index(drop=True)
    if adults.empty:
        raise ValueError("persons: no adult with a readable P_TAET (age >= %d) among %d persons"
                         % (MIN_ADULT_AGE, n_persons))
    without_absence = adults.loc[~adults["person_id"].isin(set(absence["person_id"])),
                                 "person_id"]
    if len(without_absence):
        raise ValueError("absence: %d adult(s) have no row, their presence on the reporting day "
                         "is unknown (person_id e.g. %s)"
                         % (len(without_absence), sorted(without_absence.tolist())[:5]))
    absent_ids = absent_person_ids(absence)

    adult_trips = trips[trips["person_id"].isin(set(adults["person_id"]))]
    harmonised_persons, harmonised_trips = P.harmonise_model(
        adults, adult_trips, homes_with_ars5, absent_person_ids=absent_ids)
    day_labels, day_diagnostics = SP.person_day_labels(harmonised_persons, harmonised_trips)
    frame = pd.DataFrame({
        "person_id": adults["person_id"].to_numpy(),
        "age": pd.to_numeric(adults["age"]).to_numpy(),
        "student": (code[keep] == STUDENT_P_TAET).to_numpy(),
        "present": ~adults["person_id"].isin(absent_ids).to_numpy()})
    frame = frame.merge(harmonised_persons[["pid", "kreis"]].rename(columns={"pid": "person_id"}),
                        on="person_id", how="left", validate="1:1")
    frame = frame.merge(day_labels[["pid", "education_leg"]].rename(columns={"pid": "person_id"}),
                        on="person_id", how="left", validate="1:1")
    if frame["education_leg"].isna().any():
        raise ValueError("the harmonised frames lost %d adult(s): no day labels"
                         % int(frame["education_leg"].isna().sum()))
    frame["education_leg"] = (frame["education_leg"] == "yes").to_numpy()

    kreis = frame["kreis"].where(frame["kreis"].isna(), frame["kreis"].astype(str))
    outside = sorted(set(kreis.dropna()) - set(ZGB_KREISE))
    if outside:
        raise ValueError("homes: column %s carries Kreis code(s) %s outside ZGB_KREISE %s"
                         % (P.KREIS_COLUMN, outside, list(ZGB_KREISE)))
    frame["kreis"] = kreis.astype(object)
    n_no_kreis = int(kreis.isna().sum())
    _log_exclusion("adult education leg: home Kreis (an adult without one is in no table row)",
                   n_no_kreis, len(frame))
    n_absent = int((~frame["present"]).sum())
    LOG.info("%s adult education leg: %d adults (%d students, %d present, %d away all day); "
             "with an education leg %d", _LOG_TAG, len(frame), int(frame["student"].sum()),
             len(frame) - n_absent, n_absent, int(frame["education_leg"].sum()))
    diagnostics = {"n_persons": int(n_persons), "n_missing_age": int(no_age.sum()),
                   "n_adults_with_age": n_adults_with_age,
                   "n_adults_unreadable_p_taet": n_unreadable,
                   "n_adults_counted": int(len(frame)), "n_adults_no_home_kreis": n_no_kreis,
                   "n_adults_counted_absent": n_absent,
                   **{"day_%s" % key: value for key, value in day_diagnostics.items()}}
    return frame[ADULT_EDUCATION_LEG_COLUMNS], diagnostics


def education_leg_by_status_table(adults: pd.DataFrame, srv_status: pd.DataFrame,
                                  sampling_rate: float) -> tuple[pd.DataFrame, dict]:
    """The education-leg share of students and other adults, model against SrV (adults, 18plus).

    ``adults`` is :func:`adult_education_leg_frame`; ``srv_status`` the committed
    ``srv2023_education_leg_by_student_status.csv``
    (:data:`srv_student_reference.EDUCATION_LEG_BY_STATUS_COLUMNS`, codes as strings);
    ``sampling_rate`` the run's rate in (0, 1].

    Rows (:data:`EDUCATION_LEG_BY_STATUS_COLUMNS`): per ``ZGB_KREISE`` Kreis the age range
    ``18plus``, and for :data:`REGION_CODE` (the seven surveyed Kreise, :data:`REGION_SCOPE_NOTE`)
    every range of :data:`srv_student_reference.EDUCATION_AGE_RANGES` (bounds inclusive), each per
    group ``student`` / ``other_adult``. ``model_n_present`` counts the PRESENT adults of the row
    (the SrV universe: persons at home at some point of the reporting day), ``model_share`` is
    the share of them with an education leg (NaN on an empty base),
    ``model_persons_with_leg_expanded`` those with a leg divided by ``sampling_rate`` (the
    like-for-like count of ``srv_weight_sum``). Wolfsburg is model only: its SrV columns are NaN
    by design. ``difference_in_se = (model_share - srv_share) / srv_se_share`` (NaN where that
    standard error is 0 or NaN); a measurement, not a verdict.

    Adults without a home Kreis (counted, in no row) and adults away all day (outside the base)
    are reported in the diagnostics together with ``missing_srv_codes`` / ``n_missing_srv_rows``.
    Raises ``ValueError`` for a missing column, a non-boolean flag, a duplicated ``person_id``,
    a Kreis that is not a ZGB string code, or an invalid SrV row.
    """
    rate = _require_sampling_rate(sampling_rate)
    _require_columns(adults, ADULT_EDUCATION_LEG_COLUMNS, "adults")
    _require_unique_person_ids(adults, "adults")
    flags = {column: _flags(adults, column) for column in ("student", "present", "education_leg")}
    _require_string_codes(adults["kreis"], "adults", "kreis")
    outside = sorted(set(adults["kreis"].dropna()) - set(ZGB_KREISE))
    if outside:
        raise ValueError("adults: column kreis carries code(s) %s outside ZGB_KREISE %s"
                         % (outside, list(ZGB_KREISE)))
    age = pd.to_numeric(adults["age"], errors="coerce")
    if age.isna().any() or (age < MIN_ADULT_AGE).any():
        raise ValueError("adults: %d row(s) with a missing age or an age below %d"
                         % (int((age.isna() | (age < MIN_ADULT_AGE)).sum()), MIN_ADULT_AGE))
    ranges = {name: (low, high) for name, low, high in EDUCATION_AGE_RANGES}
    srv = _validated_srv_rows(
        srv_status, SRV_EDUCATION_LEG_BY_STATUS_COLUMNS, ["code", "group", "age_range"],
        "SrV education leg by student status",
        {"group": [GROUP_STUDENT, GROUP_OTHER_ADULT], "age_range": list(ranges)})

    frame = pd.DataFrame({"kreis": adults["kreis"].to_numpy(dtype=object),
                          "age": age.to_numpy(), "student": flags["student"].to_numpy(),
                          "present": flags["present"].to_numpy(),
                          "leg": flags["education_leg"].to_numpy()})
    n_no_kreis = int(frame["kreis"].isna().sum())
    n_absent = int((~frame["present"] & frame["kreis"].notna()).sum())
    frame = frame[frame["present"].to_numpy() & frame["kreis"].notna().to_numpy()]
    LOG.info("%s education leg by status: %d adults, of them without a home Kreis %d (in no "
             "row), away all day %d (outside the base); %d present adults counted", _LOG_TAG,
             len(adults), n_no_kreis, n_absent, len(frame))

    rows = []
    scopes = [(LEVEL_KREIS, kreis, frame[(frame["kreis"] == kreis).to_numpy()],
               [EDUCATION_AGE_RANGES[0][0]]) for kreis in ZGB_KREISE]
    scopes.append((LEVEL_TOTAL, REGION_CODE, frame[frame["kreis"].isin(SURVEYED_KREISE).to_numpy()],
                   list(ranges)))
    for level, code, group_frame, range_names in scopes:
        for range_name in range_names:
            low, high = ranges[range_name]
            in_range = group_frame[group_frame["age"].between(low, high).to_numpy()]
            for group, is_student in ((GROUP_STUDENT, True), (GROUP_OTHER_ADULT, False)):
                members = in_range[(in_range["student"] == is_student).to_numpy()]
                n_with_leg = int(members["leg"].sum())
                rows.append({"code": code, "level": level, "group": group,
                             "age_range": range_name, "model_n_present": int(len(members)),
                             "model_n_present_with_leg": n_with_leg,
                             "model_share": _share(n_with_leg, len(members)),
                             "model_persons_with_leg_expanded": n_with_leg / rate})
    key = ["code", "group", "age_range"]
    out = pd.DataFrame(rows).merge(
        srv[[*key, *_EDUCATION_SRV_COLUMNS]].rename(columns=_EDUCATION_SRV_COLUMNS), on=key,
        how="left", validate="1:1", indicator="_srv")
    missing = _missing_srv_codes(out, "_srv", "education leg by status")
    srv_columns = list(_EDUCATION_SRV_COLUMNS.values())
    out[srv_columns] = out[srv_columns].astype(float)
    out.loc[out["code"] == WOLFSBURG_KREIS, srv_columns] = np.nan
    out["difference_in_se"] = _difference_in_se(out["model_share"], out["srv_share"],
                                                out["srv_se_share"])
    diagnostics = {"n_adults": int(len(adults)), "n_adults_no_home_kreis": n_no_kreis,
                   "n_adults_away_all_day": n_absent, "n_adults_counted_present": int(len(frame)),
                   "missing_srv_codes": missing,
                   "n_missing_srv_rows": int(((out["_srv"] == "left_only")
                                              & (out["code"] != WOLFSBURG_KREIS)).sum())}
    region = out[(out["code"] == REGION_CODE) & (out["age_range"] == EDUCATION_AGE_RANGES[0][0])]
    LOG.info("%s education leg by status, region %s: %s", _LOG_TAG, REGION_CODE,
             "; ".join("%s model %.4f (n %d) vs SrV %.4f" % (
                 row["group"], row["model_share"], row["model_n_present"], row["srv_share"])
                 for _, row in region.iterrows()))
    return out[EDUCATION_LEG_BY_STATUS_COLUMNS], diagnostics


# --------------------------------------------------------------------------- plan source
PLAN_SOURCE_COLUMNS = ["plan_origin", "match_path", "donor_status", "model_n",
                       "share_of_students", "share_with_education_leg", "share_with_work_leg"]
#: Columns required on the students frame of :func:`plan_source_table`.
PLAN_STUDENT_COLUMNS = ("person_id", "present", "education_leg", "work_leg")
#: Columns required on the enriched persons; the seed key ``H_ID`` / ``P_ID`` is optional.
PLAN_PERSON_COLUMNS = ("person_id", "source_H_ID", "source_P_ID", "attribute_source_H_ID",
                       "attribute_source_P_ID")
SEED_KEY = ("H_ID", "P_ID")
WEEKEND_TRACE_COLUMNS = (*SEED_KEY, "resolution")
DIARY_TRACE_COLUMNS = (*SEED_KEY, "reason")
#: Diary-match reasons that keep the seed person's own diary: the diary match chose no path.
DIARY_KEEP_REASONS = (REASON_KEEP, REASON_KEEP_IMMOBILE)
DIARY_REASONS = (*DIARY_KEEP_REASONS, *REASONS_REMAP)
#: The ``resolution`` values ``weekend_plan_match.reassign_weekend_plan_sources`` writes. That
#: module keeps them as inline literals, not constants; adding constants there would change the
#: source the completed-donor stage folds into its cache token and devalidate its cached output,
#: so the set is listed here and pinned to the literals of that module by a source-scan test.
WEEKEND_RESOLUTIONS = ("own_plan", "member_completion_filler", "hh_match", "person_fallback",
                       "mixed_person_sweep")
PLAN_ORIGIN_OWN = "own_diary"
PLAN_ORIGIN_REDRAWN = "redrawn"
MATCH_PATH_UNRESOLVED = "unresolved"
DONOR_STATUS_UNKNOWN = "unknown"


def _integer_key(values: pd.Series, name: str, column: str) -> np.ndarray:
    """A key column as int64; a missing or fractional value raises (a lossy cast would join
    the wrong person)."""
    numeric = pd.to_numeric(values, errors="coerce")
    bad = numeric.isna() | (numeric != np.floor(numeric))
    if bad.any():
        raise ValueError("%s: column %s carries %d value(s) that are not an integer key, e.g. %s"
                         % (name, column, int(bad.sum()),
                            values[bad].astype(str).unique()[:5].tolist()))
    return numeric.to_numpy().astype("int64")


def _keyed(frame: pd.DataFrame, columns, name: str) -> pd.DataFrame:
    """``frame[columns]`` with an int64 seed key; a duplicated key raises (a join is m:1)."""
    _require_columns(frame, columns, name)
    out = frame[list(columns)].copy()
    for column in SEED_KEY:
        out[column] = _integer_key(frame[column], name, column)
    duplicated = out[out.duplicated(list(SEED_KEY))]
    if len(duplicated):
        raise ValueError("%s: %d duplicated (H_ID, P_ID) key(s), e.g. %s"
                         % (name, len(duplicated), duplicated[list(SEED_KEY)].head(5)
                            .values.tolist()))
    return out


def _donor_status(frame: pd.DataFrame, donor_persons: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """The donor's activity per row of ``frame`` (keyed by its int ``source_H_ID`` /
    ``source_P_ID``) and the counts of the donors not found or without a readable ``P_TAET``."""
    donors = _keyed(donor_persons, (*SEED_KEY, "P_TAET"), "donor persons")
    code, unreadable = _p_taet_codes(donors, "donor persons")
    donors = donors.assign(donor_code=code, donor_unreadable=unreadable).rename(
        columns={"H_ID": "source_H_ID", "P_ID": "source_P_ID"})
    joined = frame[["source_H_ID", "source_P_ID"]].merge(
        donors[["source_H_ID", "source_P_ID", "donor_code", "donor_unreadable"]],
        on=["source_H_ID", "source_P_ID"], how="left", validate="m:1", indicator=True)
    not_found = (joined["_merge"] == "left_only").to_numpy()
    unreadable = joined["donor_unreadable"].fillna(False).to_numpy(dtype=bool)
    donor_code = joined["donor_code"]
    status = np.select(
        [not_found | unreadable, donor_code == STUDENT_P_TAET, donor_code == PUPIL_P_TAET,
         donor_code == APPRENTICE_P_TAET, donor_code.isin(sorted(EMPLOYED_TAET))],
        [DONOR_STATUS_UNKNOWN, "student", "pupil", "apprentice", "employed"],
        default="other_not_employed").astype(object)
    return status, {"not_found": not_found, "unreadable": unreadable}


def _match_path(frame: pd.DataFrame, persons: pd.DataFrame, weekend_trace: pd.DataFrame,
                diary_trace: pd.DataFrame) -> tuple[np.ndarray, str | None]:
    """``diary:<reason>``, ``weekend:<resolution>`` or ``unresolved`` per row of ``frame``,
    and why every row is unresolved when the persons carry no seed key (else None)."""
    weekend = _keyed(weekend_trace, WEEKEND_TRACE_COLUMNS, "weekend trace")
    diary = _keyed(diary_trace, DIARY_TRACE_COLUMNS, "diary trace")
    odd_resolution = weekend["resolution"].isna() | ~weekend["resolution"].isin(
        WEEKEND_RESOLUTIONS)
    if odd_resolution.any():
        raise ValueError("weekend trace: column resolution carries %d value(s) outside %s, "
                         "e.g. %s" % (int(odd_resolution.sum()), list(WEEKEND_RESOLUTIONS),
                                      weekend.loc[odd_resolution, "resolution"].astype(str)
                                      .unique()[:5].tolist()))
    odd_reason = diary["reason"].isna() | ~diary["reason"].isin(DIARY_REASONS)
    if odd_reason.any():
        raise ValueError("diary trace: column reason carries %d value(s) outside %s, e.g. %s"
                         % (int(odd_reason.sum()), list(DIARY_REASONS),
                            diary.loc[odd_reason, "reason"].astype(str).unique()[:5].tolist()))
    missing_key = [column for column in SEED_KEY if column not in persons.columns]
    if missing_key:
        reason = ("the enriched persons carry no seed key column(s) %s, so no person reaches "
                  "the weekend or diary trace" % missing_key)
        return np.full(len(frame), MATCH_PATH_UNRESOLVED, dtype=object), reason
    seed = pd.DataFrame({column: _integer_key(frame[column], "persons", column)
                         for column in SEED_KEY})
    seed = seed.merge(diary[[*SEED_KEY, "reason"]], on=list(SEED_KEY), how="left",
                      validate="m:1")
    seed = seed.merge(weekend[[*SEED_KEY, "resolution"]], on=list(SEED_KEY), how="left",
                      validate="m:1")
    diary_path = seed["reason"].notna() & ~seed["reason"].isin(DIARY_KEEP_REASONS)
    path = np.where(diary_path, "diary:" + seed["reason"].astype(str),
                    np.where(seed["resolution"].notna(),
                             "weekend:" + seed["resolution"].astype(str),
                             MATCH_PATH_UNRESOLVED))
    return path.astype(object), None


def plan_source_table(students: pd.DataFrame, persons: pd.DataFrame,
                      donor_persons: pd.DataFrame, weekend_trace: pd.DataFrame,
                      diary_trace: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Whose day plan the model students carry, and through which match path.

    ``students`` carries :data:`PLAN_STUDENT_COLUMNS` (the :func:`model_student_frame` output);
    ``persons`` the enriched persons with :data:`PLAN_PERSON_COLUMNS` and, normally, the seed
    key ``H_ID`` / ``P_ID`` (not unique: PopulationSim replicates seed households);
    ``donor_persons`` is ``CompletedDonor.persons`` (``H_ID``, ``P_ID``, ``P_TAET``); the traces
    are the completed-donor stage's ``WEEKEND_TRACE_FILE`` (:data:`WEEKEND_TRACE_COLUMNS`) and
    ``DIARY_TRACE_FILE`` (:data:`DIARY_TRACE_COLUMNS`), one row per seed person. ``H_ID`` columns
    are cast to int64 (a missing or fractional key raises); persons join the traces and the
    donors m:1, students join persons 1:1 on ``person_id``.

    * ``plan_origin``: :data:`PLAN_ORIGIN_OWN` when (``source_H_ID``, ``source_P_ID``) equals
      (``attribute_source_H_ID``, ``attribute_source_P_ID``), else :data:`PLAN_ORIGIN_REDRAWN`.
    * ``match_path``: ``diary:<reason>`` where the diary-trace row of the seed key carries a
      reason outside :data:`DIARY_KEEP_REASONS`; else ``weekend:<resolution>`` where a weekend
      row exists; else ``unresolved`` (counted and logged, never raised: a trace need not reach
      every person). Without a seed key on ``persons`` every row is unresolved and
      ``diagnostics["unresolved_reason"]`` says why.
    * ``donor_status``: the ``P_TAET`` of the plan donor (``source_*``) in ``donor_persons``:
      ``student`` (10), ``pupil`` (9), ``apprentice`` (8), ``employed`` (``EMPLOYED_TAET``),
      else ``other_not_employed``; ``unknown`` where the donor is not found or its ``P_TAET``
      is 99 / missing (counted apart). Unknown donors above ``MAX_EXCLUSION_RATE`` of the
      re-drawn plans raise.

    Returns ``(table, diagnostics)``: :data:`PLAN_SOURCE_COLUMNS`, one row per occurring
    combination; ``share_of_students`` over all rows of ``students``; the leg shares over the
    PRESENT students of the row (absent ones have no day; NaN where none is present).
    """
    _require_columns(students, PLAN_STUDENT_COLUMNS, "students")
    _require_columns(persons, PLAN_PERSON_COLUMNS, "persons")
    _require_unique_person_ids(students, "students")
    _require_unique_person_ids(persons, "persons")
    _require_known_person_ids(students["person_id"], set(persons["person_id"]), "students")
    if students.empty:
        raise ValueError("students: no row; the plan-source table needs the model students")
    present = _flags(students, "present").to_numpy()
    for leg in ("education_leg", "work_leg"):
        odd = present & ~students[leg].isin(SP.YES_NO).to_numpy()
        if odd.any():
            raise ValueError("students: column %s carries %d value(s) outside %s among the "
                             "present students" % (leg, int(odd.sum()), list(SP.YES_NO)))

    person_columns = [*PLAN_PERSON_COLUMNS,
                      *[column for column in SEED_KEY if column in persons.columns]]
    frame = students[list(PLAN_STUDENT_COLUMNS)].merge(
        persons[person_columns], on="person_id", how="left", validate="1:1")
    for column in PLAN_PERSON_COLUMNS[1:]:
        frame[column] = _integer_key(frame[column], "persons", column)
    own = ((frame["source_H_ID"] == frame["attribute_source_H_ID"])
           & (frame["source_P_ID"] == frame["attribute_source_P_ID"])).to_numpy()
    frame["plan_origin"] = np.where(own, PLAN_ORIGIN_OWN, PLAN_ORIGIN_REDRAWN)
    frame["donor_status"], donor = _donor_status(frame, donor_persons)
    frame["match_path"], unresolved_reason = _match_path(frame, persons, weekend_trace,
                                                         diary_trace)

    redrawn = ~own
    unresolved = (frame["match_path"] == MATCH_PATH_UNRESOLVED).to_numpy()
    n_students, n_redrawn = len(frame), int(redrawn.sum())
    diagnostics = {
        "n_students": n_students, "n_own_diary": n_students - n_redrawn, "n_redrawn": n_redrawn,
        "n_redrawn_donor_not_found": int((redrawn & donor["not_found"]).sum()),
        "n_redrawn_donor_p_taet_unreadable": int((redrawn & donor["unreadable"]).sum()),
        "n_redrawn_donor_unknown": int((redrawn & (frame["donor_status"]
                                                   == DONOR_STATUS_UNKNOWN)).sum()),
        "n_own_diary_donor_unknown": int((own & (frame["donor_status"]
                                                 == DONOR_STATUS_UNKNOWN)).sum()),
        "n_unresolved": int(unresolved.sum()),
        "n_redrawn_unresolved": int((redrawn & unresolved).sum()),
        "seed_key_present": unresolved_reason is None,
        "unresolved_reason": unresolved_reason,
    }
    (LOG.warning if diagnostics["n_redrawn_unresolved"] else LOG.info)(
        "%s plan source, match path of the re-drawn plans: primary %d/%d (%.1f%%), unresolved "
        "%d (%.1f%%)%s", _LOG_TAG, n_redrawn - diagnostics["n_redrawn_unresolved"], n_redrawn,
        _rate(n_redrawn - diagnostics["n_redrawn_unresolved"], n_redrawn),
        diagnostics["n_redrawn_unresolved"], _rate(diagnostics["n_redrawn_unresolved"],
                                                   n_redrawn),
        "; %s" % unresolved_reason if unresolved_reason else "")
    if diagnostics["n_own_diary_donor_unknown"]:
        LOG.warning("%s plan source: %d own-diary student(s) whose own seed person is not in "
                    "the donor persons or has no readable P_TAET", _LOG_TAG,
                    diagnostics["n_own_diary_donor_unknown"])
    _log_exclusion("plan source, donor of the re-drawn plans found with a readable P_TAET "
                   "(not found %d, unreadable %d)"
                   % (diagnostics["n_redrawn_donor_not_found"],
                      diagnostics["n_redrawn_donor_p_taet_unreadable"]),
                   diagnostics["n_redrawn_donor_unknown"], n_redrawn)

    frame["education"] = np.where(present, frame["education_leg"] == "yes", np.nan)
    frame["work"] = np.where(present, frame["work_leg"] == "yes", np.nan)
    keys = ["plan_origin", "match_path", "donor_status"]
    grouped = frame.groupby(keys)
    table = pd.DataFrame({"model_n": grouped.size(),
                          "share_with_education_leg": grouped["education"].mean(),
                          "share_with_work_leg": grouped["work"].mean()}).reset_index()
    table["share_of_students"] = table["model_n"] / n_students
    return table.sort_values(keys)[PLAN_SOURCE_COLUMNS].reset_index(drop=True), diagnostics
