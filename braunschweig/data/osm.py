"""
Thin loader for the preprocessed OSM POI catalogue.

Reads the GeoParquet produced by ``scripts/preprocess_osm_pois.py``.
The file is already clipped to the ZGB scope (via osmconvert bbox +
precise VG250 sjoin) and carries ``commune_id`` / ``iris_id`` per row.
"""

from __future__ import annotations

import os

import geopandas as gpd


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.osm_pois_path",
        "braunschweig/preprocessed/osm_pois.parquet",
    )


def _resolve_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.osm_pois_path"),
    )


def execute(context) -> gpd.GeoDataFrame:
    path = _resolve_path(context)
    df = gpd.read_parquet(path)

    if df.crs is None:
        df = df.set_crs("EPSG:25832")
    if "location_type" in df.columns:
        df["location_type"] = df["location_type"].astype(str)

    print(
        "[braunschweig.data.osm] {:,} POIs — {}".format(
            len(df),
            df["location_type"].value_counts().to_dict()
            if "location_type" in df.columns else {},
        )
    )
    return df


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            "OSM POI parquet not found: {}\n"
            "Run scripts/preprocess_osm_pois.py".format(path)
        )
    return os.path.getsize(path)
