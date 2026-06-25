"""MiD 2023 distribution targets for the calibration corner.

Loaders return, per geographic key, the band-share vector aligned to
braunschweig.gravity.friction.BAND_EDGES_KM. Only committed MiD cells are read; the
non-distance columns are excluded and the remainder renormalised to sum to 1 (no
invented values). The shares are MiD's native (routed) distribution; the calibration
puts the euclidean model output on the same axis via metrics.apply_detour, so the
committed shares are used unchanged.
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

# P13 distance columns mapped to the 7 BAND_EDGES_KM bands. d_0 + d_0_5 -> band 0.
_P13_BAND_COLUMNS = [
    ["d_0", "d_0_5"],   # [0,5)
    ["d_5_10"],         # [5,10)
    ["d_10_20"],        # [10,20)
    ["d_20_30"],        # [20,30)
    ["d_30_50"],        # [30,50)
    ["d_50_100"],       # [50,100)
    ["d_100p"],         # [100,inf)
]


def load_p13_band_shares(mid_dir):
    """Commute-distance band shares per residence Kreis ars5 (+ '03ZGB')."""
    path = os.path.join(mid_dir, "mid2023_P13.csv")
    df = pd.read_csv(path, comment="#")
    out = {}
    for _, row in df.iterrows():
        ars5 = str(row["ars5"])
        bands = np.array([
            float(sum(row[c] for c in cols)) for cols in _P13_BAND_COLUMNS
        ], dtype=float)
        total = bands.sum()
        if total <= 0:
            continue
        out[ars5] = bands / total
    return out
