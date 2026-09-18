"""synpp stage: the general day-absence state for every person (issue #370, ADR-0110).

Owns only the synpp plumbing and the guards; every rule lives in
:mod:`braunschweig.synthesis.day_absence.absence`. Output ``{"absence": frame, "diagnostics": dict}``
with EXACTLY one row per enriched person (asserted). OFF: every person ``present`` with reason
``disabled``, no reference file is read, diagnostics ``{"enabled": False}`` -- but the frame's
SCHEMA (dtypes) and its derived ``age_band`` / ``household_size_class`` attributes are identical to
the ON path (see :func:`_disabled_frame`), so a consumer never has to branch on the flag.

Escort protection (issue #425, ADR-0110 Amendment 2): with ``day_absence_escort_protection_enabled``
the stage ALSO declares ``synthesis.population.trips`` (the pre-assignment trips) and hands the
persons carrying an escort leg (:func:`braunschweig.synthesis.escort_duty.escort_person_ids`) to
the draw as ineligible for the individual stage. The input is declared ONLY when the flag is on
(the conditional-declaration pattern of :mod:`braunschweig.matsim.scenario.population`), so the
OFF path has exactly the inputs it had before and no new DAG edge.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import numbers
import os

import numpy as np
import pandas as pd

from braunschweig.calibration import srv_absence
from braunschweig.synthesis import escort_duty
from braunschweig.synthesis.day_absence import absence as _absence
from braunschweig.synthesis.day_absence.absence import (ABSENCE_COLUMNS, DAY_ABSENCE_SEED_OFFSET,
                                                          REASON_DISABLED, STATE_PRESENT, draw_absence,
                                                          load_absence_reference)

logger = logging.getLogger(__name__)
_LOG_TAG = "[day absence]"
STAGE_NAME = "braunschweig.synthesis.day_absence.absence_stage"
#: Pure modules whose sources this stage's cache token must cover (see :func:`validate`). Both
#: modules are folded in: the draw rule itself lives in :mod:`absence` (the household/individual
#: composition, the overshoot guard), but the age-band edges/labels and the household-size-class
#: top live in :mod:`braunschweig.calibration.srv_absence` (:data:`AGE_BAND_LABELS`,
#: :data:`AGE_BAND_EDGES`, :data:`HOUSEHOLD_SIZE_CLASS_TOP`, :func:`age_band`,
#: :func:`household_size_class`) -- an edit there changes what a person is drawn AGAINST just as
#: much as an edit to :mod:`absence` changes HOW they are drawn, so both must devalidate the cache.
#: :mod:`braunschweig.synthesis.escort_duty` (issue #425) decides WHO is escort-protected, so an
#: edit to the escort-leg definition must devalidate the cache too.
_HELPER_MODULES = (_absence, srv_absence, escort_duty)
#: Pre-assignment trips, declared as an input ONLY when escort protection is on.
TRIPS_STAGE = "synthesis.population.trips"

KEY_ENABLED = "day_absence_enabled"
DEFAULT_ENABLED = True
KEY_HOUSEHOLD_STAGE = "day_absence_household_stage_enabled"
DEFAULT_HOUSEHOLD_STAGE = True
#: Issue #425 / ADR-0110 Amendment 2. CODE default False = byte-identical to PR #389 (arm 3);
#: configs/base_bs.yml sets true (the same code-default/config-default split as
#: KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE below).
KEY_ESCORT_PROTECTION = "day_absence_escort_protection_enabled"
DEFAULT_ESCORT_PROTECTION = False
KEY_MAX_BAND_DEVIATION_PP = "day_absence_max_band_deviation_pp"
DEFAULT_MAX_BAND_DEVIATION_PP = 1.0
#: Issue #388: minimum UNCLIPPED household size eligible for the individual residual stage. The
#: CONFIGURED default is 2 (households of size 1 carry the household-stage rate only, since the
#: SrV household-level clustering effect the individual residual corrects for is not well
#: identified for singles); the CODE default in draw_absence() stays 1 for byte-identical legacy
#: behaviour when this stage is bypassed (e.g. a direct absence.draw_absence() call in a test).
KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE = "day_absence_individual_stage_min_household_size"
DEFAULT_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE = 2
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
    context.config(KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE, DEFAULT_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE)
    # The trips are needed only to find escort legs; declare them -- and the DAG edge -- only on
    # the ON path (issue #425), so the OFF path keeps exactly the inputs it had before.
    context.config(KEY_ESCORT_PROTECTION, DEFAULT_ESCORT_PROTECTION)
    # Both flags, so the declaration matches exactly what execute() reads: with day_absence_enabled
    # false execute returns before ever touching the trips, and the edge would be dead weight.
    if context.config(KEY_ENABLED) and context.config(KEY_ESCORT_PROTECTION):
        context.stage(TRIPS_STAGE)


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


def _validate_individual_stage_min_household_size(raw_value):
    """Validate ``day_absence_individual_stage_min_household_size``: a genuine integer >= 1.

    ``int(raw_value)`` alone would silently TRUNCATE a non-integral float (e.g. ``2.7`` -> ``2``),
    hiding a likely configuration typo (CLAUDE.md "fail early", "no silent fallbacks") -- only a
    ``numbers.Integral`` (covers plain ``int`` AND numpy integer types such as ``numpy.int64``, e.g.
    when a config value has round-tripped through a numpy/pandas-backed loader; never ``bool``,
    which is technically an ``int`` subtype but not a meaningful household-size count) or a
    ``float``/``numpy.floating`` with no fractional part (e.g. ``2.0``) is accepted.
    """
    error = ValueError(f"{_LOG_TAG} {KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE} must be an integer >= 1, "
                       f"got {raw_value!r}")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (numbers.Integral, numbers.Real)):
        raise error
    if not isinstance(raw_value, numbers.Integral) and not float(raw_value).is_integer():
        raise error
    value = int(raw_value)
    if value < 1:
        raise error
    return value


def execute(context):
    """Draw the general day-absence state of every enriched person.

    Diagnostics keys on the ON path: ``enabled`` plus every key of
    :func:`braunschweig.synthesis.day_absence.absence.draw_absence` (``n_persons``,
    ``n_households``, ``n_absent_household``, ``n_absent_individual``, ``n_absent_total``,
    ``share_absent_total``, ``share_absent_in_fully_absent_households``, ``n_bands_overshoot``,
    ``n_bands_unreachable``, ``household_stage``, ``individual_stage_min_household_size``,
    ``n_persons_ineligible_individual_stage``, ``n_persons_ineligible_household_size``,
    ``n_persons_escort_protected``, ``by_band``, ``by_size_class``) plus ``n_band_guard_hits`` (see
    the deviation guard below) and ``escort_protection`` (the flag as read, issue #425). On the OFF
    path: ``{"enabled": False}`` only.

    Escort protection (issue #425): when ``day_absence_escort_protection_enabled`` is true the
    pre-assignment trips are read and every person with an escort leg becomes ineligible for the
    individual stage. Fallback transparency: the protected share is logged as a rate, and an EMPTY
    escort set next to a NON-EMPTY trips table WARNS -- that combination is the "escort purpose is
    off in the synthetic trips" defect class ``commute_day.state_stage`` warns about, not a
    population that escorts nobody.
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
    individual_stage_min_household_size = _validate_individual_stage_min_household_size(
        context.config(KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE))
    escort_protection = bool(context.config(KEY_ESCORT_PROTECTION))
    reference = load_absence_reference(os.path.join(str(context.config("data_path")), *SRV_SUBDIR))
    logger.info("%s parameters: household_stage=%s, %s=%d, %s=%s, random_seed=%d (+%d offset), reference "
                "bands %s, sizes %s", _LOG_TAG, household_stage, KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE,
                individual_stage_min_household_size, KEY_ESCORT_PROTECTION, escort_protection, random_seed,
                DAY_ABSENCE_SEED_OFFSET, {b: round(v, 4) for b, v in reference.p_absent_by_band.items()},
                {k: round(v, 4) for k, v in reference.p_all_absent_by_size.items()})
    escort_protected_person_ids = None
    if escort_protection:
        trips = context.stage(TRIPS_STAGE)
        escort_protected_person_ids = escort_duty.escort_person_ids(trips)
        if not escort_protected_person_ids and len(trips) > 0:
            logger.warning("%s %s is true but NO person carries an %r leg although the trips table has %d "
                           "rows -- this almost always means the escort purpose is OFF in the synthetic "
                           "trips, not that nobody escorts; the gate is inert in this run.", _LOG_TAG,
                           KEY_ESCORT_PROTECTION, escort_duty.ESCORT_PURPOSE, len(trips))
    rng = np.random.RandomState(random_seed + DAY_ABSENCE_SEED_OFFSET)
    absence, diagnostics = draw_absence(persons, reference, rng, household_stage=household_stage,
                                        individual_stage_min_household_size=individual_stage_min_household_size,
                                        escort_protected_person_ids=escort_protected_person_ids)
    assert len(absence) == len(persons) and not absence["person_id"].duplicated().any(), (
        f"{_LOG_TAG} the absence frame must carry exactly one row per person")
    n_guard_hits = 0
    for band, cell in diagnostics["by_band"].items():
        # Compact per-band line (final-review fix wave, MINOR finding 5): includes
        # n_eligible_present so the individual-stage eligibility gate's effect on each band is
        # visible even when the deviation guard below does not fire.
        logger.info("%s band %s: realised %.2f%% vs reference %.2f%% (n=%d, n_eligible=%d)", _LOG_TAG,
                   band, 100.0 * cell["realised_rate"], 100.0 * cell["reference_rate"], cell["n"],
                   cell["n_eligible_present"])
        if cell["n"] >= MIN_PERSONS_FOR_BAND_GUARD:
            deviation_pp = 100.0 * (cell["realised_rate"] - cell["reference_rate"])
            if abs(deviation_pp) > max_dev_pp:
                n_guard_hits += 1
                logger.warning("%s band %s realised %.2f%% vs reference %.2f%% (%.2f pp, n=%d) exceeds %s=%.2f pp",
                               _LOG_TAG, band, 100 * cell["realised_rate"], 100 * cell["reference_rate"],
                               deviation_pp, cell["n"], KEY_MAX_BAND_DEVIATION_PP, max_dev_pp)
    # Fallback-transparency logging for the individual-stage eligibility gate (issue #388, CLAUDE.md
    # "no silent fallbacks"): a per-size-class realised-vs-SrV comparison and the ineligible share.
    for size_class, cell in diagnostics["by_size_class"].items():
        logger.info("%s size %d: realised %.2f%% vs SrV %.2f%% (delta %.2f pp, n=%d)", _LOG_TAG,
                   size_class, 100.0 * cell["realised_rate"], 100.0 * cell["reference_rate"],
                   cell["delta_pp"], cell["n"])
    n_total = diagnostics["n_persons"]
    n_ineligible = diagnostics["n_persons_ineligible_individual_stage"]
    n_by_size = diagnostics["n_persons_ineligible_household_size"]
    n_by_escort = diagnostics["n_persons_escort_protected"]
    ineligible_rate = n_ineligible / n_total if n_total else float("nan")
    if escort_protection:
        logger.info("%s %d/%d (%.2f%%) persons are present but ineligible for the individual stage: %d by "
                   "household size below %s=%d, %d by escort protection", _LOG_TAG, n_ineligible, n_total,
                   100.0 * ineligible_rate, n_by_size, KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE,
                   individual_stage_min_household_size, n_by_escort)
    else:
        # An inactive gate is not named, so an OFF-path log never suggests escort protection ran.
        logger.info("%s %d/%d (%.2f%%) persons are present but ineligible for the individual stage "
                   "(household size below %s=%d)", _LOG_TAG, n_ineligible, n_total,
                   100.0 * ineligible_rate, KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE,
                   individual_stage_min_household_size)
    if escort_protection:
        # Fallback-transparency rate for the escort gate on its own (issue #425).
        n_present = n_total - diagnostics["n_absent_household"]
        # n_by_escort is the gate's MARGINAL effect (escort leg, not already excluded by household
        # size), which is what "how much did this gate change" means; the wording says so, because
        # "present persons with an escort leg" alone would be a larger, different number.
        logger.info("%s escort protection: %d/%d present persons (%.2f%%) are excluded from the "
                   "individual stage by escort protection (escort leg, not already excluded by "
                   "household size)", _LOG_TAG, n_by_escort, n_present,
                   100.0 * n_by_escort / max(n_present, 1))
    diagnostics = dict(diagnostics)
    diagnostics["enabled"] = True
    diagnostics["escort_protection"] = escort_protection
    diagnostics["n_band_guard_hits"] = n_guard_hits
    return {"absence": absence, "diagnostics": diagnostics}
