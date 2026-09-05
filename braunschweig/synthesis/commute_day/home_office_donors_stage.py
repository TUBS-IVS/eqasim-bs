"""synpp stage: the MiD home-office-day donor pool (ADR-0104, issue #244, Phase B Task 4).

Reads the raw MiD 2023 delivery (``MiD2023_Personen.csv`` / ``MiD2023_Wege.csv`` /
``MiD2023_Haushalte.csv`` under ``braunschweig.population.popsim.mid_dir``) and hands it to the
pure builder :func:`braunschweig.synthesis.commute_day.donor_pool.build_home_office_donor_pool`.
This module owns ONLY the I/O and the synpp plumbing; every rule about who is a donor, which
attributes a donor carries and how the donor's trip chain is built lives in ``donor_pool`` and is
documented there.

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
import inspect
import logging
import os

import pandas as pd

from braunschweig.popsim import plan_validation as _plan_validation
from braunschweig.popsim import trips as _popsim_trips
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
#: MiD person columns that IMPROVE the diagnostics but are not required: ``M_HOFF`` (the
#: home-office-module flag, whose disagreement with ``starb2`` is reported by
#: ``donor_pool.select_home_office_day_donors``) and ``P_GEW`` (the MiD person expansion weight,
#: used ONLY to report the donor share weighted as well as unweighted). A delivery lacking either
#: is loaded without it and the omission is logged, never silently assumed.
PERSON_OPTIONAL_COLUMNS = ("M_HOFF", "P_GEW")
#: MiD Wege columns: exactly what the trip-table builder needs (imported from the single
#: committed definition, never re-typed) plus ``wegkm`` -- the raw trip length the
#: commute-distance fallback reads (``commute_day_state_reference.first_work_trip_length_km``).
WEGE_COLUMNS = tuple(MID_WEGE_REQUIRED_COLS) + ("wegkm",)
#: MiD household columns: ``H_GR`` (household size, binned by the matching module) and
#: ``H_ANZAUTO`` (car ownership).
HOUSEHOLD_COLUMNS = ("H_ID", "H_GR", "H_ANZAUTO")

# --------------------------------------------------------------------------- output schema

#: Columns of the ``attributes`` frame: ``donor_pool.donor_attributes``' own columns plus the
#: three ``donor_pool.attach_trip_derived_attributes`` reads from the BUILT chains (``n_trips``,
#: ``has_education_leg``, ``has_work_leg``; rulings R7 and R9).
ATTRIBUTE_COLUMNS = ("donor_id", "H_ID", "P_ID", "sex", "age", "age_class", "employed",
                     "has_children_u14", "has_car", "has_active_escort", "household_size",
                     "distance_km", "distance_class", "distance_source",
                     "n_trips", "has_education_leg", "has_work_leg")
#: Columns of the ``trips`` frame (``donor_pool.donor_trips``): the ``synthesis.population.trips``
#: CONTRACT with ``person_id`` renamed to ``donor_id``, plus the two documented extras.
TRIP_COLUMNS = tuple("donor_id" if column == "person_id" else column for column in CONTRACT) \
    + ("euclidean_distance", "trip_key")

#: Helper modules whose source is folded into :func:`validate`'s token. synpp hashes only THIS
#: file's source, so without them an edit to the pure donor-pool builder (or to the trip-table
#: construction underneath it) would silently reuse a stale cached donor pool -- the exact hazard
#: recorded for ``braunschweig.popsim.trips_stage`` (see its ``validate`` docstring).
_HELPER_MODULES = (_donor_pool, _popsim_trips, _plan_validation)


def configure(context):
    context.config(KEY_MID_DIR)
    context.config(KEY_ESCORT_PURPOSE, DEFAULT_ESCORT_PURPOSE)
    context.config(KEY_ESCORT_PASSIVE_EDUCATION, DEFAULT_ESCORT_PASSIVE_EDUCATION)
    context.config(KEY_EXPLICIT_ROUND_TRIP_PURPOSES, DEFAULT_EXPLICIT_ROUND_TRIP_PURPOSES)
    context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.config("random_seed")


def validate(context):
    """Cache token: the raw MiD input sizes plus the pure builders' source hash.

    ``0`` when the model is disabled (the stage reads nothing, so nothing can invalidate it).
    Otherwise ``"<summed byte size of the three raw MiD files>-<md5 of :data:`_HELPER_MODULES`>"``:
    the size half notices a re-delivered MiD extract, the hash half notices an edit to the pure
    donor-pool/trip-construction code that synpp's own per-file hashing would miss (see
    :data:`_HELPER_MODULES`). A missing raw file raises ``RuntimeError`` naming it -- the pipeline
    must fail here, at validation time, not halfway through a run.
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
                       "reported as absent rather than assumed.", _LOG_TAG, optional_absent)
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


def execute(context):
    """Build the donor pool (or the empty OFF-path output).

    Stage-level diagnostics added on top of the builder's own (see the module docstring):
    ``enabled``, ``n_mid_persons`` / ``n_mid_trips`` / ``n_mid_households`` (raw rows read),
    ``donor_share_unweighted`` and ``donor_share_weighted`` (share of MiD persons selected as
    home-office-day donors; the weighted one is ``NaN`` when the delivery carries no ``P_GEW``).
    """
    if not bool(context.config(KEY_ENABLED)):
        logger.info("%s %s is false -- returning an empty donor pool (no raw MiD is read).",
                    _LOG_TAG, KEY_ENABLED)
        return _empty_output()

    mid_dir = context.config(KEY_MID_DIR)
    logger.info("%s reading the raw MiD delivery from %s", _LOG_TAG, mid_dir)
    persons = _read_persons(mid_dir)
    wege = _read_columns(mid_dir, WEGE_FILE, WEGE_COLUMNS)
    households = _read_columns(mid_dir, HOUSEHOLDS_FILE, HOUSEHOLD_COLUMNS)
    logger.info("%s read %d persons, %d trips, %d households", _LOG_TAG,
                len(persons), len(wege), len(households))

    attributes, trips, diagnostics = build_home_office_donor_pool(
        persons, wege, households,
        random_seed=int(context.config("random_seed")),
        escort_purpose=bool(context.config(KEY_ESCORT_PURPOSE)),
        escort_passive_education=bool(context.config(KEY_ESCORT_PASSIVE_EDUCATION)),
        explicit_round_trip_purposes=bool(context.config(KEY_EXPLICIT_ROUND_TRIP_PURPOSES)),
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
