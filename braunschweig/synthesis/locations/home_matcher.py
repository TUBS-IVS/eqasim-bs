# braunschweig/synthesis/locations/home_matcher.py
"""Lexicographic per-cell home matcher: type (primary) then size (secondary)."""
from __future__ import annotations
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
