"""Issue #133: joint (cross-attribute) plausibility checks.

Both validation registries check only univariate margins -- a population can
hit every marginal and still contain individually impossible records. Exactly
this bug class (#96 field-width collision, couple/studies constants,
consistent_car_availability) was previously only found MANUALLY. The checks
here are hard logical invariants (not rare-but-possible combinations), run at
the end of the popsim stage, logged with counts/rates, and raise only above an
explicitly passed threshold (measure-before-harden, like the minor-employment
guard in PR #102 which keeps watching the under-15 employed RATE).
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import plausibility as pl


def _clean_persons() -> pd.DataFrame:
    return pd.DataFrame({
        "age":                  [40, 10, 17, 70],
        "employed":             [True, False, False, False],
        "has_license":          [True, False, True, True],
        "number_of_cars":       [1, 1, 0, 0],
        "car_availability":     ["all", "some", "none", "none"],
        "household_size":       [2, 2, 1, 1],
        "has_pt_subscription":  [True, False, False, False],
        "pt_subscription_type": ["monat_abo_jahreskarte", "fahre_nie", "fahre_nie", "fahre_nie"],
    })


def test_clean_population_has_zero_violations() -> None:
    report = pl.check_joint_plausibility(_clean_persons())
    assert report["n_violations_total"] == 0
    assert all(c["n_violations"] == 0 for c in report["checks"].values())


def test_each_hard_invariant_is_detected() -> None:
    persons = _clean_persons()
    persons.loc[0, "car_availability"] = "none"      # cars=1 but availability none
    persons.loc[1, "employed"] = True                 # employed child (age 10)
    persons.loc[1, "has_license"] = True              # licence at age 10
    persons.loc[2, "has_pt_subscription"] = True      # subscription + fahre_nie
    report = pl.check_joint_plausibility(persons)
    checks = report["checks"]
    assert checks["car_availability_mismatch"]["n_violations"] == 1
    assert checks["employed_child"]["n_violations"] == 1
    assert checks["license_underage"]["n_violations"] == 1
    assert checks["pt_never_contradiction"]["n_violations"] == 1
    assert report["n_violations_total"] == 4


def test_couple_in_single_person_household_detected() -> None:
    persons = _clean_persons()
    persons["couple"] = [False, False, True, False]   # index 2: household_size == 1
    report = pl.check_joint_plausibility(persons)
    assert report["checks"]["couple_single"]["n_violations"] == 1


def test_missing_columns_skip_check_gracefully() -> None:
    persons = _clean_persons().drop(columns=["pt_subscription_type"])
    report = pl.check_joint_plausibility(persons)
    assert "pt_never_contradiction" in report["skipped"]
    # 'couple' is absent from the popsim frame entirely -> also skipped.
    assert "couple_single" in report["skipped"]


def test_raise_above_threshold() -> None:
    persons = _clean_persons()
    persons["car_availability"] = "none"              # rows with cars now violate
    with pytest.raises(ValueError, match="joint plausibility"):
        pl.check_joint_plausibility(persons, raise_above_rate=0.1)


def test_no_raise_when_threshold_none() -> None:
    persons = _clean_persons()
    persons["car_availability"] = "none"
    report = pl.check_joint_plausibility(persons, raise_above_rate=None)
    assert report["n_violations_total"] > 0
