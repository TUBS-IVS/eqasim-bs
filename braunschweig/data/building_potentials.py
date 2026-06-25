"""synpp data stage: building-level activity potentials.

Loads the renamed parquet produced by
``scripts/import_building_activity_potentials.py`` and attaches ``commune_id``
by a centroid spatial join to the VG250 Gemeinde polygons (mirroring
``braunschweig.data.buildings``). The footprint polygon is kept as the active
geometry so downstream consumers can point-in-polygon join candidates onto it.

Stage name: ``braunschweig.data.building_potentials``.
"""
from __future__ import annotations

import os

import geopandas as gpd

CRS_METRIC = "EPSG:25832"

POTENTIAL_COLUMNS = [
    "potential_work", "potential_school", "potential_university",
    "potential_kindergarten", "potential_leisure",
    "potential_retail_daily", "potential_retail_non_daily",
    "potential_generic",
]
REQUIRED_COLUMNS = ["building_id"] + POTENTIAL_COLUMNS


def load_potentials(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_parquet(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(CRS_METRIC)
    elif gdf.crs.to_epsg() != 25832:
        gdf = gdf.to_crs(CRS_METRIC)
    missing = [c for c in REQUIRED_COLUMNS if c not in gdf.columns]
    if missing:
        raise ValueError(
            "building_activity_potentials parquet missing columns %s; "
            "re-run scripts/import_building_activity_potentials.py" % missing
        )
    return gdf


def assign_commune(gdf: gpd.GeoDataFrame, zones: gpd.GeoDataFrame):
    """Attach ``commune_id`` by centroid-in-polygon (primary) with nearest-zone
    fallback. Returns ``(gdf, primary_count, fallback_count)`` (CLAUDE.md
    fallback transparency)."""
    if zones.crs != gdf.crs:
        zones = zones.to_crs(gdf.crs)
    pts = gdf.copy()
    pts["geometry"] = gdf.geometry.centroid
    joined = gpd.sjoin(
        pts[["building_id", "geometry"]],
        zones[["commune_id", "geometry"]],
        how="left", predicate="within",
    ).drop(columns=["index_right"]).drop_duplicates("building_id")
    primary_count = int(joined["commune_id"].notna().sum())

    missing = joined[joined["commune_id"].isna()][["building_id", "geometry"]]
    fallback_count = int(len(missing))
    if fallback_count:
        near = gpd.sjoin_nearest(
            missing, zones[["commune_id", "geometry"]], how="left",
        ).drop_duplicates("building_id")[["building_id", "commune_id"]]
        fill = dict(zip(near["building_id"], near["commune_id"]))
        joined.loc[joined["commune_id"].isna(), "commune_id"] = \
            joined.loc[joined["commune_id"].isna(), "building_id"].map(fill)

    out = gdf.merge(
        joined[["building_id", "commune_id"]], on="building_id", how="left"
    )
    return gpd.GeoDataFrame(out, geometry="geometry", crs=gdf.crs), \
        primary_count, fallback_count


def configure(context):
    context.config("data_path")
    context.config(
        "building_potentials_path",
        "braunschweig/buildings/building_activity_potentials.parquet",
    )
    context.stage("data.spatial.municipalities")


def execute(context) -> gpd.GeoDataFrame:
    path = os.path.join(context.config("data_path"),
                        context.config("building_potentials_path"))
    gdf = load_potentials(path)
    zones = context.stage("data.spatial.municipalities")
    gdf, primary, fallback = assign_commune(gdf, zones)
    total = primary + fallback
    share = (fallback / total) if total else 0.0
    print(
        "[braunschweig.data.building_potentials] %d buildings; "
        "commune join primary %d (%.1f%%), fallback nearest %d (%.1f%%)"
        % (len(gdf), primary, 100.0 * primary / total if total else 0.0,
           fallback, 100.0 * share)
    )
    return gdf


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("building_potentials_path"))
    if not os.path.exists(path):
        raise RuntimeError(
            "building_activity_potentials.parquet missing: run "
            "scripts/import_building_activity_potentials.py"
        )
    return os.path.getsize(path)
