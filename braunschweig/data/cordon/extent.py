"""Write the cordon extent as a single-polygon GeoPackage for eqasim's cutter.

``org.eqasim.core.scenario.cutter.RunScenarioCutter`` takes ``--extent-path``: a
shapefile/GeoPackage that must contain a SINGLE polygon feature, compact and
WITHOUT holes (eqasim-java/docs/cutting.md). This module turns our cordon polygon
(dissolved RVB + buffer, see ``braunschweig.data.spatial.cordon``) into exactly
that, so the build-then-cut flow can hand the extent to the cutter.

See ``docs/codebase/eqasim-java-outside-mechanism.md`` and
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


def single_polygon_without_holes(geom: BaseGeometry) -> Polygon:
    """Reduce a (Multi)Polygon to one compact polygon without interior holes.

    The cutter requires a single, hole-free polygon: pick the largest part of a
    MultiPolygon and keep only its exterior ring. (A hole would wrongly mark an
    interior area as "outside" the cordon.)
    """
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)
    if not isinstance(geom, Polygon):
        raise ValueError(f"single_polygon_without_holes: expected a polygon, got {geom.geom_type}")
    return Polygon(geom.exterior)


def write_cordon_extent(out_path: str, cordon_polygon: BaseGeometry,
                        crs: str = "EPSG:25832") -> str:
    """Write ``cordon_polygon`` as a single-feature, hole-free GeoPackage and
    return the path (suitable for ``RunScenarioCutter --extent-path``)."""
    poly = single_polygon_without_holes(cordon_polygon)
    gdf = gpd.GeoDataFrame({"name": ["cordon"]}, geometry=[poly], crs=crs)
    gdf.to_file(out_path, driver="GPKG")
    return out_path
