"""Reference tables for the commute-day-state model (spec 2026-09-04-commute-day-state-design.md).

Pure builders over MiD 2023 person/trip frames (raw microdata lives on the run server only;
this module is called by scripts/extract_mid_workday_location.py there) and loaders for the
committed aggregates. Not imported by any population-synthesis or location stage.

MiD 2023 variables (SUF B1): ``M_HOFF`` (home-office module asked), ``arbwo`` (reporting day is a
weekday), ``P_STARB1`` (worked on the reporting day: 1 yes, 2 no, 9 no answer; other codes such as
202/206/402/404/407 mean "not employed" or "not asked" and are outside this module's universe --
see ``MID_ASKED_WORK_ON_DAY``), ``starb2`` (work location on the day, only meaningful when
``P_STARB1 == 1``: 1 at home, 2 usual workplace, 3/4/5/96 other places, 99 no answer; MiD also
sets ``starb2 == 409`` as a companion "did not work" code whenever ``P_STARB1 == 2``),
``P_ARB_ENTF`` (distance to the usual workplace in km; raw codes 996/999 are missing and every
code >= 1000, e.g. 2202/2206/4402/4404/4407, is a MiD filter/skip code -- see
``clean_mid_commute_distance_km``; 200.0 is a legitimate top-coded distance, not missing),
``P_GEW`` (person weight), ``W_ZWECK`` (trip purpose code on the Wege/trip file; 6 = escort/
bring-fetch someone), ``HP_ALTER`` (household-member age), ``HP_SEX`` (household-member sex;
codes 1/2/3/9, 2 = female).
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day state reference]"

COMMUTE_CLASS_EDGES_KM = (0.0, 10.0, 25.0, 50.0, 100.0, 200.0, float("inf"))
COMMUTE_CLASS_LABELS = ("lt10", "10_25", "25_50", "50_100", "100_200", "gt200")

MID_MODULE = 1
MID_WEEKDAY = 1
MID_WORKED_ON_DAY = 1
MID_DID_NOT_WORK_ON_DAY = 2
MID_STARB1_MISSING = 9
# P_STARB1 codes that stay inside the commute-day-state universe (worked / did not work / no
# answer). Every other P_STARB1 code (e.g. 202, 206, 402, 404, 407) means "not employed" or
# "not asked" in the MiD codeplan and is dropped before any state or distance is derived.
MID_ASKED_WORK_ON_DAY = (MID_WORKED_ON_DAY, MID_DID_NOT_WORK_ON_DAY, MID_STARB1_MISSING)

MID_AT_HOME = 1
MID_AT_WORKPLACE = 2
MID_OTHER_PLACE = (3, 4, 5, 96)
MID_DID_NOT_WORK = 409

MID_DISTANCE_MISSING = (996.0, 999.0)
# MiD filter/skip codes for P_ARB_ENTF are encoded as values >= 1000 (e.g. 2202, 2206, 4402,
# 4404, 4407); anything at or above this threshold is a code, never a real distance.
MID_DISTANCE_FILTER_CODE_MIN_KM = 1000.0
MID_DISTANCE_TOPCODE_KM = 200.0

MID_ESCORT_ACTIVE = 6
MID_CHILD_MAX_AGE = 13
MID_SEX_FEMALE = 2

WORKDAY_LOCATION_TABLE = "mid2023_workday_location_by_commute_distance.csv"
HOME_OFFICE_DONOR_POOL_TABLE = "mid2023_home_office_donor_pool.csv"

SHARE_COLUMNS = ("share_at_workplace", "share_at_home", "share_did_not_work",
                 "share_other_place", "share_missing")


def _log_filter_step(step_description: str, n_before: int, n_after: int) -> None:
    """Log one universe-filter step under the module's single log tag.

    Warns instead of informs when the kept share is zero or at/below 1% of the input: per the
    project's no-silent-fallbacks policy, a near-total collapse after a filter step almost always
    signals a broken filter (wrong column, wrong code) rather than a genuinely rare subpopulation,
    and must be surfaced loudly rather than passed through as a routine exclusion rate.
    """
    n_dropped = n_before - n_after
    share_kept = (n_after / n_before) if n_before > 0 else float("nan")
    message = "%s %s: %d/%d rows kept (%d dropped, %.1f%% kept)"
    percent_kept = 100.0 * share_kept if n_before > 0 else 0.0
    args = (_LOG_TAG, step_description, n_after, n_before, n_dropped, percent_kept)
    if n_before > 0 and share_kept <= 0.01:
        logger.warning(message, *args)
    else:
        logger.info(message, *args)


def clean_mid_commute_distance_km(values) -> pd.Series:
    """Map raw MiD ``P_ARB_ENTF`` codes to a clean commute distance in km, or ``NaN``.

    The MiD missing-value codes 996 and 999 (``MID_DISTANCE_MISSING``) and every MiD filter/skip
    code -- encoded as a value ``>= MID_DISTANCE_FILTER_CODE_MIN_KM`` (e.g. 2202, 2206, 4402,
    4404, 4407) -- become ``NaN``. The top-code ``MID_DISTANCE_TOPCODE_KM`` (200.0) is a legitimate
    distance ("200 km or more, reported as 200") and is kept unchanged.

    Raw MiD ``P_ARB_ENTF`` values must be passed through this helper before
    ``classify_commute_distance``, which is generic over any commute-distance source and has no
    notion of MiD-specific missing- or filter-value codes.
    """
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    is_missing_code = numeric.isin(MID_DISTANCE_MISSING) | (numeric >= MID_DISTANCE_FILTER_CODE_MIN_KM)
    return numeric.where(~is_missing_code)


def classify_commute_distance(km) -> str | None:
    """Commute-distance class label for a distance in kilometres.

    Generic over any commute-distance source (MiD, model output, ...): returns ``None`` for
    missing (``NaN``) or non-positive distances, and classifies everything else using
    ``COMMUTE_CLASS_EDGES_KM``. A distance of exactly ``200.0`` is classified as ``"100_200"``
    rather than ``"gt200"``, matching the MiD top-code convention ("200 km or more, reported as
    200"); distances strictly greater than 200 are a legitimate ``"gt200"`` (e.g. for model
    distances that were never MiD top-coded).

    Raw MiD ``P_ARB_ENTF`` values must be cleaned with ``clean_mid_commute_distance_km`` first --
    this function does not recognise the MiD missing-value codes (996, 999) or filter/skip codes
    (>= 1000) and would otherwise misclassify them as very large distances.
    """
    if km is None or pd.isna(km) or km <= 0:
        return None
    if km == MID_DISTANCE_TOPCODE_KM:
        return "100_200"
    idx = int(np.digitize([km], COMMUTE_CLASS_EDGES_KM[1:-1])[0])
    return COMMUTE_CLASS_LABELS[idx]


def _mid_universe(persons: pd.DataFrame) -> pd.DataFrame:
    """Restrict ``persons`` to the commute-day-state universe and attach ``distance_class``.

    Two filter steps are applied and logged separately (see ``_log_filter_step``) so a collapsed
    universe is diagnosable from the logs alone:

    1. module/weekday: ``M_HOFF == MID_MODULE`` and ``arbwo == MID_WEEKDAY``.
    2. P_STARB1 in ``MID_ASKED_WORK_ON_DAY`` (1 worked, 2 did not work, 9 no answer) -- every other
       P_STARB1 code means "not employed" or "not asked" and is not part of this universe.

    Adds a cleaned ``distance_class`` column (one of ``COMMUTE_CLASS_LABELS`` or ``NaN`` when
    ``P_ARB_ENTF`` is missing/a filter code, via ``clean_mid_commute_distance_km`` then
    ``classify_commute_distance``). The count of universe persons with a missing/invalid distance
    is logged once here; callers must not repeat that count in their own logging.
    """
    n_input = len(persons)
    module_weekday = persons[(persons["M_HOFF"] == MID_MODULE) & (persons["arbwo"] == MID_WEEKDAY)].copy()
    _log_filter_step("module/weekday filter", n_input, len(module_weekday))

    sel = module_weekday[module_weekday["P_STARB1"].isin(MID_ASKED_WORK_ON_DAY)].copy()
    _log_filter_step("P_STARB1 filter (worked/did not work/no answer)", len(module_weekday), len(sel))

    distance = clean_mid_commute_distance_km(sel["P_ARB_ENTF"])
    sel["distance_class"] = [classify_commute_distance(d) for d in distance]
    n_missing_distance = int(sel["distance_class"].isna().sum())
    logger.info("%s distance cleaning: %d/%d universe persons have a missing/invalid distance (%.1f%%)",
                _LOG_TAG, n_missing_distance, len(sel), 100.0 * n_missing_distance / max(len(sel), 1))
    return sel


def _person_state(persons: pd.DataFrame) -> pd.Series:
    """Vectorised reporting-day work-location state per person (see ``build_mid_workday_location_table``).

    Assumes ``persons`` is already restricted to the ``MID_ASKED_WORK_ON_DAY`` universe (see
    ``_mid_universe``). ``did_not_work`` = ``P_STARB1 == MID_DID_NOT_WORK_ON_DAY`` only;
    ``missing`` = ``P_STARB1 == MID_STARB1_MISSING`` (no answer) or ``P_STARB1 ==
    MID_WORKED_ON_DAY`` with a ``starb2`` code not mapped to at_home/at_workplace/other_place
    (e.g. 99, "no answer" on starb2).

    Also checks ``P_STARB1``/``starb2`` consistency: MiD sets ``starb2 == MID_DID_NOT_WORK`` (409)
    as a companion code whenever ``P_STARB1 == MID_DID_NOT_WORK_ON_DAY``, and never when
    ``P_STARB1 == MID_WORKED_ON_DAY``. A non-zero count of violations is a data-quality problem in
    the raw extract, not a modelling choice, and is logged as a warning rather than silently
    absorbed into either state.
    """
    p_starb1 = persons["P_STARB1"]
    starb2 = persons["starb2"]

    inconsistent = ((p_starb1 == MID_WORKED_ON_DAY) & (starb2 == MID_DID_NOT_WORK)) \
        | ((p_starb1 == MID_DID_NOT_WORK_ON_DAY) & (starb2 != MID_DID_NOT_WORK))
    n_inconsistent = int(inconsistent.sum())
    if n_inconsistent > 0:
        logger.warning("%s P_STARB1/starb2 consistency check: %d/%d rows have a starb2 code "
                        "inconsistent with P_STARB1 (worked but starb2==409, or did-not-work but "
                        "starb2!=409)", _LOG_TAG, n_inconsistent, len(persons))

    did_not_work = p_starb1 == MID_DID_NOT_WORK_ON_DAY
    starb1_missing = p_starb1 == MID_STARB1_MISSING
    at_home = (p_starb1 == MID_WORKED_ON_DAY) & (starb2 == MID_AT_HOME)
    at_workplace = (p_starb1 == MID_WORKED_ON_DAY) & (starb2 == MID_AT_WORKPLACE)
    other_place = (p_starb1 == MID_WORKED_ON_DAY) & (starb2.isin(MID_OTHER_PLACE))

    return pd.Series(
        np.select(
            [did_not_work, starb1_missing, at_home, at_workplace, other_place],
            ["did_not_work", "missing", "at_home", "at_workplace", "other_place"],
            default="missing",
        ),
        index=persons.index,
    )


def build_mid_workday_location_table(persons: pd.DataFrame) -> pd.DataFrame:
    """Weighted reporting-day work location by commute-distance class (weekdays, module persons).

    Restricts ``persons`` to the commute-day-state universe (see ``_mid_universe``: home-office
    module, reporting weekday, codeable P_STARB1), then classifies each person by commute-distance
    class and by reporting-day work-location STATE (see ``_person_state``): ``did_not_work``
    (``P_STARB1 == MID_DID_NOT_WORK_ON_DAY``), else ``at_home``/``at_workplace``/``other_place``
    from ``starb2`` when ``P_STARB1 == MID_WORKED_ON_DAY``, else ``missing`` (``P_STARB1 ==
    MID_STARB1_MISSING`` or an unmapped ``starb2`` -- i.e. the state itself is undetermined, not
    the distance).

    Persons without a valid distance (``NaN``, ``<= 0``, or a MiD missing/filter code -- see
    ``clean_mid_commute_distance_km``) are excluded from the per-class rows entirely and are
    instead counted in ``n_missing_distance`` on the ``all`` row; they are still included in the
    ``all`` row's weighted shares. ``share_missing`` is therefore the ``P_GEW``-weighted share of
    universe persons whose STATE is undetermined -- it is unrelated to distance completeness. The
    five ``share_*`` columns (``SHARE_COLUMNS``) sum to 1.0 for every row.

    Returns one row per distance class actually observed in ``COMMUTE_CLASS_LABELS`` (``gt200`` is
    absent by construction: MiD top-codes distances at 200 km, see ``classify_commute_distance``)
    plus one ``all`` row, with columns ``distance_class, n_unweighted, n_missing_distance,
    share_at_workplace, share_at_home, share_did_not_work, share_other_place, share_missing``.
    """
    sel = _mid_universe(persons)
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
    logger.info("%s workday-location table: %d rows, %d persons total", _LOG_TAG, len(table), len(sel))
    return table


def build_mid_home_office_donor_pool(persons: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    """Cell sizes of the MiD home-office-day donor pool (weekday, worked, at home).

    The donor pool is universe persons (see ``_mid_universe``) who worked on the reporting day
    (``P_STARB1 == MID_WORKED_ON_DAY``) at home (``starb2 == MID_AT_HOME``). Each donor is
    cross-classified by ``distance_class`` (``COMMUTE_CLASS_LABELS`` plus ``"missing"`` for an
    invalid/absent ``P_ARB_ENTF``), ``has_children`` (any household member, from ``persons``
    regardless of module participation, with ``HP_ALTER <= MID_CHILD_MAX_AGE`` sharing the donor's
    ``H_ID``), and ``has_active_escort`` (at least one trip in ``trips`` with ``W_ZWECK ==
    MID_ESCORT_ACTIVE``). For every distance class an additional ``has_children == "all"`` /
    ``has_active_escort == "all"`` row totals across the two boolean dimensions; the overall
    ``distance_class == "all"`` row totals across distance classes too.

    Diagnostics per cell (unweighted counts, ``P_GEW``-weighted share): ``n_donors``, ``n_mobile``
    (donors with at least one trip in ``trips``), ``mean_trips_mobile`` (mean trip count among the
    mobile donors; ``NaN`` if the cell has no mobile donor), ``share_female`` (weighted by
    ``P_GEW``, using ``MID_SEX_FEMALE``; ``NaN`` if the cell's total weight is zero).
    """
    module = _mid_universe(persons)
    pool = module[(module["P_STARB1"] == MID_WORKED_ON_DAY) & (module["starb2"] == MID_AT_HOME)].copy()
    pool["distance_class"] = pool["distance_class"].fillna("missing")

    children_by_household = persons.loc[persons["HP_ALTER"] <= MID_CHILD_MAX_AGE, "H_ID"].value_counts()
    pool["has_children"] = pool["H_ID"].map(children_by_household).fillna(0).gt(0)

    trip_count_by_person = trips.groupby("HP_ID").size()
    escort_trip_count_by_person = trips.loc[trips["W_ZWECK"] == MID_ESCORT_ACTIVE, "HP_ID"].value_counts()
    pool["n_trips"] = pool["HP_ID"].map(trip_count_by_person).fillna(0).astype(int)
    pool["has_active_escort"] = pool["HP_ID"].map(escort_trip_count_by_person).fillna(0).gt(0)

    def _cell(distance_label, has_children_label, has_escort_label, sub):
        weight = sub["P_GEW"].astype(float)
        total_weight = weight.sum()
        mobile = sub[sub["n_trips"] > 0]
        share_female = float(weight[sub["HP_SEX"] == MID_SEX_FEMALE].sum() / total_weight) if total_weight > 0 \
            else float("nan")
        return {
            "distance_class": distance_label,
            "has_children": has_children_label,
            "has_active_escort": has_escort_label,
            "n_donors": int(len(sub)),
            "n_mobile": int(len(mobile)),
            "mean_trips_mobile": float(mobile["n_trips"].mean()) if len(mobile) else float("nan"),
            "share_female": share_female,
        }

    rows = []
    distance_labels_with_totals = list(COMMUTE_CLASS_LABELS) + ["missing", "all"]
    for distance_label in distance_labels_with_totals:
        sub_for_distance = pool if distance_label == "all" else pool[pool["distance_class"] == distance_label]
        rows.append(_cell(distance_label, "all", "all", sub_for_distance))
        for has_children in (False, True):
            for has_active_escort in (False, True):
                sub = sub_for_distance[(sub_for_distance["has_children"] == has_children)
                                        & (sub_for_distance["has_active_escort"] == has_active_escort)]
                rows.append(_cell(distance_label, has_children, has_active_escort, sub))
    table = pd.DataFrame(rows)
    logger.info("%s home-office donor pool: %d donors, %d rows (escort %.1f%%, children %.1f%%)",
                _LOG_TAG, len(pool), len(table),
                100.0 * pool["has_active_escort"].mean() if len(pool) else 0.0,
                100.0 * pool["has_children"].mean() if len(pool) else 0.0)
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
