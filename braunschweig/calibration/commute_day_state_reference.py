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
``P_GEW`` (person weight), ``W_ZWECK`` (trip purpose code on the Wege/trip file; 1 = trip to work,
6 = escort/bring-fetch someone -- purpose mapping per ``braunschweig.popsim.trips.
PURPOSE_BY_W_ZWECK``), ``wegkm`` (trip length in km on the Wege file), ``ST_WOTAG`` (reporting
day of the week, raw code; this module asserts no mapping from that code to a named weekday),
``HP_ALTER`` (household-member age), ``HP_SEX`` (household-member sex; codes 1/2/3/9, 2 = female).
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


DONOR_CLASS_MISSING = "missing"
DONOR_CLASS_TOTAL = "all"
#: Columns ``donor_vs_assigned_class`` expects on its ``workers`` / ``donors`` frames.
DONOR_WORKER_COLUMNS = ("person_id", "hts_id", "assigned_distance_class")
DONOR_COLUMNS = ("hts_id", "donor_distance_km", "donor_worked_on_day", "donor_starb2")
#: Columns ``donor_universe_diagnostics`` expects (one row per WORKER, donor attributes joined on).
DONOR_UNIVERSE_COLUMNS = ("donor_distance_km", "donor_in_home_office_module",
                          "donor_reporting_day_weekday", "donor_reporting_day_of_week")
#: Above this share of non-comparable rows (donor distance missing) ``donor_vs_assigned_class``
#: warns: the mismatch share it returns then describes a minority of the cohort, and reading it as
#: a property of the whole population would be wrong (CLAUDE.md "Fallback transparency").
DEFAULT_WARN_MISSING_SHARE = 0.5


def _require_frame_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def _normalised_class(values, index=None) -> pd.Series:
    """Map a commute-class column to ``COMMUTE_CLASS_LABELS`` or :data:`DONOR_CLASS_MISSING`.

    Anything that is not one of the six labels (``NaN``, an empty string, an ``unresolved``
    marker) becomes ``missing`` -- the helper never guesses a class it was not given. ``index``
    must be passed whenever ``values`` is a plain sequence built from a frame, so the result
    aligns with that frame's index instead of a fresh ``RangeIndex``.
    """
    series = pd.Series(values, index=index).astype("object")
    known = series.isin(COMMUTE_CLASS_LABELS)
    return series.where(known, DONOR_CLASS_MISSING)


def _share(numerator: int, denominator: int) -> float:
    """``numerator / denominator`` as a float, or ``NaN`` for an empty denominator."""
    return float(numerator) / denominator if denominator else float("nan")


def _code_key(code) -> str:
    """Render one raw MiD code as a stable dictionary key.

    An integral code is rendered without a decimal point even when pandas widened its column to
    float (which happens as soon as a single value is missing), so the emitted keys do not depend
    on the dtype the reader happened to produce.
    """
    if isinstance(code, float) and float(code).is_integer():
        return str(int(code))
    return str(code)


def _code_histogram(values) -> dict:
    """Count occurrences of each raw code in ``values``, keyed by the code as a string.

    Missing values are counted under the key ``"missing"``. No code is translated into a label:
    the caller documents the MiD coding, this helper only counts what the data contains.
    """
    series = pd.Series(values)
    histogram = {}
    for code, count in series.value_counts(dropna=True).items():
        histogram[_code_key(code)] = histogram.get(_code_key(code), 0) + int(count)
    n_missing = int(series.isna().sum())
    if n_missing:
        histogram[DONOR_CLASS_MISSING] = n_missing
    return dict(sorted(histogram.items()))


def donor_vs_assigned_class(workers: pd.DataFrame, donors: pd.DataFrame, *,
                            warn_missing_share: float = DEFAULT_WARN_MISSING_SHARE
                            ) -> tuple[pd.DataFrame, dict]:
    """Cross-tabulate each synthetic worker's ASSIGNED commute-distance class against the class
    of the MiD donor the worker's attributes came from.

    Phase A measurement for the commute-day-state model (spec 2026-09-04, issue #244): the MiD
    workday-location table (:func:`build_mid_workday_location_table`) is indexed by the DONOR's
    commute-distance class, while the model would apply it to the worker's ASSIGNED (synthesised)
    distance. Where the two classes disagree -- and in particular where the assigned class is
    strictly HIGHER than the donor's -- a state drawn from the donor's own reporting day would be
    taken from the wrong row of that table. This function measures the size of that population;
    it decides nothing.

    ``workers`` carries :data:`DONOR_WORKER_COLUMNS` (``person_id``, ``hts_id``,
    ``assigned_distance_class``); ``donors`` carries :data:`DONOR_COLUMNS` (``hts_id``,
    ``donor_distance_km`` -- already cleaned with :func:`clean_mid_commute_distance_km`, ``NaN``
    allowed --, ``donor_worked_on_day`` = MiD ``P_STARB1`` and ``donor_starb2`` = MiD ``starb2``).
    ``donors`` must be unique on ``hts_id``; workers whose ``hts_id`` has no donor row are
    excluded from the cross-tab and reported as ``n_workers - n_matched_donor``.

    Returns ``(cross_tab, diagnostics)``:

    * ``cross_tab`` has one row per donor class (``COMMUTE_CLASS_LABELS``, then
      :data:`DONOR_CLASS_MISSING` for donors without a usable distance, then
      :data:`DONOR_CLASS_TOTAL`), with ``n_donor_total``, one ``n_<assigned class>`` count column
      per assigned class (the six labels plus ``missing``) and the matching ``share_<assigned
      class>`` row shares. Every row and column is emitted even when empty, so the table shape is
      stable across runs. It carries COUNTS only -- no person, household or MiD identifier.
    * ``diagnostics`` (all counts unweighted sample counts): ``n_workers``, ``n_matched_donor``,
      ``n_donor_distance_missing`` (matched workers whose donor class is ``missing``),
      ``n_assigned_class_missing``, ``n_comparable`` (matched workers where BOTH classes are
      known), ``n_assigned_gt_donor`` / ``n_assigned_lt_donor`` / ``n_assigned_eq_donor``
      (strictly higher / lower / equal in ``COMMUTE_CLASS_LABELS`` order),
      ``share_assigned_gt_donor`` (of ``n_comparable``),
      ``n_assigned_gt_donor_by_assigned_class`` (dict, assigned class -> count) and the donor
      reporting-day state counts over the matched workers (``n_donor_worked_on_day``,
      ``n_donor_did_not_work_on_day``, ``n_donor_at_home``, ``n_donor_at_workplace``).

    When the share of matched workers whose donor class is ``missing`` exceeds
    ``warn_missing_share`` the function logs a WARNING: ``share_assigned_gt_donor`` is then a
    property of a minority subset, and that subset is not necessarily representative of the whole
    cohort.
    """
    _require_frame_columns(workers, DONOR_WORKER_COLUMNS, "workers frame")
    _require_frame_columns(donors, DONOR_COLUMNS, "donors frame")
    if donors["hts_id"].duplicated().any():
        n_duplicated = int(donors["hts_id"].duplicated().sum())
        raise ValueError(f"donors frame must be unique on hts_id, but {n_duplicated} duplicate "
                         "hts_id value(s) are present; a duplicated donor would multiply workers "
                         "in the cross-tab")

    n_workers = int(len(workers))
    merged = workers.merge(donors, on="hts_id", how="inner", validate="many_to_one")
    n_matched_donor = int(len(merged))

    donor_class = _normalised_class([classify_commute_distance(km) for km in merged["donor_distance_km"]],
                                    index=merged.index)
    assigned_class = _normalised_class(merged["assigned_distance_class"], index=merged.index)

    class_rank = {label: rank for rank, label in enumerate(COMMUTE_CLASS_LABELS)}
    donor_rank = donor_class.map(class_rank)
    assigned_rank = assigned_class.map(class_rank)
    comparable = donor_rank.notna() & assigned_rank.notna()
    assigned_gt_donor = comparable & (assigned_rank > donor_rank)
    assigned_lt_donor = comparable & (assigned_rank < donor_rank)
    assigned_eq_donor = comparable & (assigned_rank == donor_rank)

    class_labels = list(COMMUTE_CLASS_LABELS) + [DONOR_CLASS_MISSING]
    counts = pd.crosstab(donor_class, assigned_class)
    counts = counts.reindex(index=class_labels, columns=class_labels, fill_value=0).astype(int)

    rows = []
    for label in class_labels + [DONOR_CLASS_TOTAL]:
        row_counts = counts.sum(axis=0) if label == DONOR_CLASS_TOTAL else counts.loc[label]
        n_donor_total = int(row_counts.sum())
        row = {"donor_distance_class": label, "n_donor_total": n_donor_total}
        for assigned_label in class_labels:
            row[f"n_{assigned_label}"] = int(row_counts[assigned_label])
        for assigned_label in class_labels:
            row[f"share_{assigned_label}"] = (float(row_counts[assigned_label]) / n_donor_total
                                              if n_donor_total > 0 else float("nan"))
        rows.append(row)
    cross_tab = pd.DataFrame(rows)

    n_comparable = int(comparable.sum())
    n_assigned_gt_donor = int(assigned_gt_donor.sum())
    by_assigned_class = assigned_class[assigned_gt_donor].value_counts()
    share_assigned_gt_donor = (float(n_assigned_gt_donor) / n_comparable
                               if n_comparable > 0 else float("nan"))
    diagnostics = {
        "n_workers": n_workers,
        "n_matched_donor": n_matched_donor,
        "n_donor_distance_missing": int((donor_class == DONOR_CLASS_MISSING).sum()),
        "n_assigned_class_missing": int((assigned_class == DONOR_CLASS_MISSING).sum()),
        "n_comparable": n_comparable,
        "n_assigned_gt_donor": n_assigned_gt_donor,
        "n_assigned_lt_donor": int(assigned_lt_donor.sum()),
        "n_assigned_eq_donor": int(assigned_eq_donor.sum()),
        "share_assigned_gt_donor": share_assigned_gt_donor,
        "n_assigned_gt_donor_by_assigned_class": {str(label): int(count)
                                                  for label, count in by_assigned_class.items()},
        "n_donor_worked_on_day": int((merged["donor_worked_on_day"] == MID_WORKED_ON_DAY).sum()),
        "n_donor_did_not_work_on_day": int((merged["donor_worked_on_day"] == MID_DID_NOT_WORK_ON_DAY).sum()),
        "n_donor_at_home": int(((merged["donor_worked_on_day"] == MID_WORKED_ON_DAY)
                                & (merged["donor_starb2"] == MID_AT_HOME)).sum()),
        "n_donor_at_workplace": int(((merged["donor_worked_on_day"] == MID_WORKED_ON_DAY)
                                     & (merged["donor_starb2"] == MID_AT_WORKPLACE)).sum()),
    }

    logger.info("%s donor-vs-assigned class: %d/%d workers matched a donor (%.2f%%); donor distance "
                "missing for %d (%.2f%% of matched), assigned class missing for %d",
                _LOG_TAG, n_matched_donor, n_workers,
                100.0 * n_matched_donor / max(n_workers, 1),
                diagnostics["n_donor_distance_missing"],
                100.0 * diagnostics["n_donor_distance_missing"] / max(n_matched_donor, 1),
                diagnostics["n_assigned_class_missing"])
    logger.info("%s donor-vs-assigned class: %d/%d comparable workers have an assigned class ABOVE "
                "the donor class (%.2f%%); below %d, equal %d",
                _LOG_TAG, n_assigned_gt_donor, n_comparable, 100.0 * share_assigned_gt_donor,
                diagnostics["n_assigned_lt_donor"], diagnostics["n_assigned_eq_donor"])
    share_missing = _share(diagnostics["n_donor_distance_missing"], n_matched_donor)
    diagnostics["share_donor_distance_missing"] = share_missing
    diagnostics["warn_missing_share"] = float(warn_missing_share)
    if share_missing > warn_missing_share:
        logger.warning("%s donor-vs-assigned class: %d/%d matched workers (%.2f%%) have NO usable "
                       "donor distance, above the %.2f%% warning threshold. share_assigned_gt_donor "
                       "(%.4f) therefore describes only the %d comparable workers, and that subset "
                       "is NOT necessarily representative of the whole cohort -- do not read it as "
                       "a property of all %d workers",
                       _LOG_TAG, diagnostics["n_donor_distance_missing"], n_matched_donor,
                       100.0 * share_missing, 100.0 * warn_missing_share,
                       share_assigned_gt_donor, n_comparable, n_workers)
    return cross_tab, diagnostics


def donor_universe_diagnostics(worker_donors: pd.DataFrame) -> dict:
    """Aggregate-only description of the MiD donors behind a synthetic worker cohort.

    ``worker_donors`` is one row PER WORKER with the donor's attributes already joined on
    (:data:`DONOR_UNIVERSE_COLUMNS`): ``donor_distance_km`` (already cleaned with
    :func:`clean_mid_commute_distance_km`), ``donor_in_home_office_module`` (MiD ``M_HOFF``),
    ``donor_reporting_day_weekday`` (MiD ``arbwo``) and ``donor_reporting_day_of_week``
    (MiD ``ST_WOTAG``). Counts are therefore worker counts, not donor-person counts: a donor
    used by many workers is counted once per worker, which is the quantity that matters when
    asking what the SYNTHETIC POPULATION inherited.

    Why this exists (spec 2026-09-04, issue #244): ``P_ARB_ENTF`` is a question of the MiD
    home-office module, so it can only be valid for donors with ``M_HOFF == MID_MODULE``. Whether
    a missing donor distance means "not asked" or "asked and refused" is answerable only by
    splitting the validity rate by that module flag, and whether the donors' reporting days are
    weekdays at all is answerable only from ``arbwo`` / ``ST_WOTAG``. Both are measured here
    rather than asserted.

    Returns a dict of counts and shares: ``n_workers``, ``n_in_home_office_module``,
    ``n_not_in_home_office_module``, ``n_module_flag_other``, ``n_distance_valid``,
    ``share_distance_valid``, the same valid counts/shares split by module flag
    (``*_in_module`` / ``*_not_in_module``), and two raw code histograms
    ``n_by_reporting_day_weekday`` (``arbwo``) and ``n_by_reporting_day_of_week``
    (``ST_WOTAG``). The code histograms are keyed by the MiD code as a string; this function
    asserts NO mapping from a ``ST_WOTAG`` code to a named weekday, because the repository
    carries no committed statement of that coding.
    """
    _require_frame_columns(worker_donors, DONOR_UNIVERSE_COLUMNS, "worker_donors frame")
    n_workers = int(len(worker_donors))
    in_module = worker_donors["donor_in_home_office_module"] == MID_MODULE
    not_in_module = (worker_donors["donor_in_home_office_module"].notna()
                     & (worker_donors["donor_in_home_office_module"] != MID_MODULE))
    distance_valid = worker_donors["donor_distance_km"].notna() & (worker_donors["donor_distance_km"] > 0)

    n_in_module = int(in_module.sum())
    n_not_in_module = int(not_in_module.sum())
    diagnostics = {
        "n_workers": n_workers,
        "n_in_home_office_module": n_in_module,
        "n_not_in_home_office_module": n_not_in_module,
        "n_module_flag_other": n_workers - n_in_module - n_not_in_module,
        "n_distance_valid": int(distance_valid.sum()),
        "share_distance_valid": _share(int(distance_valid.sum()), n_workers),
        "n_distance_valid_in_module": int((distance_valid & in_module).sum()),
        "share_distance_valid_in_module": _share(int((distance_valid & in_module).sum()), n_in_module),
        "n_distance_valid_not_in_module": int((distance_valid & not_in_module).sum()),
        "share_distance_valid_not_in_module": _share(int((distance_valid & not_in_module).sum()),
                                                     n_not_in_module),
        "n_by_reporting_day_weekday": _code_histogram(worker_donors["donor_reporting_day_weekday"]),
        "n_by_reporting_day_of_week": _code_histogram(worker_donors["donor_reporting_day_of_week"]),
    }
    logger.info("%s donor universe over %d workers: %d donors in the home-office module "
                "(P_ARB_ENTF valid for %.2f%% of them) and %d outside it (valid for %.2f%%); "
                "overall distance validity %.2f%%",
                _LOG_TAG, n_workers, n_in_module,
                100.0 * diagnostics["share_distance_valid_in_module"], n_not_in_module,
                100.0 * diagnostics["share_distance_valid_not_in_module"],
                100.0 * diagnostics["share_distance_valid"])
    logger.info("%s donor reporting day over %d workers: arbwo %s, ST_WOTAG %s (raw MiD codes)",
                _LOG_TAG, n_workers, diagnostics["n_by_reporting_day_weekday"],
                diagnostics["n_by_reporting_day_of_week"])
    return diagnostics


#: MiD ``W_ZWECK`` code of a trip to work. Committed source of the purpose mapping:
#: ``braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK`` (1 -> "work"). Code 2 ("business trip") is
#: deliberately EXCLUDED here: this helper reconstructs the HOME->WORKPLACE commute length that
#: ``P_ARB_ENTF`` would otherwise state, and a business trip is not that commute.
MID_WORK_TRIP_PURPOSE = 1
#: Trip lengths at or above this value are MiD filter/missing codes (the same >= 1000 convention
#: as ``MID_DISTANCE_FILTER_CODE_MIN_KM`` for ``P_ARB_ENTF``), never a real commute length.
MID_TRIP_LENGTH_MAX_KM = 1000.0
#: Column the trip-length fallback writes; see :func:`first_work_trip_length_km`.
WORK_TRIP_LENGTH_COLUMN = "work_trip_length_km"


def first_work_trip_length_km(trips: pd.DataFrame) -> pd.DataFrame:
    """First valid work-trip length in km per MiD person, from the raw trip file.

    The commute-day-state spec (2026-09-04) foresees the donor's WORK-TRIP LENGTH as the
    commute-distance source whenever ``P_ARB_ENTF`` is missing. ``P_ARB_ENTF`` is a question of
    the home-office module, so for donors outside that module it is missing by construction and
    this is not a rare fallback but the main path -- which is exactly why it is measured rather
    than assumed (see :func:`donor_universe_diagnostics`).

    ``trips`` needs the columns ``H_ID``, ``P_ID``, ``W_ZWECK`` and ``wegkm``. A row qualifies
    when ``W_ZWECK == MID_WORK_TRIP_PURPOSE`` and ``0 < wegkm < MID_TRIP_LENGTH_MAX_KM``; per
    person the FIRST qualifying row in file order is kept (a reporting day can hold several work
    trips, e.g. an outbound and a return leg, and averaging them would invent a distance the
    respondent never reported).

    Returns a frame with one row per person that has such a trip, columns ``H_ID``, ``P_ID`` and
    :data:`WORK_TRIP_LENGTH_COLUMN`. Persons without a qualifying trip are simply absent; the
    caller decides what a missing length means.
    """
    _require_frame_columns(trips, ("H_ID", "P_ID", "W_ZWECK", "wegkm"), "trips frame")
    length_km = pd.to_numeric(trips["wegkm"], errors="coerce")
    qualifies = ((trips["W_ZWECK"] == MID_WORK_TRIP_PURPOSE) & length_km.notna()
                 & (length_km > 0) & (length_km < MID_TRIP_LENGTH_MAX_KM))
    selected = trips.loc[qualifies, ["H_ID", "P_ID"]].copy()
    selected[WORK_TRIP_LENGTH_COLUMN] = length_km[qualifies].to_numpy()
    first = selected.drop_duplicates(subset=["H_ID", "P_ID"], keep="first").reset_index(drop=True)
    logger.info("%s work-trip lengths: %d/%d trips qualify (W_ZWECK == %d, 0 < wegkm < %.0f); "
                "%d distinct persons keep their first qualifying trip",
                _LOG_TAG, int(qualifies.sum()), len(trips), MID_WORK_TRIP_PURPOSE,
                MID_TRIP_LENGTH_MAX_KM, len(first))
    return first
