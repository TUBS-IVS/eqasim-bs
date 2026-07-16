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


def assign_census_commune(points_gdf, df_municipalities, id_column, point_geometry=None):
    """Attach the CENSUS commune_id (12-digit ARS) to each point by point-in-polygon
    against the census municipality polygons (data.spatial.municipalities).

    This reconciles the RVB TAZ gpkg's AGS-8 Gemeinde codes with the census
    communes by LOCATION: the two sources use different Gemeinde partitions /
    reform vintages, so a handful of AGS codes have no direct census counterpart.
    A geometric join sidesteps the code mismatch entirely -- a point is placed in
    whichever census Gemeinde actually contains it. Points outside every polygon
    use a nearest-commune fallback (counted and logged, never silent). Dedups on
    ``id_column`` (never the index) and asserts no row is lost. ``point_geometry``
    overrides the join point (e.g. representative_point for polygons/footprints).
    Returns (DataFrame[id_column, commune_ars], primary, fallback).
    """
    if id_column not in points_gdf.columns:
        raise ValueError("assign_census_commune requires an explicit id column %r" % id_column)
    muni = df_municipalities[["commune_id", "geometry"]].copy()
    muni["commune_id"] = muni["commune_id"].astype(str)
    if muni.crs != points_gdf.crs:
        muni = muni.to_crs(points_gdf.crs)
    muni = muni.rename(columns={"commune_id": "commune_ars"})

    pts = points_gdf[[id_column]].copy()
    pts["geometry"] = (point_geometry if point_geometry is not None else points_gdf.geometry).values
    pts = gpd.GeoDataFrame(pts, geometry="geometry", crs=points_gdf.crs)

    joined = gpd.sjoin(pts, muni, how="left", predicate="within").drop(columns=["index_right"])
    joined = joined.drop_duplicates(id_column, keep="first")
    primary = int(joined["commune_ars"].notna().sum())

    missing_ids = joined.loc[joined["commune_ars"].isna(), id_column]
    fallback = int(len(missing_ids))
    if fallback:
        miss = pts[pts[id_column].isin(missing_ids)]
        near = gpd.sjoin_nearest(miss, muni, how="left").drop_duplicates(id_column, keep="first")
        fill = near.set_index(id_column)["commune_ars"]
        sel = joined[id_column].isin(missing_ids)
        joined.loc[sel, "commune_ars"] = joined.loc[sel, id_column].map(fill)

    if len(joined) != len(points_gdf):
        raise ValueError(
            "assign_census_commune row count changed (%d in, %d out); duplicate %r"
            % (len(points_gdf), len(joined), id_column))
    total = primary + fallback
    logger.info(
        "[taz_margins.commune] %d points; within %d (%.1f%%), nearest fallback %d (%.1f%%)",
        total, primary, 100.0 * primary / total if total else 0.0,
        fallback, 100.0 * fallback / total if total else 0.0)
    return joined[[id_column, "commune_ars"]], primary, fallback


def build_dest_attraction_per_taz(df_buildings, df_employees, df_taz, df_municipalities):
    """Split each census commune's authoritative employee total across its TAZ by
    building potential_work share (DECISION 1, commune-total-preserving: the BA
    Kreis-level control is untouched).

    Each TAZ is assigned to its census commune (12-digit ARS) SPATIALLY, by
    point-in-polygon against data.spatial.municipalities (B2). This replaces the
    former AGS-8 -> ARS-12 code crosswalk, which could not cover the ~10 Gemeinde
    codes where the RVB gpkg and the census disagree; the geometric join places
    every TAZ in whichever census Gemeinde contains it, so the mismatch vanishes.

    Buildings are then assigned to TAZ (nearest-TAZ fallback CONSTRAINED to the
    building's Kreis, DECISION 2). The Kreis is the first 5 digits of the
    building's AGS-8 commune_id, which is IDENTICAL in AGS-8 and ARS-12 (only the
    Gemeinde suffix differs), so it is a reliable constraint despite the full-code
    mismatch. Communes with employees but no TAZ raise (M4). Returns
    (DataFrame[taz_id, commune_id(ARS), attraction], primary, fallback), one row
    per taz_id.
    """
    # 1) Each TAZ -> its census commune (ARS-12) by geometry (representative_point
    #    is guaranteed inside the polygon). All TAZ are placed, so a commune with
    #    no buildings still gets rows (uniform fallback below).
    taz_geom = df_taz.geometry
    taz_commune, _, _ = assign_census_commune(
        gpd.GeoDataFrame(df_taz[["taz_id"]].copy(), geometry=taz_geom.values, crs=df_taz.crs),
        df_municipalities, id_column="taz_id",
        point_geometry=taz_geom.representative_point())
    taz_commune["taz_id"] = taz_commune["taz_id"].astype(str)

    # 2) Each building -> a TAZ (nearest fallback constrained to the building's
    #    Kreis; the Kreis prefix is reliable even though the full AGS code mismatches).
    bld = df_buildings[["potential_work"]].copy()
    bld["_bid"] = range(len(bld))
    bld["kreis"] = df_buildings["commune_id"].astype(str).str[:5]
    bld_geom = df_buildings.geometry
    assigned, primary, fallback = assign_taz(
        gpd.GeoDataFrame(bld, geometry=bld_geom.values, crs=df_buildings.crs),
        df_taz, id_column="_bid", kreis_column="kreis",
        # Pass a GeoSeries (not GeometryArray) so assign_taz can call .values on it.
        point_geometry=bld_geom.representative_point())
    # M3: merge potential_work back by _bid, never positional .values.
    pot = assigned.merge(bld[["_bid", "potential_work"]], on="_bid", how="left")
    pot_by_taz = pot.groupby("taz_id")["potential_work"].sum().rename("pot").reset_index()
    pot_by_taz["taz_id"] = pot_by_taz["taz_id"].astype(str)

    # 3) Every TAZ (from the geometric taz->commune map) gets a row; TAZ without
    #    buildings -> pot 0. Grouping is by the TAZ's census commune (ARS-12).
    grid = taz_commune.merge(pot_by_taz, on="taz_id", how="left").fillna({"pot": 0.0})

    emp = (df_employees.assign(commune_id=df_employees["commune_id"].astype(str))
                       .groupby("commune_id")["weight"].sum().rename("emp"))     # ARS-12 keyed
    # M4: every employer commune must be reachable by some TAZ, else its mass is silently lost.
    missing = set(emp.index.astype(str)) - set(grid["commune_ars"].astype(str))
    if missing:
        raise ValueError("%d communes have employees but no TAZ: %s"
                         % (len(missing), sorted(missing)[:5]))
    grid = grid.merge(emp, left_on="commune_ars", right_index=True, how="left").fillna({"emp": 0.0})

    grid["pot_sum"] = grid.groupby("commune_ars")["pot"].transform("sum")
    grid["n_taz"] = grid.groupby("commune_ars")["taz_id"].transform("count")
    # Fallback transparency (CLAUDE.md): a commune with zero total building
    # potential falls back to a uniform 1/n_taz split. That is designed for
    # single communes, but a broken building->TAZ potential join upstream would
    # silently flatten EVERY commune's attraction to uniform -- so the rate is
    # counted and logged, with a WARNING when it dominates.
    zero_pot_communes = grid.loc[grid["pot_sum"] <= 0, "commune_ars"].nunique()
    n_communes = grid["commune_ars"].nunique()
    if zero_pot_communes:
        rate = zero_pot_communes / n_communes if n_communes else 0.0
        log = logger.warning if rate > 0.5 else logger.info
        log(
            "[taz_margins.attraction] %d/%d communes (%.1f%%) have zero total "
            "building potential -> uniform 1/n_taz split (fallback). A dominant "
            "rate means the building potential join upstream is broken.",
            zero_pot_communes, n_communes, 100.0 * rate,
        )
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
    """Distribute each commune's census population across its TAZ by the home-point
    DISTRIBUTION, keyed on commune_id (12-digit ARS).

    The population producer (data.census.filtered / popsim.stage -- the FULL
    population) and the home-point producer (home_cell -- the SAMPLED population)
    use DIFFERENT household_id spaces (a composite census string vs a reindexed
    integer) and cannot be joined on household_id. But BOTH carry the same 12-digit
    ARS ``commune_id``, so the origin margin is a WITHIN-COMMUNE split (directly
    analogous to the destination potential_work split): within each commune, the
    share of home points in each TAZ weights that commune's authoritative census
    population. The home's Kreis (commune_id[:5]) constrains any nearest-TAZ
    fallback to the same Kreis.

    df_homes: home_cell [commune_id (ARS-12), geometry] (per household).
    df_population: data.census.filtered [commune_id (ARS-12), weight] (per person).
    Returns (DataFrame[taz_id, commune_id, population], primary, fallback); each
    commune's population is fully distributed across its TAZ (per-commune conserved).
    """
    # 1) authoritative per-commune population weight (the FULL population).
    pop_by_commune = (df_population.assign(commune_id=df_population["commune_id"].astype(str))
                      .groupby("commune_id")["weight"].sum().rename("commune_pop").reset_index())

    # 2) assign each home POINT to a TAZ; the kreis-constrained fallback uses the
    #    home's OWN ARS-12 commune_id[:5] (a synthetic per-row id keeps assign_taz's
    #    dedup safe and independent of the incompatible household_id).
    homes = df_homes[["commune_id", "geometry"]].reset_index(drop=True).copy()
    homes["commune_id"] = homes["commune_id"].astype(str)
    homes["kreis"] = homes["commune_id"].str[:5]
    homes["_home_id"] = range(len(homes))
    homes = gpd.GeoDataFrame(homes, geometry="geometry", crs=df_homes.crs)
    home_taz, primary, fallback = assign_taz(
        homes, df_taz, id_column="_home_id", kreis_column="kreis")
    # attach the home's OWN commune (ARS-12); assign_taz returns the TAZ's commune
    # (AGS-8), which we do NOT use for the split (we split by the population commune).
    home_taz = home_taz.merge(
        homes[["_home_id", "commune_id"]].rename(columns={"commune_id": "home_commune"}),
        on="_home_id", how="left")

    # 3) within-commune TAZ home-share: fraction of a commune's homes in each TAZ.
    counts = (home_taz.groupby(["home_commune", "taz_id"]).size()
              .rename("n_homes").reset_index())
    counts["commune_total"] = counts.groupby("home_commune")["n_homes"].transform("sum")
    counts["share"] = counts["n_homes"] / counts["commune_total"]

    # 4) origin population per TAZ = commune weight x within-commune home-share.
    m = counts.merge(pop_by_commune, left_on="home_commune", right_on="commune_id", how="left")
    n_no_pop = int(m["commune_pop"].isna().sum())
    if n_no_pop:
        logger.warning(
            "[taz_margins.origin] %d TAZ-commune rows: commune has homes but no census "
            "population -> 0 weight (check commune_id alignment)", n_no_pop)
    m["population"] = m["share"] * m["commune_pop"].fillna(0.0)
    out = (m[["taz_id", "home_commune", "population"]]
           .rename(columns={"home_commune": "commune_id"}))
    out["taz_id"] = out["taz_id"].astype(str)
    # Conservation transparency: a commune with census population but ZERO
    # sampled home points never enters `counts`, so its whole census weight
    # silently vanishes from the origin margin (the docstring promises
    # per-commune conservation). Expected at low sampling rates for tiny
    # communes -- but the lost mass must be visible, not silent.
    lost = pop_by_commune[~pop_by_commune["commune_id"]
                          .isin(set(counts["home_commune"]))]
    if len(lost):
        lost_weight = float(lost["commune_pop"].sum())
        total_weight = float(pop_by_commune["commune_pop"].sum())
        lost_share = lost_weight / total_weight if total_weight else 0.0
        log = logger.warning if lost_share > 0.01 else logger.info
        log(
            "[taz_margins.origin] %d commune(s) have census population but no "
            "sampled home point -> %.0f persons (%.2f%% of total) missing from "
            "the origin margin (e.g. %s). Expected for tiny communes at low "
            "sampling rates; a large share means a commune_id mismatch.",
            len(lost), lost_weight, 100.0 * lost_share,
            sorted(lost["commune_id"].astype(str))[:5],
        )
    return out[["taz_id", "commune_id", "population"]], primary, fallback
