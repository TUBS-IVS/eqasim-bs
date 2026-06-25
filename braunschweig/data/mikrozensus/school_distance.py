"""Destatis Mikrozensus 2024 school-trip distance distribution by school type.

Source: Destatis, "Schueler/innen und Studierende nach Entfernung, Zeitaufwand
und benutztem Verkehrsmittel fuer den Hinweg zur Schule/Hochschule", Endergebnis
Mikrozensus 2024 (Anteile in %). National. Used to benchmark the BBS (berufsbildende
Schulen) education level, whose regional catchment MiD Tabelle 43 does not isolate.
"""
from __future__ import annotations

import os

import pandas as pd

from braunschweig.calibration import circuity

# distance-band midpoints (km); the open 50+ band is assumed at 65 km
# (midpoint of the [50, 80) interval, consistent with Destatis rounding the
# distribution to integer percent; see test_banded_mean_km_matches_hand_calc).
BAND_MIDPOINTS_KM = (2.5, 7.5, 17.5, 37.5, 65.0)
_BAND_COLS = ["lt5", "b5_10", "b10_25", "b25_50", "ge50"]


def banded_mean_km(shares_percent):
    """Mean routed km from a 5-band share vector (percentages summing to ~100)."""
    total = sum(shares_percent)
    return sum(s * m for s, m in zip(shares_percent, BAND_MIDPOINTS_KM)) / total


def bbs_target_km(raw, detour_factor=None, network="car", mode="curve"):
    """Straight-line BBS target from the routed banded mean (car network).

    Legacy path: pass ``detour_factor`` -> ``routed / detour_factor`` (back-compat).
    Tier 3C path (default): invert the distance-dependent circuity curve for
    ``network`` via ``circuity.routed_to_euclidean``.
    """
    row = raw[raw["school_type"] == "berufsbildend"].iloc[0]
    routed = banded_mean_km([float(row[c]) for c in _BAND_COLS])
    if detour_factor is not None:
        return routed / float(detour_factor)
    return float(circuity.routed_to_euclidean(routed, network, mode=mode))


def hochschule_target_km(raw, detour_factor=None, network="car", mode="curve"):
    """Straight-line Hochschule target from the routed banded mean (car network).

    Legacy path: pass ``detour_factor`` -> ``routed / detour_factor`` (back-compat).
    Tier 3C path (default): invert the distance-dependent circuity curve for
    ``network`` via ``circuity.routed_to_euclidean``.
    """
    row = raw[raw["school_type"] == "hochschule"].iloc[0]
    routed = banded_mean_km([float(row[c]) for c in _BAND_COLS])
    if detour_factor is not None:
        return routed / float(detour_factor)
    return float(circuity.routed_to_euclidean(routed, network, mode=mode))


def configure(context):
    context.config("data_path")
    context.config("mikrozensus_school_distance_path",
                   "braunschweig/mikrozensus/mikrozensus2024_school_distance_by_type.csv")


def _resolve_path(context):
    return os.path.join(context.config("data_path"),
                        context.config("mikrozensus_school_distance_path"))


def execute(context):
    return pd.read_csv(_resolve_path(context))
