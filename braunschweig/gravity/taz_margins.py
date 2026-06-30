"""Pure helpers building TAZ-keyed gravity margins + a taz->kreis lookup.

REUSE: home/building -> TAZ assignment follows the
``braunschweig.data.building_potentials.assign_commune`` idiom (within primary,
nearest fallback, primary/fallback logged) but dedups on an EXPLICIT id key
(never the index) and asserts mass conservation. The destination rescale follows
``apply_sector_aware_attraction``'s commune-total-preserving renorm.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

CRS_METRIC = "EPSG:25832"


def assign_taz(points_gdf, df_taz, id_column, point_geometry=None):
    """Attach taz_id + commune_id to each row by point-in-polygon (primary) with a
    commune-constrained nearest-TAZ fallback. Dedups on ``id_column`` (NOT the
    index). Returns (gdf, primary, fallback) and asserts no row is lost."""
    if id_column not in points_gdf.columns:
        raise ValueError("assign_taz requires an explicit id column %r" % id_column)
    taz = df_taz[["taz_id", "commune_id", "geometry"]].copy()
    if taz.crs != points_gdf.crs:
        taz = taz.to_crs(points_gdf.crs)
    pts = points_gdf[[id_column]].copy()
    pts["geometry"] = (point_geometry if point_geometry is not None else points_gdf.geometry).values
    pts = gpd.GeoDataFrame(pts, geometry="geometry", crs=points_gdf.crs)

    joined = gpd.sjoin(pts, taz, how="left", predicate="within").drop(columns=["index_right"])
    joined = joined.drop_duplicates(id_column, keep="first")
    primary = int(joined["taz_id"].notna().sum())

    missing = joined[joined["taz_id"].isna()][[id_column]]
    fallback = int(len(missing))
    if fallback:
        near = gpd.sjoin_nearest(
            pts[pts[id_column].isin(missing[id_column])], taz, how="left",
        ).drop_duplicates(id_column, keep="first")[[id_column, "taz_id", "commune_id"]]
        fill = near.set_index(id_column)
        sel = joined[id_column].isin(missing[id_column])
        joined.loc[sel, "taz_id"] = joined.loc[sel, id_column].map(fill["taz_id"])
        joined.loc[sel, "commune_id"] = joined.loc[sel, id_column].map(fill["commune_id"])

    if len(joined) != len(points_gdf):
        raise ValueError(
            "assign_taz lost rows (%d in, %d out) -- duplicate %r or join blow-up"
            % (len(points_gdf), len(joined), id_column)
        )
    total = primary + fallback
    print("[taz_margins.assign] %d points; TAZ within %d (%.1f%%), nearest fallback %d (%.1f%%)"
          % (total, primary, 100.0 * primary / total if total else 0.0,
             fallback, 100.0 * fallback / total if total else 0.0))
    return joined[[id_column, "taz_id", "commune_id"]], primary, fallback


def build_origin_population_per_taz(df_homes, df_population, df_taz):
    """Re-bin the PER-PERSON census population (weight=1.0/person, keyed
    household_id) onto TAZ by the per-household home POINT (one-to-many on
    household_id). Returns (DataFrame[taz_id, commune_id, population], primary,
    fallback). The summed population equals the input population total."""
    homes = df_homes[["household_id", "geometry"]].copy()
    home_taz, primary, fallback = assign_taz(homes, df_taz, id_column="household_id")
    pop = df_population[["household_id", "weight"]]
    merged = pop.merge(home_taz[["household_id", "taz_id", "commune_id"]], on="household_id", how="left")
    if merged["taz_id"].isna().any():
        raise ValueError(
            "%d persons have a household with no home point -> no TAZ; the population "
            "and home-point producers must share household_id" % int(merged["taz_id"].isna().sum())
        )
    out = (merged.groupby(["taz_id", "commune_id"])["weight"].sum()
                 .rename("population").reset_index())
    out["taz_id"] = out["taz_id"].astype(str)
    return out, primary, fallback
