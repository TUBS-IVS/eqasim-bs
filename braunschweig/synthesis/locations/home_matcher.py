# braunschweig/synthesis/locations/home_matcher.py
"""Lexicographic per-cell home matcher: type (primary) then size (secondary)."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import numpy as np, pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from braunschweig.popsim.cells import parse_inspire_id

TYPES = ("efh_zfh", "mfh", "sonst")
_PEN = {("efh_zfh", "efh_zfh"): 0, ("mfh", "mfh"): 0, ("sonst", "sonst"): 0,
        ("efh_zfh", "sonst"): 1, ("sonst", "efh_zfh"): 1, ("mfh", "sonst"): 1,
        ("sonst", "mfh"): 1, ("efh_zfh", "mfh"): 2, ("mfh", "efh_zfh"): 2}


def solve_type_flow(hh_by_type: dict, cap_by_type: dict) -> dict:
    hh = {t: int(hh_by_type.get(t, 0)) for t in TYPES}
    cap = {t: int(cap_by_type.get(t, 0)) for t in TYPES}
    flow = {}
    # 1) fill matching type
    for t in TYPES:
        m = min(hh[t], cap[t])
        if m:
            flow[(t, t)] = m
            hh[t] -= m
            cap[t] -= m
    # 2) route remaining HH to cheapest remaining capacity
    pairs = sorted(((src, dst) for src in TYPES for dst in TYPES if src != dst),
                   key=lambda p: _PEN[p])
    for src, dst in pairs:
        if hh[src] and cap[dst]:
            m = min(hh[src], cap[dst])
            flow[(src, dst)] = flow.get((src, dst), 0) + m
            hh[src] -= m
            cap[dst] -= m
    return flow


@dataclass(frozen=True)
class MatchReport:
    n_households: int
    n_type_match: int
    n_overcapacity: int


def match_cell(households: pd.DataFrame, slots: pd.DataFrame, rng):
    hh = households.reset_index(drop=True).copy()
    n = len(hh)
    if slots is None or slots.empty:
        # no buildings in the cell at all -> caller handles the in-cell random point
        return pd.DataFrame({"household_id": hh["household_id"], "building_id": pd.NA}), \
            MatchReport(n_households=n, n_type_match=0, n_overcapacity=n)
    slots = slots.reset_index(drop=True).copy()
    hh_by_type = hh.groupby("btype").size().to_dict()
    cap_by_type = slots.groupby("btype").size().to_dict()
    flow = solve_type_flow(hh_by_type, cap_by_type)

    # queues of HH ids per type, sorted by size desc (largest HH first)
    hh_q = {t: hh[hh.btype == t].sort_values("household_size", ascending=False)["household_id"].tolist()
            for t in TYPES}
    # queues of slots per type, sorted by size desc (largest dwelling first)
    sl_q = {t: slots[slots.btype == t].sort_values("size", ascending=False).to_dict("records")
            for t in TYPES}
    assign = {}
    n_type_match = 0
    for (src, dst), m in flow.items():
        for _ in range(m):
            hid = hh_q[src].pop(0)
            slot = sl_q[dst].pop(0)
            assign[hid] = slot["building_id"]
            if src == dst:
                n_type_match += 1
    # over-capacity: HH left with no slot -> over-occupy a same-type (else any) building
    n_over = 0
    leftover = [hid for t in TYPES for hid in hh_q[t]]
    if leftover:
        hh_btype = hh.set_index("household_id")["btype"]
        for hid in leftover:
            t = hh_btype.loc[hid]
            pool = slots[slots.btype == t]
            if pool.empty:
                pool = slots
            assign[hid] = pool.sort_values("size", ascending=False).iloc[0]["building_id"]
            n_over += 1
    out = pd.DataFrame({"household_id": hh["household_id"]})
    out["building_id"] = out["household_id"].map(assign)
    return out, MatchReport(n_households=n, n_type_match=n_type_match, n_overcapacity=n_over)


def _descending_queue(values: np.ndarray, positions: np.ndarray) -> deque:
    """Return source positions in pandas' descending ``sort_values`` order."""
    series = pd.Series(values[positions], index=positions)
    return deque(series.sort_values(ascending=False).index.tolist())


def match_cell_arrays(households: pd.DataFrame, slots: pd.DataFrame, rng):
    """Array-backed equivalent of :func:`match_cell` for the default-ON path.

    The flow and ordering contracts deliberately remain in the legacy functions;
    this implementation only replaces repeated per-type DataFrame construction
    and ``pop(0)`` list shifting with indexed arrays and deques.
    """
    hh = households.reset_index(drop=True)
    n = len(hh)
    if slots is None or slots.empty:
        return pd.DataFrame({"household_id": hh["household_id"], "building_id": pd.NA}), \
            MatchReport(n_households=n, n_type_match=0, n_overcapacity=n)

    slots = slots.reset_index(drop=True)
    hh_types = hh["btype"].to_numpy()
    hh_ids = hh["household_id"].to_numpy()
    # ``to_dict('records')`` in the legacy code materializes Python scalars;
    # retain that observable dtype behaviour for the ``map`` result below.
    slot_buildings = slots["building_id"].tolist()
    hh_sizes = hh["household_size"].to_numpy()
    slot_sizes = slots["size"].to_numpy()

    # Series.eq mirrors the legacy pandas filters for nullable/object labels;
    # direct NumPy object comparison can collapse to a scalar in the presence
    # of pd.NA and would silently exclude every known type.
    hh_positions = {
        kind: np.flatnonzero(
            hh["btype"].eq(kind).fillna(False).to_numpy(dtype=bool)
        )
        for kind in TYPES
    }
    slot_positions = {
        kind: np.flatnonzero(
            slots["btype"].eq(kind).fillna(False).to_numpy(dtype=bool)
        )
        for kind in TYPES
    }
    flow = solve_type_flow(
        {kind: len(hh_positions[kind]) for kind in TYPES},
        {kind: len(slot_positions[kind]) for kind in TYPES},
    )
    hh_queues = {
        kind: _descending_queue(hh_sizes, hh_positions[kind]) for kind in TYPES
    }
    slot_queues = {
        kind: _descending_queue(slot_sizes, slot_positions[kind]) for kind in TYPES
    }
    assigned = {}
    n_type_match = 0
    for (source, destination), amount in flow.items():
        for _ in range(amount):
            household_position = hh_queues[source].popleft()
            slot_position = slot_queues[destination].popleft()
            assigned[hh_ids[household_position]] = slot_buildings[slot_position]
            if source == destination:
                n_type_match += 1

    n_overcapacity = 0
    leftover_positions = [
        position for kind in TYPES for position in hh_queues[kind]
    ]
    if leftover_positions:
        largest_slot_positions = {
            kind: _descending_queue(slot_sizes, slot_positions[kind]) for kind in TYPES
        }
        any_largest_slot = _descending_queue(slot_sizes, np.arange(len(slots)))
        for household_position in leftover_positions:
            household_type = hh_types[household_position]
            candidates = largest_slot_positions[household_type]
            slot_position = candidates[0] if candidates else any_largest_slot[0]
            assigned[hh_ids[household_position]] = slot_buildings[slot_position]
            n_overcapacity += 1

    out = pd.DataFrame({"household_id": hh["household_id"]})
    out["building_id"] = out["household_id"].map(assigned)
    return out, MatchReport(
        n_households=n,
        n_type_match=n_type_match,
        n_overcapacity=n_overcapacity,
    )



def random_point_in_cell(cell_id: str, rng) -> Point:
    res, north, east = parse_inspire_id(cell_id)   # EPSG:3035 SW corner, res metres
    x = east + float(rng.uniform(0, res))
    y = north + float(rng.uniform(0, res))
    p3035 = gpd.GeoSeries([Point(x, y)], crs="EPSG:3035")
    return p3035.to_crs("EPSG:25832").iloc[0]
