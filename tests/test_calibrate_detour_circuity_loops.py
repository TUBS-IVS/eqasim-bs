"""The detour circuity calibration leaves loop legs (strolls, dog walks) out of its OD pairs (#679).

A loop activity sits at the place of the activity before it, so the leg into it has no
displacement; as an OD pair of the walk pool it would be a zero-distance pair that carries no
information about the detour. The hand-built frames follow the layout of the cached stages the
script reads (the same layout as ``tests/test_secondary_subtype_validation.py``).
"""
from __future__ import annotations

import importlib.util
import pathlib

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from scripts.validate_secondary_distances import AmbiguousCacheRunError, RunPickles

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "calibrate_detour_circuity.py"
_spec = importlib.util.spec_from_file_location("calibrate_detour_circuity", _SCRIPT)
_calibrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_calibrate)


def _stage_frames():
    """One person: home, shop (1 km), a stroll that starts at the shop, a leisure leg of 3 km."""
    xs = [0.0, 1000.0, 1000.0, 4000.0, 0.0]
    df_xy = pd.DataFrame({"person_id": 1, "activity_index": range(5), "x_m": xs, "y_m": 0.0,
                          "commune_id": "03101000"})
    df_acts = pd.DataFrame({"person_id": 1, "activity_index": range(5),
                            "purpose": ["home", "shop", "leisure", "leisure", "home"]})
    df_trips = pd.DataFrame({"person_id": 1, "trip_index": range(1, 5),
                             "mode": ["walk", "walk", "walk", "car"]})
    return df_xy, df_acts, df_trips


def _marker(loop_origin):
    return pd.DataFrame({"person_id": 1, "activity_index": range(5), "loop_origin": loop_origin})


def test_the_leg_into_a_marked_loop_is_left_out_of_the_secondary_od_pairs():
    legs = _calibrate._build_secondary_legs(
        *_stage_frames(), df_loop_origin=_marker([None, None, "secondary", None, None]))
    # Activities 1 (shop), 3 (leisure) and 4 (home, not secondary) -> legs into 1 and 3 only.
    assert legs["activity_index"].tolist() == [1, 3]
    assert "loop_origin" not in legs.columns
    assert legs["prev_x"].tolist() == [0.0, 1000.0]
    assert legs["x_m"].tolist() == [1000.0, 4000.0]


def test_without_a_loop_marker_every_secondary_leg_stays():
    legs = _calibrate._build_secondary_legs(*_stage_frames(), df_loop_origin=None)
    assert legs["activity_index"].tolist() == [1, 2, 3]


def test_a_secondary_activity_without_a_marker_row_stops_the_calibration():
    marker = _marker([None, None, "secondary", None, None]).query("activity_index != 3")
    with pytest.raises(RuntimeError, match=r"1/3 secondary activities.*no row"):
        _calibrate._build_secondary_legs(*_stage_frames(), df_loop_origin=marker)


def _fake_stages(monkeypatch, marker, resolution_error=None):
    """Patch the cache readers of ``_extract_od_pools`` with the hand-built stages of one person
    (a worker); the education stage is absent (RuntimeError, as in a cache without it)."""
    df_xy, df_acts, df_trips = _stage_frames()
    locations = gpd.GeoDataFrame(
        df_xy[["person_id", "activity_index", "commune_id"]],
        geometry=[Point(x, 0.0) for x in df_xy["x_m"]], crs="EPSG:25832")
    stages = {
        "synthesis.population.spatial.home.locations": gpd.GeoDataFrame(
            {"household_id": [10]}, geometry=[Point(0.0, 0.0)], crs="EPSG:25832"),
        "braunschweig.synthesis.spatial.home_zones": pd.DataFrame(
            {"household_id": [10], "commune_id": ["03101000"]}),
        "braunschweig.synthesis.population.enriched": pd.DataFrame(
            {"person_id": [1], "household_id": [10], "employed": [True], "age": [40]}),
        "braunschweig.locations.work": gpd.GeoDataFrame(
            {"commune_id": ["03101000"]}, geometry=[Point(2000.0, 0.0)], crs="EPSG:25832"),
    }
    run_frames = {"L": locations, "A": df_acts, "T": df_trips, "S": None}

    def fake_load_stage(working_directory, stage):
        if stage not in stages:
            raise RuntimeError(f"No cached pickle found for stage '{stage}'")
        return stages[stage]

    def fake_resolve(working_directory, tag="[circuity]"):
        if resolution_error is not None:
            raise resolution_error
        return RunPickles(locations="L", activities="A", trips="T", secondary="S")

    monkeypatch.setattr(_calibrate, "_load_stage", fake_load_stage)
    monkeypatch.setattr(_calibrate, "resolve_run_pickles", fake_resolve)
    monkeypatch.setattr(_calibrate, "read_pickle", lambda path: run_frames[path])
    monkeypatch.setattr(_calibrate, "load_loop_origin", lambda *args, **kwargs: marker)


def test_the_od_pools_leave_the_loop_leg_out_and_a_marker_mismatch_is_not_swallowed(monkeypatch):
    """Through ``_extract_od_pools``: the walk pool holds the shop leg and the 3 km leisure leg
    (the stroll is left out) and the car pool the commute only; a marker pickle of another run
    raises out of the stage-not-found handling (a RuntimeError too) instead of being logged
    away."""
    _fake_stages(monkeypatch, _marker([None, None, "secondary", None, None]))
    car_origins, _, walk_origins, walk_dests, _, _ = _calibrate._extract_od_pools("unused")
    assert walk_dests.tolist() == [[1000.0, 0.0], [4000.0, 0.0]]
    assert len(car_origins) == 1

    _fake_stages(monkeypatch, _marker([None, None, "secondary", None, None]).query(
        "activity_index != 3"))
    with pytest.raises(RuntimeError, match=r"no row"):
        _calibrate._extract_od_pools("unused")


def test_a_cache_that_does_not_say_which_run_the_legs_belong_to_stops_the_calibration(monkeypatch):
    """The stage-not-found handling of ``_extract_od_pools`` logs and goes on without secondary
    pairs; an ambiguous cache (several runs, no link in pipeline.json) must not be handled that
    way."""
    _fake_stages(monkeypatch, None, resolution_error=AmbiguousCacheRunError("[circuity] ambiguous"))
    with pytest.raises(AmbiguousCacheRunError, match="ambiguous"):
        _calibrate._extract_od_pools("unused")

    _fake_stages(monkeypatch, None, resolution_error=RuntimeError("No cached pickle found"))
    _, _, walk_origins, _, _, _ = _calibrate._extract_od_pools("unused")
    assert len(walk_origins) == 0     # the ordinary stage-not-found: no secondary pairs, no stop


def test_activities_that_do_not_cover_the_locations_stop_the_calibration():
    df_xy, df_acts, df_trips = _stage_frames()
    with pytest.raises(RuntimeError, match=r"same \(person_id, activity_index\)"):
        _calibrate._build_secondary_legs(
            df_xy, df_acts[df_acts["activity_index"] != 3], df_trips, df_loop_origin=None)
