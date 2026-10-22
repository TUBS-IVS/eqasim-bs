"""Extract Braunschweig parking zones 1a and 1b from the city's georeferenced zone map (one-off curation, #249).

Not part of the pipeline. Input: the zone overview map raw_sources/bs_zone_map_1a_1b.jpg (City of Braunschweig,
image 20251126_Karte-Parkzone-Innenstadt-a-b-2025-01, gitignored) and the affine of georeference_zone_map.py.
Zone 1b is the green fill (G - R > 12 and G - B > 40, closed by 3 px, holes filled, specks < 400 px dropped).
Zone 1a is the region enclosed by the dotted outline: dark pixels (max RGB < 70) joined by a 4 px dilation, closed
along the lower map edge between x = 590 and 710 px where the outline leaves the map, flood-filled from an interior
seed, grown back by 4 px and cut by the green fill. Masks -> row-run polygons -> inverse affine -> EPSG:25832,
simplified by 1 m. Output: a two-feature GeoJSON (EPSG:4326) read by assemble_parking_zones.py.

Usage::

    python scripts/curation/parking_zones_2026/extract_braunschweig_zone_map.py \
        --image raw_sources/bs_zone_map_1a_1b.jpg --affine bs_zone_map_affine.json --out bs_zone_map_ia_ib.geojson
"""
from __future__ import annotations

import argparse
import json

import geopandas as gpd
import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.affinity import affine_transform
from shapely.geometry import box
from shapely.ops import unary_union

MAP_X_MAX_PX = 1280
DOT_DILATION_PX = 4
GREEN_CLOSING_PX = 3
MIN_PART_AREA_PX = 400
SIMPLIFY_M = 1.0
EXIT_LEFT_PX, EXIT_RIGHT_PX = 590, 710
SEED_ROW_COL = (720, 600)


def mask_to_polygon(mask: np.ndarray):
    rects = []
    for row in range(mask.shape[0]):
        cols = np.flatnonzero(mask[row])
        if not len(cols):
            continue
        breaks = np.flatnonzero(np.diff(cols) > 1)
        starts = np.concatenate([[cols[0]], cols[breaks + 1]])
        ends = np.concatenate([cols[breaks], [cols[-1]]])
        rects += [box(s, row, e + 1, row + 1) for s, e in zip(starts, ends)]
    return unary_union(rects)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image", required=True)
    parser.add_argument("--affine", required=True, help="JSON of georeference_zone_map.py")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    rgb = np.asarray(Image.open(args.image).convert("RGB")).astype(int)[:, :MAP_X_MAX_PX]
    height = rgb.shape[0]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    green = ((g - r) > 12) & ((g - b) > 40)
    green = ndimage.binary_closing(green, iterations=GREEN_CLOSING_PX)
    green = ndimage.binary_fill_holes(green)
    labels, n = ndimage.label(green)
    sizes = ndimage.sum(green, labels, range(1, n + 1))
    green = np.isin(labels, 1 + np.flatnonzero(sizes >= MIN_PART_AREA_PX))

    band = ndimage.binary_dilation(rgb.max(axis=2) < 70, iterations=DOT_DILATION_PX)
    band[height - 3:height, EXIT_LEFT_PX:EXIT_RIGHT_PX] = True
    if band[SEED_ROW_COL]:
        raise SystemExit("seed lies on the outline band; choose another interior pixel")
    labels, _ = ndimage.label(~band)
    interior = labels == labels[SEED_ROW_COL]
    if interior[0, :].any() or interior[:, 0].any() or interior[:, -1].any():
        raise SystemExit("flood fill leaked to the map edge; the dotted outline has a gap")
    interior = ndimage.binary_fill_holes(interior)
    zone_ia = ndimage.binary_dilation(interior, iterations=DOT_DILATION_PX) & ~green

    fit = json.load(open(args.affine, encoding="utf-8"))
    ax, ay = np.array(fit["affine_px"]), np.array(fit["affine_py"])
    forward = np.array([[ax[0], ax[1]], [ay[0], ay[1]]])
    inverse = np.linalg.inv(forward)
    offset = -inverse @ np.array([ax[2], ay[2]])
    matrix = [inverse[0, 0], inverse[0, 1], inverse[1, 0], inverse[1, 1], offset[0], offset[1]]
    rows = []
    for zone_id, mask in (("bs_zone_ia", zone_ia), ("bs_zone_ib", green)):
        world = affine_transform(mask_to_polygon(mask), matrix).buffer(0).simplify(SIMPLIFY_M, preserve_topology=True)
        rows.append({"zone_id": zone_id, "pixels": int(mask.sum()), "geometry": world})
    zones = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:25832")
    overlap = zones.geometry.iloc[0].intersection(zones.geometry.iloc[1]).area
    if overlap > 0:
        zones.loc[zones["zone_id"] == "bs_zone_ia", "geometry"] = zones.geometry.iloc[0].difference(zones.geometry.iloc[1])
    zones.to_crs(4326).to_file(args.out, driver="GeoJSON")
    print(f"written {args.out}: " + ", ".join(f"{row['zone_id']} {row['geometry'].area:.0f} m2" for row in rows)
          + f"; overlap trimmed {overlap:.1f} m2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
