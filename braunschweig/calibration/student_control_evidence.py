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
  bound.

Build rule (design section 6.8, fixed before the measurement; both thresholds are ASSUMPTIONS):

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
comparison tables by design (the model derives availability itself) and are never evaluated.

The MiD bases of the four diary-dependent controls are reduced (persons without a diary of the
day are absent, see :mod:`braunschweig.calibration.mid_student_reference`), so the table carries
the MiD unweighted count of each cell next to the SrV one.

Level gate: the student estimates the owner decides the level on. ``mid_to_census_ratio_germany``
= MiD share of students (``P_TAET`` 10) among the 20 to 29 year olds / census share of the group
"pupils and students without paid work" among the 20 to 29 year olds, both for Germany; the census
group times that ratio is the second estimate. The same ratio for Land 03 (Niedersachsen) is a
sensitivity. The third estimate, the residents implied by enrollment, needs the origin-destination
table of Phase 1 and is NaN here; until then the enrollment at the local sites is the bound.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.calibration.mid_student_reference import REGION_GERMANY as MID_GERMANY_CODE
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.calibration.srv_participation_universe import REGION_CODE, UNSURVEYED_KREISE
from braunschweig.calibration.student_behaviour_labels import (
    BEHAVIOUR_COLUMNS, CONTROLS, NOT_STUDENT, UNIVERSE_ADULTS, UNIVERSE_STUDENTS,
    share_standard_error)

logger = logging.getLogger(__name__)

_LOG_TAG = "[student control evidence]"

# ASSUMPTION (design section 6.8): a cell with fewer respondents is too thin for a regional
# target. A judgement fixed before the measurement, not a sourced threshold.
MIN_RESPONDENTS_PER_CELL = 30
# ASSUMPTION (design section 6.8): a MiD-SrV difference above two standard errors of the SrV share
# is taken as real, i.e. as something a control would steer. A judgement, not a sourced threshold.
MIN_DIFFERENCE_STANDARD_ERRORS = 2.0
RULE_UNIVERSE = UNIVERSE_STUDENTS
# Comparison tables by design (design section 6.8: the model derives availability itself).
COMPARISON_ONLY = ("car_availability", "driving_licence")
# Their SrV codebook variables in srv2023_student_comparison.csv (srv_student_reference.
# COMPARISON_VARIABLES, which also carries bicycle availability V_FK_VERFUEG).
COMPARISON_ONLY_SRV_VARIABLES = {"car_availability": "V_PKW_VERFUEG",
                                 "driving_licence": "V_FUEHR_PKW"}

# ASSUMPTION: when rule 2 cannot be evaluated for more than half of the cells (a NaN share means
# an empty base of a whole control), the input tables are broken, not thin, so the build raises.
# Half mirrors srv_student_reference.MAX_EXCLUSION_RATE; a judgement, not a sourced threshold.
MAX_EXCLUSION_RATE = 0.5

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


def build_level_gate(srv_shares: pd.DataFrame, census_students: pd.DataFrame,
                     mid_share_20_29: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
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
    ``residents_implied_by_enrollment_persons`` is NaN (Phase 1).

    Raises ``ValueError`` on a missing column or region, a duplicated code, a census row of
    another age group, a surveyed Kreis without SrV adults, an unsurveyed Kreis with SrV adults,
    an undefined MiD-to-census ratio, a site scope outside :data:`SITE_SCOPES`, a negative count
    or a local site outside the ZGB.
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
            "residents_implied_by_enrollment_persons": float("nan"),
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
