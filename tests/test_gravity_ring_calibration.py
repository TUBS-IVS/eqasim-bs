from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# The per-RS7 calibrator was migrated into the calibration corner (PR #18); the
# scripts/ shim now only re-exports `main`, so import the helpers from their new home.
from braunschweig.calibration._legacy_gravity_per_rs7 import kreis_distance_to_zgb  # noqa: E402
from braunschweig.calibration._legacy_gravity_per_rs7 import select_ring_anchors  # noqa: E402
from braunschweig.calibration._legacy_gravity_per_rs7 import fit_panel_rs7_slopes  # noqa: E402


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


def test_panel_recovers_known_per_rs7_slopes():
    """The full-panel GLM recovers per-RS7 distance slopes from data generated
    by a known model (identification + correctness)."""
    origins = ["o1", "o2", "o3", "o4"]
    rs7 = {"o1": 72, "o2": 72, "o3": 74, "o4": 74}
    ofe = {"o1": 0.0, "o2": 0.3, "o3": 0.5, "o4": 0.1}
    dests = ["d1", "d2", "d3", "d4", "d5"]
    dfe = {"d1": 0.0, "d2": 0.2, "d3": -0.1, "d4": 0.4, "d5": 0.1}
    slope = {72: -0.04, 74: -0.09}      # rural (74) steeper than urban (72)
    const = 5.0
    # Irregular, NON-separable distance matrix: distance must not equal
    # (origin constant + dest constant), otherwise it is collinear with the
    # origin/dest fixed effects and only the slope DIFFERENCE is identified.
    dist_matrix = {
        "o1": [12.0, 40.0, 25.0, 60.0, 33.0],
        "o2": [55.0, 18.0, 70.0, 22.0, 48.0],
        "o3": [30.0, 65.0, 15.0, 50.0, 80.0],
        "o4": [42.0, 27.0, 58.0, 35.0, 19.0],
    }

    rows = []
    for o in origins:
        for j, d in enumerate(dests):
            dist = dist_matrix[o][j]
            mean = np.exp(const + ofe[o] + dfe[d] + slope[rs7[o]] * dist)
            rows.append({"orig_ars": o, "dest_ars": d, "distance_km": dist, "flow": mean})
    df = pd.DataFrame(rows)
    k2rs7 = pd.DataFrame({"ars5": origins, "dominant_rs7": [rs7[o] for o in origins]})

    slopes, diag = fit_panel_rs7_slopes(df, origins, k2rs7, max_distance_km=1000.0, rescale_to=None)

    assert diag["converged"] is True
    assert diag["n_obs"] == 20
    assert abs(slopes[72] - (-0.04)) < 1e-3
    assert abs(slopes[74] - (-0.09)) < 1e-3
    assert slopes[74] < slopes[72]            # rural decays faster (shorter trips)


def test_panel_rescales_to_target_mean():
    """With rescale_to set, the flow-weighted mean of the returned slopes equals
    the target (regional-mean-preserving)."""
    origins = ["o1", "o2", "o3", "o4"]
    rs7 = {"o1": 72, "o2": 72, "o3": 74, "o4": 74}
    dests = ["d1", "d2", "d3", "d4", "d5"]
    slope = {72: -0.04, 74: -0.09}
    rows = []
    for i, o in enumerate(origins):
        for j, d in enumerate(dests):
            dist = 10.0 + 12.0 * j + 7.0 * i
            mean = np.exp(5.0 + slope[rs7[o]] * dist)
            rows.append({"orig_ars": o, "dest_ars": d, "distance_km": dist, "flow": mean})
    df = pd.DataFrame(rows)
    k2rs7 = pd.DataFrame({"ars5": origins, "dominant_rs7": [rs7[o] for o in origins]})

    slopes, diag = fit_panel_rs7_slopes(df, origins, k2rs7, max_distance_km=1000.0, rescale_to=-0.065)
    flow = diag["flow_total_by_code"]
    wmean = sum(slopes[c] * flow[c] for c in slopes) / sum(flow.values())
    assert abs(wmean - (-0.065)) < 1e-3
