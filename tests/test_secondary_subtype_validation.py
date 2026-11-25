"""Unit tests for the subtype validation helpers (issue #127, Task 6).

Covers the pure, synthetic-frame-only helpers added to
``braunschweig.calibration.secondary_measurement`` for the leisure/other
W_ZWD subtype split: per-group realised distance summaries, the
"leisure_visit" residential-placement share check, and the generic
boundary-clip share computation also used (via a per-run print) by
``braunschweig.synthesis.locations.secondary_chainsolvers`` for the
"leisure_excursion" transparency log.

No MiD/ALKIS data is used -- all frames are hand-built with known group means
and counts so every assertion can be verified by hand.
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration.secondary_measurement import (
    SUBTYPE_DONOR_MEAN_KM_RANGE,
    boundary_clip_share,
    leave_out_loop_legs,
    per_group_distance_summary,
    placement_share_at_positive_potential,
)

# Loaded via importlib (mirrors tests/test_secondary_distance_dispatch.py) so
# the script-level sys.path insertion fires correctly.
_spec = importlib.util.spec_from_file_location(
    "validate_secondary_distances",
    pathlib.Path("scripts/validate_secondary_distances.py"),
)
_validate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validate_mod)


# ---------------------------------------------------------------------------
# per_group_distance_summary
# ---------------------------------------------------------------------------


def test_per_group_distance_summary_unweighted_matches_hand_computed_means():
    df = pd.DataFrame({
        "group": ["leisure_local", "leisure_local", "leisure_visit", "leisure_visit", "leisure_visit"],
        "distance_km": [4.0, 6.0, 10.0, 20.0, 30.0],
    })
    summary = per_group_distance_summary(df, "group", "distance_km").set_index("group")
    assert summary.loc["leisure_local", "n"] == 2
    assert summary.loc["leisure_local", "mean_distance"] == pytest.approx(5.0)
    assert summary.loc["leisure_visit", "n"] == 3
    assert summary.loc["leisure_visit", "mean_distance"] == pytest.approx(20.0)


def test_per_group_distance_summary_weighted_matches_hand_computed_weighted_mean():
    df = pd.DataFrame({
        "group": ["other_escort", "other_escort", "other_escort"],
        "distance_km": [4.0, 8.0, 12.0],
        "weight": [1.0, 1.0, 2.0],
    })
    # weighted mean = (4*1 + 8*1 + 12*2) / (1 + 1 + 2) = 36 / 4 = 9.0
    summary = per_group_distance_summary(df, "group", "distance_km", weight_column="weight")
    row = summary.iloc[0]
    assert row["group"] == "other_escort"
    assert row["n"] == 3
    assert row["mean_distance"] == pytest.approx(9.0)


def test_per_group_distance_summary_multiple_groups_weighted():
    df = pd.DataFrame({
        "group": ["other_errand_short", "other_errand_short", "other_errand_long"],
        "distance_km": [5.0, 7.0, 15.0],
        "weight": [2.0, 2.0, 1.0],
    })
    summary = per_group_distance_summary(df, "group", "distance_km", weight_column="weight").set_index("group")
    assert summary.loc["other_errand_short", "mean_distance"] == pytest.approx(6.0)
    assert summary.loc["other_errand_long", "mean_distance"] == pytest.approx(15.0)
    assert summary.loc["other_errand_short", "n"] == 2
    assert summary.loc["other_errand_long", "n"] == 1


def test_per_group_distance_summary_empty_frame_returns_empty_with_expected_columns():
    df = pd.DataFrame(columns=["group", "distance_km"])
    summary = per_group_distance_summary(df, "group", "distance_km")
    assert list(summary.columns) == ["group", "n", "mean_distance"]
    assert len(summary) == 0


def test_per_group_distance_summary_zero_weight_group_raises():
    df = pd.DataFrame({
        "group": ["other_escort"],
        "distance_km": [5.0],
        "weight": [0.0],
    })
    with pytest.raises(ValueError):
        per_group_distance_summary(df, "group", "distance_km", weight_column="weight")


def test_subtype_donor_mean_km_range_covers_every_subtype_group():
    # The in-sample sanity pins must cover every subtype group a run can realise
    # (no silent gaps): the four leisure groups of issue #127 plus
    # "leisure_unspecified" (the fifth leisure subtype, issue #373 / ADR-0115)
    # and the three "other" groups. Since ADR-0116 all eight are POINT pins from
    # one ad-hoc measurement on the weekday diary universe -- the frame each
    # layer is built from -- not the ranges the 2026-07-09 design spec's
    # taxonomy tables carried; see the dict's own comment for frame and date.
    expected_groups = {
        "leisure_local", "leisure_visit", "leisure_activity", "leisure_excursion",
        "leisure_unspecified",
        "other_errand_short", "other_errand_long", "other_escort",
    }
    assert set(SUBTYPE_DONOR_MEAN_KM_RANGE.keys()) == expected_groups
    for lo, hi in SUBTYPE_DONOR_MEAN_KM_RANGE.values():
        assert lo <= hi


# ---------------------------------------------------------------------------
# placement_share_at_positive_potential
# ---------------------------------------------------------------------------


def test_placement_share_all_positive_returns_one():
    df = pd.DataFrame({"pot_visit": [1.0, 5.0, 0.2]})
    share, n_pos, n_total = placement_share_at_positive_potential(df, "pot_visit")
    assert share == pytest.approx(1.0)
    assert (n_pos, n_total) == (3, 3)


def test_placement_share_mixed_positive_and_zero():
    df = pd.DataFrame({"pot_visit": [1.0, 0.0, 0.0, 2.0]})
    share, n_pos, n_total = placement_share_at_positive_potential(df, "pot_visit")
    assert share == pytest.approx(0.5)
    assert (n_pos, n_total) == (2, 4)


def test_placement_share_respects_group_mask():
    df = pd.DataFrame({
        "activity": ["leisure_visit", "leisure_local", "leisure_visit"],
        "pot_visit": [1.0, 0.0, 0.0],
    })
    mask = df["activity"] == "leisure_visit"
    share, n_pos, n_total = placement_share_at_positive_potential(df, "pot_visit", group_mask=mask)
    assert n_total == 2
    assert n_pos == 1
    assert share == pytest.approx(0.5)


def test_placement_share_empty_subset_returns_nan():
    df = pd.DataFrame({"activity": ["leisure_local"], "pot_visit": [0.0]})
    mask = df["activity"] == "leisure_visit"
    share, n_pos, n_total = placement_share_at_positive_potential(df, "pot_visit", group_mask=mask)
    assert np.isnan(share)
    assert (n_pos, n_total) == (0, 0)


# ---------------------------------------------------------------------------
# boundary_clip_share
# ---------------------------------------------------------------------------


def test_boundary_clip_share_scalar_ceiling():
    desired = np.array([10_000.0, 50_000.0, 90_000.0, 120_000.0])
    share, n_clipped, n_total = boundary_clip_share(desired, ceiling_m=100_000.0)
    assert n_total == 4
    assert n_clipped == 1  # only 120_000 > 100_000
    assert share == pytest.approx(0.25)


def test_boundary_clip_share_per_leg_ceiling_array():
    desired = np.array([10.0, 20.0, 30.0])
    ceiling = np.array([5.0, 25.0, 35.0])
    share, n_clipped, n_total = boundary_clip_share(desired, ceiling)
    # 10 > 5 clipped; 20 <= 25 not clipped; 30 <= 35 not clipped.
    assert n_clipped == 1
    assert n_total == 3
    assert share == pytest.approx(1.0 / 3.0)


def test_boundary_clip_share_none_clipped():
    desired = np.array([1.0, 2.0, 3.0])
    share, n_clipped, n_total = boundary_clip_share(desired, ceiling_m=100.0)
    assert n_clipped == 0
    assert share == pytest.approx(0.0)


def test_boundary_clip_share_all_clipped():
    desired = np.array([200.0, 300.0])
    share, n_clipped, n_total = boundary_clip_share(desired, ceiling_m=100.0)
    assert n_clipped == 2
    assert share == pytest.approx(1.0)


def test_boundary_clip_share_empty_returns_nan():
    share, n_clipped, n_total = boundary_clip_share(np.array([]), ceiling_m=10.0)
    assert np.isnan(share)
    assert (n_clipped, n_total) == (0, 0)


# ---------------------------------------------------------------------------
# build_subtype_report (validation script): honest skip vs real computation.
# ---------------------------------------------------------------------------


def test_build_subtype_report_logs_honest_skip_when_no_subtype_column(caplog):
    # Today's cached stage output carries no subtype label (see the module
    # note in scripts/validate_secondary_distances.py) -- the report must say
    # so explicitly rather than silently doing nothing.
    distances = {
        "shop": pd.DataFrame({"euclidean_km": [1.0], "mode": ["car"]}),
        "leisure": pd.DataFrame({"euclidean_km": [2.0], "mode": ["car"]}),
        "other": pd.DataFrame({"euclidean_km": [3.0], "mode": ["car"]}),
    }
    with caplog.at_level(logging.INFO):
        _validate_mod.build_subtype_report(distances)
    messages = "\n".join(caplog.messages)
    assert "NOT available in this cache" in messages
    assert "activity_subtype" in messages
    assert "leisure_visit placement share: NOT available" in messages


def test_build_subtype_report_computes_real_rows_when_subtype_column_present(caplog):
    df_leisure = pd.DataFrame({
        "euclidean_km": [4.0, 6.0, 19.0, 21.0],
        "mode": ["car", "car", "car", "car"],
        "activity_subtype": ["leisure_local", "leisure_local", "leisure_visit", "leisure_visit"],
        "pot_visit": [0.0, 0.0, 5.0, 0.0],
    })
    distances = {
        "shop": pd.DataFrame({"euclidean_km": [], "mode": []}),
        "leisure": df_leisure,
        "other": pd.DataFrame({"euclidean_km": [], "mode": []}),
    }
    with caplog.at_level(logging.INFO):
        _validate_mod.build_subtype_report(distances)
    messages = "\n".join(caplog.messages)
    # Per-group mean for leisure_local: (4+6)/2 = 5.0 km.
    assert "leisure_local" in messages and "5.00 km" in messages
    # Per-group mean for leisure_visit: (19+21)/2 = 20.0 km.
    assert "leisure_visit" in messages and "20.00 km" in messages
    # In-sample sanity line cites the spec, not a target.
    assert "in-sample sanity (NOT a validated target" in messages
    # Placement share: 1/2 leisure_visit legs have pot_visit > 0 -> 50.0%.
    assert "leisure_visit placement share (pot_visit > 0): 1/2 (50.0%" in messages


# ---------------------------------------------------------------------------
# Loops left out of the comparison (issue #679)
# ---------------------------------------------------------------------------


def _write_stage_caches(directory, secondary_loop_origin):
    """One person: home, shop (1 km away), a stroll that starts at the shop, a leisure leg of 3 km.

    ``secondary_loop_origin`` is the ``loop_origin`` column of the secondary locations result
    (a list per activity), or the string ``"absent"`` (column missing, switch off) or
    ``"no_stage"`` (no secondary pickle in the cache at all).
    """
    import pickle

    import geopandas as gpd
    from shapely.geometry import Point

    xs = [0.0, 1000.0, 1000.0, 4000.0, 0.0]
    frame = gpd.GeoDataFrame({
        "person_id": 1, "activity_index": range(5), "location_id": [-1, 7, 7, 8, -1],
        "geometry": [Point(x, 0.0) for x in xs]}, crs="EPSG:25832")
    activities = pd.DataFrame({"person_id": 1, "activity_index": range(5),
                               "purpose": ["home", "shop", "leisure", "leisure", "home"]})
    trips = pd.DataFrame({"person_id": 1, "trip_index": range(1, 5), "mode": "car"})
    stages = {"synthesis.population.spatial.locations": frame,
              "synthesis.population.activities": activities,
              "synthesis.population.trips": trips}
    if secondary_loop_origin != "no_stage":
        secondary = frame.copy()
        if secondary_loop_origin != "absent":
            secondary["loop_origin"] = pd.Series(secondary_loop_origin, dtype=object)
        stages["braunschweig.synthesis.locations.secondary_chainsolvers"] = (
            secondary, pd.DataFrame())
    for name, content in stages.items():
        with open(directory / (name + "__test.p"), "wb") as handle:
            pickle.dump(content, handle)


def test_the_comparison_leaves_the_loop_legs_out_and_reports_their_count_and_share(tmp_path):
    _write_stage_caches(tmp_path, [None, None, "secondary", None, None])
    distances = _validate_mod.measure_secondary_distances(str(tmp_path))
    # The leg INTO the stroll carries the loop mark; its length is the 0 km of a loop.
    assert distances["leisure"]["loop_origin"].tolist() == ["secondary", None]
    assert distances["leisure"]["euclidean_km"].tolist() == pytest.approx([0.0, 3.0])

    kept, summary = leave_out_loop_legs(distances)
    assert kept["leisure"]["euclidean_km"].tolist() == pytest.approx([3.0])
    assert kept["shop"]["euclidean_km"].tolist() == pytest.approx([1.0])
    by_purpose = summary.set_index("purpose")
    assert by_purpose.loc["leisure", "n_loops"] == 1 and by_purpose.loc["leisure", "n_legs"] == 2
    assert by_purpose.loc["leisure", "loop_share"] == pytest.approx(0.5)
    assert by_purpose.loc["shop", "n_loops"] == 0
    # The report on the loop-free legs sees one leisure leg.
    rows = _validate_mod.build_report(kept, _w12_targets_stub())
    assert {row["purpose"]: row["n"] for row in rows} == {"shop": 1, "leisure": 1, "other": 0}


@pytest.mark.parametrize("secondary_loop_origin", ["absent", "no_stage"])
def test_without_a_loop_column_the_comparison_is_unchanged(tmp_path, caplog, secondary_loop_origin):
    """Switch off (no ``loop_origin`` in the secondary result) or a cache with no such stage:
    the legs keep every row and column, no loop summary exists, and the log says which of the
    two it was instead of staying silent."""
    _write_stage_caches(tmp_path, secondary_loop_origin)
    with caplog.at_level(logging.INFO):
        distances = _validate_mod.measure_secondary_distances(str(tmp_path))
    assert list(distances["leisure"].columns) == ["euclidean_km", "mode"]
    assert distances["leisure"]["euclidean_km"].tolist() == pytest.approx([0.0, 3.0])
    kept, summary = leave_out_loop_legs(distances)
    assert summary is None and kept is distances
    assert "loop_origin" in "\n".join(caplog.messages)


def _w12_targets_stub():
    """W12 band shares of three purposes (uniform) -- the EMD value itself is not asserted."""
    uniform = np.full(9, 1.0 / 9.0)
    return {"shop": uniform, "leisure": uniform, "other": uniform,
            "shop_mean_km": 5.0, "leisure_mean_km": 15.0, "other_mean_km": 11.0}


@pytest.mark.parametrize("secondary_loop_origin, loops_reported", [
    ([None, None, "secondary", None, None], True),
    ("absent", False),
])
def test_the_script_reports_the_loops_and_the_survey_table_only_when_loops_are_marked(
        tmp_path, monkeypatch, capsys, secondary_loop_origin, loops_reported):
    """Marked loops: printed summary, the survey table named, one extra CSV row. No marker
    (switch off): no loop text and the CSV is the per-purpose table it always was."""
    cache = tmp_path / "cache"
    cache.mkdir()
    _write_stage_caches(cache, secondary_loop_origin)
    out_csv = tmp_path / "out.csv"
    monkeypatch.setattr("sys.argv", [
        "validate_secondary_distances.py", "--cache", str(cache),
        "--mid-dir", "eqasim-data/data/braunschweig/mid", "--output", str(out_csv)])
    _validate_mod.main()

    printed = capsys.readouterr().out
    written = pd.read_csv(out_csv)
    assert ("Loop legs" in printed) is loops_reported
    assert ("mid2023_W12_triplength_by_purpose.csv" in printed) is loops_reported
    if loops_reported:
        loop_row = written[written["purpose"] == "loops_left_out"].iloc[0]
        assert loop_row["n"] == 1
        assert loop_row["loop_share_of_secondary_legs"] == pytest.approx(1 / 3)  # 1 of 3 legs
        assert written.set_index("purpose").loc["leisure", "n"] == 1
    else:
        assert list(written["purpose"]) == ["shop", "leisure", "other"]
        assert "loop_share_of_secondary_legs" not in written.columns
        assert written.set_index("purpose").loc["leisure", "n"] == 2
