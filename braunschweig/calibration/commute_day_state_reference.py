"""Reference tables for the commute-day-state model (spec 2026-09-04-commute-day-state-design.md).

Pure builders over MiD 2023 person/trip frames (raw microdata lives on the run server only;
this module is called by scripts/extract_mid_workday_location.py there) and loaders for the
committed aggregates. Not imported by any population-synthesis or location stage.

MiD 2023 variables (SUF B1): ``M_HOFF`` (home-office module asked), ``arbwo`` (reporting day is a
weekday), ``P_STARB1`` (worked on the reporting day: 1 yes), ``starb2`` (work location on the day:
1 at home, 2 usual workplace, 3/4/5/96 other places, 409 did not work), ``P_ARB_ENTF`` (distance to
the usual workplace in km, top-coded at 200; 996/999 missing), ``P_GEW`` (person weight).
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

COMMUTE_CLASS_EDGES_KM = (0.0, 10.0, 25.0, 50.0, 100.0, 200.0, float("inf"))
COMMUTE_CLASS_LABELS = ("lt10", "10_25", "25_50", "50_100", "100_200", "gt200")

MID_MODULE = 1
MID_WEEKDAY = 1
MID_WORKED_ON_DAY = 1
MID_AT_HOME = 1
MID_AT_WORKPLACE = 2
MID_OTHER_PLACE = (3, 4, 5, 96)
MID_DID_NOT_WORK = 409
MID_DISTANCE_MISSING = (996.0, 999.0)
MID_DISTANCE_TOPCODE_KM = 200.0

WORKDAY_LOCATION_TABLE = "mid2023_workday_location_by_commute_distance.csv"
HOME_OFFICE_DONOR_POOL_TABLE = "mid2023_home_office_donor_pool.csv"

SHARE_COLUMNS = ("share_at_workplace", "share_at_home", "share_did_not_work",
                 "share_other_place", "share_missing")


def classify_commute_distance(km) -> str | None:
    """Commute-distance class label for a distance in kilometres.

    Returns ``None`` for missing (``NaN``) or non-positive distances. A distance of exactly
    ``200.0`` is classified as ``"100_200"`` rather than ``"gt200"`` because the MiD top-code
    means "200 km or more, reported as 200" -- it is not evidence the true distance falls in the
    open-ended tail, so it is kept in the highest closed class instead of the unbounded one.
    """
    if km is None or pd.isna(km) or km <= 0:
        return None
    if km == MID_DISTANCE_TOPCODE_KM:
        return "100_200"
    idx = int(np.digitize([km], COMMUTE_CLASS_EDGES_KM[1:-1])[0])
    return COMMUTE_CLASS_LABELS[idx]


def _mid_weekday_module(persons: pd.DataFrame) -> pd.DataFrame:
    """Restrict to persons who answered the home-office module on a reporting weekday.

    Adds a ``distance_class`` column (one of ``COMMUTE_CLASS_LABELS`` or ``NaN`` when
    ``P_ARB_ENTF`` is missing/out of range). Logs how many persons were dropped by the
    module/weekday filter and how many of the remainder have a missing distance, so a silent
    collapse of the input (e.g. a wrong column name) is visible in the logs rather than only in
    downstream share numbers.
    """
    n_input = len(persons)
    sel = persons[(persons["M_HOFF"] == MID_MODULE) & (persons["arbwo"] == MID_WEEKDAY)].copy()
    n_dropped = n_input - len(sel)
    dist = pd.to_numeric(sel["P_ARB_ENTF"], errors="coerce")
    dist = dist.where(~dist.isin(MID_DISTANCE_MISSING))
    sel["distance_class"] = [classify_commute_distance(d) for d in dist]
    n_missing_distance = int(sel["distance_class"].isna().sum())
    logger.info("[mid weekday module filter] %d/%d persons kept (%d dropped: not module day or "
                "not a weekday), %d of the kept persons have a missing/invalid distance",
                len(sel), n_input, n_dropped, n_missing_distance)
    return sel


def _person_state(persons: pd.DataFrame) -> pd.Series:
    """Vectorised reporting-day work-location state per person (see ``build_mid_workday_location_table``)."""
    did_not_work = persons["P_STARB1"] != MID_WORKED_ON_DAY
    at_home = persons["starb2"] == MID_AT_HOME
    at_workplace = persons["starb2"] == MID_AT_WORKPLACE
    other_place = persons["starb2"].isin(MID_OTHER_PLACE)
    return pd.Series(
        np.select(
            [did_not_work, at_home, at_workplace, other_place],
            ["did_not_work", "at_home", "at_workplace", "other_place"],
            default="missing",
        ),
        index=persons.index,
    )


def build_mid_workday_location_table(persons: pd.DataFrame) -> pd.DataFrame:
    """Weighted reporting-day work location by commute-distance class (weekdays, module persons).

    Restricts to persons who answered the home-office module (``M_HOFF``) on a reporting weekday
    (``arbwo``), then classifies each by commute distance (``P_ARB_ENTF``) and by reporting-day
    work-location STATE: ``did_not_work`` (``P_STARB1 != 1``), else ``at_home``/``at_workplace``/
    ``other_place`` from ``starb2``, else ``missing`` (a ``starb2`` code not mapped to any of the
    above -- i.e. the state itself is undetermined, not the distance).

    Persons without a valid distance (``NaN``, ``<= 0``, or the 996/999 missing codes) are
    excluded from the per-class rows entirely and are instead counted in ``n_missing_distance``
    on the ``all`` row; they are still included in the ``all`` row's weighted shares. ``share_missing``
    is therefore the ``P_GEW``-weighted share of workers (``P_STARB1 == 1``) whose ``starb2`` code
    does not map to ``at_home``/``at_workplace``/``other_place`` -- it is unrelated to distance
    completeness. The five ``share_*`` columns sum to 1.0 for every row.

    Returns one row per distance class actually observed in ``COMMUTE_CLASS_LABELS`` (``gt200`` is
    absent by construction: MiD top-codes distances at 200 km, see ``classify_commute_distance``)
    plus one ``all`` row, with columns ``distance_class, n_unweighted, n_missing_distance,
    share_at_workplace, share_at_home, share_did_not_work, share_other_place, share_missing``.
    """
    sel = _mid_weekday_module(persons)
    sel["state"] = _person_state(sel)
    rows = []

    def _row(label, sub, n_missing_distance=0):
        weight = sub["P_GEW"].astype(float)
        total_weight = weight.sum()
        row = {"distance_class": label, "n_unweighted": int(len(sub)),
               "n_missing_distance": int(n_missing_distance)}
        for state in ("at_workplace", "at_home", "did_not_work", "other_place", "missing"):
            share = float(weight[sub["state"] == state].sum() / total_weight) if total_weight > 0 else float("nan")
            row[f"share_{state}"] = share
        return row

    for label in COMMUTE_CLASS_LABELS:
        sub = sel[sel["distance_class"] == label]
        if len(sub):
            rows.append(_row(label, sub))
    n_missing_distance = int(sel["distance_class"].isna().sum())
    rows.append(_row("all", sel, n_missing_distance))
    table = pd.DataFrame(rows)
    logger.info("[mid workday location] %d weekday module persons, %d without a valid distance (%.1f%%)",
                len(sel), n_missing_distance, 100.0 * n_missing_distance / max(len(sel), 1))
    return table


def _load(directory, name):
    path = os.path.join(str(directory), name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Committed MiD reference table missing: {path}. Regenerate on the run "
                                 "server with scripts/extract_mid_workday_location.py (raw MiD is server-only).")
    return pd.read_csv(path, comment="#")


def load_workday_location_table(mid_dir):
    """Load the committed MiD workday-location-by-distance-class table (see ``WORKDAY_LOCATION_TABLE``)."""
    return _load(mid_dir, WORKDAY_LOCATION_TABLE)


def load_home_office_donor_pool(mid_dir):
    """Load the committed MiD home-office donor-pool cell-size table (see ``HOME_OFFICE_DONOR_POOL_TABLE``)."""
    return _load(mid_dir, HOME_OFFICE_DONOR_POOL_TABLE)
