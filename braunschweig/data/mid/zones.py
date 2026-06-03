"""
Zoning system for the MiD 2023 regional sample.

The eight ZGB Kreise match exactly the eight 'Teilgebiete' reported in the
MiD 2023 regional tables, so we build the zones by dissolving the Gemeinde
polygons (from eqasim_common.data.spatial.iris) on their 5-digit Kreis code
(departement_id / AGS-5).

Zone names follow the naming convention used in braunschweig/data/mid/data.py.
"""

import geopandas as gpd

# AGS-5 -> zone name used in data.py
ZONE_NAMES = {
    "03101": "braunschweig",
    "03102": "salzgitter",
    "03103": "wolfsburg",
    "03151": "gifhorn",
    "03153": "goslar",
    "03154": "helmstedt",
    "03157": "peine",
    "03158": "wolfenbuettel",
}


def configure(context):
    context.stage("eqasim_common.data.spatial.iris")


def execute(context):
    df = context.stage("eqasim_common.data.spatial.iris")[["departement_id", "geometry"]].copy()
    df["departement_id"] = df["departement_id"].astype(str)

    # Dissolve Gemeinden into Kreis polygons.
    df_zones = df.dissolve(by="departement_id", as_index=False)

    # Map 5-digit Kreis AGS to readable zone name.
    df_zones["name"] = df_zones["departement_id"].map(ZONE_NAMES)

    missing = df_zones[df_zones["name"].isna()]
    if len(missing) > 0:
        raise RuntimeError(
            "Unexpected Kreis AGS in scope: %s" % missing["departement_id"].tolist()
        )

    return gpd.GeoDataFrame(df_zones[["name", "geometry"]], crs=df.crs)
