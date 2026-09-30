"""Georeference a north-up municipal zone-map image on OSM street centrelines (one-off curation, issue #249).

Not part of the pipeline. Used once, on 2026-09-29, for the City of Braunschweig overview map of parking zones
1a/1b (raw_sources/bs_zone_map_1a_1b.jpg, 1920x1450 px, map area x < 1280 px); the resulting affine is recorded in
the digitising notes of bs_zone_ia/bs_zone_ib. Inputs are gitignored raw files of the curation session: the image
and the Overpass context features of Braunschweig (raw_overpass/03101000_context_features.geojson).

Phase 1 (global): an anisotropic similarity (tx, ty, sx, sy, theta) is fitted to rough control points, improved by a
small grid search and Nelder-Mead on the mean distance (truncated at 10 px) of OSM centreline samples (every 4 m)
to road-like pixels (locally brighter than a 25 px median, not dark text). Phase 2 (local): windows of 160 px are
matched by integer shifts (+-16 px, truncated distance 8 px, samples every 3 m); windows with a clear match become
control points of a robust affine fit (3-sigma rejection), iterated three times. Output: JSON with the affine
(px = a E + b N + c, py = d E + e N + f; EPSG:25832 -> image pixels), the control-point RMS and m/px.

Usage::

    python scripts/curation/parking_zones_2026/georeference_zone_map.py --image raw_sources/bs_zone_map_1a_1b.jpg \
        --context raw_overpass/03101000_context_features.geojson \
        --rough-control-points scripts/curation/parking_zones_2026/bs_zone_map_rough_control_points.json \
        --map-x-max 1280 --out bs_zone_map_affine.json
"""
from __future__ import annotations

import argparse
import itertools
import json

import geopandas as gpd
import numpy as np
from PIL import Image
from scipy import ndimage, optimize

GLOBAL_TRUNCATE_PX = 10.0
GLOBAL_SAMPLE_SPACING_M = 4.0
LOCAL_WINDOW_PX = 160
LOCAL_SEARCH_PX = 16
LOCAL_TRUNCATE_PX = 8.0
LOCAL_SAMPLE_SPACING_M = 3.0
LOCAL_MATCH_MAX_PX = 3.5
LOCAL_ITERATIONS = 3


def road_mask(rgb: np.ndarray, *, strict_text_threshold: bool) -> np.ndarray:
    brightness = rgb.mean(axis=2)
    background = ndimage.median_filter(brightness, size=25)
    not_text = rgb.max(axis=2) >= 150 if strict_text_threshold else rgb.max(axis=2) > 150
    road = ((brightness - background) > 5.0) & not_text
    return ndimage.binary_opening(road, iterations=1)


def centreline_samples(context_path, spacing_m: float) -> np.ndarray:
    context = gpd.read_file(context_path).to_crs(25832)
    streets = context[context["kind"].isin(["street_named", "street_fee"])
                      & context.geometry.geom_type.isin(["LineString", "MultiLineString"])]
    samples = []
    for geometry in streets.geometry:
        for line in getattr(geometry, "geoms", [geometry]):
            for fraction in np.linspace(0, 1, max(2, int(line.length // spacing_m))):
                point = line.interpolate(fraction, normalized=True)
                samples.append((point.x, point.y))
    return np.array(samples)


def global_fit(rgb, context_path, rough_cps):
    distance = ndimage.distance_transform_edt(~road_mask(rgb, strict_text_threshold=True))
    height, width = distance.shape
    cps = np.array(rough_cps, dtype=float)
    e0, n0 = cps[:, 2].mean(), cps[:, 3].mean()
    samples = centreline_samples(context_path, GLOBAL_SAMPLE_SPACING_M)
    u, v = samples[:, 0] - e0, -(samples[:, 1] - n0)

    def transform(params, uu, vv):
        tx, ty, sx, sy, t = params
        return tx + sx * (np.cos(t) * uu - np.sin(t) * vv), ty + sy * (np.sin(t) * uu + np.cos(t) * vv)

    def cost(params):
        px, py = transform(params, u, v)
        inside = (px >= 0) & (px < width - 1) & (py >= 0) & (py < height - 1)
        if inside.sum() < 2000:
            return 1e6
        values = ndimage.map_coordinates(distance, [py[inside], px[inside]], order=1)
        return float(np.minimum(values, GLOBAL_TRUNCATE_PX).mean())

    cu, cv = cps[:, 2] - e0, -(cps[:, 3] - n0)

    def cp_residual(params):
        px, py = transform(params, cu, cv)
        return np.concatenate([px - cps[:, 0], py - cps[:, 1]])

    initial = optimize.least_squares(cp_residual, [cps[:, 0].mean(), cps[:, 1].mean(), 0.68, 0.68, 0.0]).x
    best, best_cost = initial, cost(initial)
    for dtx, dty, ds, dt in itertools.product(range(-15, 16, 5), range(-15, 16, 5), (-0.02, 0.0, 0.02),
                                              np.radians([-1.5, 0, 1.5])):
        trial = initial + np.array([dtx, dty, initial[2] * ds, initial[3] * ds, dt])
        trial_cost = cost(trial)
        if trial_cost < best_cost:
            best, best_cost = trial, trial_cost
    steps = np.array([2.0, 2.0, 0.005, 0.005, np.radians(0.3)])
    result = optimize.minimize(lambda d: cost(best + d * steps), np.zeros(5), method="Nelder-Mead",
                               options={"maxiter": 4000, "xatol": 1e-3, "fatol": 1e-5})
    params = best + result.x * steps
    return params, e0, n0, cost(params)


def local_fit(rgb, context_path, params, e0, n0):
    distance = ndimage.distance_transform_edt(~road_mask(rgb, strict_text_threshold=False))
    height, width = distance.shape
    pts = centreline_samples(context_path, LOCAL_SAMPLE_SPACING_M)
    tx, ty, sx, sy, t = params
    u, v = pts[:, 0] - e0, -(pts[:, 1] - n0)
    px = tx + sx * (np.cos(t) * u - np.sin(t) * v)
    py = ty + sy * (np.sin(t) * u + np.cos(t) * v)
    design = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
    ax, *_ = np.linalg.lstsq(design, px, rcond=None)
    ay, *_ = np.linalg.lstsq(design, py, rcond=None)

    def mean_dist(qx, qy):
        inside = (qx >= 0) & (qx < width - 1) & (qy >= 0) & (qy < height - 1)
        if inside.sum() < 30:
            return np.nan, 0
        values = ndimage.map_coordinates(distance, [qy[inside], qx[inside]], order=1)
        return float(np.minimum(values, LOCAL_TRUNCATE_PX).mean()), int(inside.sum())

    history, residuals, keep, cps = [], None, None, None
    half = LOCAL_WINDOW_PX // 2
    for iteration in range(LOCAL_ITERATIONS):
        qx, qy = design @ ax, design @ ay
        rows = []
        for cx in range(half, width - half + 1, half):
            for cy in range(half, height - half + 1, half):
                sel = (np.abs(qx - cx) < half) & (np.abs(qy - cy) < half)
                if sel.sum() < 150:
                    continue
                base, _ = mean_dist(qx[sel], qy[sel])
                found = (base, 0, 0)
                for dx in range(-LOCAL_SEARCH_PX, LOCAL_SEARCH_PX + 1):
                    for dy in range(-LOCAL_SEARCH_PX, LOCAL_SEARCH_PX + 1):
                        d, n = mean_dist(qx[sel] + dx, qy[sel] + dy)
                        if n and d < found[0]:
                            found = (d, dx, dy)
                if found[0] > LOCAL_MATCH_MAX_PX:
                    continue
                rows.append((pts[sel, 0].mean(), pts[sel, 1].mean(), qx[sel].mean() + found[1],
                             qy[sel].mean() + found[2], found[0], found[1], found[2]))
        cps = np.array(rows)
        d2 = np.column_stack([cps[:, 0], cps[:, 1], np.ones(len(cps))])
        keep = np.ones(len(cps), dtype=bool)
        for _ in range(5):
            nx, *_ = np.linalg.lstsq(d2[keep], cps[keep, 2], rcond=None)
            ny, *_ = np.linalg.lstsq(d2[keep], cps[keep, 3], rcond=None)
            residuals = np.hypot(d2 @ nx - cps[:, 2], d2 @ ny - cps[:, 3])
            new_keep = residuals < 3 * max(residuals[keep].std(), 0.5) + 1.0
            if (new_keep == keep).all():
                break
            keep = new_keep
        ax, ay = nx, ny
        total, _ = mean_dist(design @ ax, design @ ay)
        history.append({"iteration": iteration, "control_points": int(len(cps)), "kept": int(keep.sum()),
                        "cp_rms_px": float(np.sqrt((residuals[keep] ** 2).mean())),
                        "global_mean_truncated_distance_px": total})
        print(history[-1])
    metres_per_px = 1 / np.sqrt(abs(ax[0] * ay[1] - ax[1] * ay[0]))
    return {"affine_px": ax.tolist(), "affine_py": ay.tolist(), "metres_per_px": float(metres_per_px),
            "history": history, "control_points_E_N_px_py_dist_dx_dy": cps[keep].round(2).tolist(),
            "cp_residual_px": residuals[keep].round(2).tolist()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image", required=True)
    parser.add_argument("--context", required=True, help="Overpass context features (build_parking_zones_from_osm.py)")
    parser.add_argument("--rough-control-points", required=True, help="JSON {points: [{px, py, E, N}, ...]} in EPSG:25832")
    parser.add_argument("--map-x-max", type=int, required=True, help="map area width in px (legend excluded)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    rgb = np.asarray(Image.open(args.image).convert("RGB")).astype(float)[:, :args.map_x_max]
    document = json.load(open(args.rough_control_points, encoding="utf-8"))
    rough = [[p["px"], p["py"], p["E"], p["N"]] for p in document["points"]]
    params, e0, n0, cost = global_fit(rgb, args.context, rough)
    print("global fit", params.tolist(), "cost", cost)
    report = local_fit(rgb, args.context, params, e0, n0)
    report.update({"global_params_tx_ty_sx_sy_theta": params.tolist(), "E0": e0, "N0": n0, "global_cost_px": cost})
    with open(args.out, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    rms = report["history"][-1]["cp_rms_px"]
    print(f"affine written to {args.out}: {report['metres_per_px']:.3f} m/px, control-point RMS {rms:.2f} px = "
          f"{rms * report['metres_per_px']:.1f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
