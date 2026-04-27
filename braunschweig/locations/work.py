"""Braunschweig work-location stage.

Wraps ``bavaria.locations.work`` (ZGB-8 ALKIS/OSM workplaces) and appends
one synthetic workplace per external Kreis identified by
``braunschweig.data.external_workplaces``. This turns the work-location
universe from "ZGB-8 only" into "ZGB-8 + the external Kreise in the BA
Pendler outbound table", which is what the commute-distance distribution
(MiD P13) and the Kreis OD matrix (BA Pendler) require in order to be
reproducible in the synthetic population.

Schema matches ``bavaria.locations.work`` exactly so downstream code
(``synthesis.population.spatial.primary.candidates``,
``synthesis.population.spatial.primary.locations``) keeps working
unchanged: every sampled destination ``commune_id`` has exactly one
workplace inside it (the synthetic centroid), which is then picked up by
the nearest-commute-distance matching.
"""

from __future__ import annotations

import pandas as pd
import geopandas as gpd


def configure(context):
    context.stage("bavaria.locations.work")
    context.stage("braunschweig.data.external_workplaces")


def execute(context) -> gpd.GeoDataFrame:
    df_zgb = context.stage("bavaria.locations.work")
    df_ext = context.stage("braunschweig.data.external_workplaces")

    if df_ext.crs != df_zgb.crs:
        df_ext = df_ext.to_crs(df_zgb.crs)

    # Build synthetic rows that mirror the bavaria.locations.work schema
    # (employees, fake, commune_id, iris_id, geometry, location_id).
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
        f"ZGB workplaces: {len(df_zgb):,} (Σ employees = {zgb_emp_total:,}) + "
        f"external synthetic workplaces: {n_ext} (Σ SvB = {ext_svb:,})"
    )

    return df_combined
