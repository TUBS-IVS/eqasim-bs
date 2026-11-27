"""Decision aid for the student LEVEL of the student balance (issue #140, Phase 2). Not a validation.

The level gate (``../student_level_gate.csv``) shows the balance below the SrV count of the students
aged 20 to 29. Before the owner decides whether the level stands, this script puts head counts of the
students aged 20 to 29 living in each Kreis next to each other (items 1 to 3 from three sources, item 4
a combination of 1 and 2) and measures why the plain SrV count is high.

1. **Balance.** The gate column ``balance_students_20_29_persons`` (enrollment of winter semester
   2024/25), and the same flows rescaled to the enrollment of winter semester 2022/23: the semester of
   the origin table, half a year after the census day and inside the SrV year. Every row of the flow
   table is divided by ``LSN enrollment of the institution / CHE total of the institution``.
   ASSUMPTION L1: the site split and the home pattern of 2024/25 hold for 2022/23. Institutions without
   a CHE total or without LSN enrollment are not rescaled (counted and logged).
2. **Census extrapolation.** The census count of the pupils and students outside the labour force,
   aged 20 to 29, of the Kreis, times ``enrolled students aged 20 to 29 in Germany / the same census
   count for Germany`` (``national_enrollment_20_29.csv``; summer semester 2022 holds the census day,
   winter semester 2022/23 is the sensitivity). ASSUMPTION L2: the national ratio holds in every Kreis.
   The count is by main residence and includes distance learners and every institution.
3. **SrV with the school degree corrected.** SrV has no school degree in its weights. The SrV persons
   aged 20 to 29 of a Kreis are post-stratified to the census count by school degree (university
   entrance qualification yes / no, census table 2000S-2038):
   ``students = N_census(with) * p_SrV(student | with) + N_census(without) * p_SrV(student | without)``.
   ASSUMPTION L3: SrV category 3 of ``V_SCHULAB`` equals the census category "Fachhochschul- oder
   Hochschulreife (Abitur)". ASSUMPTION L4: inside a school-degree class the SrV respondents stand for
   the whole Kreis, also for its municipalities SrV did not survey.

4. **Census pattern at the level of the balance.** The students aged 20 to 29 of the eight Kreise as the
   balance counts them (its total, anchored on enrollment), distributed over the Kreise in proportion to
   the census count of item 2. It keeps the regional level of the balance and replaces only its split
   over the Kreise; with one regional employed share of the students it also returns the census count of
   the students outside the labour force in every Kreis. Uses ASSUMPTION L2 for the split only.

Columns ``*_minus_srv_in_se``: a head count minus the school-corrected SrV count, in standard errors of
the latter; the header gives the sum of their squares over the seven surveyed Kreise per candidate (a
reading aid, the standard errors are approximate).

SrV universe of this aid: every person with a valid ``GEWICHT_P_ZENSUS``, INCLUDING the persons away
from home all day (a head count of residents). The gate column ``srv_estimate_students_20_29_persons``
uses the share of the persons at home on the day instead; it is copied here unchanged for comparison.

Third table: the students aged 20 to 29 with an education leg on the reporting day (``E_ZWECK_9`` in
:data:`srv_participation_universe.EDUCATION_E_ZWECK_9`, the definition of the day factor), SrV with the
corrected weights against ``balance x day factor``. It shares the SrV education-leg share with the day
factor, so it adds the split Braunschweig / surrounding Kreise, not a second test of the level. It also
gives the share of the students away from home all day per group (all students with a valid weight).

Optional fourth table (``--mid-persons``): the same school-degree check on the national MiD sample,
which DOES carry the school degree in its person weight, and the share of students with a second home.

Standard errors (approximate, for reading the tables, not a test): ``sqrt(p (1 - p) / n_effective)``
with the Kish ``n_effective`` of the base; 0 where a share is 0 or 1. The census counts and the
national ratio are treated as exact.

Usage (local-only inputs as arguments)::

    python analyse_student_level.py --srv-dir <folder with the three SrV CSV files>
        --zensus-students <zensus2022_students_20_29_kreis.csv>
        --zensus-school-dir <folder with the 2000S-2038 JSON files>
        [--mid-persons <MiD2023_Personen.csv>] --source-commit <sha> --out-dir <this folder>
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[5]
sys.path.append(str(REPO))

from braunschweig.calibration import srv_participation_universe as U  # noqa: E402
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE  # noqa: E402
from braunschweig.calibration.srv_student_reference import BRAUNSCHWEIG_KREIS  # noqa: E402
from braunschweig.data.education import student_od_table as T  # noqa: E402

logger = logging.getLogger("analyse_student_level")
_TAG = "[student level aid]"

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE.parent
GATE = ARTIFACTS / "student_level_gate.csv"
OD_TABLE = REPO / "eqasim-data/data/braunschweig/schools/student_od_table.csv"
CHE = REPO / "eqasim-data/data/braunschweig/schools/che_student_origins_ws2022_23.csv"
SRV_PARAMETERS = REPO / "eqasim-data/data/braunschweig/srv/srv2023_student_parameters.csv"
NATIONAL = HERE / "national_enrollment_20_29.csv"

CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)
PERSONS_FILE, LEGS_FILE, HOUSEHOLDS_FILE = "SrV2023_Personen.csv", "SrV2023_Wege.csv", "SrV2023_Haushalte.csv"
PERSON_READ_COLUMNS = ["HHNR", "PNR", "V_ALTER", "V_ERW", "V_SCHULAB", "E_ANZ_WEGE", "GEWICHT_P_ZENSUS",
                       "MITTL_WERKTAG"]

AGE_MIN_YEARS, AGE_MAX_YEARS = 20, 29
SRV_STUDENT = 7                    # V_ERW "Student/Studentin"
SRV_WITH_QUALIFICATION = 3         # V_SCHULAB "Allgemeine oder fachgebundene Hochschulreife (Abitur)"
SRV_WITHOUT_QUALIFICATION = (1, 2, 4)   # Hauptschule, Realschule, (noch) ohne Schulabschluss
SRV_AWAY_ALL_DAY = -7              # E_ANZ_WEGE of a person away from home all day
CLASS_WITH, CLASS_WITHOUT, CLASS_UNKNOWN = "with", "without", "unknown"

CENSUS_SCHOOL_TABLE = "2000S-2038"
CENSUS_AGE = "ALT020B029"
CENSUS_QUALIFICATION = "ABSCH-FHRABI"
CENSUS_TOTAL = "%TOTAL%"
CENSUS_PUBLISHED = "e"
CENSUS_GERMANY = "DG"
CENSUS_COUNT_TOLERANCE_PERSONS = 10.0   # the publisher rounds the counts to 10

SEMESTER_CENSUS_DAY, SEMESTER_SENSITIVITY = "SS 2022", "WS 2022/23"
GROUP_SURROUNDING = "surrounding_six"   # the surveyed Kreise without Braunschweig
GROUP_SURVEYED = U.REGION_CODE          # the seven surveyed Kreise, the pooled code of the gate table

# balance column -> the same regional total distributed over the Kreise by the census pattern (item 4)
CANDIDATES_PATTERN = {
    "balance_students_20_29_persons": "census_pattern_students_20_29_persons",
    "balance_ws2022_23_students_20_29_persons": "census_pattern_ws2022_23_students_20_29_persons",
}
# the head counts compared with the school-corrected SrV count
CANDIDATES = [*CANDIDATES_PATTERN, *CANDIDATES_PATTERN.values(), "census_extrapolation_students_20_29_persons",
              "srv_gate_estimate_students_20_29_persons"]

MID_STUDENT, MID_UNKNOWN_ACTIVITY = 10, 99        # P_TAET
MID_WITH_QUALIFICATION = (8, 9)                    # P_BIL1 Fachhochschulreife, Abitur
MID_UNKNOWN_SCHOOL = 99
MID_SECOND_HOME_YES, MID_SECOND_HOME_NO = 1, 2     # P_NEBEN


# --------------------------------------------------------------------------- small helpers
def _load_cli():
    spec = importlib.util.spec_from_file_location("build_student_balance",
                                                  REPO / "scripts" / "build_student_balance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _n_effective(weights: pd.Series) -> float:
    total = float(weights.sum())
    squares = float((weights ** 2).sum())
    return total ** 2 / squares if squares > 0 else float("nan")


def _share(weights: pd.Series, mask: pd.Series) -> float:
    total = float(weights.sum())
    return float(weights[mask].sum()) / total if total > 0 else float("nan")


def _standard_error(share: float, n_effective: float) -> float:
    if not (np.isfinite(share) and np.isfinite(n_effective) and n_effective > 0):
        return float("nan")
    return float(np.sqrt(max(share * (1.0 - share), 0.0) / n_effective))


def _root_sum_square(values) -> float:
    values = np.asarray(list(values), dtype=float)
    return float(np.sqrt(np.sum(values ** 2)))


# --------------------------------------------------------------------------- census school degree
def _census_cell(dataset: dict, wanted: dict, path: Path) -> float:
    """The published count of one cell; raises for an absent category or a withheld cell."""
    position = 0
    for name, size in zip(dataset["id"], dataset["size"]):
        index = dataset["dimension"][name]["category"]["index"]
        if name in wanted:
            if wanted[name] not in index:
                raise ValueError("%s: category %r of %s is absent; the table layout changed"
                                 % (path.name, wanted[name], name))
            step = index[wanted[name]]
        else:
            if size != 1:
                raise ValueError("%s: dimension %s has %d categories, expected 1" % (path.name, name, size))
            step = 0
        position = position * size + step
    value, flag = dataset["value"][position], dataset["status"][position]
    if flag != CENSUS_PUBLISHED or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("%s: cell %s is %r with status flag %r, not a published positive count"
                         % (path.name, wanted, value, flag))
    return float(value)


def read_census_school(path: Path, region_variable: str, regions) -> pd.DataFrame:
    """Persons aged 20 to 29 and those with a university entrance qualification, per region.

    ``path`` is one file of census table 2000S-2038 as the census database delivers it (two datasets:
    the detailed school degree ``BILSA4`` and the coarse one ``BILSA3`` whose total is every person).
    """
    if not path.is_file():
        raise FileNotFoundError("census file %s is missing; fetch table %s from the census database first"
                                % (path, CENSUS_SCHOOL_TABLE))
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    found = {variable: [ds for ds in document["data"] if variable in ds["id"]] for variable in ("BILSA4", "BILSA3")}
    for variable, datasets in found.items():
        if len(datasets) != 1:
            raise ValueError("%s has %d datasets with variable %s, expected one"
                             % (path.name, len(datasets), variable))
    rows = []
    for region in regions:
        total = _census_cell(found["BILSA3"][0],
                             {"BILSA3": CENSUS_TOTAL, "ALTGR2": CENSUS_AGE, region_variable: region}, path)
        with_q = _census_cell(found["BILSA4"][0],
                              {"BILSA4": CENSUS_QUALIFICATION, "ALTGR2": CENSUS_AGE, region_variable: region}, path)
        rows.append({"code": region, "persons": total, CLASS_WITH: with_q, CLASS_WITHOUT: total - with_q})
    return pd.DataFrame(rows).set_index("code")


# --------------------------------------------------------------------------- SrV
def read_srv(srv_dir: Path) -> tuple[pd.DataFrame, dict]:
    """SrV persons of the ZGB Kreise with a valid census weight, one row per person, and the counts of
    every exclusion. Columns: pid, kreis, weight, age, school_class, status_known, student, present,
    education_leg."""
    for name in (PERSONS_FILE, LEGS_FILE, HOUSEHOLDS_FILE):
        if not (srv_dir / name).is_file():
            raise FileNotFoundError("SrV file %s is missing in %s" % (name, srv_dir))
    persons = pd.read_csv(srv_dir / PERSONS_FILE, usecols=PERSON_READ_COLUMNS, **CSV_READ_KWARGS)
    legs = pd.read_csv(srv_dir / LEGS_FILE, usecols=U.LEG_COLUMNS, **CSV_READ_KWARGS)
    households = pd.read_csv(srv_dir / HOUSEHOLDS_FILE, usecols=U.HOUSEHOLD_COLUMNS, **CSV_READ_KWARGS)
    U.require_average_weekday(persons)
    counts = {"n_persons_total": int(len(persons))}

    ags = households["AGS"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(8)
    kreis_by_household = pd.Series(ags.str[:5].to_numpy(), index=households["HHNR"].to_numpy())
    if kreis_by_household.index.duplicated().any():
        raise ValueError("SrV households: duplicated HHNR")
    frame = pd.DataFrame({
        "pid": U._person_ids(persons),
        "kreis": persons["HHNR"].map(kreis_by_household),
        "weight": pd.to_numeric(persons["GEWICHT_P_ZENSUS"], errors="coerce"),
        "age": pd.to_numeric(persons["V_ALTER"], errors="coerce"),
        "status": pd.to_numeric(persons["V_ERW"], errors="coerce"),
        "school": pd.to_numeric(persons["V_SCHULAB"], errors="coerce"),
        "n_legs": pd.to_numeric(persons["E_ANZ_WEGE"], errors="coerce"),
    })
    valid_weight = frame["weight"] >= 0
    counts["n_excluded_invalid_weight"] = int((~valid_weight).sum())
    frame = frame[valid_weight]
    in_region = frame["kreis"].isin(ZGB_KREISE)
    counts["n_excluded_outside_region"] = int((~in_region).sum())
    frame = frame[in_region].copy()
    counts["n_persons_valid"] = int(len(frame))
    counts["n_missing_age"] = int(frame["age"].isna().sum())

    frame["school_class"] = CLASS_UNKNOWN
    frame.loc[frame["school"] == SRV_WITH_QUALIFICATION, "school_class"] = CLASS_WITH
    frame.loc[frame["school"].isin(SRV_WITHOUT_QUALIFICATION), "school_class"] = CLASS_WITHOUT
    frame["status_known"] = frame["status"] >= 0
    frame["student"] = frame["status"] == SRV_STUDENT
    negative = frame["n_legs"] < 0
    if not (frame.loc[negative, "n_legs"] == SRV_AWAY_ALL_DAY).all():
        raise ValueError("SrV persons: a negative E_ANZ_WEGE other than %d; the away-all-day rule would be wrong"
                         % SRV_AWAY_ALL_DAY)
    frame["present"] = ~negative & frame["n_legs"].notna()
    pids, leg_counts = U.persons_with_purpose(legs, U.EDUCATION_E_ZWECK_9)
    frame["education_leg"] = frame["pid"].isin(pids)
    counts.update(leg_counts)
    return frame.drop(columns=["status", "school", "n_legs"]), counts


def _class_columns(rated: pd.DataFrame) -> dict:
    """Student share, its base and standard error inside the two school-degree classes."""
    out = {}
    for label in (CLASS_WITH, CLASS_WITHOUT):
        base = rated[rated["school_class"] == label]
        share, n_eff = _share(base["weight"], base["student"]), _n_effective(base["weight"])
        out.update({"n_%s_qualification" % label: int(len(base)),
                    "n_effective_%s_qualification" % label: n_eff,
                    "student_share_%s_qualification" % label: share,
                    "se_student_share_%s_qualification" % label: _standard_error(share, n_eff)})
    return out


def _sample_columns(code: str, part: pd.DataFrame, share_census: float) -> dict:
    known = part[part["school_class"] != CLASS_UNKNOWN]
    status_base = part[part["status_known"]]
    row = {"code": code, "n_persons_20_29": int(len(part)),
           "n_unknown_school_degree": int((part["school_class"] == CLASS_UNKNOWN).sum()),
           "srv_share_with_qualification_weighted": _share(known["weight"], known["school_class"] == CLASS_WITH),
           "srv_share_with_qualification_unweighted": float((known["school_class"] == CLASS_WITH).mean()),
           "zensus_share_with_qualification": share_census}
    row.update(_class_columns(known[known["status_known"]]))
    row["srv_student_share_uncorrected"] = _share(status_base["weight"], status_base["student"])
    return row


def school_degree_rows(young: pd.DataFrame, census: pd.DataFrame, groups: dict) -> pd.DataFrame:
    """One row per surveyed Kreis and per group: the SrV sample by school degree against the census,
    the student shares inside the two classes and the corrected student count."""
    rows, by_kreis = [], {}
    for code in [k for k in ZGB_KREISE if k not in U.UNSURVEYED_KREISE]:
        row = _sample_columns(code, young[young["kreis"] == code],
                              float(census.loc[code, CLASS_WITH] / census.loc[code, "persons"]))
        corrected = sum(float(census.loc[code, label]) * row["student_share_%s_qualification" % label]
                        for label in (CLASS_WITH, CLASS_WITHOUT))
        row["srv_students_school_corrected_persons"] = corrected
        row["se_srv_students_school_corrected_persons"] = _root_sum_square(
            float(census.loc[code, label]) * row["se_student_share_%s_qualification" % label]
            for label in (CLASS_WITH, CLASS_WITHOUT))
        row["srv_student_share_school_corrected"] = corrected / float(census.loc[code, "persons"])
        by_kreis[code] = row
        rows.append(row)
    for group, codes in groups.items():
        persons = float(census.loc[codes, "persons"].sum())
        row = _sample_columns(group, young[young["kreis"].isin(codes)],
                              float(census.loc[codes, CLASS_WITH].sum()) / persons)
        # A group is the SUM of its Kreis estimates: each Kreis keeps its own census margin.
        corrected = sum(by_kreis[c]["srv_students_school_corrected_persons"] for c in codes)
        row["srv_students_school_corrected_persons"] = corrected
        row["se_srv_students_school_corrected_persons"] = _root_sum_square(
            by_kreis[c]["se_srv_students_school_corrected_persons"] for c in codes)
        row["srv_student_share_school_corrected"] = corrected / persons
        rows.append(row)
    return pd.DataFrame(rows)


def corrected_weights(young: pd.DataFrame, census: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """``young`` with two weights that sum to the census persons aged 20 to 29 of each Kreis:
    ``weight_population`` (one factor per Kreis) and ``weight_school`` (one factor per Kreis and
    school-degree class; a person without a readable school degree keeps the Kreis factor)."""
    out = young.copy()
    out["weight_population"] = np.nan
    out["weight_school"] = np.nan
    n_fallback = 0
    for code, part in out.groupby("kreis"):
        persons = float(census.loc[code, "persons"])
        factor_kreis = persons / float(part["weight"].sum())
        out.loc[part.index, "weight_population"] = part["weight"] * factor_kreis
        out.loc[part.index, "weight_school"] = part["weight"] * factor_kreis
        unknown = part["school_class"] == CLASS_UNKNOWN
        # the persons without a readable school degree keep their Kreis factor; the two classes share
        # the remaining census persons in the census proportion
        scale = (persons - float(part.loc[unknown, "weight"].sum()) * factor_kreis) / persons
        for label in (CLASS_WITH, CLASS_WITHOUT):
            members = part[part["school_class"] == label]
            if members.empty:
                raise ValueError("SrV Kreis %s has no person aged 20 to 29 in school class %r" % (code, label))
            out.loc[members.index, "weight_school"] = (
                members["weight"] * float(census.loc[code, label]) * scale / float(members["weight"].sum()))
        n_fallback += int(unknown.sum())
    n_total = int(len(out))
    logger.info("%s school-degree weight: primary %d/%d (%.1f%%), Kreis-factor fallback for an unreadable school "
                "degree %d (%.1f%%)", _TAG, n_total - n_fallback, n_total, 100.0 * (n_total - n_fallback) / n_total,
                n_fallback, 100.0 * n_fallback / n_total)
    return out, {"n_school_weight_primary": n_total - n_fallback, "n_school_weight_fallback": n_fallback}


def education_leg_rows(weighted: pd.DataFrame, groups: dict, balance: pd.DataFrame, day_factor: float) -> pd.DataFrame:
    rows = []
    for group, codes in groups.items():
        students = weighted[weighted["kreis"].isin(codes) & weighted["student"]]
        away_share = _share(students["weight_school"], ~students["present"])
        part = students[students["present"]]
        with_leg = part[part["education_leg"]]
        share = _share(part["weight_school"], part["education_leg"])
        rows.append({
            "code": group,
            "n_students_unweighted": int(len(students)),
            "n_students_away_all_day_unweighted": int((~students["present"]).sum()),
            "srv_school_corrected_away_all_day_share": away_share,
            "se_srv_school_corrected_away_all_day_share":
                _standard_error(away_share, _n_effective(students["weight_school"])),
            "n_students_present_unweighted": int(len(part)),
            "n_students_with_education_leg_unweighted": int(len(with_leg)),
            "srv_population_weight_students_with_education_leg_persons": float(with_leg["weight_population"].sum()),
            "srv_school_corrected_students_with_education_leg_persons": float(with_leg["weight_school"].sum()),
            "srv_school_corrected_students_present_persons": float(part["weight_school"].sum()),
            "srv_school_corrected_education_leg_share": share,
            "se_srv_school_corrected_education_leg_share": _standard_error(share, _n_effective(part["weight_school"])),
            "balance_times_day_factor_persons":
                float(balance.loc[codes, "balance_students_20_29_persons"].sum()) * day_factor,
            "balance_ws2022_23_times_day_factor_persons":
                float(balance.loc[codes, "balance_ws2022_23_students_20_29_persons"].sum()) * day_factor,
            "day_factor": day_factor,
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- balance
def balance_by_home(od: pd.DataFrame, che_totals: pd.Series, gate: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Students aged 20 to 29 living in each ZGB Kreis: the gate value and the flows rescaled to the
    CHE totals of winter semester 2022/23 (ASSUMPTION L1)."""
    sites = od.drop_duplicates("destination_id")
    lsn = sites.groupby("institution")["enrollment_persons"].sum(min_count=1)
    che_by_lsn = {T.INSTITUTION_CROSSWALK[name]: float(total) for name, total in che_totals.items()
                  if name in T.INSTITUTION_CROSSWALK}
    factors, kept = {}, []
    for institution in sorted(set(od["institution"])):
        enrollment = lsn.get(institution, float("nan"))
        if institution in che_by_lsn and np.isfinite(enrollment) and enrollment > 0:
            factors[institution] = float(enrollment) / che_by_lsn[institution]
        else:
            factors[institution] = 1.0
            kept.append(institution)
    logger.info("%s semester rescaling: primary %d/%d institutions rescaled to their CHE total, fallback %d kept "
                "at the LSN semester (no CHE total or no LSN enrollment): %s", _TAG, len(factors) - len(kept),
                len(factors), len(kept), kept)
    scaled = od["students_persons"].astype(float) / od["institution"].map(factors).astype(float)
    region = od["home_ars5"].isin(ZGB_KREISE)
    now = od.loc[region].groupby("home_ars5")["students_persons"].sum()
    then = scaled[region].groupby(od.loc[region, "home_ars5"]).sum()
    gate_kreise = gate.loc[list(ZGB_KREISE)]
    worst = float((now.reindex(ZGB_KREISE) - gate_kreise["balance_students_persons"]).abs().max())
    if worst > 1e-3:
        raise ValueError("the flow table and the gate table disagree on the students living per Kreis "
                         "(largest difference %.6g persons); they do not come from one run" % worst)
    age_share = gate_kreise["balance_students_20_29_persons"] / gate_kreise["balance_students_persons"]
    out = pd.DataFrame({
        "balance_students_20_29_persons": gate_kreise["balance_students_20_29_persons"].astype(float),
        "balance_ws2022_23_students_20_29_persons": then.reindex(ZGB_KREISE) * age_share,
    })
    return out, {"semester_factors": factors, "institutions_not_rescaled": kept}


# --------------------------------------------------------------------------- MiD (optional)
def mid_check(path: Path, census_germany: pd.Series) -> pd.DataFrame:
    """National MiD sample, persons aged 20 to 29: the school degree before and after the person weight
    (which carries the school degree), the student shares and the share with a second home."""
    columns = ["HP_ALTER", "P_TAET", "P_GEW", "P_BIL1", "P_NEBEN"]
    persons = pd.read_csv(path, usecols=columns, low_memory=False)
    n_total = int(len(persons))
    persons = persons[persons["P_GEW"] > 0]
    young = persons[(persons["HP_ALTER"] >= AGE_MIN_YEARS) & (persons["HP_ALTER"] <= AGE_MAX_YEARS)
                    & (persons["P_TAET"] != MID_UNKNOWN_ACTIVITY)]
    weight = young["P_GEW"]
    known = young["P_BIL1"] != MID_UNKNOWN_SCHOOL
    with_q = young["P_BIL1"].isin(MID_WITH_QUALIFICATION)
    student = young["P_TAET"] == MID_STUDENT
    answered = young["P_NEBEN"].isin([MID_SECOND_HOME_YES, MID_SECOND_HOME_NO])
    second = young["P_NEBEN"] == MID_SECOND_HOME_YES
    row = {
        "code": "DE", "n_persons_total": n_total, "n_persons_20_29": int(len(young)),
        "n_unknown_school_degree": int((~known).sum()),
        "mid_share_with_qualification_unweighted": float(with_q[known].mean()),
        "mid_share_with_qualification_weighted": _share(weight[known], with_q[known]),
        "zensus_share_with_qualification": float(census_germany[CLASS_WITH] / census_germany["persons"]),
        "mid_student_share_weighted": _share(weight, student),
        "mid_student_share_with_qualification": _share(weight[known & with_q], student[known & with_q]),
        "mid_student_share_without_qualification": _share(weight[known & ~with_q], student[known & ~with_q]),
        "n_students_20_29": int(student.sum()),
        "mid_students_second_home_share": _share(weight[student & answered], second[student & answered]),
        "mid_other_persons_second_home_share": _share(weight[~student & answered], second[~student & answered]),
    }
    return pd.DataFrame([row])


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--srv-dir", required=True)
    parser.add_argument("--zensus-students", required=True)
    parser.add_argument("--zensus-school-dir", required=True)
    parser.add_argument("--mid-persons", default=None)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    cli = _load_cli()
    out_dir = Path(args.out_dir)
    srv_dir, school_dir = Path(args.srv_dir), Path(args.zensus_school_dir)
    school_kreis = school_dir / ("%s_ALTGR2_GEOLK4.json" % CENSUS_SCHOOL_TABLE)
    school_germany = school_dir / ("%s_ALTGR2_GEODL1.json" % CENSUS_SCHOOL_TABLE)

    gate = pd.read_csv(GATE, comment="#", dtype={"code": str}).set_index("code")
    od = pd.read_csv(OD_TABLE, comment="#", dtype={"home_ars5": str, "site_kreis_ars5": str})
    che = pd.read_csv(CHE, comment="#", dtype={"origin_ars5": str})
    national = pd.read_csv(NATIONAL, comment="#").set_index("semester")
    zensus = pd.read_csv(args.zensus_students, comment="#", dtype={"region": str}).set_index("region")
    parameters = cli._read_csv(SRV_PARAMETERS, cli.PARAMETER_COLUMNS, "SrV student parameters")
    away_share = float(cli._parameter_shares(parameters, "day", "away_all_day", "students_all")["yes"])
    education_leg_share = float(cli.read_header_value(SRV_PARAMETERS, cli.EDUCATION_LEG_KEY))
    day_factor = education_leg_share * (1.0 - away_share)

    surveyed = [code for code in ZGB_KREISE if code not in U.UNSURVEYED_KREISE]
    groups = {BRAUNSCHWEIG_KREIS: [BRAUNSCHWEIG_KREIS],
              GROUP_SURROUNDING: [code for code in surveyed if code != BRAUNSCHWEIG_KREIS],
              GROUP_SURVEYED: surveyed}

    census = read_census_school(school_kreis, "GEOLK4", list(ZGB_KREISE))
    census_germany = read_census_school(school_germany, "GEODL1", [CENSUS_GERMANY]).loc[CENSUS_GERMANY]
    difference = float((census["persons"] - zensus.loc[list(ZGB_KREISE), "all_persons"].astype(float)).abs().max())
    if difference > CENSUS_COUNT_TOLERANCE_PERSONS:
        raise ValueError("the persons aged 20 to 29 of census table %s and of the activity-status table differ by "
                         "up to %.0f persons; the two tables do not describe one population"
                         % (CENSUS_SCHOOL_TABLE, difference))

    # 1. balance, 2. census extrapolation
    balance, balance_info = balance_by_home(od, T.che_totals(che), gate)
    group_z = zensus["students_nonemployed_persons"].astype(float)
    ratio = {semester: float(national.loc[semester, "students_20_29_persons"]) / float(group_z[CENSUS_GERMANY])
             for semester in (SEMESTER_CENSUS_DAY, SEMESTER_SENSITIVITY)}
    logger.info("%s enrolled students aged 20 to 29 per census pupil or student outside the labour force, Germany: "
                "%s %.4f, %s %.4f", _TAG, SEMESTER_CENSUS_DAY, ratio[SEMESTER_CENSUS_DAY], SEMESTER_SENSITIVITY,
                ratio[SEMESTER_SENSITIVITY])

    # 3. SrV with the school degree corrected
    srv, counts = read_srv(srv_dir)
    young = srv[(srv["age"] >= AGE_MIN_YEARS) & (srv["age"] <= AGE_MAX_YEARS)]
    counts["n_persons_20_29"] = int(len(young))
    counts["n_unreadable_status_20_29"] = int((~young["status_known"]).sum())
    school = school_degree_rows(young, census, {GROUP_SURROUNDING: groups[GROUP_SURROUNDING],
                                                GROUP_SURVEYED: groups[GROUP_SURVEYED]})
    weighted, weight_counts = corrected_weights(young, census)
    counts.update(weight_counts)
    legs = education_leg_rows(weighted, groups, balance, day_factor)

    corrected = school.set_index("code")
    rows = []
    for code in [*ZGB_KREISE, GROUP_SURROUNDING, GROUP_SURVEYED]:
        codes = groups.get(code, [code])
        has_srv = all(c in surveyed for c in codes)
        z = float(group_z.loc[codes].sum())
        rows.append({
            "code": code,
            "zensus_population_20_29_persons": float(zensus.loc[codes, "all_persons"].astype(float).sum()),
            "zensus_pupils_students_outside_labour_force_20_29_persons": z,
            "balance_students_20_29_persons": float(balance.loc[codes, "balance_students_20_29_persons"].sum()),
            "balance_ws2022_23_students_20_29_persons":
                float(balance.loc[codes, "balance_ws2022_23_students_20_29_persons"].sum()),
            "census_extrapolation_students_20_29_persons": z * ratio[SEMESTER_CENSUS_DAY],
            "census_extrapolation_ws2022_23_students_20_29_persons": z * ratio[SEMESTER_SENSITIVITY],
            "srv_school_corrected_students_20_29_persons":
                float(corrected.loc[code, "srv_students_school_corrected_persons"]) if has_srv else float("nan"),
            "se_srv_school_corrected_students_20_29_persons":
                float(corrected.loc[code, "se_srv_students_school_corrected_persons"]) if has_srv else float("nan"),
            "srv_gate_estimate_students_20_29_persons":
                float(gate.loc[codes, "srv_estimate_students_20_29_persons"].sum()) if has_srv else float("nan"),
            "se_srv_gate_estimate_students_20_29_persons":
                _root_sum_square(gate.loc[codes, "srv_se_estimate_students_20_29_persons"]) if has_srv
                else float("nan"),
            "census_times_mid_ratio_students_20_29_persons":
                float(gate.loc[codes, "census_estimate_students_20_29_persons"].sum()),
        })
    estimates = pd.DataFrame(rows).set_index("code")
    kreise = list(ZGB_KREISE)
    pattern = estimates.loc[kreise, "zensus_pupils_students_outside_labour_force_20_29_persons"]
    pattern = pattern / float(pattern.sum())
    for source, target in CANDIDATES_PATTERN.items():
        estimates[target] = np.nan
        estimates.loc[kreise, target] = pattern * float(estimates.loc[kreise, source].sum())
        for group in (GROUP_SURROUNDING, GROUP_SURVEYED):
            estimates.loc[group, target] = float(estimates.loc[groups[group], target].sum())
    squares = {}
    for candidate in CANDIDATES:
        column = candidate.replace("_students_20_29_persons", "") + "_minus_srv_in_se"
        estimates[column] = ((estimates[candidate] - estimates["srv_school_corrected_students_20_29_persons"])
                             / estimates["se_srv_school_corrected_students_20_29_persons"])
        squares[candidate] = float((estimates.loc[surveyed, column] ** 2).sum())
    logger.info("%s sum of squared differences to the school-corrected SrV count over the %d surveyed Kreise, "
                "in SrV standard errors: %s", _TAG, len(surveyed),
                {name: round(value, 2) for name, value in squares.items()})
    estimates = estimates.reset_index()

    today = dt.date.today().isoformat()
    inputs = [("level_gate", GATE, False), ("flow_table", OD_TABLE, False), ("che_origins", CHE, False),
              ("srv_student_parameters", SRV_PARAMETERS, False), ("national_enrollment", NATIONAL, False),
              ("zensus_students", Path(args.zensus_students), True), ("zensus_school_kreis", school_kreis, True),
              ("zensus_school_germany", school_germany, True),
              ("srv_persons", srv_dir / PERSONS_FILE, True), ("srv_legs", srv_dir / LEGS_FILE, True),
              ("srv_households", srv_dir / HOUSEHOLDS_FILE, True)]
    if args.mid_persons:
        inputs.append(("mid_persons", Path(args.mid_persons), True))
    common = [
        "# Generated by docs/runs/artifacts/student-balance-2026-11-26/level_decision/analyse_student_level.py on "
        "%s; code state eqasim-bs %s." % (today, args.source_commit),
        "# A decision aid built from the inputs below, not a validation. Assumptions L1 to L4 and the standard "
        "errors are described in the docstring of the script.",
        "# Inputs:", *cli._input_lines(inputs),
        "# Census school degree: table %s of the census database (https://ergebnisse.zensus2022.de), persons aged "
        "15 or older by age group and highest school degree, reference day 2022-05-15, licence dl-de/by-2-0; data "
        "call /proxy/api/rest/tables/%s/data/ with '2000S ALTGR2 BILSA3§BILSA4 <GEOLK4|GEODL1> PRS006 STAG', fetched "
        "2026-11-27." % (CENSUS_SCHOOL_TABLE, CENSUS_SCHOOL_TABLE),
        "# Ages %d to %d inclusive. Region Kreise %s; SrV does not survey %s." % (
            AGE_MIN_YEARS, AGE_MAX_YEARS, list(ZGB_KREISE), list(U.UNSURVEYED_KREISE)),
        "# Rows %s = the surveyed Kreise without Braunschweig, %s = the seven surveyed Kreise; a group is the sum "
        "of its Kreise, its standard error the root of the summed squares." % (GROUP_SURROUNDING, GROUP_SURVEYED),
        "# SrV counts: %s" % json.dumps(counts, sort_keys=True),
    ]
    cli._write(estimates, out_dir / "student_level_estimates_20_29.csv", [
        "# Table: student_level_estimates_20_29.csv (issue #140, Phase 2). Students aged 20 to 29 living in each "
        "Kreis: balance, census extrapolation, school-corrected SrV, census pattern.", *common,
        "# balance_students_20_29_persons: the gate column (enrollment of winter semester 2024/25); "
        "balance_ws2022_23_...: the same flows divided by LSN enrollment / CHE total of the institution (L1); "
        "not rescaled: %s." % balance_info["institutions_not_rescaled"],
        "# census_extrapolation_...: zensus_pupils_students_outside_labour_force_20_29_persons times enrolled "
        "students aged 20 to 29 in Germany / the same census count for Germany (L2): %s ratio %.10g, %s ratio "
        "%.10g." % (SEMESTER_CENSUS_DAY, ratio[SEMESTER_CENSUS_DAY], SEMESTER_SENSITIVITY,
                    ratio[SEMESTER_SENSITIVITY]),
        "# srv_school_corrected_...: SrV post-stratified to the census school degree of the Kreis (L3, L4), all "
        "persons with a valid weight; srv_gate_estimate_... and census_times_mid_ratio_...: the columns "
        "srv_estimate_students_20_29_persons and census_estimate_students_20_29_persons of the gate table, "
        "unchanged.",
        "# census_pattern_...: the total of the balance column over the eight Kreise, distributed by the census "
        "count of the pupils and students outside the labour force (item 4 of the docstring).",
        "# *_minus_srv_in_se: head count minus srv_school_corrected_..., in its standard errors; sum of the "
        "squares over the %d surveyed Kreise: %s." % (len(surveyed), json.dumps(
            {name: round(value, 4) for name, value in squares.items()})),
    ])
    cli._write(school, out_dir / "srv_school_degree_20_29.csv", [
        "# Table: srv_school_degree_20_29.csv (issue #140, Phase 2). The SrV sample aged 20 to 29 by school degree "
        "against the census, and the student shares inside the two classes.", *common,
        "# with qualification: SrV V_SCHULAB %d; census category %s (Fachhochschul- oder Hochschulreife). Persons "
        "without a readable school degree are left out of the shares and counted (n_unknown_school_degree)." % (
            SRV_WITH_QUALIFICATION, CENSUS_QUALIFICATION),
        "# srv_students_school_corrected_persons = census persons with x student share with + census persons "
        "without x student share without; the census persons are those of the whole Kreis.",
    ])
    cli._write(legs, out_dir / "student_education_leg_day_20_29.csv", [
        "# Table: student_education_leg_day_20_29.csv (issue #140, Phase 2). Students aged 20 to 29 with an "
        "education leg on the reporting day: SrV against balance x day factor.", *common,
        "# away_all_day: students with E_ANZ_WEGE %d among all students with a valid weight (weight_school)."
        % SRV_AWAY_ALL_DAY,
        "# Education-leg columns: students at home at some point on the day; education leg = E_ZWECK_9 in %s. "
        "weight_population: the SrV "
        "weight times one factor per Kreis so that the persons aged 20 to 29 sum to the census; weight_school: one "
        "factor per Kreis and school-degree class." % sorted(U.EDUCATION_E_ZWECK_9),
        "# day_factor = %s %.10g x (1 - away_all_day_share %.10g), both from srv2023_student_parameters.csv (adult "
        "students of all ages). The SrV side and the day factor share the SrV education-leg share: the table shows "
        "the split between Braunschweig and the surrounding Kreise, not a second test of the level." % (
            cli.EDUCATION_LEG_KEY, education_leg_share, away_share),
    ])
    if args.mid_persons:
        mid = mid_check(Path(args.mid_persons), census_germany)
        cli._write(mid, out_dir / "mid_school_degree_check_20_29.csv", [
            "# Table: mid_school_degree_check_20_29.csv (issue #140, Phase 2). National MiD 2023 sample aged 20 to "
            "29: school degree before and after the person weight, student shares, second home.", *common,
            "# MiD: weight P_GEW; student = P_TAET %d (P_TAET %d left out); with qualification = P_BIL1 in %s "
            "(P_BIL1 %d left out and counted); second home = P_NEBEN %d among the persons who answered it." % (
                MID_STUDENT, MID_UNKNOWN_ACTIVITY, list(MID_WITH_QUALIFICATION), MID_UNKNOWN_SCHOOL,
                MID_SECOND_HOME_YES),
        ])
    else:
        logger.info("%s --mid-persons not given: the national MiD table is not written", _TAG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
