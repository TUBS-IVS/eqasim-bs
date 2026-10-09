"""Which legs leave the supplied area, and how consecutive far destinations form one stay.

Pure pandas; no synpp. A leg is a portal leg when its distance exceeds the threshold: for work
and education legs the straight-line distance from the person's home to the ASSIGNED location
(the location already exists, the sampler never sees these legs), for every other leg the
donor's reported distance (``euclidean_distance``, from MiD ``wegkm_imp``, a ROUTE length) capped
by the displacement upper bound of the donor's own diary (``displacement_bound_m``, ADR-0141). A
return home is never a portal leg, and a NaN distance (the synthetic home closure) is never
one either. Consecutive portal destinations form ONE outside stay, the semantics of the eqasim
cutter's ``MergeOutsideActivities``: the leg into the run is the outbound leg, the first leg
after it the return leg, the legs inside the run are removed by ``rewrite.rewrite_trips``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.synthesis.portal_trips.config_keys import OUTSIDE_PURPOSE  # noqa: F401 (vocabulary)

HOME_PURPOSE = "home"
PRIMARY_PURPOSES = ("work", "education")
STAY_COLUMNS = ["person_id", "outbound_trip_index", "return_trip_index", "n_removed_legs"]


def _require_unique(ids: pd.Series, column: str, source: str) -> None:
    """Fail early: a duplicated id would silently duplicate or misalign coordinate lookups."""
    duplicated = pd.Series(ids).loc[lambda values: values.duplicated()].unique()
    if len(duplicated):
        raise ValueError(f"[portal_trips] {source} has non-unique {column} values, e.g. "
                         f"{duplicated[:5].tolist()}; exactly one row per {column} is required")


def person_home_xy(persons: pd.DataFrame, df_home) -> pd.DataFrame:
    """Home coordinates per person, joined through the household."""
    homes = pd.DataFrame({"household_id": df_home["household_id"].to_numpy(),
                          "x": df_home.geometry.x.to_numpy(), "y": df_home.geometry.y.to_numpy()})
    _require_unique(homes["household_id"], "household_id", "df_home")
    out = persons[["person_id", "household_id"]].merge(homes, on="household_id", how="left")
    _require_unique(out["person_id"], "person_id", "persons")
    missing = int(out["x"].isna().sum())
    if missing:
        raise ValueError(f"[portal_trips] {missing} persons have no home coordinate")
    return out.set_index("person_id")[["x", "y"]]


def _xy_by_person(frame: pd.DataFrame) -> pd.DataFrame:
    if len(frame) == 0:
        return pd.DataFrame(columns=["x", "y"], index=pd.Index([], name="person_id"), dtype=float)
    _require_unique(frame["person_id"], "person_id", "the assigned-location table")
    return pd.DataFrame({"x": frame.geometry.x.to_numpy(), "y": frame.geometry.y.to_numpy()},
                        index=pd.Index(frame["person_id"].to_numpy(), name="person_id"))


def primary_xy(df_work: pd.DataFrame, df_education: pd.DataFrame) -> dict:
    """Assigned work and education coordinates per person (one row per person each)."""
    return {"work": _xy_by_person(df_work), "education": _xy_by_person(df_education)}


def _grouped_exclusive_sum(values: np.ndarray, group_start: np.ndarray) -> np.ndarray:
    """Sum of ``values`` strictly before each element, restarting at every True in ``group_start``."""
    group_id = np.cumsum(group_start)
    inclusive = pd.Series(values).groupby(group_id).cumsum().to_numpy()
    return inclusive - values


def displacement_bound_m(trips: pd.DataFrame) -> np.ndarray:
    """Upper bound of the straight-line displacement of each leg's destination from the donor's diary.

    By the triangle inequality the displacement of a leg's destination from home is at most the way out
    (reported distances of the legs from the last home departure up to the leg's origin, 0 when the leg
    starts at home) plus the way back (reported distances of the legs from the leg's destination to the
    next home arrival, inclusive of that home-bound leg). The bound needs BOTH sides: a side is unknown when
    one of its legs has no finite reported distance (e.g. the synthetic home closure) or when the chain does
    not start at home before the leg / does not reach home after it; the result is then NaN (no silent
    guess). Home-bound legs get NaN (they are never portal legs).

    The reported distances are route lengths, so the bound is conservative: it over-estimates the
    displacement. Sums never cross a home arrival or a person. Vectorised (per-tour cumulative sums);
    precondition: ``trips`` is sorted by ``person_id, trip_index``, as for ``find_outside_stays``.
    Returns a float array aligned to the rows of ``trips``.
    """
    if len(trips) == 0:
        return np.zeros(0, dtype=float)
    reported = trips["euclidean_distance"].astype(float).to_numpy()
    unknown_leg = np.isnan(reported).astype(float)
    filled = np.where(np.isnan(reported), 0.0, reported)
    persons = trips["person_id"].to_numpy()
    leaves_home = trips["preceding_purpose"].to_numpy() == HOME_PURPOSE
    arrives_home = trips["following_purpose"].to_numpy() == HOME_PURPOSE

    # Way out: a tour begins at every home departure; the first leg of a person and the leg after a home
    # arrival begin one too, which is a complete tour only when that leg itself leaves home (a chain that
    # does not start at home has an unknown way out). pandas cumsum skips NaN, so unknown legs are summed
    # separately as a count instead of being propagated as NaN.
    tour_start = leaves_home | np.r_[True, persons[1:] != persons[:-1]] | np.r_[False, arrives_home[:-1]]
    started_at_home = pd.Series(leaves_home).groupby(np.cumsum(tour_start)).transform("first").to_numpy()
    way_out = _grouped_exclusive_sum(filled, tour_start)
    way_out_known = started_at_home & (_grouped_exclusive_sum(unknown_leg, tour_start) == 0)

    # Way back: the same on the reversed chain, where a tour begins at every home arrival, at the last leg of
    # a person and at a leg followed by a home departure (the chain then did not reach home).
    tour_end = (arrives_home | np.r_[persons[:-1] != persons[1:], True] | np.r_[leaves_home[1:], False])[::-1]
    ended_at_home = pd.Series(arrives_home[::-1]).groupby(np.cumsum(tour_end)).transform("first").to_numpy()
    way_back = _grouped_exclusive_sum(filled[::-1], tour_end)[::-1]
    way_back_known = (ended_at_home & (_grouped_exclusive_sum(unknown_leg[::-1], tour_end) == 0))[::-1]

    return np.where(way_out_known & way_back_known & ~arrives_home, way_out + way_back, np.nan)


def classification_distance_frame(trips: pd.DataFrame, home_xy: pd.DataFrame, primary_xy: dict) -> pd.DataFrame:
    """Metres that decide the classification of each leg, plus the fallback and displacement-bound diagnostics.

    Columns (aligned to ``trips.index``):

    - ``classification_distance_m`` (float, metres): the ONE distance used for the portal rule and for the
      external point draw. Work/education legs: home to the assigned location. Every other leg:
      ``min(reported distance, displacement_bound_m)``, or the reported distance when no bound is known.
    - ``used_reported_distance`` (bool): True ONLY for work and education legs whose
      home-to-assigned-location distance is NaN (no assigned location or no home coordinate), so the
      donor's reported distance was used instead. The caller logs the rate; a high rate means the
      assigned-location join is broken.
    - ``displacement_bound_m`` (float, metres): the diary's displacement upper bound (see
      ``displacement_bound_m``) for non-primary, non-home-bound legs; NaN elsewhere and where a side of the
      bound is unknown.
    - ``bound_applied`` (bool): the bound was finite and smaller than the finite reported distance.
    - ``bound_unknown`` (bool): a non-primary, non-home-bound leg without a usable bound (its reported
      distance stays). The caller logs the rate.
    """
    distance = trips["euclidean_distance"].astype(float).to_numpy().copy()
    reported = distance.copy()
    used_reported = np.zeros(len(trips), dtype=bool)
    purposes = trips["following_purpose"].to_numpy()
    person_ids = trips["person_id"].to_numpy()
    for purpose in PRIMARY_PURPOSES:
        mask = purposes == purpose
        if not mask.any():
            continue
        located = primary_xy[purpose].reindex(person_ids[mask])
        home = home_xy.reindex(person_ids[mask])
        assigned = np.hypot(located["x"].to_numpy() - home["x"].to_numpy(),
                            located["y"].to_numpy() - home["y"].to_numpy())
        # A person without an assigned location keeps the reported distance (legacy fixtures).
        missing = np.isnan(assigned)
        distance[mask] = np.where(missing, distance[mask], assigned)
        used_reported[mask] = missing
    # The bound applies to the legs the sampler drew: not work/education (assigned location), not the return
    # home. A NaN reported distance stays NaN (never portal): there is nothing to tighten.
    non_primary = ~np.isin(purposes, PRIMARY_PURPOSES) & (purposes != HOME_PURPOSE)
    bound = np.where(non_primary, displacement_bound_m(trips), np.nan)
    tighter = non_primary & np.isfinite(bound) & np.isfinite(reported) & (bound < reported)
    distance[tighter] = bound[tighter]
    return pd.DataFrame({"classification_distance_m": distance, "used_reported_distance": used_reported,
                         "displacement_bound_m": bound, "bound_applied": tighter,
                         "bound_unknown": non_primary & np.isnan(bound)}, index=trips.index)


def classification_distance_m(trips: pd.DataFrame, home_xy: pd.DataFrame, primary_xy: dict) -> pd.Series:
    """Metres that decide the classification of each leg (see ``classification_distance_frame``)."""
    return classification_distance_frame(trips, home_xy, primary_xy)["classification_distance_m"]


def portal_flags(distance_m: pd.Series, following_purpose: pd.Series, threshold_m: float) -> pd.Series:
    """The one portal rule: distance strictly beyond ``threshold_m``, finite, and the leg is not a return home."""
    is_portal = distance_m.gt(threshold_m) & distance_m.notna() & following_purpose.ne(HOME_PURPOSE)
    return is_portal.rename("is_portal")


def classify_portal_legs(trips: pd.DataFrame, home_xy: pd.DataFrame, primary_xy: dict,
                         threshold_m: float) -> pd.Series:
    """True for every leg whose destination lies beyond ``threshold_m``; never home, never NaN."""
    distance = classification_distance_m(trips, home_xy, primary_xy)
    return portal_flags(distance, trips["following_purpose"], threshold_m)


def find_outside_stays(trips: pd.DataFrame, is_portal: pd.Series) -> pd.DataFrame:
    """One row per maximal run of consecutive portal legs within a person (see module docstring).

    Preconditions: ``trips`` is sorted by ``person_id, trip_index`` with a fresh ``RangeIndex``, and
    ``is_portal`` is positionally aligned to ``trips`` (same length, same row order). The length is
    checked; the sort order is the caller's responsibility.
    """
    if len(is_portal) != len(trips):
        raise ValueError(f"[portal_trips] is_portal has {len(is_portal)} entries but trips has "
                         f"{len(trips)} rows; the flags must be positionally aligned to the trips")
    flags = np.asarray(is_portal, dtype=bool)
    persons = trips["person_id"].to_numpy()
    trip_index = trips["trip_index"].to_numpy()
    same_as_previous = np.r_[False, persons[1:] == persons[:-1]]
    same_as_next = np.r_[persons[:-1] == persons[1:], False]
    previous_flag = np.r_[False, flags[:-1]] & same_as_previous
    next_flag = np.r_[flags[1:], False] & same_as_next
    starts = np.flatnonzero(flags & ~previous_flag)
    ends = np.flatnonzero(flags & ~next_flag)
    rows = []
    for start, end in zip(starts, ends):
        has_return = end + 1 < len(flags) and same_as_next[end]
        rows.append({
            "person_id": persons[start],
            "outbound_trip_index": int(trip_index[start]),
            "return_trip_index": float(trip_index[end + 1]) if has_return else np.nan,
            "n_removed_legs": int(end - start),
        })
    return pd.DataFrame(rows, columns=STAY_COLUMNS)
