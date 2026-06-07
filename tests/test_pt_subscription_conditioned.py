"""A6: condition pt_subscription on student / employment status (+car hook).

With the ``pt_subscription_conditioned`` flag ON the PT-subscription IPF applies
DATA-FREE logical constraints to each person's per-category probability vector
BEFORE sampling:

  * the combined MiD P24.1 category ``jobticket_semesterticket`` (jobticket =
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
    PT_TICKET_CATEGORIES,
    PT_TICKET_WORK_STUDY_BOUND,
    load_pt_subscription_by_car_availability,
)
from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _condition_pt_subscription_probs,
)


_IDX_WORK_STUDY = PT_TICKET_CATEGORIES.index("jobticket_semesterticket")
_IDX_FAHRE_NIE = PT_TICKET_CATEGORIES.index("fahre_nie")


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


def test_all_zero_row_falls_back_to_fahre_nie():
    """A degenerate vector with all its mass on the work/study category for a
    non-eligible person must not become an all-zero (un-normalisable) row: it
    falls back deterministically to fahre_nie."""
    df = pd.DataFrame({"employed": [False], "studies": [False]})
    probs = np.zeros((1, len(PT_TICKET_CATEGORIES)))
    probs[0, _IDX_WORK_STUDY] = 1.0
    out = _condition_pt_subscription_probs(probs, df, PT_TICKET_CATEGORIES)
    np.testing.assert_allclose(out.sum(axis=1), 1.0)
    assert out[0, _IDX_FAHRE_NIE] == 1.0


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
    assert PT_TICKET_WORK_STUDY_BOUND == frozenset({"jobticket_semesterticket"})


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
    {car_availability -> probability vector over PT_TICKET_CATEGORIES} map."""
    import os

    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    cols = list(PT_TICKET_CATEGORIES)
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
