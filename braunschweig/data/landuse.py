"""
Load the preprocessed ATKIS landuse polygons (LGLN FS_LN_03) for the
Braunschweig region.

The dataset is already clipped to the configured scope and written as a
single zstd-compressed GeoParquet by ``preprocess_alkis_landuse.py``.
Each polygon carries:

    - ``layer``     original ATKIS layer name (e.g. ``ln_wohnnutzung``)
    - ``activity``  eqasim activity category (residential / work /
                    education / shop / leisure / other / None)
    - ``area_m2``   polygon area in square metres
    - ``geometry``  (Multi)Polygon in EPSG:25832
"""

from __future__ import annotations

import os

import geopandas as gpd


def configure(context):
    context.config("data_path")
    context.config("braunschweig.landuse_path", "braunschweig/preprocessed/landuse.parquet")


def _resolve_path(context) -> str:
    return os.path.join(context.config("data_path"), context.config("braunschweig.landuse_path"))


def execute(context) -> gpd.GeoDataFrame:
    path = _resolve_path(context)
    df = gpd.read_parquet(path)

    if df.crs is None:
        df = df.set_crs("EPSG:25832")
    if "layer" in df.columns:
        df["layer"] = df["layer"].astype(str)
    if "activity" in df.columns:
        df["activity"] = df["activity"].astype("object")  # keep NaN

    print(
        "[braunschweig.data.landuse] {:,} polygons across {} layers".format(
            len(df), df["layer"].nunique() if "layer" in df.columns else 0
        )
    )
    return df


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            "Landuse preprocessed file not found: {}\nRun scripts/preprocess_alkis_landuse.py".format(path)
        )
    return os.path.getsize(path)
