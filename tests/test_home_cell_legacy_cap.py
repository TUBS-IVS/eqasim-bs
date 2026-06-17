"""Tests for _legacy_capped_buildings -- the 400 m2 cap restored for the legacy path.

The shared braunschweig.data.buildings stage was uncapped so the TYPED path can
use large MFH footprints (it bounds via capacity). The LEGACY area-weighted path
has no capacity mechanism, so without a cap a single large block would dominate
the lottery. _legacy_capped_buildings re-applies the pre-branch <400 m2 guard
on that path only.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig.synthesis.locations import home_cell


def _make_buildings(areas):
    """Return a minimal buildings GeoDataFrame with the given area_m2 values."""
    n = len(areas)
    records = {
        "building_id": list(range(n)),
        "area_m2": list(areas),
        "commune_id": ["test_commune"] * n,
        "geometry": [Point(float(i), 0.0) for i in range(n)],
    }
    return gpd.GeoDataFrame(records, crs="EPSG:25832")


def test_legacy_cap_removes_buildings_at_or_above_400():
    buildings = _make_buildings([50.0, 399.9, 400.0, 500.0, 1200.0])
    result = home_cell._legacy_capped_buildings(buildings)
    assert (result["area_m2"] < 400.0).all(), (
        "Expected all returned rows to have area_m2 < 400; "
        "got: {}".format(result["area_m2"].tolist())
    )


def test_legacy_cap_exact_count_of_sub400_rows():
    areas = [50.0, 399.9, 400.0, 500.0, 1200.0]
    buildings = _make_buildings(areas)
    result = home_cell._legacy_capped_buildings(buildings)
    expected_n = sum(1 for a in areas if a < 400.0)
    assert len(result) == expected_n, (
        "Expected {} rows (area_m2 < 400), got {}".format(expected_n, len(result))
    )


def test_legacy_cap_building_id_is_contiguous_0_to_n_minus_1():
    buildings = _make_buildings([10.0, 200.0, 400.0, 800.0])
    result = home_cell._legacy_capped_buildings(buildings)
    expected = list(range(len(result)))
    assert result["building_id"].tolist() == expected, (
        "Expected contiguous building_id 0..{}, got {}".format(
            len(result) - 1, result["building_id"].tolist()
        )
    )


def test_legacy_cap_no_large_footprint_survives():
    large_areas = [400.0, 401.0, 1000.0, 50000.0]
    small_areas = [1.0, 100.0, 399.9]
    buildings = _make_buildings(small_areas + large_areas)
    result = home_cell._legacy_capped_buildings(buildings)
    assert not (result["area_m2"] >= 400.0).any(), (
        "At least one >=400 m2 building survived the cap."
    )


def test_legacy_cap_all_sub400_input_unchanged_length():
    """If ALL input buildings are already <400 m2, nothing is dropped."""
    areas = [1.0, 50.0, 100.0, 200.0, 399.9]
    buildings = _make_buildings(areas)
    result = home_cell._legacy_capped_buildings(buildings)
    assert len(result) == len(buildings), (
        "All buildings were already <400 m2; none should be dropped. "
        "Input {}, output {}.".format(len(buildings), len(result))
    )
