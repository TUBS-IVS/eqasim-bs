"""Education-location candidates from OSM/ALKIS POI data.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/locations/education.py``.
Moved to ``eqasim_common.locations`` in Phase 2.5 of the eqasim-bs refactor;
inherited unchanged.  It reads the OSM POIs staged by
``eqasim_common.data.osm.locations`` (the BS configs define no alias for that
stage).
"""

import numpy as np
import pandas as pd


def configure(context):
    context.stage("eqasim_common.data.osm.locations")
    context.stage("data.spatial.municipalities")

MINIMUM_AREA = 20

# Share of fallback items above which a warning is printed
FALLBACK_WARN_RATE = 0.05

def execute(context):
    # Load data
    df = context.stage("eqasim_common.data.osm.locations")    
    df = df[df["location_type"] == "education"].copy()
    df["fake"] = False

    # Weight
    df["weight"] = np.maximum(df["area"], MINIMUM_AREA) * df["floors"]

    # Handle types
    for education_type in ["kindergarten", "school", "university"]:
        f = df["building"] == education_type
        df.loc[f, "education_type"] = education_type

    for education_type in ["kindergarten", "school", "university"]:
        f = df["amenity"] == education_type
        df.loc[f, "education_type"] = education_type

    df = df[~df["education_type"].isna()].copy()

    # Need this for the IDF logic, not for the Germany logic
    df_fake = context.stage("data.spatial.municipalities")
    df_fake = df_fake[~df_fake["commune_id"].isin(df["commune_id"])].copy()

    df_fake["geometry"] = df_fake["geometry"].centroid

    # Fallback transparency: real OSM education POIs vs. municipality-centroid placeholders
    n_real_municipalities = df["commune_id"].nunique()
    n_fake_municipalities = len(df_fake)
    n_municipalities = n_real_municipalities + n_fake_municipalities
    fake_rate = n_fake_municipalities / max(n_municipalities, 1)
    print("[education] municipalities with OSM POI (primary) {}/{} ({:.1f}%), centroid fallback {} ({:.1f}%)".format(
        n_real_municipalities, n_municipalities, 100 * (1 - fake_rate),
        n_fake_municipalities, 100 * fake_rate))
    if fake_rate > FALLBACK_WARN_RATE:
        print("[education] WARNING: {:.1f}% of municipalities use a centroid fallback (> {:.0f}%)".format(
            100 * fake_rate, 100 * FALLBACK_WARN_RATE))

    df_fake["iris_id"] = df_fake["commune_id"].astype(str) + "0000"
    df_fake["iris_id"] = df_fake["iris_id"].astype("category")

    df_fake["fake"] = True

    df_fake["education_type"] = "unknown"
    df_fake["weight"] = 1.0

    # Merge
    df = pd.concat([
        df[["fake", "commune_id", "iris_id", "education_type", "weight", "geometry"]], 
        df_fake[["fake", "commune_id", "iris_id", "education_type", "weight", "geometry"]]
    ])

    # Convert to category
    df["education_type"] = df["education_type"].astype("category")

    # Identifiers
    df["location_id"] = np.arange(len(df))
    df["location_id"] = "edu_" + df["location_id"].astype(str)

    return df
