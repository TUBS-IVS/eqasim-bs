"""Regression tests for the ADR-0056 seed-weight A/B comparator audit fixes.

Each test pins one audited defect (braunschweig/analysis/seed_weight_ab/
compare_seed_weight_quality.py):

  1. The per-control "srmse" must use the canonical denominator (RMSE / mean of
     ALL targets, matching population_validation.quality_assessment.assess), not
     the script's original RMSE / mean of POSITIVE targets only (kept alongside
     as "srmse_pos"). The two must differ whenever a zero-target cell exists.
  2. The person-marginal max share-diff must not silently skip (age_band, HP_SEX)
     cells that are present in only one variant (NaN in the pivot); those cells
     must be filled with 0 and included in the max().
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.analysis.seed_weight_ab.compare_seed_weight_quality import (
    per_control_metrics,
    person_marginal_max_diff,
)


def test_canonical_srmse_differs_from_srmse_pos_with_zero_target_cell():
    # One cell has target == 0; canonical SRMSE divides by mean(ALL targets)
    # (0, 10, 10 -> mean 6.667), the legacy "srmse_pos" divides by mean(POSITIVE
    # targets only) (10, 10 -> mean 10). Same RMSE, different denominator.
    fit = pd.DataFrame({
        "control": ["c1", "c1", "c1"],
        "target": [0, 10, 10],
        "realised": [2, 8, 12],
        "abs_error": [2, 2, 2],
    })

    out = per_control_metrics(fit, "v")
    row = out.iloc[0]

    # rmse = sqrt(mean([(2-0)^2, (8-10)^2, (12-10)^2])) = sqrt(4) = 2.0
    assert row["rmse"] == pytest.approx(2.0)
    # canonical: 2.0 / mean([0, 10, 10]) = 2.0 / 6.6667 = 0.3
    assert row["srmse"] == pytest.approx(0.3)
    # legacy positive-only: 2.0 / mean([10, 10]) = 2.0 / 10 = 0.2
    assert row["srmse_pos"] == pytest.approx(0.2)
    assert row["srmse"] != pytest.approx(row["srmse_pos"])
    # canonical must be the larger of the two (smaller denominator).
    assert row["srmse"] > row["srmse_pos"]


def test_srmse_columns_nan_when_all_targets_zero():
    """Both denominators are undefined (no positive mean) -- NaN, not a crash
    or a fabricated 0/0 value."""
    fit = pd.DataFrame({
        "control": ["allzero", "allzero"],
        "target": [0, 0],
        "realised": [3, 5],
        "abs_error": [3, 5],
    })
    out = per_control_metrics(fit, "v")
    row = out.iloc[0]
    assert pd.isna(row["srmse"])
    assert pd.isna(row["srmse_pos"])


def test_person_marginal_max_diff_includes_one_variant_only_cell():
    """(18-29, m) exists only in variant A (share 0.9); variant B never drew a
    person in that cell at all, so it is NaN, not 0, in the raw pivot.

    Before the fix, `(shares[a] - shares[b]).abs().max()` used pandas' default
    skipna=True, so the NaN row was silently dropped from the max -- reporting
    max_diff == 0.0 (the only surviving row, (0-17, m), truly ties at 0.0) even
    though variant A holds a 0.9 share in a cell variant B has none of at all.
    The fix must fill the missing side with 0 and report max_diff == 0.9.
    """
    marginals = pd.DataFrame({
        "variant": ["A", "A", "B"],
        "age_band": ["0-17", "18-29", "0-17"],
        "HP_SEX": ["m", "m", "m"],
        "share": [0.5, 0.9, 0.5],
    })

    max_diff, n_variant_only = person_marginal_max_diff(marginals, "A", "B")

    assert n_variant_only == 1
    assert max_diff == pytest.approx(0.9)


def test_person_marginal_max_diff_zero_when_all_cells_shared():
    """Sanity check: with no variant-only cells, n_variant_only is 0 and the
    max diff reduces to the plain paired comparison."""
    marginals = pd.DataFrame({
        "variant": ["A", "A", "B", "B"],
        "age_band": ["0-17", "18-29", "0-17", "18-29"],
        "HP_SEX": ["m", "m", "m", "m"],
        "share": [0.5, 0.5, 0.6, 0.4],
    })

    max_diff, n_variant_only = person_marginal_max_diff(marginals, "A", "B")

    assert n_variant_only == 0
    assert max_diff == pytest.approx(0.1)
