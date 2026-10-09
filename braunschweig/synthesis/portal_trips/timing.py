"""Arrival at the gate and re-entry time of a portal stay (eqasim-bs#442, ADR-0141).

The donor diary supplies the DURATIONS of the outbound and the return leg; the synthetic geometry (origin, gate,
external point; metres, one CRS) decides how each duration is split at the gate. No reported (donor) distance enters
the timing: it describes the donor trip, not the synthetic geometry (for a work/education stay the point is the
ASSIGNED location, which the donor distance has no relation to).

Outbound leg: ``share_in = d(origin, gate) / (d(origin, gate) + d(gate, point))`` and
``arrival_at_gate = departure + share_in * (arrival - departure)``. The secondary chain solver samples the
distance of the preceding secondary leg from ``arrival - departure``, so the original diary arrival at the far
destination would inflate it.

Return leg: ``share_out = d(gate, point) / (d(gate, point) + d(gate, return_proxy))`` with ``return_proxy`` the
stay origin (the same proxy ``rewrite.rewrite_trips`` uses for the inside distance of the return leg), and
``t_reentry = return.departure + share_out * (return.arrival - return.departure)``; the remaining
``(1 - share_out)`` of the duration is the inside part of the return leg.

Both shares lie in [0, 1] by construction. A zero denominator (every distance involved is zero) gives the share 0.5
and is counted as ``degenerate``. A re-entry before the outbound departure or the arrival at the gate cannot
occur with consistent diary data and is clamped and counted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Share used when both distances of a split are zero (nothing to split by); the stay is counted as degenerate.
DEGENERATE_SHARE = 0.5

TIMING_COLUMNS = ["t_reentry", "share_out", "inside_return_duration", "clamped", "share_degenerate", "has_return"]
OUTBOUND_COLUMNS = ["outbound_arrival_time", "outbound_share", "outbound_share_degenerate"]


def _leg_values(trips: pd.DataFrame, person_ids, trip_indices, column: str) -> np.ndarray:
    """Look up one trip column for (person, trip index) pairs; absent pairs give NaN."""
    lookup = trips.set_index(["person_id", "trip_index"])[column]
    keys = pd.MultiIndex.from_arrays([person_ids, trip_indices])
    return lookup.reindex(keys).to_numpy(dtype=float)


def _as_xy(name: str, values, n_stays: int) -> np.ndarray:
    """Coerce to a finite float (n_stays, 2) array or raise a ValueError naming the argument."""
    xy = np.asarray(values, dtype=float)
    if xy.shape != (n_stays, 2):
        raise ValueError(f"[portal_trips] {name} {xy.shape} must be ({n_stays}, 2), one row per stay")
    if not np.isfinite(xy).all():
        # A NaN coordinate would otherwise look like a zero denominator and be absorbed as a degenerate share.
        raise ValueError(f"[portal_trips] {name} holds non-finite coordinates; every stay needs a resolved "
                         "origin, gate and external point before its times are split")
    return xy


def _distance_m(from_xy: np.ndarray, to_xy: np.ndarray) -> np.ndarray:
    return np.hypot(to_xy[:, 0] - from_xy[:, 0], to_xy[:, 1] - from_xy[:, 1])


def _split_share(first_m: np.ndarray, second_m: np.ndarray):
    """``first / (first + second)`` per row and the mask of degenerate rows (zero denominator, share 0.5)."""
    total_m = first_m + second_m
    degenerate = ~(total_m > 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(degenerate, DEGENERATE_SHARE, first_m / total_m)
    return share, degenerate


def outbound_arrivals(stays: pd.DataFrame, trips: pd.DataFrame, origin_xy, gate_xy, point_xy) -> pd.DataFrame:
    """One row per stay: the arrival time at the gate of the outbound leg (see the module docstring).

    ``origin_xy``, ``gate_xy`` and ``point_xy`` are (n_stays, 2) arrays in the same metric CRS; times are seconds.
    ``outbound_share_degenerate`` marks stays whose origin -> gate and gate -> point distances are both zero
    (share 0.5). Raises ``ValueError`` when the outbound leg has no departure or arrival time in ``trips``.
    """
    origin_xy = _as_xy("origin_xy", origin_xy, len(stays))
    gate_xy = _as_xy("gate_xy", gate_xy, len(stays))
    point_xy = _as_xy("point_xy", point_xy, len(stays))
    persons = stays["person_id"].to_numpy()
    outbound_index = stays["outbound_trip_index"].to_numpy()
    departure = _leg_values(trips, persons, outbound_index, "departure_time")
    arrival = _leg_values(trips, persons, outbound_index, "arrival_time")
    share, degenerate = _split_share(_distance_m(origin_xy, gate_xy), _distance_m(gate_xy, point_xy))
    arrival_at_gate = departure + share * (arrival - departure)
    unresolved = ~np.isfinite(arrival_at_gate)
    if unresolved.any():
        raise ValueError(f"[portal_trips] {int(unresolved.sum())} stays name an outbound trip whose departure or "
                         f"arrival time is not available in the trips table (first person_id "
                         f"{persons[unresolved][0]}); the stay table and the trips table are inconsistent")
    return pd.DataFrame({"outbound_arrival_time": arrival_at_gate, "outbound_share": share,
                         "outbound_share_degenerate": degenerate}, columns=OUTBOUND_COLUMNS)


def reentry_times(stays: pd.DataFrame, trips: pd.DataFrame, gate_xy, point_xy, origin_xy,
                  outbound_arrival_time=None) -> pd.DataFrame:
    """One row per stay, in the order of ``stays`` (see the module docstring).

    ``gate_xy``, ``point_xy`` and ``origin_xy`` (the return proxy: the stay origin) are (n_stays, 2) arrays in the
    same metric CRS; times are seconds. ``share_degenerate`` marks stays with a return leg whose gate -> point and
    gate -> origin distances are both zero (share 0.5); ``clamped`` marks re-entries moved up to the lower bound,
    which is the outbound departure, or ``outbound_arrival_time`` (the arrival at the gate, see
    ``outbound_arrivals``) when given, so the gate activity never ends before it starts. Stays without a return
    leg (``has_return`` False) carry NaN in the numeric columns.
    """
    gate_xy = _as_xy("gate_xy", gate_xy, len(stays))
    point_xy = _as_xy("point_xy", point_xy, len(stays))
    origin_xy = _as_xy("origin_xy", origin_xy, len(stays))
    persons = stays["person_id"].to_numpy()
    has_return = stays["return_trip_index"].notna().to_numpy()
    return_index = np.where(has_return, stays["return_trip_index"].fillna(-1).to_numpy(), -1).astype(int)
    outbound_departure = _leg_values(trips, persons, stays["outbound_trip_index"].to_numpy(), "departure_time")
    departure = _leg_values(trips, persons, return_index, "departure_time")
    arrival = _leg_values(trips, persons, return_index, "arrival_time")
    share, degenerate = _split_share(_distance_m(gate_xy, point_xy), _distance_m(gate_xy, origin_xy))
    duration = arrival - departure
    t_reentry = departure + share * duration
    unresolved = has_return & ~(np.isfinite(t_reentry) & np.isfinite(outbound_departure))
    if unresolved.any():
        # A return leg that is absent from the trips table or lacks departure/arrival times would
        # otherwise surface as a NaN re-entry time that looks like a legitimate "no return" stay, and
        # a NaN outbound departure would silently disable the clamp below.
        raise ValueError(f"[portal_trips] {int(unresolved.sum())} stays name an outbound or return trip whose "
                         f"departure or arrival time is not available in the trips table (first person_id "
                         f"{persons[unresolved][0]}); the stay table and the trips table are inconsistent")
    lower_bound = outbound_departure
    if outbound_arrival_time is not None:
        lower_bound = np.maximum(outbound_departure, np.asarray(outbound_arrival_time, dtype=float))
    clamped = has_return & (t_reentry < lower_bound)
    t_reentry = np.where(clamped, lower_bound, t_reentry)
    inside = np.where(clamped, 0.0, (1.0 - share) * duration)
    return pd.DataFrame({
        "t_reentry": np.where(has_return, t_reentry, np.nan),
        "share_out": np.where(has_return, share, np.nan),
        "inside_return_duration": np.where(has_return, inside, np.nan),
        "clamped": clamped, "share_degenerate": degenerate & has_return, "has_return": has_return,
    }, columns=TIMING_COLUMNS)
