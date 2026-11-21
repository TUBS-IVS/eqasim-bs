"""Commuter layers of two runs on the 500 m grid of activity_grid.py (issue #648 report, heatmap).

Layers (one column per layer and run, counts of persons per cell of CELL_SIZE_METRES in EPSG:25832):
    incommuter_work     in-commuter agents (stage braunschweig.synthesis.incommuters): their work location
    incommuter_student  student in-commuters (stage braunschweig.synthesis.student_incommuters): place of study
    outcommuter_home    residents whose work location lies outside the model area, at their home location; the work
                        location is read from <prefix>commutes_pre_portal.gpkg (default layer: home -> work line of the
                        assigned primary location, before the portal step turns far legs into outside stays at the
                        cordon)
    outstudent_home     residents whose assigned education location lies outside the model area, at their home
                        location (layer "education" of the same file: home -> education line); the education
                        activities of <prefix>activities.gpkg outside the area are logged as a cross-check
The model area is the union of the 880 traffic zones of the building-potentials GeoPackage (layer "zones").

Usage (eqasim conda env):
    python commuter_grid.py POTENTIALS_GPKG OUT_DIR \
        old=<incommuters.p>,<student_incommuters.p>,<commutes_pre_portal.gpkg>,<activities.gpkg> \
        new=<incommuters.p>,<student_incommuters.p>,<commutes_pre_portal.gpkg>,<activities.gpkg>

Writes commuter_grid_500m.csv (cells with at least one non-zero value) and commuter_totals.csv (per run: persons per
layer, in-commuters against their BA Pendleratlas targets from the stage's od_target frame, rates of every fallback).
"""

from __future__ import annotations

import logging
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
import shapely

CELL_SIZE_METRES = 500
logger = logging.getLogger("commuter_grid")


def _cells(x: np.ndarray, y: np.ndarray) -> pd.MultiIndex:
    return pd.MultiIndex.from_arrays([np.floor(x / CELL_SIZE_METRES).astype(np.int64),
                                      np.floor(y / CELL_SIZE_METRES).astype(np.int64)], names=["ix", "iy"])


def _count(x: np.ndarray, y: np.ndarray, name: str) -> pd.Series:
    if len(x) == 0:
        return pd.Series(dtype=float, name=name)
    return pd.Series(1, index=_cells(x, y)).groupby(level=["ix", "iy"]).sum().rename(name)


def model_area(potentials: Path):
    zones = pyogrio.read_dataframe(potentials, layer="zones", columns=["NO"])
    area = shapely.union_all(zones.geometry.to_numpy())
    shapely.prepare(area)
    logger.info("model area: union of %d traffic zones", len(zones))
    return area


def _stage_points(pickle_path: Path, purpose: str, label: str) -> tuple[np.ndarray, np.ndarray, dict]:
    with open(pickle_path, "rb") as handle:
        frames = pickle.load(handle)
    activities, locations = frames["activities"], frames["locations"]
    chosen = activities.loc[activities["purpose"] == purpose, ["person_id", "activity_index"]]
    joined = chosen.merge(locations[["person_id", "activity_index", "geometry"]], how="left",
                          on=["person_id", "activity_index"], validate="one_to_one")
    missing = joined["geometry"].isna()
    logger.info("%s: %d %s activities of %d persons, located %d, without location %d (%.2f%%)", label, len(chosen),
                purpose, frames["persons"]["person_id"].nunique(), int((~missing).sum()), int(missing.sum()),
                100.0 * missing.mean() if len(joined) else 0.0)
    geometry = joined.loc[~missing, "geometry"]
    info = {"persons": int(frames["persons"]["person_id"].nunique()), "located": int((~missing).sum())}
    if "od_target" in frames and "n_target" in frames["od_target"]:
        target = frames["od_target"]
        inbound = target[target["direction"].astype(str).str.lower().isin(["ein", "in", "inbound"])]
        info["target"] = float(inbound["n_target"].sum())
    return shapely.get_x(geometry.to_numpy()), shapely.get_y(geometry.to_numpy()), info


def run_layers(spec: str, area) -> tuple[pd.DataFrame, dict]:
    label, paths = spec.split("=", 1)
    incommuters, students, commutes, activities = (Path(p) for p in paths.split(","))
    totals = {"run": label}
    layers = []

    x, y, info = _stage_points(incommuters, "work", f"{label} in-commuters")
    layers.append(_count(x, y, f"incommuter_work_{label}"))
    totals.update({"incommuter_work": info["located"], "incommuter_persons": info["persons"],
                   "incommuter_target": info.get("target", np.nan)})
    x, y, info = _stage_points(students, "education", f"{label} student in-commuters")
    layers.append(_count(x, y, f"incommuter_student_{label}"))
    totals.update({"incommuter_student": info["located"], "incommuter_student_persons": info["persons"]})

    work_layer = pyogrio.list_layers(commutes)[0][0]
    for layer, column, kind in ((work_layer, "outcommuter_home", "work"), ("education", "outstudent_home", "education")):
        lines = pyogrio.read_dataframe(commutes, layer=layer, columns=["person_id"])
        start = shapely.get_point(lines.geometry.to_numpy(), 0)
        end = shapely.get_point(lines.geometry.to_numpy(), -1)
        valid = ~(shapely.is_missing(start) | shapely.is_missing(end))
        outside = valid & ~shapely.contains_xy(area, shapely.get_x(end), shapely.get_y(end))
        logger.info("%s %s lines (pre-portal, layer %s): %d, valid %d (%.2f%%), destination outside the area %d "
                    "(%.2f%% of valid)", label, kind, layer, len(lines), int(valid.sum()), 100.0 * valid.mean(),
                    int(outside.sum()), 100.0 * outside.sum() / max(int(valid.sum()), 1))
        layers.append(_count(shapely.get_x(start[outside]), shapely.get_y(start[outside]), f"{column}_{label}"))
        totals.update({f"{kind}_lines": int(len(lines)), column: int(outside.sum())})

    acts = pyogrio.read_dataframe(activities, columns=["person_id", "purpose"])
    edu = (acts["purpose"] == "education").to_numpy()
    edu_out = edu & ~shapely.contains_xy(area, acts.geometry.x.to_numpy(), acts.geometry.y.to_numpy())
    logger.info("%s cross-check: education activities of the finished plans %d, outside the area %d (%.3f%%), "
                "persons %d", label, int(edu.sum()), int(edu_out.sum()), 100.0 * edu_out.sum() / max(int(edu.sum()), 1),
                acts.loc[edu_out, "person_id"].nunique())
    return pd.concat(layers, axis=1), totals


def main(potentials: Path, out_dir: Path, specs: list[str]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    area = model_area(potentials)
    grids, totals = [], []
    for spec in specs:
        grid, total = run_layers(spec, area)
        grids.append(grid)
        totals.append(total)
    grid = pd.concat(grids, axis=1).fillna(0).astype(np.int64)
    grid = grid[(grid != 0).any(axis=1)].reset_index()
    grid.insert(2, "x_km", grid["ix"] * CELL_SIZE_METRES / 1000.0)
    grid.insert(3, "y_km", grid["iy"] * CELL_SIZE_METRES / 1000.0)
    grid.to_csv(out_dir / "commuter_grid_500m.csv", index=False)
    pd.DataFrame(totals).to_csv(out_dir / "commuter_totals.csv", index=False)
    logger.info("wrote %d cells of %d m to %s", len(grid), CELL_SIZE_METRES, out_dir)
    print(pd.DataFrame(totals).to_string(index=False))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:])
