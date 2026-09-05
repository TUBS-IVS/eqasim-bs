"""Pure core of the commute-day-state model (ADR-0104, issue #244).

Gives every employed person with an assigned workplace a reporting-day state in ``STATES``
(``at_workplace``, ``home``, ``absent``), re-drawn from the donor's own reporting day ONLY when
the person's ASSIGNED commute-distance class is strictly higher than the DONOR's class (ADR-0104
"Decision"). Otherwise the donor's own day already encodes its class's not-working and
home-office behaviour and is passed through unchanged -- this avoids double-counting the
survey's own home-office mass.

Four building blocks, in the order a caller uses them:

1. :func:`donor_distance_class_from_trips` -- the DONOR's commute-distance class, derived from
   the person's own pre-assignment trips (``synthesis.population.trips``): the first valid
   work-trip length on the donor's reporting day (ADR-0104 Amendment 1; ``P_ARB_ENTF`` is a
   home-office-module-only question and is NOT used here).
2. :func:`assigned_distance_class` -- the ASSIGNED commute-distance class, from the synthesised
   home/workplace geometry (euclidean distance x detour factor).
3. :func:`keep_probability` -- ``share_at_workplace(assigned) / share_at_workplace(donor)``,
   read from the committed MiD workday-location table.
4. :func:`draw_states` -- the seeded state draw over a population of workers, producing the
   ``commute_day_state`` attribute and its diagnostics.

None of this module reads a file, calls a synpp stage, or touches raw MiD data; it operates
purely on DataFrames/GeoDataFrames passed in by the caller (a synpp stage, added in a later
Phase B task) and is exercised in ``tests/test_commute_day_state.py`` on synthetic frames only.
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.calibration.commute_day_state_reference import (
    COMMUTE_CLASS_LABELS,
    classify_commute_distance,
)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day state]"

#: Additive offset applied to the pipeline's ``random_seed`` for every draw this model makes
#: (``np.random.RandomState(random_seed + COMMUTE_DAY_SEED_OFFSET)``), so the commute-day-state
#: RNG stream never collides with another stage's use of the same base seed.
COMMUTE_DAY_SEED_OFFSET = 7301

#: The three reporting-day states a worker can end up in (ADR-0104 "Decision").
STATES = ("at_workplace", "home", "absent")

#: Ordinal rank of each MiD commute-distance class, increasing with distance. Used to decide
#: whether an ASSIGNED class is strictly higher than a DONOR class (the only condition under
#: which a re-draw happens at all).
CLASS_RANK = {label: rank for rank, label in enumerate(COMMUTE_CLASS_LABELS)}

#: MiD ``W_ZWECK`` purpose mapped onto the eqasim trip schema (``braunschweig.popsim.trips``):
#: the ``following_purpose`` value of a trip to work.
WORK_FOLLOWING_PURPOSE = "work"

#: A donor work-trip length must lie strictly between 0 and this bound to be usable; at or above
#: it the value is a MiD filter/missing code, never a real trip length (the same convention as
#: ``commute_day_state_reference.MID_TRIP_LENGTH_MAX_KM``).
MAX_DONOR_TRIP_LENGTH_KM = 1000.0

#: The MiD workday-location table (``share_at_workplace`` column) carries no ``gt200`` row --
#: MiD top-codes ``P_ARB_ENTF`` at 200 km, so the survey cannot resolve anything beyond it. A
#: class of ``gt200`` therefore reads the ``100_200`` row instead (ADR-0104 "Decision").
_GT200_TABLE_LABEL = "100_200"


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what} is missing the required column(s) {missing} "
                         f"(present: {sorted(frame.columns)})")


def donor_distance_class_from_trips(trips: pd.DataFrame, *,
                                    distance_columns=("wegkm_imp", "wegkm")) -> pd.DataFrame:
    """Per-person DONOR commute-distance class, from the donor's own pre-assignment trips.

    ``trips`` is the ``synthesis.population.trips`` frame (one row per (person, trip), the
    11-column eqasim contract plus MiD extras -- see ``braunschweig.popsim.trips_stage``): needs
    ``person_id``, ``trip_index`` and ``following_purpose``, plus at least one of
    ``distance_columns``.

    The donor distance is the value of the FIRST column of ``distance_columns`` that EXISTS on
    ``trips`` (columns are never mixed row by row: if ``wegkm_imp`` exists, every row uses
    ``wegkm_imp``, even where that particular value is ``NaN``, and ``wegkm`` is never consulted
    as a per-row fallback) on the first trip -- sorted by ``trip_index`` -- with
    ``following_purpose == "work"`` and ``0 < km < MAX_DONOR_TRIP_LENGTH_KM``. Classified with
    :func:`classify_commute_distance` using ``topcode_km=None`` (ADR-0104: a raw trip length was
    never subject to the MiD ``P_ARB_ENTF`` 200 km top-code, so an exact 200.0 km trip
    classifies as ``gt200`` rather than being folded into ``100_200``).

    Persons with no such trip get ``donor_distance_km = NaN`` and ``donor_distance_class =
    None``, and are counted in the log rather than dropped -- a missing donor distance means "no
    re-draw" (see :func:`keep_probability`), not "excluded from the population".

    Raises ``KeyError`` if none of ``distance_columns`` is present on ``trips`` -- the caller's
    trips frame carries no usable donor-distance source at all, which must fail loudly rather
    than silently produce an all-``None`` result.

    Returns one row per distinct ``person_id`` in ``trips``, columns ``person_id``,
    ``donor_distance_km``, ``donor_distance_class``.
    """
    _require_columns(trips, ("person_id", "trip_index", "following_purpose"), "trips frame")
    distance_column = next((column for column in distance_columns if column in trips.columns), None)
    if distance_column is None:
        raise KeyError(
            f"{_LOG_TAG} trips frame has none of the candidate donor-distance columns "
            f"{distance_columns}; present columns: {sorted(trips.columns)}. The "
            "synthesis.population.trips contract must carry at least one MiD trip-length extra "
            "(e.g. wegkm_imp and/or wegkm) for the donor distance to be derivable.")
    logger.info("%s donor distance column: %r (first of %s present on the trips frame)",
                _LOG_TAG, distance_column, distance_columns)

    work_trips = trips.loc[trips["following_purpose"] == WORK_FOLLOWING_PURPOSE].copy()
    distance_km = pd.to_numeric(work_trips[distance_column], errors="coerce")
    is_valid = distance_km.notna() & (distance_km > 0) & (distance_km < MAX_DONOR_TRIP_LENGTH_KM)

    valid_trips = work_trips.loc[is_valid, ["person_id", "trip_index"]].copy()
    valid_trips["donor_distance_km"] = distance_km[is_valid].to_numpy()
    first_valid = (
        valid_trips.sort_values(["person_id", "trip_index"])
        .drop_duplicates(subset="person_id", keep="first")
    )

    persons = trips[["person_id"]].drop_duplicates().reset_index(drop=True)
    result = persons.merge(first_valid[["person_id", "donor_distance_km"]], on="person_id", how="left")
    result["donor_distance_class"] = [
        classify_commute_distance(km, topcode_km=None) for km in result["donor_distance_km"]
    ]

    n_persons = len(result)
    n_missing = int(result["donor_distance_class"].isna().sum())
    logger.info(
        "%s donor distance class: %d/%d persons (%.1f%%) have a usable donor work-trip length; "
        "%d (%.1f%%) have no valid work trip and are counted with donor_distance_class=None "
        "(no re-draw for them, see keep_probability)",
        _LOG_TAG, n_persons - n_missing, n_persons,
        100.0 * (n_persons - n_missing) / max(n_persons, 1),
        n_missing, 100.0 * n_missing / max(n_persons, 1))
    return result


def _validate_projected_equal_crs(df_home: gpd.GeoDataFrame, df_work: gpd.GeoDataFrame) -> None:
    """Both geometry inputs must carry the same, non-None, PROJECTED CRS.

    Shapely computes a planar distance on raw coordinates whatever CRS label a frame carries, so
    a mismatch -- or a geographic (degree-based) CRS -- would still "run" and silently produce a
    number with no defensible unit (CLAUDE.md "Geospatial processing").
    """
    home_crs, work_crs = df_home.crs, df_work.crs
    if home_crs is None or work_crs is None:
        raise ValueError(f"{_LOG_TAG} missing CRS: df_home={home_crs}, df_work={work_crs}; every "
                         "input geometry must carry an explicit CRS")
    if home_crs != work_crs:
        raise ValueError(f"{_LOG_TAG} CRS mismatch between df_home ({home_crs}) and df_work "
                         f"({work_crs}); both must match -- reproject upstream")
    if not home_crs.is_projected:
        raise ValueError(f"{_LOG_TAG} geometries use the geographic CRS {home_crs}; metric "
                         "distances require a projected CRS (the pipeline uses EPSG:25832)")


def assigned_distance_class(df_work: gpd.GeoDataFrame, df_home: gpd.GeoDataFrame,
                            persons: pd.DataFrame, detour: float) -> pd.DataFrame:
    """Per-worker ASSIGNED commute-distance class, from home/workplace geometry.

    ``df_work`` -- GeoDataFrame ``person_id, location_id, geometry`` (the work half of
    ``synthesis.population.spatial.primary.locations``); ``df_home`` -- GeoDataFrame
    ``household_id, geometry``; ``persons`` -- DataFrame ``person_id, household_id``. Both
    geometry inputs must carry the same projected CRS (see :func:`_validate_projected_equal_crs`).

    ``distance_km`` is the euclidean home->workplace distance in metres, converted to routed
    kilometres by multiplying with ``detour`` and dividing by 1000 (the same ENTD detour-factor
    convention as ``braunschweig.popsim.trips_stage``). ``assigned_distance_class`` applies
    :func:`classify_commute_distance` with ``topcode_km=None``: this is a continuous model
    distance that was never subject to the MiD ``P_ARB_ENTF`` 200 km top-code, so a distance of
    exactly 200.0 km classifies as ``gt200`` rather than being folded into ``100_200``.

    Workers whose household has no home geometry (a broken ``household_id`` join) are dropped
    and counted in the log rather than silently propagated with a ``NaN`` distance.

    Returns ``person_id, distance_km, assigned_distance_class`` (one row per worker with a
    resolvable home geometry).
    """
    _require_columns(df_work, ("person_id", "location_id", "geometry"), "df_work")
    _require_columns(df_home, ("household_id", "geometry"), "df_home")
    _require_columns(persons, ("person_id", "household_id"), "persons")
    _validate_projected_equal_crs(df_home, df_work)

    n_duplicate_home = int(df_home["household_id"].duplicated().sum())
    if n_duplicate_home:
        logger.warning("%s df_home has %d duplicate household_id row(s); keeping the first per "
                       "household", _LOG_TAG, n_duplicate_home)
    homes = df_home.drop_duplicates(subset="household_id", keep="first")

    home_per_person = persons[["person_id", "household_id"]].merge(
        homes[["household_id", "geometry"]].rename(columns={"geometry": "home_geometry"}),
        on="household_id", how="left")

    frame = df_work[["person_id", "geometry"]].rename(columns={"geometry": "work_geometry"}).merge(
        home_per_person[["person_id", "home_geometry"]], on="person_id", how="left")

    n_workers = len(frame)
    n_no_home = int(frame["home_geometry"].isna().sum())
    if n_no_home:
        logger.warning(
            "%s %d/%d workers (%.1f%%) have no home geometry after the household_id join and are "
            "dropped -- check the home-locations / household_id join",
            _LOG_TAG, n_no_home, n_workers, 100.0 * n_no_home / max(n_workers, 1))
    frame = frame[frame["home_geometry"].notna()].copy()

    home_points = gpd.GeoSeries(frame["home_geometry"].values, crs=df_home.crs)
    work_points = gpd.GeoSeries(frame["work_geometry"].values, crs=df_work.crs)
    frame["distance_km"] = home_points.distance(work_points).to_numpy() / 1000.0 * float(detour)
    frame["assigned_distance_class"] = [
        classify_commute_distance(km, topcode_km=None) for km in frame["distance_km"]
    ]

    logger.info("%s assigned distance class: %d workers classified (detour factor %.2f)",
                _LOG_TAG, len(frame), float(detour))
    return frame[["person_id", "distance_km", "assigned_distance_class"]].reset_index(drop=True)


def _build_share_at_workplace_lookup(table: pd.DataFrame, classes_needed) -> dict:
    """``share_at_workplace`` for each class in ``classes_needed``, folding ``gt200`` onto ``100_200``.

    ``table`` is ``commute_day_state_reference.load_workday_location_table(mid_dir)`` (or an
    equivalent synthetic frame in tests): columns ``distance_class``, ``share_at_workplace``.
    Only the classes actually needed by the caller are looked up (never all six unconditionally):
    the committed MiD table always carries every class the production pipeline needs, but a
    smaller test table exercising only a subset of classes must not be rejected for omitting
    classes nothing asks for.
    """
    _require_columns(table, ("distance_class", "share_at_workplace"), "the workday-location table")
    indexed = table.set_index("distance_class")["share_at_workplace"]
    lookup = {}
    for label in classes_needed:
        table_label = _GT200_TABLE_LABEL if label == "gt200" else label
        if table_label not in indexed.index:
            raise KeyError(
                f"{_LOG_TAG} workday-location table has no row for distance_class={table_label!r} "
                f"(needed to look up class {label!r}); present classes: {sorted(indexed.index)}")
        lookup[label] = float(indexed.loc[table_label])
    return lookup


def keep_probability(assigned_class: str, donor_class, table: pd.DataFrame) -> float:
    """``P(keep)`` for a worker re-drawn from a donor class to an assigned class.

    ``P(keep) = share_at_workplace(assigned_class) / share_at_workplace(donor_class)``, both read
    from ``table`` (see :func:`_build_share_at_workplace_lookup`; an assigned OR donor class of
    ``gt200`` reads the ``100_200`` row, MiD has no ``gt200`` row). Clipped to ``[0, 1]``: the
    ratio is a probability, and the MiD shares are not required to be monotonically ordered
    outside the substitution the model acts on.

    ``donor_class`` of ``None`` (or ``NaN``) means the person has no usable donor distance --
    :func:`donor_distance_class_from_trips` returns ``None`` for such persons -- and ALWAYS
    returns ``1.0`` (no re-draw at all; the donor's own day is used unchanged).
    """
    if pd.isna(donor_class):
        return 1.0
    lookup = _build_share_at_workplace_lookup(table, (assigned_class, donor_class))
    ratio = lookup[assigned_class] / lookup[donor_class]
    return float(np.clip(ratio, 0.0, 1.0))


def draw_states(workers: pd.DataFrame, table: pd.DataFrame, rng: np.random.RandomState, *,
                far_threshold_km: float, absent_share_far: float, escort_persons) -> tuple:
    """Seeded reporting-day state draw over a population of workers.

    ``workers`` -- one row per worker: ``person_id``, ``distance_km`` (assigned, km),
    ``assigned_distance_class``, ``donor_distance_class`` (``None``/``NaN`` allowed). ``table``
    is the MiD workday-location table (see :func:`keep_probability`). ``escort_persons`` -- a set
    of ``person_id`` values with an active escort leg on their donor's reporting day (ADR-0104
    Assumption 4: such a day evidences presence at home, so escort-duty persons may become
    ``home`` but never ``absent``).

    Rule, per worker, in order:

    1. Persons sorted by ``person_id`` first, so the draw is reproducible independent of the
       caller's row order. Two draws are then taken from ``rng`` for the WHOLE population at
       once, ``u`` (the keep draw) then ``u2`` (the far/absent draw) -- both length ``n``.
    2. Re-draw eligibility: ``CLASS_RANK[assigned] > CLASS_RANK[donor]`` (donor class known).
       Not eligible -> ``at_workplace``, ``p_keep = 1.0``, ``redrawn = False``; reason
       ``"donor_class_missing"`` when the donor class itself is unknown, else ``"not_eligible"``.
    3. Eligible: ``p_keep = keep_probability(assigned, donor, table)``. ``u < p_keep`` ->
       ``at_workplace``, reason ``"kept"``.
    4. Eligible and NOT kept: the far rule -- ``distance_km > far_threshold_km`` AND the person
       is NOT in ``escort_persons`` AND ``u2 < absent_share_far`` -> ``absent``, reason
       ``"absent_far"``. A far, escort-protected person instead gets reason
       ``"home_escort_protected"`` (never absent, by construction). Every other not-kept person
       gets ``"home"``, reason ``"home_redraw"``.

    Returns ``(frame, diagnostics)``. ``frame`` columns: ``person_id``, ``commute_day_state``,
    ``p_keep``, ``redrawn`` (bool), ``reason``. ``diagnostics``: ``n_workers``,
    ``n_redraw_eligible``, ``n_donor_class_missing``, ``n_at_workplace``, ``n_home``,
    ``n_absent``, ``n_escort_protected`` (far, escort-protected, not-kept persons -- i.e. where
    escort protection changed the outcome from what would otherwise have been ``absent``), and
    ``by_assigned_class`` (dict, assigned class -> ``{"at_workplace": n, "home": n, "absent": n}``).
    """
    _require_columns(workers, ("person_id", "distance_km", "assigned_distance_class",
                              "donor_distance_class"), "workers frame")
    workers = workers.sort_values("person_id").reset_index(drop=True)
    n = len(workers)

    assigned_class = workers["assigned_distance_class"]
    donor_class = workers["donor_distance_class"]
    donor_missing = donor_class.isna()

    classes_needed = set(assigned_class.dropna().unique()) | set(donor_class.dropna().unique())
    share_lookup = _build_share_at_workplace_lookup(table, classes_needed)

    assigned_rank = assigned_class.map(CLASS_RANK)
    donor_rank = donor_class.map(CLASS_RANK)
    eligible = (donor_rank.notna() & assigned_rank.notna() & (assigned_rank > donor_rank)).to_numpy()

    assigned_share = assigned_class.map(share_lookup)
    donor_share = donor_class.map(share_lookup)
    ratio = (assigned_share / donor_share).clip(lower=0.0, upper=1.0)
    p_keep = pd.Series(1.0, index=workers.index).where(~pd.Series(eligible, index=workers.index), ratio)

    u = rng.random_sample(n)
    u2 = rng.random_sample(n)

    is_escort = workers["person_id"].isin(escort_persons).to_numpy()
    is_far = (workers["distance_km"] > far_threshold_km).to_numpy()

    kept = eligible & (u < p_keep.to_numpy())
    not_kept_eligible = eligible & ~kept
    absent_condition = not_kept_eligible & is_far & ~is_escort & (u2 < absent_share_far)
    escort_protected_condition = not_kept_eligible & is_far & is_escort
    home_redraw_condition = not_kept_eligible & ~(absent_condition | escort_protected_condition)

    state_values = np.full(n, "at_workplace", dtype=object)
    reason_values = np.full(n, "not_eligible", dtype=object)
    reason_values[donor_missing.to_numpy()] = "donor_class_missing"

    state_values[kept] = "at_workplace"
    reason_values[kept] = "kept"
    state_values[absent_condition] = "absent"
    reason_values[absent_condition] = "absent_far"
    state_values[escort_protected_condition] = "home"
    reason_values[escort_protected_condition] = "home_escort_protected"
    state_values[home_redraw_condition] = "home"
    reason_values[home_redraw_condition] = "home_redraw"

    result = pd.DataFrame({
        "person_id": workers["person_id"].to_numpy(),
        "commute_day_state": state_values,
        "p_keep": p_keep.to_numpy(),
        "redrawn": eligible,
        "reason": reason_values,
    })

    by_assigned_class: dict = {}
    for label, state_value in zip(assigned_class, state_values):
        cell = by_assigned_class.setdefault(label, {s: 0 for s in STATES})
        cell[state_value] += 1

    n_at_workplace = int((state_values == "at_workplace").sum())
    n_home = int((state_values == "home").sum())
    n_absent = int((state_values == "absent").sum())
    diagnostics = {
        "n_workers": n,
        "n_redraw_eligible": int(eligible.sum()),
        "n_donor_class_missing": int(donor_missing.sum()),
        "n_at_workplace": n_at_workplace,
        "n_home": n_home,
        "n_absent": n_absent,
        "n_escort_protected": int(escort_protected_condition.sum()),
        "by_assigned_class": by_assigned_class,
    }

    logger.info(
        "%s state draw: %d workers, %d eligible for re-draw (%.1f%%), %d donor class missing "
        "(%.1f%%)", _LOG_TAG, n, diagnostics["n_redraw_eligible"],
        100.0 * diagnostics["n_redraw_eligible"] / max(n, 1), diagnostics["n_donor_class_missing"],
        100.0 * diagnostics["n_donor_class_missing"] / max(n, 1))
    logger.info(
        "%s state shares: at_workplace %.1f%%, home %.1f%%, absent %.1f%% (of which "
        "escort-protected from absent: %d)",
        _LOG_TAG, 100.0 * n_at_workplace / max(n, 1), 100.0 * n_home / max(n, 1),
        100.0 * n_absent / max(n, 1), diagnostics["n_escort_protected"])

    return result, diagnostics
