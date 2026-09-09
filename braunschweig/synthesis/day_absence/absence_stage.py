"""synpp stage: the general day-absence state for every person (issue #370, ADR-0110).

Owns only the synpp plumbing and the guards; every rule lives in
:mod:`braunschweig.synthesis.day_absence.absence`. Output ``{"absence": frame, "diagnostics": dict}``
with EXACTLY one row per enriched person (asserted). OFF: every person ``present`` with reason
``disabled``, no reference file is read, diagnostics ``{"enabled": False}`` -- but the frame's
SCHEMA (dtypes) and its derived ``age_band`` / ``household_size_class`` attributes are identical to
the ON path (see :func:`_disabled_frame`), so a consumer never has to branch on the flag.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import os

import numpy as np
import pandas as pd

from braunschweig.calibration import srv_absence
from braunschweig.synthesis.day_absence import absence as _absence
from braunschweig.synthesis.day_absence.absence import (ABSENCE_COLUMNS, DAY_ABSENCE_SEED_OFFSET,
                                                          REASON_DISABLED, STATE_PRESENT, draw_absence,
                                                          load_absence_reference)

logger = logging.getLogger(__name__)
_LOG_TAG = "[day absence]"
STAGE_NAME = "braunschweig.synthesis.day_absence.absence_stage"
#: Pure modules whose sources this stage's cache token must cover (see :func:`validate`).
_HELPER_MODULES = (_absence,)

KEY_ENABLED = "day_absence_enabled"
DEFAULT_ENABLED = True
KEY_HOUSEHOLD_STAGE = "day_absence_household_stage_enabled"
DEFAULT_HOUSEHOLD_STAGE = True
KEY_MAX_BAND_DEVIATION_PP = "day_absence_max_band_deviation_pp"
DEFAULT_MAX_BAND_DEVIATION_PP = 1.0
#: Bands with fewer persons than this are not judged against the deviation guard (sampling noise).
MIN_PERSONS_FOR_BAND_GUARD = 1000
#: Subdirectory of ``data_path`` holding the committed SrV reference tables.
SRV_SUBDIR = ("braunschweig", "srv")


def validate(context):
    """synpp validation token: md5 over the pure helper modules' sources.

    synpp hashes only THIS module's source, so an edit to a helper module it imports would
    otherwise leave the cached stage output in place although the rules that produced it changed.
    The token folds those sources in, so a helper edit devalidates the stage exactly like an edit
    here (same mechanism as ``braunschweig.synthesis.commute_day.state_stage.validate``).
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    context.stage("synthesis.population.enriched")
    context.config("random_seed")
    context.config("data_path")
    context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.config(KEY_HOUSEHOLD_STAGE, DEFAULT_HOUSEHOLD_STAGE)
    context.config(KEY_MAX_BAND_DEVIATION_PP, DEFAULT_MAX_BAND_DEVIATION_PP)


def _disabled_frame(persons):
    """OFF-path frame: schema- and attribute-identical to the ON-path frame, without any I/O.

    ``household_size_class`` and ``age_band`` are derived with the SAME pure helpers
    :func:`~braunschweig.synthesis.day_absence.absence.draw_absence` itself calls
    (:func:`braunschweig.calibration.srv_absence.household_size_class` /
    :func:`~braunschweig.calibration.srv_absence.age_band`), so the OFF frame's dtypes never drift
    from the ON frame's -- a consumer must be able to treat the two paths identically (this is the
    schema-stability rule the OFF path must not violate).

    Unlike the ON path (:func:`~braunschweig.synthesis.day_absence.absence.draw_absence`, which
    RAISES on a missing age), a missing age here stays ``NaN`` in ``age_band`` rather than failing:
    the OFF path must never fail on data the ON path would reject. A non-zero count is logged.
    """
    household_sizes = persons.groupby("household_id")["person_id"].transform("size").to_numpy()
    household_size_class = srv_absence.household_size_class(household_sizes)
    age_band = srv_absence.age_band(persons["age"])
    n_missing_age = int(age_band.isna().sum())
    if n_missing_age > 0:
        logger.warning("%s %s is false -- %d/%d persons have no age and get age_band = NaN on the "
                       "disabled path (the enabled path would raise on this).", _LOG_TAG, KEY_ENABLED,
                       n_missing_age, len(persons))
    return pd.DataFrame({
        "person_id": persons["person_id"].to_numpy(), "household_id": persons["household_id"].to_numpy(),
        "day_absence_state": STATE_PRESENT, "age_band": age_band.to_numpy(),
        "household_size_class": household_size_class,
        "p_household": 0.0, "p_individual": 0.0, "reason": REASON_DISABLED})[list(ABSENCE_COLUMNS)]


def execute(context):
    """Draw the general day-absence state of every enriched person.

    Diagnostics keys on the ON path: ``enabled`` plus every key of
    :func:`braunschweig.synthesis.day_absence.absence.draw_absence` (``n_persons``,
    ``n_households``, ``n_absent_household``, ``n_absent_individual``, ``n_absent_total``,
    ``share_absent_total``, ``share_absent_in_fully_absent_households``, ``n_bands_overshoot``,
    ``household_stage``, ``by_band``) plus ``n_band_guard_hits`` (see the deviation guard below).
    On the OFF path: ``{"enabled": False}`` only.
    """
    persons = context.stage("synthesis.population.enriched")
    for column in ("person_id", "household_id", "age"):
        if column not in persons.columns:
            raise ValueError(f"{_LOG_TAG} synthesis.population.enriched lacks {column!r}")
    if not bool(context.config(KEY_ENABLED)):
        logger.info("%s %s is false -- every one of the %d persons is present.", _LOG_TAG, KEY_ENABLED, len(persons))
        return {"absence": _disabled_frame(persons), "diagnostics": {"enabled": False}}
    random_seed = int(context.config("random_seed"))
    household_stage = bool(context.config(KEY_HOUSEHOLD_STAGE))
    max_dev_pp = float(context.config(KEY_MAX_BAND_DEVIATION_PP))
    reference = load_absence_reference(os.path.join(str(context.config("data_path")), *SRV_SUBDIR))
    logger.info("%s parameters: household_stage=%s, random_seed=%d (+%d offset), reference bands %s, sizes %s",
                _LOG_TAG, household_stage, random_seed, DAY_ABSENCE_SEED_OFFSET,
                {b: round(v, 4) for b, v in reference.p_absent_by_band.items()},
                {k: round(v, 4) for k, v in reference.p_all_absent_by_size.items()})
    rng = np.random.RandomState(random_seed + DAY_ABSENCE_SEED_OFFSET)
    absence, diagnostics = draw_absence(persons, reference, rng, household_stage=household_stage)
    assert len(absence) == len(persons) and not absence["person_id"].duplicated().any(), (
        f"{_LOG_TAG} the absence frame must carry exactly one row per person")
    n_guard_hits = 0
    for band, cell in diagnostics["by_band"].items():
        if cell["n"] >= MIN_PERSONS_FOR_BAND_GUARD:
            deviation_pp = 100.0 * (cell["realised_rate"] - cell["reference_rate"])
            if abs(deviation_pp) > max_dev_pp:
                n_guard_hits += 1
                logger.warning("%s band %s realised %.2f%% vs reference %.2f%% (%.2f pp, n=%d) exceeds %s=%.2f pp",
                               _LOG_TAG, band, 100 * cell["realised_rate"], 100 * cell["reference_rate"],
                               deviation_pp, cell["n"], KEY_MAX_BAND_DEVIATION_PP, max_dev_pp)
    diagnostics = dict(diagnostics); diagnostics["enabled"] = True; diagnostics["n_band_guard_hits"] = n_guard_hits
    return {"absence": absence, "diagnostics": diagnostics}
