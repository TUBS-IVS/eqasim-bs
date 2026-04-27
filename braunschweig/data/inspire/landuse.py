"""
INSPIRE 100m landuse spatial-prior stage (TASK-012).

Reads a preprocessed Copernicus Land Monitoring Service tile (CLC+
Backbone or CLC 2018 reclassified to a Lower-Saxony bounding box) as a
GeoParquet of 100 m raster cells, each carrying a coarse activity-class
that the secondary-location sampler can use as a spatial prior.

The Copernicus product itself requires a registered download (CC-BY 4.0
with attribution); we therefore expect the user to fetch the tile
manually following the instructions in
``eqasim-data/DOWNLOAD_CHECKLIST_BS.md`` and place the resulting
parquet at ``braunschweig.inspire_landuse_path``.

Output schema
-------------
    cell_id    (int / str)        unique INSPIRE 100m grid id
    class      (str / category)   one of
                                  ``residential``, ``industrial``,
                                  ``retail``, ``agriculture``, ``other``
    geometry   (Polygon, EPSG:3035)

Behaviour
---------
- When the configured path is missing on disk the stage returns an
  empty GeoDataFrame and prints a notice (so default pipelines stay
  runnable). Downstream consumers must guard via the
  ``braunschweig.use_landuse_prior`` flag.
"""

from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd

VALID_CLASSES = ("residential", "industrial", "retail", "agriculture", "other")


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.inspire_landuse_path",
        "braunschweig/preprocessed/inspire_landuse.parquet",
    )
    context.config("braunschweig.use_landuse_prior", False)


def _resolve(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.inspire_landuse_path"),
    )


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"cell_id": pd.Series(dtype="object"),
         "class": pd.Series(dtype="object")},
        geometry=gpd.GeoSeries([], crs="EPSG:3035"),
    )


def execute(context) -> gpd.GeoDataFrame:
    if not bool(context.config("braunschweig.use_landuse_prior")):
        # Feature flag off — return empty frame, log nothing noisy.
        return _empty()

    path = _resolve(context)
    if not os.path.exists(path):
        print(
            "[braunschweig.data.inspire.landuse] {} not found — returning "
            "empty frame. Provide a Copernicus 100 m tile per "
            "DOWNLOAD_CHECKLIST_BS.md to enable the spatial prior."
            .format(path)
        )
        return _empty()

    gdf = gpd.read_parquet(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:3035")
    elif gdf.crs.to_epsg() != 3035:
        gdf = gdf.to_crs("EPSG:3035")

    required = {"cell_id", "class"}
    missing = required - set(gdf.columns)
    if missing:
        raise RuntimeError(
            f"[braunschweig.data.inspire.landuse] {path} missing columns: "
            f"{sorted(missing)}"
        )

    gdf["class"] = gdf["class"].astype(str).str.lower()
    invalid = sorted(set(gdf["class"].unique()) - set(VALID_CLASSES))
    if invalid:
        raise RuntimeError(
            f"[braunschweig.data.inspire.landuse] Unknown class labels in "
            f"{path}: {invalid}. Valid set: {VALID_CLASSES}"
        )

    print(
        "[braunschweig.data.inspire.landuse] {:,} 100 m cells, classes: {}"
        .format(len(gdf), sorted(gdf["class"].unique()))
    )
    return gdf


def validate(context):
    if not bool(context.config("braunschweig.use_landuse_prior")):
        return 0
    path = _resolve(context)
    return os.path.getsize(path) if os.path.exists(path) else 0
