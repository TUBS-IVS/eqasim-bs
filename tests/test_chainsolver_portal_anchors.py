"""The gate of a portal stay splits the chain like work or education and reaches the locations output."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations import secondary_chainsolvers as sc
from braunschweig.synthesis.locations.secondary_chainsolvers import portal_anchors
from braunschweig.synthesis.portal_trips.config_keys import DEFAULT_ENABLED, KEY_ENABLED
from synthesis.population.spatial.secondary import problems

ANCHORS_STAGE = "braunschweig.synthesis.portal_trips.anchors"


def _anchors():
    return gpd.GeoDataFrame({"person_id": [7], "activity_index": [2], "gate_id": ["gate_e"], "kind": ["road"],
                             "mode": ["car"]}, geometry=[Point(40000.0, 0.0)], crs="EPSG:25832")


def test_outside_is_a_fixed_anchored_purpose_of_the_problem_splitter():
    assert "outside" in problems.FIXED_PURPOSES and "outside" in problems.ANCHORED_PURPOSES


def test_anchors_dict_and_location_rows_follow_the_escort_anchor_shapes():
    anchors = portal_anchors.anchors_dict(_anchors())
    assert anchors[(7, 2)].coords[0] == (40000.0, 0.0)
    rows = portal_anchors.location_rows(_anchors())
    assert list(rows.columns) == ["person_id", "activity_index", "location_id", "geometry"]
    # Coordinate-only activity: -1 like the in-commuter gate home (no "portal_*" facility exists);
    # object dtype like the resident location_id column; the gate id stays traceable in the anchors frame.
    assert rows.loc[0, "location_id"] == -1
    assert rows["location_id"].dtype == object
    assert portal_anchors.merge_anchors(None, {}) is None
    assert portal_anchors.merge_anchors({(1, 1): Point(0, 0)}, anchors) == {(1, 1): Point(0, 0), (7, 2): anchors[(7, 2)]}


def test_location_rows_of_an_empty_anchors_frame_keep_the_columns():
    rows = portal_anchors.location_rows(_anchors().iloc[0:0])
    assert list(rows.columns) == ["person_id", "activity_index", "location_id", "geometry"]
    assert len(rows) == 0


def test_find_assignment_problems_splits_a_chain_at_the_outside_anchor():
    trips = pd.DataFrame({
        "person_id": [7, 7, 7], "trip_index": [0, 1, 2],
        "preceding_purpose": ["home", "shop", "outside"], "following_purpose": ["shop", "outside", "home"],
        "mode": ["walk", "car", "car"], "travel_time": [900.0, 3600.0, 3600.0]})
    locations = pd.DataFrame({"person_id": [7], "home": [Point(0.0, 0.0)], "work": [None], "education": [None]})
    found = list(problems.find_assignment_problems(trips, locations, activity_anchors=portal_anchors.anchors_dict(_anchors())))
    # home -> shop -> outside is one problem (shop variable, both ends fixed); outside -> home has no variable activity
    assert len(found) == 1
    assert found[0]["purposes"] == ["shop"]
    assert found[0]["destination"].tolist() == [[40000.0, 0.0]]


class _FakeContext:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.registered = {}
        self.staged = []

    def config(self, key, default=None, volatile=False):
        if key in self.registered:
            return self.registered[key]
        value = self.overrides.get(key, default)
        self.registered[key] = value
        return value

    def stage(self, name, *a, **k):
        self.staged.append(name)
        return None


def test_configure_stages_the_anchors_when_the_feature_is_on():
    ctx = _FakeContext()
    sc.configure(ctx)
    assert ctx.registered[KEY_ENABLED] == DEFAULT_ENABLED
    assert DEFAULT_ENABLED is True
    assert ANCHORS_STAGE in ctx.staged


def test_configure_does_not_stage_the_anchors_when_the_feature_is_off():
    ctx = _FakeContext({KEY_ENABLED: False})
    sc.configure(ctx)
    assert ctx.registered[KEY_ENABLED] is False
    assert ANCHORS_STAGE not in ctx.staged


def test_portal_anchor_lines_reach_the_module_logger(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger=sc.logger.name):
        sc._apply_portal_anchors(_anchors(), None)
    assert any("portal anchors: 1 outside stays fixed at their gates" in m for m in caplog.messages)


def test_apply_portal_anchors_merges_into_the_escort_anchors():
    merged, rows = sc._apply_portal_anchors(_anchors(), {(1, 1): Point(0, 0)})
    assert set(merged) == {(1, 1), (7, 2)}
    assert len(rows) == 1


def test_apply_portal_anchors_off_leaves_the_escort_anchors_untouched():
    escort = {(1, 1): Point(0, 0)}
    merged, rows = sc._apply_portal_anchors(None, escort)
    assert merged is escort
    assert rows is None


def test_outside_trips_without_any_portal_anchor_raise_a_clear_error():
    trips = pd.DataFrame({"person_id": [7, 7], "trip_index": [0, 1],
                          "preceding_purpose": ["home", "outside"], "following_purpose": ["outside", "home"]})
    with pytest.raises(ValueError, match="portal trips without anchors"):
        sc._require_anchors_for_outside_trips(_anchors().iloc[0:0], trips)


def test_outside_trips_with_anchors_and_trips_without_outside_pass():
    trips = pd.DataFrame({"person_id": [7, 7], "trip_index": [0, 1],
                          "preceding_purpose": ["home", "outside"], "following_purpose": ["outside", "home"]})
    sc._require_anchors_for_outside_trips(_anchors(), trips)
    plain = trips.assign(preceding_purpose=["home", "shop"], following_purpose=["shop", "home"])
    sc._require_anchors_for_outside_trips(_anchors().iloc[0:0], plain)
