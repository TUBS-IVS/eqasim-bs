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


def test_build_anchor_targets_warns_on_high_coverage_skip(capsys):
    """CLAUDE.md fallback transparency rule #2: a high coverage-skip rate
    must be escalated (WARNING), not just reported at a flat informational
    level (#193 Task 2 review finding).

    High-skip fixture (hand-computed, 3 (origin, dest_kreis) row groups):
        (A, 03101) <- A->A only, mass 5    (below threshold 20 -> skipped)
        (A, 03151) <- A->C only, mass 8    (below threshold 20 -> skipped)
        (B, 03101) <- B->A only, mass 50   (>= threshold 20 -> anchorable)
    With min_observed_commuters=20: 2 of 3 rows skipped = 0.667 > 0.5
    (HIGH_COVERAGE_SKIP_WARN_FRACTION) -> the log line must carry "WARNING".
    """
    from braunschweig.gravity.verbindungen_anchor import build_anchor_targets

    ref_od_high_skip = pd.DataFrame({
        "origin_zone_id": ["A", "A", "B"],
        "destination_zone_id": ["A", "C", "A"],
        "commuters": [5, 8, 50],
    })
    _, stats = build_anchor_targets(
        ref_od_high_skip, _zones(), min_observed_commuters=20)
    assert stats["n_rows_total"] == 3
    assert stats["n_rows_anchorable"] == 1
    assert stats["n_rows_skipped_coverage"] == 2
    assert "WARNING" in capsys.readouterr().out

    # The existing low-skip fixture (row (B, 03101) mass 12 < 20 is the only
    # skip: 1/3 rows = 0.33 < 0.5) must NOT trigger the escalation.
    build_anchor_targets(_ref_od_zones(), _zones(), min_observed_commuters=20)
    assert "WARNING" not in capsys.readouterr().out


def _model_od_gemeinde():
    # Calibrated Gemeinde OD. Zone-level view (hand-computed):
    #   A->A: a1->a1 30 + a1->a2 10 + a2->a1 20        = 60? no: 30+10+20 = 60 -> use 40
    # Chosen values (see asserts): A->A = 40 (a1->a2 30, a2->a1 10),
    # A->B = 60 (a1->b1 45, a2->b1 15), A->C = 20 (a1->c1 20),
    # B->A = 12 (b1->a1 12), B->C = 5 (b1->c1 5, censored in ref).
    return pd.DataFrame({
        "origin_id":      ["a1", "a2", "a1", "a2", "a1", "b1", "b1"],
        "destination_id": ["a2", "a1", "b1", "b1", "c1", "a1", "c1"],
        "flow":           [30.0, 10.0, 45.0, 15.0, 20.0, 12.0, 5.0],
    })


def test_apply_inner_anchor_hand_computed():
    from braunschweig.gravity.verbindungen_anchor import (
        apply_inner_anchor, build_anchor_targets,
    )
    targets, _ = build_anchor_targets(
        _ref_od_zones(), _zones(), min_observed_commuters=20)
    out, stats = apply_inner_anchor(
        _model_od_gemeinde(), _zone_map(), _zones(), targets)
    z = out.copy()
    zmap = _zone_map().set_index("commune_id")["zone_id"]
    z["oz"] = z["origin_id"].map(zmap)
    z["dz"] = z["destination_id"].map(zmap)
    zz = z.groupby(["oz", "dz"])["flow"].sum()
    # Row (A, 03101): observed dests {A, B}; model mass M = 40 + 60 = 100.
    # Targets .6/.4 -> anchored A->A = 60, A->B = 40.
    assert math.isclose(zz[("A", "A")], 60.0)
    assert math.isclose(zz[("A", "B")], 40.0)
    # Pass-down proportional: a1->a2 : a2->a1 stays 3:1 -> 45 / 15.
    g = z.set_index(["origin_id", "destination_id"])["flow"]
    assert math.isclose(g[("a1", "a2")], 45.0)
    assert math.isclose(g[("a2", "a1")], 15.0)
    # Row (A, 03151): single observed dest already share 1.0 -> unchanged.
    assert math.isclose(zz[("A", "C")], 20.0)
    # Row (B, 03101): below coverage -> untouched (12). Censored B->C untouched.
    assert math.isclose(zz[("B", "A")], 12.0)
    assert math.isclose(zz[("B", "C")], 5.0)
    # Kreis-block totals conserved: (03101->03101) = 40+60+12 = 112 before,
    # 60+40+12 = 112 after; (03101->03151) = 20+5 = 25 both.
    # anchored_mass sums the row-observed mass m over BOTH anchored rows:
    # (A,03101) m=100 AND the single-observed-dest (A,03151) m=20 (target
    # share 1.0 -> factor 1.0, still "anchored" so its mass counts -- matches
    # n_rows_anchored==2 below). total flow = 137.
    assert math.isclose(stats["anchored_mass_share"],
                        (100.0 + 20.0) / (100.0 + 20.0 + 12.0 + 5.0))
    assert stats["n_rows_anchored"] == 2   # (A,03101) anchored; (A,03151)
    # counts as anchored too (factor 1.0)
    # Both anchored rows have full model coverage on every observed dest zone
    # (A: A&B model mass 40 & 60, both > 0; C: single dest, model mass 20 > 0)
    # -> the partial-zero-renorm fallback must not fire on this fixture.
    assert stats["n_rows_partial_zero_renorm"] == 0


def test_apply_inner_anchor_idempotent_and_off_identity():
    from braunschweig.gravity.verbindungen_anchor import (
        apply_inner_anchor, build_anchor_targets,
    )
    targets, _ = build_anchor_targets(
        _ref_od_zones(), _zones(), min_observed_commuters=20)
    once, _ = apply_inner_anchor(
        _model_od_gemeinde(), _zone_map(), _zones(), targets)
    twice, _ = apply_inner_anchor(once, _zone_map(), _zones(), targets)
    pd.testing.assert_frame_equal(
        once.sort_values(["origin_id", "destination_id"]).reset_index(drop=True),
        twice.sort_values(["origin_id", "destination_id"]).reset_index(drop=True))


def test_apply_inner_anchor_zero_mass_guard():
    from braunschweig.gravity.verbindungen_anchor import (
        apply_inner_anchor, build_anchor_targets,
    )
    targets, _ = build_anchor_targets(
        _ref_od_zones(), _zones(), min_observed_commuters=20)
    # Model has NO mass on row (A, 03151) although the reference observes it.
    od = _model_od_gemeinde()
    od = od[~((od["origin_id"].isin(["a1", "a2"]))
              & (od["destination_id"] == "c1"))].reset_index(drop=True)
    out, stats = apply_inner_anchor(od, _zone_map(), _zones(), targets)
    assert stats["n_rows_skipped_zero_mass"] == 1
    # nothing invented: no new rows appear
    assert len(out) == len(od)
    # The one anchored row (A, 03101, dests A&B) has full model coverage on
    # both observed dests -> not a partial-zero-renorm case either.
    assert stats["n_rows_partial_zero_renorm"] == 0


def test_apply_inner_anchor_partial_zero_conserves(capsys):
    """Partial-zero within an anchorable row must still conserve the block.

    An anchorable row (origin zone P, dest Kreis 09999) observes three
    destination zones {X, Y, Z} with target shares 0.5/0.3/0.2, but the
    calibrated model routed ZERO flow to Z (the gravity model can miss a
    reference-observed relation). Z cannot be scaled up from nothing; its
    share is redistributed over the fillable pairs {X, Y} so the full row
    mass stays inside the Kreis-pair block. Without the renormalisation the
    row would lose Z's 20 percent of mass and apply_inner_anchor's
    block-conservation assertion would raise.

    This is also the ONLY anchored row in the fixture, so it doubles as the
    hand-derived case for the partial-zero-renorm FALLBACK counter/log (#193
    Task 3 review finding, CLAUDE.md fallback transparency): 1 of the 1
    anchored row hits the fallback = 100% > HIGH_PARTIAL_ZERO_WARN_FRACTION
    (0.5) -> the log line must escalate to "WARNING" and name the row and its
    zero-model dest Z.
    """
    from braunschweig.gravity.verbindungen_anchor import apply_inner_anchor
    df_zones = gpd.GeoDataFrame({
        "zone_id": ["P", "X", "Y", "Z"],
        "kreis_id": ["09999", "09999", "09999", "09999"],
        "centroid_x": [0.0, 1000.0, 2000.0, 3000.0],
        "centroid_y": [0.0, 0.0, 0.0, 0.0],
    }, geometry=[box(0, 0, 1, 1), box(1000, 0, 1001, 1),
                 box(2000, 0, 2001, 1), box(3000, 0, 3001, 1)],
        crs="EPSG:25832")
    df_zone_map = pd.DataFrame({
        "commune_id": ["p1", "x1", "y1", "z1"],
        "zone_id":    ["P",  "X",  "Y",  "Z"],
    })
    df_targets = pd.DataFrame({
        "origin_zone_id":         ["P", "P", "P"],
        "dest_kreis":             ["09999", "09999", "09999"],
        "destination_zone_id":    ["X", "Y", "Z"],
        "target_share":           [0.5, 0.3, 0.2],
        "row_observed_commuters": [100.0, 100.0, 100.0],
    })
    # Model reaches X and Y but NOT Z (no p1->z1 flow at all): model mass 60/40.
    df_od = pd.DataFrame({
        "origin_id":      ["p1", "p1"],
        "destination_id": ["x1", "y1"],
        "flow":           [60.0, 40.0],
    })

    out, stats = apply_inner_anchor(df_od, df_zone_map, df_zones, df_targets)

    g = out.set_index(["origin_id", "destination_id"])["flow"]
    # (a) Kreis-pair block (09999 -> 09999) conserved: 100 before, 100 after
    #     (returning without raising already proves the internal assertion held).
    assert math.isclose(g[("p1", "x1")] + g[("p1", "y1")], 100.0)
    # (b) Z's 0.2 share is absorbed by X and Y in proportion to their target
    #     shares (0.5 : 0.3), renormalised to 0.625 : 0.375 of the row mass.
    assert math.isclose(g[("p1", "x1")], 62.5)
    assert math.isclose(g[("p1", "y1")], 37.5)
    assert math.isclose(g[("p1", "x1")] / g[("p1", "y1")], 0.5 / 0.3)
    # (c) nothing invented on the zero pair; no new rows, row counted anchored.
    assert len(out) == len(df_od)
    assert stats["n_rows_anchored"] == 1
    assert stats["n_rows_skipped_zero_mass"] == 0
    # (d) the ONLY anchored row hit the partial-zero renormalisation fallback
    #     (Z was zero-model) -> the fallback counter must report it, never
    #     silently (CLAUDE.md fallback transparency, #193 Task 3 review).
    assert stats["n_rows_partial_zero_renorm"] == 1

    # (e) the fallback must be LOGGED, not just counted: the affected row and
    #     its zero-model destination zone are named, and since 1/1 anchored
    #     rows hit it (100% > HIGH_PARTIAL_ZERO_WARN_FRACTION = 0.5) the log
    #     escalates to WARNING (same escalation pattern as the coverage-skip
    #     heuristic in build_anchor_targets).
    log = capsys.readouterr().out
    assert "partial-zero renorm fallback rows" in log
    assert "(P, 09999)" in log
    assert "Z" in log
    assert "WARNING" in log


def test_run_inner_anchor_end_to_end(tmp_path):
    # Wires Task-1 comparison zones + cell-level reference into one call.
    from braunschweig.data.verbindungen.zones import (
        build_comparison_zones, build_zones_frames,
    )
    from braunschweig.gravity.verbindungen_anchor import run_inner_anchor
    from tests.fixtures.verbindungen_fixtures import (
        make_municipalities_gdf, write_cells_shapefile_zip,
    )
    import geopandas as gpd
    zip_path = write_cells_shapefile_zip(tmp_path)
    gdf_raw = gpd.read_file(f"zip://{zip_path}!verbindungen-verkehrszellen.shp")
    df_cells, df_cell_commune, _ = build_zones_frames(
        gdf_raw, make_municipalities_gdf(), scope=["03101", "03151"],
        max_fallback_share=0.60)
    # Reference on CELLS: parent zone 031010001000 (two stadtteil cells) ->
    # vg250-3: 60 + 40 = 100 observed on ONE zone pair after collapse.
    df_ref_cells = pd.DataFrame({
        "origin_cell_id": ["stadtteil-1", "stadtteil-2"],
        "destination_cell_id": ["vg250-3", "vg250-3"],
        "commuters": [60, 40],
    })
    od = pd.DataFrame({
        "origin_id": ["031010001000", "031010001000"],
        "destination_id": ["031510000001", "031510029999"],
        "flow": [10.0, 30.0],
    })
    out, stats = run_inner_anchor(
        od, df_cells, df_cell_commune, df_ref_cells,
        min_observed_commuters=20)
    # Both destination Gemeinden lie in zone vg250-3: single observed dest
    # zone -> share 1.0 -> flows unchanged, but the machinery ran end-to-end.
    pd.testing.assert_frame_equal(
        out.sort_values("destination_id").reset_index(drop=True),
        od.sort_values("destination_id").reset_index(drop=True))
    assert stats["n_rows_anchored"] == 1
