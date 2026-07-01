"""TAZ-keyed calibration helpers (matsim-free) for the commute friction re-fit.

Builds the TAZ calibration layer from cached population-level stages and performs
the TAZ-keyed work-location assignment + per-RS7 straight-line distance measurement.
Mirrors the TAZ pass of braunschweig.gravity.model but as pure functions so the
Furness loop in scripts/calibrate_gravity_distribution.py can iterate friction
factors on the TAZ zone system. Only committed MiD P13 references are used
downstream; this module does not read any reference values.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.gravity.distance_matrix_taz import taz_distance_matrix
from braunschweig.gravity.taz_margins import (
    assign_taz,
    build_dest_attraction_per_taz,
    build_origin_population_per_taz,
    taz_to_kreis_lookup,
)

logger = logging.getLogger(__name__)


def build_taz_calibration_inputs(df_taz, df_homes, df_population, df_employees,
                                 df_buildings, df_municipalities):
    """Assemble the TAZ calibration layer. See module/Task-1 interface for keys.

    All spatial inputs must be in EPSG:25832. Per-commune population and employee
    totals are conserved across their TAZ (the margin builders enforce this).

    Parameters
    ----------
    df_taz : GeoDataFrame
        TAZ polygons with columns [taz_id, commune_id (AGS-8), kreis (5-ARS),
        regiostar7 (int), geometry], EPSG:25832.
    df_homes : GeoDataFrame
        Home locations with columns [household_id, commune_id (12-ARS), geometry].
    df_population : DataFrame
        Census population with columns [commune_id (12-ARS), weight].
    df_employees : DataFrame
        Employment totals with columns [commune_id (12-ARS), weight].
    df_buildings : GeoDataFrame
        Building footprints with columns [commune_id (AGS-8), potential_work, geometry].
    df_municipalities : GeoDataFrame
        Census municipality polygons with columns [commune_id (12-ARS), geometry].

    Returns
    -------
    dict with keys:
        zones (list[str])              : sorted taz_id values.
        df_dist_taz (DataFrame)        : [origin_id, destination_id, distance_km].
        df_pop_taz (DataFrame)         : [origin_id, population].
        df_emp_taz (DataFrame)         : [destination_id, employees].
        rs7_by_zone (dict[str,int])    : taz_id -> RegioStar-7 class.
        zone_to_kreis (dict[str,str])  : taz_id -> 5-digit Kreis ARS.
        home_taz (DataFrame)           : [household_id, taz_id, commune_id, x_m, y_m].
    """
    zones = sorted(df_taz["taz_id"].astype(str).tolist())

    df_dist_taz = taz_distance_matrix(df_taz)

    pop_taz, pop_primary, pop_fallback = build_origin_population_per_taz(
        df_homes, df_population, df_taz)
    att_taz, att_primary, att_fallback = build_dest_attraction_per_taz(
        df_buildings, df_employees, df_taz, df_municipalities)
    logger.info(
        "[commute-taz] origin-margin homes primary %d / fallback %d; "
        "dest-margin buildings primary %d / fallback %d",
        pop_primary, pop_fallback, att_primary, att_fallback,
    )

    df_pop_taz = (pop_taz.rename(columns={"taz_id": "origin_id", "population": "population"})
                  [["origin_id", "population"]].copy())
    df_emp_taz = (att_taz.rename(columns={"taz_id": "destination_id", "attraction": "employees"})
                  [["destination_id", "employees"]].copy())

    rs7_by_zone = dict(zip(df_taz["taz_id"].astype(str), df_taz["regiostar7"].astype(int)))
    zone_to_kreis = taz_to_kreis_lookup(df_taz)

    # Assign each home point to its TAZ (Kreis-constrained), keep home coordinates.
    # The Kreis is derived from the 12-digit ARS commune_id (first 5 digits), which
    # is identical in ARS-12 and AGS-8 so it reliably constrains the nearest-TAZ
    # fallback to the correct Kreis.
    homes = df_homes.copy()
    homes["_kreis"] = homes["commune_id"].astype(str).str[:5]
    assigned, home_primary, home_fallback = assign_taz(
        homes, df_taz, id_column="household_id", kreis_column="_kreis")
    logger.info("[commute-taz] home->TAZ primary %d / fallback %d", home_primary, home_fallback)
    # Extract x/y coordinates from the original df_homes geometry using the
    # original index order; merge by household_id keeps everything aligned.
    coords = pd.DataFrame({
        "household_id": df_homes["household_id"].values,
        "x_m": df_homes.geometry.x.values,
        "y_m": df_homes.geometry.y.values,
    })
    home_taz = assigned.merge(coords, on="household_id", how="left")

    return {
        "zones": zones,
        "df_dist_taz": df_dist_taz,
        "df_pop_taz": df_pop_taz,
        "df_emp_taz": df_emp_taz,
        "rs7_by_zone": rs7_by_zone,
        "zone_to_kreis": zone_to_kreis,
        "home_taz": home_taz,
    }


def assign_and_measure_taz(od_matrix, zones, home_taz, work_by_taz, rs7_by_zone,
                           random_seed):
    """Assign work TAZ from the OD matrix and measure realised straight-line km.

    Mirrors assign_and_measure (Gemeinde) but keyed on taz_id. Workers whose drawn
    destination TAZ has no work locations are skipped (dropped, counted, not
    reassigned) -- reported as a rate by the caller (CLAUDE.md no-silent-fallback).
    km_by_kreis keys are the home Kreis (first 5 chars of the home TAZ's Kreis via
    rs7 is not enough -> use the taz_id's Kreis); km_by_rs7 keys are int RS7.

    Parameters
    ----------
    od_matrix : np.ndarray
        NxN row-normalised OD probability matrix. Rows correspond to origin zones,
        columns to destination zones, in the same order as `zones`.
    zones : list[str]
        Ordered list of taz_id values matching the OD matrix axes.
    home_taz : DataFrame
        Output of build_taz_calibration_inputs['home_taz']: columns
        [household_id, taz_id, x_m, y_m] (optionally also 'kreis').
    work_by_taz : dict[str, tuple(np.ndarray Nx2, np.ndarray N)]
        Pre-built work-location lookup. Keys are taz_id strings. Each value is a
        tuple (xy, weights) where xy has shape (K, 2) with (x_m, y_m) columns and
        weights is a length-K probability vector (need not sum to 1 -- normalised
        internally).
    rs7_by_zone : dict[str, int]
        taz_id -> RegioStar-7 class. Used to group distances by RS7 type.
    random_seed : int
        Seed for the NumPy RandomState so the assignment is reproducible.

    Returns
    -------
    km_by_kreis : dict[str, np.ndarray]
        Home-Kreis -> array of straight-line distances in km. Only populated when
        home_taz contains a 'kreis' column; otherwise an empty dict.
    km_by_rs7 : dict[int, np.ndarray]
        RS7 class -> array of straight-line distances in km.
    skip_rate : float
        Fraction of workers dropped because their drawn destination TAZ had no work
        locations or was not present in the zone index. Logged at INFO level.
    """
    rng = np.random.RandomState(random_seed)
    zone_index = {z: i for i, z in enumerate(zones)}
    km_by_kreis: dict[str, list] = {}
    km_by_rs7: dict[int, list] = {}
    n_skipped = 0
    n_total = len(home_taz)
    for _, row in home_taz.iterrows():
        origin_taz = str(row["taz_id"])
        if origin_taz not in zone_index:
            n_skipped += 1
            continue
        od_row = od_matrix[zone_index[origin_taz], :]
        s = od_row.sum()
        if s <= 0:
            n_skipped += 1
            continue
        dest_taz = zones[int(rng.choice(len(zones), p=od_row / s))]
        if dest_taz not in work_by_taz or len(work_by_taz[dest_taz][0]) == 0:
            n_skipped += 1
            continue
        xy, w = work_by_taz[dest_taz]
        wx, wy = xy[int(rng.choice(len(xy), p=w / w.sum()))]
        d_km = float(np.hypot(wx - float(row["x_m"]), wy - float(row["y_m"])) / 1000.0)
        home_rs7 = int(rs7_by_zone.get(origin_taz, -1))
        if home_rs7 > 0:
            km_by_rs7.setdefault(home_rs7, []).append(d_km)
        # Kreis key: only populated when the caller provides a 'kreis' column in home_taz.
        if "kreis" in home_taz.columns:
            km_by_kreis.setdefault(str(row["kreis"]), []).append(d_km)
    skip_rate = (n_skipped / n_total) if n_total else 0.0
    logger.info(
        "[assign-taz] n_total=%d skip=%d (%.1f%%) rs7_groups=%s",
        n_total, n_skipped, skip_rate * 100.0, sorted(km_by_rs7.keys()),
    )
    return (
        {k: np.array(v) for k, v in km_by_kreis.items()},
        {k: np.array(v) for k, v in km_by_rs7.items()},
        skip_rate,
    )


def build_work_by_taz(df_work_taz):
    """Build a per-TAZ work-location lookup from a work-locations GeoDataFrame tagged with taz_id.

    For each TAZ, collects the (x, y) coordinates of all work locations and their
    weights. The 'employees' column is used as weight when present and positive;
    otherwise uniform weights are used. The weight vector sums to 1.0 so
    assign_and_measure_taz's internal ``w / w.sum()`` normalisation never hits 0/0.

    Parameters
    ----------
    df_work_taz : GeoDataFrame
        Work-location candidates already tagged with 'taz_id' (string). Must carry
        a metric geometry column (EPSG:25832). Optionally carries an 'employees'
        column used as sampling weight.

    Returns
    -------
    dict[str, tuple(np.ndarray, np.ndarray)]
        Keys are taz_id strings. Each value is ``(xy, w)`` where ``xy`` has shape
        ``(K, 2)`` with columns (x_m, y_m) in metres and ``w`` is a length-K
        probability vector summing to 1.0.
    """
    out = {}
    for taz_id, grp in df_work_taz.groupby("taz_id"):
        xy = np.column_stack([grp.geometry.x.values, grp.geometry.y.values])
        if "employees" in grp.columns and float(grp["employees"].sum()) > 0:
            w = grp["employees"].to_numpy(dtype=float)
            w = w / w.sum()
        else:
            w = np.ones(len(grp)) / len(grp)
        out[str(taz_id)] = (xy, w)
    return out
