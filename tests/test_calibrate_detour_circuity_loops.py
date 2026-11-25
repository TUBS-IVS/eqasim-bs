"""The detour circuity calibration leaves loop legs (strolls, dog walks) out of its OD pairs (#679).

A loop activity sits at the place of the activity before it, so the leg into it has no
displacement; as an OD pair of the walk pool it would be a zero-distance pair that carries no
information about the detour. The hand-built frames follow the layout of the cached stages the
script reads (the same layout as ``tests/test_secondary_subtype_validation.py``).
"""
from __future__ import annotations

import importlib.util
import pathlib

import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "calibrate_detour_circuity", pathlib.Path("scripts/calibrate_detour_circuity.py"))
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
    """A marker pickle of another run must not turn the unmatched activity into 'not a loop'
    (and the stage-not-found handling of the caller must not swallow this error)."""
    marker = _marker([None, None, "secondary", None, None]).query("activity_index != 3")
    with pytest.raises(RuntimeError, match=r"1/3 secondary activities.*no row"):
        _calibrate._build_secondary_legs(*_stage_frames(), df_loop_origin=marker)
