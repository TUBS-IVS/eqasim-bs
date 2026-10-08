"""Re-entry time of a portal stay, from the donor's diary alone (eqasim-bs#442).

The stay ends when the agent crosses back into the supplied area:
``t_reentry = return.departure + share_out * (return.arrival - return.departure)`` with
``share_out`` the straight-line share of the return leg that lies outside (gate -> external
point over the leg's reported distance). No speed assumption: the diary's own duration is
split. A missing or zero reported distance gives ``share_out = 1`` (counted); a re-entry before
the outbound departure cannot occur with consistent diary data and is clamped and counted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TIMING_COLUMNS = ["t_reentry", "share_out", "inside_return_duration", "clamped", "share_capped", "has_return"]


def _leg_values(trips: pd.DataFrame, person_ids, trip_indices, column: str) -> np.ndarray:
    """Look up one trip column for (person, trip index) pairs; absent pairs give NaN."""
    lookup = trips.set_index(["person_id", "trip_index"])[column]
    keys = pd.MultiIndex.from_arrays([person_ids, trip_indices])
    return lookup.reindex(keys).to_numpy(dtype=float)


def reentry_times(stays: pd.DataFrame, trips: pd.DataFrame, gate_xy, point_xy) -> pd.DataFrame:
    """One row per stay, in the order of ``stays`` (see the module docstring).

    ``gate_xy`` and ``point_xy`` are (n_stays, 2) arrays in the same metric CRS; times are seconds.
    ``share_capped`` marks stays whose share was forced to 1 (reported distance missing, zero or
    shorter than the outside distance); ``clamped`` marks re-entries moved up to the outbound
    departure. Stays without a return leg (``has_return`` False) carry NaN in the numeric columns.
    """
    gate_xy = np.asarray(gate_xy, dtype=float)
    point_xy = np.asarray(point_xy, dtype=float)
    if gate_xy.shape != (len(stays), 2) or point_xy.shape != (len(stays), 2):
        raise ValueError(f"[portal_trips] gate_xy {gate_xy.shape} and point_xy {point_xy.shape} must both be "
                         f"({len(stays)}, 2), one row per stay")
    persons = stays["person_id"].to_numpy()
    has_return = stays["return_trip_index"].notna().to_numpy()
    return_index = np.where(has_return, stays["return_trip_index"].fillna(-1).to_numpy(), -1).astype(int)
    outbound_departure = _leg_values(trips, persons, stays["outbound_trip_index"].to_numpy(), "departure_time")
    departure = _leg_values(trips, persons, return_index, "departure_time")
    arrival = _leg_values(trips, persons, return_index, "arrival_time")
    reported = _leg_values(trips, persons, return_index, "euclidean_distance")
    outside_m = np.hypot(point_xy[:, 0] - gate_xy[:, 0], point_xy[:, 1] - gate_xy[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        share = outside_m / reported
    share_capped = ~np.isfinite(share) | (share > 1.0)
    share = np.where(share_capped, 1.0, share)
    duration = arrival - departure
    t_reentry = departure + share * duration
    unresolved = has_return & ~np.isfinite(t_reentry)
    if unresolved.any():
        # A return leg that is absent from the trips table or lacks departure/arrival times would
        # otherwise surface as a NaN re-entry time that looks like a legitimate "no return" stay.
        raise ValueError(f"[portal_trips] {int(unresolved.sum())} stays name a return trip whose departure or "
                         f"arrival time is not available in the trips table (first person_id "
                         f"{persons[unresolved][0]}); the stay table and the trips table are inconsistent")
    clamped = has_return & (t_reentry < outbound_departure)
    t_reentry = np.where(clamped, outbound_departure, t_reentry)
    inside = np.where(clamped, 0.0, (1.0 - share) * duration)
    return pd.DataFrame({
        "t_reentry": np.where(has_return, t_reentry, np.nan),
        "share_out": np.where(has_return, share, np.nan),
        "inside_return_duration": np.where(has_return, inside, np.nan),
        "clamped": clamped, "share_capped": share_capped & has_return, "has_return": has_return,
    }, columns=TIMING_COLUMNS)
