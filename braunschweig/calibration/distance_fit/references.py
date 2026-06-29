"""Committed MiD reference adapters with independence tags for the distance-fit diagnostic.

Each adapter returns (targets_by_key, band_edges_or_None, reference_tag). Tags:
  out_of_sample      -> genuine independent comparison (validation)
  in_sample          -> the reference tuned the model (calibration residual)
  input_reproduction -> the reference IS the model input (reproduction check)
No invented values: every adapter reads a committed CSV under mid_dir.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.calibration.targets import (
    load_p13_band_shares, load_p13_band_shares_by_rs7,
)
from braunschweig.gravity.friction import BAND_EDGES_KM

logger = logging.getLogger(__name__)

W12_PURPOSE_MAP = {
    "Einkauf": "shop", "Freizeit": "leisure", "Erledigung": "other",
    "Arbeit": "work", "Ausbildung": "education", "dienstlich": "work",
}
_W12_COLS = [
    ("d_unter_0_5km", 0.0, 0.5), ("d_0_5_1km", 0.5, 1.0), ("d_1_2km", 1.0, 2.0),
    ("d_2_5km", 2.0, 5.0), ("d_5_10km", 5.0, 10.0), ("d_10_20km", 10.0, 20.0),
    ("d_20_50km", 20.0, 50.0), ("d_50_100km", 50.0, 100.0), ("d_100km_plus", 100.0, np.inf),
]
_T43_AGE_COLS = ["km_0_6", "km_7_10", "km_11_13", "km_14_17"]

# P38.2 distance band columns and their edges in km.
# Source: MiD 2023 Tabelle A P38.2, columns:
#   d_unter_5km, d_5_10km, d_10_20km, d_20_30km, d_30_50km,
#   d_50_100km, d_100_200km, d_200_300km, d_300km_plus
# The d_unplausibel_keine_angabe and mittel_km columns are excluded from
# normalisation so shares reflect the distance-class distribution only.
_P38_2_BAND_COLS = [
    "d_unter_5km",
    "d_5_10km",
    "d_10_20km",
    "d_20_30km",
    "d_30_50km",
    "d_50_100km",
    "d_100_200km",
    "d_200_300km",
    "d_300km_plus",
]
# Corresponding band edges in km: 9 bands -> 10 edges.
_P38_2_BAND_EDGES_KM = [0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 100.0, 200.0, 300.0, float("inf")]


def work_p13(mid_dir):
    """Per-Kreis commute-distance band shares from MiD P13 (ars5 keys).

    Returns (targets, BAND_EDGES_KM list, 'out_of_sample').
    """
    return load_p13_band_shares(mid_dir), list(BAND_EDGES_KM), "out_of_sample"


def work_p13_rs7(mid_dir):
    """Per-RS7 commute-distance band shares from MiD P13 Raumtyp block (int keys).

    Returns (targets, BAND_EDGES_KM list, 'out_of_sample').
    """
    return load_p13_band_shares_by_rs7(mid_dir), list(BAND_EDGES_KM), "out_of_sample"


def work_p38_2(mid_dir):
    """Per-Kreis commute-distance band shares from MiD P38.2 (region-name keys).

    Source: mid2023_P38_2_commute_distance_by_kreis.csv.
    Header columns: region, d_unter_5km, d_5_10km, d_10_20km, d_20_30km, d_30_50km,
        d_50_100km, d_100_200km, d_200_300km, d_300km_plus, d_unplausibel_keine_angabe,
        mittel_km.
    Values are row-percentages; d_unplausibel_keine_angabe and mittel_km are excluded
    before normalisation so shares reflect the 9 distance bands only. The
    d_unplausibel_keine_angabe column is excluded and each Kreis row is renormalised
    to sum to 1 over the 9 valid distance bands, i.e. implausible/missing distances
    are redistributed proportionally across the valid bands (the same convention as the
    P13 reference).
    Aggregate rows (region lowercased in {"gesamt", "total", "insgesamt"}) are skipped;
    the returned dict contains only actual Kreis/Stadt rows.
    Band edges: [0, 5, 10, 20, 30, 50, 100, 200, 300, inf) km.
    Returns (targets, _P38_2_BAND_EDGES_KM, 'out_of_sample').
    """
    path = os.path.join(mid_dir, "mid2023_P38_2_commute_distance_by_kreis.csv")
    df = pd.read_csv(path, comment="#")
    targets = {}
    n_primary = 0
    n_skip = 0
    _AGGREGATE_LABELS = {"gesamt", "total", "insgesamt"}
    for _, row in df.iterrows():
        region = str(row["region"]).strip()
        if region.lower() in _AGGREGATE_LABELS:
            logger.debug("[distance-fit] P38.2: skipping aggregate row '%s'.", region)
            continue
        band_values = np.array([float(row[c]) for c in _P38_2_BAND_COLS], dtype=float)
        total = band_values.sum()
        if total <= 0:
            logger.warning(
                "[distance-fit] P38.2 row '%s' sums to 0 across distance bands; skipped.",
                region,
            )
            n_skip += 1
            continue
        targets[region] = band_values / total
        n_primary += 1
    logger.info(
        "[distance-fit] P38.2: loaded %d rows, skipped %d (zero distance total).",
        n_primary, n_skip,
    )
    return targets, list(_P38_2_BAND_EDGES_KM), "out_of_sample"


def education_t43(mid_dir):
    """Mean school-trip distances (km) per RS7 x age band from MiD T43 (str keys).

    Keys are '{regiostar7}|{age_col}', e.g. '72|km_0_6'. Values are mean km
    (scalar float, not a distribution). Returns (targets, None, 'in_sample').
    """
    path = os.path.join(mid_dir, "mid2023_T43_school_distance_by_rs7.csv")
    df = pd.read_csv(path, comment="#")
    targets = {}
    for _, row in df.iterrows():
        rs7 = int(row["regiostar7"])
        for col in _T43_AGE_COLS:
            targets[f"{rs7}|{col}"] = float(row[col])
    logger.info("[distance-fit] T43: loaded %d (rs7 x age-band) mean-distance targets.", len(targets))
    return targets, None, "in_sample"


def secondary_w12(mid_dir):
    """Per-purpose secondary-trip band shares from MiD W12 (purpose-name keys).

    Maps MiD Hauptwegezweck labels to model purpose names via W12_PURPOSE_MAP.
    When a label is absent from the map, the row is skipped and a warning is logged.
    When a purpose would be overwritten (duplicate mapping), the first occurrence is kept.
    Values are normalised to sum to 1 across the 9 W12 distance bands.
    Returns (targets, band_edges, 'input_reproduction').
    """
    path = os.path.join(mid_dir, "mid2023_W12_triplength_by_purpose.csv")
    df = pd.read_csv(path, comment="#")
    edges = [lo for _, lo, _ in _W12_COLS] + [_W12_COLS[-1][2]]
    cols = [c for c, _, _ in _W12_COLS]
    targets = {}
    n_primary = 0
    n_skip_unmapped = 0
    n_skip_zero = 0
    for _, row in df.iterrows():
        raw = str(row["hauptwegezweck"]).strip()
        purpose = W12_PURPOSE_MAP.get(raw)
        if purpose is None:
            logger.warning("[distance-fit] W12 row '%s' not in W12_PURPOSE_MAP; skipped.", raw)
            n_skip_unmapped += 1
            continue
        shares = np.array([float(row[c]) for c in cols], dtype=float)
        total = shares.sum()
        if total <= 0:
            logger.warning("[distance-fit] W12 row '%s' sums to 0; skipped.", raw)
            n_skip_zero += 1
            continue
        if purpose in targets:
            logger.info("[distance-fit] W12: '%s' maps to already-populated '%s'; keeping first.",
                        raw, purpose)
            continue
        targets[purpose] = shares / total
        n_primary += 1
    logger.info(
        "[distance-fit] W12: primary %d/%d rows (%.1f%%), skipped unmapped %d, zero %d.",
        n_primary, n_primary + n_skip_unmapped + n_skip_zero,
        100.0 * n_primary / max(1, n_primary + n_skip_unmapped + n_skip_zero),
        n_skip_unmapped, n_skip_zero,
    )
    return targets, edges, "input_reproduction"
