"""Georeferenced zone outlines of the Braunschweig ParkGO annex map (one-off curation, parking cost zones v2, #436).

Not part of the pipeline. Input: the north-up rendering of the ParkGO annex map ('Anlage zur ParkGO', page 3 of
2_08_Parkgebuehrenordnung_2025.pdf; raw_sources/bs_2_08_annex_north_up.png, 1477 x 1155 px, gitignored) and the
affine of the v1 curation (parkgo_annex_affine.json: EPSG:25832 -> pixels, outline RMS 11.1 m, BgA check points
26.8 m RMS). The annex draws the zones Ia, Ib and II as faces enclosed by thick black lines.

Method: the zone lines are the dark pixels (max RGB < ``LINE_MAX_RGB``) after a 3 x 3 opening, kept where a connected
part has at least ``LINE_MINIMUM_PX`` pixels (the v1 line mask of the fit); the faces are the connected parts of the
complement of the lines grown by ``LINE_DILATION_PX``. Each zone takes the faces that contain its seed pixels
(``ZONE_SEEDS``: zone Ib is the Wallring face plus the small face the 'Zone I b' arrow points at; zone II includes the
triangle the arrow line cuts off it), every line pixel goes to the nearest face, so neighbouring zones share an
outline in the middle of the drawn line. Masks -> row-run polygons -> inverse affine -> EPSG:25832, simplified by
``SIMPLIFY_M``. The check points of the affine are re-projected and their residuals printed as a self-check.

Use: ``--reference-outline`` of ``scripts/build_parking_zones_from_osm.py --regulation`` for Braunschweig and the
tariff-zone assignment of the rule-based cores in ``assemble_parking_zones.py`` (spec amendment A2: a plausibility
check, not a geometry source). Output: GeoJSON (EPSG:4326) with one feature per zone (``zone`` ia, ib, ii).

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/extract_parkgo_annex_zones.py \
        --image eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_north_up.png \
        --affine scripts/curation/parking_zones_2026/parkgo_annex_affine.json \
        --out eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_zones_georeferenced.geojson
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.affinity import affine_transform
from shapely.geometry import box
from shapely.ops import unary_union

#: Darkness threshold of the zone lines. The v1 line mask of the affine fit used 60 on its rendering; on the
#: rendering in raw_sources (sha256 a38fdec988364af8...) the outlines close only from 100 on (tested 60 to 120 on
#: 2026-09-30: below 100 the Ia, Ib and II faces merge with the outside), and at 100 the three zone faces differ
#: from the v1 faces by -0.7 % (Ia), -1.5 % (Ib) and -0.3 % (II) of their pixels.
LINE_MAX_RGB = 100
LINE_MINIMUM_PX = 1500
LINE_DILATION_PX = 2
SIMPLIFY_M = 1.0
#: Seed pixels (x, y) of the north-up image per zone; each seed selects the face that contains it.
ZONE_SEEDS = {
    "ia": ((250, 420), (310, 600)),
    "ib": ((230, 110), (85, 400), (470, 480)),
    "ii": ((900, 700), (560, 400)),
}


def line_mask(rgb: np.ndarray) -> np.ndarray:
    dark = rgb.max(axis=2) < LINE_MAX_RGB
    thick = ndimage.binary_opening(dark, structure=np.ones((3, 3)))
    labels, count = ndimage.label(thick)
    sizes = ndimage.sum(thick, labels, range(1, count + 1))
    return np.isin(labels, 1 + np.flatnonzero(sizes >= LINE_MINIMUM_PX))


def mask_to_polygon(mask: np.ndarray):
    rects = []
    for row in range(mask.shape[0]):
        cols = np.flatnonzero(mask[row])
        if not len(cols):
            continue
        breaks = np.flatnonzero(np.diff(cols) > 1)
        starts = np.concatenate([[cols[0]], cols[breaks + 1]])
        ends = np.concatenate([cols[breaks], [cols[-1]]])
        rects += [box(start, row, end + 1, row + 1) for start, end in zip(starts, ends)]
    return unary_union(rects)


def zone_masks(rgb: np.ndarray) -> dict:
    """Zone -> boolean mask covering its faces and the nearer half of the lines around them."""
    lines = ndimage.binary_dilation(line_mask(rgb), iterations=LINE_DILATION_PX)
    faces, _ = ndimage.label(~lines)
    zone_of_face = {}
    for zone, seeds in ZONE_SEEDS.items():
        for x, y in seeds:
            face = int(faces[y, x])
            if face == 0:
                raise SystemExit(f"seed {(x, y)} of zone {zone} lies on a zone line")
            if zone_of_face.get(face, zone) != zone:
                raise SystemExit(f"face {face} is claimed by zones {zone_of_face[face]} and {zone}")
            zone_of_face[face] = zone
    height, width = faces.shape
    for face in zone_of_face:
        ys, xs = np.nonzero(faces == face)
        if ys.min() == 0 or xs.min() == 0 or ys.max() == height - 1 or xs.max() == width - 1:
            raise SystemExit(f"face {face} touches the image edge: a zone outline has a gap")
    # Every line pixel takes the label of the nearest face.
    _, (rows, cols) = ndimage.distance_transform_edt(faces == 0, return_indices=True)
    filled = faces[rows, cols]
    return {zone: np.isin(filled, [face for face, owner in zone_of_face.items() if owner == zone])
            for zone in ZONE_SEEDS}


def inverse_affine(fit: dict) -> list:
    """shapely affine_transform matrix (pixels -> EPSG:25832) of the forward fit (EPSG:25832 -> pixels)."""
    ax, ay = np.array(fit["affine_px"]), np.array(fit["affine_py"])
    forward = np.array([[ax[0], ax[1]], [ay[0], ay[1]]])
    inverse = np.linalg.inv(forward)
    offset = -inverse @ np.array([ax[2], ay[2]])
    return [inverse[0, 0], inverse[0, 1], inverse[1, 0], inverse[1, 1], offset[0], offset[1]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image", required=True, help="north-up rendering of the annex (1477 x 1155 px)")
    parser.add_argument("--affine", required=True, help="parkgo_annex_affine.json")
    parser.add_argument("--out", required=True, help="GeoJSON to write (refused if it exists)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} exists; choose a new file name (curation inputs are never overwritten)")
    image = Path(args.image)
    rgb = np.asarray(Image.open(image).convert("RGB")).astype(int)
    fit = json.loads(Path(args.affine).read_text(encoding="utf-8"))
    ax, ay = np.array(fit["affine_px"]), np.array(fit["affine_py"])
    residuals = []
    for point in fit["check_points"]:
        px = ax[0] * point["E"] + ax[1] * point["N"] + ax[2]
        py = ay[0] * point["E"] + ay[1] * point["N"] + ay[2]
        residuals.append(float(np.hypot(px - point["px"], py - point["py"]) * fit["metres_per_px"]))
        print(f"check point {point['label']}: residual {residuals[-1]:.1f} m")
    print(f"check-point RMS {np.sqrt(np.mean(np.square(residuals))):.1f} m")
    matrix = inverse_affine(fit)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    rows = []
    for zone, mask in zone_masks(rgb).items():
        world = affine_transform(mask_to_polygon(mask), matrix).buffer(0).simplify(SIMPLIFY_M, preserve_topology=True)
        rows.append({"zone": zone, "pixels": int(mask.sum()), "area_m2": round(float(world.area), 1),
                     "image_sha256": digest, "outline_rms_m": round(float(fit["outline_rms_m"]), 1),
                     "check_point_rms_m": round(float(np.sqrt(np.mean(np.square(residuals)))), 1),
                     "geometry": world})
    zones = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:25832")
    overlaps = [(a, b, zones.geometry[i].intersection(zones.geometry[j]).area) for i, a in enumerate(zones["zone"])
                for j, b in enumerate(zones["zone"]) if i < j]
    zones.to_crs(4326).to_file(out, driver="GeoJSON")
    print(f"written {out}: " + ", ".join(f"{row['zone']} {row['area_m2']:.0f} m2" for row in rows)
          + "; pairwise overlaps " + ", ".join(f"{a}/{b} {area:.1f} m2" for a, b, area in overlaps)
          + f"; image sha256 {digest[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
