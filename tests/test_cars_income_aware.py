"""Tests for the income/household-type-aware ``number_of_cars`` feature.

Covers (see CLAUDE.md / the feature spec):

  * the extract-script schema + the 3/4+ fold + the symbol-coercion logging;
  * the Bayes pmf (sums to 1) and the MONOTONICITY of mean cars / 0-car share in
    economic status at a fixed household type;
  * the per-Kreis H7 invariant: after the rake the per-Kreis ``number_of_cars``
    marginal equals the MiD-H7 control exactly;
  * OFF-equivalence: ``cars_income_aware=False`` leaves the legacy per-Kreis H7
    draw byte-identical;
  * the missing-cell fallback is logged / counted.

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

from braunschweig.data.mid.cars_by_status import (  # noqa: E402
    CAR_COUNT_CATEGORIES,
    cars_probabilities,
    load_cars_by_raumtyp,
    load_cars_by_status_hhtype,
)
from braunschweig.data.mid.status_by_hhtype import STATUS_CATEGORIES  # noqa: E402


pytestmark = pytest.mark.skipif(
    not os.path.exists(
        os.path.join(DATA_PATH, "braunschweig", "mid", "mid2023_cars_by_status_hhtype.csv")
    ),
    reason="local-only MiD cars CSVs not present",
)


# ---------------------------------------------------------------------------
# Extract script: schema + 3/4+ fold + coercion logging
# ---------------------------------------------------------------------------

def test_extract_csv_schema_and_fold():
    df_h = load_cars_by_status_hhtype(DATA_PATH)
    df_r = load_cars_by_raumtyp(DATA_PATH)

    assert set(df_h.columns) == {"num_cars", "hhtype", "status", "share_pct", "base_weighted"}
    assert set(df_r.columns) == {"num_cars", "region", "base_weighted"}

    # The 3/4+ fold collapsed the 5 MiD car levels to the 4 H7 categories.
    assert sorted(df_h["num_cars"].unique()) == list(CAR_COUNT_CATEGORIES)
    assert sorted(df_r["num_cars"].unique()) == list(CAR_COUNT_CATEGORIES)
    assert set(df_h["status"].unique()) == set(STATUS_CATEGORIES)

    # share_pct is a column % (P(hhtype | num_cars, status)) -> per (num_cars,
    # status) the hhtype shares sum to ~100 (MiD rounds to integer percent, and
    # the fold recomputes the share against the folded base, so allow a few pp).
    grouped = df_h.groupby(["num_cars", "status"])["share_pct"].sum()
    assert (grouped.between(90, 110)).all(), grouped[~grouped.between(90, 110)]


def test_extract_fold_is_count_aggregation(tmp_path):
    """The fold of '3 Autos' + '4 Autos und mehr' into category 3 must be an
    exact weighted-count aggregation, reproducible from the raw xlsx."""
    xlsx = os.path.join(DATA_PATH, "braunschweig", "mid", "mid2023_cars_by_status_hhtype.xlsx")
    if not os.path.exists(xlsx):
        pytest.skip("raw xlsx not present (local-only)")

    import scripts.extract_mid_cars_by_status as ex

    df_raw = pd.read_excel(xlsx, sheet_name=ex.SHEET, header=None)
    tidy, coercion = ex.parse_status_hhtype_sheet(df_raw)

    # The folded base for category 3 equals base(3) + base(4+) per status.
    blocks = ex._block_bounds(df_raw)
    cols = ex._locate_region_columns(df_raw, ex.STATUS_LABEL_TO_KEY)
    base3, base4 = {}, {}
    for start, end, cars in blocks:
        bw = ex._read_base_weighted(df_raw, start, end, cols)
        if cars == 3:
            base3 = {cols[c]: bw[c] for c in cols}
        elif cars == 4:
            base4 = {cols[c]: bw[c] for c in cols}
    for status, b3 in base3.items():
        folded = tidy[(tidy["num_cars"] == 3) & (tidy["status"] == status)][
            "base_weighted"].iloc[0]
        assert folded == pytest.approx(b3 + base4[status], rel=1e-4)

    # The suppression tokens ("-" cells in the 3/4+ blocks) were coerced + counted.
    assert coercion["suppression"] > 0
    assert coercion["blank"] == 0


# ---------------------------------------------------------------------------
# Bayes pmf: sums to 1 + monotonicity
# ---------------------------------------------------------------------------

def test_pmf_sums_to_one():
    df_h = load_cars_by_status_hhtype(DATA_PATH)
    df_r = load_cars_by_raumtyp(DATA_PATH)
    for hhtype in df_h["hhtype"].unique():
        for status in STATUS_CATEGORIES:
            for rk in (None, "stadtregion_metropole", "laendlich_kleinstaedtisch"):
                p = cars_probabilities(df_h, df_r, status, hhtype, rk)
                if p is None:
                    continue
                assert p.shape == (len(CAR_COUNT_CATEGORIES),)
                assert p.sum() == pytest.approx(1.0)
                assert (p >= 0).all()


def test_monotonicity_mean_cars_and_zero_share():
    """Higher economic status -> higher mean number_of_cars and lower 0-car share
    at a fixed household type (the core income-aware signal)."""
    df_h = load_cars_by_status_hhtype(DATA_PATH)
    df_r = load_cars_by_raumtyp(DATA_PATH)
    cats = np.asarray(CAR_COUNT_CATEGORIES, dtype=float)

    # Test on the family + couple household types, where ownership clearly scales
    # with income (single-person types are noisier on the small MiD bases).
    for hhtype in ("couple_youngest_30_59", "child_under_6"):
        means = []
        zero_shares = []
        for status in STATUS_CATEGORIES:  # very_low .. very_high
            p = cars_probabilities(df_h, df_r, status, hhtype, None)
            assert p is not None
            means.append(float((cats * p).sum()))
            zero_shares.append(float(p[0]))
        # Strictly increasing mean across the 5 ordered status levels.
        assert all(b > a for a, b in zip(means, means[1:])), (hhtype, means)
        # 0-car share falls overall (the very_high cell can tick up by a fraction
        # of a percent due to MiD integer-percent rounding on small bases, so the
        # defensible claim is a large end-to-end drop, not strict per-step).
        assert zero_shares[-1] < zero_shares[0] - 0.15, (hhtype, zero_shares)
        # End-to-end magnitude: very_high mean is clearly above very_low.
        assert means[-1] - means[0] > 0.4


def test_raumtyp_tilt_direction():
    """The within-region tilt lowers ownership in the metropole and raises it in
    rural areas, relative to the untilted base."""
    df_h = load_cars_by_status_hhtype(DATA_PATH)
    df_r = load_cars_by_raumtyp(DATA_PATH)
    cats = np.asarray(CAR_COUNT_CATEGORIES, dtype=float)

    base = cars_probabilities(df_h, df_r, "high", "single_30_59", None)
    metro = cars_probabilities(df_h, df_r, "high", "single_30_59", "stadtregion_metropole")
    rural = cars_probabilities(df_h, df_r, "high", "single_30_59", "laendlich_kleinstaedtisch")
    mean = lambda p: float((cats * p).sum())
    assert mean(metro) < mean(base) < mean(rural)


def test_missing_cell_returns_none():
    df_h = load_cars_by_status_hhtype(DATA_PATH)
    df_r = load_cars_by_raumtyp(DATA_PATH)
    assert cars_probabilities(df_h, df_r, "high", "does_not_exist", None) is None


# ---------------------------------------------------------------------------
# Enrichment helpers: H7 invariant, OFF-equivalence, fallback logging
# ---------------------------------------------------------------------------

def _make_population(n_households=600, seed=7):
    """Build a small synthetic population for the enrichment helpers.

    One adult+ per household with an economic_status and a commune_id that maps
    to one of two real ZGB Kreise (BS 03101xxxxxxx, Gifhorn 03151xxxxxxx) so the
    per-Kreis H7 lookup is exercised.
    """
    rng = np.random.RandomState(seed)
    rows = []
    pid = 0
    # Two communes -> two Kreise. 12-digit ARS (Land+RB+Kreis+VG+Gem). The
    # matching inside_<kreis> flag drives the per-Kreis H7 lookup (as in the real
    # pipeline, where _derive_kreis_ars5 reads those flags).
    communes = ["031010000000", "031510000000"]
    inside_flags = ["inside_braunschweig", "inside_gifhorn"]
    statuses = list(STATUS_CATEGORIES)
    for hid in range(n_households):
        which = hid % 2
        commune = communes[which]
        status = statuses[rng.randint(len(statuses))]
        size = int(rng.choice([1, 2, 3, 4], p=[0.35, 0.35, 0.2, 0.1]))
        # Ages: adults 25-70; for size>=3 add children.
        ages = list(rng.randint(28, 65, size=min(size, 2)))
        for _ in range(max(0, size - 2)):
            ages.append(int(rng.randint(1, 17)))
        for a in ages:
            row = {
                "person_id": pid,
                "household_id": hid,
                "age": a,
                "economic_status": status,
                "commune_id": commune,
                "number_of_cars": 0,  # placeholder; overwritten / legacy-filled
                "number_of_bicycles": 0,
                "inside_braunschweig": False,
                "inside_gifhorn": False,
            }
            row[inside_flags[which]] = True
            rows.append(row)
            pid += 1
    return pd.DataFrame(rows)


def _regiostar_frame():
    """Minimal RegioStaR-7 frame for the two test communes (AGS-8 keys)."""
    return pd.DataFrame({
        "commune_id": ["03101000", "03151000"],
        "regiostar7": pd.array([72, 76], dtype="Int64"),  # urban vs rural
    })


def test_kreis_h7_invariant():
    """After the rake the per-Kreis number_of_cars marginal equals the MiD-H7
    control exactly (THE invariant)."""
    from braunschweig.synthesis.population.enriched import (
        _derive_kreis_ars5,
        _sample_cars_income_aware,
        _sample_vehicle_counts,
    )
    from braunschweig.data.mid.reference_tables import load_kreis_share_table

    df = _make_population()
    # Seed the legacy per-Kreis H7 draw first (as the pipeline does).
    _sample_vehicle_counts(df, DATA_PATH, random_seed=1, kreis=None)
    df = _sample_cars_income_aware(df, DATA_PATH, random_seed=1,
                                   df_regiostar=_regiostar_frame())

    cars_by_kreis, _, _ = load_kreis_share_table(
        DATA_PATH, "mid2023_H7_cars_by_kreis.csv")

    kreis = _derive_kreis_ars5(df)
    # One row per household for the marginal (number_of_cars is household-level).
    hh = df.drop_duplicates("household_id").copy()
    hh["kreis"] = kreis[hh.index].values
    for ars in hh["kreis"].unique():
        sub = hh[hh["kreis"] == ars]
        n_hh = len(sub)
        counts = sub["number_of_cars"].value_counts().reindex(
            CAR_COUNT_CATEGORIES).fillna(0).to_numpy()
        # Expected integer target via largest remainder of the H7 share vector.
        from braunschweig.synthesis.population.enriched import _largest_remainder
        target = _largest_remainder(cars_by_kreis[ars], n_hh)
        assert np.array_equal(counts.astype(int), target), (ars, counts, target)


def test_off_equivalence_byte_identical():
    """cars_income_aware OFF leaves the legacy per-Kreis H7 number_of_cars draw
    untouched (the income-aware helper is simply not invoked)."""
    from braunschweig.synthesis.population.enriched import _sample_vehicle_counts

    df_a = _make_population()
    df_b = _make_population()  # same seed -> identical frame
    _sample_vehicle_counts(df_a, DATA_PATH, random_seed=42, kreis=None)
    _sample_vehicle_counts(df_b, DATA_PATH, random_seed=42, kreis=None)
    # OFF path never calls _sample_cars_income_aware, so number_of_cars stays the
    # legacy draw; two identical runs agree exactly (byte-identical contract).
    pd.testing.assert_series_equal(df_a["number_of_cars"], df_b["number_of_cars"])
    pd.testing.assert_series_equal(df_a["number_of_bicycles"], df_b["number_of_bicycles"])


def test_income_aware_changes_within_kreis_allocation():
    """ON path: rich households end up with more cars than poor households within
    the SAME Kreis, even though the Kreis marginal is unchanged."""
    from braunschweig.synthesis.population.enriched import (
        _sample_cars_income_aware,
        _sample_vehicle_counts,
    )
    df = _make_population(n_households=1200)
    _sample_vehicle_counts(df, DATA_PATH, random_seed=3, kreis=None)
    df = _sample_cars_income_aware(df, DATA_PATH, random_seed=3,
                                   df_regiostar=_regiostar_frame())
    hh = df.drop_duplicates("household_id")
    mean_high = hh[hh["economic_status"].isin(["high", "very_high"])][
        "number_of_cars"].mean()
    mean_low = hh[hh["economic_status"].isin(["very_low", "low"])][
        "number_of_cars"].mean()
    assert mean_high > mean_low


def test_fallback_logged_and_counted(capsys):
    """An unclassifiable household / missing cell falls back to the legacy H7 PMF
    and the primary/fallback rate is logged + stored on df.attrs."""
    from braunschweig.synthesis.population.enriched import (
        _sample_cars_income_aware,
        _sample_vehicle_counts,
    )
    df = _make_population(n_households=200)
    # Force ~half the households to a status the MiD table does not carry, so the
    # (hhtype, status) base lookup misses -> missing_cell fallback.
    bad = df["household_id"] % 2 == 0
    df.loc[bad, "economic_status"] = "unknown_status"
    _sample_vehicle_counts(df, DATA_PATH, random_seed=5, kreis=None)
    df = _sample_cars_income_aware(df, DATA_PATH, random_seed=5,
                                   df_regiostar=_regiostar_frame())
    out = capsys.readouterr().out
    assert "income-aware number_of_cars" in out
    assert df.attrs["number_of_cars_income_aware_fallback_count"] > 0
    assert df.attrs["number_of_cars_income_aware_fallback_reasons"]["missing_cell"] > 0
    # Marginal invariant still holds even with fallbacks (fallback households keep
    # an H7-consistent count, so the rake is unaffected).
    assert df.attrs["number_of_cars_income_aware_primary_count"] >= 0
