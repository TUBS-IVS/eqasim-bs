"""Tests for the inner VerBindungen calibration anchor (#193).

Fixture world used throughout (hand-computed in comments):
Kreis 03101 holds zones A (2 Gemeinden a1, a2) and B (1 Gemeinde b1);
Kreis 03151 holds zone C (1 Gemeinde c1).
Reference (zone level, observed >= 10 only):
    A->A 60, A->B 40          (row (A, 03101): observed mass 100 -> shares .6/.4)
    A->C 30                   (row (A, 03151): single observed dest -> share 1.0)
    B->A 12                   (row (B, 03101): mass 12 -> below threshold 20)

Run with::

    python -m pytest tests/test_verbindungen_anchor.py -v
"""
from __future__ import annotations

import math
import pathlib
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _zones():
    return gpd.GeoDataFrame({
        "zone_id": ["A", "B", "C"],
        "kreis_id": ["03101", "03101", "03151"],
        "centroid_x": [500.0, 2500.0, 10500.0],
        "centroid_y": [500.0, 500.0, 500.0],
    }, geometry=[box(0, 0, 1000, 1000), box(2000, 0, 3000, 1000),
                 box(10000, 0, 11000, 1000)], crs="EPSG:25832")


def _zone_map():
    return pd.DataFrame({
        "commune_id": ["a1", "a2", "b1", "c1"],
        "zone_id": ["A", "A", "B", "C"],
    })


def _ref_od_zones():
    return pd.DataFrame({
        "origin_zone_id": ["A", "A", "A", "B"],
        "destination_zone_id": ["A", "B", "C", "A"],
        "commuters": [60, 40, 30, 12],
    })


def test_collapse_od_to_zones_groups_and_guards():
    from braunschweig.gravity.verbindungen_anchor import collapse_od_to_zones
    df_cell_zone = pd.DataFrame({
        "cell_id": ["stadtteil-1", "stadtteil-2", "vg250-3"],
        "zone_id": ["A", "A", "B"],
    })
    od_cells = pd.DataFrame({
        "origin_cell_id": ["stadtteil-1", "stadtteil-2", "stadtteil-1"],
        "destination_cell_id": ["vg250-3", "vg250-3", "stadtteil-2"],
        "commuters": [10, 15, 20],
    })
    out = collapse_od_to_zones(od_cells, df_cell_zone)
    o = out.set_index(["origin_zone_id", "destination_zone_id"])["commuters"]
    assert o[("A", "B")] == 25 and o[("A", "A")] == 20
    with pytest.raises(RuntimeError, match="unmapped"):
        collapse_od_to_zones(
            pd.DataFrame({"origin_cell_id": ["ghost"],
                          "destination_cell_id": ["vg250-3"],
                          "commuters": [10]}),
            df_cell_zone)


def test_build_anchor_targets_shares_and_coverage():
    from braunschweig.gravity.verbindungen_anchor import build_anchor_targets
    targets, stats = build_anchor_targets(
        _ref_od_zones(), _zones(), min_observed_commuters=20)
    t = targets.set_index(["origin_zone_id", "dest_kreis", "destination_zone_id"])
    # row (A, 03101): shares 60/100 and 40/100
    assert math.isclose(t.loc[("A", "03101", "A"), "target_share"], 0.6)
    assert math.isclose(t.loc[("A", "03101", "B"), "target_share"], 0.4)
    # row (A, 03151): single observed dest -> 1.0
    assert math.isclose(t.loc[("A", "03151", "C"), "target_share"], 1.0)
    # row (B, 03101): mass 12 < 20 -> excluded entirely
    assert ("B", "03101", "A") not in t.index
    assert stats["n_rows_total"] == 3
    assert stats["n_rows_anchorable"] == 2
    assert stats["n_rows_skipped_coverage"] == 1
    # shares sum to 1 within every anchorable row
    sums = targets.groupby(["origin_zone_id", "dest_kreis"])["target_share"].sum()
    assert np.allclose(sums.to_numpy(), 1.0)
