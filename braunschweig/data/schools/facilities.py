"""synpp stage: load the NDS school facilities CSV as a GeoDataFrame.

Output: GeoDataFrame[school_id, level, capacity, commune_id, geocode_quality,
geometry] in EPSG:25832, consumed by
braunschweig.synthesis.locations.education_gravity. ``geocode_quality`` is
carried through for traceability so downstream validation can report the share
of pupils assigned to schools placed by a coarse PLZ-centroid fallback rather
than a resolved address.
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
    # Carry the geocoding provenance through for traceability. Older CSVs
    # without the column default to "unknown" so the loader stays compatible.
    geocode_quality = (
        df["geocode_quality"].astype(str)
        if "geocode_quality" in df.columns
        else "unknown"
    )
    gdf = gpd.GeoDataFrame(
        df.assign(
            commune_id=df["ags8"].astype(str).str.zfill(8),
            geocode_quality=geocode_quality,
        ),
        geometry=[Point(x, y) for x, y in zip(df["x"], df["y"])],
        crs=CRS_METRIC,
    )
    return gdf[["school_id", "level", "capacity", "commune_id",
                "geocode_quality", "geometry"]]


def configure(context):
    context.config("data_path")
    context.config("nds_schools_path", "braunschweig/schools/nds_schools_zgb.csv")


def _resolve_path(context):
    return os.path.join(context.config("data_path"),
                        context.config("nds_schools_path"))


def execute(context):
    df = pd.read_csv(_resolve_path(context), dtype={"ags8": str, "kreis5": str})
    gdf = build_facilities_frame(df)
    n_plz = int((gdf["geocode_quality"] == "plz_centroid").sum())
    print(f"[braunschweig.data.schools.facilities] {len(gdf)} (school,level) "
          f"facilities; capacity by level: "
          f"{gdf.groupby('level')['capacity'].sum().round(0).to_dict()}; "
          f"{n_plz} placed by PLZ-centroid fallback "
          f"({100.0 * n_plz / max(len(gdf), 1):.1f}%)")
    return gdf


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"NDS school facilities CSV not found: {path}\n"
            "Run scripts/extract_nds_schools.py to generate it."
        )
    return os.path.getsize(path)
