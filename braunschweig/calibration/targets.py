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
    # dtype=str at READ time: int64 inference would strip the ars5 leading zero
    # irreversibly ("03101" -> 3101); today only the "03ZGB" Gesamt row forces
    # object dtype by accident (same latent trap fixed in data.mid.references).
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
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


# ---------------------------------------------------------------------------
# W12 secondary-trip distance targets
# ---------------------------------------------------------------------------

# W12 distance band edges in km: 9 bands aligned to MiD 2023 Tabelle A W12.
# Source columns: d_unter_0_5km, d_0_5_1km, d_1_2km, d_2_5km, d_5_10km,
#                 d_10_20km, d_20_50km, d_50_100km, d_100km_plus.
W12_BAND_EDGES_KM = (0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, float("inf"))

# W12 CSV column -> band index (columns in order, band 0..8).
_W12_BAND_COLUMNS = [
    "d_unter_0_5km",
    "d_0_5_1km",
    "d_1_2km",
    "d_2_5km",
    "d_5_10km",
    "d_10_20km",
    "d_20_50km",
    "d_50_100km",
    "d_100km_plus",
]

# Map model secondary purpose names to W12 hauptwegezweck labels.
W12_PURPOSE_MAP = {
    "shop": "Einkauf",
    "leisure": "Freizeit",
    "other": "Erledigung",
}


def _w12_row_to_band_shares(row):
    """Convert one W12 CSV row to a length-9 band-share array summing to 1.

    The columns are integer percent values; renormalised to sum to 1 so minor
    rounding in the CSV does not bias the EMD. Returns None when the total is
    zero (row cannot be used as a target).
    """
    bands = np.array(
        [float(row[c]) for c in _W12_BAND_COLUMNS],
        dtype=float,
    )
    total = bands.sum()
    return (bands / total) if total > 0 else None


def load_w12_band_shares(mid_dir):
    """Secondary trip-length band shares per purpose from MiD 2023 Tabelle A W12.

    Source: mid2023_W12_triplength_by_purpose.csv (committed under mid_dir).
    Purposes Einkauf / Freizeit / Erledigung map to model purposes shop / leisure / other.

    Returns a dict keyed by model purpose name:
        {
            "shop":    length-9 ndarray summing to 1 (W12_BAND_EDGES_KM),
            "leisure": ...,
            "other":   ...,
        }
    Also includes the raw W12 arithmetic mean_km for each purpose:
        {"shop_mean_km": float, "leisure_mean_km": float, "other_mean_km": float}

    Raises FileNotFoundError when the CSV is absent (fail-fast, no silent fallback).
    """
    path = os.path.join(mid_dir, "mid2023_W12_triplength_by_purpose.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"W12 CSV not found at '{path}'. "
            "Ensure mid-dir points to the correct MiD data directory."
        )
    df = pd.read_csv(path, comment="#")
    # Index by hauptwegezweck
    df = df.set_index("hauptwegezweck")

    reverse_map = {v: k for k, v in W12_PURPOSE_MAP.items()}
    out = {}
    for w12_label, model_purpose in reverse_map.items():
        if w12_label not in df.index:
            raise KeyError(
                f"W12 CSV does not contain purpose row '{w12_label}'. "
                f"Available rows: {list(df.index)}"
            )
        row = df.loc[w12_label]
        shares = _w12_row_to_band_shares(row)
        if shares is None:
            raise ValueError(
                f"W12 CSV row '{w12_label}' has zero total distance-band share. "
                "Check the CSV content."
            )
        out[model_purpose] = shares
        out[f"{model_purpose}_mean_km"] = float(row["mittel_km"])
    return out
