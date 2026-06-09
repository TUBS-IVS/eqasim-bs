"""Tests for the cross-cordon validation writers (CSV + GPKG, every run).

After B5/B6: the agents frame carries entry_x/entry_y/entry_kind so PT in-commuters
appear at their rail station in the map outputs, not at the road gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

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
    """summary.md must report all entry kinds including real_origin (no undercount)."""
    # Build a mixed-kind agents frame: 5 real_origin, 2 road_gate, 1 rail_station.
    rows = (
        [("03241", "ein", "car", "real_origin",   300.0, 400.0, "g3")] * 5
        + [("03241", "ein", "car", "road_gate",   100.0, 200.0, "g1")] * 2
        + [("03241", "ein", "pt",  "rail_station", 50.0,  80.0, "g2")] * 1
    )
    agents_mixed = pd.DataFrame(rows, columns=["ars5", "direction", "mode", "entry_kind",
                                               "entry_x", "entry_y", "gate_id"])
    paths = write_cordon_validation(str(tmp_path), agents_mixed)
    summary = Path(paths["summary"]).read_text(encoding="utf-8")

    # Every entry kind must appear in the summary.
    assert "real_origin" in summary, "summary must mention real_origin count"
    assert "road_gate" in summary, "summary must mention road_gate entry count"
    assert "rail_station" in summary, "summary must mention rail_station entry count"

    # The per-kind counts embedded in the summary must match expected values.
    assert "real_origin: 5" in summary, "real_origin count should be 5"
    assert "road_gate: 2" in summary, "road_gate count should be 2"
    assert "rail_station: 1" in summary, "rail_station count should be 1"

    # The sum of all per-kind counts must equal the total agent count (no undercount).
    total = len(agents_mixed)  # 8
    assert f"Agents: {total:,}" in summary, "agent total must be in summary"


def test_works_without_targets(tmp_path):
    paths = write_cordon_validation(str(tmp_path), _agents())
    assert Path(paths["gates_gpkg"]).exists()
    assert Path(paths["commuter_validation"]).exists()


# ---------------------------------------------------------------------------
# Tests for write_gate_volumes (per-gate BA in/out gravity expectation)
# ---------------------------------------------------------------------------

from braunschweig.data.cordon.validation_output import write_gate_volumes  # noqa: E402


def test_write_gate_volumes_both_directions(tmp_path):
    """write_gate_volumes writes gate_volumes.csv and gate_volumes.gpkg
    with per-gate inbound+outbound summed over Kreise.
    """
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000", "gate_0001"]},
        geometry=[Point(600000, 5790000), Point(640000, 5790000)],
        crs="EPSG:25832",
    )
    assignment = pd.DataFrame({
        "ars5":    ["03241",    "15083",    "15083"],
        "gate_id": ["gate_0000", "gate_0001", "gate_0000"],
        "inbound": [1000, 800, 200],
        "outbound": [500, 900, 100],
    })
    paths = write_gate_volumes(str(tmp_path), gates, assignment)

    # Both output files must exist.
    assert Path(paths["gate_volumes_csv"]).exists(), "gate_volumes.csv not written"
    assert Path(paths["gate_volumes_gpkg"]).exists(), "gate_volumes.gpkg not written"

    df = pd.read_csv(paths["gate_volumes_csv"])
    assert set(["gate_id", "inbound", "outbound", "gate_x", "gate_y"]).issubset(df.columns), \
        f"missing columns; got {list(df.columns)}"

    # gate_0000 receives inbound 1000+200=1200, outbound 500+100=600.
    g0 = df[df["gate_id"] == "gate_0000"].iloc[0]
    assert g0["inbound"] == 1200
    assert g0["outbound"] == 600

    # gate_0001 receives inbound 800, outbound 900.
    g1 = df[df["gate_id"] == "gate_0001"].iloc[0]
    assert g1["inbound"] == 800
    assert g1["outbound"] == 900


def test_write_gate_volumes_gpkg_has_geometry(tmp_path):
    """gate_volumes.gpkg must carry point geometry so QGIS can map it."""
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000"]},
        geometry=[Point(600000, 5790000)],
        crs="EPSG:25832",
    )
    assignment = pd.DataFrame({
        "ars5": ["03241"], "gate_id": ["gate_0000"],
        "inbound": [500], "outbound": [300],
    })
    paths = write_gate_volumes(str(tmp_path), gates, assignment)
    gdf = gpd.read_file(paths["gate_volumes_gpkg"])
    assert gdf.geometry.notna().all(), "gate_volumes.gpkg has null geometries"
    assert "inbound" in gdf.columns and "outbound" in gdf.columns


def test_write_gate_volumes_coordinates_match_gates(tmp_path):
    """gate_x/gate_y in the CSV must match the gate GeoDataFrame point coordinates."""
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000", "gate_0001"]},
        geometry=[Point(600000, 5790000), Point(640000, 5810000)],
        crs="EPSG:25832",
    )
    assignment = pd.DataFrame({
        "ars5": ["03241", "15083"],
        "gate_id": ["gate_0000", "gate_0001"],
        "inbound": [100, 200], "outbound": [50, 80],
    })
    paths = write_gate_volumes(str(tmp_path), gates, assignment)
    df = pd.read_csv(paths["gate_volumes_csv"])
    row0 = df[df["gate_id"] == "gate_0000"].iloc[0]
    assert abs(row0["gate_x"] - 600000) < 1.0
    assert abs(row0["gate_y"] - 5790000) < 1.0
    row1 = df[df["gate_id"] == "gate_0001"].iloc[0]
    assert abs(row1["gate_x"] - 640000) < 1.0
    assert abs(row1["gate_y"] - 5810000) < 1.0
