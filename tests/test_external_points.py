import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from braunschweig.data.cordon.external_points import (
    _largest_remainder,
    gemeinde_external_points,
)


def _gem(ars5, gem_ags, ewz, cx):
    return {"ars5": ars5, "gem_ags": gem_ags, "ewz": ewz,
            "geometry": Polygon([(cx, 0), (cx + 1, 0), (cx + 1, 1), (cx, 1)])}


def test_split_is_population_weighted_and_sums_to_kreis_flow():
    gem = gpd.GeoDataFrame([
        _gem("03241", "03241001", 75000, 0),
        _gem("03241", "03241002", 25000, 10),
        _gem("15083", "15083001", 10000, 50),
    ], crs="EPSG:25832")
    kreis_flow = pd.DataFrame({"ars5": ["03241", "15083"], "flow": [1000, 40]})
    out = gemeinde_external_points(kreis_flow, gem, min_flow=50)
    assert set(out["ars5"]) == {"03241"}              # 15083 below threshold dropped
    assert out["flow"].sum() == 1000                  # exact largest-remainder
    f = dict(zip(out["gem_ags"], out["flow"]))
    assert f["03241001"] == 750 and f["03241002"] == 250
    assert (out["commune_id"] == "EXT" + out["gem_ags"]).all()
    assert (out["commune_id"].str[3:8] == out["ars5"]).all()   # Kreis recoverable
    assert out["commune_id"].is_unique


def test_zero_pop_gemeinden_excluded_and_kreis_total_preserved():
    gem = gpd.GeoDataFrame([
        _gem("03241", "03241001", 100000, 0),
        _gem("03241", "03241002", 0, 10),
    ], crs="EPSG:25832")
    kreis_flow = pd.DataFrame({"ars5": ["03241"], "flow": [500]})
    out = gemeinde_external_points(kreis_flow, gem, min_flow=50)
    assert len(out) == 1 and out["flow"].iloc[0] == 500


def test_min_flow_boundary_kept():
    """A Kreis with flow exactly == min_flow must be kept (>= not >)."""
    gem = gpd.GeoDataFrame([
        _gem("03241", "03241001", 50000, 0),
    ], crs="EPSG:25832")
    kreis_flow = pd.DataFrame({"ars5": ["03241"], "flow": [50]})
    out = gemeinde_external_points(kreis_flow, gem, min_flow=50)
    assert len(out) == 1 and out["flow"].iloc[0] == 50


def test_largest_remainder_sums_exactly_to_total():
    """Non-divisible case: total=10, equal weights=[1,1,1] -> [4,3,3] or permutation summing to 10."""
    result = _largest_remainder(10, [1, 1, 1])
    assert sum(result) == 10
    assert len(result) == 3
    assert all(v >= 0 for v in result)
