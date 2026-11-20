"""Activity and workplace counts of two synthesis runs on a regular grid, with the building potentials as a reference
(issue #648 report, heatmap).

Reads the activity layer of each run's ``<prefix>activities.gpkg`` (points, EPSG:25832) and the building layer of the
building-potentials GeoPackage (polygons, EPSG:25832; centroid per building). Counts per square cell of
``CELL_SIZE_METRES`` in EPSG:25832, so no reprojection is involved.

Layers (one column per layer and source):
    <purpose>     activities of that purpose (every activity of every synthetic person; purpose "outside" is left
                  out because it marks cordon stays outside the model area)
    workplace     persons per work location: work activities, one per (person, location)
    nonhome       all activities except home and outside
    *_pot         reference from the building potentials (relative weights of the producer, not observed counts):
                  workplace/work <- assigned_Workers; education <- assigned_School + assigned_University +
                  assigned_Kindergarten; shop <- assigned_Retail_Daily + assigned_Retail_Non_Daily;
                  leisure <- assigned_Leisure; other <- value_errands

Usage (eqasim conda env):
    python activity_grid.py OLD_ACTIVITIES_GPKG NEW_ACTIVITIES_GPKG POTENTIALS_GPKG OUT_DIR

Writes activity_grid_500m.csv (cells with at least one non-zero value) and activity_grid_totals.csv (sum per layer
and source, for the log and the manifest).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio

CELL_SIZE_METRES = 500
logger = logging.getLogger("activity_grid")
POTENTIAL_LAYERS = {
    "workplace": ["assigned_Workers"], "work": ["assigned_Workers"],
    "education": ["assigned_School", "assigned_University", "assigned_Kindergarten"],
    "shop": ["assigned_Retail_Daily", "assigned_Retail_Non_Daily"], "leisure": ["assigned_Leisure"],
    "other": ["value_errands"]}


def _cells(x: np.ndarray, y: np.ndarray) -> pd.MultiIndex:
    return pd.MultiIndex.from_arrays([np.floor(x / CELL_SIZE_METRES).astype(np.int64),
                                      np.floor(y / CELL_SIZE_METRES).astype(np.int64)], names=["ix", "iy"])


def activity_counts(path: Path, label: str) -> pd.DataFrame:
    frame = pyogrio.read_dataframe(path, columns=["person_id", "purpose"])
    if frame.geometry.geom_type.ne("Point").any():
        raise ValueError(f"{path}: the activity layer holds non-point geometries")
    x, y = frame.geometry.x.to_numpy(), frame.geometry.y.to_numpy()
    missing = ~(np.isfinite(x) & np.isfinite(y))
    logger.info("%s: %d activities, %d without coordinates; purposes %s", label, len(frame), int(missing.sum()),
                frame["purpose"].value_counts().to_dict())
    frame = pd.DataFrame({"person_id": frame["person_id"].to_numpy(), "purpose": frame["purpose"].to_numpy(),
                          "x": x, "y": y})[~missing]
    frame = frame[frame["purpose"] != "outside"]
    cells = _cells(frame["x"].to_numpy(), frame["y"].to_numpy())
    keyed = pd.DataFrame({"ix": cells.get_level_values("ix"), "iy": cells.get_level_values("iy"),
                          "purpose": frame["purpose"].to_numpy()})
    counts = keyed.groupby(["ix", "iy", "purpose"]).size().unstack("purpose", fill_value=0)
    counts.columns = [f"{c}_{label}" for c in counts.columns]
    counts[f"nonhome_{label}"] = counts[[c for c in counts.columns if not c.startswith("home_")]].sum(axis=1)
    work = frame[frame["purpose"] == "work"].drop_duplicates(["person_id", "x", "y"])
    counts[f"workplace_{label}"] = pd.Series(1, index=_cells(work["x"].to_numpy(), work["y"].to_numpy())).groupby(
        level=["ix", "iy"]).sum().reindex(counts.index).fillna(0)
    logger.info("%s: %d persons with a work location, %d cells", label, int(counts[f"workplace_{label}"].sum()),
                len(counts))
    return counts


def potential_weights(path: Path) -> pd.DataFrame:
    columns = sorted({c for cols in POTENTIAL_LAYERS.values() for c in cols})
    buildings = pyogrio.read_dataframe(path, layer="buildings", columns=columns)
    centroid = buildings.geometry.centroid
    cells = _cells(centroid.x.to_numpy(), centroid.y.to_numpy())
    out = {}
    for layer, cols in POTENTIAL_LAYERS.items():
        values = buildings[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum(axis=1).to_numpy()
        out[f"{layer}_pot"] = pd.Series(values, index=cells).groupby(level=["ix", "iy"]).sum()
    logger.info("potentials: %d buildings; totals %s", len(buildings),
                {k: round(float(v.sum()), 1) for k, v in out.items()})
    return pd.DataFrame(out)


def main(old_path: Path, new_path: Path, potentials: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    grid = pd.concat([activity_counts(old_path, "old"), activity_counts(new_path, "new"),
                      potential_weights(potentials)], axis=1).fillna(0.0)
    grid = grid[(grid != 0).any(axis=1)].reset_index()
    grid.insert(2, "x_km", grid["ix"] * CELL_SIZE_METRES / 1000.0)
    grid.insert(3, "y_km", grid["iy"] * CELL_SIZE_METRES / 1000.0)
    value_columns = [c for c in grid.columns if c not in ("ix", "iy", "x_km", "y_km")]
    for column in value_columns:
        if not column.endswith("_pot"):
            grid[column] = grid[column].round().astype(np.int64)
        else:
            grid[column] = grid[column].round(2)
    grid.to_csv(out_dir / "activity_grid_500m.csv", index=False)
    totals = grid[value_columns].sum().rename("total").reset_index().rename(columns={"index": "layer"})
    totals.to_csv(out_dir / "activity_grid_totals.csv", index=False)
    logger.info("wrote %d cells of %d m to %s", len(grid), CELL_SIZE_METRES, out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
