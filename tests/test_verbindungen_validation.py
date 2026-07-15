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


def test_conditional_od_check_raises_on_empty_reference():
    from braunschweig.analysis.verbindungen_validation import conditional_od_check
    model = _od([("A", "A", 50)])
    empty_ref = _od([])
    # Fail-early contract: an empty reference means the loader/clip upstream
    # broke; comparing against nothing must never return silently.
    with pytest.raises(ValueError, match="reference OD frame is empty"):
        conditional_od_check(model, empty_ref)


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


def _synthetic_population():
    """4 employed persons: 3 in cell A (2 work A, 1 works B), 1 outside cells."""
    df_home = gpd.GeoDataFrame({
        "household_id": [10, 11, 12, 13],
        "commune_id": ["031010001000"] * 4,
        "home_location_id": [1, 2, 3, 4],
    }, geometry=[Point(400, 400), Point(600, 600), Point(700, 300),
                 Point(9000, 9000)], crs="EPSG:25832")
    df_persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "household_id": [10, 11, 12, 13],
        "age_range": ["adult"] * 4,
        "commune_id": ["031010001000"] * 4,
        "has_work_trip": [True, True, True, True],
        "has_education_trip": [False] * 4,
    })
    df_work = gpd.GeoDataFrame({
        "person_id": [1, 2, 3, 4],
        "commune_id": ["031010001000"] * 4,
        "location_id": [101, 102, 103, 104],
    }, geometry=[Point(450, 450), Point(650, 650), Point(1500, 500),
                 Point(500, 500)], crs="EPSG:25832")
    return df_home, df_work, df_persons


def test_build_validation_outputs_end_to_end():
    from braunschweig.analysis.verbindungen_validation import build_validation_outputs
    df_home, df_work, df_persons = _synthetic_population()
    cells = _cells()
    ref = _od([("A", "A", 60), ("A", "B", 40)])
    margins = pd.DataFrame({
        "cell_id": ["A", "B"],
        "workers_at_home": pd.array([100, 50], dtype="Int64"),
        "workers_at_workplace": pd.array([80, 70], dtype="Int64"),
    })
    pendler = pd.DataFrame({
        "orig_ars": ["03101"], "dest_ars": ["03151"], "flow": [100.0],
    })
    out = build_validation_outputs(
        df_home, df_work, df_persons, cells, ref, margins, pendler)
    assert set(out) == {"summary", "margin", "od_per_origin",
                        "od_by_kreis_pair", "vintage_drift"}
    summary = out["summary"].set_index("metric")["value"]
    # person 4: home outside every cell -> unassigned share 1/4
    assert math.isclose(float(summary["unassigned_person_share"]), 0.25)
    # realised OD (persons 1-3): A->A 2, A->B 1
    # ref-conditional p_ref=(0.6,0.4); model p=(2/3,1/3)
    # TVD = 0.5*(|2/3-0.6|+|1/3-0.4|) = 0.5*(0.0666..+0.0666..) = 0.0666..
    assert math.isclose(float(summary["weighted_tvd"]), 1.0 / 15.0, abs_tol=1e-9)
    # margin check present with 2 cells
    assert int(out["margin"].shape[0]) == 2
    # intra-cell shares: model 2/3, ref 0.6
    assert math.isclose(float(summary["intra_cell_share_model"]), 2.0 / 3.0)
    assert math.isclose(float(summary["intra_cell_share_reference"]), 0.6)


def test_build_validation_outputs_tolerates_zero_mass_cell():
    from braunschweig.analysis.verbindungen_validation import build_validation_outputs
    df_home, df_work, df_persons = _synthetic_population()
    cells = _cells()
    # reference has no row for origin B (like the gemeindefreie ZGB cells)
    ref = _od([("A", "A", 60), ("A", "B", 40)])
    margins = pd.DataFrame({
        "cell_id": ["A", "B"],
        "workers_at_home": pd.array([100, pd.NA], dtype="Int64"),
        "workers_at_workplace": pd.array([80, 70], dtype="Int64"),
    })
    pendler = pd.DataFrame({
        "orig_ars": ["03101"], "dest_ars": ["03151"], "flow": [100.0],
    })
    out = build_validation_outputs(
        df_home, df_work, df_persons, cells, ref, margins, pendler)
    # must not raise; margin check silently drops the NA cell (n_cells == 1)
    assert int(out["summary"].set_index("metric").loc["margin_n_cells", "value"]) == 1
