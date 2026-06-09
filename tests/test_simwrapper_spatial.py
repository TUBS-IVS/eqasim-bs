"""Unit tests for braunschweig.analysis.simwrapper.spatial_export and the
new card helpers in writers.

All tests use tiny synthetic data -- no external files are required.
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.analysis.simwrapper import writers as w
from braunschweig.analysis.simwrapper.spatial_export import (
    BEV_POWERTRAIN_VALUE,
    fleet_by_kreis,
    write_xyt_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fleet_gdf() -> gpd.GeoDataFrame:
    """Tiny synthetic fleet GeoDataFrame (EPSG:25832) for unit tests."""
    data = {
        "household_id": ["hh1", "hh2", "hh3", "hh4"],
        "kreis_ags5": ["03101", "03101", "03102", "03102"],
        "powertrain": [BEV_POWERTRAIN_VALUE, "petrol", BEV_POWERTRAIN_VALUE, "diesel"],
        "engine_power_kw": [150.0, 90.0, 120.0, 80.0],
        "engine_power_ps": [204.0, 122.0, 163.0, 109.0],
        "is_bev": [1, 0, 1, 0],
        "geometry": [
            Point(600000, 5780000),
            Point(600100, 5780100),
            Point(650000, 5800000),
            Point(650100, 5800100),
        ],
    }
    return gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:25832")


# ---------------------------------------------------------------------------
# Tests: write_xyt_csv
# ---------------------------------------------------------------------------

class TestWriteXytCsv:
    def test_header_lines(self, tmp_path: Path):
        gdf = _make_fleet_gdf()
        write_xyt_csv(gdf, tmp_path, "test.xyt.csv", "engine_power_kw")
        lines = (tmp_path / "test.xyt.csv").read_text(encoding="utf-8").splitlines()
        assert lines[0] == "# EPSG:25832", f"First line must be '# EPSG:25832', got {lines[0]!r}"

    def test_header_row(self, tmp_path: Path):
        gdf = _make_fleet_gdf()
        write_xyt_csv(gdf, tmp_path, "test.xyt.csv", "engine_power_kw")
        lines = (tmp_path / "test.xyt.csv").read_text(encoding="utf-8").splitlines()
        assert lines[1] == "time,x,y,value", f"Second line must be CSV header, got {lines[1]!r}"

    def test_data_row_count(self, tmp_path: Path):
        gdf = _make_fleet_gdf()
        write_xyt_csv(gdf, tmp_path, "test.xyt.csv", "engine_power_kw")
        df = pd.read_csv(tmp_path / "test.xyt.csv", comment="#")
        # All 4 rows have non-null geometry and non-null engine_power_kw.
        assert len(df) == 4, f"Expected 4 data rows, got {len(df)}"

    def test_time_column_is_zero(self, tmp_path: Path):
        gdf = _make_fleet_gdf()
        write_xyt_csv(gdf, tmp_path, "test.xyt.csv", "engine_power_kw")
        df = pd.read_csv(tmp_path / "test.xyt.csv", comment="#")
        assert (df["time"] == 0).all(), "time column must be all zeros"

    def test_null_rows_excluded(self, tmp_path: Path):
        gdf = _make_fleet_gdf().copy()
        # Introduce a null value and a null geometry row.
        gdf.loc[0, "engine_power_kw"] = float("nan")
        gdf.loc[1, "geometry"] = None
        write_xyt_csv(gdf, tmp_path, "test.xyt.csv", "engine_power_kw")
        df = pd.read_csv(tmp_path / "test.xyt.csv", comment="#")
        # Row 0 (null value) and row 1 (null geometry) excluded -> 2 rows.
        assert len(df) == 2, f"Expected 2 data rows after null exclusion, got {len(df)}"

    def test_wrong_crs_raises(self, tmp_path: Path):
        gdf = _make_fleet_gdf().to_crs(epsg=4326)
        with pytest.raises(AssertionError, match="EPSG:25832"):
            write_xyt_csv(gdf, tmp_path, "bad.xyt.csv", "engine_power_kw")


# ---------------------------------------------------------------------------
# Tests: fleet_by_kreis
# ---------------------------------------------------------------------------

class TestFleetByKreis:
    def test_columns(self):
        gdf = _make_fleet_gdf()
        result = fleet_by_kreis(gdf)
        expected_cols = {"kreis_ags5", "n_vehicles", "bev_share_pct",
                         "mean_power_kw", "mean_power_ps"}
        assert expected_cols.issubset(result.columns), (
            f"Missing columns: {expected_cols - set(result.columns)}"
        )

    def test_kreis_counts(self):
        gdf = _make_fleet_gdf()
        result = fleet_by_kreis(gdf)
        counts = result.set_index("kreis_ags5")["n_vehicles"]
        assert counts["03101"] == 2
        assert counts["03102"] == 2

    def test_bev_share(self):
        gdf = _make_fleet_gdf()
        result = fleet_by_kreis(gdf)
        shares = result.set_index("kreis_ags5")["bev_share_pct"]
        # 03101: 1 BEV / 2 total = 50%
        assert abs(shares["03101"] - 50.0) < 0.01, (
            f"03101 BEV share should be 50%, got {shares['03101']}"
        )
        # 03102: 1 BEV / 2 total = 50%
        assert abs(shares["03102"] - 50.0) < 0.01, (
            f"03102 BEV share should be 50%, got {shares['03102']}"
        )

    def test_mean_power_kw(self):
        gdf = _make_fleet_gdf()
        result = fleet_by_kreis(gdf)
        means = result.set_index("kreis_ags5")["mean_power_kw"]
        # 03101: (150 + 90) / 2 = 120
        assert abs(means["03101"] - 120.0) < 0.01, (
            f"03101 mean kW should be 120, got {means['03101']}"
        )
        # 03102: (120 + 80) / 2 = 100
        assert abs(means["03102"] - 100.0) < 0.01, (
            f"03102 mean kW should be 100, got {means['03102']}"
        )


# ---------------------------------------------------------------------------
# Tests: card_xytime in writers
# ---------------------------------------------------------------------------

class TestCardXytime:
    def test_type_is_xytime(self):
        card = w.card_xytime("Power map", "fleet_power_kw.xyt.csv")
        assert card["type"] == "xytime"

    def test_file_key_not_dataset(self):
        card = w.card_xytime("Power map", "fleet_power_kw.xyt.csv")
        assert "file" in card, "card_xytime must use 'file' key"
        assert "dataset" not in card, "card_xytime must NOT use 'dataset' key"

    def test_file_value(self):
        card = w.card_xytime("Power map", "fleet_power_kw.xyt.csv")
        assert card["file"] == "fleet_power_kw.xyt.csv"

    def test_radius_default(self):
        card = w.card_xytime("Power map", "fleet_power_kw.xyt.csv")
        assert card["radius"] == 6

    def test_radius_override(self):
        card = w.card_xytime("Power map", "fleet_power_kw.xyt.csv", radius=4)
        assert card["radius"] == 4

    def test_width_default(self):
        card = w.card_xytime("Power map", "fleet_power_kw.xyt.csv")
        assert card["width"] == 2

    def test_value_label(self):
        card = w.card_xytime("Power map", "f.xyt.csv", value_label="kW")
        assert card["valueLabel"] == "kW"

    def test_no_value_label_absent(self):
        card = w.card_xytime("Power map", "f.xyt.csv")
        assert "valueLabel" not in card

    def test_description(self):
        card = w.card_xytime("T", "f.xyt.csv", description="desc text")
        assert card["description"] == "desc text"


# ---------------------------------------------------------------------------
# Tests: card_choropleth in writers
# ---------------------------------------------------------------------------

class TestCardChoropleth:
    def test_type_is_map(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct",
        )
        assert card["type"] == "map"

    def test_shapes_file(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct",
        )
        assert card["shapes"]["file"] == "kreise.geojson"

    def test_shapes_join(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct", join="ars5",
        )
        assert card["shapes"]["join"] == "ars5"

    def test_datasets_agg(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct",
        )
        assert card["datasets"]["agg"]["file"] == "kreis_fleet.csv"

    def test_display_fill_column_name(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct",
        )
        assert card["display"]["fill"]["columnName"] == "bev_share_pct"

    def test_display_fill_color_ramp(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct", color_ramp="Plasma",
        )
        assert card["display"]["fill"]["colorRamp"]["ramp"] == "Plasma"

    def test_display_fill_steps(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct",
        )
        assert card["display"]["fill"]["colorRamp"]["steps"] == 7

    def test_display_fill_join_matches_shapes(self):
        card = w.card_choropleth(
            "BEV share", "kreise.geojson", "kreis_fleet.csv",
            value_col="bev_share_pct", join="ars5",
        )
        assert card["display"]["fill"]["join"] == "ars5"
        assert card["display"]["fill"]["join"] == card["shapes"]["join"]

    def test_description(self):
        card = w.card_choropleth(
            "T", "g.geojson", "d.csv", value_col="col", description="desc"
        )
        assert card["description"] == "desc"

    def test_no_description_absent(self):
        card = w.card_choropleth(
            "T", "g.geojson", "d.csv", value_col="col"
        )
        assert "description" not in card
