"""Workplace candidates for Braunschweig (ZGB-8 + external Kreise).

This module is the merged successor of:
- ``bavaria/locations/work.py`` (Origin: eqasim-bavaria @ b20fbe6) - ZGB-8
  ALKIS/OSM workplace candidates plus a synthetic centroid for any
  Gemeinde missing in the OSM extract.
- ``braunschweig/locations/work.py`` - the external-Kreis appender that
  adds one synthetic workplace per outbound Kreis from
  ``braunschweig.data.external_workplaces``.

Phase 2.7 of the eqasim-bs refactor merged both into a single module so the
BS pipeline no longer delegates through ``bavaria.locations.work``.

Schema is preserved (employees, fake, commune_id, iris_id, geometry,
location_id) so downstream stages (commute-distance matching, primary
locations) work unchanged.

When ``work_building_potentials`` is True (default), the ON path REPLACES the
ALKIS/OSM candidate set entirely with gpkg activity buildings that carry
``potential_work > 0``.  The ALKIS area*floors path is used only when the flag
is False, and is byte-identical to the pre-feature pipeline.

When ``taz_work_location_choice`` is True, each work candidate is additionally
assigned a ``taz_id`` column via point-in-polygon against the TAZ polygons.
External/synthetic-centroid candidates that lie outside any TAZ polygon fall
back to the commune-as-TAZ assignment (commune_id used as taz_id), and this
fallback rate is logged so it is observable.  The OFF path does not touch
``taz_id`` and is byte-identical to the pre-feature pipeline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd


# --- Building-potential REPLACE builder ------------------------------------

def build_work_candidates_from_potentials(df_buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """REPLACE work candidates with gpkg activity buildings carrying potential_work > 0.

    Each row in the returned GeoDataFrame corresponds to one gpkg building whose
    ``potential_work`` is strictly positive.  The legacy ALKIS area*floors weights
    are NOT used at all on this path.

    Parameters
    ----------
    df_buildings:
        GeoDataFrame from ``braunschweig.data.building_potentials`` with columns
        ``potential_work``, ``commune_id``, and polygon geometry (EPSG:25832).

    Returns
    -------
    GeoDataFrame
        Columns: employees (float), fake (bool), commune_id (str), iris_id (str),
        geometry (Point, centroid of the input footprint polygon).  CRS matches
        the input.
    """
    df = df_buildings[df_buildings["potential_work"] > 0].copy()
    out = gpd.GeoDataFrame({
        "employees": df["potential_work"].astype(float).values,
        "fake": False,
        "commune_id": df["commune_id"].astype(str).values,
        "iris_id": df["commune_id"].astype(str).values,
        "geometry": df.geometry.centroid.values,
    }, crs=df.crs)
    return out


# --- Inherited from eqasim-bavaria -----------------------------------------

def _execute_base(context):
    """OSM/ALKIS workplace points OR gpkg-building REPLACE + synthetic centroids."""
    enabled = context.config("work_building_potentials")

    if enabled:
        # ON path: entire candidate set comes from gpkg activity buildings.
        df = build_work_candidates_from_potentials(
            context.stage("braunschweig.data.building_potentials"))
        n_gpkg = len(df)
    else:
        # OFF path: legacy ALKIS area*floors, byte-identical to the pre-feature pipeline.
        df = context.stage("braunschweig.data.locations")
        df = df[df["location_type"] == "work"].copy()
        df["employees"] = (df["area"] * df["floors"]).astype(float)
        df["fake"] = False
        df = df[["employees", "fake", "commune_id", "iris_id", "geometry"]]
        n_gpkg = 0

    # Synthetic centroid for any Gemeinde missing from the candidate set (UNCHANGED logic).
    df_fake = context.stage("data.spatial.municipalities")
    df_fake = df_fake[~df_fake["commune_id"].isin(df["commune_id"])].copy()
    df_fake["geometry"] = df_fake["geometry"].centroid
    df_fake["iris_id"] = df_fake["commune_id"].astype(str) + "0000"
    df_fake["iris_id"] = df_fake["iris_id"].astype("category")
    df_fake["employees"] = 1
    df_fake["fake"] = True

    n_fake = len(df_fake)
    df = pd.concat([
        df[["employees", "fake", "commune_id", "iris_id", "geometry"]],
        df_fake[["employees", "fake", "commune_id", "iris_id", "geometry"]],
    ])
    df["location_id"] = np.arange(len(df))
    df["location_id"] = "work_" + df["location_id"].astype(str)

    if enabled:
        print("[braunschweig.locations.work] REPLACE candidates from building "
              "potentials: %d gpkg work buildings + %d synthetic-centroid Gemeinden"
              % (n_gpkg, n_fake))
    return df


# --- Braunschweig-specific -------------------------------------------------

def configure(context):
    context.stage("data.spatial.municipalities")
    context.stage("braunschweig.data.external_workplaces")
    enabled = context.config("work_building_potentials", True)
    if enabled:
        context.stage("braunschweig.data.building_potentials")
    else:
        context.stage("braunschweig.data.locations")
    # TAZ dep: only declare when the flag is ON so the OFF path is unaffected.
    if context.config("taz_work_location_choice", False):
        context.stage("braunschweig.data.spatial.taz")


def _attach_taz_id(df_combined: gpd.GeoDataFrame, df_taz: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach ``taz_id`` to every work candidate by point-in-polygon.

    Candidates that fall outside any TAZ polygon (external synthetic centroids,
    candidates in Kreise not covered by the ZGB TAZ dataset) fall back to
    using their ``commune_id`` as the ``taz_id``.  This degenerate fallback
    keeps the schema consistent: every candidate has a ``taz_id``, but external
    rows are effectively treated as one-TAZ-per-commune.  The primary and
    fallback counts are logged so the rate is observable (CLAUDE.md: no silent
    fallbacks).

    Parameters
    ----------
    df_combined:
        Combined work candidate GeoDataFrame (geometry = centroid point).
    df_taz:
        TAZ polygons from ``braunschweig.data.spatial.taz``.

    Returns
    -------
    GeoDataFrame
        Input frame with an additional ``taz_id`` column (str).
    """
    import logging
    logger = logging.getLogger(__name__)

    if df_taz.crs != df_combined.crs:
        df_taz = df_taz.to_crs(df_combined.crs)

    taz_geom = df_taz[["taz_id", "geometry"]]

    # PIP join: location_id is the unique key.
    pts = df_combined[["location_id", "geometry"]].copy()
    pts = gpd.GeoDataFrame(pts, geometry="geometry", crs=df_combined.crs)
    joined = gpd.sjoin(pts, taz_geom, how="left", predicate="within").drop(
        columns=["index_right"], errors="ignore"
    )
    joined = joined.drop_duplicates("location_id", keep="first")

    n_primary = int(joined["taz_id"].notna().sum())
    n_fallback = int(joined["taz_id"].isna().sum())
    total = n_primary + n_fallback

    logger.info(
        "[braunschweig.locations.work] TAZ assignment: %d/%d candidates primary "
        "(%.1f%%), %d fallback commune-as-TAZ (%.1f%%)",
        n_primary, total, 100.0 * n_primary / total if total else 0.0,
        n_fallback, 100.0 * n_fallback / total if total else 0.0,
    )

    # Apply: primary from PIP, fallback from commune_id.
    taz_map = joined.set_index("location_id")["taz_id"]
    df_combined = df_combined.copy()
    df_combined["taz_id"] = df_combined["location_id"].map(taz_map)
    # Fallback: commune_id used as taz_id for candidates outside any TAZ polygon.
    f_missing = df_combined["taz_id"].isna()
    if f_missing.any():
        df_combined.loc[f_missing, "taz_id"] = df_combined.loc[f_missing, "commune_id"].astype(str)

    df_combined["taz_id"] = df_combined["taz_id"].astype(str)
    return df_combined


def execute(context) -> gpd.GeoDataFrame:
    # Read the TAZ flag (no default here; the default is declared in configure()).
    taz_on = context.config("taz_work_location_choice")

    df_zgb = _execute_base(context)

    if context.config("work_building_potentials"):
        by_commune = df_zgb[~df_zgb["fake"]].groupby("commune_id")["employees"].sum()
        print("[braunschweig.locations.work] potential_work per-commune sum "
              "(cross-check vs Census SvB): %d communes, total %.0f"
              % (len(by_commune), float(by_commune.sum())))

    df_ext = context.stage("braunschweig.data.external_workplaces")

    if df_ext.crs != df_zgb.crs:
        df_ext = df_ext.to_crs(df_zgb.crs)

    # Build synthetic rows that mirror the base schema (employees, fake,
    # commune_id, iris_id, geometry, location_id).
    df_rows = gpd.GeoDataFrame({
        "employees": df_ext["employees"].astype(int),
        "fake": False,
        "commune_id": df_ext["commune_id"].astype(str),
        "iris_id": df_ext["iris_id"].astype(str),
        "geometry": df_ext["geometry"],
    }, crs=df_zgb.crs)

    # Re-issue location_ids sequentially so the concatenated DataFrame
    # has a contiguous unique id space ("work_0", "work_1", ...).
    df_combined = gpd.GeoDataFrame(
        pd.concat([
            df_zgb.drop(columns=["location_id"], errors="ignore"),
            df_rows,
        ], ignore_index=True),
        crs=df_zgb.crs,
    )
    df_combined["location_id"] = "work_" + pd.Series(
        range(len(df_combined)), index=df_combined.index
    ).astype(str)

    n_ext = len(df_rows)
    ext_svb = int(df_rows["employees"].sum())
    zgb_emp_total = int(df_zgb["employees"].sum()) if "employees" in df_zgb.columns else 0
    print(
        "[braunschweig.locations.work] "
        f"ZGB workplaces: {len(df_zgb):,} (sum employees = {zgb_emp_total:,}) + "
        f"external synthetic workplaces: {n_ext} (sum SvB = {ext_svb:,})"
    )

    # When the TAZ flag is ON, attach taz_id to every candidate so the
    # downstream pool filter (candidates.py _filter_pool_by_zone) can select by
    # taz_id.  The OFF path does not add taz_id; the column is absent and the
    # commune_id filter path is byte-identical.
    if taz_on:
        df_taz = context.stage("braunschweig.data.spatial.taz")
        df_combined = _attach_taz_id(df_combined, df_taz)

    return df_combined
