"""Extract the twelve committed SrV 2023 university-student aggregates (issue #140, Phases 0, 1, 3).

Reads the LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata (persons, legs,
households; cp1252, semicolon, decimal comma) and writes twelve small aggregate tables to
``--out-dir`` (default ``eqasim-data/data/braunschweig/srv``). Phase 0:

    srv2023_student_share_by_kreis.csv               student share of adults / 20-29, presence
    srv2023_education_status_by_age.csv              status of adults with an education leg
    srv2023_student_education_distance_by_kreis.csv  home-to-education distance bands, adult students
    srv2023_student_behaviour.csv                    behaviour cells, adults and students
    srv2023_student_comparison.csv                   car / licence / season ticket, students vs others

Phase 1 (needs ``--sites``, the local-only site table R1 ``lsn_university_sites.csv``: the
``site_kreis_ars5`` of its rows with ``scope == "local"`` are the site Kreise and its sha256
goes into the header of every new file):

    srv2023_student_parameters.csv                   parameter cells of the adult students, away rows
    srv2023_young_adult_status_by_kreis.csv          status of the young adults by Kreis
    srv2023_student_post_matsim_reference.csv        modes, daily distance and travel time
    srv2023_student_education_destination.csv        destination Kreis of the first education leg

All definitions live in ``braunschweig.calibration.srv_student_reference`` (and the shared
label vocabulary in ``braunschweig.calibration.student_behaviour_labels``); this script owns
only the raw I/O, the CLI and the provenance headers. Each written file carries an
``# Exclusions:`` line with the builder's counts, so the exclusion counts live IN the file.

Usage (eqasim env, from a worktree; the raw directory is local-only and always passed):
    python scripts/extract_srv_student_reference.py --raw <srv2023_raw dir> \
        --sites <lsn_university_sites.csv> \
        --out-dir eqasim-data/data/braunschweig/srv --source-commit <sha>
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import srv_student_reference as R  # noqa: E402
from braunschweig.calibration import srv_plan_structure as PS  # noqa: E402
from braunschweig.calibration import student_behaviour_labels as L  # noqa: E402
from braunschweig.calibration import student_control_evidence as CE  # noqa: E402
from braunschweig.calibration import student_parameters as SP  # noqa: E402
from braunschweig.calibration.srv_distance_targets import (  # noqa: E402
    EDUCATION_PURPOSES, WOLFSBURG_KREIS, ZGB_KREISE, select_person_observations)
# The raw SrV read format (cp1252, semicolon, decimal comma) has one home; reuse it.
from scripts.extract_srv_participation_universe import CSV_READ_KWARGS  # noqa: E402

OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"

PERSONS_FILE = "SrV2023_Personen.csv"
LEGS_FILE = "SrV2023_Wege.csv"
HOUSEHOLDS_FILE = "SrV2023_Haushalte.csv"

# Leg columns: the behaviour cells (R.LEG_COLUMNS) plus what select_person_observations reads.
TRIP_COLUMNS = ["V_ZWECK", "E_START_ZWECK", "V_START_LAGE", "V_ZIEL_LAGE", "V_START_AGS",
                "V_ZIEL_AGS", "GIS_LAENGE", "GIS_LAENGE_GUELTIG", "GEWICHT_W_ZENSUS",
                "REGIOSTAR7"]

SITE_REQUIRED_COLUMNS = ["site_kreis_ars5", "scope"]
SITE_SCOPE_LOCAL = "local"


def _union(*column_lists) -> list:
    """Columns of several lists, first occurrence order, no duplicates (a usecols list)."""
    return list(dict.fromkeys(column for columns in column_lists for column in columns))


# The Phase 0 builders read their own columns; Phase 1 adds the plan-structure harmonisation, the
# parameter table, the away rows, the post-MATSim values and the destination reference.
PERSON_READ_COLUMNS = _union(R.PERSON_COLUMNS, PS.PERSON_COLUMNS, R.PARAMETER_PERSON_COLUMNS,
                             R.AWAY_PERSON_COLUMNS, R.POST_MATSIM_PERSON_COLUMNS)
LEG_READ_COLUMNS = _union(R.LEG_COLUMNS, TRIP_COLUMNS, PS.TRIP_COLUMNS,
                          R.POST_MATSIM_LEG_COLUMNS, R.DESTINATION_LEG_COLUMNS)
HOUSEHOLD_READ_COLUMNS = _union(R.HOUSEHOLD_COLUMNS, PS.HOUSEHOLD_COLUMNS,
                                R.PARAMETER_HOUSEHOLD_COLUMNS)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_srv_student_reference")

_UNIVERSE_UNITS_NOTE = (
    "(n_persons_total is the raw Personen-file row count; missing_weight is measured on it, "
    "away_from_home on the persons surviving the weight filter (n_students_away_from_home / "
    "n_adult_students_away_from_home: the students / adult students among them), missing_kreis "
    "on the persons surviving the away-from-home filter; n_universe is the resulting person "
    "count; "
    "n_missing_age and n_unreadable_employment_code are measured on n_universe, and "
    "n_excluded_unreadable_employment_code is the same persons removed from this table; "
    "n_legs_total and n_legs_missing_purpose, where present, are raw Wege-file row counts)")


def _require_columns(path: Path, required: list) -> None:
    """Fail early with the file name and the missing column(s) rather than a bare KeyError."""
    header = pd.read_csv(path, nrows=0, **CSV_READ_KWARGS)
    missing = [column for column in required if column not in header.columns]
    if missing:
        raise ValueError("%s: missing required column(s) %s" % (path, missing))


PHASE3_TABLE_LABEL = "Phase 3 reference table"


def _exclusions(*dicts) -> str:
    merged = {}
    for diagnostics in dicts:
        merged.update(diagnostics)
    return "# Exclusions: " + ", ".join("%s=%s" % (key, value) for key, value in merged.items())


def _common_header(table_name: str, source_commit: str,
                   table_label: str = "Phase 0 reference table R5") -> list:
    """Provenance lines all the tables share (source, code state, universe, weight, caveats).

    ``table_label`` names the phase / reference table in the ``# Table:`` line.
    """
    return [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig scientific-use",
        "#   microdata (local-only, see srv2023_raw/README.md; files %s, %s, %s),"
        % (PERSONS_FILE, LEGS_FILE, HOUSEHOLDS_FILE),
        "#   generated by scripts/extract_srv_student_reference.py on %s."
        % dt.date.today().isoformat(),
        "# Code state: eqasim-bs %s, modules braunschweig/calibration/srv_student_reference.py"
        % source_commit,
        "#   and braunschweig/calibration/student_behaviour_labels.py (definitions live there).",
        "# Table: %s (issue #140, %s). A measurement, not a validation." % (table_name, table_label),
        "# Universe: srv_participation_universe.prepare_universe_persons -- valid non-negative",
        "#   GEWICHT_P_ZENSUS, persons at home at some point on the (Tuesday-Thursday) reporting",
        "#   day, household Kreis inside the 8 ZGB Kreise. Persons whose V_ERW is missing or",
        "#   negative (-8 not surveyed, -10 implausible) are EXCLUDED from this table and counted",
        "#   (never classed 'not a student'). Student = V_ERW %d; adult = V_ALTER >= %d."
        % (R.STUDENT_V_ERW, L.MIN_ADULT_AGE),
        "# Weight: GEWICHT_P_ZENSUS (person expansion to Zensus 2022; never the stratum-internal",
        "#   GEWICHT_P, ADR-0055), unless the table says otherwise.",
        "# Standard errors: se_share = sqrt(share * (1 - share) / n_effective) with the Kish",
        "#   effective sample size n_effective = (sum w)^2 / sum w^2 over exactly the rows of the",
        "#   share's base. A share is NaN, never 0.0, when its base is empty.",
        "# Kreis: first 5 digits of the zero-padded 8-digit household AGS, joined via HHNR (never",
        "#   the design stratum ST_CODE); code-to-name mapping braunschweig.analysis.spatial.ZGB8.",
        "# Wolfsburg (%s) is NOT surveyed by SrV: where a table has Kreis rows its row is"
        % WOLFSBURG_KREIS,
        "#   emitted with zero counts and NaN shares, never dropped or backfilled (issue #405).",
        "# Coverage caveat: stratified PSU design over ~44 selected municipalities; per-Kreis",
        "#   rows represent the covered municipalities only (ASSUMPTION-grade for the full Kreis).",
    ]


def _share_header(diagnostics: dict, universe_diagnostics: dict, source_commit: str) -> list:
    return _common_header(R.STUDENT_SHARE_TABLE, source_commit) + [
        "# Rows: one kreis row per ZGB Kreis %s + one %s row over their union."
        % (list(ZGB_KREISE), R.REGION_CODE),
        "# Columns: n_adults / n_students (unweighted adults and adult students),",
        "#   student_share_adults (weighted), n_effective_adults, se_student_share_adults; the",
        "#   same for ages 20-29 (V_ALTER 20..29 inclusive); students_weighted_persons = weight",
        "#   sum of the adult students. presence_share (%s row only, NaN on kreis rows): weighted"
        % R.REGION_CODE,
        "#   share of the students of ALL ages with at least one leg with E_ZWECK_9 in %s on the"
        % sorted(R.EDUCATION_E_ZWECK_9),
        "#   reporting day, with n_effective_presence and se_presence_share.",
        "# Every share here excludes persons away from home all day (E_ANZ_WEGE -7, outside the",
        "#   universe; the students among them are n_students_away_from_home below).",
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _status_header(diagnostics: dict, universe_diagnostics: dict, source_commit: str) -> list:
    return _common_header(R.EDUCATION_STATUS_TABLE, source_commit) + [
        "# Region level only (all surveyed Kreise together).",
        "# Base: persons with at least one leg with E_ZWECK_9 in %s, by age band %s (V_ALTER,"
        % (sorted(R.EDUCATION_E_ZWECK_9), [band for band, _, _ in R.STATUS_AGE_BANDS]),
        "#   bounds inclusive; persons under 18 or without an age are in no band, counted as",
        "#   n_with_education_leg_in_no_adult_band).",
        "# Status precedence: student (V_ERW %d), pupil (%d), apprentice (%d, although %d is in"
        % (R.STUDENT_V_ERW, R.PUPIL_V_ERW, R.APPRENTICE_V_ERW, R.APPRENTICE_V_ERW),
        "#   srv_plan_structure.EMPLOYED_V_ERW), employed (the rest of EMPLOYED_V_ERW), other.",
        "# Columns: age_band, status, n_unweighted, share (weighted within the age band).",
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _distance_header(observation_diagnostics: dict, selection_log: dict,
                     universe_diagnostics: dict, n_bootstrap: int, seed: int,
                     source_commit: str) -> list:
    return _common_header(R.EDUCATION_DISTANCE_TABLE, source_commit) + [
        "# Observations: srv_distance_targets.select_person_observations with V_ZWECK purposes",
        "#   %s (ADR-0102: per person the first home-to-education trip, else the first"
        % list(EDUCATION_PURPOSES),
        "#   education-to-home trip; GIS-routed km where GIS_LAENGE_GUELTIG > 0), kept for the",
        "#   ADULT students (V_ALTER >= %d) of the universe above, the same persons as the"
        % L.MIN_ADULT_AGE,
        "#   share, behaviour and comparison tables (select_student_observations); students",
        "#   under %d or without a valid age are counted as n_observations_students_not_adult."
        % L.MIN_ADULT_AGE,
        "# Weight of THIS table: GEWICHT_W_ZENSUS of the selected trip, as",
        "#   select_person_observations returns it (the convention of the committed SrV",
        "#   distance tables).",
        "# Bands: km edges %s, LEFT-inclusive, labels %s."
        % (list(L.STUDENT_DISTANCE_EDGES_KM), list(L.STUDENT_DISTANCE_LABELS)),
        "# emd_noise_95: 95th percentile of the band EMD between a bootstrap resample of the",
        "#   row's persons and the row itself (srv_distance_targets.bootstrap_emd_noise_floor,",
        "#   n_bootstrap=%d, seed=%d); 0.0 for a single person, NaN for none." % (n_bootstrap, seed),
        "# Rows: one kreis row per ZGB Kreis + one %s row over their union." % R.REGION_CODE,
        # The selection log shares key names with the universe diagnostics (n_missing_age), so
        # its keys are prefixed rather than allowed to overwrite them.
        _exclusions(universe_diagnostics,
                    {"selection_%s" % key: value for key, value in selection_log.items()},
                    observation_diagnostics)
        + " (selection_* = select_person_observations keys, units as in"
        " scripts/extract_srv_primary_distance_targets.py; n_observations_* count selected"
        " persons before / after the adult-student restriction)",
    ]


def _behaviour_header(diagnostics: dict, universe_diagnostics: dict,
                      source_commit: str) -> list:
    return _common_header(R.BEHAVIOUR_TABLE, source_commit) + [
        "# Region level only. Label vocabulary: student_behaviour_labels.CONTROLS (shared with",
        "#   the MiD seed side). Universes: %s = adults with a readable V_ERW, non-students"
        % L.UNIVERSE_ADULTS,
        "#   labelled %s; %s = the adult students alone (student labels renormalised)."
        % (L.NOT_STUDENT, L.UNIVERSE_STUDENTS),
        "# Definitions: education / work leg = E_ZWECK_9 in %s / %s; trip class from E_ANZ_WEGE"
        % (sorted(R.EDUCATION_E_ZWECK_9), sorted(R.WORK_E_ZWECK_9)),
        "#   (right-inclusive edges %s); arrival = V_ANKUNFT_STUNDE of the first education leg"
        % list(L.TRIP_CLASS_EDGES),
        "#   by V_BEGINN_STUNDE then WNR (right-inclusive hour edges %s); flatrate = E_OEV_FK in"
        % list(L.ARRIVAL_HOUR_EDGES),
        "#   %s, -8 (not surveyed) kept as no flatrate." % sorted(R.FLATRATE_E_OEV_FK),
        "# Per-control exclusions: a student with an unreadable start or arrival hour leaves the",
        "#   arrival control only, a student with E_OEV_FK -10 the season_ticket control only, in",
        "#   both universes; counted below.",
        "# UNIVERSE GAP to the MiD mirror mid2023_student_behaviour_seed.csv: persons away from",
        "#   home all day (E_ANZ_WEGE -7) are outside this universe, MiD keeps them; the adult",
        "#   students dropped this way are n_adult_students_away_from_home below.",
        "# Columns: %s; n_effective is the Kish size of the control's base in that universe."
        % ", ".join(L.BEHAVIOUR_COLUMNS),
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE
        + " (n_students and n_students_unreadable_* count adult students; "
        "n_students_away_from_home counts students of all ages)",
    ]


def _comparison_header(diagnostics: dict, universe_diagnostics: dict,
                       source_commit: str) -> list:
    return _common_header(R.COMPARISON_TABLE, source_commit) + [
        "# Region level only. Variables %s, each code as delivered (SrV codebook; missing"
        % list(R.COMPARISON_VARIABLES),
        "#   codes kept as their own rows), for the groups %s (adult students) and %s (other"
        % (R.GROUP_STUDENT, R.GROUP_OTHER_ADULT),
        "#   adults with a readable V_ERW). Every code seen among the adults is emitted for both.",
        "# Columns: variable, group, code, n_unweighted, share (weighted within the group).",
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _sites_line(site_kreise: frozenset, sites_sha256: str) -> list:
    return ["# Site Kreise (R1 local-only site table, rows with scope == %s): %s; table sha256 %s."
            % (SITE_SCOPE_LOCAL, sorted(site_kreise), sites_sha256)]


def _phase1_header(table_name: str, source_commit: str, sites: list,
                   table_label: str = "Phase 1 reference table") -> list:
    """The shared provenance block of the Phase 1 / Phase 3 tables plus the site line.

    ``table_label`` is the phase in the ``# Table:`` line; the Phase 3 tables pass their own.
    """
    return _common_header(table_name, source_commit, table_label) + sites


def _parameter_header(diagnostics: dict, universe_diagnostics: dict, source_commit: str,
                      sites: list) -> list:
    return _phase1_header(R.PARAMETER_TABLE, source_commit, sites) + [
        "# Parameter cells of the adult students (V_ERW %d, age >= %d) of the universe above,"
        % (R.STUDENT_V_ERW, L.MIN_ADULT_AGE),
        "#   vocabulary student_parameters.PARAMETERS, every label emitted, a small cell with its",
        "#   n_unweighted (no pooling). Day parameters: student_parameters.person_day_labels on",
        "#   srv_plan_structure.harmonise_srv frames (one function for survey and model).",
        "# The away_all_day rows (bases students_all and other_adults_all) are NOT part of the",
        "#   universe above: they are measured on the raw person file BEFORE the universe filter",
        "#   (the filter drops exactly the persons away from home all day), after the weight,",
        "#   weekday and Kreis rules; their counts are the away_all_day_* keys below.",
        "# TWO definitions of an education leg, each with its own job:",
        "#   education_leg_share_harmonised_purpose = weighted share of the adult students with a",
        "#   trip of harmonised purpose education (the comparison with the model, the",
        "#   education_leg rows of this table); education_leg_share_control_definition = share",
        "#   with E_ZWECK_9 in %s (the definition of the behaviour table, the day factor and"
        % sorted(R.EDUCATION_E_ZWECK_9),
        "#   the control targets).",
        "# The work leg has the same two definitions: the work_leg rows use the harmonised purpose,",
        "#   work_leg_share_control_definition = share with E_ZWECK_9 in %s (the definition of the"
        % sorted(R.WORK_E_ZWECK_9),
        "#   work_by_employment target). For both control-definition shares the exclusion keys",
        "#   carry <leg>_n_with_control_definition, <leg>_n_without_control_definition (respondents,",
        "#   adult students of the universe above), <leg>_n_effective_control_definition (Kish size)",
        "#   and <leg>_se_share_control_definition = sqrt(p (1 - p) / n_effective).",
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _young_adult_header(diagnostics: dict, universe_diagnostics: dict, source_commit: str,
                        sites: list) -> list:
    return _phase1_header(R.YOUNG_ADULT_STATUS_TABLE, source_commit, sites) + [
        "# Base of THIS table: persons aged %d to %d (bounds inclusive) of the universe above, not"
        % R.AGE_BAND_20_29,
        "#   all adults (the 'adult = V_ALTER >= N' remark above defines a student, not this base).",
        "# Status %s of these young adults per Kreis and one %s row; Wolfsburg"
        % (list(R.YOUNG_ADULT_STATUSES), R.REGION_CODE),
        "#   is emitted with zero counts and NaN shares.",
        "# Columns: %s." % ", ".join(R.YOUNG_ADULT_STATUS_COLUMNS),
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE
        + " (n_young_adult_base_missing_age counts the persons without a valid age among the"
        " persons with a readable V_ERW of this table's own base)",
    ]


def _post_matsim_header(diagnostics: dict, universe_diagnostics: dict, n_bootstrap: int,
                        seed: int, source_commit: str, sites: list) -> list:
    return _phase1_header(R.POST_MATSIM_TABLE, source_commit, sites) + [
        "# Values the post-MATSim analysis compares the model's adult students against: main",
        "#   mode (E_HVM_5) of all legs and of the education legs, each leg weighted with its",
        "#   person's weight; the daily distance (km) and travel time (min) means.",
        "# SUB-UNIVERSE of the two daily totals: %s." % R.DAILY_TOTAL_SUB_UNIVERSE,
        "#   Persons with the code %d (n_not_computable_* below) are not in their base; this is"
        % R.NOT_COMPUTABLE_DAILY_TOTAL,
        "#   a documented definition of the SrV codebook, not a fallback.",
        "# se_value: seeded person bootstrap (n_bootstrap=%d, seed=%d); NaN for a base of one"
        % (n_bootstrap, seed),
        "#   person or none.",
        "# Columns: %s." % ", ".join(R.POST_MATSIM_COLUMNS),
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _destination_header(diagnostics: dict, universe_diagnostics: dict, n_bootstrap: int,
                        seed: int, source_commit: str, sites: list) -> list:
    return _phase1_header(R.DESTINATION_TABLE, source_commit, sites) + [
        "# Destination Kreis (V_ZIEL_AGS, zero-padded to 8 digits) of the FIRST education leg (by",
        "#   start hour, start minute, WNR) of the adult students with an education leg, per home",
        "#   group %s; shares weighted with GEWICHT_P_ZENSUS." % list(R.HOME_GROUPS),
        "# The cells are POOLED across the home Kreise of a group (see the builder docstring).",
        "# noise_floor_tvd_95: 95th percentile of the total-variation distance of a person",
        "#   bootstrap of the home group (n_bootstrap=%d, seed=%d); NaN for an empty group."
        % (n_bootstrap, seed),
        "# Columns: %s." % ", ".join(R.DESTINATION_COLUMNS),
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _by_area_header(diagnostics: dict, universe_diagnostics: dict, source_commit: str,
                    sites: list) -> list:
    return _phase1_header(R.PARAMETER_BY_AREA_TABLE, source_commit, sites,
                          PHASE3_TABLE_LABEL) + [
        "# The rows of %s once per surveyed home area (column area): the households are filtered"
        % R.PARAMETER_TABLE,
        "#   by their Kreis, the persons by their household, the universe by its Kreis; every",
        "#   definition, base and exclusion rule is that of the parameter table (same builder).",
        # Machine-readable line (csv_header.read_header_mapping); student_targets.read_student_shares checks
        # the configured home areas of the stage against it, so keep the format.
        "# Home areas: %s"
        % "; ".join("%s=%s" % (area, ",".join(kreise)) for area, kreise in R.HOME_AREAS.items()),
        "# The areas above are srv_student_reference.HOME_AREAS. Wolfsburg (%s) is not surveyed and has"
        % WOLFSBURG_KREIS,
        "#   no row here (the model side reports it with NaN SrV columns).",
        "# The two areas together are the region: per (parameter, base, label) the n_unweighted of",
        "#   the two areas add up to the row of %s. No cell is pooled (design A15);"
        % R.PARAMETER_TABLE,
        "#   every row carries its n_unweighted.",
        "# Two leg definitions as in the parameter table: the education_leg and work_leg rows use",
        "#   the harmonised purpose; <leg>_share_control_definition (per area, in the exclusion",
        "#   keys below, with its n_with / n_without / n_effective / se_share keys) uses E_ZWECK_9",
        "#   in %s (education) and %s (work, the work_by_employment definition)."
        % (sorted(R.EDUCATION_E_ZWECK_9), sorted(R.WORK_E_ZWECK_9)),
        "# Columns: area, %s." % ", ".join(SP.PARAMETER_COLUMNS),
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE
        + " (keys of the builder are prefixed with the area; the universe keys are the region's)",
    ]


def _education_leg_by_status_header(diagnostics: dict, universe_diagnostics: dict,
                                    source_commit: str, sites: list) -> list:
    return _phase1_header(R.EDUCATION_LEG_BY_STATUS_TABLE, source_commit, sites,
                          PHASE3_TABLE_LABEL) + [
        "# Base of THIS table: adults (V_ALTER >= %d) of the universe above with a readable V_ERW"
        " and a valid age,"
        % L.MIN_ADULT_AGE,
        "#   at home at some point on the reporting day; group %s (V_ERW %d) or %s (everybody else)."
        % (R.GROUP_STUDENT, R.STUDENT_V_ERW, R.GROUP_OTHER_ADULT),
        "# Education leg: E_ZWECK_9 in %s, the control definition of"
        % sorted(R.EDUCATION_E_ZWECK_9),
        "#   target2026_education_18plus_by_kreis.csv (srv_participation_universe."
        "persons_with_purpose).",
        "# Rows: per ZGB Kreis age_range 18plus (Wolfsburg a zero row with NaN share) and for the",
        "#   region %s every age_range of %s (bounds inclusive; 200 = open end), per group."
        % (R.REGION_CODE, [name for name, _, _ in R.EDUCATION_AGE_RANGES]),
        "# Columns: %s; share = weighted share of the base with an education leg,"
        % ", ".join(R.EDUCATION_LEG_BY_STATUS_COLUMNS),
        "#   n_with_leg_unweighted its unweighted count, weight_sum the weight of the persons WITH a",
        "#   leg (expanded persons), n_effective the Kish size of the whole base of the row.",
        "# Who the other adults with a leg are (pupil, apprentice, employed, other by age band):",
        "#   see %s, not repeated here." % R.EDUCATION_STATUS_TABLE,
        _exclusions(universe_diagnostics, diagnostics) + " " + _UNIVERSE_UNITS_NOTE,
    ]


def _area_rule_header(source_commit: str) -> list:
    control_parameters = [parameter for parameter, _ in CE.AREA_RULE_CONTROL_DEFINITION_CELLS]
    return [
        "# Source: computed by scripts/extract_srv_student_reference.py on %s from the by-area"
        % dt.date.today().isoformat(),
        "#   parameter table %s and its exclusion keys of the same run and code state; no raw data"
        % R.PARAMETER_BY_AREA_TABLE,
        "#   enters this table.",
        "# Table: %s (issue #140, %s). A measurement, not a validation."
        % (CE.AREA_RULE_TABLE, PHASE3_TABLE_LABEL),
        "# Code state: eqasim-bs %s, braunschweig/calibration/student_control_evidence.py"
        % source_commit,
        "#   (area_rule_table). A measurement, not a validation (issue #140, design A3 rule 2).",
        "# Rule: a parameter gets a value of its own for each home area (%s) only when the two"
        % ", ".join(R.HOME_AREAS),
        "#   areas differ by more than %s standard errors of the difference (z = (share_%s -"
        % (CE.MIN_DIFFERENCE_STANDARD_ERRORS, R.AREA_BRAUNSCHWEIG),
        "#   share_%s) / sqrt(se_%s^2 + se_%s^2)) AND enough_respondents holds: both bases have at"
        % (R.AREA_SURROUNDING, R.AREA_BRAUNSCHWEIG, R.AREA_SURROUNDING),
        "#   least %d respondents and the label and the rest at least %d respondents on each side."
        % (CE.MIN_RESPONDENTS_PER_AREA, CE.MIN_RESPONDENTS_EACH_SIDE),
        "# ASSUMPTIONS (designer's judgements, design A14 and A20, not sourced thresholds): the %s"
        % CE.MIN_DIFFERENCE_STANDARD_ERRORS,
        "#   standard errors, the %d and the %d respondents."
        % (CE.MIN_RESPONDENTS_PER_AREA, CE.MIN_RESPONDENTS_EACH_SIDE),
        "# Bases: n_base_<area> = respondents of the cell's base in that area (students present at",
        "#   home on the reporting day; for away_all_day all students), n_label_<area> with the",
        "#   label, n_other_<area> = n_base - n_label. Cells: %s."
        % ["%s/%s" % (parameter, label) for parameter, _, label in CE.AREA_RULE_CELLS],
        "# Two definitions of the education and the work leg, each in its own row: education_leg and",
        "#   work_leg use the HARMONISED purpose (rows of the by-area table); %s use E_ZWECK_9 in"
        % " and ".join(control_parameters),
        "#   %s (education) / %s (work, the work_by_employment target definition), from the"
        % (sorted(R.EDUCATION_E_ZWECK_9), sorted(R.WORK_E_ZWECK_9)),
        "#   <leg>_*_control_definition exclusion keys of %s (base students_present, label yes,"
        % R.PARAMETER_BY_AREA_TABLE,
        "#   n_label / n_other = respondents with / without the leg). The two definitions agree on",
        "#   the SrV data of this run; the rule is applied to each separately and nothing relies on",
        "#   that agreement. The soft work-leg entry of the synthesis reads the control row.",
        "# Columns: %s." % ", ".join(CE.AREA_RULE_COLUMNS),
    ]


def read_site_kreise(path: Path) -> tuple:
    """Site Kreise (``site_kreis_ars5`` of the ``local`` rows) and the sha256 of the table.

    The site table is local-only (R1). Only the LEADING ``#`` lines are skipped (a ``#`` inside a
    field stays), the required columns are checked first (fail early), the codes are read as
    strings so a leading zero stays. The file is read as UTF-8, the encoding
    ``scripts/seed_lsn_university_sites.py`` writes the table in.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError("site table missing: %s" % path)
    n_comment_lines = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            n_comment_lines += 1
    table = pd.read_csv(path, skiprows=n_comment_lines, dtype=str)
    missing = [column for column in SITE_REQUIRED_COLUMNS if column not in table.columns]
    if missing:
        raise ValueError("%s: missing required column(s) %s" % (path, missing))
    local = table.loc[table["scope"] == SITE_SCOPE_LOCAL, "site_kreis_ars5"]
    if local.isna().any():
        raise ValueError("%s: a local row has no site_kreis_ars5" % path)
    if local.empty:
        raise ValueError("%s: no row with scope == %r" % (path, SITE_SCOPE_LOCAL))
    return frozenset(local), hashlib.sha256(path.read_bytes()).hexdigest()


def _write(df: pd.DataFrame, path: Path, header: list) -> None:
    """Write the provenance-headed CSV with LF line endings and ``%.10g`` floats."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in header:
            fh.write(line + "\n")
        df.to_csv(fh, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", path, len(df))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", required=True,
                        help="SrV raw directory (local-only; must contain %s, %s, %s)"
                             % (PERSONS_FILE, LEGS_FILE, HOUSEHOLDS_FILE))
    parser.add_argument("--sites", required=True,
                        help="local-only site table R1 (lsn_university_sites.csv; columns "
                             "site_kreis_ars5, scope); never a default path")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT),
                        help="Directory to write the twelve tables into")
    parser.add_argument("--source-commit", required=True,
                        help="git commit of the code state, recorded in every header")
    parser.add_argument("--n-bootstrap", type=int, default=500,
                        help="bootstrap resamples (distance-band EMD noise floor, post-MATSim "
                             "standard errors, destination noise floor)")
    parser.add_argument("--seed", type=int, default=0,
                        help="random seed of all three bootstraps")
    args = parser.parse_args(argv)

    raw = Path(args.raw)
    for name in (PERSONS_FILE, LEGS_FILE, HOUSEHOLDS_FILE):
        if not (raw / name).exists():
            raise FileNotFoundError("SrV raw file missing: %s" % (raw / name))
    site_kreise, sites_sha256 = read_site_kreise(Path(args.sites))
    _require_columns(raw / PERSONS_FILE, PERSON_READ_COLUMNS)
    _require_columns(raw / LEGS_FILE, LEG_READ_COLUMNS)
    _require_columns(raw / HOUSEHOLDS_FILE, HOUSEHOLD_READ_COLUMNS)

    persons = pd.read_csv(raw / PERSONS_FILE, usecols=PERSON_READ_COLUMNS, **CSV_READ_KWARGS)
    legs = pd.read_csv(raw / LEGS_FILE, usecols=LEG_READ_COLUMNS, **CSV_READ_KWARGS)
    households = pd.read_csv(raw / HOUSEHOLDS_FILE, usecols=HOUSEHOLD_READ_COLUMNS,
                             **CSV_READ_KWARGS)
    logger.info("read %d persons, %d legs, %d households", len(persons), len(legs),
                len(households))

    universe, universe_diagnostics = R.student_universe(persons, households)
    share, share_diagnostics = R.build_student_share_table(universe, legs)
    status, status_diagnostics = R.build_education_status_table(universe, legs)
    observations, selection_log = select_person_observations(
        legs, persons[["HHNR", "PNR", "V_ALTER"]], households, EDUCATION_PURPOSES)
    student_observations, observation_diagnostics = R.select_student_observations(
        observations, universe)
    distance = R.build_education_distance_table(student_observations, args.n_bootstrap,
                                                args.seed)
    behaviour, behaviour_diagnostics = R.build_behaviour_table(universe, legs)
    comparison, comparison_diagnostics = R.build_comparison_table(universe)

    # Phase 1: the harmonised frames come from the same function the model side passes through.
    harmonised_persons, harmonised_trips = PS.harmonise_srv(
        persons[PS.PERSON_COLUMNS], legs[PS.TRIP_COLUMNS], households[PS.HOUSEHOLD_COLUMNS])
    parameters, parameter_diagnostics = R.build_parameter_table(
        universe, persons, households, harmonised_persons, harmonised_trips, legs)
    young_adults, young_adult_diagnostics = R.build_young_adult_status_table(universe)
    post_matsim, post_matsim_diagnostics = R.build_post_matsim_reference(
        universe, persons, legs, args.n_bootstrap, args.seed)
    destination, destination_diagnostics = R.build_destination_reference(
        universe, legs, site_kreise, args.n_bootstrap, args.seed)

    # Phase 3: the same parameter builder per home area, the education leg by student status,
    # and the area rule computed from the by-area table just built (no raw data in the rule).
    by_area, by_area_diagnostics = R.build_parameter_table_by_area(
        universe, persons, households, harmonised_persons, harmonised_trips, legs)
    education_by_status, education_by_status_diagnostics = \
        R.build_education_leg_by_student_status(universe, legs)
    area_rule = CE.area_rule_table(by_area, control_diagnostics=by_area_diagnostics)

    out = Path(args.out_dir)
    commit = args.source_commit
    _write(share, out / R.STUDENT_SHARE_TABLE,
           _share_header(share_diagnostics, universe_diagnostics, commit))
    _write(status, out / R.EDUCATION_STATUS_TABLE,
           _status_header(status_diagnostics, universe_diagnostics, commit))
    _write(distance, out / R.EDUCATION_DISTANCE_TABLE,
           _distance_header(observation_diagnostics, selection_log, universe_diagnostics,
                            args.n_bootstrap, args.seed, commit))
    _write(behaviour, out / R.BEHAVIOUR_TABLE,
           _behaviour_header(behaviour_diagnostics, universe_diagnostics, commit))
    _write(comparison, out / R.COMPARISON_TABLE,
           _comparison_header(comparison_diagnostics, universe_diagnostics, commit))
    sites_lines = _sites_line(site_kreise, sites_sha256)
    _write(parameters, out / R.PARAMETER_TABLE,
           _parameter_header(parameter_diagnostics, universe_diagnostics, commit, sites_lines))
    _write(young_adults, out / R.YOUNG_ADULT_STATUS_TABLE,
           _young_adult_header(young_adult_diagnostics, universe_diagnostics, commit,
                               sites_lines))
    _write(post_matsim, out / R.POST_MATSIM_TABLE,
           _post_matsim_header(post_matsim_diagnostics, universe_diagnostics, args.n_bootstrap,
                               args.seed, commit, sites_lines))
    _write(destination, out / R.DESTINATION_TABLE,
           _destination_header(destination_diagnostics, universe_diagnostics, args.n_bootstrap,
                               args.seed, commit, sites_lines))
    _write(by_area, out / R.PARAMETER_BY_AREA_TABLE,
           _by_area_header(by_area_diagnostics, universe_diagnostics, commit, sites_lines))
    _write(education_by_status, out / R.EDUCATION_LEG_BY_STATUS_TABLE,
           _education_leg_by_status_header(education_by_status_diagnostics,
                                           universe_diagnostics, commit, sites_lines))
    _write(area_rule, out / CE.AREA_RULE_TABLE,
           _area_rule_header(commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
