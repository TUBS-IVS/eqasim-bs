import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from braunschweig.data.spatial.taz import load_taz_zones, REQUIRED_COLUMNS


def _valid_taz_gdf():
    poly_a = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    poly_b = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    return gpd.GeoDataFrame(
        {
            "taz_id": ["310101901", "310101902"],
            "taz_name": ["BS Stadtkern 6", "BS Stadtkern 7"],
            "commune_id": ["03101000", "03101000"],
            "kreis": ["03101", "03101"],
            "regiostar7": [72, 74],
        },
        geometry=[poly_a, poly_b], crs="EPSG:25832",
    )


def test_load_accepts_valid_input(tmp_path):
    path = tmp_path / "taz.parquet"
    _valid_taz_gdf().to_parquet(path)
    loaded = load_taz_zones(str(path))
    assert len(loaded) == 2
    assert loaded.crs.to_epsg() == 25832
    assert set(REQUIRED_COLUMNS).issubset(loaded.columns)


def test_load_rejects_missing_column(tmp_path):
    gdf = _valid_taz_gdf().drop(columns=["kreis"])
    path = tmp_path / "missing.parquet"
    gdf.to_parquet(path)
    with pytest.raises(ValueError, match="missing columns"):
        load_taz_zones(str(path))


def test_load_rejects_duplicate_taz_id(tmp_path):
    gdf = _valid_taz_gdf()
    gdf.loc[1, "taz_id"] = gdf.loc[0, "taz_id"]
    path = tmp_path / "dup.parquet"
    gdf.to_parquet(path)
    with pytest.raises(ValueError, match="duplicate taz_id"):
        load_taz_zones(str(path))


def test_load_rejects_out_of_range_rs7(tmp_path):
    gdf = _valid_taz_gdf()
    gdf.loc[0, "regiostar7"] = 12
    path = tmp_path / "rs7.parquet"
    gdf.to_parquet(path)
    with pytest.raises(ValueError, match="outside 71..77"):
        load_taz_zones(str(path))


def test_load_rejects_empty_geometry(tmp_path):
    gdf = _valid_taz_gdf()
    gdf["geometry"] = [Polygon(), Polygon()]
    path = tmp_path / "empty.parquet"
    gdf.to_parquet(path)
    with pytest.raises(ValueError, match="empty/missing geometry"):
        load_taz_zones(str(path))
