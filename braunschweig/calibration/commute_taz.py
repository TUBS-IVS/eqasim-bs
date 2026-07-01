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
