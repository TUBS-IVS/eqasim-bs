"""Decision tables of the student controls (issue #140, Phase 0): build rule and level gate.

Pure builders over the already-written reference tables (no I/O, no synpp).
``scripts/build_student_control_evidence.py`` is the only caller; it reads the tables and owns the
CLI and the provenance headers.

* :func:`evaluate_build_rule` -- for every candidate behaviour control of
  :data:`student_behaviour_labels.CONTROLS`, the regional survey (SrV, R5
  ``srv2023_student_behaviour.csv``) against the national survey that seeds the model (MiD, R4
  ``mid2023_student_behaviour_seed.csv``), and whether the control is built or stays a comparison
  table.
* :func:`build_level_gate` -- per ZGB Kreis and for the pooled area SrV covers, the estimates of
  the students aged 20 to 29 living there, side by side, plus the enrollment at local sites as a
  comparison figure (not a bound: residents who study elsewhere or at a distance-learning
  university are survey students but not local enrollment, and local enrollment includes
  in-commuters who live elsewhere).
* :func:`arrival_status_overlap` -- the overlap of the arrival and the status control, read from
  the build-rule table.

Build rule (fixed before the measurement in the design of this work, issue #140, to be recorded
in an ADR in the next phase; both thresholds are ASSUMPTIONS):

1. every SrV cell of the control holds at least :data:`MIN_RESPONDENTS_PER_CELL` respondents --
   the unweighted count of each student label in the ``adults_18plus`` universe, the universe of
   the control's cells;
2. in at least one cell the MiD share differs from the SrV share by more than
   :data:`MIN_DIFFERENCE_STANDARD_ERRORS` standard errors of the SrV share. Both shares are taken
   WITHIN the students (:data:`RULE_UNIVERSE`): the student LEVEL is the job of the status
   control, and comparing shares of all adults would make every candidate differ through that
   level alone. The standard error is ``sqrt(share * (1 - share) / n_effective)`` with the Kish
   effective sample size of exactly that students universe (the ``n_effective`` of the SrV row in
   :data:`RULE_UNIVERSE`), recomputed here from the share and that size so that the two
   columns written next to each other cannot disagree;
3. the one-batch smoke of Phase 2 keeps the fit of the hard controls (``rule3_smoke`` =
   ``pending_phase2``; not decidable in Phase 0).

The verdict of a control is ``build_candidate`` when all its labels pass rule 1 and at least one
passes rule 2, else ``comparison_only``. ``status_x_education_leg`` is evaluated the same way at
region level; its Kreis pattern is not part of this table. :data:`COMPARISON_ONLY` variables are
comparison tables by decision (the model derives availability itself) and are never evaluated.

Overlap: the arrival cell ``student_no_education_leg`` and the status cell ``student_noedu`` are
both defined as the students without an education leg on the reporting day, so the two controls
must never be built together. The two CONTROL bases differ slightly (the arrival base leaves out
students with an unreadable start or arrival hour of their education leg), so the shares and
differences of the two cells are nearly, not exactly, equal; :func:`arrival_status_overlap`
reports both cells' unweighted counts and through which cells arrival passes rule 2.

The MiD bases of the four diary-dependent controls are reduced (persons without a diary of the
day are absent, see :mod:`braunschweig.calibration.mid_student_reference`), so the table carries
the MiD unweighted count of each cell next to the SrV one.

Level gate: the student estimates the owner decides the level on. ``mid_to_census_ratio_germany``
= MiD share of students (``P_TAET`` 10) among the 20 to 29 year olds / census share of the group
"pupils and students without paid work" among the 20 to 29 year olds, both for Germany; the census
group times that ratio is the second estimate. The same ratio for Land 03 (Niedersachsen) is a
sensitivity. The third estimate, the residents implied by enrollment, comes from the student
balance (``students_living_persons``) and is passed in as ``residents_implied``; it stays NaN
when none is passed (the Phase 0 call). The enrollment at the local sites is a comparison figure
only (where students study, all ages, its own semester).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping

import numpy as np
import pandas as pd

from braunschweig.calibration.mid_student_reference import REGION_GERMANY as MID_GERMANY_CODE
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.calibration.srv_participation_universe import REGION_CODE, UNSURVEYED_KREISE
from braunschweig.calibration.srv_student_reference import AREA_BRAUNSCHWEIG, AREA_SURROUNDING
from braunschweig.calibration.student_parameters import BASE_PRESENT, BASE_STUDENTS_ALL
from braunschweig.calibration.student_behaviour_labels import (
    BEHAVIOUR_COLUMNS, CONTROLS, MAX_EXCLUSION_RATE, NOT_STUDENT, UNIVERSE_ADULTS,
    UNIVERSE_STUDENTS, share_standard_error)

logger = logging.getLogger(__name__)

_LOG_TAG = "[student control evidence]"

# ASSUMPTION (design of issue #140, fixed before the measurement): a cell with fewer respondents
# is too thin for a regional target. A judgement, not a sourced threshold.
MIN_RESPONDENTS_PER_CELL = 30
# ASSUMPTION (design of issue #140): a MiD-SrV difference above two standard errors of the SrV
# share is taken as real, i.e. as something a control would steer. A judgement, not a sourced
# threshold.
MIN_DIFFERENCE_STANDARD_ERRORS = 2.0
RULE_UNIVERSE = UNIVERSE_STUDENTS

# Area rule (design A3 rule 2 of the student synthesis, issue #140): a parameter gets a value of
# its own for Braunschweig and the surrounding Kreise only when the two areas differ clearly AND
# both rest on enough respondents. ASSUMPTION thresholds (A20 of the design), judgements fixed
# before the measurement, not sourced values: at least this many respondents in the base of each
# area's share (the students present; all students for away_all_day), and at least this many
# with and without the label on either side. The difference limit is
# MIN_DIFFERENCE_STANDARD_ERRORS above.
MIN_RESPONDENTS_PER_AREA = 100
MIN_RESPONDENTS_EACH_SIDE = 10
AREA_RULE_CELLS = (("education_leg", BASE_PRESENT, "yes"), ("work_leg", BASE_PRESENT, "yes"),
                   ("season_ticket", BASE_PRESENT, "flatrate"),
                   ("away_all_day", BASE_STUDENTS_ALL, "yes"))
AREA_RULE_TABLE = "srv2023_student_area_rule.csv"
_AREAS = (AREA_BRAUNSCHWEIG, AREA_SURROUNDING)
AREA_RULE_COLUMNS = (["parameter", "base", "label"]
                     + ["%s_%s" % (key, area) for area in _AREAS
                        for key in ("n_base", "n_label", "n_other", "share", "se")]
                     + ["difference_in_se", "enough_respondents", "area_value"])
# Comparison tables by decision of issue #140 (the model derives availability itself).
COMPARISON_ONLY = ("car_availability", "driving_licence")
# Their SrV codebook variables in srv2023_student_comparison.csv (srv_student_reference.
# COMPARISON_VARIABLES, which also carries season-ticket availability V_FK_VERFUEG).
COMPARISON_ONLY_SRV_VARIABLES = {"car_availability": "V_PKW_VERFUEG",
                                 "driving_licence": "V_FUEHR_PKW"}
# The two cells that hold the same students (no education leg on the reporting day).
STATUS_CONTROL, STATUS_NO_EDUCATION_LABEL = "status_x_education_leg", "student_noedu"
ARRIVAL_CONTROL, ARRIVAL_NO_EDUCATION_LABEL = "arrival", "student_no_education_leg"

VERDICT_BUILD = "build_candidate"
VERDICT_COMPARISON = "comparison_only"
RULE3_PENDING = "pending_phase2"

BUILD_RULE_COLUMNS = ["control", "label", "srv_n_unweighted_adults", "mid_n_unweighted_adults",
                      "srv_share_students", "mid_share_students", "srv_n_effective_students",
                      "srv_se_share_students", "difference_in_se", "rule1_cell_size_ok",
                      "rule2_differs", "rule3_smoke", "verdict"]

# Census table (scripts/extract_zensus2022_students_by_age.py, whose GERMANY_CODE and AGE_GROUP a
# test pins these to): Kreise five digits, Land two digits, Germany "DG".
CENSUS_GERMANY_CODE = "DG"
CENSUS_AGE_GROUP = "20-29"
CENSUS_COLUMNS = ["region", "age_group", "students_nonemployed_persons", "all_persons"]
# Land of the sensitivity ratio (Niedersachsen): the same two-digit code in MiD and the census.
SENSITIVITY_LAND = "03"
MID_SHARE_COLUMNS = ["region", "share_students"]
SRV_SHARE_COLUMNS = ["code", "n_adults", "n_students_20_29", "student_share_20_29",
                     "se_student_share_20_29", "students_weighted_persons",
                     "student_share_adults"]
SITE_COLUMNS = ["site_kreis_ars5", "scope", "students_persons"]
SCOPE_LOCAL = "local"
SITE_SCOPES = frozenset({SCOPE_LOCAL, "surrounding"})

LEVEL_GATE_COLUMNS = [
    "code", "srv_n_students_20_29", "srv_student_share_20_29", "srv_se_student_share_20_29",
    "census_population_20_29_persons", "census_students_nonemployed_20_29_persons",
    "srv_estimate_students_20_29_persons", "mid_to_census_ratio_germany",
    "census_estimate_students_20_29_persons", "mid_to_census_ratio_land_03",
    "census_estimate_land_ratio_students_20_29_persons", "srv_students_weighted_persons",
    "srv_student_share_adults", "enrollment_local_sites_persons",
    "residents_implied_by_enrollment_persons"]


# --------------------------------------------------------------------------- helpers
def _require_columns(frame: pd.DataFrame, required, name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError("%s table is missing required column(s) %s" % (name, missing))


def _unique_index(frame: pd.DataFrame, key, name: str) -> pd.DataFrame:
    duplicated = frame[frame.duplicated(key, keep=False)]
    if len(duplicated):
        raise ValueError("%s table: duplicated key %s for %s" % (
            name, key, sorted(set(map(tuple, duplicated[key].astype(str).values)))[:10]))
    return frame.set_index(key)


# --------------------------------------------------------------------------- build rule
def _behaviour_keys(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """The behaviour table indexed by (control, universe, label), vocabulary-checked."""
    _require_columns(frame, BEHAVIOUR_COLUMNS, name)
    indexed = _unique_index(frame, ["control", "universe", "label"], name)
    expected = set()
    for control, labels in CONTROLS.items():
        expected |= {(control, UNIVERSE_STUDENTS, label) for label in labels}
        expected |= {(control, UNIVERSE_ADULTS, label) for label in (*labels, NOT_STUDENT)}
    present = set(indexed.index)
    if present != expected:
        raise ValueError(
            "%s table: (control, universe, label) keys differ from the vocabulary of "
            "student_behaviour_labels.CONTROLS; unexpected %s, missing %s"
            % (name, sorted(present - expected), sorted(expected - present)))
    return indexed


def evaluate_build_rule(srv_behaviour: pd.DataFrame, mid_behaviour: pd.DataFrame) -> pd.DataFrame:
    """One row per control x student label with both rules and the control's verdict.

    ``srv_behaviour`` / ``mid_behaviour``: tables with :data:`student_behaviour_labels.
    BEHAVIOUR_COLUMNS`, every control of ``CONTROLS`` in both universes. Columns
    :data:`BUILD_RULE_COLUMNS`. ``difference_in_se`` is signed, ``(SrV share - MiD share) /
    srv_se_share_students``, NaN where that standard error is NaN or zero (an empty or one-label
    base, which rule 1 already rejects); ``rule2_differs`` is ``|difference_in_se| >``
    :data:`MIN_DIFFERENCE_STANDARD_ERRORS` and False for NaN (also when the MiD share is NaN).
    Those not-evaluable cells are counted (:func:`build_rule_exclusions`) and logged as primary
    against excluded. Raises ``ValueError`` when a label is present in one survey only (naming
    it), on a duplicated key, on any key outside the vocabulary, or when more than
    :data:`MAX_EXCLUSION_RATE` of the cells are not evaluable.
    """
    _require_columns(srv_behaviour, BEHAVIOUR_COLUMNS, "SrV behaviour")
    _require_columns(mid_behaviour, BEHAVIOUR_COLUMNS, "MiD behaviour")
    srv_keys = set(map(tuple, srv_behaviour[["control", "universe", "label"]].values))
    mid_keys = set(map(tuple, mid_behaviour[["control", "universe", "label"]].values))
    if srv_keys != mid_keys:
        raise ValueError("the two surveys carry different behaviour cells: SrV only %s, MiD only %s"
                         % (sorted(srv_keys - mid_keys), sorted(mid_keys - srv_keys)))
    srv = _behaviour_keys(srv_behaviour, "SrV behaviour")
    mid = _behaviour_keys(mid_behaviour, "MiD behaviour")

    rows = []
    for control, labels in CONTROLS.items():
        control_rows = []
        for label in labels:
            srv_students = srv.loc[(control, RULE_UNIVERSE, label)]
            srv_share = float(srv_students["share"])
            mid_share = float(mid.loc[(control, RULE_UNIVERSE, label), "share"])
            n_effective = float(srv_students["n_effective"])
            se = share_standard_error(srv_share, n_effective)
            difference = ((srv_share - mid_share) / se if se > 0 and not np.isnan(mid_share)
                          else float("nan"))
            n_srv = int(srv.loc[(control, UNIVERSE_ADULTS, label), "n_unweighted"])
            control_rows.append({
                "control": control, "label": label, "srv_n_unweighted_adults": n_srv,
                "mid_n_unweighted_adults": int(mid.loc[(control, UNIVERSE_ADULTS, label),
                                                       "n_unweighted"]),
                "srv_share_students": srv_share, "mid_share_students": mid_share,
                "srv_n_effective_students": n_effective, "srv_se_share_students": se,
                "difference_in_se": difference,
                "rule1_cell_size_ok": n_srv >= MIN_RESPONDENTS_PER_CELL,
                "rule2_differs": bool(abs(difference) > MIN_DIFFERENCE_STANDARD_ERRORS),
                "rule3_smoke": RULE3_PENDING})
        build = (all(row["rule1_cell_size_ok"] for row in control_rows)
                 and any(row["rule2_differs"] for row in control_rows))
        verdict = VERDICT_BUILD if build else VERDICT_COMPARISON
        for row in control_rows:
            row["verdict"] = verdict
        logger.info("%s %s: rule 1 (>= %d respondents) %d/%d labels, rule 2 (> %.1f se) %d/%d "
                    "labels -> %s", _LOG_TAG, control, MIN_RESPONDENTS_PER_CELL,
                    sum(row["rule1_cell_size_ok"] for row in control_rows), len(control_rows),
                    MIN_DIFFERENCE_STANDARD_ERRORS,
                    sum(row["rule2_differs"] for row in control_rows), len(control_rows), verdict)
        rows += control_rows
    table = pd.DataFrame(rows, columns=BUILD_RULE_COLUMNS)
    exclusions = build_rule_exclusions(table)
    n_cells, n_excluded = exclusions["n_cells"], exclusions["n_cells_rule2_not_evaluable"]
    rate = n_excluded / n_cells if n_cells else float("nan")
    logger.info("%s rule 2 evaluable: primary %d/%d (%.1f%%), excluded %d (%.1f%%; MiD share NaN "
                "%d, SrV standard error NaN or 0 %d); an excluded cell counts as 'does not differ'",
                _LOG_TAG, n_cells - n_excluded, n_cells, 100.0 * (1.0 - rate), n_excluded,
                100.0 * rate, exclusions["n_cells_mid_share_nan"],
                exclusions["n_cells_srv_se_undefined"])
    if n_cells and rate > MAX_EXCLUSION_RATE:
        raise ValueError("%s rule 2 cannot be evaluated for %d of %d cells (%.1f%%), above the "
                         "%.0f%% limit: an empty survey base, not a measurement (%s)"
                         % (_LOG_TAG, n_excluded, n_cells, 100.0 * rate,
                            100.0 * MAX_EXCLUSION_RATE, exclusions))
    return table


def build_rule_exclusions(rule: pd.DataFrame) -> dict:
    """Counts of the cells of a :func:`evaluate_build_rule` table where rule 2 is not evaluable.

    ``n_cells_mid_share_nan``: the MiD share is NaN (empty MiD base of the control);
    ``n_cells_srv_se_undefined``: the SrV standard error is NaN or 0 (empty or one-label base);
    ``n_cells_rule2_not_evaluable``: either (``difference_in_se`` NaN), counted as "does not
    differ". The CLI writes these into the ``# Exclusions:`` header line.
    """
    se = rule["srv_se_share_students"]
    return {"n_cells": int(len(rule)),
            "n_cells_rule2_not_evaluable": int(rule["difference_in_se"].isna().sum()),
            "n_cells_mid_share_nan": int(rule["mid_share_students"].isna().sum()),
            "n_cells_srv_se_undefined": int((se.isna() | (se == 0)).sum())}


def arrival_status_overlap(rule: pd.DataFrame) -> dict:
    """The overlap of the arrival and the status control in a :func:`evaluate_build_rule` table.

    :data:`ARRIVAL_NO_EDUCATION_LABEL` of the arrival control and :data:`STATUS_NO_EDUCATION_LABEL`
    of the status control share one definition (no education leg on the reporting day) on
    slightly different control bases, so an arrival verdict carried by that cell alone nearly
    repeats the status difference. Returns ``status_verdict``, ``arrival_verdict``,
    ``status_noedu_difference_in_se``, ``arrival_no_education_leg_difference_in_se``,
    ``n_unweighted_srv`` / ``n_unweighted_mid`` (``{"status": .., "arrival": ..}``, the unweighted
    counts of the two cells in the adults universe, from ``srv_n_unweighted_adults`` /
    ``mid_n_unweighted_adults``) and ``arrival_time_labels_passing_rule2`` (the arrival-time
    labels with ``rule2_differs``, in vocabulary order; empty when arrival passes rule 2 only
    through the shared cell or not at all). Raises ``ValueError`` when either control or cell is
    missing.
    """
    _require_columns(rule, BUILD_RULE_COLUMNS, "build rule")
    indexed = _unique_index(rule, ["control", "label"], "build rule")
    needed = [(STATUS_CONTROL, STATUS_NO_EDUCATION_LABEL),
              *((ARRIVAL_CONTROL, label) for label in CONTROLS[ARRIVAL_CONTROL])]
    missing = [key for key in needed if key not in indexed.index]
    if missing:
        raise ValueError("build rule table: cells %s missing" % missing)
    time_labels = [label for label in CONTROLS[ARRIVAL_CONTROL]
                   if label != ARRIVAL_NO_EDUCATION_LABEL]
    status_key = (STATUS_CONTROL, STATUS_NO_EDUCATION_LABEL)
    arrival_key = (ARRIVAL_CONTROL, ARRIVAL_NO_EDUCATION_LABEL)
    return {
        "n_unweighted_srv": {"status": int(indexed.loc[status_key, "srv_n_unweighted_adults"]),
                             "arrival": int(indexed.loc[arrival_key,
                                                        "srv_n_unweighted_adults"])},
        "n_unweighted_mid": {"status": int(indexed.loc[status_key, "mid_n_unweighted_adults"]),
                             "arrival": int(indexed.loc[arrival_key,
                                                        "mid_n_unweighted_adults"])},
        "status_verdict": str(indexed.loc[(STATUS_CONTROL, STATUS_NO_EDUCATION_LABEL), "verdict"]),
        "arrival_verdict": str(indexed.loc[(ARRIVAL_CONTROL, ARRIVAL_NO_EDUCATION_LABEL),
                                           "verdict"]),
        "status_noedu_difference_in_se": float(
            indexed.loc[(STATUS_CONTROL, STATUS_NO_EDUCATION_LABEL), "difference_in_se"]),
        "arrival_no_education_leg_difference_in_se": float(
            indexed.loc[(ARRIVAL_CONTROL, ARRIVAL_NO_EDUCATION_LABEL), "difference_in_se"]),
        "arrival_time_labels_passing_rule2": [
            label for label in time_labels
            if bool(indexed.loc[(ARRIVAL_CONTROL, label), "rule2_differs"])],
    }


# --------------------------------------------------------------------------- level gate
def _require_regions(indexed: pd.DataFrame, regions, name: str) -> None:
    missing = [region for region in regions if region not in indexed.index]
    if missing:
        raise ValueError("%s table: region(s) %s missing" % (name, missing))


def _census_share(census: pd.DataFrame, region: str) -> float:
    row = census.loc[region]
    persons = float(row["all_persons"])
    return float(row["students_nonemployed_persons"]) / persons if persons > 0 else float("nan")


def _ratio(mid: pd.DataFrame, census: pd.DataFrame, mid_region: str, census_region: str) -> float:
    mid_share = float(mid.loc[mid_region, "share_students"])
    census_share = _census_share(census, census_region)
    if not (mid_share > 0 and census_share > 0):
        raise ValueError("MiD-to-census ratio for %s / %s is undefined: MiD share_students %r, "
                         "census share %r" % (mid_region, census_region, mid_share, census_share))
    return mid_share / census_share


def _checked_residents_implied(residents_implied: Mapping[str, float]) -> dict:
    """``residents_implied`` as a dict, after the checks that fail early (naming Kreis and value)."""
    values = dict(residents_implied)
    missing = [code for code in ZGB_KREISE if code not in values]
    unknown = sorted(set(values) - set(ZGB_KREISE))
    if missing or unknown:
        raise ValueError("residents_implied: Kreis(e) %s missing, %s outside the ZGB %s"
                         % (missing, unknown, list(ZGB_KREISE)))
    bad = {code: values[code] for code in ZGB_KREISE
           if not (np.isfinite(values[code]) and values[code] >= 0)}
    if bad:
        raise ValueError("residents_implied: non-finite or negative value(s) %s" % bad)
    return {code: float(values[code]) for code in ZGB_KREISE}


def build_level_gate(srv_shares: pd.DataFrame, census_students: pd.DataFrame,
                     mid_share_20_29: pd.DataFrame, sites: pd.DataFrame,
                     residents_implied: Mapping[str, float] | None = None) -> pd.DataFrame:
    """Student estimates per ZGB Kreis and for the pooled area SrV covers.

    Inputs: ``srv_shares`` = R5 ``srv2023_student_share_by_kreis.csv`` (``code`` per ZGB Kreis and
    :data:`srv_participation_universe.REGION_CODE`); ``census_students`` = R3 (``region`` per
    Kreis, :data:`SENSITIVITY_LAND` and :data:`CENSUS_GERMANY_CODE`, ``age_group``
    :data:`CENSUS_AGE_GROUP`); ``mid_share_20_29`` = R4 ``mid2023_student_share_20_29_by_land.csv``
    (``region`` :data:`mid_student_reference.REGION_GERMANY` and two-digit Laender); ``sites`` =
    R1 (``site_kreis_ars5``, ``scope``, ``students_persons``). All codes are strings.

    Rows: one per :data:`srv_distance_targets.ZGB_KREISE`, then the pooled covered area coded
    :data:`REGION_CODE` = the ZGB Kreise minus
    :data:`srv_participation_universe.UNSURVEYED_KREISE`, the Kreise the SrV region row covers.
    Every column of that row (census counts, estimates, enrollment) is summed or applied over
    exactly those Kreise, so all estimates in it describe the same area. An unsurveyed Kreis
    (Wolfsburg) keeps its row with NaN SrV shares and estimates; its census estimate and
    enrollment exist. Columns :data:`LEVEL_GATE_COLUMNS`; ``enrollment_local_sites_persons`` is
    the sum over ``scope == local`` sites in the Kreis, 0 where there is none;
    ``residents_implied_by_enrollment_persons`` is NaN when ``residents_implied`` is None (the
    Phase 0 call; its output is unchanged). Otherwise ``residents_implied`` maps every ZGB Kreis to
    the students living in it (``by_home_kreis`` of :mod:`braunschweig.data.education.
    student_od_table`, ``students_living_persons``): the column holds each Kreis's value and the
    pooled row the sum over the covered Kreise only (Wolfsburg's value stays in its own row).

    Raises ``ValueError`` on a missing column or region, a duplicated code, a census row of
    another age group, a surveyed Kreis without SrV adults, an unsurveyed Kreis with SrV adults,
    an undefined MiD-to-census ratio, a site scope outside :data:`SITE_SCOPES`, a negative count
    or a local site outside the ZGB, and, for ``residents_implied``, on a missing or unknown Kreis
    or a non-finite or negative value.
    """
    _require_columns(srv_shares, SRV_SHARE_COLUMNS, "SrV student share")
    _require_columns(census_students, CENSUS_COLUMNS, "census students")
    _require_columns(mid_share_20_29, MID_SHARE_COLUMNS, "MiD share 20-29")
    _require_columns(sites, SITE_COLUMNS, "sites")
    srv = _unique_index(srv_shares, "code", "SrV student share")
    census = _unique_index(census_students, "region", "census students")
    mid = _unique_index(mid_share_20_29, "region", "MiD share 20-29")
    _require_regions(srv, [*ZGB_KREISE, REGION_CODE], "SrV student share")
    _require_regions(census, [*ZGB_KREISE, SENSITIVITY_LAND, CENSUS_GERMANY_CODE],
                     "census students")
    _require_regions(mid, [MID_GERMANY_CODE, SENSITIVITY_LAND], "MiD share 20-29")
    used_census = census.loc[[*ZGB_KREISE, SENSITIVITY_LAND, CENSUS_GERMANY_CODE]]
    wrong_age = used_census[used_census["age_group"] != CENSUS_AGE_GROUP]
    if len(wrong_age):
        raise ValueError("census students table: region(s) %s carry age_group %s, expected %r"
                         % (list(wrong_age.index), sorted(set(wrong_age["age_group"])),
                            CENSUS_AGE_GROUP))

    covered = [code for code in ZGB_KREISE if code not in UNSURVEYED_KREISE]
    empty_covered = [code for code in covered if not srv.loc[code, "n_adults"] > 0]
    filled_unsurveyed = [code for code in UNSURVEYED_KREISE if srv.loc[code, "n_adults"] > 0]
    if empty_covered or filled_unsurveyed:
        raise ValueError("SrV student share table: surveyed Kreise without adults %s, unsurveyed "
                         "Kreise %s with adults %s; the pooled area would not match the SrV "
                         "region row" % (empty_covered, list(UNSURVEYED_KREISE),
                                         filled_unsurveyed))

    scopes = set(sites["scope"]) - SITE_SCOPES
    if scopes:
        raise ValueError("sites table: column scope carries value(s) %s outside %s"
                         % (sorted(scopes), sorted(SITE_SCOPES)))
    students_persons = pd.to_numeric(sites["students_persons"], errors="coerce")
    if students_persons.isna().any() or (students_persons < 0).any():
        raise ValueError("sites table: column students_persons has missing, non-numeric or "
                         "negative value(s)")
    local = sites.assign(students_persons=students_persons)[sites["scope"] == SCOPE_LOCAL]
    foreign = sorted(set(local["site_kreis_ars5"]) - set(ZGB_KREISE))
    if foreign:
        raise ValueError("sites table: local site(s) in Kreis %s outside the ZGB %s"
                         % (foreign, list(ZGB_KREISE)))
    enrollment = local.groupby("site_kreis_ars5")["students_persons"].sum()

    ratio_germany = _ratio(mid, census, MID_GERMANY_CODE, CENSUS_GERMANY_CODE)
    ratio_land = _ratio(mid, census, SENSITIVITY_LAND, SENSITIVITY_LAND)
    implied = (None if residents_implied is None
               else _checked_residents_implied(residents_implied))

    def _row(code: str, srv_row: pd.Series, kreise) -> dict:
        population = float(census.loc[list(kreise), "all_persons"].sum())
        group = float(census.loc[list(kreise), "students_nonemployed_persons"].sum())
        share_20_29 = float(srv_row["student_share_20_29"])
        return {
            "code": code, "srv_n_students_20_29": int(srv_row["n_students_20_29"]),
            "srv_student_share_20_29": share_20_29,
            "srv_se_student_share_20_29": float(srv_row["se_student_share_20_29"]),
            "census_population_20_29_persons": population,
            "census_students_nonemployed_20_29_persons": group,
            "srv_estimate_students_20_29_persons": share_20_29 * population,
            "mid_to_census_ratio_germany": ratio_germany,
            "census_estimate_students_20_29_persons": group * ratio_germany,
            "mid_to_census_ratio_land_03": ratio_land,
            "census_estimate_land_ratio_students_20_29_persons": group * ratio_land,
            "srv_students_weighted_persons": float(srv_row["students_weighted_persons"]),
            "srv_student_share_adults": float(srv_row["student_share_adults"]),
            "enrollment_local_sites_persons": float(sum(enrollment.get(kreis, 0)
                                                        for kreis in kreise)),
            "residents_implied_by_enrollment_persons": (
                float("nan") if implied is None else float(sum(implied[kreis] for kreis in kreise))),
        }

    rows = [_row(code, srv.loc[code], [code]) for code in ZGB_KREISE]
    rows.append(_row(REGION_CODE, srv.loc[REGION_CODE], covered))
    gate = pd.DataFrame(rows, columns=LEVEL_GATE_COLUMNS)

    with_site = [code for code in ZGB_KREISE if code in enrollment.index]
    logger.info("%s level gate: enrollment from local sites in %d/%d Kreise, %d without a local "
                "site set to 0 persons %s", _LOG_TAG, len(with_site), len(ZGB_KREISE),
                len(ZGB_KREISE) - len(with_site), sorted(set(ZGB_KREISE) - set(with_site)))
    logger.info("%s level gate: SrV estimate in %d/%d Kreise, NaN for the unsurveyed %s; pooled "
                "row %s over %s; MiD-to-census ratio Germany %.4f, Land %s %.4f", _LOG_TAG,
                len(covered), len(ZGB_KREISE), list(UNSURVEYED_KREISE), REGION_CODE, covered,
                ratio_germany, SENSITIVITY_LAND, ratio_land)
    return gate


def area_rule_table(by_area: pd.DataFrame, cells=AREA_RULE_CELLS) -> pd.DataFrame:
    """Design A3 rule 2: which parameters get a value of their own per home area.

    ``by_area`` is the table of :func:`srv_student_reference.build_parameter_table_by_area`
    (columns ``area, parameter, base, label, n_unweighted, share, se_share``). One row per cell
    ``(parameter, label)`` of ``cells`` on its base, columns :data:`AREA_RULE_COLUMNS`.
    ``n_base_<area>`` is the sum of ``n_unweighted`` over the labels of the cell's (parameter,
    base) in that area, ``n_label_<area>`` the respondents with the label, ``n_other_<area>`` the
    rest (base minus label). ``difference_in_se = (share_braunschweig - share_surrounding) /
    sqrt(se_braunschweig^2 + se_surrounding^2)``, NaN when that denominator is not positive
    (such a cell is not evaluable and gets no area value, counted and logged).
    ``enough_respondents``: both bases hold at least :data:`MIN_RESPONDENTS_PER_AREA` and the four
    label / other counts at least :data:`MIN_RESPONDENTS_EACH_SIDE`. ``area_value`` =
    ``enough_respondents`` and ``abs(difference_in_se) > MIN_DIFFERENCE_STANDARD_ERRORS``.
    Raises ``ValueError`` when a cell is missing in an area or an area is unexpected.
    """
    needed = ["area", "parameter", "base", "label", "n_unweighted", "share", "se_share"]
    _require_columns(by_area, needed, "by-area parameter table")
    unexpected = sorted(set(by_area["area"]) - set(_AREAS))
    if unexpected:
        raise ValueError("by-area parameter table: area(s) %s outside the surveyed areas %s"
                         % (unexpected, list(_AREAS)))
    rows = []
    for parameter, base, label in cells:
        row = {"parameter": parameter, "base": base, "label": label}
        for area in _AREAS:
            cell = by_area[(by_area["area"] == area) & (by_area["parameter"] == parameter)
                           & (by_area["base"] == base)]
            chosen = cell[cell["label"] == label]
            if len(chosen) != 1:
                raise ValueError("by-area parameter table: expected exactly one row for %s / %s /"
                                 " %s in area %s, found %d" % (parameter, base, label, area,
                                                               len(chosen)))
            n_base = int(cell["n_unweighted"].sum())
            n_label = int(chosen["n_unweighted"].iloc[0])
            row.update({"n_base_%s" % area: n_base, "n_label_%s" % area: n_label,
                        "n_other_%s" % area: n_base - n_label,
                        "share_%s" % area: float(chosen["share"].iloc[0]),
                        "se_%s" % area: float(chosen["se_share"].iloc[0])})
        rows.append(row)
    table = pd.DataFrame(rows)
    first, second = _AREAS
    denominator = np.hypot(table["se_%s" % first], table["se_%s" % second])
    table["difference_in_se"] = np.where(
        denominator > 0, (table["share_%s" % first] - table["share_%s" % second]) / denominator,
        np.nan)
    thin_bases = ((table["n_base_%s" % first] >= MIN_RESPONDENTS_PER_AREA)
                  & (table["n_base_%s" % second] >= MIN_RESPONDENTS_PER_AREA))
    sides = [table["%s_%s" % (key, area)] >= MIN_RESPONDENTS_EACH_SIDE
             for area in _AREAS for key in ("n_label", "n_other")]
    table["enough_respondents"] = thin_bases & sides[0] & sides[1] & sides[2] & sides[3]
    table["area_value"] = (table["enough_respondents"]
                           & (table["difference_in_se"].abs() > MIN_DIFFERENCE_STANDARD_ERRORS))
    table = table[AREA_RULE_COLUMNS]
    n_not_evaluable = int(table["difference_in_se"].isna().sum())
    logger.info("%s area rule: %d cells, enough respondents in %d, area value in %d, %d without a "
                "defined difference (zero standard error, no area value)", _LOG_TAG, len(table),
                int(table["enough_respondents"].sum()), int(table["area_value"].sum()),
                n_not_evaluable)
    if n_not_evaluable:
        logger.warning("%s area rule: %d of %d cells have a zero standard error on both sides and "
                       "are not evaluable", _LOG_TAG, n_not_evaluable, len(table))
    return table
