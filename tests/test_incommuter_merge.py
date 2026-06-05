"""Test the concat helper that appends in-commuter frames to resident stages."""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.incommuter_merge._base import concat_frame  # noqa: E402


def test_concat_empty_is_noop():
    res = pd.DataFrame({"person_id": [0, 1], "v": [10, 11]})
    out = concat_frame(res, pd.DataFrame(columns=["person_id", "v"]), "person_id")
    pd.testing.assert_frame_equal(out, res)


def test_concat_appends_aligns_to_resident_columns_and_sorts():
    res = pd.DataFrame({"person_id": [0, 1], "v": [10, 11]})
    inc = pd.DataFrame({"person_id": [100], "v": [99], "extra": [1]})  # extra dropped
    out = concat_frame(res, inc, "person_id")
    assert list(out["person_id"]) == [0, 1, 100]
    assert list(out.columns) == ["person_id", "v"]


def test_concat_geodataframe_keeps_type_and_crs():
    res = gpd.GeoDataFrame({"person_id": [0]}, geometry=[Point(0, 0)], crs="EPSG:25832")
    inc = gpd.GeoDataFrame({"person_id": [100]}, geometry=[Point(1, 1)], crs="EPSG:25832")
    out = concat_frame(res, inc, "person_id")
    assert isinstance(out, gpd.GeoDataFrame)
    assert str(out.crs) == "EPSG:25832"
    assert list(out["person_id"]) == [0, 100]
