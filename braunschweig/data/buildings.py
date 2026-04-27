"""
Braunschweig building registry — drop-in replacement for
``braunschweig.data.buildings``.

Emits the same schema expected by ``bavaria.locations.home``:
    building_id   int
    weight        float    (proxy for dwelling capacity, = footprint area)
    commune_id    str      (AGS, 8-digit)
    iris_id       str      (AGS, 8-digit; ZGB has no IRIS-level split)
    geometry      Point    (building centroid, EPSG:25832)

The stage reads the ALKIS parquet produced by
``scripts/preprocess_alkis_landuse.py`` and retains only polygons whose
AAA Gebäudefunktion maps to ``residential`` (plus ``unknown`` codes that
pass the area filter, as a safety net for settlements where our GFK
mapping is incomplete).  Garages, sheds and other ancillary buildings
(``activity == 'ancillary'``) are explicitly excluded.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd


# Residential GFK codes collapse to these activity labels in the
# preprocessing stage.  'unknown' is a fallback for codes not yet in our
# mapping table — we still include them but only if they pass the area
# filter so pure garages/sheds get excluded even without an explicit code.
RESIDENTIAL_ACTIVITIES = {"residential", "unknown"}

# Minimum / maximum footprint area in m² — same heuristic as the Bavarian
# Hausumringe pipeline.  40 m² eliminates garages/sheds, 400 m² eliminates
# apartment complexes that would otherwise dominate sampling.
AREA_MIN = 40.0
AREA_MAX = 400.0


def configure(context):
    context.stage("braunschweig.data.alkis")
    context.stage("bavaria.data.spatial.iris")


def execute(context) -> gpd.GeoDataFrame:
    df_alkis = context.stage("braunschweig.data.alkis")
    df_zones = context.stage("bavaria.data.spatial.iris")

    # Ensure aligned CRS (both should be UTM32N/EPSG:25832).
    if df_zones.crs != df_alkis.crs:
        df_zones = df_zones.to_crs(df_alkis.crs)

    df = df_alkis.copy()
    df["activity"] = df["activity"].astype(str)

    # 1) Keep residential & unknown activities, drop garages & other
    #    ancillary footprints outright.
    df = df[df["activity"].isin(RESIDENTIAL_ACTIVITIES)].copy()

    # 2) Area filter (40–400 m²).  ``area_m2`` already exists from the
    #    preprocessing step; recompute if missing as a defensive fallback.
    if "area_m2" not in df.columns:
        df["area_m2"] = df.geometry.area.astype("float32")
    df = df[(df["area_m2"] >= AREA_MIN) & (df["area_m2"] < AREA_MAX)].copy()

    print(
        "[braunschweig.data.buildings] {:,} candidate dwellings after GFK+area filter".format(len(df))
    )

    # 3) Weight by area, take centroid.
    df["weight"] = df["area_m2"].astype(float)
    df["geometry"] = df.geometry.centroid
    df["building_id"] = np.arange(len(df))

    # 4) Impute commune_id / iris_id via spatial join to VG250 Gemeinden.
    df = gpd.sjoin(
        df,
        df_zones[["geometry", "commune_id", "iris_id"]],
        how="left",
        predicate="within",
    ).reset_index(drop=True).drop(columns=["index_right"])

    # Fallback: for buildings whose centroid landed outside any Gemeinde
    # polygon (tiny topology artefacts) use the AGS attribute directly.
    # Cast categorical columns to object first so we can inject any AGS.
    for col in ("commune_id", "iris_id"):
        if isinstance(df[col].dtype, pd.CategoricalDtype):
            df[col] = df[col].astype(object)
    if "AGS" in df.columns:
        missing = df["commune_id"].isna()
        if missing.any():
            df.loc[missing, "commune_id"] = df.loc[missing, "AGS"].astype(str)
            df.loc[missing, "iris_id"] = df.loc[missing, "AGS"].astype(str)

    df = df[df["commune_id"].notna()].copy()

    df_combined = df[["building_id", "weight", "commune_id", "iris_id", "geometry"]]
    df_combined = gpd.GeoDataFrame(df_combined, crs=df.crs)

    # 5) Fill Gemeinden that ended up with zero buildings (should be rare
    #    but happens for tiny exclaves) via the zone centroid.
    required_zones = set(df_zones["commune_id"].unique())
    available_zones = set(df_combined["commune_id"].unique())
    missing_zones = required_zones - available_zones

    if missing_zones:
        print(
            "[braunschweig.data.buildings] filling {} commune(s) with "
            "zone centroids".format(len(missing_zones))
        )
        df_missing = df_zones[df_zones["commune_id"].isin(missing_zones)][
            ["commune_id", "iris_id", "geometry"]
        ].copy()
        df_missing["geometry"] = df_missing["geometry"].centroid
        df_missing["building_id"] = np.arange(len(df_missing)) + len(df_combined)
        df_missing["weight"] = 1.0
        df_combined = gpd.GeoDataFrame(
            pd.concat([df_combined, df_missing], ignore_index=True),
            crs=df_combined.crs,
        )

    print(
        "[braunschweig.data.buildings] returning {:,} buildings across {} communes".format(
            len(df_combined), df_combined["commune_id"].nunique()
        )
    )
    return df_combined


def validate(context):
    return 0
