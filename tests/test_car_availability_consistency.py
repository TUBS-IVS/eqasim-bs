"""A5: consistent car_availability (causal IPF chain).

With the ``consistent_car_availability`` flag ON the enrichment stage derives
``car_availability`` in {none, some, all} CONDITIONALLY on the per-person MiD
licence and the MiD-H7 household car count, then rakes the residual to the MiD
P19 "jederzeit" (car available at any time) marginal so that aggregate is still
matched. This replaces the legacy free P19 IPF + all/none binarisation, which
could give car_availability="all" to a person in a 0-car household or without a
licence.

These tests pin the consistency invariants and the P19-marginal match on a
synthetic population, driving :func:`_derive_car_availability_consistent`
directly (the per-stage helper extracted for the causal chain).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _derive_car_availability_consistent,
    CAR_AVAILABILITY_CATEGORIES,
)


_KREIS_A = "braunschweig"
_KREIS_B = "gifhorn"


def _make_mid(target_a: float = 0.55, target_b: float = 0.45) -> dict:
    """Minimal ``mid`` stage object: only the P19 per-zone 'jederzeit' targets
    are read by the A5 derivation."""
    return {
        "car_availability_constraints": [
            {"zone": _KREIS_A, "target": target_a},
            {"zone": _KREIS_B, "target": target_b},
        ]
    }


def _make_persons(n: int = 2000, seed: int = 13) -> pd.DataFrame:
    """Synthetic population with households, ages, licences and household car
    counts spanning the 0-car / no-licence / shared-car / surplus-car cases."""
    rng = np.random.RandomState(seed)
    n_hh = n // 3
    household_id = rng.randint(0, n_hh, size=n)
    # Household car count drawn per household, then broadcast to persons.
    cars_per_hh = rng.choice([0, 1, 2, 3], size=n_hh, p=[0.15, 0.45, 0.30, 0.10])
    number_of_cars = cars_per_hh[household_id]

    age = rng.randint(0, 90, size=n)
    has_license = (age >= 18) & (rng.random_sample(n) < 0.8)

    inside_a = rng.random_sample(n) < 0.5
    df = pd.DataFrame(
        {
            "household_id": household_id,
            "age": age,
            "has_license": has_license,
            "number_of_cars": number_of_cars,
            "inside_braunschweig": inside_a,
            "inside_gifhorn": ~inside_a,
        }
    )
    return df


def _derive(df, mid, seed=42, minimum_age_car=0):
    return _derive_car_availability_consistent(df, mid, seed, minimum_age_car)


# ---------------------------------------------------------------------------
# Consistency invariants
# ---------------------------------------------------------------------------

def test_zero_car_household_always_none():
    """No person in a 0-car household has car_availability != 'none'."""
    df = _make_persons()
    df = _derive(df, _make_mid())
    zero_car = df["number_of_cars"] == 0
    assert (df.loc[zero_car, "car_availability"] == "none").all()


def test_no_licence_always_none():
    """No person without a driving licence has car_availability != 'none'."""
    df = _make_persons()
    df = _derive(df, _make_mid())
    no_licence = ~df["has_license"]
    assert (df.loc[no_licence, "car_availability"] == "none").all()


def test_surplus_cars_give_all_for_licensed_adults():
    """Households with number_of_cars >= number_of_licensed_adults -> the
    licensed adults get 'all' (before the optional P19 demotion). We test the
    pure conditional rule with P19 targets set to 1.0 so no demotion occurs."""
    df = _make_persons()
    # Targets at 1.0 -> the rake never demotes "all" to "some".
    df = _derive(df, _make_mid(target_a=1.0, target_b=1.0))

    licensed_by_hh = (
        df.groupby("household_id")["has_license"].sum()
    )
    cars_by_hh = df.groupby("household_id")["number_of_cars"].max()
    surplus_hh = cars_by_hh[(cars_by_hh > 0) & (cars_by_hh >= licensed_by_hh)].index

    f = df["household_id"].isin(surplus_hh) & df["has_license"]
    assert (df.loc[f, "car_availability"] == "all").all()


def test_some_category_present():
    """The 'some' category exists (intra-household competition), not just
    all/none -- households with more licensed adults than cars."""
    df = _make_persons()
    df = _derive(df, _make_mid())
    counts = df["car_availability"].value_counts()
    assert counts.get("some", 0) > 0
    assert counts.get("all", 0) > 0
    assert counts.get("none", 0) > 0


def test_result_categories_are_canonical():
    df = _make_persons()
    df = _derive(df, _make_mid())
    assert set(df["car_availability"].cat.categories) == set(CAR_AVAILABILITY_CATEGORIES)
    assert df["car_availability"].notna().all()


# ---------------------------------------------------------------------------
# P19 marginal match
# ---------------------------------------------------------------------------

def test_p19_jederzeit_marginal_matched_within_tolerance():
    """After the rake, the 'all' (car available at any time) share per Kreis
    matches the P19 'jederzeit' target within tolerance, given the eligible pool
    is large enough to reach it."""
    df = _make_persons(n=6000)
    target_a, target_b = 0.50, 0.40
    df = _derive(df, _make_mid(target_a=target_a, target_b=target_b))

    for flag, target in [("inside_braunschweig", target_a),
                         ("inside_gifhorn", target_b)]:
        in_zone = df[flag]
        share_all = (df.loc[in_zone, "car_availability"] == "all").mean()
        # The eligible pool (licensed + car-owning) bounds the achievable share;
        # with the synthetic distribution it comfortably exceeds the target, so
        # the rake should hit it within a small rounding tolerance.
        assert abs(share_all - target) < 0.02, (
            f"{flag}: share_all={share_all:.3f} target={target:.3f}"
        )


def test_number_of_cars_made_household_consistent():
    """A5 makes number_of_cars household-constant (max within household) so the
    downstream household table (drop_duplicates) is coherent."""
    df = _make_persons()
    df = _derive(df, _make_mid())
    per_hh_unique = df.groupby("household_id")["number_of_cars"].nunique()
    assert (per_hh_unique == 1).all()


def test_fallback_logged_when_target_unreachable():
    """When the P19 target exceeds the eligible pool, the shortfall is recorded
    as a fallback (kept conditional) and counted -- no silent failure."""
    df = _make_persons(n=2000)
    # Force an unreachable target: 1.0 'all' in a zone where many persons are
    # floored to 'none' (carless / unlicensed) is impossible.
    df = _derive(df, _make_mid(target_a=1.0, target_b=1.0))
    assert "car_availability_rake_fallback_count" in df.attrs
    assert df.attrs["car_availability_rake_fallback_count"] > 0
