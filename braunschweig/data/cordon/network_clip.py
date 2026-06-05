"""OSM-clip geometry for the cross-cordon network build.

The simulation network is clipped (osmosis ``--bounding-polygon``) to the dissolved
in-scope municipalities. For the cross-cordon feature the network must extend
*beyond* the cordon so motorways / Bundesstrassen cross it and the eqasim scenario
cutter can create real boundary links (gates) and ``outside`` activities. This
module enlarges only the OSM clip polygon (the network extent) by a source buffer;
it does NOT change the population scope (which stays the in-scope municipalities).

Flag-gated: with the cordon disabled (or a zero buffer) the returned geometry is
the plain dissolved union, so the network build is byte-identical to the legacy
clip. Region-neutral; expects a metric CRS (EPSG:25832 for ZGB).

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd

from braunschweig.data.spatial.cordon import build_cordon_polygon


def osm_clip_geometry(municipalities: gpd.GeoDataFrame, cordon_enabled: bool,
                      source_buffer_m: float) -> gpd.GeoDataFrame:
    """Return the single-polygon GeoDataFrame to clip the OSM network by.

    Args:
        municipalities: in-scope municipality polygons (metric CRS).
        cordon_enabled: master cross-cordon flag. When False, the legacy clip
            (dissolved union, no buffer) is returned regardless of the buffer.
        source_buffer_m: outward buffer in metres applied when the cordon is
            enabled, so the built network reaches beyond the cordon. <= 0 is a
            no-op (returns the dissolved union).

    Returns:
        A one-row GeoDataFrame ``[geometry]`` in ``municipalities.crs`` holding the
        (possibly buffered) dissolved region, ready for
        :func:`data.osm.cleaned.write_poly`.
    """
    buffer_m = float(source_buffer_m) if (cordon_enabled and source_buffer_m) else 0.0
    if buffer_m < 0:
        buffer_m = 0.0
    geom = build_cordon_polygon(municipalities, buffer_m)
    return gpd.GeoDataFrame(geometry=[geom], crs=municipalities.crs)
