import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon
from braunschweig.gravity.taz_margins import build_origin_population_per_taz


def _taz():
    a = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    b = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    return gpd.GeoDataFrame(
        {"taz_id": ["T1", "T2"], "commune_id": ["031010000000", "031010000000"],
         "kreis": ["03101", "03101"]},
        geometry=[a, b], crs="EPSG:25832",
    )


def _homes():  # 1 row per household, with a POINT
    return gpd.GeoDataFrame(
        {"household_id": [1, 2, 3]},
        geometry=[Point(5, 5), Point(5, 6), Point(15, 5)],  # hh1,hh2 in T1; hh3 in T2
        crs="EPSG:25832",
    )


def _population_per_person():  # PER-PERSON: weight=1.0, many rows per household
    return pd.DataFrame({
        "household_id": [1, 1, 2, 3, 3, 3],
        "commune_id":   ["031010000000"] * 6,
        "weight":       [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    })


def test_origin_margin_sums_persons_per_taz():
    df, primary, fallback = build_origin_population_per_taz(_homes(), _population_per_person(), _taz())
    out = df.set_index("taz_id")["population"]
    assert out["T1"] == 3.0   # hh1(2 persons) + hh2(1) = 3
    assert out["T2"] == 3.0   # hh3(3 persons)
    assert primary == 3 and fallback == 0


def test_origin_margin_conserves_total():
    df, _, _ = build_origin_population_per_taz(_homes(), _population_per_person(), _taz())
    assert df["population"].sum() == 6.0   # == OFF gravity commune weight sum


def test_origin_margin_outside_point_uses_commune_constrained_fallback():
    homes = gpd.GeoDataFrame({"household_id": [1]}, geometry=[Point(100, 100)], crs="EPSG:25832")
    pop = pd.DataFrame({"household_id": [1], "commune_id": ["031010000000"], "weight": [1.0]})
    df, primary, fallback = build_origin_population_per_taz(homes, pop, _taz())
    assert fallback == 1 and primary == 0
    assert df["population"].sum() == 1.0
