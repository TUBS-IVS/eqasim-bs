"""Pure helpers building TAZ-keyed gravity margins.

REUSE: home/building -> TAZ assignment follows the
``braunschweig.data.building_potentials.assign_commune`` idiom (within primary,
nearest fallback, primary/fallback logged) but dedups on an EXPLICIT id key
(never the index) and asserts mass conservation. The nearest-TAZ fallback is
CONSTRAINED to the point's own Kreis (when a kreis key is given) so no mass
crosses a Kreis boundary (the BA Pendleratlas control is Kreis-level).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)

CRS_METRIC = "EPSG:25832"


def assign_taz(points_gdf, df_taz, id_column, kreis_column=None, point_geometry=None):
    """Attach taz_id + commune_id to each point by point-in-polygon (primary).

    Points in no TAZ use a nearest-TAZ fallback; when ``kreis_column`` is given
    the nearest search is CONSTRAINED to TAZ within the point's own Kreis so no
    mass crosses a Kreis boundary (DECISION 2). A point whose Kreis has no TAZ
    raises (coverage gap; no silent cross-Kreis fallback). Dedups on
    ``id_column`` (never the index) and asserts no row is lost. ``point_geometry``
    overrides the join point (e.g. representative_point for footprints).
    Returns (gdf[id_column, taz_id, commune_id], primary, fallback).
    """
    if id_column not in points_gdf.columns:
        raise ValueError("assign_taz requires an explicit id column %r" % id_column)
    taz = df_taz[["taz_id", "commune_id", "kreis", "geometry"]].copy()
    if taz.crs != points_gdf.crs:
        taz = taz.to_crs(points_gdf.crs)
    taz_geom = taz[["taz_id", "commune_id", "geometry"]]

    keep = [id_column] + ([kreis_column] if kreis_column else [])
    pts = points_gdf[keep].copy()
    pts["geometry"] = (point_geometry if point_geometry is not None else points_gdf.geometry).values
    pts = gpd.GeoDataFrame(pts, geometry="geometry", crs=points_gdf.crs)

    join_pts = pts.drop(columns=[kreis_column]) if kreis_column else pts
    joined = gpd.sjoin(join_pts, taz_geom, how="left", predicate="within").drop(columns=["index_right"])
    joined = joined.drop_duplicates(id_column, keep="first")
    primary = int(joined["taz_id"].notna().sum())

    missing_ids = joined.loc[joined["taz_id"].isna(), id_column]
    fallback = int(len(missing_ids))
    if fallback:
        miss = pts[pts[id_column].isin(missing_ids)]
        if kreis_column is not None:
            parts = []
            for kreis, grp in miss.groupby(kreis_column):
                taz_k = taz[taz["kreis"].astype(str) == str(kreis)][["taz_id", "commune_id", "geometry"]]
                if len(taz_k) == 0:
                    raise ValueError(
                        "kreis %s has %d unplaced points but no TAZ polygon "
                        "(coverage gap; no silent cross-Kreis fallback)" % (kreis, len(grp)))
                near = gpd.sjoin_nearest(
                    grp.drop(columns=[kreis_column]), taz_k, how="left",
                ).drop_duplicates(id_column, keep="first")
                parts.append(near[[id_column, "taz_id", "commune_id"]])
            fill = pd.concat(parts).set_index(id_column)
        else:
            near = gpd.sjoin_nearest(miss, taz_geom, how="left").drop_duplicates(id_column, keep="first")
            fill = near.set_index(id_column)[["taz_id", "commune_id"]]
        sel = joined[id_column].isin(missing_ids)
        joined.loc[sel, "taz_id"] = joined.loc[sel, id_column].map(fill["taz_id"])
        joined.loc[sel, "commune_id"] = joined.loc[sel, id_column].map(fill["commune_id"])

    if len(joined) != len(points_gdf):
        raise ValueError(
            "assign_taz row count changed (%d in, %d out); duplicate %r or join blow-up"
            % (len(points_gdf), len(joined), id_column))
    total = primary + fallback
    logger.info(
        "[taz_margins.assign] %d points; within %d (%.1f%%), nearest fallback %d (%.1f%%)",
        total, primary, 100.0 * primary / total if total else 0.0,
        fallback, 100.0 * fallback / total if total else 0.0)
    return joined[[id_column, "taz_id", "commune_id"]], primary, fallback


def normalize_commune_to_ars(commune_id_series, ags_to_ars):
    """Map an 8-digit AGS commune_id to the 12-digit ARS used by the population /
    employees frames (the same crosswalk braunschweig.data.census.employees uses).
    Raise on any unmapped AGS (no silent miss -> no all-zero attraction, B2)."""
    out = commune_id_series.astype(str).map(ags_to_ars)
    if out.isna().any():
        missing = sorted(commune_id_series[out.isna()].astype(str).unique())[:5]
        raise ValueError("%d commune_id (AGS) have no ARS mapping, e.g. %s"
                         % (int(out.isna().sum()), ", ".join(missing)))
    return out


def build_dest_attraction_per_taz(df_buildings, df_employees, df_taz, ags_to_ars):
    """Split each commune's authoritative employee total across its TAZ by building
    potential_work share (DECISION 1, commune-total-preserving). commune_id is
    normalised AGS-8 -> ARS-12 before the employees join (B2). Communes with
    employees but no TAZ raise (M4). Returns (DataFrame[taz_id, commune_id(ARS),
    attraction], primary, fallback)."""
    bld = df_buildings[["building_id", "potential_work"]].copy()
    # Derive kreis from commune_id (AGS-8, first 5 digits) to constrain the nearest-TAZ
    # fallback to the point's own Kreis so no employment mass crosses a Kreis boundary
    # (DECISION 2). commune_id is mandatory in df_buildings (building_potentials contract).
    bld["kreis"] = df_buildings["commune_id"].astype(str).str[:5]
    bld_geom = df_buildings.geometry
    assigned, primary, fallback = assign_taz(
        gpd.GeoDataFrame(bld, geometry=bld_geom.values, crs=df_buildings.crs),
        df_taz, id_column="building_id", kreis_column="kreis",
        # Pass a GeoSeries (not GeometryArray) so assign_taz can call .values on it.
        point_geometry=bld_geom.representative_point(),
    )
    # M3: merge potential_work back by building_id, never positional .values.
    pot = assigned.merge(bld[["building_id", "potential_work"]], on="building_id", how="left")
    # commune_id here is still AGS-8 (from df_taz via assign_taz); ARS-12 mapping happens below.
    pot_by_taz = (pot.groupby(["taz_id", "commune_id"])["potential_work"].sum()
                     .rename("pot").reset_index())

    # All TAZ of every commune (so a commune with no buildings still gets rows).
    taz_index = df_taz[["taz_id", "commune_id"]].drop_duplicates()
    grid = taz_index.merge(pot_by_taz, on=["taz_id", "commune_id"], how="left").fillna({"pot": 0.0})
    grid["commune_ars"] = normalize_commune_to_ars(grid["commune_id"], ags_to_ars)   # B2

    emp = df_employees.groupby("commune_id")["weight"].sum().rename("emp")            # ARS-12 keyed
    # M4: every employer commune must have TAZ rows, else its mass is silently lost.
    missing = set(emp.index.astype(str)) - set(grid["commune_ars"].astype(str))
    if missing:
        raise ValueError("%d communes have employees but no TAZ: %s"
                         % (len(missing), sorted(missing)[:5]))
    grid = grid.merge(emp, left_on="commune_ars", right_index=True, how="left").fillna({"emp": 0.0})

    grid["pot_sum"] = grid.groupby("commune_ars")["pot"].transform("sum")
    grid["n_taz"] = grid.groupby("commune_ars")["taz_id"].transform("count")
    share = (grid["pot"] / grid["pot_sum"].where(grid["pot_sum"] > 0)).fillna(1.0 / grid["n_taz"])
    grid["attraction"] = share * grid["emp"]
    grid["taz_id"] = grid["taz_id"].astype(str)
    return grid[["taz_id", "commune_ars", "attraction"]].rename(columns={"commune_ars": "commune_id"}), \
        primary, fallback


def taz_to_kreis_lookup(df_taz):
    """Return a dict mapping taz_id (str) -> 5-digit Kreis ARS (str).

    Used by ``_zone_to_kreis`` when the gravity model operates on TAZ-keyed
    origin/destination identifiers instead of the legacy commune_id AGS-8.
    """
    return dict(zip(df_taz["taz_id"].astype(str), df_taz["kreis"].astype(str)))


def build_origin_population_per_taz(df_homes, df_population, df_taz):
    """Re-bin the PER-PERSON census population (weight per person, keyed
    household_id) onto TAZ via the per-household home POINT (one-to-many on
    household_id). The home's Kreis (commune_id[:5] from the per-person frame)
    constrains any nearest-TAZ fallback to the same Kreis. Returns
    (DataFrame[taz_id, commune_id, population], primary, fallback); the summed
    population equals the input total."""
    hh = df_population.drop_duplicates("household_id")[["household_id", "commune_id"]].copy()
    hh["kreis"] = hh["commune_id"].astype(str).str[:5]
    homes = df_homes[["household_id", "geometry"]].merge(
        hh[["household_id", "kreis"]], on="household_id", how="left")
    homes = gpd.GeoDataFrame(homes, geometry="geometry", crs=df_homes.crs)
    if homes["kreis"].isna().any():
        raise ValueError("%d home households have no population row (kreis unknown)"
                         % int(homes["kreis"].isna().sum()))
    home_taz, primary, fallback = assign_taz(
        homes, df_taz, id_column="household_id", kreis_column="kreis")
    merged = df_population[["household_id", "weight"]].merge(
        home_taz[["household_id", "taz_id", "commune_id"]], on="household_id", how="left")
    if merged["taz_id"].isna().any():
        raise ValueError(
            "%d persons have a household with no home point -> no TAZ; the population "
            "and home-point producers must share household_id" % int(merged["taz_id"].isna().sum()))
    out = (merged.groupby(["taz_id", "commune_id"])["weight"].sum()
                 .rename("population").reset_index())
    out["taz_id"] = out["taz_id"].astype(str)
    return out, primary, fallback
