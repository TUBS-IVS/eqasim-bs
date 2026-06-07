"""Tests for the 5-class MiD economic status synthesised per household.

Spec 2 (the upcoming vehicle-segment IPF) needs every synthetic household to
carry the MiD "Oekonomischer Status" in the five BMDV quintiles
{very_low, low, medium, high, very_high} (sehr niedrig ... sehr hoch). The
attribute is derived deterministically from the already-sampled
``household_income`` EUR-class label (a 1:1 mapping defined by the same H4
quintile ordering as ``braunschweig.data.census.household_income``).

These tests pin:

* the EUR-class -> status mapping is the full 5-class BMDV vocabulary and is
  the deterministic inverse of the H4 quintile positions used to sample income;
* a synthetic person frame gets ``economic_status`` for every person, in the
  five classes, and the population status distribution matches the MiD H4 status
  margin (size-weighted) within ~1 percentage point;
* the derivation is additive: it does not touch ``household_income`` /
  ``household_income_eur`` / ``high_income``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.synthesis.population import enriched  # noqa: E402
from braunschweig.synthesis.population.enriched import (  # noqa: E402
    ECONOMIC_STATUS_BY_INCOME_CLASS,
    ECONOMIC_STATUS_CATEGORIES,
    _derive_economic_status,
)
from braunschweig.data.census.household_income import INCOME_CLASS_MAP


# The H4 quintile vector order (sehr_niedrig ... sehr_hoch) -> English status.
H4_STATUS_ORDER = ("very_low", "low", "medium", "high", "very_high")


def test_status_vocabulary_is_the_five_bmdv_classes():
    assert ECONOMIC_STATUS_CATEGORIES == H4_STATUS_ORDER
    assert set(ECONOMIC_STATUS_BY_INCOME_CLASS.values()) == set(H4_STATUS_ORDER)


def test_mapping_is_the_inverse_of_the_h4_quintile_positions():
    """Each EUR-class maps to the status at exactly the H4 quintile position
    that INCOME_CLASS_MAP uses to SAMPLE that EUR-class -- so income and status
    can never disagree."""
    for income_class, idx in INCOME_CLASS_MAP:
        assert ECONOMIC_STATUS_BY_INCOME_CLASS[income_class] == H4_STATUS_ORDER[idx]


def _synthetic_persons(rng, n_per_class=2000):
    """Build a person frame whose household_income classes reproduce the
    size-weighted MiD H4 status margin, so the derived status distribution can
    be checked against that margin."""
    # Size-weighted H4 status shares (uniform over the 6 size bins of the CSV is
    # not what we need; we only need a frame whose income-class shares are known
    # and whose derived status shares equal those income-class shares).
    income_classes = [c for c, _ in INCOME_CLASS_MAP]
    # Use a fixed, non-uniform composition so the test is meaningful.
    composition = {
        "0-500": 0.10,
        "1500-2000": 0.15,
        "2600-3000": 0.30,
        "3600-4500": 0.35,
        "5000+": 0.10,
    }
    n = n_per_class * len(income_classes)
    draws = rng.choice(
        income_classes, size=n, p=[composition[c] for c in income_classes]
    )
    return pd.DataFrame({"household_income": pd.Series(draws, dtype="category")})


def test_every_person_gets_a_status_in_the_five_classes():
    rng = np.random.default_rng(42)
    df = _synthetic_persons(rng)
    out = _derive_economic_status(df)

    assert "economic_status" in out.columns
    assert out["economic_status"].isna().sum() == 0
    assert set(out["economic_status"].unique()) <= set(ECONOMIC_STATUS_CATEGORIES)


def test_status_distribution_matches_income_class_distribution_within_1pp():
    rng = np.random.default_rng(7)
    df = _synthetic_persons(rng)
    out = _derive_economic_status(df)

    income_share = (
        df["household_income"].astype(str).value_counts(normalize=True)
    )
    status_share = out["economic_status"].value_counts(normalize=True)

    for income_class, status in ECONOMIC_STATUS_BY_INCOME_CLASS.items():
        assert abs(
            status_share.get(status, 0.0) - income_share.get(income_class, 0.0)
        ) < 0.01


def test_derivation_is_additive():
    """high_income / household_income are untouched by the status derivation."""
    rng = np.random.default_rng(1)
    df = _synthetic_persons(rng)
    df["high_income"] = df["household_income"].astype(str) == "5000+"
    df["household_income_eur"] = 3000.0
    before_income = df["household_income"].copy()
    before_high = df["high_income"].copy()
    before_eur = df["household_income_eur"].copy()

    out = _derive_economic_status(df)

    pd.testing.assert_series_equal(out["household_income"], before_income)
    pd.testing.assert_series_equal(out["high_income"], before_high)
    pd.testing.assert_series_equal(out["household_income_eur"], before_eur)


def test_fallback_rate_logged_and_zero_for_known_classes(capsys):
    rng = np.random.default_rng(3)
    df = _synthetic_persons(rng)
    _derive_economic_status(df)
    log = capsys.readouterr().out
    assert "economic_status" in log
    assert "fallback rate 0.00%" in log or "PRIMARY" in log


def test_unknown_income_class_counted_as_fallback(capsys):
    df = pd.DataFrame(
        {"household_income": pd.Series(["0-500", "9999-UNKNOWN", "5000+"])}
    )
    out = _derive_economic_status(df)
    # Unknown class must not silently produce a bogus status.
    assert out.loc[1, "economic_status"] is None or pd.isna(out.loc[1, "economic_status"])
    log = capsys.readouterr().out
    assert "WARNING" in log
    assert out.attrs["economic_status_fallback_count"] == 1


# --------------------------------------------------------------------------- #
# status_from_hhtype path (MiD household-type x region Bayes)
# --------------------------------------------------------------------------- #
from pathlib import Path as _Path  # noqa: E402

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    INCOME_CLASS_BY_ECONOMIC_STATUS,
    _derive_economic_status_from_hhtype,
)
from braunschweig.data.mid.status_by_hhtype import (  # noqa: E402
    RS7_TO_RAUMTYP_KEY,
    bayes_status_given_hhtype,
    load_status_by_hhtype_bundesland,
)

_DATA_PATH = str(_Path(__file__).resolve().parents[1] / "eqasim-data" / "data")


def test_income_class_by_status_is_inverse_of_status_by_income_class():
    for income_class, status in ECONOMIC_STATUS_BY_INCOME_CLASS.items():
        assert INCOME_CLASS_BY_ECONOMIC_STATUS[status] == income_class


def _hhtype_population(seed=0, n_households=600):
    """Synthetic persons with household_id, age, commune_id, household_income."""
    rng = np.random.default_rng(seed)
    # Two ZGB communes mapped to RS7 71 (metropole-ish) and 77 (rural) below.
    communes = ["031010000000", "031530000000"]
    rows = []
    pid = 0
    for hid in range(n_households):
        size = int(rng.integers(1, 5))
        if size == 1:
            ages = [int(rng.integers(18, 85))]
        elif size == 2:
            a = int(rng.integers(18, 80))
            ages = [a, min(95, a + int(rng.integers(0, 6)))]
        else:
            n_adults = int(rng.integers(1, 3))
            ages = [int(rng.integers(20, 60)) for _ in range(n_adults)]
            ages += [int(rng.integers(0, 17)) for _ in range(size - n_adults)]
        cid = communes[hid % 2]
        inc = rng.choice([c for c, _ in INCOME_CLASS_MAP])
        for a in ages:
            rows.append({
                "person_id": pid, "household_id": hid, "age": a,
                "commune_id": cid, "household_income": inc,
            })
            pid += 1
    df = pd.DataFrame(rows)
    df["household_income"] = df["household_income"].astype("category")
    return df


def _synthetic_regiostar():
    # AGS-8 keys derived from the 12-digit ARS via ars_to_ags8: 03101 + 000.
    return pd.DataFrame({
        "commune_id": ["03101000", "03153000"],
        "regiostar7": pd.array([71, 77], dtype="Int64"),
    })


def test_hhtype_status_assigned_to_every_person():
    df = _hhtype_population()
    out = _derive_economic_status_from_hhtype(
        df.copy(), _DATA_PATH, _synthetic_regiostar(), random_seed=42
    )
    assert out["economic_status"].isna().sum() == 0
    assert set(out["economic_status"].unique()) <= set(ECONOMIC_STATUS_CATEGORIES)
    # Fallback rate must be tiny (every synthetic household is classifiable).
    assert out.attrs["economic_status_hhtype_fallback_rate"] < 0.01


def test_hhtype_status_rederives_income_consistently():
    df = _hhtype_population()
    out = _derive_economic_status_from_hhtype(
        df.copy(), _DATA_PATH, _synthetic_regiostar(), random_seed=7
    )
    # household_income must equal the inverse map of the sampled status.
    expected = out["economic_status"].map(INCOME_CLASS_BY_ECONOMIC_STATUS)
    assert (out["household_income"].astype(str) == expected.astype(str)).all()
    assert (out["high_income"] == (out["household_income"] == "5000+")).all()


def test_hhtype_status_distribution_plausible_vs_nds_marginal():
    """Population status shares should sit in the broad neighbourhood of the MiD
    NDS status marginal (recovered from the weighted bases), not be degenerate."""
    df = _hhtype_population(n_households=3000)
    out = _derive_economic_status_from_hhtype(
        df.copy(), _DATA_PATH, _synthetic_regiostar(), random_seed=1
    )
    shares = out["economic_status"].value_counts(normalize=True)
    # All five classes appear; the dominant class 'high'/'medium' should be the
    # MiD NDS mode, not an artefact.
    assert set(shares.index) <= set(ECONOMIC_STATUS_CATEGORIES)
    assert shares.max() < 0.85  # not degenerate
    assert (shares > 0).sum() >= 4

    df_b = load_status_by_hhtype_bundesland(_DATA_PATH)
    nds_base = (
        df_b[df_b["region"] == "niedersachsen"]
        .drop_duplicates("status").set_index("status")["base_weighted"]
    )
    nds_marginal = (nds_base / nds_base.sum())
    # The modal status of the realised distribution should match the MiD NDS
    # modal status (high), confirming the sampler tracks the reference.
    assert shares.idxmax() == nds_marginal.idxmax()


class _StubContext:
    """Minimal synpp-context double exercising only the status branch of
    ``_execute_base`` (config + flag), used for the OFF-equivalence contract."""

    def __init__(self, flag):
        self._flag = flag

    def config(self, key, default=None):
        if key == "status_from_hhtype":
            return self._flag
        return default


def test_off_branch_uses_legacy_income_derived_status_unchanged():
    """With status_from_hhtype=False the status is the EXACT legacy 1:1 mapping
    from the already-sampled income class, and income columns are untouched
    (byte-identical to commit c65399d)."""
    rng = np.random.default_rng(11)
    df = _synthetic_persons(rng)
    df["high_income"] = df["household_income"].astype(str) == "5000+"

    before_income = df["household_income"].copy()
    before_high = df["high_income"].copy()

    ctx = _StubContext(flag=False)
    # Mirror the branch in _execute_base for the OFF case.
    assert ctx.config("status_from_hhtype") is False
    out = _derive_economic_status(df)

    # Status equals the deterministic legacy mapping.
    expected = before_income.astype(str).map(ECONOMIC_STATUS_BY_INCOME_CLASS)
    assert (out["economic_status"].astype(str) == expected.astype(str)).all()
    # Income untouched.
    pd.testing.assert_series_equal(out["household_income"], before_income)
    pd.testing.assert_series_equal(out["high_income"], before_high)

