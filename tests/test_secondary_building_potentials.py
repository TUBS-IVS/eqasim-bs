import numpy as np
import pytest
import pandas as pd
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


def _buildings(rows):
    """rows: (building_id, retail_daily, retail_non_daily, leisure, errands) tuples."""
    return gpd.GeoDataFrame(
        {"building_id": [r[0] for r in rows],
         "potential_retail_daily": [r[1] for r in rows],
         "potential_retail_non_daily": [r[2] for r in rows],
         "potential_leisure": [r[3] for r in rows],
         "potential_errands": [r[4] for r in rows],
         "commune_id": ["03101000"] * len(rows)},
        geometry=[Polygon([(10 * r[0], 0), (10 * r[0], 10), (10 * r[0] + 10, 10), (10 * r[0] + 10, 0)])
                  for r in rows],
        crs="EPSG:25832",
    )


def test_build_secondary_candidates_replace_shop_leisure_from_gpkg():
    out = build_secondary_candidates(_buildings([(7, 4.0, 3.0, 9.0, 2.0)]))
    gpkg = out[out["location_id"] == "sec_b_7"].iloc[0]
    assert gpkg["offers_shop"] and gpkg["offers_leisure"] and gpkg["offers_other"]
    assert gpkg["pot_shop"] == 7.0 and gpkg["pot_leisure"] == 9.0
    assert gpkg.geometry.geom_type == "Point"
    assert len(out) == 1


# ---------------------------------------------------------------------------
# "other" candidates = buildings with potential_errands > 0 (owner decision
# 2026-11-07); the legacy catalog points are no longer candidates.
# ---------------------------------------------------------------------------

def test_other_candidates_are_buildings_with_errand_potential():
    bld = _buildings([
        (0, 0.0, 0.0, 0.0, 42.0),   # errand-only building -> new sec_b_ row
        (1, 2.0, 1.0, 0.0, 7.0),    # retail + errand -> ONE row carrying both
        (2, 0.0, 0.0, 0.0, 0.0),    # nothing -> no row
        (3, 5.0, 0.0, 0.0, 0.0),    # retail only -> not an other candidate
    ])
    out = build_secondary_candidates(bld)
    assert out["location_id"].is_unique
    assert set(out["location_id"]) == {"sec_b_0", "sec_b_1", "sec_b_3"}
    out = out.set_index("location_id")
    assert out.loc["sec_b_0", "offers_other"] and out.loc["sec_b_0", "pot_other"] == 42.0
    assert not out.loc["sec_b_0", "offers_shop"] and not out.loc["sec_b_0", "offers_leisure"]
    assert out.loc["sec_b_1", "offers_other"] and out.loc["sec_b_1", "pot_other"] == 7.0
    assert out.loc["sec_b_1", "offers_shop"] and out.loc["sec_b_1", "pot_shop"] == 3.0
    assert not out.loc["sec_b_3", "offers_other"] and out.loc["sec_b_3", "pot_other"] == 0.0
    # every row, errand-only ones included, stays an escort candidate
    assert out["offers_escort"].all()


def test_zero_other_supply_raises():
    bld = _buildings([(0, 2.0, 1.0, 3.0, 0.0)])
    with pytest.raises(ValueError, match="other"):
        build_secondary_candidates(bld)


def test_errand_candidates_not_duplicated_by_srv_category_append():
    """The SrV errand-category append finds the errand buildings already present."""
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        append_location_category_columns,
    )
    bld = _buildings([(0, 0.0, 0.0, 0.0, 42.0), (1, 2.0, 1.0, 0.0, 7.0)])
    bld["bosserhof_class_clean"] = ["service", "service"]
    mapping = pd.DataFrame({"bosserhof_class": ["service"],
                            "location_category": ["errand_service"]})
    cand = build_secondary_candidates(bld)
    out = append_location_category_columns(cand, bld, mapping)
    assert out["location_id"].is_unique and len(out) == len(cand)
    out = out.set_index("location_id")
    assert out.loc["sec_b_0", "pot_errand_service"] == 42.0
    assert out.loc["sec_b_1", "pot_errand_service"] == 7.0
    assert out.loc["sec_b_1", "pot_other"] == 7.0
