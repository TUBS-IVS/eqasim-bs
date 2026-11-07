"""synpp data stage: building-level activity potentials.

Reads the ``buildings`` layer of the redistribution GeoPackage
(``08_redistribution.gpkg``, config ``building_potentials_path``) directly and
maps it onto the internal schema the location-choice consumers use. The
``pois`` and ``zones`` layers of the file are not used. ``commune_id`` is
attached by a representative-point spatial join to the VG250 Gemeinde polygons
(mirroring ``braunschweig.data.buildings``). The footprint polygon stays the
active geometry so downstream consumers can point-in-polygon join candidates
onto it.

Source -> internal columns (the "other" potential is ``value_errands``, owner
decision 2026-11-07):

    assigned_Workers -> potential_work, assigned_School -> potential_school,
    assigned_University -> potential_university,
    assigned_Kindergarten -> potential_kindergarten,
    assigned_Retail_Daily -> potential_retail_daily,
    assigned_Retail_Non_Daily -> potential_retail_non_daily,
    assigned_Leisure -> potential_leisure, value_errands -> potential_errands,
    value_business / sports / meetup / lessons -> potential_<same> (stored, not
    consumed yet).

``building_id`` is a contiguous integer 0..n-1 assigned after sorting by the
source id (deterministic; candidate ids ``sec_b_<building_id>`` must not carry
the ``/`` of OSM ids); the source id is kept as ``source_building_id``.

Stage name: ``braunschweig.data.building_potentials``.
"""
from __future__ import annotations

import os
import re

import geopandas as gpd
import numpy as np
import pandas as pd

CRS_METRIC = "EPSG:25832"
SOURCE_LAYER = "buildings"

#: source column -> internal potential column
SOURCE_TO_POTENTIAL = {
    "assigned_Workers": "potential_work",
    "assigned_School": "potential_school",
    "assigned_University": "potential_university",
    "assigned_Kindergarten": "potential_kindergarten",
    "assigned_Retail_Daily": "potential_retail_daily",
    "assigned_Retail_Non_Daily": "potential_retail_non_daily",
    "assigned_Leisure": "potential_leisure",
    "value_errands": "potential_errands",
    "value_business": "potential_business",
    "value_sports": "potential_sports",
    "value_meetup": "potential_meetup",
    "value_lessons": "potential_lessons",
}
POTENTIAL_COLUMNS = list(SOURCE_TO_POTENTIAL.values())

#: source columns carried over unchanged (zone_no may be NaN)
PASSTHROUGH_COLUMNS = ["zone_no", "label_en", "value_assumed", "floor_area_m2", "storeys"]
REQUIRED_SOURCE_COLUMNS = (["building_id", "bosserhof_class"] + list(SOURCE_TO_POTENTIAL)
                           + PASSTHROUGH_COLUMNS)


def normalise_bosserhof_class(classes: pd.Series) -> pd.Series:
    """Lowercase, every run of non-alphanumeric characters -> one space, stripped.

    Matches the keys of ``bosserhof_class_to_location_category.csv``.
    """
    return (classes.astype(str).str.lower()
            .str.replace(r"[^0-9a-z]+", " ", regex=True).str.strip())


def load_potentials(path: str) -> gpd.GeoDataFrame:
    """Load, validate and map the ``buildings`` layer (fail fast on bad input)."""
    if not os.path.exists(path):
        raise ValueError(
            "[braunschweig.data.building_potentials] redistribution GeoPackage not "
            "found: %s (config building_potentials_path)" % path)
    import pyogrio
    layers = [name for name, _kind in pyogrio.list_layers(path)]
    if SOURCE_LAYER not in layers:
        raise ValueError(
            "[braunschweig.data.building_potentials] layer '%s' not found in %s; "
            "available layers: %s" % (SOURCE_LAYER, path, layers))
    gdf = gpd.read_file(path, layer=SOURCE_LAYER)

    if gdf.crs is None:
        raise ValueError(
            "[braunschweig.data.building_potentials] %s has no CRS; expected %s "
            "(an unknown CRS is not guessed)" % (path, CRS_METRIC))
    if gdf.crs.to_epsg() != 25832:
        raise ValueError(
            "[braunschweig.data.building_potentials] %s is in CRS %s; expected %s. "
            "Re-export the layer in %s (no silent reprojection)."
            % (path, gdf.crs, CRS_METRIC, CRS_METRIC))

    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in gdf.columns]
    if missing:
        raise ValueError(
            "[braunschweig.data.building_potentials] layer '%s' of %s is missing "
            "columns %s" % (SOURCE_LAYER, path, missing))

    for col in list(SOURCE_TO_POTENTIAL) + ["floor_area_m2", "storeys"]:
        values = pd.to_numeric(gdf[col], errors="raise").astype(float)
        nan_count = int(values.isna().sum())
        if nan_count:
            raise ValueError(
                "[braunschweig.data.building_potentials] %d NaN values in %s"
                % (nan_count, col))
        negative_count = int((values < 0).sum())
        if negative_count:
            raise ValueError(
                "[braunschweig.data.building_potentials] %d negative values in %s"
                % (negative_count, col))
        gdf[col] = values
    # A missing class must not become the string "nan" (astype(str) in
    # normalise_bosserhof_class): no class mapping resolves it, so the building
    # would silently drop out of every location category. Fail fast instead.
    bad_class = gdf["bosserhof_class"].isna() | gdf["bosserhof_class"].astype(str).str.strip().eq("")
    if int(bad_class.sum()):
        raise ValueError(
            "[braunschweig.data.building_potentials] %d buildings have an empty or "
            "NaN bosserhof_class in layer '%s' of %s"
            % (int(bad_class.sum()), SOURCE_LAYER, path))
    if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
        raise ValueError(
            "[braunschweig.data.building_potentials] empty/missing geometry in "
            "layer '%s' of %s" % (SOURCE_LAYER, path))
    if gdf["building_id"].isna().any() or not gdf["building_id"].is_unique:
        raise ValueError(
            "[braunschweig.data.building_potentials] source building_id must be "
            "unique and non-empty (%d duplicates, %d missing)"
            % (int(gdf["building_id"].duplicated().sum()),
               int(gdf["building_id"].isna().sum())))

    gdf = gdf.sort_values("building_id", kind="stable").reset_index(drop=True)
    out = gpd.GeoDataFrame(
        {
            "building_id": np.arange(len(gdf), dtype=np.int64),
            "source_building_id": gdf["building_id"].astype(str).values,
            "bosserhof_class_clean": normalise_bosserhof_class(gdf["bosserhof_class"]).values,
            **{internal: gdf[source].values for source, internal in SOURCE_TO_POTENTIAL.items()},
            **{c: gdf[c].values for c in PASSTHROUGH_COLUMNS},
        },
        geometry=gdf.geometry.values, crs=CRS_METRIC,
    )
    print(
        "[braunschweig.data.building_potentials] loaded %d buildings from %s; "
        "zone_no NaN %d; potentials nonzero/sum: %s"
        % (len(out), os.path.basename(path), int(out["zone_no"].isna().sum()),
           ", ".join("%s %d/%.0f" % (c, int((out[c] > 0).sum()), float(out[c].sum()))
                     for c in POTENTIAL_COLUMNS)))
    return out


def assign_commune(gdf: gpd.GeoDataFrame, zones: gpd.GeoDataFrame):
    """Attach ``commune_id`` by point-in-polygon (primary) with nearest-zone
    fallback. Returns ``(gdf, primary_count, fallback_count)`` (CLAUDE.md
    fallback transparency).

    The join point is ``representative_point()`` rather than the centroid: for
    concave or multi-part footprints the centroid can fall outside the polygon,
    which would force an unnecessary nearest-zone fallback. ``representative_point``
    is guaranteed to lie inside the geometry."""
    if zones.crs != gdf.crs:
        zones = zones.to_crs(gdf.crs)
    pts = gdf.copy()
    pts["geometry"] = gdf.geometry.representative_point()
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
        "braunschweig/buildings/08_redistribution.gpkg",
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
            "redistribution GeoPackage missing: %s (config building_potentials_path, "
            "default braunschweig/buildings/08_redistribution.gpkg)" % path
        )
    return os.path.getsize(path)
