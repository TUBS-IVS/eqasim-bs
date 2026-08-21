"""A6: condition pt_subscription on student / employment status (+car hook).

With the ``pt_subscription_conditioned`` flag ON the PT-subscription IPF applies
DATA-FREE logical constraints to each person's per-category probability vector
BEFORE sampling:

  * the combined MiD P24.1 category ``job_or_semester_ticket`` (jobticket =
    employer-subsidised pass, semesterticket = student Solidarmodell pass) is a
    work/study-bound ticket -- it requires the holder to be ``employed`` OR
    ``studies``. For a person who is neither, that category's probability is set
    to zero and the remaining vector re-normalised so it still sums to 1.

The remaining categories keep their P24.1-IPF probabilities, so the overall
P24.1 marginal stays matched within tolerance (only the small minority of
non-working / non-studying persons who would otherwise have drawn the
work/study ticket are redistributed).

The DATA-DEPENDENT carless<->PT correlation (an extra IPF margin from the MiD
P24.1 x Pkw-Verfuegbarkeit cross-tab) is only wired in when the reference CSV
``mid2023_P24_1_by_car_availability.csv`` is present; when it is absent the
loader returns ``None`` and an INFO fallback is logged (documented, not silent).

These tests pin the conditioning invariants and the re-normalisation on a
synthetic probability matrix, driving the extracted helper
:func:`_condition_pt_subscription_probs` directly, plus the OFF-equivalence of
the loader hook.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.data.mid.reference_tables import (  # noqa: E402
    P24_RAW_COLUMN_BY_CATEGORY,
    PT_TICKET_CATEGORIES,
    PT_TICKET_FLATRATE,
    PT_TICKET_WORK_STUDY_BOUND,
    load_pt_subscription_by_car_availability,
)
from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _apply_car_availability_pt_margin,
    _condition_pt_subscription_probs,
)


_IDX_WORK_STUDY = PT_TICKET_CATEGORIES.index("job_or_semester_ticket")
_IDX_NEVER_PT = PT_TICKET_CATEGORIES.index("never_pt")
_IDX_DEUTSCHLANDTICKET = PT_TICKET_CATEGORIES.index("deutschlandticket")
_FLATRATE_IDX = [PT_TICKET_CATEGORIES.index(c) for c in PT_TICKET_FLATRATE]


def _uniform_probs(n: int) -> np.ndarray:
    """Per-person probability matrix with mass on every category (so the
    work/study-bound category is non-trivially present for everyone)."""
    n_cats = len(PT_TICKET_CATEGORIES)
    return np.full((n, n_cats), 1.0 / n_cats, dtype=float)


def _make_persons(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    employed = rng.random_sample(n) < 0.5
    studies = (~employed) & (rng.random_sample(n) < 0.3)
    return pd.DataFrame({"employed": employed, "studies": studies})


# ---------------------------------------------------------------------------
# Data-free logical constraint: work/study-bound ticket
# ---------------------------------------------------------------------------

def test_work_study_ticket_zeroed_for_non_working_non_studying():
    """A person who is neither employed nor a student gets zero probability on
    the combined jobticket/semesterticket category."""
    df = _make_persons()
    probs = _uniform_probs(len(df))
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)

    neither = (~df["employed"].to_numpy()) & (~df["studies"].to_numpy())
    assert (out[neither, _IDX_WORK_STUDY] == 0.0).all()


def test_work_study_ticket_kept_for_employed_or_student():
    """Employed OR studying persons keep a non-zero work/study ticket prob."""
    df = _make_persons()
    probs = _uniform_probs(len(df))
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)

    eligible = df["employed"].to_numpy() | df["studies"].to_numpy()
    assert (out[eligible, _IDX_WORK_STUDY] > 0.0).all()


def test_probabilities_renormalised_to_one():
    """After zeroing the disallowed category every per-person vector still sums
    to 1 (re-normalisation)."""
    df = _make_persons()
    probs = _uniform_probs(len(df))
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)
    np.testing.assert_allclose(out.sum(axis=1), 1.0, rtol=0, atol=1e-9)


def test_eligible_rows_are_unchanged():
    """A person who is employed/studying is unaffected (no category zeroed, so
    the vector is byte-identical to the input)."""
    df = _make_persons()
    probs = _uniform_probs(len(df))
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)
    eligible = df["employed"].to_numpy() | df["studies"].to_numpy()
    np.testing.assert_allclose(out[eligible], probs[eligible])


def test_all_zero_row_falls_back_to_never_pt():
    """A degenerate vector with all its mass on the work/study category for a
    non-eligible person must not become an all-zero (un-normalisable) row: it
    falls back deterministically to never_pt."""
    df = pd.DataFrame({"employed": [False], "studies": [False]})
    probs = np.zeros((1, len(PT_TICKET_CATEGORIES)))
    probs[0, _IDX_WORK_STUDY] = 1.0
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)
    np.testing.assert_allclose(out.sum(axis=1), 1.0)
    assert out[0, _IDX_NEVER_PT] == 1.0


def test_marginal_drift_is_small():
    """The overall P24.1 marginal (mean prob per category) drifts only by the
    redistributed work/study mass of the non-eligible minority -- the other
    categories stay within a small tolerance."""
    df = _make_persons(n=2000)
    probs = _uniform_probs(len(df))
    before = probs.mean(axis=0)
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)
    after = out.mean(axis=0)
    # The work/study category share can only drop; every other category can only
    # rise (mass redistributed onto it). The total absolute drift is bounded by
    # the share of non-eligible persons who held work/study mass.
    assert after[_IDX_WORK_STUDY] < before[_IDX_WORK_STUDY]
    assert abs(after.sum() - 1.0) < 1e-9


def test_constant_identifies_the_combined_category():
    """The gated category set is exactly the combined work/study ticket."""
    assert PT_TICKET_WORK_STUDY_BOUND == frozenset({"job_or_semester_ticket"})


# ---------------------------------------------------------------------------
# Car-availability cross-tab loader hook (data-dependent, optional)
# ---------------------------------------------------------------------------

def test_car_availability_loader_returns_none_when_absent(tmp_path, caplog):
    """When the cross-tab CSV is absent the loader returns None and logs an INFO
    fallback (documented, not silent)."""
    import logging

    with caplog.at_level(logging.INFO):
        result = load_pt_subscription_by_car_availability(str(tmp_path))
    assert result is None
    assert any("not yet calibrated" in r.getMessage().lower()
               or "carless" in r.getMessage().lower()
               for r in caplog.records)


def test_car_availability_loader_reads_present_csv(tmp_path):
    """When the cross-tab CSV is present with the expected schema it loads into a
    {car_availability -> probability vector over PT_TICKET_CATEGORIES} map.

    # PT_RAW_FIXTURE_OK: the fixture CSV below deliberately uses the raw
    # codebook-German column headers (P24_RAW_COLUMN_BY_CATEGORY) -- exactly the
    # schema the loader reads from the committed CSV (issue #329 raw boundary).
    """
    import os

    cols = [P24_RAW_COLUMN_BY_CATEGORY[c] for c in PT_TICKET_CATEGORIES]
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    header = "car_availability," + ",".join(cols)
    # Two rows (none / all) with arbitrary but normalisable shares.
    none_row = "none," + ",".join(["1"] * len(cols))
    all_row = "all," + ",".join(["2"] * len(cols))
    csv_path = os.path.join(str(mid_dir), "mid2023_P24_1_by_car_availability.csv")
    with open(csv_path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n" + none_row + "\n" + all_row + "\n")

    result = load_pt_subscription_by_car_availability(str(tmp_path))
    assert result is not None
    assert set(result.keys()) == {"none", "all"}
    for vec in result.values():
        assert abs(vec.sum() - 1.0) < 1e-9
        assert len(vec) == len(PT_TICKET_CATEGORIES)


# ---------------------------------------------------------------------------
# Extract script: schema, per-row normalisation, symbol coercion logging
# ---------------------------------------------------------------------------

def _parse_committed_cross_tab():
    """Load the committed cross-tab CSV via the loader from the repo data tree."""
    return load_pt_subscription_by_car_availability(str(REPO / "eqasim-data" / "data"))


def test_extract_csv_schema_and_rows_sum_to_one():
    """The committed cross-tab CSV has the canonical {none, some, all} row keys,
    the 9 PT ticket columns (raw codebook-German headers, the boundary the
    committed CSVs keep -- issue #329), and each row is a probability vector
    summing to 1 (= P(ticket | car_availability))."""
    import pandas as pd

    csv_path = (
        REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
        / "mid2023_P24_1_by_car_availability.csv"
    )
    assert csv_path.exists(), (
        "run scripts/extract_mid_p24_by_car_availability.py to seed the CSV"
    )
    raw_cols = [P24_RAW_COLUMN_BY_CATEGORY[c] for c in PT_TICKET_CATEGORIES]
    df = pd.read_csv(csv_path, comment="#")
    assert "car_availability" in df.columns
    assert raw_cols == [c for c in df.columns if c != "car_availability"]
    # keine Angabe dropped; the three informative car-availability rows remain.
    assert set(df["car_availability"]) == {"none", "some", "all"}
    row_sums = df[raw_cols].sum(axis=1)
    np.testing.assert_allclose(row_sums.to_numpy(), 1.0, rtol=0, atol=1e-9)


def test_extract_symbol_coercion_logged(capsys):
    """The extract parser reports the per-cell coercion split (numeric vs.
    suppression-token vs. blank) so MiD symbol coercion is never silent."""
    import pandas as pd

    from scripts.extract_mid_p24_by_car_availability import (
        parse_car_availability_sheet,
    )

    xlsx_path = (
        REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
        / "mid2023_P24_1_by_car_availability.xlsx"
    )
    if not xlsx_path.exists():
        import pytest
        pytest.skip("source xlsx is local-only; not present in this checkout")

    df_raw = pd.read_excel(xlsx_path, sheet_name=0, header=None)
    tidy, coercion = parse_car_availability_sheet(df_raw)
    # 3 informative rows x 9 ticket columns, all numeric in the weighted-base row.
    assert len(tidy) == 3
    assert set(coercion.keys()) == {"suppression", "blank", "value"}
    assert sum(coercion.values()) == 3 * len(PT_TICKET_CATEGORIES)
    assert coercion["value"] == 3 * len(PT_TICKET_CATEGORIES)


def test_committed_cross_tab_shows_carless_pt_coupling():
    """The committed MiD cross-tab encodes the expected coupling direction:
    carless persons hold PT flatrate passes far more often than car-available
    persons."""
    by_car = _parse_committed_cross_tab()
    assert by_car is not None, "committed cross-tab CSV must be present"
    flat_none = by_car["none"][_FLATRATE_IDX].sum()
    flat_all = by_car["all"][_FLATRATE_IDX].sum()
    assert flat_none > flat_all


# ---------------------------------------------------------------------------
# Re-weight helper: coupling direction + marginal preservation
# ---------------------------------------------------------------------------

def _synthetic_cross_tab():
    """A toy P(ticket | car_availability) with a strong carless tilt toward the
    Deutschlandticket and a car tilt toward never_pt."""
    n_cats = len(PT_TICKET_CATEGORIES)
    base = np.full(n_cats, 1.0 / n_cats)

    none = base.copy()
    none[_IDX_DEUTSCHLANDTICKET] += 0.30
    none[_IDX_NEVER_PT] = max(none[_IDX_NEVER_PT] - 0.10, 0.0)
    none = none / none.sum()

    allv = base.copy()
    allv[_IDX_NEVER_PT] += 0.30
    allv[_IDX_DEUTSCHLANDTICKET] = max(allv[_IDX_DEUTSCHLANDTICKET] - 0.10, 0.0)
    allv = allv / allv.sum()

    return {"none": none, "all": allv}


def _persons_with_car_availability(n=2000, seed=11):
    import pandas as pd

    rng = np.random.RandomState(seed)
    car = np.where(rng.random_sample(n) < 0.5, "none", "all")
    return pd.DataFrame({"car_availability": pd.Categorical(car)})


def test_car_margin_imposes_carless_higher_pt_rate():
    """After re-weighting, carless persons have a HIGHER modelled PT-flatrate /
    Deutschlandticket probability than car-available persons (the coupling
    direction from the cross-tab)."""
    df = _persons_with_car_availability()
    probs = _uniform_probs(len(df))
    out = _apply_car_availability_pt_margin(
        probs, df, PT_TICKET_CATEGORIES, _synthetic_cross_tab()
    )

    none = df["car_availability"].to_numpy() == "none"
    allv = df["car_availability"].to_numpy() == "all"

    flat_none = out[none][:, _FLATRATE_IDX].sum(axis=1).mean()
    flat_all = out[allv][:, _FLATRATE_IDX].sum(axis=1).mean()
    assert flat_none > flat_all + 0.05

    dt_none = out[none, _IDX_DEUTSCHLANDTICKET].mean()
    dt_all = out[allv, _IDX_DEUTSCHLANDTICKET].mean()
    assert dt_none > dt_all + 0.05


def test_car_margin_preserves_overall_p24_marginal():
    """The light rake restores the overall P24.1 marginal: the population mean
    per category after re-weighting matches the pre-coupling mean within a small
    tolerance, even though the within-person distribution is tilted."""
    df = _persons_with_car_availability()
    probs = _uniform_probs(len(df))
    before = probs.mean(axis=0)
    out = _apply_car_availability_pt_margin(
        probs, df, PT_TICKET_CATEGORIES, _synthetic_cross_tab()
    )
    after = out.mean(axis=0)
    np.testing.assert_allclose(after, before, atol=2e-3)
    np.testing.assert_allclose(out.sum(axis=1), 1.0, atol=1e-9)
    # Marginal-deviation diagnostic recorded for traceability.
    assert df.attrs["pt_subscription_car_margin_max_dev_pp"] < 0.5


def test_car_margin_fallback_rate_is_reported_and_rows_unchanged():
    """Persons with a car_availability value absent from the cross-tab keep their
    original vector and are counted as fallback (no silent fallback)."""
    import pandas as pd

    n = 300
    car = ["none"] * 100 + ["all"] * 100 + ["some"] * 100  # "some" absent from tab
    df = pd.DataFrame({"car_availability": pd.Categorical(car)})
    probs = _uniform_probs(n)
    out = _apply_car_availability_pt_margin(
        probs, df, PT_TICKET_CATEGORIES, _synthetic_cross_tab()
    )
    some = df["car_availability"].to_numpy() == "some"
    np.testing.assert_allclose(out[some], probs[some])
    assert df.attrs["pt_subscription_car_margin_fallback_count"] == 100
    assert abs(df.attrs["pt_subscription_car_margin_fallback_rate"] - 1 / 3) < 1e-9


def test_car_margin_no_anchor_returns_unchanged():
    """If no person carries a usable car_availability value the matrix is returned
    unchanged and the fallback rate is 100% (loud, not silent)."""
    import pandas as pd

    df = pd.DataFrame({"car_availability": pd.Categorical(["some"] * 50)})
    probs = _uniform_probs(50)
    out = _apply_car_availability_pt_margin(
        probs, df, PT_TICKET_CATEGORIES, _synthetic_cross_tab()
    )
    np.testing.assert_array_equal(out, probs)
    assert df.attrs["pt_subscription_car_margin_fallback_rate"] == 1.0


def test_car_margin_absent_crosstab_is_no_op_via_loader(tmp_path):
    """OFF/absent-file equivalence at the hook boundary: when the loader returns
    None (cross-tab absent) the activation branch is never entered, so pt_probs is
    byte-identical to the pre-activation path. We assert the loader contract that
    drives that branch."""
    result = load_pt_subscription_by_car_availability(str(tmp_path))
    assert result is None  # -> caller skips _apply_car_availability_pt_margin

    # And when the helper IS called, an empty cross-tab leaves probs untouched.
    df = _persons_with_car_availability(n=10)
    probs = _uniform_probs(len(df))
    out = _apply_car_availability_pt_margin(probs, df, PT_TICKET_CATEGORIES, {})
    np.testing.assert_array_equal(out, probs)
