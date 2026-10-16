"""Home-based tours, leg geometry and the outside classification (eqasim-bs#453).

A home-based tour starts at every leg that departs from ``home``; the chain start opens the first tour
(a chain that starts away from home still has a tour 0). A tour is an OUTSIDE tour when any of its legs
ends outside the cut extent or at an ``outside`` activity (a portal gate lies inside the extent by
construction, ADR-0141, so the purpose test is what catches it). Leg distances are straight-line metres
between the realised activity locations; a missing location is an error, never a fallback.

All functions are vectorised over the legs (about 3.4 million at 100 %); none loops over legs in Python.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from braunschweig.synthesis.outside_tour_modes.config_keys import HOME_PURPOSE, OUTSIDE_PURPOSE

ENDPOINT_COLUMNS = ["ox", "oy", "dx", "dy"]


def require_sorted(trips: pd.DataFrame) -> None:
    """Fail early: tour ids and the cumulative sums below are only meaningful on sorted rows."""
    keys = trips[["person_id", "trip_index"]].to_numpy()
    if len(keys) > 1:
        person_ok = keys[1:, 0] >= keys[:-1, 0]
        same = keys[1:, 0] == keys[:-1, 0]
        index_ok = ~same | (keys[1:, 1] > keys[:-1, 1])
        if not (person_ok.all() and index_ok.all()):
            raise ValueError("[outside_tour_modes] trips must be sorted by person_id, trip_index")


def home_based_tour_ids(trips: pd.DataFrame) -> np.ndarray:
    """0-based tour id per leg within its person: a new tour at every departure from home after the first leg."""
    require_sorted(trips)
    starts = trips["preceding_purpose"].eq(HOME_PURPOSE).to_numpy()
    person = trips["person_id"].to_numpy()
    first_of_person = np.ones(len(trips), dtype=bool)
    first_of_person[1:] = person[1:] != person[:-1]
    # the first leg of a person never increments; later home departures do
    increments = starts & ~first_of_person
    cumulative = np.cumsum(increments)
    # reset per person: subtract the cumulative value at the person's first leg. The cumulative sum is
    # non-decreasing, so a running maximum carries each person's offset over the rest of that person's legs.
    offsets = np.where(first_of_person, cumulative, 0)
    offsets = np.maximum.accumulate(offsets)
    return (cumulative - offsets).astype(int)


def leg_endpoints(trips: pd.DataFrame, locations) -> pd.DataFrame:
    """Origin and destination coordinates per leg (index aligned with ``trips``); raises on a gap."""
    points = pd.DataFrame({"person_id": locations["person_id"].to_numpy(),
                           "activity_index": locations["activity_index"].to_numpy(),
                           "x": locations.geometry.x.to_numpy(), "y": locations.geometry.y.to_numpy()})
    if points.duplicated(["person_id", "activity_index"]).any():
        raise ValueError("[outside_tour_modes] locations carry duplicated (person_id, activity_index) rows")
    keyed = points.set_index(["person_id", "activity_index"])
    person = trips["person_id"].to_numpy()
    trip_index = trips["trip_index"].to_numpy()
    origin = keyed.reindex(pd.MultiIndex.from_arrays([person, trip_index]))
    destination = keyed.reindex(pd.MultiIndex.from_arrays([person, trip_index + 1]))
    missing = origin["x"].isna().to_numpy() | destination["x"].isna().to_numpy()
    if missing.any():
        first = int(np.flatnonzero(missing)[0])
        raise ValueError(f"[outside_tour_modes] {int(missing.sum())} legs have no location for their origin or "
                         f"destination activity (first: person_id {person[first]}, trip_index "
                         f"{trip_index[first]}); the locations frame must cover every activity")
    return pd.DataFrame({"ox": origin["x"].to_numpy(), "oy": origin["y"].to_numpy(),
                         "dx": destination["x"].to_numpy(), "dy": destination["y"].to_numpy()},
                        index=trips.index, columns=ENDPOINT_COLUMNS)


def leg_distance_m(endpoints: pd.DataFrame) -> np.ndarray:
    """Straight-line leg length in metres (projected CRS) between the realised activity locations."""
    return np.hypot(endpoints["dx"].to_numpy() - endpoints["ox"].to_numpy(),
                    endpoints["dy"].to_numpy() - endpoints["oy"].to_numpy())


def destination_outside(endpoints: pd.DataFrame, polygon) -> np.ndarray:
    """True per leg when its destination lies outside ``polygon`` (vectorised; a point on the boundary is outside)."""
    inside = shapely.contains_xy(polygon, endpoints["dx"].to_numpy(), endpoints["dy"].to_numpy())
    return ~np.asarray(inside, dtype=bool)


def outside_tour_flags(trips: pd.DataFrame, tour_ids: np.ndarray, dest_outside: np.ndarray) -> np.ndarray:
    """True for every leg of a tour that has an outside destination or an ``outside`` purpose at either end."""
    touches = (np.asarray(dest_outside, dtype=bool)
               | trips["following_purpose"].eq(OUTSIDE_PURPOSE).to_numpy()
               | trips["preceding_purpose"].eq(OUTSIDE_PURPOSE).to_numpy())
    per_tour = pd.Series(touches).groupby([trips["person_id"].to_numpy(), np.asarray(tour_ids)]).transform("any")
    return per_tour.to_numpy(dtype=bool)
