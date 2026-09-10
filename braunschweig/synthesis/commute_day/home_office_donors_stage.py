"""synpp stage: the MiD home-office-day donor pool (ADR-0104, issue #244, Phase B Task 4).

Reads the raw MiD 2023 delivery (``MiD2023_Personen.csv`` / ``MiD2023_Wege.csv`` /
``MiD2023_Haushalte.csv`` under ``braunschweig.population.popsim.mid_dir``) and hands it to the
pure builder :func:`braunschweig.synthesis.commute_day.donor_pool.build_home_office_donor_pool`.
This module owns ONLY the I/O and the synpp plumbing; every rule about who is a donor, which
attributes a donor carries and how the donor's trip chain is built lives in ``donor_pool`` and is
documented there. What this module DOES own is the wiring of those rules to the configuration:
the donor day is built with the SAME plan-structure flags and the SAME closure dwell model the
production trip build uses, and the donor universe is narrowed by the SAME diary-realisability
flags the plan-source pool uses (issue #374; see the config-key block below).

Output: ``(attributes, trips, diagnostics)``.

* ``attributes`` -- one row per donor, columns :data:`ATTRIBUTE_COLUMNS` (see
  ``donor_pool.donor_attributes``).
* ``trips`` -- one row per (donor, trip), columns :data:`TRIP_COLUMNS`: the
  ``synthesis.population.trips`` contract with ``person_id`` renamed to ``donor_id``, plus
  ``euclidean_distance`` and ``trip_key`` (see ``donor_pool.donor_trips``).
* ``diagnostics`` -- the builder's diagnostics dict (see
  ``donor_pool.build_home_office_donor_pool``) plus the stage-level counts documented on
  :func:`execute`.

With ``commute_day_state_enabled`` FALSE the stage reads nothing at all and returns two EMPTY
frames carrying exactly those columns (so a downstream consumer sees the same schema either way)
and ``{"enabled": False}``.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import logging
import os

import pandas as pd

from braunschweig.popsim import closure_dwell as _closure_dwell
from braunschweig.popsim import diary_facts as _diary_facts
from braunschweig.popsim import diary_plan_match as _diary_plan_match
from braunschweig.popsim import escort_pairing as _escort_pairing
from braunschweig.popsim import plan_validation as _plan_validation
from braunschweig.popsim import trips as _popsim_trips
from braunschweig.popsim import trips_stage as _trips_stage
from braunschweig.popsim.mid.csv_format import detect_csv_separator
from braunschweig.popsim.mid.donor import MID_WEGE_REQUIRED_COLS
from braunschweig.popsim.trips_stage import CONTRACT
from braunschweig.synthesis.commute_day import donor_pool as _donor_pool
from braunschweig.synthesis.commute_day.donor_pool import build_home_office_donor_pool

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day donors stage]"

# --------------------------------------------------------------------------- config keys

#: Directory holding the raw MiD 2023 delivery; the SAME key ``braunschweig.popsim.trips_stage``
#: reads, so both stages can never point at different MiD extracts.
KEY_MID_DIR = "braunschweig.population.popsim.mid_dir"
#: Master switch of the whole commute-day-state model (ADR-0104); OFF makes this stage a no-op.
KEY_ENABLED = "commute_day_state_enabled"
DEFAULT_ENABLED = True

#: Trip-construction flags, with the IDENTICAL defaults ``braunschweig.popsim.trips_stage``
#: declares -- the donor's day must be built by exactly the same rules as the synthetic
#: population's day, or a replaced day would follow a different purpose/mode vocabulary than the
#: one it replaces (ruling R2, see ``donor_pool.donor_trips``).
KEY_ESCORT_PURPOSE = "escort_purpose"
DEFAULT_ESCORT_PURPOSE = False
KEY_ESCORT_PASSIVE_EDUCATION = "escort_passive_education"
DEFAULT_ESCORT_PASSIVE_EDUCATION = False
KEY_EXPLICIT_ROUND_TRIP_PURPOSES = "explicit_round_trip_purposes"
DEFAULT_EXPLICIT_ROUND_TRIP_PURPOSES = True
#: Passive escort leg -> the accompanying adult's purpose (issue #372, ADR-0112). Key names and
#: defaults are imported from ``braunschweig.popsim.stage.config_keys`` (the SHARED constants),
#: never re-typed here -- same rule as ``w_zweck_10_as_leisure`` below.
#: W_ZWECK 10 "anderer Zweck" -> leisure (issue #373, ADR-0111). Key name and default are
#: imported from ``braunschweig.popsim.stage.config_keys`` (the SHARED constants), never
#: re-typed here -- see the plan-structure-keys note below for why every stage that reads
#: a key declared by more than one stage must import the same constant.

#: Plan-structure keys (issues #366/#367), declared and read here for the SAME reason the three
#: flags above are: the donor's day must be built by exactly the rules the replaced day was built
#: by. They were MISSING until issue #374 -- the pure builder called the shared trip-table builder
#: without them although its docstring claimed full parity -- so a donor day kept the rbW summary
#: legs and the leading arrive-home leg production drops, and closed an open chain with the
#: constant one-hour dwell rather than the empirical one. Key names and DEFAULTS are imported
#: from the leaf module ``braunschweig.popsim.stage.config_keys`` (never re-typed), so this stage,
#: ``braunschweig.popsim.trips_stage`` and ``braunschweig.popsim.completed_donor`` cannot declare
#: the same key with different defaults; the import is deferred into ``configure()``/``execute()``
#: to avoid pulling in the heavy ``braunschweig.popsim.stage`` package at import time (the pattern
#: ``trips_stage`` uses, and the reason ``config_keys`` is hashed by :func:`validate` below).
#:
#: The two DONOR-FILTER keys are the plan-source realisability flags, read with exactly the
#: meaning ``braunschweig.popsim.diary_plan_match`` gives them, so one base-config value steers
#: the plan-source pool and this donor pool alike:
#:
#: * ``diary_plan_match`` is the master switch; with it off no donor filter runs at all.
#: * ``exclude_holiday_plan_sources`` additionally gates the holiday filter.
#: * ``exclude_rbw_legs`` additionally gates the rbW-only filter -- the same coupling
#:   ``diary_plan_match._own_diary_reason`` applies (a diary of nothing but rbW legs is only
#:   unusable because those legs are the ones being dropped).

# --------------------------------------------------------------------------- raw MiD inputs

PERSONS_FILE = "MiD2023_Personen.csv"
WEGE_FILE = "MiD2023_Wege.csv"
HOUSEHOLDS_FILE = "MiD2023_Haushalte.csv"
RAW_FILES = (PERSONS_FILE, WEGE_FILE, HOUSEHOLDS_FILE)

#: MiD person columns the donor pool cannot be built without (donor filter + attributes):
#: ``HP_ID`` (donor key), ``H_ID``/``P_ID`` (Wege join), ``arbwo``/``P_STARB1``/``starb2`` (the
#: home-office-day filter), ``P_ARB_ENTF`` (commute-distance cross-check), ``HP_ALTER``/``HP_SEX``.
PERSON_REQUIRED_COLUMNS = ("HP_ID", "H_ID", "P_ID", "arbwo", "P_STARB1", "starb2",
                           "P_ARB_ENTF", "HP_ALTER", "HP_SEX")
#: MiD person columns that are loaded WHEN PRESENT: ``M_HOFF`` (the home-office-module flag,
#: whose disagreement with ``starb2`` is reported by
#: ``donor_pool.select_home_office_day_donors``), ``P_GEW`` (the MiD person expansion weight,
#: used ONLY to report the donor share weighted as well as unweighted) and the three columns the
#: issue-#374 donor filters read: ``anzwege1``/``mobil`` (the no-diary filter) and ``feiertag``
#: (the holiday filter). A delivery lacking one is loaded without it and the omission is logged,
#: never silently assumed. "Optional" here is about LOADING only: a column a LIVE filter needs is
#: not optional at all -- :func:`execute` raises when one is absent (see
#: :data:`DONOR_FILTER_REQUIRED_COLUMNS`), because silently skipping a filter would leave donors
#: in the pool that the configuration says must not be there.
PERSON_OPTIONAL_COLUMNS = ("M_HOFF", "P_GEW", "anzwege1", "mobil", "feiertag")
#: Per donor filter: the config flag it is steered by (for the error message) and the MiD person
#: columns ``donor_pool.filter_donor_diaries`` needs to apply it.
DONOR_FILTER_REQUIRED_COLUMNS = (
    ("exclude_no_diary", ("anzwege1", "mobil")),
    ("exclude_holidays", ("feiertag",)),
)
#: MiD Wege columns: exactly what the trip-table builder needs (imported from the single
#: committed definition, never re-typed) plus ``wegkm`` -- the raw trip length the
#: commute-distance fallback reads (``commute_day_state_reference.first_work_trip_length_km``).
#: ``HP_ALTER``, the household member's age the passive-escort pairing needs to decide who counts
#: as the accompanying ADULT (``escort_pairing.REQUIRED_COLUMNS``, issue #372), needs no entry
#: here: ``MID_WEGE_REQUIRED_COLS`` requires it, unconditionally rather than under
#: ``escort_passive_from_adult``, so that a delivery without it fails at LOAD time naming the
#: column instead of deep inside map_purpose, and so that the raw read never depends on a flag.
WEGE_COLUMNS = tuple(MID_WEGE_REQUIRED_COLS) + ("wegkm",)
#: MiD household columns: ``H_GR`` (household size, binned by the matching module) and
#: ``H_ANZAUTO`` (car ownership).
HOUSEHOLD_COLUMNS = ("H_ID", "H_GR", "H_ANZAUTO")

# --------------------------------------------------------------------------- output schema

#: Columns of the ``attributes`` frame: ``donor_pool.donor_attributes``' own columns plus the
#: four ``donor_pool.attach_trip_derived_attributes`` adds -- ``n_trips``, ``has_education_leg``
#: and ``has_work_leg`` from the BUILT chains, ``is_immobile`` from the RAW Wege file (rulings R7
#: and R9; see that function for why the last one cannot be derived from ``n_trips == 0``).
ATTRIBUTE_COLUMNS = ("donor_id", "H_ID", "P_ID", "sex", "age", "age_class", "employed",
                     "has_children_u14", "has_car", "has_active_escort", "household_size",
                     "distance_km", "distance_class", "distance_source",
                     "n_trips", "is_immobile", "has_education_leg", "has_work_leg")
#: Columns of the ``trips`` frame (``donor_pool.donor_trips``): the ``synthesis.population.trips``
#: CONTRACT with ``person_id`` renamed to ``donor_id``, plus the two documented extras.
TRIP_COLUMNS = tuple("donor_id" if column == "person_id" else column for column in CONTRACT) \
    + ("euclidean_distance", "trip_key")

#: Helper modules whose source is folded into :func:`validate`'s token. synpp hashes only THIS
#: file's source, so without them an edit to the pure donor-pool builder (or to the trip-table
#: construction underneath it) would silently reuse a stale cached donor pool -- the exact hazard
#: recorded for ``braunschweig.popsim.trips_stage`` (see its ``validate`` docstring).
#: ``trips_stage`` and ``closure_dwell`` shape the synthesised chain closure's dwell time (the
#: donor pool builds the same empirical model the production trip build uses); ``diary_facts``
#: classifies the rbW-only diaries and ``diary_plan_match`` owns the MiD codes the donor filters
#: read (issue #374); ``escort_pairing`` decides which adult leg each passive escort leg is paired
#: with and therefore the purpose the donor's child leg receives under
#: ``escort_passive_from_adult`` (issue #372). Over-hashing only costs a cache rebuild;
#: under-hashing silently serves a stale pool.
_HELPER_MODULES = (_donor_pool, _popsim_trips, _plan_validation, _trips_stage, _closure_dwell,
                   _diary_facts, _diary_plan_match, _escort_pairing)
#: Modules hashed by NAME because they are imported inside ``configure()``/``execute()`` rather
#: than at module level (see the config-key block above). Written as string LITERALS, like
#: every other stage's deferred list: ``tests/test_synpp_helper_hash_invariant.py`` resolves
#: these statically with ``ast`` and cannot follow a name binding. Hashed for the same reason
#: ``trips_stage`` hashes it: a renamed key or a changed declared default must not serve a
#: donor pool built under the old option surface.
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.popsim.stage.config_keys",)


def configure(context):
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_CLOSURE_DWELL_MODEL, DEFAULT_DIARY_PLAN_MATCH,
        DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_ESCORT_PASSIVE_FROM_ADULT,
        DEFAULT_EXCLUDE_HOLIDAY_PLAN_SOURCES, DEFAULT_EXCLUDE_RBW_LEGS,
        DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES, DEFAULT_W_ZWECK_10_AS_LEISURE,
        KEY_CLOSURE_DWELL_MIN_OBS, KEY_CLOSURE_DWELL_MODEL, KEY_DIARY_PLAN_MATCH,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_ESCORT_PASSIVE_FROM_ADULT,
        KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, KEY_EXCLUDE_RBW_LEGS,
        KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, KEY_W_ZWECK_10_AS_LEISURE,
    )
    context.config(KEY_MID_DIR)
    context.config(KEY_ESCORT_PURPOSE, DEFAULT_ESCORT_PURPOSE)
    context.config(KEY_ESCORT_PASSIVE_EDUCATION, DEFAULT_ESCORT_PASSIVE_EDUCATION)
    context.config(KEY_EXPLICIT_ROUND_TRIP_PURPOSES, DEFAULT_EXPLICIT_ROUND_TRIP_PURPOSES)
    context.config(KEY_EXCLUDE_RBW_LEGS, DEFAULT_EXCLUDE_RBW_LEGS)
    context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG)
    context.config(KEY_CLOSURE_DWELL_MODEL, DEFAULT_CLOSURE_DWELL_MODEL)
    context.config(KEY_CLOSURE_DWELL_MIN_OBS, _trips_stage.DEFAULT_CLOSURE_DWELL_MIN_OBS)
    context.config(KEY_W_ZWECK_10_AS_LEISURE, DEFAULT_W_ZWECK_10_AS_LEISURE)
    context.config(KEY_ESCORT_PASSIVE_FROM_ADULT, DEFAULT_ESCORT_PASSIVE_FROM_ADULT)
    context.config(KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES)
    context.config(KEY_DIARY_PLAN_MATCH, DEFAULT_DIARY_PLAN_MATCH)
    context.config(KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, DEFAULT_EXCLUDE_HOLIDAY_PLAN_SOURCES)
    context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.config("random_seed")


def validate(context):
    """Cache token: the raw MiD input sizes plus the pure builders' source hash.

    ``0`` when the model is disabled (the stage reads nothing, so nothing can invalidate it).
    Otherwise ``"<summed byte size of the three raw MiD files>-<md5 of :data:`_HELPER_MODULES`
    plus :data:`_DEFERRED_HELPER_MODULE_NAMES`>"``: the size half notices a re-delivered MiD
    extract, the hash half notices an edit to the pure donor-pool/trip-construction code (or to
    the declared option surface) that synpp's own per-file hashing would miss (see
    :data:`_HELPER_MODULES`). A missing raw file raises ``RuntimeError`` naming it -- the pipeline
    must fail here, at validation time, not halfway through a run. A deferred module that fails to
    import raises rather than being skipped: skipping it would keep the stale cache alive exactly
    when the code is broken.
    """
    if not bool(context.config(KEY_ENABLED)):
        return 0
    mid_dir = context.config(KEY_MID_DIR)
    total_size = 0
    for name in RAW_FILES:
        path = os.path.join(str(mid_dir), name)
        if not os.path.exists(path):
            raise RuntimeError(
                f"{_LOG_TAG} missing raw MiD input file: {path}. The commute-day-state model "
                f"({KEY_ENABLED}) needs the raw MiD 2023 delivery ({', '.join(RAW_FILES)}) in "
                f"the directory configured as {KEY_MID_DIR}; set {KEY_ENABLED}: false to run "
                "without the model.")
        total_size += os.path.getsize(path)
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_source = inspect.getsource(importlib.import_module(module_name))
        except Exception as error:
            raise RuntimeError(
                f"{_LOG_TAG} validate(): cannot hash the deferred helper module {module_name!r} "
                f"({type(error).__name__}: {error}); it must not be skipped, because skipping it "
                "would silently reuse a stale cached donor pool.") from error
        digest.update(deferred_source.encode("utf-8"))
    return f"{total_size}-{digest.hexdigest()}"


def _read_persons(mid_dir):
    """Read the MiD person file, required columns plus whichever optional ones exist."""
    path = os.path.join(str(mid_dir), PERSONS_FILE)
    separator = detect_csv_separator(path)
    header = pd.read_csv(path, sep=separator, nrows=0).columns
    missing = [column for column in PERSON_REQUIRED_COLUMNS if column not in header]
    if missing:
        raise RuntimeError(
            f"{_LOG_TAG} {path} is missing the required MiD person column(s) {missing} "
            f"(present: {sorted(header)[:20]} ...); the home-office-day donor pool cannot be "
            "built without them.")
    optional_present = [column for column in PERSON_OPTIONAL_COLUMNS if column in header]
    optional_absent = [column for column in PERSON_OPTIONAL_COLUMNS if column not in header]
    if optional_absent:
        logger.warning("%s MiD person file has no %s column(s); the diagnostics they feed are "
                       "reported as absent rather than assumed. A column an ACTIVE donor filter "
                       "needs is not merely reported -- execute() raises on it (see "
                       "_require_donor_filter_columns).", _LOG_TAG, optional_absent)
    return pd.read_csv(path, sep=separator,
                       usecols=list(PERSON_REQUIRED_COLUMNS) + optional_present)


def _read_columns(mid_dir, file_name, columns):
    """Read exactly ``columns`` from one raw MiD file, failing loudly on a missing column."""
    path = os.path.join(str(mid_dir), file_name)
    separator = detect_csv_separator(path)
    header = pd.read_csv(path, sep=separator, nrows=0).columns
    missing = [column for column in columns if column not in header]
    if missing:
        raise RuntimeError(
            f"{_LOG_TAG} {path} is missing the required column(s) {missing} (present: "
            f"{sorted(header)[:20]} ...).")
    return pd.read_csv(path, sep=separator, usecols=list(columns), low_memory=False)


def _empty_output():
    """The OFF-path output: two empty frames with the ON-path columns, and ``enabled: False``."""
    attributes = pd.DataFrame(columns=list(ATTRIBUTE_COLUMNS))
    trips = pd.DataFrame(columns=list(TRIP_COLUMNS))
    return attributes, trips, {"enabled": False}


def _weighted_donor_share(persons, donor_ids):
    """MiD-weighted share of persons selected as donors, or ``NaN`` without ``P_GEW``.

    Reported ALONGSIDE the unweighted share so the donor universe can be read as a population
    share of home-office-day workers, not only as a raw record count. Returns ``NaN`` (never a
    substituted value) when the delivery carries no ``P_GEW`` column.
    """
    if "P_GEW" not in persons.columns:
        return float("nan")
    weights = pd.to_numeric(persons["P_GEW"], errors="coerce")
    total = float(weights.sum())
    if not total > 0:
        return float("nan")
    return float(weights[persons["HP_ID"].isin(donor_ids)].sum()) / total


def _require_donor_filter_columns(persons, filter_flags):
    """Raise unless every ACTIVE donor filter's MiD person columns were delivered.

    Fail-fast, never a silent skip: a filter whose column is missing would leave donors in the
    pool that the configuration says must not be there, and the pool would look perfectly healthy
    (CLAUDE.md: no silent fallbacks). The message names the missing column AND the config key to
    switch off if the delivery genuinely cannot carry it.
    """
    from braunschweig.popsim.stage.config_keys import (
        KEY_DIARY_PLAN_MATCH, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES,
    )
    steering_key = {"exclude_no_diary": KEY_DIARY_PLAN_MATCH,
                    "exclude_holidays": KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES}
    for flag, columns in DONOR_FILTER_REQUIRED_COLUMNS:
        if not filter_flags[flag]:
            continue
        missing = [column for column in columns if column not in persons.columns]
        if missing:
            raise RuntimeError(
                f"{_LOG_TAG} the {flag} donor filter is ON but {PERSONS_FILE} does not carry the "
                f"MiD person column(s) {missing}; the filter cannot run and must not be silently "
                f"skipped. Either use a delivery carrying {list(columns)} or set "
                f"{steering_key[flag]}: false.")


def execute(context):
    """Build the donor pool (or the empty OFF-path output).

    Stage-level diagnostics added on top of the builder's own (see the module docstring):
    ``enabled``, ``n_mid_persons`` / ``n_mid_trips`` / ``n_mid_households`` (raw rows read),
    ``donor_share_unweighted`` and ``donor_share_weighted`` (share of MiD persons selected as
    home-office-day donors AND surviving the diary filters; the weighted one is ``NaN`` when the
    delivery carries no ``P_GEW``).
    """
    from braunschweig.popsim.stage.config_keys import (
        KEY_CLOSURE_DWELL_MIN_OBS, KEY_CLOSURE_DWELL_MODEL, KEY_DIARY_PLAN_MATCH,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_ESCORT_PASSIVE_FROM_ADULT,
        KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, KEY_EXCLUDE_RBW_LEGS,
        KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, KEY_W_ZWECK_10_AS_LEISURE,
    )
    if not bool(context.config(KEY_ENABLED)):
        logger.info("%s %s is false -- returning an empty donor pool (no raw MiD is read).",
                    _LOG_TAG, KEY_ENABLED)
        return _empty_output()

    exclude_rbw_legs = bool(context.config(KEY_EXCLUDE_RBW_LEGS))
    # The donor filters follow the plan-source realisability flags exactly (see the config-key
    # block above): diary_plan_match is the master switch, and the holiday / rbW-only filters are
    # additionally gated by their own key, as diary_plan_match._own_diary_reason gates them.
    diary_plan_match_on = bool(context.config(KEY_DIARY_PLAN_MATCH))
    filter_flags = {
        "exclude_no_diary": diary_plan_match_on,
        "exclude_holidays": diary_plan_match_on
        and bool(context.config(KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES)),
        "exclude_only_rbw": diary_plan_match_on and exclude_rbw_legs,
    }

    mid_dir = context.config(KEY_MID_DIR)
    logger.info("%s reading the raw MiD delivery from %s", _LOG_TAG, mid_dir)
    persons = _read_persons(mid_dir)
    wege = _read_columns(mid_dir, WEGE_FILE, WEGE_COLUMNS)
    households = _read_columns(mid_dir, HOUSEHOLDS_FILE, HOUSEHOLD_COLUMNS)
    logger.info("%s read %d persons, %d trips, %d households", _LOG_TAG,
                len(persons), len(wege), len(households))
    _require_donor_filter_columns(persons, filter_flags)

    attributes, trips, diagnostics = build_home_office_donor_pool(
        persons, wege, households,
        random_seed=int(context.config("random_seed")),
        escort_purpose=bool(context.config(KEY_ESCORT_PURPOSE)),
        escort_passive_education=bool(context.config(KEY_ESCORT_PASSIVE_EDUCATION)),
        explicit_round_trip_purposes=bool(context.config(KEY_EXPLICIT_ROUND_TRIP_PURPOSES)),
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=bool(context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG)),
        closure_dwell_model=str(context.config(KEY_CLOSURE_DWELL_MODEL)),
        closure_dwell_min_obs=int(context.config(KEY_CLOSURE_DWELL_MIN_OBS)),
        w_zweck_10_as_leisure=bool(context.config(KEY_W_ZWECK_10_AS_LEISURE)),
        escort_passive_from_adult=bool(context.config(KEY_ESCORT_PASSIVE_FROM_ADULT)),
        passive_pair_max_gap_minutes=float(context.config(KEY_PASSIVE_PAIR_MAX_GAP_MINUTES)),
        **filter_flags,
    )

    diagnostics = dict(diagnostics)
    diagnostics["enabled"] = True
    diagnostics["n_mid_persons"] = len(persons)
    diagnostics["n_mid_trips"] = len(wege)
    diagnostics["n_mid_households"] = len(households)
    diagnostics["donor_share_unweighted"] = len(attributes) / max(len(persons), 1)
    diagnostics["donor_share_weighted"] = _weighted_donor_share(
        persons, set(attributes["donor_id"]))
    logger.info(
        "%s donor pool: %d donors from %d MiD persons (%.2f%% unweighted, %.2f%% P_GEW-weighted), "
        "%d donor trips; builder diagnostics: %s", _LOG_TAG, len(attributes), len(persons),
        100.0 * diagnostics["donor_share_unweighted"],
        100.0 * diagnostics["donor_share_weighted"], len(trips),
        {key: value for key, value in diagnostics.items() if key != "cells"})
    return attributes, trips, diagnostics
