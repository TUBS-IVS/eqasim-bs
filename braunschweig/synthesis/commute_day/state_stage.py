"""synpp stage: the reporting-day ``commute_day_state`` draw (ADR-0104, issue #244, Phase B Task 4).

Wires the pure modules of this package into the pipeline; every rule lives in them and is
documented there, never re-stated here:

* :func:`braunschweig.synthesis.commute_day.state.donor_distance_class_from_trips` -- the DONOR's
  commute-distance class, from the person's own pre-assignment trips (ADR-0104 Amendment 1: the
  donor's first valid work-trip length is the PRIMARY source; ``P_ARB_ENTF`` is a
  home-office-module question and is a cross-check only, never merged in here).
* :func:`braunschweig.synthesis.commute_day.state.assigned_distance_class` -- the ASSIGNED class,
  from the synthesised home/workplace geometry.
* :func:`braunschweig.synthesis.commute_day.state.draw_states` -- the seeded state draw.
* :func:`braunschweig.synthesis.commute_day.matching.match_home_office_donors` -- the donor for
  every person drawn to ``home``.

Universe: every person with an assigned WORK location (the work half of
``synthesis.population.spatial.primary.locations``). A worker drawn to ``home`` for whom the
matching finds no donor at any coarsening level is DOWNGRADED to ``at_workplace`` with
``reason = "home_not_replaceable"`` -- ADR-0104's documented default -- and counted; above
``commute_day_max_not_replaceable_share`` of the ``home`` cohort the stage RAISES instead, because
a donor pool that cannot serve most of the cohort makes the drawn state shares meaningless rather
than merely sparse (CLAUDE.md "Fallback transparency").

Output: ``{"states": DataFrame, "diagnostics": dict}``. ``states`` carries one row per worker,
columns :data:`STATE_COLUMNS`; see :func:`execute` for the diagnostics keys. With
``commute_day_state_enabled`` FALSE every worker gets ``at_workplace`` /
``reason = "disabled"`` and the diagnostics are ``{"enabled": False}``, so the stage stays
readable by its consumers without a branch of their own.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.calibration.commute_day_state_reference import MID_CHILD_MAX_AGE
from braunschweig.calibration.commute_day_state_reference import load_workday_location_table
from braunschweig.constants import ROUTED_DETOUR_FACTOR
from braunschweig.popsim.chain_matching import derive_age_class
from braunschweig.synthesis.commute_day.matching import match_home_office_donors
from braunschweig.synthesis.commute_day.state import (
    COMMUTE_DAY_SEED_OFFSET,
    assigned_distance_class,
    donor_distance_class_from_trips,
    draw_states,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day state]"

# --------------------------------------------------------------------------- config keys

KEY_ENABLED = "commute_day_state_enabled"
DEFAULT_ENABLED = True
#: Assigned distance (km) above which a not-kept worker may become ``absent`` (a weekly/far
#: commuter who is simply not in the region on the reporting day) instead of ``home``.
KEY_FAR_THRESHOLD_KM = "commute_day_far_threshold_km"
DEFAULT_FAR_THRESHOLD_KM = 200.0
#: Share (0..1) of not-kept FAR workers that become ``absent`` rather than ``home``.
KEY_ABSENT_SHARE_FAR = "commute_day_absent_share_far"
DEFAULT_ABSENT_SHARE_FAR = 1.0
#: Above this share of the ``home`` cohort without any donor the stage raises (see the module
#: docstring).
KEY_MAX_NOT_REPLACEABLE_SHARE = "commute_day_max_not_replaceable_share"
DEFAULT_MAX_NOT_REPLACEABLE_SHARE = 0.5
#: Euclidean -> routed conversion for the ASSIGNED commute distance. Same key and default as
#: ``braunschweig.analysis.synthesis.work_participation_by_kreis`` (the Phase A measurement), so
#: the measured and the modelled assigned classes are built from the identical convention.
KEY_DETOUR = "cds_detour_factor"
DEFAULT_DETOUR_FACTOR = ROUTED_DETOUR_FACTOR

#: Subdirectory of ``data_path`` holding the committed MiD reference tables.
MID_REFERENCE_SUBDIR = ("braunschweig", "mid")

#: eqasim trip purpose marking an escort leg (issue #201); a person with such a leg on their own
#: pre-assignment day evidences presence at home and may become ``home`` but never ``absent``
#: (ADR-0104 Assumption 4).
ESCORT_PURPOSE = "escort"

#: Columns of the ``states`` frame this stage returns.
STATE_COLUMNS = ("person_id", "commute_day_state", "p_keep", "redraw_eligible", "reason",
                 "donor_id", "coarsening_level", "assigned_distance_class",
                 "donor_distance_class", "distance_km")

#: ``reason`` value of a ``home`` worker downgraded to ``at_workplace`` because the donor pool
#: held no replaceable day for them.
REASON_NOT_REPLACEABLE = "home_not_replaceable"
#: ``reason`` value on the OFF path.
REASON_DISABLED = "disabled"

#: Below this share of workers whose DONOR class came from the primary source (a work-trip
#: length, ADR-0104 Amendment 1) the stage warns: a majority without a donor class means most
#: workers can never be re-drawn at all, which is a broken join far more often than a real
#: property of the population.
WARN_PRIMARY_DONOR_SOURCE_SHARE = 0.5


def configure(context):
    context.stage("synthesis.population.trips")
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("braunschweig.synthesis.commute_day.home_office_donors_stage")
    context.config("random_seed")
    context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.config(KEY_FAR_THRESHOLD_KM, DEFAULT_FAR_THRESHOLD_KM)
    context.config(KEY_ABSENT_SHARE_FAR, DEFAULT_ABSENT_SHARE_FAR)
    context.config(KEY_MAX_NOT_REPLACEABLE_SHARE, DEFAULT_MAX_NOT_REPLACEABLE_SHARE)
    context.config(KEY_DETOUR, DEFAULT_DETOUR_FACTOR)
    context.config("data_path")


def _require_columns(frame, columns, what):
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)[:20]})")


def _escort_person_ids(trips):
    """Person ids with an escort leg on their own pre-assignment reporting day.

    Both trip ends are inspected (``following_purpose`` and ``preceding_purpose``): the escorting
    person's outbound leg ARRIVES at the escort activity, the return leg DEPARTS from it, and
    either is evidence of the escort duty (ADR-0104 Assumption 4).
    """
    _require_columns(trips, ("person_id", "following_purpose", "preceding_purpose"),
                     "the trips frame")
    is_escort = ((trips["following_purpose"] == ESCORT_PURPOSE)
                 | (trips["preceding_purpose"] == ESCORT_PURPOSE))
    return set(trips.loc[is_escort, "person_id"])


def _households_with_children(persons):
    """Household ids with at least one member aged ``<= MID_CHILD_MAX_AGE``.

    The same child-age bound the DONOR side uses (``donor_pool.donor_attributes`` reads
    ``MID_CHILD_MAX_AGE`` from the reference module), so the matching criterion means the same
    thing on both sides.
    """
    _require_columns(persons, ("household_id", "age"), "the enriched persons frame")
    is_child = pd.to_numeric(persons["age"], errors="coerce") <= MID_CHILD_MAX_AGE
    return set(persons.loc[is_child, "household_id"])


def _persons_home_frame(person_ids, workers, persons, escort_persons):
    """Attribute frame for :func:`match_home_office_donors`, one row per ``home`` worker.

    Built from the enriched population so every criterion is the model's own attribute, never a
    guess: ``sex`` and ``age`` verbatim, ``age_class`` via
    :func:`braunschweig.popsim.chain_matching.derive_age_class` (the identical binning the donor
    side uses), ``household_size`` UNBINNED (the matching module bins both sides itself, ruling
    R1), ``has_car`` from ``number_of_cars > 0``, ``has_children_u14`` from the ages of the
    person's own household members, ``has_active_escort`` from the person's own trips, and
    ``has_license`` ONLY when the enriched frame carries it (the MiD donor pool does not, so the
    matching module then skips that soft criterion rather than inventing a value).
    """
    _require_columns(persons, ("person_id", "household_id", "sex", "age", "household_size",
                               "number_of_cars"), "the enriched persons frame")
    households_with_children = _households_with_children(persons)

    columns = ["person_id", "household_id", "sex", "age", "household_size", "number_of_cars"]
    if "has_license" in persons.columns:
        columns.append("has_license")
    frame = persons.loc[persons["person_id"].isin(person_ids), columns].copy()
    frame["age_class"] = derive_age_class(frame["age"].to_numpy())
    frame["has_car"] = frame["number_of_cars"] > 0
    frame["has_children_u14"] = frame["household_id"].isin(households_with_children)
    frame["has_active_escort"] = frame["person_id"].isin(escort_persons)
    frame = frame.merge(workers[["person_id", "assigned_distance_class"]], on="person_id",
                        how="left")

    n_missing = len(person_ids) - len(frame)
    if n_missing:
        raise ValueError(
            f"{_LOG_TAG} {n_missing}/{len(person_ids)} worker(s) drawn to 'home' have no row in "
            "the enriched population; the person_id join between the work locations and "
            "synthesis.population.enriched is broken.")
    return frame


def _donor_source_by_assigned_class(workers):
    """Per-assigned-class rate at which the PRIMARY donor-distance source was available.

    ADR-0104 Amendment 1 duty: the donor class comes from the donor's own work-trip length, which
    a donor who worked at home or did not work that day simply does not have. That residual must
    be reported per class, never passed over silently -- a worker without a donor class is never
    re-drawn at all (``keep_probability`` returns 1.0), so an unnoticed collapse of this rate
    would silently disable the model for part of the population.

    Returns a dict, assigned class (``"unknown"`` for a missing class) ->
    ``{"n_workers", "n_donor_from_trip_length", "n_donor_missing", "share_primary"}``.
    """
    result: dict = {}
    has_donor_class = workers["donor_distance_class"].notna()
    for label, group in zip(workers["assigned_distance_class"], has_donor_class):
        key = "unknown" if pd.isna(label) else label
        cell = result.setdefault(key, {"n_workers": 0, "n_donor_from_trip_length": 0,
                                       "n_donor_missing": 0, "share_primary": 0.0})
        cell["n_workers"] += 1
        if group:
            cell["n_donor_from_trip_length"] += 1
        else:
            cell["n_donor_missing"] += 1
    for cell in result.values():
        cell["share_primary"] = cell["n_donor_from_trip_length"] / max(cell["n_workers"], 1)
    return result


def _disabled_states(worker_ids):
    """OFF-path states frame: every worker ``at_workplace`` with ``reason = "disabled"``."""
    return pd.DataFrame({
        "person_id": np.asarray(worker_ids),
        "commute_day_state": "at_workplace",
        "p_keep": 1.0,
        "redraw_eligible": False,
        "reason": REASON_DISABLED,
        "donor_id": np.nan,
        "coarsening_level": np.nan,
        "assigned_distance_class": None,
        "donor_distance_class": None,
        "distance_km": np.nan,
    })[list(STATE_COLUMNS)]


def execute(context):
    """Draw the reporting-day state of every worker.

    Diagnostics keys: ``enabled``; every key of
    :func:`braunschweig.synthesis.commute_day.state.draw_states` (``n_workers``,
    ``n_redraw_eligible``, ``n_donor_class_missing``, ``n_at_workplace``, ``n_home``,
    ``n_absent``, ``n_escort_protected``, ``by_assigned_class``); the matching diagnostics under
    ``matching`` (see :func:`braunschweig.synthesis.commute_day.matching.match_home_office_donors`);
    ``n_home_drawn`` / ``n_home_matched`` / ``n_home_not_replaceable`` /
    ``share_home_not_replaceable`` (the downgrade, see the module docstring);
    ``donor_source_by_assigned_class`` (ADR-0104 Amendment 1, see
    :func:`_donor_source_by_assigned_class`); ``share_donor_source_primary`` (the same rate over
    all workers); and ``final_state_counts`` (state counts AFTER the downgrade -- the drawn counts
    in ``n_at_workplace`` / ``n_home`` / ``n_absent`` are the draw's own, before it).
    """
    df_trips = context.stage("synthesis.population.trips")
    df_persons = context.stage("synthesis.population.enriched")
    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, _df_education = context.stage("synthesis.population.spatial.primary.locations")
    donor_attributes, _donor_trips, _donor_diagnostics = context.stage(
        "braunschweig.synthesis.commute_day.home_office_donors_stage")

    _require_columns(df_work, ("person_id",), "the primary work locations frame")
    worker_ids = df_work["person_id"].to_numpy()

    if not bool(context.config(KEY_ENABLED)):
        logger.info("%s %s is false -- every one of the %d workers stays at_workplace.",
                    _LOG_TAG, KEY_ENABLED, len(worker_ids))
        return {"states": _disabled_states(worker_ids), "diagnostics": {"enabled": False}}

    detour_factor = float(context.config(KEY_DETOUR))
    far_threshold_km = float(context.config(KEY_FAR_THRESHOLD_KM))
    absent_share_far = float(context.config(KEY_ABSENT_SHARE_FAR))
    max_not_replaceable_share = float(context.config(KEY_MAX_NOT_REPLACEABLE_SHARE))
    random_seed = int(context.config("random_seed"))
    data_path = context.config("data_path")
    logger.info(
        "%s parameters: detour_factor=%.3f, far_threshold_km=%.1f, absent_share_far=%.3f, "
        "max_not_replaceable_share=%.3f, random_seed=%d (+%d offset)", _LOG_TAG, detour_factor,
        far_threshold_km, absent_share_far, max_not_replaceable_share, random_seed,
        COMMUTE_DAY_SEED_OFFSET)

    table = load_workday_location_table(os.path.join(str(data_path), *MID_REFERENCE_SUBDIR))
    escort_persons = _escort_person_ids(df_trips)

    donor_classes = donor_distance_class_from_trips(df_trips)
    assigned = assigned_distance_class(df_work, df_home,
                                       df_persons[["person_id", "household_id"]], detour_factor)
    workers = assigned.merge(donor_classes[["person_id", "donor_distance_class"]],
                             on="person_id", how="left")

    donor_source = _donor_source_by_assigned_class(workers)
    share_primary = (workers["donor_distance_class"].notna().sum() / max(len(workers), 1))
    logger.info("%s donor-distance source (ADR-0104 Amendment 1) per assigned class: %s",
                _LOG_TAG, {key: f"{cell['n_donor_from_trip_length']}/{cell['n_workers']} "
                                f"({100.0 * cell['share_primary']:.1f}%)"
                           for key, cell in sorted(donor_source.items())})
    if share_primary < WARN_PRIMARY_DONOR_SOURCE_SHARE:
        logger.warning(
            "%s only %.1f%% of workers have a PRIMARY donor distance (a work-trip length); below "
            "%.0f%% the model cannot re-draw the majority of the population, which usually "
            "signals a broken person_id/trips join rather than a real property of the population.",
            _LOG_TAG, 100.0 * share_primary, 100.0 * WARN_PRIMARY_DONOR_SOURCE_SHARE)

    rng = np.random.RandomState(random_seed + COMMUTE_DAY_SEED_OFFSET)
    states, diagnostics = draw_states(workers, table, rng, far_threshold_km=far_threshold_km,
                                      absent_share_far=absent_share_far,
                                      escort_persons=escort_persons)

    home_person_ids = states.loc[states["commute_day_state"] == "home", "person_id"]
    persons_home = _persons_home_frame(home_person_ids, workers, df_persons, escort_persons)
    matches, matching_diagnostics = match_home_office_donors(persons_home, donor_attributes, rng)

    states = states.merge(matches, on="person_id", how="left")
    states = states.merge(workers[["person_id", "assigned_distance_class",
                                   "donor_distance_class", "distance_km"]],
                          on="person_id", how="left")

    # ADR-0104's documented default for a home person the donor pool cannot serve: downgrade to
    # at_workplace, counted and reported -- never silently left as a 'home' person whose day was
    # not actually replaced (plan_replacement would keep their ORIGINAL commute day).
    is_not_replaceable = (states["commute_day_state"] == "home") & states["donor_id"].isna()
    n_home_drawn = int((states["commute_day_state"] == "home").sum())
    n_not_replaceable = int(is_not_replaceable.sum())
    states.loc[is_not_replaceable, "commute_day_state"] = "at_workplace"
    states.loc[is_not_replaceable, "reason"] = REASON_NOT_REPLACEABLE
    share_not_replaceable = n_not_replaceable / max(n_home_drawn, 1)

    final_counts = states["commute_day_state"].value_counts().to_dict()
    n_workers = len(states)
    diagnostics = dict(diagnostics)
    diagnostics["enabled"] = True
    diagnostics["matching"] = matching_diagnostics
    diagnostics["n_home_drawn"] = n_home_drawn
    diagnostics["n_home_matched"] = n_home_drawn - n_not_replaceable
    diagnostics["n_home_not_replaceable"] = n_not_replaceable
    diagnostics["share_home_not_replaceable"] = share_not_replaceable
    diagnostics["donor_source_by_assigned_class"] = donor_source
    diagnostics["share_donor_source_primary"] = float(share_primary)
    diagnostics["final_state_counts"] = {str(key): int(value)
                                         for key, value in final_counts.items()}

    logger.info(
        "%s final states over %d workers: %s; re-draw eligible %.1f%%, %d/%d home persons not "
        "replaceable (%.1f%%), coarsening levels used %s", _LOG_TAG, n_workers,
        {key: f"{value} ({100.0 * value / max(n_workers, 1):.1f}%)"
         for key, value in sorted(diagnostics["final_state_counts"].items())},
        100.0 * diagnostics["n_redraw_eligible"] / max(n_workers, 1), n_not_replaceable,
        n_home_drawn, 100.0 * share_not_replaceable,
        {level: count for level, count in matching_diagnostics["matched_by_level"].items()
         if count > 0})

    if share_not_replaceable > max_not_replaceable_share:
        raise RuntimeError(
            f"{_LOG_TAG} {n_not_replaceable}/{n_home_drawn} persons drawn to 'home' "
            f"({100.0 * share_not_replaceable:.1f}%) have no donor at any coarsening level, above "
            f"the configured {KEY_MAX_NOT_REPLACEABLE_SHARE} = {max_not_replaceable_share:.3f}. "
            "The drawn home share would then be governed by the donor pool's gaps rather than by "
            "the model; check the donor pool size and the hard matching criteria "
            "(has_active_escort, has_children_u14, has_car) before raising the threshold.")

    return {"states": states[list(STATE_COLUMNS)].reset_index(drop=True),
            "diagnostics": diagnostics}
