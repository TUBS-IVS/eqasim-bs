import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon
from braunschweig.gravity.taz_margins import build_origin_population_per_taz, assign_taz


def _taz():
    # T1,T2 in commune 03101000 / kreis 03101; T3 in commune 03154000 / kreis 03154 (far away).
    t1 = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    t2 = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    t3 = Polygon([(100, 0), (100, 10), (110, 10), (110, 0)])
    return gpd.GeoDataFrame(
        {"taz_id": ["T1", "T2", "T3"],
         "commune_id": ["03101000", "03101000", "03154000"],   # 8-digit AGS (TAZ stage contract)
         "kreis": ["03101", "03101", "03154"]},
        geometry=[t1, t2, t3], crs="EPSG:25832",
    )


def _homes():
    return gpd.GeoDataFrame(
        {"household_id": [1, 2, 3]},
        geometry=[Point(5, 5), Point(15, 5), Point(105, 5)],   # hh1->T1, hh2->T2, hh3->T3
        crs="EPSG:25832",
    )


def _pop_per_person():
    # PER-PERSON, weight=1.0, commune_id 12-digit ARS (popsim.stage contract)
    return pd.DataFrame({
        "household_id": [1, 1, 2, 3, 3, 3],
        "commune_id":   ["031010000000"] * 3 + ["031540000000"] * 3,
        "weight":       [1.0] * 6,
    })


def test_origin_margin_sums_persons_per_taz():
    df, primary, fallback = build_origin_population_per_taz(_homes(), _pop_per_person(), _taz())
    out = df.set_index("taz_id")["population"]
    assert out["T1"] == 2.0 and out["T2"] == 1.0 and out["T3"] == 3.0
    assert primary == 3 and fallback == 0


def test_origin_margin_conserves_total():
    df, _, _ = build_origin_population_per_taz(_homes(), _pop_per_person(), _taz())
    assert df["population"].sum() == 6.0


def test_origin_margin_handles_mixed_household_id_dtype():
    # Regression for the flag-ON server e2e: the real population producer
    # (data.census.filtered) and the home-point producer (home_cell) can carry
    # household_id in DIFFERENT dtypes (int64 vs object). A pandas merge on mixed
    # dtypes raises "trying to merge on int64 and object columns"; the helper must
    # normalise both to str first.
    homes = _homes()
    homes["household_id"] = homes["household_id"].astype(str)   # object
    pop = _pop_per_person()                                     # int64
    df, primary, fallback = build_origin_population_per_taz(homes, pop, _taz())
    out = df.set_index("taz_id")["population"]
    assert out["T1"] == 2.0 and out["T2"] == 1.0 and out["T3"] == 3.0
    assert df["population"].sum() == 6.0


def test_origin_margin_fallback_stays_in_kreis():
    # hh4 at (95,5): nearest polygon is T3 (kreis 03154), but hh4's commune is kreis 03101
    # -> the constrained fallback MUST pick a 03101 TAZ (T1/T2), never T3.
    homes = gpd.GeoDataFrame({"household_id": [4]}, geometry=[Point(95, 5)], crs="EPSG:25832")
    pop = pd.DataFrame({"household_id": [4], "commune_id": ["031010000000"], "weight": [1.0]})
    df, primary, fallback = build_origin_population_per_taz(homes, pop, _taz())
    assert primary == 0 and fallback == 1
    assert df["commune_id"].iloc[0] == "03101000"      # stayed in kreis 03101, NOT 03154
    assert df["population"].sum() == 1.0


def test_assign_taz_dedups_on_key_not_index():
    # distinct points sharing an index label must NOT be collapsed (B3 regression guard).
    homes = gpd.GeoDataFrame(
        {"household_id": [1, 2]}, geometry=[Point(5, 5), Point(15, 5)], crs="EPSG:25832",
        index=[0, 0],
    )
    out, primary, fallback = assign_taz(homes, _taz(), id_column="household_id")
    assert len(out) == 2 and set(out["household_id"]) == {1, 2}
