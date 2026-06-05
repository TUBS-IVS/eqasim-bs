"""Cordon polygon for the cross-cordon external-demand module.

The cordon is the dissolved union of the in-scope (ZGB) municipality polygons,
grown by a configurable buffer. The clipped MATSim network is bounded by this
polygon; links crossing its boundary become cordon gates; activities outside it
are represented as eqasim ``outside`` activities.

Region-neutral: pass any set of municipality polygons. The CRS must be a metric
projection (EPSG:25832 for the ZGB region) so the buffer is in metres.

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import math

import geopandas as gpd
from shapely.geometry.base import BaseGeometry


def build_cordon_polygon(municipalities: gpd.GeoDataFrame, buffer_m: float) -> BaseGeometry:
    """Dissolve municipality polygons into one cordon polygon, buffered by metres.

    Args:
        municipalities: polygons of the in-scope region (metric CRS).
        buffer_m: outward buffer in metres (>= 0); 0 returns the raw dissolved union.

    Returns:
        A single (Multi)Polygon: the dissolved region grown by ``buffer_m``.

    Raises:
        ValueError: if ``municipalities`` is empty or ``buffer_m`` is negative.
    """
    if municipalities is None or len(municipalities) == 0:
        raise ValueError("build_cordon_polygon: empty municipalities")
    if buffer_m < 0:
        raise ValueError("build_cordon_polygon: buffer_m must be >= 0")
    dissolved = municipalities.geometry.union_all()
    return dissolved if buffer_m == 0 else dissolved.buffer(buffer_m)


def buffer_m_from_fraction(municipalities: gpd.GeoDataFrame, fraction: float) -> float:
    """Convert a fractional margin into a buffer distance in metres.

    The distance is ``fraction`` times the half-diagonal of the dissolved bounding
    box, giving a scale-correct ~N% margin around the region regardless of its
    absolute size.

    Raises:
        ValueError: if ``fraction`` is negative.
    """
    if fraction < 0:
        raise ValueError("buffer_m_from_fraction: fraction must be >= 0")
    minx, miny, maxx, maxy = municipalities.geometry.total_bounds
    half_diag = 0.5 * math.hypot(maxx - minx, maxy - miny)
    return fraction * half_diag
