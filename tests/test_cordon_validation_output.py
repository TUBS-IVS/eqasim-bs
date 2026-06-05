"""Tests for the cross-cordon validation writers (CSV + GPKG, every run)."""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.validation_output import write_cordon_validation  # noqa: E402


def _agents():
    rows = (
        [("03241", "ein", "car", "g1", 100.0, 200.0)] * 3
        + [("03241", "ein", "pt", "g2", 50.0, 80.0)] * 1
        + [("03241", "aus", "car", "g1", 100.0, 200.0)] * 2
    )
    return pd.DataFrame(rows, columns=["ars5", "direction", "mode", "gate_id",
                                       "gate_x", "gate_y"])


def test_writes_csv_and_gpkg(tmp_path):
    od_target = pd.DataFrame([("03241", "ein", "car", 5)],
                             columns=["ars5", "direction", "mode", "n_target"])
    paths = write_cordon_validation(str(tmp_path), _agents(), od_target=od_target,
                                    sampling_rate=0.5, crs="EPSG:25832")
    for key in ("commuter_validation", "gates_csv", "gates_gpkg", "summary"):
        assert Path(paths[key]).exists(), key

    # gates.gpkg reloads with geometry + per-gate/direction/mode counts
    gdf = gpd.read_file(paths["gates_gpkg"])
    assert "n" in gdf.columns and gdf.geometry.notna().all()
    g1car = gdf[(gdf["gate_id"] == "g1") & (gdf["direction"] == "ein")
                & (gdf["mode"] == "car")].iloc[0]
    assert g1car["n"] == 3
    assert abs(g1car.geometry.x - 100.0) < 1e-6

    # commuter CSV carries the deviation columns
    cv = pd.read_csv(paths["commuter_validation"])
    assert {"n_scaled", "abs_dev", "pct_dev"}.issubset(cv.columns)


def test_works_without_targets(tmp_path):
    paths = write_cordon_validation(str(tmp_path), _agents())
    assert Path(paths["gates_gpkg"]).exists()
    assert Path(paths["commuter_validation"]).exists()
