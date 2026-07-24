"""Escort activity options in the facilities writers (issue #201)."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

import matsim.scenario.facilities as base
from braunschweig.matsim.scenario import facilities as bs_facilities


def _candidates():
    return gpd.GeoDataFrame({
        "location_id": ["sec_b_1", "sec_res_9", "sec_edu_0"],
        "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        "offers_leisure": [True, False, False],
        "offers_shop": [False, False, False],
        "offers_other": [False, False, False],
        "offers_escort": [True, True, True],
        "offers_visit": [False, True, False],
        "offers_escort_residential": [False, True, False],
        "offers_escort_edu_kindergarten": [False, False, True],
        "offers_escort_edu_school": [False, False, False],
        "offers_escort_edu_university": [False, False, False],
    }, crs="EPSG:25832")


def test_secondary_fields_include_escort():
    assert "offers_escort" in base.SECONDARY_FIELDS


def test_secondary_facility_frame_visit_fold_is_conditional():
    on = bs_facilities.secondary_facility_frame(_candidates(), leisure_visit_enabled=True)
    assert bool(on.loc[on["location_id"] == "sec_res_9", "offers_leisure"].iloc[0])
    off = bs_facilities.secondary_facility_frame(_candidates(), leisure_visit_enabled=False)
    assert not bool(off.loc[off["location_id"] == "sec_res_9", "offers_leisure"].iloc[0])
    # escort offer survives the field selection in both cases
    assert bool(off.loc[off["location_id"] == "sec_edu_0", "offers_escort"].iloc[0])
