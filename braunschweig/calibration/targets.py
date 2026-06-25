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


def _p13_row_to_band_shares(row):
    """Convert one P13 CSV row to a length-7 band-share array summing to 1.

    Sums the distance columns according to _P13_BAND_COLUMNS and renormalises.
    Non-distance columns (keine_feste_arbeit, keine_angabe) are excluded before
    normalisation so the shares reflect the distance-class distribution only.
    Returns None when the distance total is zero (row cannot be used as a target).
    """
    bands = np.array(
        [float(sum(row[c] for c in cols)) for cols in _P13_BAND_COLUMNS],
        dtype=float,
    )
    total = bands.sum()
    return (bands / total) if total > 0 else None


def load_p13_band_shares(mid_dir):
    """Commute-distance band shares per residence Kreis ars5 (+ '03ZGB')."""
    path = os.path.join(mid_dir, "mid2023_P13.csv")
    df = pd.read_csv(path, comment="#")
    out = {}
    for _, row in df.iterrows():
        ars5 = str(row["ars5"])
        shares = _p13_row_to_band_shares(row)
        if shares is not None:
            out[ars5] = shares
    return out


def load_p13_band_shares_by_rs7(mid_dir):
    """Per-RS7 commute-distance band shares from the P13 Raumtyp block.

    Returns {regiostar7_int: length-7 ndarray summing to 1}. This is a REAL
    per-RS7 target from MiD 2023 Tabelle A P13 (page 77, Raumtyp block) -- not a
    ZGB aggregate proxy.  RS7 code 71 (Metropole) is absent from the ZGB sample.
    """
    path = os.path.join(mid_dir, "mid2023_P13_commute_distance_by_rs7.csv")
    df = pd.read_csv(path, comment="#")
    out = {}
    for _, row in df.iterrows():
        shares = _p13_row_to_band_shares(row)
        if shares is not None:
            out[int(row["regiostar7"])] = shares
    return out
