"""Deterministic area-proportional grid seeding for ATKIS landuse polygons (issue #262).

Turns ATKIS ``Freizeitanlage``/``Sportanlage``/``kulturell nutzbare Anlage``
landuse polygons (layers ``ln_freiluftundnaherholung``, ``ln_sportanlage``,
``ln_kulturundunterhaltung``) into point candidates for the SrV-grounded
leisure location types (``leisure_outdoor``, ``leisure_sports``,
``leisure_culture``). A large polygon should contribute multiple candidates
roughly proportional to its area (so downstream gravity/placement logic sees
"more area -> more opportunity"), while a polygon too small to catch a grid
node still contributes exactly one candidate carrying its own true area.

CRS expectation: the input GeoDataFrame MUST be in a metric, projected CRS
(EPSG:25832 / ETRS89 / UTM zone 32N is the project standard) because
``spacing_m`` and ``represented_area_m2`` are both metric quantities computed
directly from CRS coordinates; calling this on WGS84 (EPSG:4326) input would
silently produce a degenerate/near-empty grid or wildly wrong areas.

Determinism and fragmentation invariance: grid node coordinates are ABSOLUTE
integer multiples of ``spacing_m`` in the input CRS (``x = i * spacing_m``,
``y = j * spacing_m``), fixed to the CRS origin and NOT anchored to the data's
bounding box. This is what makes the seeding deterministic (no RNG anywhere in
this module) and fragmentation-invariant: splitting one polygon into several
adjacent pieces (e.g. because ATKIS delivered it as multiple sub-polygons, or a
later processing step clips it) never changes which grid nodes fall inside the
union of the pieces, so the total candidate set for the same real-world area is
identical whether it arrives as one polygon or many. Anchoring the grid to
per-call data bounds instead would break this invariance, because the anchor
(and therefore every node coordinate) would shift depending on what else is in
the frame.

Qualification (boundary coincidence): the invariance guarantee above holds
EXCEPT when a shared fragment edge lies EXACTLY on a grid line (i.e. exactly at
an integer multiple of ``spacing_m``). ``shapely.contains`` excludes boundary
points by definition (a node exactly on a polygon's edge is not "contained" by
either side of the split), so a node sitting precisely on such a shared edge is
excluded from BOTH fragments and the split candidate count can fall short of
the whole-polygon count for that specific coincidence (e.g. splitting
``box(100, 100, 560, 560)`` at ``x=300`` with ``spacing_m=150`` drops the 3
nodes at ``x=300`` -> 6 points instead of 9). This predicate is intentional and
must not be changed (a strict "contains" boundary rule is required upstream);
the coincidence is accepted here because real ATKIS/cadastral parcel
boundaries are arbitrary reals with effectively zero probability of landing
exactly on a 150 m (or any other configured) grid line -- only synthetic,
exactly-gridded test/edge-case input can trigger it.
"""
from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import shapely

# ATKIS landuse layer name -> SrV-grounded leisure location-type category.
# Used by downstream candidate-assembly stages to label seeded points; kept
# here (next to the seeding logic that consumes the same layer names) so the
# mapping cannot drift out of sync with what this module actually seeds.
LANDUSE_LAYER_TO_CATEGORY = {
    "ln_freiluftundnaherholung": "leisure_outdoor",
    "ln_sportanlage": "leisure_sports",
    "ln_kulturundunterhaltung": "leisure_culture",
}


def _grid_nodes_in_bounds(minx, miny, maxx, maxy, spacing_m):
    """Return the absolute grid node coordinates (arrays of x, y) whose
    bounding box lies within ``[minx, maxx] x [miny, maxy]``.

    Nodes are ``i * spacing_m`` / ``j * spacing_m`` for integer ``i``, ``j``
    (the CRS origin, not the polygon bounds, anchors the grid -- see the
    module docstring on fragmentation invariance). ``i`` ranges over
    ``ceil(minx / spacing_m) .. floor(maxx / spacing_m)`` inclusive, same for
    ``j``; this is a superset of the nodes that can possibly lie inside the
    polygon, so it is safe to filter with an exact ``contains`` check next.
    """
    i_min = math.ceil(minx / spacing_m)
    i_max = math.floor(maxx / spacing_m)
    j_min = math.ceil(miny / spacing_m)
    j_max = math.floor(maxy / spacing_m)
    if i_min > i_max or j_min > j_max:
        return np.empty(0), np.empty(0)
    xs = np.arange(i_min, i_max + 1) * spacing_m
    ys = np.arange(j_min, j_max + 1) * spacing_m
    grid_x, grid_y = np.meshgrid(xs, ys)
    return grid_x.ravel(), grid_y.ravel()


def grid_seed_polygons(gdf, spacing_m):
    """Seed each polygon in ``gdf`` with area-proportional point candidates.

    For every input polygon (row order preserved), enumerate the absolute grid
    nodes (``x = i * spacing_m``, ``y = j * spacing_m``) falling within its
    bounding box, keep the ones the polygon actually ``contains`` (a node
    exactly on the boundary does NOT count as contained), and emit one output
    point per contained node with ``represented_area_m2 = spacing_m ** 2``.
    Polygons that catch zero grid nodes (too small relative to ``spacing_m``)
    instead get exactly one output point at their ``representative_point()``
    with ``represented_area_m2 = polygon.area`` (their true area, not the grid
    cell area), so no polygon -- however small -- is silently dropped.

    Output rows are ordered by input row, then within a polygon by ascending
    y then ascending x (a fixed, documented tie-break so results are byte-
    identical across repeated calls on the same input; see
    :func:`_grid_nodes_in_bounds`). No random process is involved anywhere in
    this function.

    Parameters
    ----------
    gdf : geopandas.GeoDataFrame
        Polygons with a ``layer`` column (ATKIS layer name) and a metric,
        projected CRS (EPSG:25832 expected; see module docstring).
    spacing_m : float
        Grid spacing in meters. Must be strictly positive.

    Returns
    -------
    geopandas.GeoDataFrame
        Columns ``[layer, represented_area_m2, geometry]`` (``geometry`` is
        ``Point``), same CRS as ``gdf``, index reset to a fresh RangeIndex.
    """
    spacing_m = float(spacing_m)
    if spacing_m <= 0.0:
        raise ValueError(
            "[landuse_candidates] spacing_m must be strictly positive, got %r." % spacing_m)
    if "layer" not in gdf.columns:
        raise ValueError(
            "[landuse_candidates] input GeoDataFrame is missing the required 'layer' column.")

    layers = []
    areas = []
    points = []
    n_grid_points = 0
    n_representative_points = 0

    for layer, polygon in zip(gdf["layer"], gdf.geometry):
        minx, miny, maxx, maxy = polygon.bounds
        grid_x, grid_y = _grid_nodes_in_bounds(minx, miny, maxx, maxy, spacing_m)
        if grid_x.size > 0:
            candidate_points = shapely.points(grid_x, grid_y)
            inside_mask = shapely.contains(polygon, candidate_points)
        else:
            inside_mask = np.empty(0, dtype=bool)

        if inside_mask.any():
            # Fixed, documented order: ascending y, then ascending x (ties
            # only possible via floating point equality on grid multiples,
            # which np.lexsort resolves deterministically).
            hit_x = grid_x[inside_mask]
            hit_y = grid_y[inside_mask]
            order = np.lexsort((hit_x, hit_y))
            for x, y in zip(hit_x[order], hit_y[order]):
                layers.append(layer)
                areas.append(spacing_m ** 2)
                points.append(shapely.Point(x, y))
            n_grid_points += int(inside_mask.sum())
        else:
            layers.append(layer)
            areas.append(polygon.area)
            points.append(polygon.representative_point())
            n_representative_points += 1

    out = gpd.GeoDataFrame(
        {"layer": layers, "represented_area_m2": areas},
        geometry=points,
        crs=gdf.crs,
    ).reset_index(drop=True)

    print("[landuse_candidates] %d polygons -> %d grid points + %d representative points "
          "(spacing %s m)" % (len(gdf), n_grid_points, n_representative_points, spacing_m))

    return out
