"""Model side of the student measurement and its comparison with SrV 2023 (#140, Phase 1).

Pure functions over already-loaded frames (no synpp context, no I/O); the synpp analysis stage
of this measurement supplies the frames and writes the tables.

:func:`model_student_frame` puts every adult model student into the cells of
:data:`braunschweig.calibration.student_parameters.PARAMETERS`, and :func:`compare_parameters`
sets those cells against the committed SrV parameter table
(``srv2023_student_parameters.csv``, built by
:func:`braunschweig.calibration.srv_student_reference.build_parameter_table`). A difference is a
measurement in SrV standard errors, not a validation verdict.

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
from braunschweig.calibration.mid_student_reference import (P_TAET_CODES, P_TAET_NO_ANSWER,
                                                            STUDENT_P_TAET)
from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS, ZGB_KREISE
from braunschweig.calibration.srv_student_reference import (AGE_GROUP_EDGES,
                                                            OLDER_PERSON_GAP_YEARS)
from braunschweig.calibration.student_behaviour_labels import MAX_EXCLUSION_RATE, MIN_ADULT_AGE
from braunschweig.calibration.student_control_evidence import (MIN_DIFFERENCE_STANDARD_ERRORS,
                                                               MIN_RESPONDENTS_PER_CELL)
from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES, PT_TICKET_FLATRATE
from braunschweig.popsim.attributes import map_pt_ticket_group
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
    """
    flatrate_types = pd.DataFrame({"pt_subscription_type": sorted(PT_TICKET_FLATRATE)})
    return tuple(sorted(map_pt_ticket_group(flatrate_types)["pt_ticket_group4"].unique()))


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
def _adult_students(persons: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """The adult students of ``persons`` and the counts of the persons who cannot be one."""
    raw = persons["P_TAET"]
    code = pd.to_numeric(raw, errors="coerce")
    non_numeric = raw.notna() & code.isna()
    undocumented = sorted(set(code[code.notna() & ~code.isin(sorted(P_TAET_CODES))
                                   & (code != P_TAET_NO_ANSWER)].tolist()))
    if non_numeric.any() or undocumented:
        raise ValueError("persons: column P_TAET carries code(s) %s outside the documented set "
                         "%s and %d" % (undocumented + raw[non_numeric].astype(str).tolist()[:5],
                                        sorted(P_TAET_CODES), P_TAET_NO_ANSWER))
    unreadable = code.isna() | (code == P_TAET_NO_ANSWER)
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
    _require_columns(persons, MODEL_PERSON_COLUMNS, "persons")
    _require_columns(trips, P.MODEL_TRIP_COLUMNS, "trips")
    _require_columns(homes_with_ars5, ("household_id", P.KREIS_COLUMN), "homes")
    _require_columns(absence, ABSENCE_COLUMNS, "absence")
    if not 0.0 <= float(max_unmatched_home_share) <= 1.0:
        raise ValueError("max_unmatched_home_share must be in [0, 1], got %r"
                         % max_unmatched_home_share)
    _require_unique_person_ids(persons, "persons")
    if persons["household_id"].isna().any():
        raise ValueError("persons: %d row(s) without a household_id"
                         % int(persons["household_id"].isna().sum()))
    _require_unique_person_ids(absence, "absence")
    known = set(persons["person_id"])
    _require_known_person_ids(trips["person_id"], known, "trips")
    _require_known_person_ids(absence["person_id"], known, "absence")
    # absent_person_ids reads every state other than "present" as absent, so a missing or
    # unknown state would silently become an absent student.
    state = absence["day_absence_state"]
    odd_state = state.isna() | ~state.isin(STATES)
    if odd_state.any():
        raise ValueError("absence: column day_absence_state carries %d value(s) outside %s, "
                         "e.g. %s" % (int(odd_state.sum()), list(STATES),
                                      state[odd_state].astype(str).unique()[:5].tolist()))

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


def _model_rows(model_students: pd.DataFrame) -> pd.DataFrame:
    """Model counts and shares per cell over the seven surveyed Kreise (unweighted)."""
    in_scope = model_students[model_students["kreis"].isin(SURVEYED_KREISE).to_numpy()]
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

    out = _grid().merge(_model_rows(model_students.assign(present=present.astype(bool))),
                        on=_KEY, how="left", validate="1:1")
    out = out.merge(srv, on=_KEY, how="left", validate="1:1")
    n_missing_srv = count_missing_srv_cells(out)
    n_unmeasured = int(out["base"].isin(MODEL_UNMEASURED_BASES).sum())
    (LOG.warning if n_missing_srv else LOG.info)(
        "%s cells: %d in the vocabulary; missing from the SrV table %d (NaN SrV columns), "
        "SrV only by design (bases %s) %d", _LOG_TAG, len(out), n_missing_srv,
        list(MODEL_UNMEASURED_BASES), n_unmeasured)

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
