"""
Braunschweig activity-location catalogue — drop-in replacement for
``braunschweig.data.locations``.

Sources candidate non-home locations from:

  * ALKIS Hausumringe (``braunschweig.data.alkis``) — 944k buildings with
    AAA Gebäudefunktion (GFK) codes mapped to work / education / shop /
    leisure by ``scripts/preprocess_alkis_landuse.py``.
  * ATKIS Landesnutzung (``braunschweig.data.landuse``) — polygon
    supplements, primarily used to densify the sparse ALKIS ``education``
    layer with ``ln_oeffentlicheeinrichtungen`` polygons (centroids).

Emits the exact schema consumed by ``bavaria.locations.{work,education,
secondary}``::

    geometry        Point      (centroid, EPSG:25832)
    building        str/NaN    (OSM-style tag; drives education typing)
    amenity         str/NaN    (OSM-style tag; drives education typing)
    area            float      (m² — footprint or polygon area)
    floors          int        (default 2, 1 for landuse polygons)
    commune_id      str        (AGS, 8-digit)
    iris_id         str        (= commune_id for the ZGB)
    location_type   str        (work / education / shop / leisure)
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# GFK → OSM-style building/amenity tag mapping.
#
# The downstream bavaria.locations.education stage inspects ``building`` and
# ``amenity`` to assign an ``education_type`` of {kindergarten, school,
# university}.  For every other activity these columns are purely
# informational — we still populate them for debugging but they do not
# affect location sampling.
# ---------------------------------------------------------------------------

GFK_BUILDING_TAG = {
    # Education
    "31001_2310": "school",       # Schulgebäude
    "31001_2410": "university",   # Hochschulgebäude / Forschungsinstitut
    "31001_2320": "kindergarten",
    "31001_2321": "school",
    "31001_2322": "school",
    "31001_2340": "kindergarten",
    # Work
    "31001_2000": "office",
    "31001_2010": "office",
    "31001_2500": "public",
    "31001_2600": "transportation",
    "31001_3000": "industrial",
    "31001_3021": "industrial", "31001_3022": "industrial",
    "31001_3023": "industrial", "31001_3024": "industrial",
    "31001_3034": "industrial", "31001_3041": "industrial",
    "31001_3043": "industrial", "31001_3051": "industrial",
    "31001_3065": "industrial", "31001_3072": "industrial",
    "31001_3100": "industrial", "31001_3200": "industrial",
    "31001_3211": "industrial", "31001_3281": "industrial",
    # Shop
    "31001_2100": "retail",
    "31001_2130": "retail",
    # Leisure
    "31001_2463": "church",
    "51002_1220": "historic_building",
    "51002_1230": "historic_building",
    "51002_1250": "historic_building",
    "51002_1260": "historic_building",
    "51002_1290": "historic_building",
    "51003_1201": "tower",
    "51003_1205": "tower",
}

# Minimum footprint/polygon area to keep (m²).  Below this a record is
# most likely a parcel artefact rather than a useful activity location.
MIN_AREA = 20.0

# Default number of floors assumed for buildings without a height attribute.
DEFAULT_FLOORS = 2

# ALKIS activities that feed the secondary / work / education samplers.
ACTIVITY_TO_LOCATION_TYPE = {
    "work":      "work",
    "education": "education",
    "shop":      "shop",
    "leisure":   "leisure",
}

# ATKIS landuse layers that we lift into the locations catalogue when the
# ALKIS building layer alone is too sparse.  Currently only education is
# supplemented because ALKIS yields ~1.3k education buildings for the
# entire ZGB-8 region (very sparse); work / shop / leisure are well
# covered (>10k each).
LANDUSE_SUPPLEMENTS = {
    "ln_oeffentlicheeinrichtungen": ("education", "school"),
}


def configure(context):
    context.stage("braunschweig.data.alkis")
    context.stage("braunschweig.data.landuse")
    context.stage("braunschweig.data.osm")
    context.stage("eqasim_common.data.spatial.iris")


def _prepare_alkis(df_alkis: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    df = df_alkis.copy()
    df["activity"] = df["activity"].astype(str)
    df = df[df["activity"].isin(ACTIVITY_TO_LOCATION_TYPE)].copy()

    if "area_m2" not in df.columns:
        df["area_m2"] = df.geometry.area.astype("float32")
    df = df[df["area_m2"] >= MIN_AREA].copy()

    df["location_type"] = df["activity"].map(ACTIVITY_TO_LOCATION_TYPE)

    gfk = df["GFK"].astype(str) if "GFK" in df.columns else pd.Series("", index=df.index)
    df["building"] = gfk.map(GFK_BUILDING_TAG)
    # Default 'building' tag per location_type when GFK is not in the
    # fine-grained mapping.  This matches OSM conventions so downstream
    # consumers (education.py) still trigger on recognised values.
    defaults = {"work": "office", "education": "school",
                "shop": "retail", "leisure": np.nan}
    for lt, tag in defaults.items():
        mask = (df["location_type"] == lt) & df["building"].isna()
        df.loc[mask, "building"] = tag

    # ``amenity`` mirrors kindergarten/school/university so education.py
    # triggers regardless of which column it consults first.  Initialise
    # as object dtype to avoid dtype-coercion warnings.
    df["amenity"] = pd.Series([pd.NA] * len(df), index=df.index, dtype=object)
    edu_mask = df["location_type"] == "education"
    df.loc[edu_mask, "amenity"] = df.loc[edu_mask, "building"].to_numpy()

    df["area"] = df["area_m2"].astype(float)
    df["floors"] = DEFAULT_FLOORS
    df["geometry"] = df.geometry.centroid

    return df[["geometry", "building", "amenity", "area", "floors",
               "location_type"]]


def _prepare_landuse(df_landuse: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = []
    if "layer" not in df_landuse.columns:
        return gpd.GeoDataFrame(
            columns=["geometry", "building", "amenity", "area", "floors",
                     "location_type"],
            geometry="geometry", crs=df_landuse.crs,
        )

    layers = df_landuse["layer"].astype(str)
    for layer_name, (loc_type, building_tag) in LANDUSE_SUPPLEMENTS.items():
        sub = df_landuse[layers == layer_name].copy()
        if len(sub) == 0:
            continue
        if "area_m2" not in sub.columns:
            sub["area_m2"] = sub.geometry.area.astype("float32")
        sub = sub[sub["area_m2"] >= MIN_AREA].copy()

        sub["building"] = building_tag
        sub["amenity"] = building_tag
        sub["area"] = sub["area_m2"].astype(float)
        sub["floors"] = 1
        sub["location_type"] = loc_type
        sub["geometry"] = sub.geometry.centroid
        out.append(sub[["geometry", "building", "amenity", "area", "floors",
                        "location_type"]])

    if not out:
        return gpd.GeoDataFrame(
            columns=["geometry", "building", "amenity", "area", "floors",
                     "location_type"],
            geometry="geometry", crs=df_landuse.crs,
        )
    return gpd.GeoDataFrame(pd.concat(out, ignore_index=True),
                            crs=df_landuse.crs)


def _prepare_osm(df_osm: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Harmonise the preprocessed OSM POI parquet to the common schema."""
    df = df_osm.copy()
    if "area" not in df.columns:
        df["area"] = 0.0
    df["area"] = df["area"].fillna(0.0).astype(float)
    df = df[df["area"].fillna(0.0) >= 0.0].copy()  # no area filter for OSM
    if "floors" not in df.columns:
        df["floors"] = DEFAULT_FLOORS
    df["floors"] = df["floors"].fillna(DEFAULT_FLOORS).astype(int)
    if "building" not in df.columns:
        df["building"] = pd.NA
    if "amenity" not in df.columns:
        df["amenity"] = pd.NA
    df["location_type"] = df["location_type"].astype(str)
    return df[["geometry", "building", "amenity", "area", "floors",
               "location_type", "commune_id", "iris_id"]]


def execute(context) -> gpd.GeoDataFrame:
    df_alkis = context.stage("braunschweig.data.alkis")
    df_landuse = context.stage("braunschweig.data.landuse")
    df_osm = context.stage("braunschweig.data.osm")
    df_zones = context.stage("eqasim_common.data.spatial.iris")

    if df_zones.crs != df_alkis.crs:
        df_zones = df_zones.to_crs(df_alkis.crs)

    df_bld = _prepare_alkis(df_alkis)
    df_lu = _prepare_landuse(df_landuse)

    df_bld_lu = gpd.GeoDataFrame(
        pd.concat([df_bld, df_lu], ignore_index=True),
        crs=df_alkis.crs,
    )

    # Spatial join to Gemeinde polygons to attach commune_id / iris_id.
    df_bld_lu = gpd.sjoin(
        df_bld_lu,
        df_zones[["geometry", "commune_id", "iris_id"]],
        how="inner",
        predicate="within",
    ).drop(columns=["index_right"]).reset_index(drop=True)

    # OSM POIs already carry commune_id/iris_id from the preprocessor.
    if df_osm.crs != df_alkis.crs:
        df_osm = df_osm.to_crs(df_alkis.crs)
    df_osm_h = _prepare_osm(df_osm)

    df_bld_lu["source"] = "alkis"
    df_osm_h["source"] = "osm"

    df_all = gpd.GeoDataFrame(
        pd.concat([df_bld_lu, df_osm_h], ignore_index=True),
        crs=df_alkis.crs,
    )

    df_all["floors"] = df_all["floors"].fillna(DEFAULT_FLOORS).astype(int)
    df_all["area"] = df_all["area"].astype(float)

    counts = df_all["location_type"].value_counts().to_dict()
    per_source = (
        df_all.groupby(["source", "location_type"]).size().unstack(fill_value=0)
    )
    print(
        "[braunschweig.data.locations] "
        f"{len(df_all):,} locations across {df_all['commune_id'].nunique()} "
        f"communes — {counts}"
    )
    print("[braunschweig.data.locations] per source:\n" + per_source.to_string())
    return df_all


def validate(context):
    return 0
