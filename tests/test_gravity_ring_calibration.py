from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.calibrate_gravity_per_rs7 import kreis_distance_to_zgb  # noqa: E402
from scripts.calibrate_gravity_per_rs7 import select_ring_anchors  # noqa: E402
from scripts.calibrate_gravity_per_rs7 import is_identified  # noqa: E402


class _Pt:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def test_distance_to_zgb_is_zero_at_centroid_mean():
    kreise = pd.DataFrame({
        "ars5": ["03101", "03102", "09999"],
        "centroid": [_Pt(0.0, 0.0), _Pt(2000.0, 0.0), _Pt(2000.0, 0.0)],
    })
    out = kreis_distance_to_zgb(kreise, zgb=("03101", "03102"))
    d = dict(zip(out["ars5"], out["dist_km"]))
    assert d["03101"] == 1.0
    assert round(d["09999"], 6) == 1.0


def _kreis_to_rs7(rows):
    return pd.DataFrame(rows, columns=["ars5", "dominant_rs7"])


def test_ring_grows_until_each_required_code_has_min_anchors():
    dist = pd.DataFrame({
        "ars5": ["A", "B", "C", "D"],
        "dist_km": [50.0, 60.0, 200.0, 210.0],
    })
    k2rs7 = _kreis_to_rs7([("A", 72), ("B", 72), ("C", 74), ("D", 74)])
    radius, anchors, counts = select_ring_anchors(
        dist, k2rs7, required_rs7={72, 74}, min_anchors=2,
        max_radius_km=400.0, step_km=25.0,
    )
    assert radius == 225.0
    assert set(anchors) == {"A", "B", "C", "D"}
    assert counts == {72: 2, 74: 2}


def test_ring_stops_at_max_radius_when_underfilled():
    dist = pd.DataFrame({"ars5": ["A", "B"], "dist_km": [10.0, 20.0]})
    k2rs7 = _kreis_to_rs7([("A", 72), ("B", 72)])
    radius, anchors, counts = select_ring_anchors(
        dist, k2rs7, required_rs7={72, 74}, min_anchors=2,
        max_radius_km=100.0, step_km=25.0,
    )
    assert radius == 100.0
    assert counts.get(74, 0) == 0


def test_is_identified_requires_margin_over_destinations():
    sub = pd.DataFrame({
        "dest_ars": [f"D{i % 12}" for i in range(30)],
        "distance_km": np.linspace(5, 100, 30),
        "flow": np.ones(30),
    })
    assert is_identified(sub, min_obs_margin=10) is True


def test_is_identified_false_when_obs_barely_exceed_destinations():
    sub = pd.DataFrame({
        "dest_ars": [f"D{i}" for i in range(13)],
        "distance_km": np.linspace(5, 100, 13),
        "flow": np.ones(13),
    })
    assert is_identified(sub, min_obs_margin=10) is False
