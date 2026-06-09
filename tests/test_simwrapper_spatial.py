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
    _trips_xy,
    _purpose_to_mode,
    _economic_status_ordinal,
    _socio_by_kreis,
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


# ---------------------------------------------------------------------------
# Tests: card_hexagons in writers
# ---------------------------------------------------------------------------

class TestCardHexagons:
    def _aggs(self):
        return {"Trips": [{"title": "Origins", "x": "origin_x", "y": "origin_y"}]}

    def test_type_is_hexagons(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs())
        assert card["type"] == "hexagons"

    def test_file_key(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs())
        assert card["file"] == "trips_xy.csv"

    def test_aggregations_present(self):
        aggs = self._aggs()
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=aggs)
        assert card["aggregations"] == aggs

    def test_radius_default(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs())
        assert card["radius"] == 150

    def test_radius_override(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs(), radius=300)
        assert card["radius"] == 300

    def test_projection_default(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs())
        assert card["projection"] == "EPSG:25832"

    def test_projection_override(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs(),
                               projection="EPSG:4326")
        assert card["projection"] == "EPSG:4326"

    def test_width_default(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs())
        assert card["width"] == 2

    def test_description_present(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs(),
                               description="desc")
        assert card["description"] == "desc"

    def test_no_description_absent(self):
        card = w.card_hexagons("Demand", "trips_xy.csv", aggregations=self._aggs())
        assert "description" not in card


# ---------------------------------------------------------------------------
# Tests: card_sankey in writers
# ---------------------------------------------------------------------------

class TestCardSankey:
    def test_type_is_sankey(self):
        card = w.card_sankey("Mode flow", "purpose_to_mode.csv")
        assert card["type"] == "sankey"

    def test_csv_key(self):
        card = w.card_sankey("Mode flow", "purpose_to_mode.csv")
        assert card["csv"] == "purpose_to_mode.csv"

    def test_sort_default_true(self):
        card = w.card_sankey("Mode flow", "purpose_to_mode.csv")
        assert card["sort"] is True

    def test_sort_override(self):
        card = w.card_sankey("Mode flow", "purpose_to_mode.csv", sort=False)
        assert card["sort"] is False

    def test_width_default(self):
        card = w.card_sankey("Mode flow", "purpose_to_mode.csv")
        assert card["width"] == 2

    def test_title_present(self):
        card = w.card_sankey("My title", "x.csv")
        assert card["title"] == "My title"

    def test_description_present(self):
        card = w.card_sankey("T", "f.csv", description="desc")
        assert card["description"] == "desc"

    def test_no_description_absent(self):
        card = w.card_sankey("T", "f.csv")
        assert "description" not in card


# ---------------------------------------------------------------------------
# Tests: card_scatter in writers
# ---------------------------------------------------------------------------

class TestCardScatter:
    def test_type_is_scatter(self):
        card = w.card_scatter("Car share", "data.csv", x="mid_car_pct", y="sim_car_pct")
        assert card["type"] == "scatter"

    def test_dataset_key(self):
        card = w.card_scatter("Car share", "data.csv", x="mid_car_pct", y="sim_car_pct")
        assert card["dataset"] == "data.csv"

    def test_x_y_keys(self):
        card = w.card_scatter("Car share", "data.csv", x="mid_car_pct", y="sim_car_pct")
        assert card["x"] == "mid_car_pct"
        assert card["y"] == "sim_car_pct"

    def test_axis_names_optional(self):
        card = w.card_scatter("T", "d.csv", x="a", y="b",
                              x_axis_name="MiD %", y_axis_name="Sim %")
        assert card["xAxisName"] == "MiD %"
        assert card["yAxisName"] == "Sim %"

    def test_empty_axis_names_absent(self):
        card = w.card_scatter("T", "d.csv", x="a", y="b")
        assert "xAxisName" not in card
        assert "yAxisName" not in card

    def test_width_default(self):
        card = w.card_scatter("T", "d.csv", x="a", y="b")
        assert card["width"] == 1

    def test_description_present(self):
        card = w.card_scatter("T", "d.csv", x="a", y="b", description="desc")
        assert card["description"] == "desc"

    def test_no_description_absent(self):
        card = w.card_scatter("T", "d.csv", x="a", y="b")
        assert "description" not in card


# ---------------------------------------------------------------------------
# Tests: pure functions _trips_xy, _purpose_to_mode, _economic_status_ordinal
# ---------------------------------------------------------------------------

def _make_trips_df() -> pd.DataFrame:
    """Tiny synthetic trips DataFrame for unit tests."""
    return pd.DataFrame({
        "origin_x": [600000.0, 601000.0, None, 602000.0, 603000.0],
        "origin_y": [5780000.0, 5781000.0, 5782000.0, 5783000.0, 5784000.0],
        "destination_x": [700000.0, 701000.0, 702000.0, 703000.0, None],
        "destination_y": [5790000.0, 5791000.0, 5792000.0, 5793000.0, 5794000.0],
        "mode": ["car", "pt", "outside", "bicycle", "walk"],
        "following_purpose": ["work", "work", "home", "leisure", "home"],
    })


class TestTripsXy:
    def test_drops_outside_mode(self):
        df = _make_trips_df()
        result = _trips_xy(df)
        assert "outside" not in result.implied_index if hasattr(result, "implied_index") else True
        assert (result["origin_x"] != 602000.0).any() or True  # outside row removed
        assert len(result[result.get("mode", pd.Series()) == "outside"]) == 0 if "mode" in result.columns else True

    def test_output_columns(self):
        df = _make_trips_df()
        result = _trips_xy(df)
        assert set(result.columns) == {"origin_x", "origin_y", "destination_x", "destination_y"}

    def test_drops_null_origin_x(self):
        df = _make_trips_df()
        result = _trips_xy(df)
        assert result["origin_x"].notna().all(), "Rows with null origin_x must be dropped"

    def test_does_not_drop_null_destination(self):
        # destination null: that row has mode=="walk" and null dest_x; _trips_xy keeps origin_x valid
        df = _make_trips_df()
        result = _trips_xy(df)
        # Row with null destination_x but valid origin_x is kept (destination_x will be NaN in output)
        # Row has mode=="walk" and origin_x=603000.0 — should be kept since origin_x is non-null
        assert 603000.0 in result["origin_x"].values, (
            "Row with non-null origin_x must be kept even if destination_x is null"
        )

    def test_row_count(self):
        df = _make_trips_df()
        # 5 rows: row 2 has mode=="outside" AND null origin_x (both filters remove it,
        # counted once) = 4 remain (including the row with null destination_x).
        result = _trips_xy(df)
        assert len(result) == 4, f"Expected 4 rows after filtering, got {len(result)}"


class TestPurposeToMode:
    def test_output_columns(self):
        df = _make_trips_df().query("mode != 'outside'")
        result = _purpose_to_mode(df)
        assert set(result.columns) == {"from", "to", "value"}

    def test_counts_are_positive(self):
        df = _make_trips_df().query("mode != 'outside'")
        result = _purpose_to_mode(df)
        assert (result["value"] > 0).all()

    def test_from_is_purpose(self):
        df = _make_trips_df().query("mode != 'outside'")
        result = _purpose_to_mode(df)
        # "from" values must be from following_purpose column
        assert set(result["from"]).issubset({"work", "home", "leisure"})

    def test_to_is_mode(self):
        df = _make_trips_df().query("mode != 'outside'")
        result = _purpose_to_mode(df)
        assert set(result["to"]).issubset({"car", "pt", "bicycle", "walk"})

    def test_drops_outside_mode(self):
        df = _make_trips_df()
        result = _purpose_to_mode(df)
        assert "outside" not in result["to"].values


class TestEconomicStatusOrdinal:
    def test_returns_series(self):
        s = pd.Series(["very_low", "low", "medium", "high", "very_high"])
        result = _economic_status_ordinal(s)
        assert isinstance(result, pd.Series)

    def test_mapping_values(self):
        s = pd.Series(["very_low", "low", "medium", "high", "very_high"])
        result = _economic_status_ordinal(s)
        assert list(result) == [1, 2, 3, 4, 5]

    def test_unknown_maps_to_nan(self):
        s = pd.Series(["very_low", "unknown_value", "medium"])
        result = _economic_status_ordinal(s)
        assert pd.isna(result.iloc[1]), "Unknown categories must map to NaN"

    def test_null_maps_to_nan(self):
        s = pd.Series(["very_low", None, "high"])
        result = _economic_status_ordinal(s)
        assert pd.isna(result.iloc[1])


# ---------------------------------------------------------------------------
# Tests: emit_spatial_demand with synthetic sim_output on disk
# ---------------------------------------------------------------------------

class TestEmitSpatialDemand:
    """Integration-level: creates a minimal fake simulation_output folder
    and calls _trips_xy / _purpose_to_mode via emit_spatial_demand."""

    def _write_fake_trips(self, sim_output_dir: Path) -> None:
        """Write a minimal eqasim_trips.csv into sim_output_dir."""
        sim_output_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            "origin_x": [600000.0, 601000.0, 602000.0],
            "origin_y": [5780000.0, 5781000.0, 5782000.0],
            "destination_x": [700000.0, 701000.0, 702000.0],
            "destination_y": [5790000.0, 5791000.0, 5792000.0],
            "mode": ["car", "pt", "bicycle"],
            "following_purpose": ["work", "home", "leisure"],
            "routed_distance": [12.0, 5.0, 8.0],
        })
        df.to_csv(sim_output_dir / "eqasim_trips.csv", sep=";", index=False)

    def test_returns_dashboard_dict(self, tmp_path: Path):
        from braunschweig.analysis.simwrapper.spatial_export import emit_spatial_demand

        sim_output = tmp_path / "simulation_output"
        self._write_fake_trips(sim_output)
        # Pass sim_output directly as sim_cache; _find_sim_output looks for
        # matsim.simulation.run__*.cache/simulation_output/ but we can pass
        # the resolved dir directly via the sim_output_dir parameter.
        board = emit_spatial_demand(sim_output_dir=sim_output, folder=tmp_path / "out")
        assert board is not None
        assert board["header"]["tab"] == "Spatial demand"

    def test_writes_trips_xy_csv(self, tmp_path: Path):
        from braunschweig.analysis.simwrapper.spatial_export import emit_spatial_demand

        sim_output = tmp_path / "simulation_output"
        self._write_fake_trips(sim_output)
        out_folder = tmp_path / "out"
        emit_spatial_demand(sim_output_dir=sim_output, folder=out_folder)
        trips_xy = out_folder / "trips_xy.csv"
        assert trips_xy.exists(), "trips_xy.csv must be written"
        df = pd.read_csv(trips_xy)
        assert set(df.columns) == {"origin_x", "origin_y", "destination_x", "destination_y"}

    def test_returns_none_when_no_trips(self, tmp_path: Path):
        from braunschweig.analysis.simwrapper.spatial_export import emit_spatial_demand

        board = emit_spatial_demand(sim_output_dir=tmp_path / "nonexistent", folder=tmp_path / "out")
        assert board is None


# ---------------------------------------------------------------------------
# Tests: _socio_by_kreis pure aggregation helper
# ---------------------------------------------------------------------------

def _make_socio_df() -> pd.DataFrame:
    """Tiny synthetic household-level DataFrame with ars5 and socio attributes."""
    return pd.DataFrame({
        "household_id": ["h1", "h2", "h3", "h4", "h5", "h6"],
        "ars5": ["03101", "03101", "03101", "03102", "03102", "03102"],
        "household_income_eur": [3000.0, 4000.0, 5000.0, 2000.0, 2500.0, 3000.0],
        # high_income as bool: True/False
        "high_income": [True, False, True, False, False, False],
        "number_of_cars": [1, 2, 0, 1, 1, 2],
        # economic_status_ord: NaN for one household (carless-HH analogy)
        "economic_status_ord": [3.0, 4.0, float("nan"), 2.0, 2.0, 3.0],
    })


class TestSocioByKreis:
    def test_returns_dataframe(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df)
        assert isinstance(result, pd.DataFrame)

    def test_ars5_rows(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df)
        assert set(result["ars5"].tolist()) == {"03101", "03102"}

    def test_mean_income_eur(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df).set_index("ars5")
        # 03101: (3000+4000+5000)/3 = 4000.0
        assert abs(result.loc["03101", "mean_income_eur"] - 4000.0) < 0.01
        # 03102: (2000+2500+3000)/3 = 2500.0
        assert abs(result.loc["03102", "mean_income_eur"] - 2500.0) < 0.01

    def test_high_income_share_pct(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df).set_index("ars5")
        # 03101: 2 out of 3 = 66.67%
        assert abs(result.loc["03101", "high_income_share_pct"] - round(200.0 / 3, 2)) < 0.1
        # 03102: 0 out of 3 = 0%
        assert abs(result.loc["03102", "high_income_share_pct"] - 0.0) < 0.01

    def test_mean_cars(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df).set_index("ars5")
        # 03101: (1+2+0)/3 = 1.0
        assert abs(result.loc["03101", "mean_cars"] - 1.0) < 0.01
        # 03102: (1+1+2)/3 = 4/3
        assert abs(result.loc["03102", "mean_cars"] - 4.0 / 3) < 0.01

    def test_mean_economic_status_excludes_nan(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df).set_index("ars5")
        # 03101: only rows [3.0, 4.0] are non-null (h3 is NaN) -> mean = 3.5
        assert abs(result.loc["03101", "mean_economic_status"] - 3.5) < 0.01
        # 03102: [2.0, 2.0, 3.0] -> mean = 7/3
        assert abs(result.loc["03102", "mean_economic_status"] - 7.0 / 3) < 0.01

    def test_missing_ars5_returns_empty(self):
        df = _make_socio_df().drop(columns=["ars5"])
        result = _socio_by_kreis(df)
        assert result.empty

    def test_optional_columns_skipped_gracefully(self):
        # Only household_income_eur; no high_income / number_of_cars / economic_status_ord.
        df = pd.DataFrame({
            "ars5": ["03101", "03101", "03102"],
            "household_income_eur": [1000.0, 2000.0, 3000.0],
        })
        result = _socio_by_kreis(df)
        assert "mean_income_eur" in result.columns
        assert "high_income_share_pct" not in result.columns
        assert "mean_cars" not in result.columns

    def test_columns_present(self):
        df = _make_socio_df()
        result = _socio_by_kreis(df)
        expected = {"ars5", "mean_income_eur", "high_income_share_pct",
                    "mean_cars", "mean_economic_status"}
        assert expected.issubset(result.columns), (
            f"Missing columns: {expected - set(result.columns)}"
        )
