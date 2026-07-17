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


def test_censored_bound_diagnostic_hand_computed():
    from braunschweig.gravity.verbindungen_anchor import censored_bound_diagnostic
    model = pd.DataFrame({
        "origin_zone_id": ["A", "A", "B"],
        "destination_zone_id": ["A", "B", "C"],
        "commuters": [60.0, 40.0, 50.0],
    })
    # Local reference for THIS test (observed total 100 = model observed mass
    # 60+40, so the global universe ratio is exactly 1.0): observes A->A and
    # A->B; the model's B->C is censored.
    ref = pd.DataFrame({
        "origin_zone_id": ["A", "A"],
        "destination_zone_id": ["A", "B"],
        "commuters": [60, 40],
    })
    df, summary = censored_bound_diagnostic(model, ref)
    # global ratio = ref observed / model-on-observed = 100/100 = 1.0
    # -> ref_equivalent(B->C) = 50 -> ratio_to_bound = 50/10 = 5.0.
    row = df.set_index(["origin_zone_id", "destination_zone_id"]).loc[("B", "C")]
    assert math.isclose(row["ratio_to_bound"], 5.0)
    assert math.isclose(summary["censored_mass_share"], 50.0 / 150.0)
    assert summary["share_ratio_gt_1"] > 0


def test_ao_margin_diagnostic_uses_margin_check():
    from braunschweig.gravity.verbindungen_anchor import ao_margin_diagnostic
    before = pd.DataFrame({
        "origin_zone_id": ["A", "A"], "destination_zone_id": ["A", "B"],
        "commuters": [50.0, 50.0]})
    after = pd.DataFrame({
        "origin_zone_id": ["A", "A"], "destination_zone_id": ["A", "B"],
        "commuters": [60.0, 40.0]})
    margins = pd.DataFrame({
        "cell_id": ["cellA", "cellB"],
        "workers_at_home": pd.array([100, 50], dtype="Int64"),
        "workers_at_workplace": pd.array([60, 40], dtype="Int64"),
    })
    cell_zone = pd.DataFrame({"cell_id": ["cellA", "cellB"],
                              "zone_id": ["A", "B"]})
    got = ao_margin_diagnostic(before, after, margins, cell_zone)
    # after matches the AO shares exactly -> srmse 0, before does not.
    assert got["after"]["srmse"] < got["before"]["srmse"]
    assert math.isclose(got["after"]["srmse"], 0.0, abs_tol=1e-12)


def test_ao_margin_diagnostic_logs_unmapped_join_coverage(capsys):
    """CLAUDE.md fallback transparency (Task 4 review Important 1): margin
    cells whose cell_id fails to map to a comparison zone are dropped before
    the AO comparison; the drop must be COUNTED and LOGGED (never silent),
    escalating to WARNING when it dominates the workplace mass.

    High-drop fixture (hand-computed): two margin cells, only cellMAP maps to
    zone A; cellGHOST is absent from the cell->zone crosswalk. Workplace mass
    20 (mapped) + 80 (dropped) = 100 -> dropped mass share 80/100 = 0.8 >
    HIGH_UNMAPPED_WARN_FRACTION (0.5) -> the coverage log must carry WARNING
    and report "mapped 1/2 cells (50.0%), dropped 1 cells / 80.0%".
    """
    from braunschweig.gravity.verbindungen_anchor import ao_margin_diagnostic

    before = pd.DataFrame({
        "origin_zone_id": ["A"], "destination_zone_id": ["A"],
        "commuters": [50.0]})
    after = pd.DataFrame({
        "origin_zone_id": ["A"], "destination_zone_id": ["A"],
        "commuters": [50.0]})
    margins_high_drop = pd.DataFrame({
        "cell_id": ["cellMAP", "cellGHOST"],
        "workers_at_home": pd.array([100, 100], dtype="Int64"),
        "workers_at_workplace": pd.array([20, 80], dtype="Int64"),
    })
    cell_zone = pd.DataFrame({"cell_id": ["cellMAP"], "zone_id": ["A"]})

    ao_margin_diagnostic(before, after, margins_high_drop, cell_zone)
    out_log = capsys.readouterr().out
    assert "mapped 1/2 cells" in out_log
    assert "80.0% of workplace mass" in out_log
    assert "WARNING" in out_log

    # Fully-mapped fixture: both cells resolve to a zone -> dropped 0 cells /
    # 0.0% of mass -> the escalation must NOT fire (mirrors the low-skip half
    # of test_build_anchor_targets_warns_on_high_coverage_skip).
    margins_full = pd.DataFrame({
        "cell_id": ["cellMAP", "cellMAP2"],
        "workers_at_home": pd.array([100, 100], dtype="Int64"),
        "workers_at_workplace": pd.array([20, 30], dtype="Int64"),
    })
    cell_zone_full = pd.DataFrame({"cell_id": ["cellMAP", "cellMAP2"],
                                   "zone_id": ["A", "B"]})
    before_full = pd.DataFrame({
        "origin_zone_id": ["A", "A"], "destination_zone_id": ["A", "B"],
        "commuters": [50.0, 50.0]})
    after_full = pd.DataFrame({
        "origin_zone_id": ["A", "A"], "destination_zone_id": ["A", "B"],
        "commuters": [60.0, 40.0]})
    ao_margin_diagnostic(before_full, after_full, margins_full, cell_zone_full)
    out_log_full = capsys.readouterr().out
    assert "mapped 2/2 cells" in out_log_full
    assert "WARNING" not in out_log_full


def test_intra_kreis_diagnostic_shares():
    from braunschweig.gravity.verbindungen_anchor import intra_kreis_diagnostic
    od = _model_od_gemeinde()
    df = intra_kreis_diagnostic(od, _ref_od_zones(), _zones(), _zone_map())
    d = df.set_index("kreis_id")
    # Model Kreis 03101 row: intra (a*/b1 -> a*/b1) = 30+10+45+15+12 = 112;
    # total from 03101 origins = 112 + 20 + 5 = 137.
    assert math.isclose(d.loc["03101", "model_intra_share"], 112.0 / 137.0)
    # QZM 03101 intra: observed pairs within 03101 = A->A 60, A->B 40,
    # B->A 12 = 112; from-03101 observed total = 112 + 30 = 142.
    assert math.isclose(d.loc["03101", "qzm_intra_share"], 112.0 / 142.0)


def test_intra_kreis_diagnostic_zero_qzm_mass_guard(capsys):
    """Task 4 review Important 2 + Part A.2: a Kreis that appears on the MODEL
    side but has zero/missing QZM-observed mass must yield a clean, DOCUMENTED
    result (guarded 0.0, named in the log), not a silent NaN; and an unmapped
    model-side id must be counted + logged (CLAUDE.md fallback transparency).

    Fixture (hand-computed) reuses the standard zones/zone_map. The reference
    _ref_od_zones() has ALL its origins in Kreis 03101 (A/B origins), so the
    reference observes NO mass originating in Kreis 03151 -> ref_total[03151]
    is missing. The model OD below routes flow FROM Kreis 03151 (origin c1),
    so 03151 appears in the model-side output frame while its QZM share would
    divide 0/NaN -> the guard must set qzm_intra_share = 0.0 and name 03151.

    Model OD (Gemeinde level):
        c1 -> c1   flow 20   (03151 -> 03151, intra)
        c1 -> a1   flow  5   (03151 -> 03101, cross)
        ghost -> a1 flow 3   (origin_id absent from zone_map -> UNMAPPED)
    Model-side coverage: 2/3 rows mapped, 1 dropped carrying 3 of 28 flow =
    10.7% (< 50% -> no WARNING, but must be counted/logged).
    After dropping the unmapped row: Kreis 03151 row_total = 20 + 5 = 25,
    intra = 20 -> model_intra_share = 20/25 = 0.8.
    Reference side: all zones map -> 4/4 rows, 0 dropped (no WARNING); Kreis
    03151 has no reference origin -> qzm_intra_share guarded from NaN to 0.0.
    """
    from braunschweig.gravity.verbindungen_anchor import intra_kreis_diagnostic

    od_from_03151 = pd.DataFrame({
        "origin_id":      ["c1", "c1", "ghost"],
        "destination_id": ["c1", "a1", "a1"],
        "flow":           [20.0, 5.0, 3.0],
    })
    df = intra_kreis_diagnostic(
        od_from_03151, _ref_od_zones(), _zones(), _zone_map())
    d = df.set_index("kreis_id")

    # (a) model_intra_share for 03151 is the hand-derived 0.8 (20/25).
    assert math.isclose(d.loc["03151", "model_intra_share"], 20.0 / 25.0)
    # (b) zero/missing QZM mass is guarded to a clean 0.0, NOT a silent NaN.
    assert d.loc["03151", "qzm_intra_share"] == 0.0
    assert not math.isnan(d.loc["03151", "qzm_intra_share"])
    # (c) no unhandled NaN is returned anywhere in the share columns.
    assert not df["qzm_intra_share"].isna().any()
    assert not df["model_intra_share"].isna().any()

    out_log = capsys.readouterr().out
    # (d) the guarded Kreis is NAMED in the summary log (not silent).
    assert "zero/missing QZM-observed mass" in out_log
    assert "03151" in out_log
    # (e) the unmapped model-side row is counted + logged (2/3 mapped).
    assert "mapped 2/3 rows" in out_log
    # (f) both drops are below the heuristic threshold -> no WARNING; the
    #     zero-QZM guard is a documented note, not an escalation.
    assert "WARNING" not in out_log


def test_gravity_configure_declares_anchor_flag_off():
    # Static contract: the flag is declared with default False and the
    # reference stages plus anchor_min_observed_commuters are declared ONLY
    # inside the conditional block (source scan, same style as the repo's
    # config-contract tests).
    #
    # Indentation-aware on purpose (#193 Task 5 review finding): a plain
    # substring check on "everything between the if-line and the next def"
    # cannot tell nested code from a DEDENTED, unconditionally-running copy.
    # We instead walk the source line by line, record the "if" line's own
    # indent, and collect only the subsequent non-blank lines that are
    # indented strictly deeper than it -- stopping at the first line that
    # falls back to (or below) the "if" indent. A regression that dedents the
    # two stage() calls or the anchor_min_observed_commuters config so they
    # run unconditionally drops them out of this collected block, and the
    # assertions below fail.
    src = (REPO_ROOT / "braunschweig" / "gravity" / "model.py").read_text(
        encoding="utf-8")
    assert 'context.config("braunschweig.gravity.verbindungen_anchor_enabled", False)' in src

    lines = src.splitlines()
    guard_text = 'if context.config("braunschweig.gravity.verbindungen_anchor_enabled", False):'
    i_guard = next(i for i, line in enumerate(lines) if guard_text in line)
    guard_indent = len(lines[i_guard]) - len(lines[i_guard].lstrip())

    nested_block_lines = []
    for line in lines[i_guard + 1:]:
        if not line.strip():
            continue  # blank lines carry no indentation signal; skip over them
        indent = len(line) - len(line.lstrip())
        if indent <= guard_indent:
            break  # back to (or above) the "if" level -> end of its nested block
        nested_block_lines.append(line)
    nested_block = "\n".join(nested_block_lines)

    assert 'context.stage("braunschweig.data.verbindungen.zones")' in nested_block
    assert 'context.stage("braunschweig.data.verbindungen.work_od")' in nested_block
    # Assert the full declare-call prefix, not just the bare config key, so a
    # rename that turns this into e.g. a comment or a different call still fails.
    assert ('context.config("braunschweig.verbindungen.anchor_min_observed_commuters"'
            in nested_block)


def test_execute_calls_anchor_between_calibrate_and_outbound():
    src = (REPO_ROOT / "braunschweig" / "gravity" / "model.py").read_text(
        encoding="utf-8")
    i_cal = src.index("df_work_calibrated = _calibrate(")
    i_guard = src.index(
        'if context.config("braunschweig.gravity.verbindungen_anchor_enabled"):')
    i_anchor = src.index("run_inner_anchor")
    i_out = src.index("df_work_extended = _append_outbound_flows(")
    # Defense in depth (#193 Task 5 review, Minor): also require the anchor
    # call to be reached through its own guard -- not merely present
    # somewhere between calibrate and outbound -- so a regression that hoists
    # run_inner_anchor above (or otherwise outside) the flag check still fails
    # this test even though the calibrate/outbound ordering alone would pass.
    assert i_cal < i_guard < i_anchor < i_out


def test_assign_folds_stratified_and_deterministic():
    from braunschweig.calibration.anchor_holdout import assign_folds
    ref = pd.DataFrame({
        "origin_zone_id": ["A"] * 5 + ["B"],
        "destination_zone_id": list("VWXYZ") + ["V"],
        "commuters": [30, 30, 30, 30, 30, 25],
    })
    f1 = assign_folds(ref.assign(dest_kreis="03101"), k=5, seed=20260716)
    f2 = assign_folds(ref.assign(dest_kreis="03101"), k=5, seed=20260716)
    pd.testing.assert_series_equal(f1, f2)          # deterministic
    assert set(f1.iloc[:5]) == {0, 1, 2, 3, 4}      # row A: 5 dests spread
    assert f1.iloc[5] == -1                          # row B: single dest -> train-always


def test_heldout_conditional_tvd_hand_computed():
    from braunschweig.calibration.anchor_holdout import heldout_conditional_tvd
    ref = pd.DataFrame({
        "origin_zone_id": ["A", "A", "A"],
        "destination_zone_id": ["X", "Y", "Z"],
        "commuters": [50, 30, 20],
    })
    model = pd.DataFrame({
        "origin_zone_id": ["A", "A", "A"],
        "destination_zone_id": ["X", "Y", "Z"],
        "commuters": [20.0, 30.0, 50.0],
    })
    held = pd.Series([False, True, True])   # held-out: Y and Z
    # Held-out ref conditionals: Y 30/50=.6, Z .4 ; model: Y 30/80=.375, Z .625
    # TVD = 0.5*(|.375-.6| + |.625-.4|) = 0.225
    got = heldout_conditional_tvd(model, ref, held)
    assert math.isclose(got, 0.225, abs_tol=1e-12)


def test_heldout_conditional_tvd_excludes_degenerate_single_dest_origin(capsys):
    """#193 Task 7 review Important 1 (CLAUDE.md fallback transparency): an
    origin whose held-out set has a SINGLE destination carries no
    conditional structure -- its renormalised share is trivially 1.0 for
    both model and reference, so its row TVD is always exactly 0 no matter
    how the model performs there. Such an origin must be excluded from the
    pooled score, not silently averaged in with a free 0.

    Fixture: origin A reuses test_heldout_conditional_tvd_hand_computed's
    numbers exactly (held-out Y, Z -> hand-derived TVD 0.225, held-out ref
    mass 30+20=50). Origin B adds a SINGLE held-out destination W (ref
    commuters 40; the model value at W is irrelevant since the row must be
    skipped before the model is ever read).

    Without the fix, B's degenerate row would still clear the zero-mass
    guard (model 5 / ref 40 are both > 0), renormalise trivially to 1.0 vs
    1.0 (TVD 0), and pull the pooled average toward it:
        (50*0.225 + 40*0) / (50 + 40) = 11.25 / 90 = 0.125
    With the fix, B is excluded entirely and the pooled TVD equals exactly
    A's own TVD: 11.25 / 50 = 0.225.
    """
    from braunschweig.calibration.anchor_holdout import heldout_conditional_tvd
    ref = pd.DataFrame({
        "origin_zone_id": ["A", "A", "A", "B"],
        "destination_zone_id": ["X", "Y", "Z", "W"],
        "commuters": [50, 30, 20, 40],
    })
    model = pd.DataFrame({
        "origin_zone_id": ["A", "A", "A", "B"],
        "destination_zone_id": ["X", "Y", "Z", "W"],
        "commuters": [20.0, 30.0, 50.0, 5.0],
    })
    held = pd.Series([False, True, True, True])  # A: Y,Z held; B: W held (single)
    got = heldout_conditional_tvd(model, ref, held)
    assert math.isclose(got, 0.225, abs_tol=1e-12)

    log = capsys.readouterr().out
    assert "1 informative" in log
    assert "1 skipped (single held-out dest)" in log


def test_heldout_cv_is_inert_by_construction():
    """KNOWN LIMITATION characterization test (final whole-branch review
    finding, #193; see the module docstring above and verdict()'s docstring
    for the full write-up). This anchor is a pure per-row IN-SAMPLE
    reweighting: for a CV fold, the held-out destinations of a row are, by
    the CV harness's own construction (run_anchor_holdout.py builds
    ``t_fold`` from ``train_ref = df_ref_zones[~held]``), EXCLUDED from that
    fold's training targets -- so ``apply_inner_anchor`` never assigns them a
    scaling factor and their model flow passes through UNCHANGED.
    ``heldout_conditional_tvd`` only ever reads model flow on the held-out
    destinations, so it reads the SAME (untouched) numbers for baseline and
    anchored: ``cv_anchored == cv_baseline`` BY CONSTRUCTION. This is NOT
    evidence that the anchor fails to help -- criterion (i) of the
    pre-registered rule simply cannot observe the anchor's effect at all.

    Fixture (hand-computed): one origin zone A, one dest Kreis K1, FOUR
    observed destination zones {A, B, C, D} (commune_id == lowercase
    zone_id, 1:1, so commune-level model flow reads straight across to zone
    level via a simple map). Reference commuters A->A=40, A->B=30, A->C=20,
    A->D=10 (row mass 100). Held-out split: C and D (>= 2 held-out dests, so
    the pooled TVD is informative -- not skipped as a degenerate single-dest
    origin, see the test above). Baseline model (Gemeinde level) is an even
    split: a->a = a->b = a->c = a->d = 25.

    Mirroring run_anchor_holdout.py's CV loop EXACTLY, the training targets
    are built from ONLY the train relations (A->A, A->B; C, D excluded
    before build_anchor_targets ever sees them): row mass 40+30=70, shares
    40/70 and 30/70. apply_inner_anchor therefore only ever assigns factors
    to (A,A) and (A,B) -- confirmed below by asserting those TWO trained
    pairs actually moved (25 -> 50*40/70=28.571.., 25 -> 50*30/70=21.428..;
    the anchor did something real, this is not a "did nothing at all" test)
    while (A,C) and (A,D) stay EXACTLY at their baseline value of 25 each
    (multiplied by the implicit factor 1.0 for a key absent from `factors`;
    no floating-point drift possible).

    heldout_conditional_tvd on held={C, D} therefore reads IDENTICAL model
    values (25.0, 25.0) for both baseline and "anchored" -- hand-derived TVD
    0.5*(|25/50 - 20/30| + |25/50 - 10/30|) = 0.5*(|.5-.6667|+|.5-.3333|)
    = 1/6 for BOTH, so the assertion below is an exact equality, not a
    coincidental closeness.
    """
    from braunschweig.calibration.anchor_holdout import heldout_conditional_tvd
    from braunschweig.gravity.verbindungen_anchor import (
        apply_inner_anchor, build_anchor_targets,
    )

    zones = gpd.GeoDataFrame({
        "zone_id": ["A", "B", "C", "D"],
        "kreis_id": ["K1", "K1", "K1", "K1"],
        "centroid_x": [0.0, 1000.0, 2000.0, 3000.0],
        "centroid_y": [0.0, 0.0, 0.0, 0.0],
    }, geometry=[box(0, 0, 1, 1), box(1000, 0, 1001, 1),
                 box(2000, 0, 2001, 1), box(3000, 0, 3001, 1)],
        crs="EPSG:25832")
    zone_map = pd.DataFrame({
        "commune_id": ["a", "b", "c", "d"],
        "zone_id": ["A", "B", "C", "D"],
    })
    ref_od = pd.DataFrame({
        "origin_zone_id": ["A", "A", "A", "A"],
        "destination_zone_id": ["A", "B", "C", "D"],
        "commuters": [40, 30, 20, 10],
    })
    held = pd.Series([False, False, True, True])  # C, D held-out (>= 2 dests)
    model_od = pd.DataFrame({
        "origin_id":      ["a", "a", "a", "a"],
        "destination_id": ["a", "b", "c", "d"],
        "flow":           [25.0, 25.0, 25.0, 25.0],
    })

    # Mirrors run_anchor_holdout.py's CV loop: targets are built from the
    # TRAIN relations only (held-out relations excluded before
    # build_anchor_targets ever sees them).
    train_ref = ref_od[~held.to_numpy()]
    targets, _ = build_anchor_targets(
        train_ref, zones, min_observed_commuters=5)
    anchored_od, stats = apply_inner_anchor(model_od, zone_map, zones, targets)

    def _to_zone_od(df):
        z = df.copy()
        zmap = zone_map.set_index("commune_id")["zone_id"]
        z["origin_zone_id"] = z["origin_id"].map(zmap)
        z["destination_zone_id"] = z["destination_id"].map(zmap)
        return (z.groupby(["origin_zone_id", "destination_zone_id"])["flow"]
                .sum().rename("commuters").reset_index())

    baseline_zone_od = _to_zone_od(model_od)
    anchored_zone_od = _to_zone_od(anchored_od)

    # (a) the TRAINED pairs actually moved: the anchor did something real.
    a = anchored_od.set_index(["origin_id", "destination_id"])["flow"]
    assert math.isclose(a[("a", "a")], 50.0 * 40.0 / 70.0)
    assert math.isclose(a[("a", "b")], 50.0 * 30.0 / 70.0)
    assert not math.isclose(a[("a", "a")], 25.0)
    assert not math.isclose(a[("a", "b")], 25.0)
    assert stats["n_rows_anchored"] == 1

    # (b) the HELD-OUT pairs are untouched: C, D never appear in `targets`
    # for this fold, so no factor was ever assigned to them.
    assert math.isclose(a[("a", "c")], 25.0)
    assert math.isclose(a[("a", "d")], 25.0)

    # (c) the KNOWN LIMITATION itself: heldout_conditional_tvd reads the SAME
    # (untouched) model values on C/D for baseline and anchored -> EQUAL by
    # construction, not merely close, and both finite (2 held-out dests ->
    # informative, not skipped as degenerate).
    cv_baseline = heldout_conditional_tvd(baseline_zone_od, ref_od, held)
    cv_anchored = heldout_conditional_tvd(anchored_zone_od, ref_od, held)
    assert math.isfinite(cv_baseline) and math.isfinite(cv_anchored)
    assert cv_baseline == cv_anchored
    assert math.isclose(cv_baseline, 1.0 / 6.0, abs_tol=1e-9)


def test_apply_inner_anchor_logs_join_coverage(capsys):
    """Fix B (final whole-branch review, #193): the model-side commune_id ->
    zone_id join in apply_inner_anchor is the one production ON-path join;
    its coverage (in-scope row/mass share + unmapped id samples) must be
    logged so a commune_id key mismatch is debuggable, not a silent
    "anchored mass share 0.0%". A near-total in-scope collapse escalates to
    WARNING (LOW_INSCOPE_MASS_WARN_FRACTION=0.1) -- the signature of a key
    mismatch -- while a moderate out-of-scope share (legitimate external
    flows) must NOT warn.
    """
    from braunschweig.gravity.verbindungen_anchor import (
        apply_inner_anchor, build_anchor_targets,
    )
    targets, _ = build_anchor_targets(
        _ref_od_zones(), _zones(), min_observed_commuters=20)

    # Healthy: one unmapped origin ("ghost", absent from the zone map)
    # carrying tiny mass alongside the fully-mapped model OD (total 137) ->
    # in-scope 137/140 = 97.9% -> the unmapped id is reported, NO warning.
    od_ok = pd.concat([
        _model_od_gemeinde(),
        pd.DataFrame({"origin_id": ["ghost"], "destination_id": ["a1"],
                      "flow": [3.0]}),
    ], ignore_index=True)
    apply_inner_anchor(od_ok, _zone_map(), _zones(), targets)
    cov_ok = [ln for ln in capsys.readouterr().out.splitlines()
              if "model-side join coverage" in ln]
    assert len(cov_ok) == 1
    assert "ghost" in cov_ok[0]
    assert "WARNING" not in cov_ok[0]

    # Near-total collapse: almost all mass on the unmapped "ghost" origin
    # (only b1->a1 = 1.0 maps) -> in-scope 1/101 = 1.0% <
    # LOW_INSCOPE_MASS_WARN_FRACTION (0.1) -> WARNING on the coverage line.
    od_bad = pd.DataFrame({
        "origin_id":      ["ghost", "b1"],
        "destination_id": ["a1", "a1"],
        "flow":           [100.0, 1.0],
    })
    apply_inner_anchor(od_bad, _zone_map(), _zones(), targets)
    cov_bad = [ln for ln in capsys.readouterr().out.splitlines()
               if "model-side join coverage" in ln]
    assert len(cov_bad) == 1
    assert "WARNING" in cov_bad[0]


def test_p38_band_shares_edges():
    from braunschweig.calibration.anchor_holdout import p38_band_shares
    shares = p38_band_shares(np.array([3.0, 7.0, 250.0]),
                             np.array([1.0, 1.0, 2.0]))
    assert math.isclose(shares[0], 0.25)   # <5 km
    assert math.isclose(shares[1], 0.25)   # 5-10
    assert math.isclose(shares[7], 0.50)   # 200-300
    assert math.isclose(shares.sum(), 1.0)


def test_verdict_pre_registered_rule_v2():
    """Rule v2 (#193, amended 2026-07-17 BEFORE any measurement run; v1's
    held-out-CV criterion (i) was proven structurally inert, see
    test_heldout_cv_is_inert_by_construction). The only axes the anchor can
    move are (a) zone-level AO margins (never fitted by the anchor) and
    (b) the distance distributions; everything else is conserved by the
    anchor's own invariants. Rule v2 therefore requires
    (i') AO-margin corroboration beyond measured fold noise AND
    (ii) no P13-by-RS7 EMD regression beyond that class's measured fold
    noise. All noise scales are MEASURED (fold-to-fold std), never invented.
    """
    from braunschweig.calibration.anchor_holdout import verdict

    # (i') satisfied: 0.20 -> 0.15 improves by 0.05 > ao_noise 0.01;
    # (ii) satisfied: EMD unchanged.
    good = verdict(ao_srmse_before=0.20, ao_srmse_after=0.15, ao_noise=0.01,
                   p13_emd_baseline={"72": 0.05}, p13_emd_anchored={"72": 0.05},
                   p13_noise_by_rs7={"72": 0.005})
    assert good["ao_improves"] is True
    assert good["no_distance_regression"] is True
    assert good["default_flip_supported"] is True

    # (i') fails: the AO improvement (0.005) is WITHIN the measured noise
    # (0.01) -> not corroborated -> no flip, even without any P13 regression.
    within_noise = verdict(
        ao_srmse_before=0.20, ao_srmse_after=0.195, ao_noise=0.01,
        p13_emd_baseline={"72": 0.05}, p13_emd_anchored={"72": 0.05},
        p13_noise_by_rs7={"72": 0.005})
    assert within_noise["ao_improves"] is False
    assert within_noise["default_flip_supported"] is False

    # (ii) fails: RS7 class 72 regresses 0.05 -> 0.10 beyond its noise
    # 0.005 -> no flip, even though the AO axis corroborates.
    regressed = verdict(
        ao_srmse_before=0.20, ao_srmse_after=0.15, ao_noise=0.01,
        p13_emd_baseline={"72": 0.05}, p13_emd_anchored={"72": 0.10},
        p13_noise_by_rs7={"72": 0.005})
    assert regressed["p13_regressions"] == {"72": (0.10, 0.05)}
    assert regressed["no_distance_regression"] is False
    assert regressed["default_flip_supported"] is False

    # Fail-loud contract: a baseline RS7 class WITHOUT a measured noise value
    # must raise (a silently-defaulted noise would make the regression gate
    # vacuous for that class -- the NaN-poisoning failure mode all over again).
    with pytest.raises(RuntimeError, match="noise"):
        verdict(ao_srmse_before=0.20, ao_srmse_after=0.15, ao_noise=0.01,
                p13_emd_baseline={"72": 0.05, "73": 0.04},
                p13_emd_anchored={"72": 0.05, "73": 0.04},
                p13_noise_by_rs7={"72": 0.005})
