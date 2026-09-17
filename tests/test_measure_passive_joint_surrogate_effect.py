"""Realised-effect A/B of the passive escort surrogate anchor (issue #409): the pure metric
functions behind scripts/measure_passive_joint_surrogate_effect.py."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from scripts.measure_passive_joint_surrogate_effect import (
    EFFECT_COLUMNS, summarise_effect, surrogate_effect_rows,
)

CRS = "EPSG:25832"


def _locations(rows):
    frame = pd.DataFrame.from_records(rows, columns=["person_id", "activity_index", "geometry"])
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=CRS)


def test_effect_rows_measure_child_adult_and_home_distances_in_both_arms():
    surrogate_links = pd.DataFrame({
        "child_person_id": [5], "child_activity_index": [1], "adult_person_id": [4],
        "adult_activity_index": [1], "adult_purpose": ["shop"], "link_source": ["surrogate"],
        "gap_minutes": [5.0],
    })
    # ON arm: the child sits ON the adult (distance 0), 1000 m from home.
    on = _locations([(4, 1, Point(1000, 0)), (5, 1, Point(1000, 0))])
    # OFF arm: the adult is at the same shop, the child was drawn 300 m away, 700 m from home.
    off = _locations([(4, 1, Point(1000, 0)), (5, 1, Point(700, 0))])
    homes = gpd.GeoDataFrame({"household_id": [20], "geometry": [Point(0, 0)]}, geometry="geometry", crs=CRS)
    persons = pd.DataFrame({"person_id": [4, 5], "household_id": [20, 20]})
    child_purposes = pd.DataFrame({"person_id": [5], "trip_index": [0], "following_purpose": ["shop"]})

    rows = surrogate_effect_rows("strict", surrogate_links, on, off, homes, persons, child_purposes)
    assert list(rows.columns) == EFFECT_COLUMNS
    row = rows.iloc[0]
    assert row["arm"] == "strict" and row["child_purpose"] == "shop"
    assert row["dist_child_adult_m_arm"] == pytest.approx(0.0)
    assert row["dist_child_adult_m_off"] == pytest.approx(300.0)
    assert row["dist_home_m_arm"] == pytest.approx(1000.0)
    assert row["dist_home_m_off"] == pytest.approx(700.0)


def test_effect_rows_raise_when_a_linked_activity_has_no_placement():
    surrogate_links = pd.DataFrame({
        "child_person_id": [5], "child_activity_index": [1], "adult_person_id": [4],
        "adult_activity_index": [1], "adult_purpose": ["shop"], "link_source": ["surrogate"],
        "gap_minutes": [5.0],
    })
    on = _locations([(4, 1, Point(1000, 0))])          # the child's row is missing
    homes = gpd.GeoDataFrame({"household_id": [20], "geometry": [Point(0, 0)]}, geometry="geometry", crs=CRS)
    persons = pd.DataFrame({"person_id": [4, 5], "household_id": [20, 20]})
    child_purposes = pd.DataFrame({"person_id": [5], "trip_index": [0], "following_purpose": ["shop"]})
    with pytest.raises(ValueError, match="no placed location"):
        surrogate_effect_rows("strict", surrogate_links, on, on, homes, persons, child_purposes)


def test_summary_aggregates_per_arm_and_purpose():
    rows = pd.DataFrame({
        "arm": ["strict", "strict", "relaxed"], "child_person_id": [5, 6, 5],
        "child_activity_index": [1, 1, 1], "child_purpose": ["shop", "shop", "other"],
        "adult_person_id": [4, 4, 4], "adult_activity_index": [1, 1, 2], "gap_minutes": [5.0, 7.0, 1.0],
        "dist_child_adult_m_arm": [0.0, 0.0, 0.0], "dist_child_adult_m_off": [300.0, 500.0, 100.0],
        "dist_home_m_arm": [1000.0, 1200.0, 900.0], "dist_home_m_off": [700.0, 800.0, 950.0],
    })
    summary = summarise_effect(rows).set_index(["arm", "child_purpose"])
    assert summary.loc[("strict", "shop"), "n"] == 2
    assert summary.loc[("strict", "shop"), "dist_child_adult_m_off_mean"] == pytest.approx(400.0)
    assert summary.loc[("strict", "shop"), "dist_home_m_arm_median"] == pytest.approx(1100.0)
    assert summary.loc[("relaxed", "other"), "n"] == 1
