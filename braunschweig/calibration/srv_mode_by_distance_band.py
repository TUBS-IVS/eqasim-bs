"""Extraction-time builder of the SrV 2023 mode shares by distance band and purpose group (eqasim-bs#453, ADR-0145).

The committed table answers one question for the synthesis: how often is a trip of this length, for this
purpose, made with this mode in the region. The stage ``braunschweig.synthesis.outside_tour_modes`` reads it
(through :mod:`braunschweig.calibration.srv_mode_by_distance_band_table`) to judge the donor mode of the tours
the discrete mode choice never re-chooses (tours touching an ``outside`` activity) and to draw a substitute.

Conventions (shared with :mod:`braunschweig.calibration.srv_distance_targets`, ADR-0102): unit = trip,
weight = ``GEWICHT_W_ZENSUS`` (rows with a negative weight dropped and counted), distance = ``GIS_LAENGE``
(routed km) where ``GIS_LAENGE_GUELTIG > 0`` (invalid rows dropped and counted), straight-line equivalent =
routed / ``detour_factor`` (1.3, the pipeline's ``ROUTED_DETOUR_FACTOR``), mode = ``E_HVM_5`` (1 walk,
2 bicycle, 3 car, 4 car_passenger, 5 pt; other codes dropped and counted). Purpose group from the destination
purpose ``V_ZWECK`` through ``srv_plan_structure.PURPOSE_BY_V_ZWECK`` (work, education, everything else other);
a trip to home takes the group of its origin purpose ``E_START_ZWECK`` (a return from work is a work trip);
unknown purposes and home -> home trips are dropped and counted. The exclusion counts are per criterion and
may overlap. Bands in straight-line km; a band with fewer than ``MIN_TRIPS_PER_BAND`` unweighted trips is merged
into the band below, from the top band downwards; the bottom band (0-5 km) is never merged into, because walk is
plausible there and nowhere else, so a thin band directly above it stays and is reported. A purpose group
without trips, or a band left without trips, raises: the table could not judge distances there. The group
``all`` pools the three purpose groups.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_mode_by_distance_band_table import (  # noqa: F401  (re-exported)
    ALL_GROUP, BAND_EDGES_KM, MODES, PURPOSE_GROUPS, TABLE_COLUMNS, TABLE_FILE_NAME, band_label, load_table)
from braunschweig.calibration.srv_plan_structure import PURPOSE_BY_V_ZWECK

MODE_BY_E_HVM_5 = {1: "walk", 2: "bicycle", 3: "car", 4: "car_passenger", 5: "pt"}
DEFAULT_DETOUR_FACTOR = 1.3
MIN_TRIPS_PER_BAND = 50
HOME_PURPOSE = "home"
_GROUP_BY_PURPOSE = {"work": "work", "education": "education", "shop": "other", "leisure": "other",
                     "escort": "other", "other": "other"}
REQUIRED_TRIP_COLUMNS = ["V_ZWECK", "E_START_ZWECK", "E_HVM_5", "GIS_LAENGE", "GIS_LAENGE_GUELTIG",
                         "GEWICHT_W_ZENSUS"]


def purpose_group_of(v_zweck: pd.Series, e_start_zweck: pd.Series) -> pd.Series:
    """Purpose group per trip; ``None`` for unknown codes and for home -> home trips."""
    destination = pd.to_numeric(v_zweck, errors="coerce").map(PURPOSE_BY_V_ZWECK)
    origin = pd.to_numeric(e_start_zweck, errors="coerce").map(PURPOSE_BY_V_ZWECK)
    group = destination.map(_GROUP_BY_PURPOSE)
    home_bound = destination.eq(HOME_PURPOSE)
    group = group.where(~home_bound, origin.map(_GROUP_BY_PURPOSE))
    return group.astype(object).where(group.notna(), None)


def select_trips(trips: pd.DataFrame) -> tuple:
    """The weighted, GIS-valid, mode-known trips of the three purpose groups, with exclusion counts."""
    missing = [column for column in REQUIRED_TRIP_COLUMNS if column not in trips.columns]
    if missing:
        raise ValueError(f"[srv_mode_by_distance_band] trips frame lacks the columns {missing}")
    gis_valid = pd.to_numeric(trips["GIS_LAENGE_GUELTIG"], errors="coerce").fillna(0) > 0
    weight_ok = pd.to_numeric(trips["GEWICHT_W_ZENSUS"], errors="coerce").fillna(-1) >= 0
    mode_known = pd.to_numeric(trips["E_HVM_5"], errors="coerce").isin(list(MODE_BY_E_HVM_5))
    group = purpose_group_of(trips["V_ZWECK"], trips["E_START_ZWECK"])
    purpose_ok = group.notna()
    keep = gis_valid & weight_ok & mode_known & purpose_ok
    log = {"n_trips": int(len(trips)), "n_gis_invalid": int((~gis_valid).sum()),
           "n_weight_negative": int((~weight_ok).sum()), "n_mode_unknown": int((~mode_known).sum()),
           "n_purpose_excluded": int((~purpose_ok).sum()), "n_selected": int(keep.sum())}
    selected = trips.loc[keep].copy()
    selected["mode"] = pd.to_numeric(selected["E_HVM_5"]).map(MODE_BY_E_HVM_5)
    selected["purpose_group"] = group.loc[keep].to_numpy()
    selected["weight"] = pd.to_numeric(selected["GEWICHT_W_ZENSUS"]).astype(float)
    selected["routed_km"] = pd.to_numeric(selected["GIS_LAENGE"]).astype(float)
    return selected, log


def pool_thin_bands(counts: list, edges: tuple, min_trips: int) -> list:
    """Merge thin bands into the band below, from the top downwards; the bottom band is never merged into."""
    bands = [[edges[i], edges[i + 1], int(counts[i])] for i in range(len(counts))]
    index = len(bands) - 1
    # The bottom band (index 0) is never merged INTO, so the loop stops at index 1: a band that is still
    # thin at index 1 stays and is reported by the caller as a thin band kept.
    while index > 1:
        if bands[index][2] < min_trips:
            bands[index - 1] = [bands[index - 1][0], bands[index][1], bands[index - 1][2] + bands[index][2]]
            del bands[index]
        index -= 1
    return [(lower, upper) for lower, upper, _ in bands]


def build_table(trips: pd.DataFrame, *, detour_factor: float = DEFAULT_DETOUR_FACTOR,
                min_trips_per_band: int = MIN_TRIPS_PER_BAND) -> tuple:
    """The table (one row per group x band x mode, all five modes) and a log of selection and pooling."""
    if not detour_factor > 0:
        raise ValueError(f"detour_factor must be > 0, got {detour_factor!r}")
    selected, log = select_trips(trips)
    selected["km"] = selected["routed_km"] / float(detour_factor)
    log.update({"pooled_bands": {}, "thin_bands_kept": {}, "bands": {}})
    original = [(BAND_EDGES_KM[i], BAND_EDGES_KM[i + 1]) for i in range(len(BAND_EDGES_KM) - 1)]
    rows = []
    for group in PURPOSE_GROUPS + (ALL_GROUP,):
        subset = selected if group == ALL_GROUP else selected[selected["purpose_group"] == group]
        if subset.empty:
            raise ValueError(f"[srv_mode_by_distance_band] purpose group {group!r} has no selected trips")
        km = subset["km"].to_numpy()
        counts = [int(((km >= lower) & (km < upper)).sum()) for lower, upper in original]
        bands = pool_thin_bands(counts, BAND_EDGES_KM, min_trips_per_band)
        log["pooled_bands"][group] = [band_label(*band) for band in bands if band not in original]
        log["bands"][group] = [band_label(*band) for band in bands]
        log["thin_bands_kept"][group] = []
        for lower, upper in bands:
            label = band_label(lower, upper)
            cell = subset[(subset["km"] >= lower) & (subset["km"] < upper)]
            n_band = int(len(cell))
            if n_band == 0:
                raise ValueError(f"[srv_mode_by_distance_band] band {label} of purpose group {group!r} has no trips "
                                 "after pooling; the table cannot judge distances there")
            if n_band < min_trips_per_band:
                log["thin_bands_kept"][group].append(label)
            total_weight = float(cell["weight"].sum())
            for mode in MODES:
                in_mode = cell[cell["mode"] == mode]
                share = float(in_mode["weight"].sum()) / total_weight if total_weight > 0 else 0.0
                rows.append({"purpose_group": group, "band": label, "band_lower_km": float(lower),
                             "band_upper_km": float(upper), "mode": mode, "share": share, "n_band": n_band,
                             "n_cell": int(len(in_mode))})
    return pd.DataFrame(rows, columns=TABLE_COLUMNS), log
