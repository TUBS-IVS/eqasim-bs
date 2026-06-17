# braunschweig/synthesis/locations/home_matcher.py
"""Lexicographic per-cell home matcher: type (primary) then size (secondary)."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np, pandas as pd
from shapely.geometry import Point

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
