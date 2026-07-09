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


def test_subtype_donor_mean_km_range_covers_all_seven_groups():
    # Cited-from-spec sanity ranges must cover every subtype group named in
    # the design spec's Taxonomy tables (no silent gaps).
    expected_groups = {
        "leisure_local", "leisure_visit", "leisure_activity", "leisure_excursion",
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
