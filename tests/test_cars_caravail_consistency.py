"""Cross-feature CAUSAL-ORDER consistency: income-aware number_of_cars (cars
income-aware, F-feature) x consistent car_availability (A5).

THE BUG (fixed by this commit): the enrichment stage historically derived
``car_availability`` (A5) from the LEGACY per-Kreis MiD-H7 ``number_of_cars``
BEFORE the income-aware draw OVERWROTE ``number_of_cars``. The per-Kreis H7
marginal is preserved by the income-aware rake, but WHICH households are 0-car
shifts by income, so some households ended up with ``car_availability != "none"``
while their FINAL ``number_of_cars == 0`` (and licensed-adults-vs-cars
mismatched) -- a violation of the A5 consistency guarantee.

THE FIX: the A5 derivation (and the A6 PT block that conditions on
car_availability) now run AFTER ``_sample_cars_income_aware``, so A5 sees the
FINAL income-aware car count. This test drives the helper sequence in the
fixed order and pins:

  * the A5 invariant on the FINAL cars: no household with final number_of_cars
    == 0 has car_availability != "none", and no unlicensed person does;
  * the per-Kreis number_of_cars marginal still equals the MiD-H7 control after
    the income-aware rake;
  * for the THREE non-(ON,ON) flag combinations the moved A5/PT call ORDER reads
    the SAME inputs -> byte-identical car_availability + number_of_cars vs the
    legacy order (OFF cars_income_aware -> cars unchanged; OFF A5 ->
    car_availability is the legacy binarisation, independent of the cars draw).

The income-aware draw is driven directly from the committed MiD CSVs under
``eqasim-data/data/braunschweig/mid/`` (no Python literals); the test skips when
those local-only CSVs are absent.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

DATA_PATH = os.path.join(str(REPO), "eqasim-data", "data")
_MID_DIR = os.path.join(DATA_PATH, "braunschweig", "mid")

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _sample_vehicle_counts,
    _binarise_availability,
    _sample_cars_income_aware,
    _derive_car_availability_consistent,
    CAR_AVAILABILITY_CATEGORIES,
)


pytestmark = pytest.mark.skipif(
    not (
        os.path.exists(os.path.join(_MID_DIR, "mid2023_cars_by_status_hhtype.csv"))
        and os.path.exists(os.path.join(_MID_DIR, "mid2023_cars_by_raumtyp.csv"))
        and os.path.exists(os.path.join(_MID_DIR, "mid2023_H7_cars_by_kreis.csv"))
    ),
    reason="local-only MiD cars / H7 CSVs not present",
)


# In-scope ARS-5 Kreis codes present in the committed H7 / cars CSVs and the
# matching inside_<kreis> zone names used by _derive_kreis_ars5 / A5.
_KREIS_A_ARS5 = "03101"  # braunschweig
_KREIS_B_ARS5 = "03151"  # gifhorn
_ZONE_A = "braunschweig"
_ZONE_B = "gifhorn"


def _make_persons(n: int = 3000, seed: int = 17) -> pd.DataFrame:
    """Synthetic enriched-stage frame just before the vehicle / availability /
    income-aware blocks: fractional IPF availability weights, inside_<kreis>
    flags, ages, sexes, licence, household_id and a 5-class economic_status
    (the input the income-aware cars draw couples on)."""
    rng = np.random.RandomState(seed)
    n_hh = n // 3
    household_id = rng.randint(0, n_hh, size=n)
    inside_a = rng.random_sample(n) < 0.5
    age = rng.randint(0, 90, size=n)

    # economic_status is a HOUSEHOLD attribute (drawn per household, broadcast).
    status_levels = np.array(
        ["very_low", "low", "medium", "high", "very_high"], dtype=object
    )
    status_per_hh = rng.choice(status_levels, size=n_hh, p=[0.15, 0.25, 0.3, 0.2, 0.1])

    df = pd.DataFrame(
        {
            "household_id": household_id,
            "age": age,
            "sex": np.where(rng.random_sample(n) < 0.5, "male", "female"),
            "has_license": (age >= 18) & (rng.random_sample(n) < 0.8),
            # Fractional IPF availability weights in [0, 1] (P19 / P22 output).
            "car_availability": rng.random_sample(n),
            "bicycle_availability": rng.random_sample(n),
            "economic_status": status_per_hh[household_id],
            "inside_{}".format(_ZONE_A): inside_a,
            "inside_{}".format(_ZONE_B): ~inside_a,
        }
    )
    return df


def _make_mid(target_a: float = 0.55, target_b: float = 0.45) -> dict:
    """Minimal ``mid`` object: only the P19 per-zone 'jederzeit' targets are read
    by the A5 derivation."""
    return {
        "car_availability_constraints": [
            {"zone": _ZONE_A, "target": target_a},
            {"zone": _ZONE_B, "target": target_b},
        ]
    }


def _make_regiostar() -> pd.DataFrame:
    """Empty RegioStaR table -> no raumtyp tilt (the cars draw falls back to the
    national/base pmf, which is still raked to the per-Kreis H7 control). The
    test fixture carries no commune_id, so the raumtyp key is never resolved
    regardless; this keeps the synthetic draw deterministic and H7-exact."""
    return pd.DataFrame({"commune_id": pd.Series([], dtype=str),
                         "regiostar7": pd.Series([], dtype="Int64")})


def _run_fixed_order(df, mid, seed, a5_on, cars_on, minimum_age_car=0):
    """Replicate the FIXED enrichment order for the A5 / cars-income-aware
    helpers: vehicle counts -> binarise -> income-aware cars -> A5 derive."""
    _sample_vehicle_counts(df, DATA_PATH, seed)
    if a5_on:
        # A5 ON: car uniform consumed (apply_car=False), fractional weights kept.
        _binarise_availability(df, seed, apply_car=False)
    else:
        _binarise_availability(df, seed, apply_car=True)
    if cars_on:
        df = _sample_cars_income_aware(df, DATA_PATH, seed, _make_regiostar())
    if a5_on:
        _derive_car_availability_consistent(df, mid, seed, minimum_age_car)
    return df


def _run_legacy_order(df, mid, seed, a5_on, cars_on, minimum_age_car=0):
    """Replicate the LEGACY (pre-fix) enrichment order: vehicle counts ->
    binarise -> A5 derive (on legacy cars) -> income-aware cars."""
    _sample_vehicle_counts(df, DATA_PATH, seed)
    if a5_on:
        _binarise_availability(df, seed, apply_car=False)
    else:
        _binarise_availability(df, seed, apply_car=True)
    if a5_on:
        _derive_car_availability_consistent(df, mid, seed, minimum_age_car)
    if cars_on:
        df = _sample_cars_income_aware(df, DATA_PATH, seed, _make_regiostar())
    return df


# ---------------------------------------------------------------------------
# (ON, ON): the A5 invariant must hold on the FINAL income-aware car count
# ---------------------------------------------------------------------------

def test_no_caravail_on_zero_final_cars_with_both_features():
    """consistent_car_availability=True AND cars_income_aware=True: NO household
    with FINAL number_of_cars == 0 may have car_availability != 'none'."""
    df = _make_persons()
    df = _run_fixed_order(df, _make_mid(), seed=2024, a5_on=True, cars_on=True)

    zero_car = df["number_of_cars"] == 0
    assert zero_car.any(), "fixture should contain 0-car households"
    assert (df.loc[zero_car, "car_availability"] == "none").all(), (
        "household with final number_of_cars == 0 has car_availability != none"
    )


def test_no_unlicensed_caravail_with_both_features():
    """No unlicensed person may have car_availability != 'none' (A5 hard floor on
    the FINAL state)."""
    df = _make_persons()
    df = _run_fixed_order(df, _make_mid(), seed=2024, a5_on=True, cars_on=True)
    no_licence = ~df["has_license"]
    assert (df.loc[no_licence, "car_availability"] == "none").all()


def test_fixed_order_removes_the_inconsistency_legacy_had():
    """Quantify the bug: in the LEGACY order (A5 on legacy cars, income-aware
    overwrite AFTER) some households end up car_availability != 'none' while the
    FINAL number_of_cars == 0. The FIXED order drives that count to 0."""
    seed = 2024
    mid = _make_mid()

    legacy = _run_legacy_order(_make_persons(), mid, seed, a5_on=True, cars_on=True)
    zero_car_legacy = legacy["number_of_cars"] == 0
    n_bad_legacy = int(
        (legacy.loc[zero_car_legacy, "car_availability"] != "none").sum()
    )

    fixed = _run_fixed_order(_make_persons(), mid, seed, a5_on=True, cars_on=True)
    zero_car_fixed = fixed["number_of_cars"] == 0
    n_bad_fixed = int(
        (fixed.loc[zero_car_fixed, "car_availability"] != "none").sum()
    )

    # The fix must eliminate the inconsistency, and the bug must have been real
    # (otherwise the test would not guard anything).
    assert n_bad_fixed == 0, f"fixed order still inconsistent: {n_bad_fixed}"
    assert n_bad_legacy > 0, (
        "legacy order was already consistent on this fixture -> the test does not "
        "exercise the bug; strengthen the fixture"
    )
    # Surface the numbers for the report.
    print(
        f"[test] inconsistent 0-car households with car_availability != none: "
        f"legacy={n_bad_legacy}, fixed={n_bad_fixed}"
    )


def test_caravail_categories_canonical_with_both_features():
    df = _make_persons()
    df = _run_fixed_order(df, _make_mid(), seed=2024, a5_on=True, cars_on=True)
    assert set(df["car_availability"].cat.categories) == set(CAR_AVAILABILITY_CATEGORIES)
    assert df["car_availability"].notna().all()


# ---------------------------------------------------------------------------
# Per-Kreis H7 marginal preserved by the income-aware rake (FINAL cars)
# ---------------------------------------------------------------------------

def test_per_kreis_cars_marginal_equals_h7_control():
    """After the income-aware draw the per-Kreis FINAL number_of_cars household
    distribution equals the MiD-H7 control counts (the rake invariant). Checked
    per household (one count per household_id), as the rake assigns at household
    level."""
    from braunschweig.synthesis.population.enriched import (
        load_kreis_share_table,
        _largest_remainder,
        _derive_kreis_ars5,
    )

    df = _make_persons()
    df = _run_fixed_order(df, _make_mid(), seed=2024, a5_on=True, cars_on=True)

    cars_by_kreis, cars_region, _ = load_kreis_share_table(
        DATA_PATH, "mid2023_H7_cars_by_kreis.csv"
    )

    # One car count per household + that household's Kreis.
    kreis_person = _derive_kreis_ars5(df)
    hh = pd.DataFrame(
        {
            "household_id": df["household_id"].to_numpy(),
            "number_of_cars": df["number_of_cars"].to_numpy(),
            "kreis": kreis_person.to_numpy(),
        }
    ).drop_duplicates("household_id")

    cats = [0, 1, 2, 3]
    for ars in (_KREIS_A_ARS5, _KREIS_B_ARS5):
        sub = hh[hh["kreis"] == ars]
        n_hh = len(sub)
        if n_hh == 0:
            continue
        shares = cars_by_kreis.get(ars, cars_region)
        target = _largest_remainder(shares, n_hh)  # integer counts, sum = n_hh
        realised = np.array([(sub["number_of_cars"] == c).sum() for c in cats])
        np.testing.assert_array_equal(
            realised, target,
            err_msg=f"Kreis {ars}: realised car counts {realised} != H7 target {target}",
        )


# ---------------------------------------------------------------------------
# Byte-identity of the THREE non-(ON, ON) combinations vs the legacy order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "a5_on,cars_on",
    [(True, False), (False, True), (False, False)],
    ids=["A5on_carsOFF", "A5off_carsON", "A5off_carsOFF"],
)
def test_non_on_on_combos_byte_identical_to_legacy_order(a5_on, cars_on):
    """For the three non-(ON, ON) flag combinations the moved A5/PT call ORDER
    reads the SAME inputs as the legacy order, so car_availability +
    number_of_cars are byte-identical:

      * OFF cars_income_aware -> number_of_cars unchanged after A5's old slot, so
        A5 sees the same legacy cars whether it runs before or after the (absent)
        income-aware draw;
      * OFF A5 -> car_availability is the legacy {none, all} binarisation set in
        the vehicle block, independent of the cars draw and its position.
    """
    seed = 4242
    mid = _make_mid()

    fixed = _run_fixed_order(_make_persons(), mid, seed, a5_on=a5_on, cars_on=cars_on)
    legacy = _run_legacy_order(_make_persons(), mid, seed, a5_on=a5_on, cars_on=cars_on)

    for col in ("car_availability", "number_of_cars"):
        pd.testing.assert_series_equal(
            legacy[col].reset_index(drop=True),
            fixed[col].reset_index(drop=True),
            check_names=True,
            obj=f"{col} (legacy order vs fixed order; A5={a5_on}, cars={cars_on})",
        )
