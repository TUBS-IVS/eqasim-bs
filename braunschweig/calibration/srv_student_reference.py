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
* :func:`build_education_distance_table` -- home-to-education distance bands of students per
  home Kreis, with a bootstrap noise floor of the band EMD;
* :func:`build_behaviour_table` -- the candidate behaviour cells of
  :mod:`braunschweig.calibration.student_behaviour_labels`, in the adults and in the students
  universe;
* :func:`build_comparison_table` -- car availability, licence and bicycle availability of
  students against the other adults, each code as delivered.

Definitions (SrV codebook ``SrV2023_Datenkodierung_SciUse.xlsx``):

* Universe: :func:`srv_participation_universe.prepare_universe_persons` (valid
  ``GEWICHT_P_ZENSUS``, persons at home at some point on the reporting day, household Kreis in
  the ZGB; its guards raise on any universe drift). Weight ``GEWICHT_P_ZENSUS`` (ADR-0055).
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
  ``GEWICHT_W_ZENSUS`` as that function returns it), restricted to students by
  :func:`select_student_observations`.

Every exclusion is counted in the builder's diagnostics and logged as
``primary n/N (rate), excluded m (rate)``; a step that would exclude more than
:data:`MAX_EXCLUSION_RATE` of its base raises, because that is a broken code mapping, not a
measurement. Shares are NaN, never 0.0, when their base is empty, and empty cells are emitted.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_distance_targets import (ZGB_KREISE,
                                                            bootstrap_emd_noise_floor,
                                                            weighted_band_shares)
from braunschweig.calibration.srv_participation_universe import (
    EDUCATION_E_ZWECK_9, HOUSEHOLD_COLUMNS, LEVEL_KREIS, LEVEL_TOTAL, REGION_CODE,
    WORK_E_ZWECK_9, persons_with_purpose, prepare_universe_persons)
from braunschweig.calibration.srv_participation_universe import \
    PERSON_COLUMNS as UNIVERSE_PERSON_COLUMNS
# The person key and the NaN-for-empty share rule are taken from the sibling builder rather than
# re-implemented, so the key format persons_with_purpose returns cannot drift from ours.
from braunschweig.calibration.srv_participation_universe import _person_ids, _weighted_share
from braunschweig.calibration.srv_plan_structure import EMPLOYED_V_ERW
from braunschweig.calibration.student_behaviour_labels import (
    ARRIVAL_HOUR_EDGES, BEHAVIOUR_COLUMNS, CONTROLS, MIN_ADULT_AGE, NOT_STUDENT,
    STUDENT_DISTANCE_EDGES_KM, STUDENT_DISTANCE_LABELS, TRIP_CLASS_EDGES, UNIVERSE_ADULTS,
    UNIVERSE_STUDENTS, effective_sample_size, share_standard_error)

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

# ASSUMPTION: an exclusion step that removes more than half of its base is a broken code mapping
# (a renamed column, a recoded variable), not a measurement, so the build raises instead of
# shipping a table built on the remainder. Half is a judgement, not a sourced threshold.
MAX_EXCLUSION_RATE = 0.5

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
    non-negative), ``E_OEV_FK``, ``V_PKW_VERFUEG``, ``V_FUEHR_PKW``, ``V_FK_VERFUEG`` (numeric)
    and ``is_student`` (nullable boolean, ``pd.NA`` where ``V_ERW`` is missing or negative).
    ``diagnostics`` are the universe diagnostics unchanged.

    Raises ``ValueError`` on a duplicated person key or a non-negative ``V_ERW`` outside
    :data:`V_ERW_CODES`.
    """
    _require_columns(persons, PERSON_COLUMNS, "persons")
    universe, diagnostics = prepare_universe_persons(persons, households)

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
    """Keep the :func:`srv_distance_targets.select_person_observations` rows of students.

    A row whose person is not in ``universe`` or whose ``V_ERW`` is unreadable is excluded and
    counted; a readable non-student is simply not a student and is not an exclusion.
    """
    _require_columns(observations, ["hhnr", "pnr"], "observations")
    pid = observations["hhnr"].astype(str) + "_" + observations["pnr"].astype(str)
    status = universe.set_index("pid")["is_student"]
    in_universe = pid.isin(status.index).values
    mapped = pid.map(status)
    unreadable = in_universe & mapped.isna().values
    is_student = mapped.fillna(False).astype(bool).values
    n_total = len(observations)
    n_not_in_universe = int((~in_universe).sum())
    n_unreadable = int(unreadable.sum())
    _log_exclusion("education distance observations in the universe with a readable V_ERW",
                   n_not_in_universe + n_unreadable, n_total)
    kept = observations[is_student].copy()
    diagnostics = {"n_observations_total": int(n_total),
                   "n_observations_not_in_universe": n_not_in_universe,
                   "n_observations_unreadable_employment_code": n_unreadable,
                   "n_observations_students": int(len(kept))}
    return kept, diagnostics


def build_education_distance_table(observations: pd.DataFrame, n_bootstrap: int,
                                   seed: int) -> pd.DataFrame:
    """Students' home-to-education distance band shares per Kreis and region.

    ``observations``: student rows of :func:`srv_distance_targets.select_person_observations`
    (see :func:`select_student_observations`), with ``kreis``, ``distance_km`` and ``weight``.
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
        rows += _behaviour_rows(control, UNIVERSE_STUDENTS, student_labels,
                                base["weight"].to_numpy(dtype=float), base_labels)
        adult_weight = np.concatenate([non_students["weight"].to_numpy(dtype=float),
                                       base["weight"].to_numpy(dtype=float)])
        adult_labels = np.concatenate([np.full(len(non_students), NOT_STUDENT, dtype=object),
                                       base_labels.astype(object)])
        rows += _behaviour_rows(control, UNIVERSE_ADULTS, (*student_labels, NOT_STUDENT),
                                adult_weight, adult_labels)
    table = pd.DataFrame(rows, columns=BEHAVIOUR_COLUMNS)
    diagnostics.update(n_adults=int(len(adults)), n_students=int(len(students)),
                       **arrival_diagnostics, **season_diagnostics, **leg_diagnostics)
    logger.info("%s behaviour table: %d rows (%d controls x 2 universes); %d adults, %d adult "
                "students", _LOG_TAG, len(table), len(CONTROLS), len(adults), len(students))
    return table, diagnostics


def _behaviour_rows(control: str, universe_name: str, label_set, weight: np.ndarray,
                    labels: np.ndarray) -> list:
    total = weight.sum()
    n_effective = effective_sample_size(weight)
    rows = []
    for label in label_set:
        mask = labels == label
        weight_sum = float(weight[mask].sum())
        share = weight_sum / total if total > 0 else float("nan")
        rows.append({"control": control, "universe": universe_name, "label": label,
                     "n_unweighted": int(mask.sum()), "weight_sum": weight_sum, "share": share,
                     "n_effective": n_effective,
                     "se_share": share_standard_error(share, n_effective)})
    return rows


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
    _log_exclusion("arrival control (readable start and arrival hour of the first education "
                   "leg; start unreadable %d, arrival unreadable %d)" % (n_start, n_arrival),
                   n_start + n_arrival, len(students))
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
    """Car availability, licence and bicycle availability: adult students vs other adults.

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
