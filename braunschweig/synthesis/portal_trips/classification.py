"""Which legs leave the supplied area, and how consecutive far destinations form one stay.

Pure pandas; no synpp. A leg is a portal leg when its distance exceeds the threshold: for work
and education legs the straight-line distance from the person's home to the ASSIGNED location
(the location already exists, the sampler never sees these legs), for every other leg the
donor's reported straight-line distance (``euclidean_distance``, from MiD ``wegkm_imp``). A
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


def classification_distance_frame(trips: pd.DataFrame, home_xy: pd.DataFrame, primary_xy: dict) -> pd.DataFrame:
    """Metres that decide the classification of each leg, plus a flag for the reported-distance fallback.

    Columns (aligned to ``trips.index``): ``classification_distance_m`` (float, metres) and
    ``used_reported_distance`` (bool). The flag is True ONLY for work and education legs whose
    home-to-assigned-location distance is NaN (no assigned location or no home coordinate), so
    the donor's reported distance was used instead. The caller logs the rate; a high rate means
    the assigned-location join is broken.
    """
    distance = trips["euclidean_distance"].astype(float).to_numpy().copy()
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
    return pd.DataFrame({"classification_distance_m": distance, "used_reported_distance": used_reported},
                        index=trips.index)


def classification_distance_m(trips: pd.DataFrame, home_xy: pd.DataFrame, primary_xy: dict) -> pd.Series:
    """Metres that decide the classification of each leg (see ``classification_distance_frame``)."""
    return classification_distance_frame(trips, home_xy, primary_xy)["classification_distance_m"]


def classify_portal_legs(trips: pd.DataFrame, home_xy: pd.DataFrame, primary_xy: dict,
                         threshold_m: float) -> pd.Series:
    """True for every leg whose destination lies beyond ``threshold_m``; never home, never NaN."""
    distance = classification_distance_m(trips, home_xy, primary_xy)
    is_portal = distance.gt(threshold_m) & distance.notna() & trips["following_purpose"].ne(HOME_PURPOSE)
    return is_portal.rename("is_portal")


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
