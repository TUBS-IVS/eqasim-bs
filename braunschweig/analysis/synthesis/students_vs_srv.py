"""synpp stage: the student measurement of the synthetic population against SrV 2023 (#140, Phase 1).

A MEASUREMENT, not a validation: the stage writes report files only, no synthesis or location
stage reads them, and a difference is stated in SrV standard errors, never as a verdict.

The stage loads cached synthesis stages (no MATSim) and hands them to the pure functions of
:mod:`braunschweig.analysis.student_measurement`, which own every definition (students,
adults, the presence universe, the region scope, the label vocabulary, every exclusion rule):

* :func:`~braunschweig.analysis.student_measurement.model_student_frame` and
  :func:`~braunschweig.analysis.student_measurement.compare_parameters` -> the parameter cells
  of the adult students against ``srv2023_student_parameters.csv``;
* :func:`~braunschweig.analysis.student_measurement.student_level_table` -> student shares per
  Kreis against ``srv2023_student_share_by_kreis.csv``;
* :func:`~braunschweig.analysis.student_measurement.young_adult_status_table` -> the 20 to 29
  year olds against ``srv2023_young_adult_status_by_kreis.csv`` and the Zensus 2022 employed
  count (``<data_path>/braunschweig/popsim/`` +
  :data:`braunschweig.popsim.zensus_employment_age.KREIS_EMPLOYED_BY_AGE_FILE`, local-only);
* :func:`~braunschweig.analysis.student_measurement.student_work_class_table` -> the students'
  work-control class;
* :func:`~braunschweig.analysis.student_measurement.plan_source_table` -> own diary or re-drawn
  plan, its match path and the donor's activity, from ``CompletedDonor.persons`` and the two
  trace files the completed-donor stage writes into its cache directory.

Inputs and refusals (no silent fallback)
----------------------------------------
* The model side is the reporting-day trips view the sibling plan-structure comparison reads
  (:func:`braunschweig.synthesis.portal_trips.config_keys.final_view_trips_stage`, the
  pre-portal day while the portal layer is on).
* The general day-absence draw is MANDATORY: the presence universe (persons at home at some
  point of the reporting day) is what makes the model side comparable with SrV. With
  ``day_absence_enabled`` false ``configure`` raises ``ValueError``, so the run stops at DAG
  time, before the population is built.
* A missing committed SrV table or a missing Zensus file raises ``FileNotFoundError``; a person
  frame without ``P_TAET`` raises (the stage is a popsim_mid stage).
* A missing trace file is recorded in ``provenance.json`` as ``trace_available: false`` and
  logged as a warning; the match path of every person that only that trace would resolve is
  then ``unresolved`` (counted in the plan-source diagnostics).

Outputs, under ``<output_path>/<students_vs_srv_output_subdir>/``: ``student_level_by_kreis.csv``,
``young_adult_status_by_kreis.csv``, ``student_work_class.csv``, ``student_behaviour_vs_srv.csv``,
``student_plan_source.csv`` (plain CSVs, LF, ``%.10g``, as the sibling stages write them; the
provenance lives in ``provenance.json`` and at the top of ``summary.md``), ``summary.md`` and
``provenance.json`` (git commit, parameters, SrV table and Zensus paths relative to
``data_path`` with their sha256, the trace file names, every diagnostics dict,
``sampling_rate``). No output carries an absolute path. The stage's own cache directory gets
:data:`REPORT_FILE` with the exclusion and fallback counts and rates (:func:`exclusion_report`).
"""
from __future__ import annotations

import datetime as dt
import logging
import os

import numpy as np
import pandas as pd

from braunschweig import provenance as run_provenance
from braunschweig import synpp_tokens
from braunschweig.analysis import json_output
from braunschweig.analysis import plan_structure as _plan_structure
from braunschweig.analysis import student_measurement as M
from braunschweig.analysis.synthesis import plan_structure_vs_srv as _sibling
from braunschweig.calibration import srv_plan_structure as _srv_plan_structure
from braunschweig.calibration import srv_student_reference as R
from braunschweig.calibration import student_parameters as _student_parameters
from braunschweig.popsim import zensus_employment_age as _zensus_employment_age
from braunschweig.synthesis.day_absence import absence as _day_absence
from braunschweig.synthesis.portal_trips import config_keys as _portal_config_keys

LOGGER = logging.getLogger("braunschweig.analysis.synthesis.students_vs_srv")
_LOG_TAG = "[students_vs_srv]"

KEY_SUBDIR = "students_vs_srv_output_subdir"
DEFAULT_SUBDIR = "analysis/students_vs_srv"
#: Largest share of adult students without a home Kreis the stage accepts (in [0, 1]); above
#: it ``model_student_frame`` raises. Same value and reason as the sibling's
#: ``plan_structure_max_unmatched_home_share``: a high share means a broken VG250 / household
#: join, not home-less students.
KEY_MAX_UNMATCHED_HOME_SHARE = "students_vs_srv_max_unmatched_home_share"
DEFAULT_MAX_UNMATCHED_HOME_SHARE = _sibling.DEFAULT_MAX_UNMATCHED_HOME_SHARE

#: Re-used from the sibling stage, so one config value gates every consumer of the draw.
KEY_DAY_ABSENCE_ENABLED = _sibling.KEY_DAY_ABSENCE_ENABLED
DEFAULT_DAY_ABSENCE_ENABLED = _sibling.DEFAULT_DAY_ABSENCE_ENABLED
ABSENCE_STAGE = _sibling.ABSENCE_STAGE
KEY_PORTAL_ENABLED = _sibling.KEY_PORTAL_ENABLED
DEFAULT_PORTAL_ENABLED = _sibling.DEFAULT_PORTAL_ENABLED

COMPLETED_DONOR_STAGE = "braunschweig.popsim.completed_donor"
#: File names of the two traces the completed-donor stage writes into its cache directory
#: (``braunschweig.popsim.completed_donor.WEEKEND_TRACE_FILE`` / ``DIARY_TRACE_FILE``). Literals,
#: because importing that stage module would make this token hash its whole popsim import
#: closure (ADR-0194). ``tests/test_students_vs_srv.py`` writes the traces under the
#: stage's constants, so a rename there fails the stage test (``trace_available``).
WEEKEND_TRACE_FILE = "weekend_plan_match_trace.parquet"
DIARY_TRACE_FILE = "diary_plan_match_trace.parquet"
#: The committed SrV tables this stage reads, below ``<data_path>/braunschweig/srv/``.
SRV_TABLES = (R.PARAMETER_TABLE, R.STUDENT_SHARE_TABLE, R.YOUNG_ADULT_STATUS_TABLE)
#: Columns of the SrV tables that hold codes or labels and must stay strings (a code read as
#: a number loses its leading zero, a label like ``0`` would no longer match the vocabulary).
_SRV_STRING_COLUMNS = {R.PARAMETER_TABLE: {"label": str},
                       R.STUDENT_SHARE_TABLE: {"code": str},
                       R.YOUNG_ADULT_STATUS_TABLE: {"code": str}}
ZENSUS_SUBDIR = ("braunschweig", "popsim")

STATUS = "measurement, not validation"
#: Exclusion / fallback counts and rates, written into the stage's own cache directory.
REPORT_FILE = "students_vs_srv_report.json"

OUTPUT_FILES = {"student_level": "student_level_by_kreis.csv",
                "young_adult_status": "young_adult_status_by_kreis.csv",
                "student_work_class": "student_work_class.csv",
                "student_behaviour": "student_behaviour_vs_srv.csv",
                "plan_source": "student_plan_source.csv"}

#: Repository root, for the git commit in ``provenance.json`` (this file sits at
#: <root>/braunschweig/analysis/synthesis/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

#: synpp hashes only THIS module's source; the measurement functions and every module this
#: stage imports must be in the token, or an edit there would serve a stale cached report.
_HELPER_MODULES = (M, _student_parameters, _plan_structure, _srv_plan_structure, R, _sibling,
                   _zensus_employment_age, _day_absence, _portal_config_keys, run_provenance,
                   json_output)
#: Imported inside execute()/validate() (``spatial`` pulls geopandas and the VG250 access), then
#: the rest of the import closure: modules the helpers import, whose code this stage runs
#: without importing it itself (scripts/audit_synpp_helper_hash.py, ADR-0136). Most of it comes
#: from ``student_measurement``, which reuses the popsim attribute and plan-match definitions.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.analysis.spatial",
    "braunschweig.data.bbsr.regiostar",
    "braunschweig.calibration.commute_day_state_reference",
    "braunschweig.calibration.mid_student_reference",
    "braunschweig.calibration.srv_absence",
    "braunschweig.calibration.srv_distance_targets",
    "braunschweig.calibration.srv_participation_universe",
    "braunschweig.calibration.student_behaviour_labels",
    "braunschweig.calibration.student_control_evidence",
    "braunschweig.data.mid.reference_tables",
    "braunschweig.data.mid.status_by_hhtype",
    "braunschweig.gravity.friction",
    "braunschweig.popsim.attributes",
    "braunschweig.popsim.chain_matching",
    "braunschweig.popsim.closure_dwell",
    "braunschweig.popsim.day_type",
    "braunschweig.popsim.diary_facts",
    "braunschweig.popsim.diary_plan_match",
    "braunschweig.popsim.escort_pairing",
    "braunschweig.popsim.kreis_attribute_control",
    "braunschweig.popsim.mid.participation",
    "braunschweig.popsim.missing",
    "braunschweig.popsim.plan_validation",
    "braunschweig.popsim.sampling",
    "braunschweig.popsim.seed",
    "braunschweig.popsim.time_imputation",
    "braunschweig.popsim.trips",
    "braunschweig.popsim.weekend_plan_match",
    "braunschweig.population.socioprofessional_class",
    "data.hts.entd.cleaned",
    "data.hts.hts",
    "synthesis.population.matched",
)


def srv_table_path(data_path: str, name: str) -> str:
    """Absolute path of a committed SrV table below ``data_path``."""
    return os.path.join(_sibling.srv_directory(data_path), name)


def zensus_path(data_path: str) -> str:
    """Path of the Zensus 2022 employed-by-age table below ``data_path`` (local-only file)."""
    return os.path.join(str(data_path), *ZENSUS_SUBDIR,
                        _zensus_employment_age.KREIS_EMPLOYED_BY_AGE_FILE)


def _require_file(path: str, what: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError("%s not found: %s" % (what, path))


def _input_digests(data_path: str) -> dict:
    """sha256 of the three committed SrV tables (CRLF-neutral, as git stores them) and of the
    local Zensus file; a missing file raises ``FileNotFoundError``."""
    digests = {}
    for name in SRV_TABLES:
        path = srv_table_path(data_path, name)
        _require_file(path, "committed SrV table %s (regenerate with "
                            "scripts/extract_srv_student_reference.py)" % name)
        digests[name] = synpp_tokens.text_file_sha256(path)
    path = zensus_path(data_path)
    _require_file(path, "Zensus employed-by-age table (regenerate with "
                        "scripts/extract_zensus2022_employed_by_age.py)")
    digests[_zensus_employment_age.KREIS_EMPLOYED_BY_AGE_FILE] = synpp_tokens.file_sha256(path)
    return digests


def validate(context):
    """synpp validation token: the helper modules' source plus ``files``: the VG250 / RegioStaR
    digests of ``spatial`` and the sha256 of the three committed SrV tables and the Zensus
    file, so a changed reference never serves a cached comparison."""
    token = synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES, stage_label=__name__ + ".validate()")
    from braunschweig.analysis import spatial  # a deferred helper, imported by source_token
    files = spatial.input_file_digests(strict=True)
    files.update(_input_digests(context.config("data_path")))
    token["files"] = files
    return token


def configure(context):
    context.stage("synthesis.population.enriched")
    portal_on = bool(context.config(KEY_PORTAL_ENABLED, DEFAULT_PORTAL_ENABLED))
    context.stage(_portal_config_keys.final_view_trips_stage(portal_on), alias="trips")
    # Refused here, at DAG time, not in execute(): a run without the draw must stop before it
    # builds the population, not after it (fail early).
    if not bool(context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED)):
        raise ValueError(
            "%s %s is false: the student measurement compares present persons with the SrV "
            "participation universe, which needs the general day-absence draw; enable it or drop "
            "this stage from the run list (no fallback to an all-present model side)"
            % (_LOG_TAG, KEY_DAY_ABSENCE_ENABLED))
    context.stage(ABSENCE_STAGE)
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("braunschweig.popsim.completed_donor")  # a literal: the hash audit reads it
    context.config("output_path")
    context.config("data_path")
    context.config("sampling_rate")
    context.config(KEY_SUBDIR, DEFAULT_SUBDIR)
    context.config(KEY_MAX_UNMATCHED_HOME_SHARE, DEFAULT_MAX_UNMATCHED_HOME_SHARE)


# --------------------------------------------------------------------------- inputs

def load_srv_tables(data_path: str) -> dict:
    """The three committed SrV tables, codes and labels as strings; a missing one raises."""
    tables = {}
    for name in SRV_TABLES:
        path = srv_table_path(data_path, name)
        _require_file(path, "committed SrV table %s" % name)
        tables[name] = pd.read_csv(path, comment="#", dtype=_SRV_STRING_COLUMNS[name])
        LOGGER.info("%s SrV table %s: %d rows", _LOG_TAG, path, len(tables[name]))
    return tables


def _empty_trace(columns) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="int64" if column in M.SEED_KEY else object)
                         for column in columns})


def load_trace(directory: str, file_name: str, columns, name: str) -> tuple[pd.DataFrame, dict]:
    """One trace file of the completed-donor stage and its provenance entry.

    A missing file is not an error (the trace is written only while its match is on): an empty
    frame stands in, the person paths it would resolve stay ``unresolved`` and the warning says
    so; ``trace_available`` records it.
    """
    path = os.path.join(str(directory), file_name)
    # The entry names the file inside the stage's cache directory, never the absolute cache
    # path (it would carry the run machine's home directory into the outputs).
    entry = {"stage": COMPLETED_DONOR_STAGE, "file": file_name}
    if not os.path.isfile(path):
        LOGGER.warning("%s %s trace missing (%s): its match paths are reported as unresolved",
                       _LOG_TAG, name, path)
        return _empty_trace(columns), {**entry, "trace_available": False, "sha256": None}
    frame = pd.read_parquet(path)
    LOGGER.info("%s %s trace %s: %d rows", _LOG_TAG, name, path, len(frame))
    return frame, {**entry, "trace_available": True,
                   "sha256": synpp_tokens.file_sha256(path), "n_rows": int(len(frame))}


def require_one_home_row_per_household(homes_with_ars5: pd.DataFrame) -> None:
    """Raise ``ValueError`` naming the count and the first household ids if a household has more
    than one home row: a duplicated home is an upstream defect (a household listed twice by the
    home-location stage, or a home point the VG250 join of ``spatial.assign_geographies``
    matched to two Kreise), and keeping one of the rows would assign a Kreis by row order (the
    plan-structure comparison of the same stage, ``plan_structure.harmonise_model``, keeps the
    first row with a warning; the stage refuses instead, per the rule that a duplicated join
    key raises). ``execute`` calls this before any consumer of the homes."""
    duplicated = homes_with_ars5.loc[homes_with_ars5["household_id"].duplicated(keep=False),
                                     "household_id"]
    if len(duplicated):
        ids = sorted(duplicated.unique().tolist())
        raise ValueError(
            "%s home locations with Kreis (synthesis.population.spatial.home.locations joined to "
            "VG250): %d household(s) with more than one home row (%d rows), e.g. household_id "
            "%s; each household needs exactly one home row to get a home Kreis"
            % (_LOG_TAG, len(ids), len(duplicated), ids[:5]))


def persons_with_kreis(persons: pd.DataFrame, homes_with_ars5: pd.DataFrame,
                       absent_ids: set) -> pd.DataFrame:
    """The enriched persons with their home Kreis (``kreis``, the ``ars5`` of the household,
    NaN without one) and ``present`` (not in ``absent_ids``), the frame the level and status
    tables read. Households join m:1 on ``household_id``; a duplicated home raises
    (:func:`require_one_home_row_per_household`)."""
    require_one_home_row_per_household(homes_with_ars5)
    homes = homes_with_ars5[["household_id", _plan_structure.KREIS_COLUMN]].rename(
        columns={_plan_structure.KREIS_COLUMN: "kreis"})
    frame = persons[["person_id", "household_id", "age", "P_TAET", "employed"]].merge(
        homes, on="household_id", how="left", validate="m:1")
    frame["kreis"] = frame["kreis"].astype(object).where(frame["kreis"].notna(), np.nan)
    frame["present"] = ~frame["person_id"].isin(absent_ids)
    return frame[list(M.PERSON_KREIS_COLUMNS)]


# --------------------------------------------------------------------------- report

def _fmt(value, decimals=4) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{value:.{decimals}f}"


def summary_markdown(tables: dict, provenance: dict) -> str:
    """The human-readable report; every number in it is in a CSV or ``provenance.json``."""
    parameters = provenance["parameters"]
    frame = provenance["diagnostics"]["student_frame"]
    behaviour = tables["student_behaviour"]
    compared = behaviour["difference_in_se"].notna()
    beyond = behaviour["beyond_two_se"].fillna(False).astype(bool)
    level = tables["student_level"].set_index("code")
    region = level.loc[M.REGION_CODE]
    lines = [
        "# University students: synthetic population vs SrV 2023",
        "",
        f"A {STATUS}: the tables below state differences in SrV standard errors; no "
        "synthesis stage reads them.",
        "",
        "Parameters: " + ", ".join(f"{key}={value}" for key, value in parameters.items()),
        f"Generated at: {provenance['generated_at']}",
        f"Pipeline commit: {provenance['pipeline_commit']}",
        "SrV tables: " + ", ".join(provenance["inputs"]["srv_tables"]),
        "",
        "## Universe and scope",
        "",
        M.REGION_SCOPE_NOTE,
        "",
        f"Adult students: {frame['n_adult_students_present']} present and "
        f"{frame['n_adult_students_absent']} away all day (general day-absence draw); "
        f"{frame['n_adult_students_no_kreis']} without a home Kreis. Model counts are sample "
        f"counts at sampling_rate={parameters['sampling_rate']}, unweighted.",
        "Shares are over PRESENT persons (the SrV universe: persons at home at some point of the "
        "reporting day); away_all_day is over all students.",
        "",
        "An SrV standard error of 0 (a share of 0 or 1) gives a NaN difference_in_se and an NA "
        "beyond_two_se: no finite distance in standard errors exists there.",
        "",
        "## Files",
        "",
        f"* {OUTPUT_FILES['student_behaviour']}: {len(behaviour)} parameter cells; "
        f"{int(compared.sum())} with a difference in SrV standard errors, of which "
        f"{int(beyond.sum())} beyond two standard errors; "
        f"{provenance['n_missing_srv_cells']} cell(s) missing from the SrV table.",
        f"* {OUTPUT_FILES['student_level']}: region {M.REGION_CODE} student share of the present "
        f"adults model {_fmt(region['model_student_share_adults'])} vs SrV "
        f"{_fmt(region['srv_student_share_adults'])} (difference "
        f"{_fmt(region['difference_in_se_adults'], 2)} SrV standard errors).",
        "  srv_students_weighted_persons counts only the SrV students at home on the reporting "
        "day (the participation universe), so its like-for-like model count is "
        "model_present_students_adults, not the count of all model students; the SrV students "
        "away all day are added back in Phase 2 (design A11).",
        f"* {OUTPUT_FILES['young_adult_status']}: student / employed / other among the 20 to 29 "
        "year olds, model against SrV, with the Zensus 2022 employed count on the employed rows.",
        f"* {OUTPUT_FILES['student_work_class']}: the adult students by employed flag and "
        "employment_status class of the work control.",
        f"* {OUTPUT_FILES['plan_source']}: own diary or re-drawn plan, match path and donor "
        "activity; share_of_students over all adult students, the leg shares "
        "(share_with_education_leg, share_with_work_leg) over the present students of the row. "
        f"Trace files available: {provenance['plan_source']['trace_available']}.",
        "",
    ]
    return "\n".join(lines) + "\n"


def _rate(count, total) -> float:
    return float(count) / float(total) if total else float("nan")


def exclusion_report(provenance: dict) -> dict:
    """The stage's drop / exclusion / fallback counts and their rates, for
    :data:`REPORT_FILE` in the stage's cache directory (coding standards "Validation and quality
    control"): the diagnostics dicts of ``provenance.json`` plus the trace availability, without
    any path. ``rates`` puts each exclusion over the base it was counted on; NaN (``null``) on an
    empty base."""
    diagnostics = provenance["diagnostics"]
    frame, level = diagnostics["student_frame"], diagnostics["student_level"]
    plan = diagnostics["plan_source"]
    n_students = (frame["n_adult_students_present"] + frame["n_adult_students_absent"])
    traces = provenance["plan_source"]["traces"]
    return {
        "stage": __name__,
        "status": STATUS,
        "sampling_rate": provenance["parameters"]["sampling_rate"],
        "trace_available": {name: entry["trace_available"] for name, entry in traces.items()},
        "n_missing_srv_cells": provenance["n_missing_srv_cells"],
        "rates": {
            "adult_students_no_home_kreis": frame["unmatched_home_share"],
            "adult_students_absent": _rate(frame["n_adult_students_absent"], n_students),
            "persons_missing_age": _rate(level["n_missing_age"], level["n_persons"]),
            "adults_unreadable_p_taet": _rate(level["n_adults_unreadable_p_taet"],
                                              level["n_adults_with_age"]),
            "adults_no_home_kreis": _rate(level["n_adults_no_home_kreis"],
                                          level["n_adults_with_age"]
                                          - level["n_adults_unreadable_p_taet"]),
            "plan_source_unresolved": _rate(plan["n_unresolved"], plan["n_students"]),
            "plan_source_redrawn_unresolved": _rate(plan["n_redrawn_unresolved"],
                                                    plan["n_redrawn"]),
            "plan_source_redrawn_donor_unknown": _rate(plan["n_redrawn_donor_unknown"],
                                                       plan["n_redrawn"]),
        },
        "diagnostics": diagnostics,
    }


def write_outputs(directory: str, tables: dict, provenance: dict) -> None:
    """Write the five CSVs (header-free, LF, ``%.10g``), ``provenance.json`` (strict JSON) and
    ``summary.md`` into ``directory`` (created if absent)."""
    os.makedirs(directory, exist_ok=True)
    for key, file_name in OUTPUT_FILES.items():
        tables[key].to_csv(os.path.join(directory, file_name), index=False,
                           float_format="%.10g", lineterminator="\n")
    json_output.write_json(os.path.join(directory, "provenance.json"), provenance)
    with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8",
              newline="\n") as handle:
        handle.write(summary_markdown(tables, provenance))


# --------------------------------------------------------------------------- stage

def execute(context):
    # VG250 access only at run time (the module import pulls geopandas + the archive).
    from braunschweig.analysis import spatial

    data_path = context.config("data_path")
    output_path = context.config("output_path")
    subdir = context.config(KEY_SUBDIR)
    sampling_rate = float(context.config("sampling_rate"))
    max_unmatched_home_share = float(context.config(KEY_MAX_UNMATCHED_HOME_SHARE))
    portal_on = bool(context.config(KEY_PORTAL_ENABLED))
    out_dir = os.path.join(output_path, subdir)

    # Inputs that can be missing are checked first, before any expensive work.
    digests = _input_digests(data_path)
    srv = load_srv_tables(data_path)
    zensus = _zensus_employment_age.read_kreis_employed_by_age(zensus_path(data_path))

    persons = context.stage("synthesis.population.enriched")
    trips = context.stage("trips")
    absence = context.stage(ABSENCE_STAGE)["absence"]
    df_home = context.stage("synthesis.population.spatial.home.locations")
    donor = context.stage(COMPLETED_DONOR_STAGE)
    donor_dir = context.path(COMPLETED_DONOR_STAGE)
    LOGGER.info("%s sampling_rate=%s, max_unmatched_home_share=%s, portal_layer_enabled=%s; "
                "writing to %s", _LOG_TAG, sampling_rate, max_unmatched_home_share, portal_on,
                out_dir)

    homes = spatial.assign_geographies(df_home[["household_id", "geometry"]])
    # Before model_student_frame: its plan-structure comparison would otherwise log "first row
    # kept" for a duplicated home that this stage is about to refuse.
    require_one_home_row_per_household(homes)
    students, frame_diagnostics = M.model_student_frame(
        persons, trips, homes, absence, max_unmatched_home_share=max_unmatched_home_share)
    behaviour = M.compare_parameters(students, srv[R.PARAMETER_TABLE])
    n_missing_srv_cells = M.count_missing_srv_cells(behaviour)

    absent_ids = _day_absence.absent_person_ids(absence)
    with_kreis = persons_with_kreis(persons, homes, absent_ids)
    level, level_diagnostics = M.student_level_table(
        with_kreis, srv[R.STUDENT_SHARE_TABLE], sampling_rate)
    status, status_diagnostics = M.young_adult_status_table(
        with_kreis, srv[R.YOUNG_ADULT_STATUS_TABLE], zensus, sampling_rate)
    work_class, work_class_diagnostics = M.student_work_class_table(persons)

    weekend, weekend_entry = load_trace(donor_dir, WEEKEND_TRACE_FILE, M.WEEKEND_TRACE_COLUMNS,
                                        "weekend")
    diary, diary_entry = load_trace(donor_dir, DIARY_TRACE_FILE, M.DIARY_TRACE_COLUMNS, "diary")
    plan_source, plan_diagnostics = M.plan_source_table(students, persons, donor.persons,
                                                        weekend, diary)

    tables = {"student_level": level, "young_adult_status": status,
              "student_work_class": work_class, "student_behaviour": behaviour,
              "plan_source": plan_source}
    zensus_file = _zensus_employment_age.KREIS_EMPLOYED_BY_AGE_FILE
    provenance = {
        "status": STATUS,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "pipeline_commit": run_provenance.git_commit(_REPO_ROOT),
        "parameters": {
            "output_subdir": subdir,
            "sampling_rate": sampling_rate,
            "max_unmatched_home_share": max_unmatched_home_share,
            "day_absence_enabled": True,
            "portal_layer_enabled": portal_on,
            "trips_stage": _portal_config_keys.final_view_trips_stage(portal_on),
        },
        "inputs": {
            "paths_relative_to": "data_path",
            # Paths relative to data_path: no absolute path enters an output file.
            "srv_tables": {name: {"path": "/".join((*_sibling.SRV_SUBDIR, name)),
                                  "sha256": digests[name], "n_rows": int(len(srv[name]))}
                           for name in SRV_TABLES},
            "zensus_employed_by_age": {"path": "/".join((*ZENSUS_SUBDIR, zensus_file)),
                                       "sha256": digests[zensus_file]},
        },
        "region_scope": M.REGION_SCOPE_NOTE,
        "n_missing_srv_cells": n_missing_srv_cells,
        "plan_source": {
            "trace_available": bool(weekend_entry["trace_available"]
                                    and diary_entry["trace_available"]),
            "traces": {"weekend": weekend_entry, "diary": diary_entry},
        },
        "diagnostics": {
            "student_frame": frame_diagnostics,
            "student_level": level_diagnostics,
            "young_adult_status": status_diagnostics,
            "student_work_class": work_class_diagnostics,
            "plan_source": plan_diagnostics,
        },
    }
    write_outputs(out_dir, tables, provenance)
    report_path = os.path.join(context.path(), REPORT_FILE)
    json_output.write_json(report_path, exclusion_report(provenance))
    LOGGER.info("%s exclusion report: %s", _LOG_TAG, report_path)
    LOGGER.info("%s wrote %d tables, summary.md and provenance.json to %s (%s)", _LOG_TAG,
                len(tables), out_dir, STATUS)
    return tables
