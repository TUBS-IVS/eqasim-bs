"""Tests for the cross-cordon validation writers (CSV + GPKG, every run).

After B5/B6: the agents frame carries entry_x/entry_y/entry_kind so PT in-commuters
appear at their rail station in the map outputs, not at the road gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.validation_output import write_cordon_validation  # noqa: E402


def _agents():
    """Synthetic agents frame with the B5 schema (entry_x/entry_y/entry_kind).

    3 car agents at road_gate (100, 200), 1 pt agent at rail_station (50, 80),
    2 outbound car agents at road_gate (100, 200).
    """
    rows = (
        [("03241", "ein", "car", "road_gate",   100.0, 200.0, "g1")] * 3
        + [("03241", "ein", "pt",  "rail_station", 50.0,  80.0, "g2")] * 1
        + [("03241", "aus", "car", "road_gate",   100.0, 200.0, "g1")] * 2
    )
    return pd.DataFrame(rows, columns=["ars5", "direction", "mode", "entry_kind",
                                       "entry_x", "entry_y", "gate_id"])


def test_writes_csv_and_gpkg(tmp_path):
    od_target = pd.DataFrame([("03241", "ein", "car", 5)],
                             columns=["ars5", "direction", "mode", "n_target"])
    paths = write_cordon_validation(str(tmp_path), _agents(), od_target=od_target,
                                    sampling_rate=0.5, crs="EPSG:25832")
    for key in ("commuter_validation", "gates_csv", "gates_gpkg", "summary"):
        assert Path(paths[key]).exists(), key

    # gates.gpkg must have entry_kind column and geometry built from entry_x/entry_y.
    gdf = gpd.read_file(paths["gates_gpkg"])
    assert "n" in gdf.columns and gdf.geometry.notna().all()
    assert "entry_kind" in gdf.columns, "entry_kind must be present in gates.gpkg for QGIS styling"

    # Car road_gate row: 3 agents, geometry at road gate coords (100, 200).
    car_rows = gdf[(gdf["direction"] == "ein") & (gdf["mode"] == "car")
                  & (gdf["entry_kind"] == "road_gate")]
    assert len(car_rows) == 1
    assert car_rows.iloc[0]["n"] == 3
    assert abs(car_rows.iloc[0].geometry.x - 100.0) < 1e-6
    assert abs(car_rows.iloc[0].geometry.y - 200.0) < 1e-6

    # PT rail_station row: 1 agent, geometry at rail station coords (50, 80) — NOT (100, 200).
    pt_rows = gdf[(gdf["direction"] == "ein") & (gdf["mode"] == "pt")]
    assert len(pt_rows) == 1
    pt_row = pt_rows.iloc[0]
    assert pt_row["entry_kind"] == "rail_station"
    assert abs(pt_row.geometry.x - 50.0) < 1e-6, \
        f"PT geometry x {pt_row.geometry.x} should be rail station 50.0, not road gate 100.0"
    assert abs(pt_row.geometry.y - 80.0) < 1e-6, \
        f"PT geometry y {pt_row.geometry.y} should be rail station 80.0, not road gate 200.0"

    # commuter CSV carries the deviation columns.
    cv = pd.read_csv(paths["commuter_validation"])
    assert {"n_scaled", "abs_dev", "pct_dev"}.issubset(cv.columns)


def test_gates_csv_has_entry_kind(tmp_path):
    """gates.csv must include entry_kind for downstream use."""
    paths = write_cordon_validation(str(tmp_path), _agents())
    gates_csv = pd.read_csv(paths["gates_csv"])
    assert "entry_kind" in gates_csv.columns, "gates.csv must carry entry_kind"
    assert "entry_x" in gates_csv.columns
    assert "entry_y" in gates_csv.columns


def test_summary_contains_entry_kind_counts(tmp_path):
    """summary.md must report car-via-road-gate vs pt-via-rail-station counts."""
    paths = write_cordon_validation(str(tmp_path), _agents())
    summary = Path(paths["summary"]).read_text(encoding="utf-8")
    # The summary must mention both entry kinds so the reader can distinguish them.
    assert "road_gate" in summary, "summary must mention road_gate entry count"
    assert "rail_station" in summary, "summary must mention rail_station entry count"


def test_works_without_targets(tmp_path):
    paths = write_cordon_validation(str(tmp_path), _agents())
    assert Path(paths["gates_gpkg"]).exists()
    assert Path(paths["commuter_validation"]).exists()
