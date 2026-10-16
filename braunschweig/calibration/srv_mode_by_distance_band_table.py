"""Run-time side of the SrV 2023 mode-share-by-distance-band table (eqasim-bs#453, ADR-0145).

Constants, the band label and the loader of the committed ``srv2023_mode_by_distance_band.csv``. The synpp
stage ``braunschweig.synthesis.outside_tour_modes.stage`` imports ONLY this module, so its validation token
(which hashes the whole import closure) does not depend on the extraction-time builder in
:mod:`braunschweig.calibration.srv_mode_by_distance_band`. Keep this module free of first-party imports.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

MODES = ("walk", "bicycle", "car", "car_passenger", "pt")
PURPOSE_GROUPS = ("work", "education", "other")
#: Pools the three purpose groups; the cell for portal legs, whose purpose the rewritten trip table no longer carries.
ALL_GROUP = "all"
#: Straight-line km; the last band is open.
BAND_EDGES_KM = (0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 45.0, float("inf"))
TABLE_FILE_NAME = "srv2023_mode_by_distance_band.csv"
TABLE_COLUMNS = ["purpose_group", "band", "band_lower_km", "band_upper_km", "mode", "share", "n_band", "n_cell"]


def band_label(lower: float, upper: float) -> str:
    """``0_5`` for a closed band, ``20_plus`` for the open top band (km, integers)."""
    return f"{int(lower)}_plus" if np.isinf(upper) else f"{int(lower)}_{int(upper)}"


def load_table(srv_dir) -> pd.DataFrame:
    """Read the committed table (comment header lines start with ``#``) and validate the column set."""
    path = os.path.join(str(srv_dir), TABLE_FILE_NAME)
    if not os.path.exists(path):
        raise FileNotFoundError(f"[srv_mode_by_distance_band] committed table {TABLE_FILE_NAME} not found at {path}; "
                                "regenerate it with scripts/extract_srv_mode_by_distance_band.py")
    table = pd.read_csv(path, comment="#")
    missing = [column for column in TABLE_COLUMNS if column not in table.columns]
    if missing:
        raise ValueError(f"[srv_mode_by_distance_band] {TABLE_FILE_NAME} lacks the columns {missing}")
    table = table[TABLE_COLUMNS].copy()
    table["band_lower_km"] = table["band_lower_km"].astype(float)
    table["band_upper_km"] = table["band_upper_km"].astype(float)
    return table
