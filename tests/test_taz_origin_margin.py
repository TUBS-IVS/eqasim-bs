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
    # The TAZ stage carries commune_id as 8-digit AGS.
    t1 = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    t2 = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    t3 = Polygon([(100, 0), (100, 10), (110, 10), (110, 0)])
    return gpd.GeoDataFrame(
        {"taz_id": ["T1", "T2", "T3"],
         "commune_id": ["03101000", "03101000", "03154000"],
         "kreis": ["03101", "03101", "03154"]},
        geometry=[t1, t2, t3], crs="EPSG:25832",
    )


def _homes():
    # home_cell contract: [household_id, commune_id (12-digit ARS), geometry] per household.
    # commune 031010000000: 3 homes in T1 + 1 in T2. commune 031540000000: 2 homes in T3.
    # (household_id is deliberately in a DIFFERENT id space than the population -- the
    #  redesign must NOT rely on it; it splits per commune_id.)
    return gpd.GeoDataFrame(
        {"household_id": [242, 251, 277, 346, 550, 804],
         "commune_id": ["031010000000"] * 4 + ["031540000000"] * 2},
        geometry=[Point(2, 5), Point(5, 5), Point(8, 5), Point(15, 5),
                  Point(103, 5), Point(107, 5)],
        crs="EPSG:25832",
    )


def _population():
    # data.census.filtered contract: PER-PERSON, commune_id (12-digit ARS) + weight.
    # household_id here is the FULL-population composite string (disjoint from home_cell ints).
    return pd.DataFrame({
        "household_id": ["CRS_a_0", "CRS_a_0", "CRS_b_0", "CRS_b_0",   # commune 03101, total 100
                         "CRS_c_0", "CRS_c_0"],                        # commune 03154, total 40
        "commune_id": ["031010000000"] * 4 + ["031540000000"] * 2,
        "weight":     [30.0, 20.0, 25.0, 25.0, 15.0, 25.0],
    })


def test_origin_margin_splits_commune_pop_by_home_share():
    # commune 031010000000 pop = 100, homes 3/4 in T1 + 1/4 in T2 -> T1=75, T2=25.
    # commune 031540000000 pop = 40, both homes in T3 -> T3=40.
    df, primary, fallback = build_origin_population_per_taz(_homes(), _population(), _taz())
    out = df.set_index("taz_id")["population"]
    assert abs(out["T1"] - 75.0) < 1e-9
    assert abs(out["T2"] - 25.0) < 1e-9
    assert abs(out["T3"] - 40.0) < 1e-9
    assert primary == 6 and fallback == 0


def test_origin_margin_conserves_per_commune_total():
    # each commune's census population is fully distributed across its TAZ (shares sum to 1).
    df, _, _ = build_origin_population_per_taz(_homes(), _population(), _taz())
    by_commune = df.groupby("commune_id")["population"].sum()
    assert abs(by_commune["031010000000"] - 100.0) < 1e-9
    assert abs(by_commune["031540000000"] - 40.0) < 1e-9
    assert abs(df["population"].sum() - 140.0) < 1e-9


def test_origin_margin_no_household_id_join_needed():
    # The population and home household_id spaces are DISJOINT (composite string vs int).
    # The redesign keys on commune_id, so it must still work (this is the e2e root-cause
    # regression: the old household_id join produced a 100%-empty margin).
    df, _, _ = build_origin_population_per_taz(_homes(), _population(), _taz())
    assert df["population"].sum() > 0.0
    assert abs(df["population"].sum() - 140.0) < 1e-9


def test_origin_margin_fallback_stays_in_kreis():
    # A home at (95,5) is geometrically nearest to T3 (kreis 03154), but its commune is
    # kreis 03101 -> the constrained fallback must pick a 03101 TAZ (T1/T2), never T3;
    # the commune's population lands in a 03101 TAZ.
    homes = gpd.GeoDataFrame(
        {"household_id": [999], "commune_id": ["031010000000"]},
        geometry=[Point(95, 5)], crs="EPSG:25832")
    pop = pd.DataFrame({"household_id": ["CRS_x_0"], "commune_id": ["031010000000"], "weight": [50.0]})
    df, primary, fallback = build_origin_population_per_taz(homes, pop, _taz())
    assert primary == 0 and fallback == 1
    assert set(df["taz_id"]).issubset({"T1", "T2"})   # stayed in kreis 03101
    assert abs(df["population"].sum() - 50.0) < 1e-9


def test_assign_taz_dedups_on_key_not_index():
    # distinct points sharing an index label must NOT be collapsed (B3 regression guard).
    homes = gpd.GeoDataFrame(
        {"household_id": [1, 2]}, geometry=[Point(5, 5), Point(15, 5)], crs="EPSG:25832",
        index=[0, 0],
    )
    out, primary, fallback = assign_taz(homes, _taz(), id_column="household_id")
    assert len(out) == 2 and set(out["household_id"]) == {1, 2}
