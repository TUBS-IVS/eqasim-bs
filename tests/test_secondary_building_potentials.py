import geopandas as gpd
from shapely.geometry import Point
from braunschweig.synthesis.locations.secondary_chainsolvers import _build_locations_df


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
