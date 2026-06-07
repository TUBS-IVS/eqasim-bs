"""Tests for the MiD 2023 income x economic-status reference + combination.

Covers (see CLAUDE.md / the feature spec):

  * the extract-script schema (16 Laender x 5 status x 10 brackets; 7 raumtyp x
    5 x 10) + symbol-coercion logging;
  * the per-status bracket pmf (sums to 1) + monotonicity in status (very_low has
    the lowest mean, very_high the highest);
  * the size x status combination (combine_size_status_bracket_pmf): valid pmf,
    monotone in BOTH status and size, and consistent (combining a conditional with
    itself returns that conditional).

All numeric reference values come from the committed CSVs under
``eqasim-data/data/braunschweig/mid/`` (no Python literals).
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO, "eqasim-data", "data")

from braunschweig.data.mid.income_by_size import (  # noqa: E402
    INCOME_BRACKET_BOUNDS_EUR,
    INCOME_BRACKET_CATEGORIES,
    SIZE_CATEGORIES,
    income_bracket_probabilities,
    load_income_by_size_bundesland,
    load_income_by_size_raumtyp,
)
from braunschweig.data.mid.income_by_status import (  # noqa: E402
    STATUS_CATEGORIES,
    combine_size_status_bracket_pmf,
    income_bracket_probabilities_by_status,
    load_income_by_status_bundesland,
    load_income_by_status_raumtyp,
    overall_bracket_pmf,
)

pytestmark = pytest.mark.skipif(
    not os.path.exists(
        os.path.join(
            DATA_PATH, "braunschweig", "mid", "mid2023_income_by_status_bundesland.csv"
        )
    ),
    reason="local-only MiD income-by-status CSVs not present",
)


def _bracket_midpoints() -> np.ndarray:
    mids = []
    for k in INCOME_BRACKET_CATEGORIES:
        low, high = INCOME_BRACKET_BOUNDS_EUR[k]
        mids.append((low + high) / 2.0 if high is not None else 7000.0 * 1.4)
    return np.asarray(mids, dtype=float)


# ---------------------------------------------------------------------------
# Extract script schema + coercion
# ---------------------------------------------------------------------------

def test_status_csv_schema():
    df_b = load_income_by_status_bundesland(DATA_PATH)
    df_r = load_income_by_status_raumtyp(DATA_PATH)
    expected = {"region", "status", "income_bracket", "share_pct", "base_weighted"}
    assert set(df_b.columns) == expected
    assert set(df_r.columns) == expected
    assert set(df_b["status"].unique()) == set(STATUS_CATEGORIES)
    assert set(df_b["income_bracket"].unique()) == set(INCOME_BRACKET_CATEGORIES)
    assert "niedersachsen" in set(df_b["region"].unique())
    assert len(df_b) == 16 * 5 * 10
    assert len(df_r) == 7 * 5 * 10


def test_status_columns_sum_to_100_nds():
    """Each status column sums to ~100 over brackets (it is P(bracket|status))."""
    df_b = load_income_by_status_bundesland(DATA_PATH)
    nds = df_b[df_b["region"] == "niedersachsen"]
    sums = nds.groupby("status")["share_pct"].sum()
    assert ((sums - 100.0).abs() <= 2.0).all(), sums.to_dict()


def test_status_extract_symbol_coercion_logged():
    xlsx = os.path.join(
        DATA_PATH, "braunschweig", "mid", "mid2023_income_by_status_bundesland.xlsx"
    )
    if not os.path.exists(xlsx):
        pytest.skip("raw xlsx not present (local-only)")
    import scripts.extract_mid_income_by_status as ex

    df_raw = pd.read_excel(xlsx, sheet_name=ex.SHEET, header=None)
    tidy, coercion = ex.parse_sheet(
        df_raw, ex._BUNDESLAND_MARKER, ex.REGION_LABEL_TO_KEY_BUNDESLAND
    )
    assert coercion["suppression"] > 0
    assert coercion["value"] > 0
    assert len(tidy) == 16 * 5 * 10


# ---------------------------------------------------------------------------
# Per-status pmf: sums to 1 + monotone in status
# ---------------------------------------------------------------------------

def test_status_pmf_sums_to_one():
    df_b = load_income_by_status_bundesland(DATA_PATH)
    df_r = load_income_by_status_raumtyp(DATA_PATH)
    for status in STATUS_CATEGORIES:
        for rk in (None, "stadtregion_regiopole_grossstadt", "laendlich_kleinstaedtisch"):
            p = income_bracket_probabilities_by_status(df_b, df_r, status, rk)
            assert p is not None
            assert p.shape == (len(INCOME_BRACKET_CATEGORIES),)
            assert p.sum() == pytest.approx(1.0)
            assert (p >= 0).all()


def test_status_pmf_monotone_in_status():
    df_b = load_income_by_status_bundesland(DATA_PATH)
    df_r = load_income_by_status_raumtyp(DATA_PATH)
    mids = _bracket_midpoints()
    means = [
        float((income_bracket_probabilities_by_status(df_b, df_r, s, None) * mids).sum())
        for s in STATUS_CATEGORIES
    ]
    assert all(b > a for a, b in zip(means, means[1:])), means


def test_overall_marginal_consistent_with_size_pool():
    """The status-pooled overall marginal matches the size-pooled overall marginal
    (both are P(bracket | NDS) computed from the two cross-tabs)."""
    df_sb = load_income_by_status_bundesland(DATA_PATH)
    df_sr = load_income_by_status_raumtyp(DATA_PATH)
    df_b = load_income_by_size_bundesland(DATA_PATH)
    overall = overall_bracket_pmf(df_sb, df_sr, None)
    assert overall is not None and overall.sum() == pytest.approx(1.0)
    # Size-pooled overall (base-weighted over hh_size) for NDS.
    from braunschweig.data.mid.income_by_size import _bracket_pmf_for_region_size
    nds = df_b[df_b["region"] == "niedersachsen"]
    acc = np.zeros(len(INCOME_BRACKET_CATEGORIES))
    tot = 0.0
    for sz in SIZE_CATEGORIES:
        p = _bracket_pmf_for_region_size(nds, "niedersachsen", sz)
        base = float(nds[nds["hh_size"] == sz]["base_weighted"].iloc[0])
        acc += base * p
        tot += base
    size_overall = acc / tot
    tv = 0.5 * np.abs(overall - size_overall).sum()
    assert tv < 0.02, (tv, overall, size_overall)


# ---------------------------------------------------------------------------
# combine_size_status_bracket_pmf
# ---------------------------------------------------------------------------

def test_combine_returns_valid_pmf():
    df_b = load_income_by_size_bundesland(DATA_PATH)
    df_r = load_income_by_size_raumtyp(DATA_PATH)
    df_sb = load_income_by_status_bundesland(DATA_PATH)
    df_sr = load_income_by_status_raumtyp(DATA_PATH)
    overall = overall_bracket_pmf(df_sb, df_sr, None)
    for size in SIZE_CATEGORIES:
        for status in STATUS_CATEGORIES:
            ps = income_bracket_probabilities(df_b, df_r, size, None)
            pt = income_bracket_probabilities_by_status(df_sb, df_sr, status, None)
            comb = combine_size_status_bracket_pmf(ps, pt, overall)
            assert comb.shape == ps.shape
            assert comb.sum() == pytest.approx(1.0)
            assert (comb >= 0).all()


def test_combine_with_self_is_identity():
    """Combining a conditional with itself (status == overall) returns it: the
    odds factor status/overall is 1, so the result is the size conditional."""
    df_b = load_income_by_size_bundesland(DATA_PATH)
    df_r = load_income_by_size_raumtyp(DATA_PATH)
    df_sb = load_income_by_status_bundesland(DATA_PATH)
    df_sr = load_income_by_status_raumtyp(DATA_PATH)
    overall = overall_bracket_pmf(df_sb, df_sr, None)
    ps = income_bracket_probabilities(df_b, df_r, "2", None)
    comb = combine_size_status_bracket_pmf(ps, overall, overall)
    np.testing.assert_allclose(comb, ps, atol=1e-9)


def test_combine_monotone_in_both_dimensions():
    df_b = load_income_by_size_bundesland(DATA_PATH)
    df_r = load_income_by_size_raumtyp(DATA_PATH)
    df_sb = load_income_by_status_bundesland(DATA_PATH)
    df_sr = load_income_by_status_raumtyp(DATA_PATH)
    overall = overall_bracket_pmf(df_sb, df_sr, None)
    mids = _bracket_midpoints()

    # Monotone in status at fixed size.
    means = []
    for status in STATUS_CATEGORIES:
        ps = income_bracket_probabilities(df_b, df_r, "2", None)
        pt = income_bracket_probabilities_by_status(df_sb, df_sr, status, None)
        means.append(float((combine_size_status_bracket_pmf(ps, pt, overall) * mids).sum()))
    assert all(b > a for a, b in zip(means, means[1:])), ("status", means)

    # Monotone in size at fixed status (1..4).
    means = []
    pt = income_bracket_probabilities_by_status(df_sb, df_sr, "medium", None)
    for size in ("1", "2", "3", "4"):
        ps = income_bracket_probabilities(df_b, df_r, size, None)
        means.append(float((combine_size_status_bracket_pmf(ps, pt, overall) * mids).sum()))
    assert all(b > a for a, b in zip(means, means[1:])), ("size", means)
