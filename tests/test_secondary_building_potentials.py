import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon
from braunschweig.synthesis.locations.secondary_chainsolvers import (
    _build_locations_df,
    build_scorer,
    build_secondary_candidates,
)


def _candidates():
    return gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1"],
            "offers_shop": [True, False],
            "offers_leisure": [True, False],
            "offers_other": [True, True],
            "pot_shop": [7.0, 0.0],
            "pot_leisure": [3.0, 0.0],
            "pot_other": [100.0, 50.0],
        },
        geometry=[Point(1, 2), Point(3, 4)], crs="EPSG:25832",
    )


def test_potentials_string_aligned_to_activities():
    out = _build_locations_df(_candidates(), with_potentials=True)
    # row 0 offers shop+leisure+other -> 3 activities, 3 potentials in order
    assert out.loc[0, "activities"] == "shop; leisure; other"
    assert out.loc[0, "potentials"] == "7.0; 3.0; 100.0"
    # row 1 offers only other
    assert out.loc[1, "activities"] == "other"
    assert out.loc[1, "potentials"] == "50.0"


def test_without_potentials_is_legacy_schema():
    out = _build_locations_df(_candidates(), with_potentials=False)
    assert "potentials" not in out.columns
    assert list(out.columns) == ["id", "x", "y", "activities"]


# ---------------------------------------------------------------------------
# C3r: build_scorer and build_secondary_candidates
# ---------------------------------------------------------------------------

def test_build_scorer_none_when_disabled():
    assert build_scorer(enabled=False, mode="combined",
                        pot_weight=1.0, dist_dev_weight=1.0) is None


def test_build_scorer_combined_when_enabled():
    scorer = build_scorer(enabled=True, mode="combined",
                          pot_weight=2.0, dist_dev_weight=0.5)
    assert scorer is not None
    # the upstream scorer actually combines potentials and distance
    out = scorer.score(potentials=np.array([10.0, 0.0]),
                       dist_deviations=np.array([1.0, 1.0]))
    assert list(out) == [19.5, -0.5]   # 2*pot - 0.5*ddev


def test_build_secondary_candidates_replace_shop_leisure_from_gpkg():
    legacy = gpd.GeoDataFrame(
        {"location_id": ["sec_0"], "commune_id": ["03101000"], "iris_id": ["03101000"],
         "offers_shop": [True], "offers_leisure": [True], "offers_other": [True]},
        geometry=[Point(500, 500)], crs="EPSG:25832",   # far from the gpkg building
    )
    b = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])    # centroid (5,5)
    buildings = gpd.GeoDataFrame(
        {"building_id": [7],
         "potential_retail_daily": [4.0], "potential_retail_non_daily": [3.0],
         "potential_leisure": [9.0], "potential_generic": [100.0],
         "commune_id": ["03101000"]},
        geometry=[b], crs="EPSG:25832",
    )
    out = build_secondary_candidates(legacy, buildings)
    # one gpkg row (shop+leisure) + one legacy 'other' row
    gpkg = out[out["location_id"] == "sec_b_7"].iloc[0]
    assert gpkg["offers_shop"] and gpkg["offers_leisure"] and not gpkg["offers_other"]
    assert gpkg["pot_shop"] == 7.0 and gpkg["pot_leisure"] == 9.0
    assert gpkg.geometry.geom_type == "Point"
    other = out[out["location_id"] == "sec_0"].iloc[0]
    assert other["offers_other"] and not other["offers_shop"] and not other["offers_leisure"]
    assert other["pot_other"] == 0.0     # legacy point far from the gpkg building -> generic fallback 0.0
    assert len(out) == 2
