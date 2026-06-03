"""synpp stage: load the committed NDS school facilities CSV as a GeoDataFrame.

Output: GeoDataFrame[school_id, level, capacity, commune_id, geometry] in
EPSG:25832, consumed by braunschweig.synthesis.locations.education_gravity.
"""
from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from braunschweig.data.schools.typing import SCHOOL_LEVELS

CRS_METRIC = "EPSG:25832"


def build_facilities_frame(df):
    """Validate a raw CSV frame and return the facilities GeoDataFrame."""
    for level in SCHOOL_LEVELS:
        if level not in set(df["level"]):
            raise RuntimeError(
                f"[braunschweig.data.schools.facilities] missing school level "
                f"'{level}' in school CSV; re-run scripts/extract_nds_schools.py"
            )
    if df["x"].isna().any() or df["y"].isna().any():
        raise RuntimeError(
            "[braunschweig.data.schools.facilities] NaN coordinate in school CSV"
        )
    if not (df["capacity"] > 0).all():
        raise RuntimeError(
            "[braunschweig.data.schools.facilities] non-positive capacity in CSV"
        )
    gdf = gpd.GeoDataFrame(
        df.assign(commune_id=df["ags8"].astype(str).str.zfill(8)),
        geometry=[Point(x, y) for x, y in zip(df["x"], df["y"])],
        crs=CRS_METRIC,
    )
    return gdf[["school_id", "level", "capacity", "commune_id", "geometry"]]


def configure(context):
    context.config("data_path")
    context.config("nds_schools_path", "braunschweig/schools/nds_schools_zgb.csv")


def _resolve_path(context):
    return os.path.join(context.config("data_path"),
                        context.config("nds_schools_path"))


def execute(context):
    df = pd.read_csv(_resolve_path(context), dtype={"ags8": str, "kreis5": str})
    gdf = build_facilities_frame(df)
    print(f"[braunschweig.data.schools.facilities] {len(gdf)} (school,level) "
          f"facilities; capacity by level: "
          f"{gdf.groupby('level')['capacity'].sum().round(0).to_dict()}")
    return gdf


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"NDS school facilities CSV not found: {path}\n"
            "Run scripts/extract_nds_schools.py to generate it."
        )
    return os.path.getsize(path)
