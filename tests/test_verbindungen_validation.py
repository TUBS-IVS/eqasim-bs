"""Tests for the VerBindungen validation metrics (hand-computed values).

Run with::

    python -m pytest tests/test_verbindungen_validation.py -v
"""
from __future__ import annotations

import math
import pathlib
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cells():
    # Two 1000m x 1000m cells side by side (EPSG:25832), centroids 1000m apart.
    return gpd.GeoDataFrame({
        "cell_id": ["A", "B"],
        "kreis_id": ["03101", "03151"],
        "is_stadtteil": [False, False],
        "centroid_x": [500.0, 1500.0],
        "centroid_y": [500.0, 500.0],
    }, geometry=[box(0, 0, 1000, 1000), box(1000, 0, 2000, 1000)], crs="EPSG:25832")


def _od(rows):
    return pd.DataFrame(rows, columns=["origin_cell_id", "destination_cell_id", "commuters"])


def test_assign_points_to_cells_inside_and_outside():
    from braunschweig.analysis.verbindungen_validation import assign_points_to_cells
    pts = gpd.GeoDataFrame(
        {"pid": [1, 2, 3]},
        geometry=[Point(500, 500), Point(1500, 500), Point(9000, 9000)],
        crs="EPSG:25832",
    )
    got = assign_points_to_cells(pts, _cells())
    assert got.loc[0] == "A" and got.loc[1] == "B"
    assert pd.isna(got.loc[2])


def test_conditional_od_check_hand_computed():
    from braunschweig.analysis.verbindungen_validation import conditional_od_check
    # Reference row A: A->A 60, A->B 40  => p_ref = (0.6, 0.4), row mass 100.
    ref = _od([("A", "A", 60), ("A", "B", 40)])
    # Model row A: A->A 50, A->B 50, plus 10 on a censored relation B->B?? no:
    # censored = model mass on relations absent from ref FROM THE SAME origin.
    # Model: A->A 50, A->B 50 (observed dests) + A->C 10 (censored).
    model = _od([("A", "A", 50), ("A", "B", 50), ("A", "C", 10)])
    per_origin, stats = conditional_od_check(model, ref)
    row = per_origin.set_index("origin_cell_id").loc["A"]
    # Restricted to observed dests + renormalised: p_model = (0.5, 0.5)
    # TVD = 0.5 * (|0.5-0.6| + |0.5-0.4|) = 0.1
    assert math.isclose(row["tvd"], 0.1, abs_tol=1e-9)
    # censored share of model row mass: 10 / 110
    assert math.isclose(row["censored_model_share"], 10.0 / 110.0, abs_tol=1e-9)
    # overall weighted TVD: single origin -> 0.1
    assert math.isclose(stats["weighted_tvd"], 0.1, abs_tol=1e-9)
    assert math.isclose(stats["censored_model_share"], 10.0 / 110.0, abs_tol=1e-9)


def test_band_shares_and_emd_hand_computed():
    from braunschweig.analysis.verbindungen_validation import band_shares, emd_1d
    cells = _cells()
    # intra-cell distance 0 m -> band [0,2)km; A->B centroid distance 1000 m
    # -> also band [0,2)km with these tiny cells; use bands 0-0.5-2-5 km to
    # split them: 0m -> [0,0.5), 1000m -> [0.5,2).
    ref = _od([("A", "A", 60), ("A", "B", 40)])
    model = _od([("A", "A", 50), ("A", "B", 50)])
    bands = [0.0, 0.5, 2.0, 5.0]
    s_ref = band_shares(ref, cells, bands)
    s_model = band_shares(model, cells, bands)
    assert math.isclose(s_ref.iloc[0], 0.6) and math.isclose(s_ref.iloc[1], 0.4)
    # EMD over band CDFs: |0.5-0.6| + |1.0-1.0| = 0.1
    assert math.isclose(emd_1d(s_model, s_ref), 0.1, abs_tol=1e-9)


def test_margin_check_hand_computed():
    from braunschweig.analysis.verbindungen_validation import margin_check
    model = pd.Series([50.0, 50.0], index=["A", "B"])
    ref = pd.Series([60.0, 40.0], index=["A", "B"])
    got = margin_check(model, ref)
    # shares model (0.5,0.5) vs ref (0.6,0.4):
    # srmse = sqrt(mean((0.1^2,0.1^2))) / mean(ref shares=0.5) = 0.1/0.5 = 0.2
    assert math.isclose(got["srmse"], 0.2, abs_tol=1e-9)
    assert got["n_cells"] == 2


def test_vintage_drift_cross_kreis_shares():
    from braunschweig.analysis.verbindungen_validation import vintage_drift_check
    cells = _cells()  # A -> Kreis 03101, B -> Kreis 03151
    # 2019 reference: cross-Kreis A->B 40 (all cross mass)
    ref = _od([("A", "A", 60), ("A", "B", 40)])
    # 2025 pendler: two cross pairs, shares 0.5 / 0.5
    pendler = pd.DataFrame({
        "orig_ars": ["03101", "03151"],
        "dest_ars": ["03151", "03101"],
        "flow": [100.0, 100.0],
    })
    drift = vintage_drift_check(ref, cells, pendler)
    d = drift.set_index(["orig_kreis", "dest_kreis"])
    # 2019 shares: (03101->03151)=1.0, (03151->03101)=0.0
    assert math.isclose(d.loc[("03101", "03151"), "share_2019"], 1.0)
    assert math.isclose(d.loc[("03101", "03151"), "share_2025"], 0.5)
    assert math.isclose(d.loc[("03151", "03101"), "share_2019"], 0.0)
