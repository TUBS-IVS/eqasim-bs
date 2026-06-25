import numpy as np
import geopandas as gpd
from shapely.geometry import Point, Polygon
from braunschweig.synthesis.locations.secondary_chainsolvers import (
    _build_locations_df,
    build_scorer,
    attach_secondary_potentials,
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
# C3: build_scorer and attach_secondary_potentials
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


def test_attach_secondary_potentials_maps_activities():
    cand = gpd.GeoDataFrame(
        {"location_id": ["sec_0", "sec_1"]},
        geometry=[Point(5, 5), Point(500, 500)], crs="EPSG:25832",
    )
    b = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    buildings = gpd.GeoDataFrame(
        {"building_id": [0],
         "potential_retail_daily": [4.0], "potential_retail_non_daily": [3.0],
         "potential_leisure": [9.0], "potential_generic": [100.0]},
        geometry=[b], crs="EPSG:25832",
    )
    out = attach_secondary_potentials(cand, buildings)
    # sec_0 is inside the building
    assert out.loc[0, "pot_shop"] == 7.0      # retail_daily + retail_non_daily
    assert out.loc[0, "pot_leisure"] == 9.0
    assert out.loc[0, "pot_other"] == 100.0
    # sec_1 outside -> fallback 0.0
    assert out.loc[1, "pot_shop"] == 0.0
    assert out.loc[1, "pot_leisure"] == 0.0
    assert out.loc[1, "pot_other"] == 0.0
