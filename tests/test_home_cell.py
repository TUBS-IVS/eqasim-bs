"""Tests for the cell-accurate home placement stage (popsim cell->building handoff).

These tests exercise the PURE functions of
``braunschweig.synthesis.locations.home_cell``:

- ``building_cell_id`` floors an EPSG:3035 centroid to its 100 m INSPIRE cell id
  (round-trips with ``braunschweig.popsim.cells.parse_inspire_id``);
- ``assign_homes_to_cell_buildings`` places each household in an AREA-weighted
  building of ITS own 100 m cell (deterministic for a fixed seed), falling back
  to a commune-level area-weighted draw when the cell has no building, and
  reports the cell-accurate vs commune-fallback split (CLAUDE.md
  no-silent-fallback).

Synthetic tiny GeoDataFrames are used (no 3 GB ALKIS file).
"""

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig.popsim.cells import parse_inspire_id
from braunschweig.synthesis.locations import home_cell


def test_building_cell_id_floors_to_sw_corner():
    # A centroid inside the 100 m cell whose SW corner is N2689100 / E4337000.
    cell_id = home_cell.building_cell_id(north_m=2689137.0, east_m=4337042.0)
    assert cell_id == "CRS3035RES100mN2689100E4337000"

    res, north, east = parse_inspire_id(cell_id)
    assert res == 100
    assert north == 2689100
    assert east == 4337000

    # Exactly on the SW corner stays in the same cell (floor is idempotent there).
    on_corner = home_cell.building_cell_id(north_m=2689100.0, east_m=4337000.0)
    assert on_corner == cell_id


def _buildings_frame():
    """Two 100 m cells (A, B) plus a commune-only cell (C, no households).

    Cell A has two buildings of very different area; cell B one building. All in
    commune "031010000000"; cell C is a third cell in the SAME commune used as a
    commune-fallback target. Geometry is EPSG:25832 (the buildings' native CRS).
    """
    # SW corners in EPSG:3035; centroids placed +50 m inside each cell.
    cells = {
        "A": (2689100, 4337000),
        "B": (2689200, 4337000),
        "C": (2689300, 4337000),
    }
    rows = []
    # cell A: two buildings, areas 10 (small) and 1000 (large).
    rows.append(("A", 10.0))
    rows.append(("A", 1000.0))
    # cell B: one building.
    rows.append(("B", 50.0))
    # cell C: one building (commune fallback target; no household lives here).
    rows.append(("C", 70.0))

    records = []
    for building_id, (cell_key, weight) in enumerate(rows):
        north, east = cells[cell_key]
        records.append(
            {
                "building_id": building_id,
                "weight": weight,
                "commune_id": "031010000000",
                "cell_key": cell_key,
                # store the EPSG:3035 SW corner so the test knows the expected cell.
                "north3035": north,
                "east3035": east,
            }
        )
    df = pd.DataFrame.from_records(records)
    # Build EPSG:25832 points (arbitrary but distinct); the stage reprojects to
    # 3035 internally, so we attach a 3035 cell id column directly for the test
    # by constructing geometries via the helper instead.
    return df


def _buildings_geo(df_3035: pd.DataFrame) -> gpd.GeoDataFrame:
    """Turn the helper frame into an EPSG:25832 GeoDataFrame with cell ids.

    We construct the 3035 centroid (SW corner + 50 m), derive the cell id with
    the production helper, then reproject the same point to 25832 so the stage's
    own reprojection round-trips back to the same cell.
    """
    pts_3035 = [
        Point(row.east3035 + 50.0, row.north3035 + 50.0) for row in df_3035.itertuples()
    ]
    gdf_3035 = gpd.GeoDataFrame(df_3035.copy(), geometry=pts_3035, crs="EPSG:3035")
    gdf_25832 = gdf_3035.to_crs("EPSG:25832")
    return gdf_25832


def test_assignment_places_household_in_its_own_cell_and_is_deterministic():
    buildings = _buildings_geo(_buildings_frame())

    # Two households in cell A, one in cell B.
    households = pd.DataFrame(
        {
            "household_id": ["hhA1", "hhA2", "hhB1"],
            "commune_id": ["031010000000"] * 3,
            "ZENSUS100m": [
                "CRS3035RES100mN2689100E4337000",  # cell A
                "CRS3035RES100mN2689100E4337000",  # cell A
                "CRS3035RES100mN2689200E4337000",  # cell B
            ],
        }
    )

    result, report = home_cell.assign_homes_to_cell_buildings(
        households, buildings, random_seed=42
    )

    by_hh = result.set_index("household_id")
    # cell-A households may only get a cell-A building (id 0 or 1), never cell-B.
    assert by_hh.loc["hhA1", "home_location_id"] in {0, 1}
    assert by_hh.loc["hhA2", "home_location_id"] in {0, 1}
    # cell-B household must get the single cell-B building (id 2).
    assert by_hh.loc["hhB1", "home_location_id"] == 2

    # All three placed in their own cell, none via commune fallback.
    assert report.n_households == 3
    assert report.n_in_cell == 3
    assert report.n_commune_fallback == 0
    assert report.n_unplaced == 0

    # Determinism: same seed -> identical assignment.
    result2, _ = home_cell.assign_homes_to_cell_buildings(
        households, buildings, random_seed=42
    )
    pd.testing.assert_series_equal(
        result.set_index("household_id")["home_location_id"],
        result2.set_index("household_id")["home_location_id"],
    )


def test_area_weighted_draw_favours_large_building():
    buildings = _buildings_geo(_buildings_frame())
    # Many households in cell A: the 1000 m building (id 1) should dominate the
    # 10 m building (id 0) under area weighting.
    households = pd.DataFrame(
        {
            "household_id": [f"hh{i}" for i in range(200)],
            "commune_id": ["031010000000"] * 200,
            "ZENSUS100m": ["CRS3035RES100mN2689100E4337000"] * 200,
        }
    )
    result, _ = home_cell.assign_homes_to_cell_buildings(
        households, buildings, random_seed=7
    )
    counts = result["home_location_id"].value_counts()
    # Large building (id 1, area 1000) must get the clear majority over id 0.
    assert counts.get(1, 0) > counts.get(0, 0)
    # Roughly proportional to area (1000 vs 10 ~ 99 %); allow generous slack.
    assert counts.get(1, 0) > 150


def test_commune_fallback_when_cell_has_no_building():
    buildings = _buildings_geo(_buildings_frame())
    # Household in a cell with NO building, but in the same commune (fallback).
    households = pd.DataFrame(
        {
            "household_id": ["hhX"],
            "commune_id": ["031010000000"],
            # An empty cell (no building there); commune still has buildings.
            "ZENSUS100m": ["CRS3035RES100mN2689900E4337900"],
        }
    )
    result, report = home_cell.assign_homes_to_cell_buildings(
        households, buildings, random_seed=1
    )
    by_hh = result.set_index("household_id")
    # Placed in SOME building of the commune (any of 0..3), never unplaced.
    assert by_hh.loc["hhX", "home_location_id"] in {0, 1, 2, 3}
    assert by_hh.loc["hhX", "geometry"] is not None

    assert report.n_households == 1
    assert report.n_in_cell == 0
    assert report.n_commune_fallback == 1
    assert report.n_unplaced == 0


def test_output_schema_and_one_row_per_household():
    buildings = _buildings_geo(_buildings_frame())
    households = pd.DataFrame(
        {
            "household_id": ["hhA1", "hhB1"],
            "commune_id": ["031010000000", "031010000000"],
            "ZENSUS100m": [
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2689200E4337000",
            ],
        }
    )
    result, _ = home_cell.assign_homes_to_cell_buildings(
        households, buildings, random_seed=0
    )
    assert list(result.columns) == [
        "household_id",
        "commune_id",
        "home_location_id",
        "geometry",
    ]
    assert len(result) == len(households)
    assert result["household_id"].is_unique
    assert result["geometry"].notna().all()
    # Geometry stays in the buildings' native CRS (EPSG:25832).
    assert result.crs is not None
    assert result.crs.to_epsg() == 25832
