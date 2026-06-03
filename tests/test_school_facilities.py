import numpy as np
import pandas as pd
import pytest
from braunschweig.data.schools.facilities import build_facilities_frame


def _csv_frame():
    return pd.DataFrame({
        "school_id": ["abs_1", "abs_2", "bbs_3"],
        "name": ["A", "B", "C"],
        "level": ["grundschule", "sekundar_1", "sekundar_2"],
        "capacity": [200.0, 150.0, 800.0],
        "ags8": ["03101000", "03158001", "03101000"],
        "kreis5": ["03101", "03158", "03101"],
        "x": [605000.0, 612000.0, 605500.0],
        "y": [5790000.0, 5780000.0, 5790500.0],
        "geocode_quality": ["address", "address", "plz_centroid"],
        "dist_to_osm_edu_m": [40.0, 120.0, 300.0],
        "validated": [True, True, True],
    })


def test_build_facilities_frame_schema_and_crs():
    gdf = build_facilities_frame(_csv_frame())
    assert list(gdf.columns) == ["school_id", "level", "capacity",
                                 "commune_id", "geometry"]
    assert gdf.crs.to_string() == "EPSG:25832"
    assert set(gdf["level"]) == {"grundschule", "sekundar_1", "sekundar_2"}
    assert (gdf["capacity"] > 0).all()
    assert gdf.iloc[0]["commune_id"] == "03101000"


def test_build_facilities_frame_rejects_missing_level():
    df = _csv_frame()
    df = df[df["level"] != "sekundar_2"]
    with pytest.raises(RuntimeError, match="missing school level"):
        build_facilities_frame(df)


def test_build_facilities_frame_rejects_nan_coords():
    df = _csv_frame()
    df.loc[0, "x"] = np.nan
    with pytest.raises(RuntimeError, match="NaN coordinate"):
        build_facilities_frame(df)
