# braunschweig/synthesis/portal_trips/rewrite.py
"""Rewrite the trip table around the outside stays and emit the gate anchors (eqasim-bs#442).

Per stay: the legs inside the run are dropped; the outbound leg's destination becomes the
``outside`` activity at the gate (its departure is unchanged); the return leg departs from the
gate at the diary re-entry time with the inside share of its reported duration; both legs
carry the fixed mode and ``portal_leg`` True; their ``euclidean_distance`` becomes the inside
part so downstream distance statistics see what is simulated: outbound origin -> gate, and for the
return leg gate -> the same ``origin_xy`` (the stay's origin), NOT gate -> the return leg's own
destination. Only the reported ``euclidean_distance`` is affected; the chain solver does not use it
(see ADR-0141, eqasim-bs#442). The chain columns are recomputed exactly as the trip build derives
them (``trip_index`` 0-based per person, first/last flags, ``trip_duration``, ``activity_duration`` =
next departure - arrival, NaN on the last trip).
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig.synthesis.portal_trips.config_keys import OUTSIDE_PURPOSE

PORTAL_LEG_COLUMN = "portal_leg"
_ROW_ID_COLUMN = "_portal_row_id"
ANCHOR_COLUMNS = ["person_id", "activity_index", "gate_id", "kind", "mode", "geometry"]


def empty_anchors(crs) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({column: pd.Series(dtype=object) for column in ANCHOR_COLUMNS if column != "geometry"},
                            geometry=[], crs=crs)


def recompute_chain_columns(trips: pd.DataFrame) -> pd.DataFrame:
    """Sort by person and trip index and re-derive the per-person chain columns."""
    out = trips.sort_values(["person_id", "trip_index"], kind="mergesort").reset_index(drop=True)
    out["trip_index"] = out.groupby("person_id").cumcount()
    out["is_first_trip"] = out["trip_index"] == 0
    out["is_last_trip"] = out["trip_index"] == out.groupby("person_id")["trip_index"].transform("max")
    out["trip_duration"] = out["arrival_time"] - out["departure_time"]
    next_departure = out.groupby("person_id")["departure_time"].shift(-1)
    out["activity_duration"] = next_departure - out["arrival_time"]
    return out


def rewrite_trips(trips: pd.DataFrame, stays: pd.DataFrame, gate_rows: pd.DataFrame, times: pd.DataFrame,
                  modes: pd.DataFrame, origin_xy, crs):
    """Return ``(trips_out, anchors)`` -- see the module docstring.

    ``stays``, ``gate_rows``, ``times``, ``modes`` and ``origin_xy`` are row-aligned (one row per stay);
    ``origin_xy`` is the chain anchor (metres, ``crs``) the outbound leg departs from -- the caller
    decides and counts any proxy used for it. The input is not modified. With no stays the input
    order and index are returned unchanged, plus the ``portal_leg`` column (all False), and an empty
    anchors frame.
    """
    out = trips.copy()
    out[PORTAL_LEG_COLUMN] = False
    if len(stays) == 0:
        return out, empty_anchors(crs)
    origin_xy = np.asarray(origin_xy, dtype=float)
    lengths = {"gate_rows": len(gate_rows), "times": len(times), "modes": len(modes), "origin_xy": len(origin_xy)}
    if any(length != len(stays) for length in lengths.values()):
        raise ValueError("rewrite_trips: per-stay inputs must be row-aligned with the %d stays, got %s"
                         % (len(stays), lengths))
    out = out.reset_index(drop=True)
    # Vectorised lookup of the rows each stay touches, via (person_id, trip_index) -> row position.
    positions = pd.Series(np.arange(len(out)), index=pd.MultiIndex.from_frame(out[["person_id", "trip_index"]]))
    if not positions.index.is_unique:
        raise ValueError("rewrite_trips: (person_id, trip_index) is not unique in the trip table; "
                         "the stay frame cannot be mapped to rows.")
    person_ids = stays["person_id"].to_numpy()
    start = stays["outbound_trip_index"].to_numpy(dtype=int)
    n_removed = stays["n_removed_legs"].to_numpy(dtype=int)
    has_return_stay = stays["return_trip_index"].notna().to_numpy()

    def _row_positions(person, trip_index):
        found = positions.reindex(pd.MultiIndex.from_arrays([person, trip_index])).to_numpy()
        if np.isnan(found).any():
            raise ValueError("rewrite_trips: %d stay leg(s) reference a (person_id, trip_index) that is not in "
                             "the trip table, e.g. person_id=%s trip_index=%s."
                             % (int(np.isnan(found).sum()), person[np.isnan(found)][0],
                                np.asarray(trip_index)[np.isnan(found)][0]))
        return found.astype(int)

    outbound_positions = _row_positions(person_ids, start)
    return_positions = np.full(len(stays), -1, dtype=int)
    return_positions[has_return_stay] = _row_positions(
        person_ids[has_return_stay], stays["return_trip_index"].to_numpy()[has_return_stay].astype(int))
    removed_stay = np.repeat(np.arange(len(stays)), n_removed)
    removed_offset = np.arange(n_removed.sum()) - np.repeat(np.cumsum(n_removed) - n_removed, n_removed) + 1
    drop_positions = _row_positions(person_ids[removed_stay], start[removed_stay] + removed_offset)
    gate_xy = gate_rows[["x", "y"]].to_numpy(dtype=float)
    inside_m = np.hypot(gate_xy[:, 0] - origin_xy[:, 0], gate_xy[:, 1] - origin_xy[:, 1])
    mode = modes["mode"].to_numpy()

    out.iloc[outbound_positions, out.columns.get_loc("following_purpose")] = OUTSIDE_PURPOSE
    out.iloc[outbound_positions, out.columns.get_loc("mode")] = mode
    out.iloc[outbound_positions, out.columns.get_loc("euclidean_distance")] = inside_m
    out.iloc[outbound_positions, out.columns.get_loc(PORTAL_LEG_COLUMN)] = True

    with_return = return_positions >= 0
    returning = return_positions[with_return]
    out.iloc[returning, out.columns.get_loc("preceding_purpose")] = OUTSIDE_PURPOSE
    out.iloc[returning, out.columns.get_loc("mode")] = mode[with_return]
    out.iloc[returning, out.columns.get_loc("euclidean_distance")] = inside_m[with_return]
    out.iloc[returning, out.columns.get_loc("departure_time")] = times["t_reentry"].to_numpy()[with_return]
    out.iloc[returning, out.columns.get_loc("arrival_time")] = (
        times["t_reentry"].to_numpy() + times["inside_return_duration"].to_numpy())[with_return]
    out.iloc[returning, out.columns.get_loc(PORTAL_LEG_COLUMN)] = True

    # Track the surviving rows through the re-sort with a temporary row id, so no trip identifier column is needed.
    out[_ROW_ID_COLUMN] = np.arange(len(out))
    if len(drop_positions):
        out = out.drop(out.index[drop_positions])
    out = recompute_chain_columns(out)
    # The outside activity follows the outbound leg: activity_index = new trip_index + 1.
    new_index = pd.Series(out["trip_index"].to_numpy(), index=out[_ROW_ID_COLUMN].to_numpy())
    outbound_new = new_index.reindex(outbound_positions).to_numpy()
    lost = np.isnan(outbound_new)
    if lost.any():
        raise ValueError("rewrite_trips: %d stay(s) have an outbound leg that does not survive the rewrite "
                         "(removed by another stay of the same person, i.e. overlapping stays), e.g. person_id=%s."
                         % (int(lost.sum()), person_ids[lost][0]))
    out = out.drop(columns=[_ROW_ID_COLUMN])
    anchors = gpd.GeoDataFrame({
        "person_id": person_ids,
        "activity_index": (outbound_new + 1).astype(int),
        "gate_id": gate_rows["gate_id"].to_numpy(), "kind": gate_rows["kind"].to_numpy(), "mode": mode,
    }, geometry=[Point(x, y) for x, y in gate_xy], crs=crs)[ANCHOR_COLUMNS]
    return out, anchors
