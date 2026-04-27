"""
Load the preprocessed ALKIS buildings for the Braunschweig region.

This stage reads the GeoParquet produced by
``scripts/preprocess_alkis_landuse.py`` and returns it as a GeoDataFrame
keyed by OI / AGS / GFK / activity.  Downstream stages (buildings, work,
education, secondary) can filter on ``activity`` instead of having to
re-decode the AAA GFK catalogue each time.
"""

from __future__ import annotations

import os

import geopandas as gpd


def configure(context):
    context.config("data_path")
    context.config("braunschweig.alkis_path", "braunschweig/preprocessed/alkis_buildings.parquet")


def _resolve_path(context) -> str:
    return os.path.join(context.config("data_path"), context.config("braunschweig.alkis_path"))


def execute(context) -> gpd.GeoDataFrame:
    path = _resolve_path(context)
    df = gpd.read_parquet(path)

    # Defensive: ensure CRS and column types.
    if df.crs is None:
        df = df.set_crs("EPSG:25832")
    if "activity" in df.columns:
        df["activity"] = df["activity"].astype(str)

    print(
        "[braunschweig.data.alkis] {:,} buildings, {} AGS, activities: {}".format(
            len(df),
            df["AGS"].nunique() if "AGS" in df.columns else 0,
            df["activity"].value_counts().to_dict() if "activity" in df.columns else {},
        )
    )
    return df


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            "ALKIS preprocessed file not found: {path}\n"
            "Run:\n"
            "  python scripts/preprocess_alkis_landuse.py \\\n"
            "      --raw-root eqasim-data/data/braunschweig \\\n"
            "      --vg250 eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip \\\n"
            "      --out-root eqasim-data/data/braunschweig/preprocessed".format(path=path)
        )
    return os.path.getsize(path)
