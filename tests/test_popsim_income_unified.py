"""Tests for the unified INKAR-scaled income derivation across popsim sources.

Phase 5g.5 — income unification:
  ``braunschweig.popsim.income.apply_inkar_income_eur`` is a shared helper that
  turns a per-person income-class column into:
    - ``household_income_eur`` = income_class_midpoint_EUR * INKAR_scale[Kreis]
    - ``high_income``           = (household_income_eur >= high_income_threshold)

The MiD source uses ``INCOME_GROUP_MIDPOINT_EUR`` (hheink_gr1 1..15 groups);
the ENTD source uses ``ENTD_INCOME_CLASS_MIDPOINT_EUR`` (income_class 0..13 bands).
Both pass through the SAME INKAR scaling + high_income rule.

DESIGN RATIONALE
----------------
The IPF / enriched path already applies INKAR scaling (see
``braunschweig.synthesis.population.enriched._apply_inkar_income_scale``):
    household_income_eur = income_class_midpoint_EUR * INKAR_scale[Kreis]
    (scale from braunschweig.data.inkar.household_income, ars5 -> scale)

The popsim paths previously diverged:
  - popsim_mid:  household_income_eur = midpoint (no INKAR); high_income = "over_7000"
  - popsim_open: household_income_eur ABSENT;               high_income = income_class>=13

This test module verifies the unified behaviour. The IPF/default income path
(braunschweig.synthesis.population.enriched) is NOT touched by this change.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.income import (
    apply_inkar_income_eur,
    ENTD_INCOME_CLASS_MIDPOINT_EUR,
    HIGH_INCOME_THRESHOLD_EUR,
)
from braunschweig.popsim.attributes import INCOME_GROUP_MIDPOINT_EUR


# ---------------------------------------------------------------------------
# Tiny helper fixtures
# ---------------------------------------------------------------------------

def _inkar_scale(ars5_to_scale: dict) -> pd.DataFrame:
    """Build a minimal INKAR scale frame from a dict {ars5: scale}."""
    data = [{"ars5": k, "einkommen_eur": 1000.0 * v, "scale": v}
            for k, v in ars5_to_scale.items()]
    return pd.DataFrame(data)


def _persons_mid(income_groups: list, departement_ids: list) -> pd.DataFrame:
    """Build a MiD-style persons frame with household_income (categorical)."""
    from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP
    return pd.DataFrame({
        "person_id":       list(range(len(income_groups))),
        "household_id":    list(range(len(income_groups))),
        "departement_id":  departement_ids,
        # household_income is the categorical label derived from hheink_gr1
        "household_income": [INCOME_CLASS_BY_GROUP[g] for g in income_groups],
        # income_class_eur_col carries the raw EUR midpoint from INCOME_GROUP_MIDPOINT_EUR
        "income_class_eur": [INCOME_GROUP_MIDPOINT_EUR[g] for g in income_groups],
    })


def _persons_entd(income_classes: list, departement_ids: list) -> pd.DataFrame:
    """Build an ENTD-style persons frame with income_class (integer 0..13)."""
    return pd.DataFrame({
        "person_id":      list(range(len(income_classes))),
        "household_id":   list(range(len(income_classes))),
        "departement_id": departement_ids,
        "income_class":   income_classes,
    })


# ---------------------------------------------------------------------------
# Test 1: INKAR scale is applied to midpoint
# ---------------------------------------------------------------------------

def test_inkar_income_scales_midpoint():
    """household_income_eur = midpoint * INKAR_scale[Kreis].

    Kreis 03101 has scale 1.2. MiD hheink_gr1=4 -> midpoint 1750.0 EUR.
    Expected: household_income_eur = 1750.0 * 1.2 = 2100.0 EUR.
    high_income = (2100.0 >= 5000.0) = False.
    """
    inkar = _inkar_scale({"03101": 1.2})
    midpoints = {1: 250.0, 4: 1750.0, 15: 8000.0}
    persons = pd.DataFrame({
        "person_id":      [0],
        "departement_id": ["03101"],
        "household_income_eur": [1750.0],  # pre-set midpoint (to be replaced by INKAR)
    })
    # Provide a midpoint Series aligned to persons index
    midpoint_series = pd.Series([1750.0], index=persons.index)

    out = apply_inkar_income_eur(persons, inkar, midpoint_series=midpoint_series)

    assert abs(out.loc[0, "household_income_eur"] - 1750.0 * 1.2) < 1.0, (
        f"Expected {1750.0 * 1.2}, got {out.loc[0, 'household_income_eur']}"
    )
    assert out.loc[0, "high_income"] == False


def test_inkar_high_income_scale():
    """MiD hheink_gr1=15 -> midpoint 8000.0; scale 1.1 -> 8800.0 -> high_income True."""
    inkar = _inkar_scale({"03101": 1.1})
    persons = pd.DataFrame({
        "person_id":      [0],
        "departement_id": ["03101"],
        "household_income_eur": [8000.0],
    })
    midpoint_series = pd.Series([8000.0], index=persons.index)

    out = apply_inkar_income_eur(persons, inkar, midpoint_series=midpoint_series)

    assert out.loc[0, "household_income_eur"] == round(8000.0 * 1.1, 0)
    assert out.loc[0, "high_income"] == True


# ---------------------------------------------------------------------------
# Test 2: high_income consistent numeric rule
# ---------------------------------------------------------------------------

def test_high_income_consistent_rule():
    """Two persons, EUR 4000 and 6000 -> high_income [False, True].

    The threshold is HIGH_INCOME_THRESHOLD_EUR = 5000.0 exactly.
    """
    inkar = _inkar_scale({"03101": 1.0})  # scale 1.0: midpoint unchanged
    persons = pd.DataFrame({
        "person_id":      [0, 1],
        "departement_id": ["03101", "03101"],
        "household_income_eur": [4000.0, 6000.0],
    })
    midpoint_series = pd.Series([4000.0, 6000.0], index=persons.index)

    out = apply_inkar_income_eur(persons, inkar, midpoint_series=midpoint_series)

    assert out.loc[0, "high_income"] == False, "4000 EUR should not be high_income"
    assert out.loc[1, "high_income"] == True,  "6000 EUR should be high_income"


def test_high_income_boundary_at_5000():
    """Boundary condition: exactly 5000.0 EUR -> high_income True (>=)."""
    inkar = _inkar_scale({"03101": 1.0})
    persons = pd.DataFrame({
        "person_id":      [0, 1],
        "departement_id": ["03101", "03101"],
        "household_income_eur": [4999.0, 5000.0],
    })
    midpoint_series = pd.Series([4999.0, 5000.0], index=persons.index)

    out = apply_inkar_income_eur(persons, inkar, midpoint_series=midpoint_series)

    assert out.loc[0, "high_income"] == False, "4999 EUR should not be high_income"
    assert out.loc[1, "high_income"] == True,  "5000 EUR should be high_income (>= threshold)"


# ---------------------------------------------------------------------------
# Test 3: missing Kreis falls back to scale 1.0 and is logged
# ---------------------------------------------------------------------------

def test_missing_kreis_falls_back_scale_one_and_logged(caplog):
    """A Kreis absent from INKAR falls back to scale=1.0 (midpoint unchanged).

    The fallback count must be logged (CLAUDE.md no-silent-fallback rule).
    """
    # INKAR has 03101 only; 99999 is absent
    inkar = _inkar_scale({"03101": 1.3})
    persons = pd.DataFrame({
        "person_id":      [0, 1],
        "departement_id": ["03101", "99999"],
        "household_income_eur": [2000.0, 2000.0],
    })
    midpoint_series = pd.Series([2000.0, 2000.0], index=persons.index)

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.income"):
        out = apply_inkar_income_eur(persons, inkar, midpoint_series=midpoint_series)

    # 03101 person: scaled by 1.3
    assert abs(out.loc[0, "household_income_eur"] - 2000.0 * 1.3) < 1.0, (
        f"03101 person expected {2000.0 * 1.3}, got {out.loc[0, 'household_income_eur']}"
    )
    # 99999 person: scale=1.0 fallback (midpoint unchanged)
    assert abs(out.loc[1, "household_income_eur"] - 2000.0 * 1.0) < 1.0, (
        f"99999 (absent) person expected {2000.0}, got {out.loc[1, 'household_income_eur']}"
    )
    # Fallback rate must be logged
    log_text = caplog.text
    assert "fallback" in log_text.lower() or "1/2" in log_text or "missing" in log_text.lower(), (
        f"Expected fallback rate logging, got: {log_text!r}"
    )


def test_missing_kreis_fallback_rate_in_attrs():
    """apply_inkar_income_eur stores primary/fallback counts in .attrs for test introspection."""
    inkar = _inkar_scale({"03101": 1.0})
    persons = pd.DataFrame({
        "person_id":      [0, 1, 2],
        "departement_id": ["03101", "03101", "99999"],
        "household_income_eur": [1000.0, 2000.0, 3000.0],
    })
    midpoint_series = pd.Series([1000.0, 2000.0, 3000.0], index=persons.index)

    out = apply_inkar_income_eur(persons, inkar, midpoint_series=midpoint_series)

    assert out.attrs.get("inkar_primary_count") == 2
    assert out.attrs.get("inkar_fallback_count") == 1
    assert abs(out.attrs.get("inkar_fallback_rate") - 1 / 3) < 0.01


# ---------------------------------------------------------------------------
# Test 4: both sources use the same high_income rule
# ---------------------------------------------------------------------------

def test_both_sources_use_same_high_income_rule():
    """MiD and ENTD persons at equivalent EUR levels must produce the same high_income.

    MiD: hheink_gr1=10 (4600-5000, midpoint 4800.0) -> high_income False (< 5000).
    MiD: hheink_gr1=11 (5000-5600, midpoint 5300.0) -> high_income True (>= 5000).
    ENTD: income_class=10 (3000-4000, midpoint 3500.0) -> high_income False.
    ENTD: income_class=11 (4000-6000, midpoint 5000.0) -> high_income True (>= 5000).

    Both are evaluated against the SAME threshold HIGH_INCOME_THRESHOLD_EUR=5000.
    """
    inkar = _inkar_scale({"03101": 1.0})

    # MiD case (group 10 = 4800 EUR; group 11 = 5300 EUR)
    mid_persons = pd.DataFrame({
        "person_id":      [0, 1],
        "departement_id": ["03101", "03101"],
        "household_income_eur": [0.0, 0.0],  # will be replaced
    })
    mid_midpoints = pd.Series(
        [INCOME_GROUP_MIDPOINT_EUR[10], INCOME_GROUP_MIDPOINT_EUR[11]],
        index=mid_persons.index,
    )
    mid_out = apply_inkar_income_eur(mid_persons, inkar, midpoint_series=mid_midpoints)

    assert mid_out.loc[0, "high_income"] == False, (
        f"MiD group 10 midpoint {INCOME_GROUP_MIDPOINT_EUR[10]} should not be high_income"
    )
    assert mid_out.loc[1, "high_income"] == True, (
        f"MiD group 11 midpoint {INCOME_GROUP_MIDPOINT_EUR[11]} should be high_income"
    )

    # ENTD case (class 10 = 3500 EUR; class 11 = 5000 EUR)
    entd_persons = pd.DataFrame({
        "person_id":      [0, 1],
        "departement_id": ["03101", "03101"],
        "household_income_eur": [0.0, 0.0],
    })
    entd_midpoints = pd.Series(
        [ENTD_INCOME_CLASS_MIDPOINT_EUR[10], ENTD_INCOME_CLASS_MIDPOINT_EUR[11]],
        index=entd_persons.index,
    )
    entd_out = apply_inkar_income_eur(entd_persons, inkar, midpoint_series=entd_midpoints)

    assert entd_out.loc[0, "high_income"] == False, (
        f"ENTD class 10 midpoint {ENTD_INCOME_CLASS_MIDPOINT_EUR[10]} should not be high_income"
    )
    assert entd_out.loc[1, "high_income"] == True, (
        f"ENTD class 11 midpoint {ENTD_INCOME_CLASS_MIDPOINT_EUR[11]} should be high_income"
    )


# ---------------------------------------------------------------------------
# Test 5: ENTD income class midpoints are well-formed
# ---------------------------------------------------------------------------

def test_entd_income_class_midpoint_eur_covers_all_classes():
    """ENTD_INCOME_CLASS_MIDPOINT_EUR must cover classes -1 through 13."""
    expected_classes = set(range(-1, 14))
    assert set(ENTD_INCOME_CLASS_MIDPOINT_EUR.keys()) == expected_classes, (
        f"ENTD_INCOME_CLASS_MIDPOINT_EUR missing classes: "
        f"{expected_classes - set(ENTD_INCOME_CLASS_MIDPOINT_EUR.keys())}"
    )


def test_entd_income_class_midpoints_are_positive_except_missing():
    """All ENTD income class midpoints must be positive EUR values, except class -1 (missing)."""
    for cls, midpoint in ENTD_INCOME_CLASS_MIDPOINT_EUR.items():
        if cls == -1:
            # missing code; midpoint is None or NaN (not used for scaling)
            assert midpoint is None or (midpoint != midpoint), (  # NaN check
                f"ENTD class -1 should have None/NaN midpoint, got {midpoint}"
            )
        else:
            assert midpoint > 0, f"ENTD class {cls} midpoint should be > 0, got {midpoint}"


def test_entd_income_class_midpoints_are_monotonically_non_decreasing():
    """ENTD midpoints for classes 0..13 must be non-decreasing (higher class -> higher income)."""
    prev = 0.0
    for cls in range(0, 14):
        midpoint = ENTD_INCOME_CLASS_MIDPOINT_EUR[cls]
        assert midpoint >= prev, (
            f"ENTD class {cls} midpoint {midpoint} is less than previous {prev} "
            "(midpoints must be non-decreasing)."
        )
        prev = midpoint


# ---------------------------------------------------------------------------
# Test 6: build_persons (MiD path) now applies INKAR scaling
# ---------------------------------------------------------------------------

def test_build_persons_mid_applies_inkar_scaling():
    """build_persons (MiD source) passes INKAR scale to map_mid_person_attributes.

    When inkar_scale is supplied with Kreis 03101 scale=1.5, the household with
    hheink_gr1=4 (midpoint 1750.0) should produce household_income_eur = 1750*1.5 = 2625.
    """
    from braunschweig.popsim import assembly

    inkar = _inkar_scale({"03101": 1.5})
    merged = pd.DataFrame({
        "ZENSUS100m": ["A"],
        "ZENSUS1km":  ["KA"],
        "H_ID":       [1],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    mid_hh = pd.DataFrame({
        "H_ID":       [1],
        "oek_status": [3],
        "hheink_gr1": [4],     # midpoint 1750.0 EUR
        "H_ANZAUTO":  [1],
        "H_ANZRAD":   [1],
        "anzpedrad":  [1],
        "H_ANZPED":   [0],
    })
    mid_p = pd.DataFrame({
        "H_ID":      [1],
        "P_ID":      [1],
        "HP_ALTER":  [40],
        "HP_SEX":    [1],
        "P_TAET":    [1],
        "P_FSCHEIN": [1],
        "P_FKARTE":  [3],
        "P_BKAT":    [1],
    })

    persons, _ = assembly.build_persons(
        merged, mid_hh, mid_p,
        rng=np.random.RandomState(0),
        inkar_scale=inkar,
    )

    expected = round(1750.0 * 1.5, 0)
    got = float(persons["household_income_eur"].iloc[0])
    assert abs(got - expected) < 1.0, (
        f"With INKAR scale 1.5, household_income_eur should be ~{expected}, got {got}"
    )


def test_build_persons_mid_high_income_uses_numeric_rule():
    """build_persons (MiD source) sets high_income = (household_income_eur >= 5000).

    hheink_gr1=15 -> midpoint 8000.0; scale 1.0 -> 8000.0 EUR -> high_income True.
    hheink_gr1=4  -> midpoint 1750.0; scale 1.0 -> 1750.0 EUR -> high_income False.
    """
    from braunschweig.popsim import assembly

    inkar = _inkar_scale({"03101": 1.0})
    merged = pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km":  ["KA", "KB"],
        "H_ID":       [1, 2],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })
    mid_hh = pd.DataFrame({
        "H_ID":       [1, 2],
        "oek_status": [3, 5],
        "hheink_gr1": [4, 15],
        "H_ANZAUTO":  [0, 0],
        "H_ANZRAD":   [0, 0],
        "anzpedrad":  [0, 0],
        "H_ANZPED":   [0, 0],
    })
    mid_p = pd.DataFrame({
        "H_ID":      [1, 2],
        "P_ID":      [1, 1],
        "HP_ALTER":  [40, 40],
        "HP_SEX":    [1, 1],
        "P_TAET":    [1, 1],
        "P_FSCHEIN": [1, 1],
        "P_FKARTE":  [3, 3],
        "P_BKAT":    [1, 1],
    })

    persons, _ = assembly.build_persons(
        merged, mid_hh, mid_p,
        rng=np.random.RandomState(0),
        inkar_scale=inkar,
    )

    # Group 4 (1750 EUR * 1.0 = 1750) -> not high_income
    hh1 = persons[persons["source_household_id"] == 1]
    assert not hh1["high_income"].any(), (
        f"hheink_gr1=4 (1750 EUR) should not be high_income; got {hh1['high_income'].tolist()}"
    )
    # Group 15 (8000 EUR * 1.0 = 8000) -> high_income
    hh2 = persons[persons["source_household_id"] == 2]
    assert hh2["high_income"].all(), (
        f"hheink_gr1=15 (8000 EUR) should be high_income; got {hh2['high_income'].tolist()}"
    )


def test_build_persons_skip_inkar_income_scale_keeps_raw_midpoint():
    """skip_inkar_income_scale=True skips the (redundant) INKAR per-Kreis scaling.

    When income_kreis_control is on it draws a fresh per-Kreis continuous income and
    OVERWRITES household_income_eur downstream, so the INKAR scaling in build_persons
    is wasted. With skip_inkar_income_scale=True and INKAR scale 1.5 for Kreis 03101,
    the raw midpoint (1750.0 for hheink_gr1=4) is left UNSCALED -- it is exactly the
    value the control step replaces. economic_status (the income-control input) and
    the categorical household_income label must remain so the control step still has
    its inputs.
    """
    from braunschweig.popsim import assembly

    inkar = _inkar_scale({"03101": 1.5})
    merged = pd.DataFrame({
        "ZENSUS100m": ["A"], "ZENSUS1km": ["KA"], "H_ID": [1],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    mid_hh = pd.DataFrame({
        "H_ID": [1], "oek_status": [3], "hheink_gr1": [4],
        "H_ANZAUTO": [1], "H_ANZRAD": [1], "anzpedrad": [1], "H_ANZPED": [0],
    })
    mid_p = pd.DataFrame({
        "H_ID": [1], "P_ID": [1], "HP_ALTER": [40], "HP_SEX": [1],
        "P_TAET": [1], "P_FSCHEIN": [1], "P_FKARTE": [3], "P_BKAT": [1],
    })

    persons, _ = assembly.build_persons(
        merged, mid_hh, mid_p,
        rng=np.random.RandomState(0),
        inkar_scale=inkar,
        skip_inkar_income_scale=True,
    )

    # INKAR scaling skipped -> raw midpoint 1750.0 (NOT 1750*1.5 = 2625).
    got = float(persons["household_income_eur"].iloc[0])
    assert abs(got - 1750.0) < 1.0, (
        f"skip_inkar_income_scale=True must leave the raw midpoint 1750.0 "
        f"(INKAR not applied, value is overwritten by income_kreis_control); got {got}"
    )
    # The income-control inputs/labels must survive the skip.
    assert "economic_status" in persons.columns, "economic_status must remain (control input)"
    assert "household_income" in persons.columns, "categorical household_income label must remain"


def test_build_persons_skip_inkar_default_false_still_scales():
    """The default skip_inkar_income_scale=False preserves the legacy INKAR scaling.

    Byte-identical OFF path: with the default (no skip) and INKAR scale 1.5,
    household_income_eur = 1750.0 * 1.5 = 2625.0 (same as before this feature).
    """
    from braunschweig.popsim import assembly

    inkar = _inkar_scale({"03101": 1.5})
    merged = pd.DataFrame({
        "ZENSUS100m": ["A"], "ZENSUS1km": ["KA"], "H_ID": [1],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    mid_hh = pd.DataFrame({
        "H_ID": [1], "oek_status": [3], "hheink_gr1": [4],
        "H_ANZAUTO": [1], "H_ANZRAD": [1], "anzpedrad": [1], "H_ANZPED": [0],
    })
    mid_p = pd.DataFrame({
        "H_ID": [1], "P_ID": [1], "HP_ALTER": [40], "HP_SEX": [1],
        "P_TAET": [1], "P_FSCHEIN": [1], "P_FKARTE": [3], "P_BKAT": [1],
    })

    persons, _ = assembly.build_persons(
        merged, mid_hh, mid_p,
        rng=np.random.RandomState(0),
        inkar_scale=inkar,
    )

    got = float(persons["household_income_eur"].iloc[0])
    assert abs(got - round(1750.0 * 1.5, 0)) < 1.0, (
        f"default (no skip) must apply INKAR scaling 1.5 -> 2625.0; got {got}"
    )


def test_build_persons_no_inkar_uses_scale_one():
    """build_persons with no inkar_scale (None) uses scale=1.0 (pure midpoint).

    This ensures unit tests that don't supply INKAR still produce the correct
    midpoint value (no crash, no silent wrong value).
    """
    from braunschweig.popsim import assembly

    merged = pd.DataFrame({
        "ZENSUS100m": ["A"],
        "ZENSUS1km":  ["KA"],
        "H_ID":       [1],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    mid_hh = pd.DataFrame({
        "H_ID":       [1],
        "oek_status": [3],
        "hheink_gr1": [4],     # midpoint 1750.0 EUR
        "H_ANZAUTO":  [0],
        "H_ANZRAD":   [0],
        "anzpedrad":  [0],
        "H_ANZPED":   [0],
    })
    mid_p = pd.DataFrame({
        "H_ID":      [1],
        "P_ID":      [1],
        "HP_ALTER":  [40],
        "HP_SEX":    [1],
        "P_TAET":    [1],
        "P_FSCHEIN": [1],
        "P_FKARTE":  [3],
        "P_BKAT":    [1],
    })

    # No inkar_scale supplied (None) -> scale=1.0
    persons, _ = assembly.build_persons(
        merged, mid_hh, mid_p,
        rng=np.random.RandomState(0),
        inkar_scale=None,
    )

    # With scale=1.0, household_income_eur = midpoint = 1750.0
    got = float(persons["household_income_eur"].iloc[0])
    assert abs(got - 1750.0) < 1.0, (
        f"Without INKAR, household_income_eur should equal midpoint 1750.0, got {got}"
    )


# ---------------------------------------------------------------------------
# Test 7: popsim_open ENTD path now sets household_income_eur
# ---------------------------------------------------------------------------

def test_entd_map_person_attributes_emits_household_income_eur():
    """EntdSource.map_person_attributes must now emit household_income_eur (was absent before).

    household_income_eur = ENTD_INCOME_CLASS_MIDPOINT_EUR[income_class] * INKAR_scale.
    With scale=1.0 (no inkar), household_income_eur = the raw ENTD midpoint.
    """
    from braunschweig.popsim.sources.entd import EntdSource

    hh = pd.DataFrame({
        "household_id":       [10, 30],
        "household_weight":   [1.0, 1.0],
        "household_size":     [2, 1],
        "number_of_cars":     [1, 0],
        "number_of_bicycles": [0, 0],
        "income_class":       [5, 13],    # class 5 -> 1350.0 EUR; class 13 -> 12000.0 EUR
        "urban_type":         ["central_city", "none"],  # Phase 4A: required by _HH_JOIN_COLS
        "departement_id":     ["03101", "03101"],
    })
    p = pd.DataFrame({
        "person_id":              [100, 300],
        "household_id":           [10,  30],
        "person_weight":          [1.0, 1.0],
        "age":                    [40,  70],
        "sex":                    ["male", "male"],
        "employed":               [True, False],
        "studies":                [False, False],
        "has_license":            [True, False],
        "has_pt_subscription":    [True, False],
        "number_of_trips":        [3, 1],
        "departement_id":         ["03101", "03101"],
        "trip_weight":            [1.0, 1.0],
        "socioprofessional_class":[3, 7],
    })

    src = EntdSource()
    # Without inkar_scale: should use scale=1.0. Unified mapper contract:
    # (persons, pseudonym_map); ENTD's map is empty.
    out, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    assert "household_income_eur" in out.columns, (
        "EntdSource.map_person_attributes must now emit household_income_eur"
    )
    # class 5 -> midpoint 1350.0; scale 1.0 -> 1350.0
    row_cls5 = out[out["age"] == 40].iloc[0]
    assert abs(row_cls5["household_income_eur"] - ENTD_INCOME_CLASS_MIDPOINT_EUR[5]) < 1.0, (
        f"income_class=5 -> expected {ENTD_INCOME_CLASS_MIDPOINT_EUR[5]}, "
        f"got {row_cls5['household_income_eur']}"
    )


def test_entd_map_person_attributes_high_income_uses_numeric_rule():
    """EntdSource.map_person_attributes high_income = (household_income_eur >= 5000).

    income_class=13 -> midpoint 12000.0 EUR -> high_income True.
    income_class=5  -> midpoint 1350.0  EUR -> high_income False.
    (With scale=1.0.)
    """
    from braunschweig.popsim.sources.entd import EntdSource

    hh = pd.DataFrame({
        "household_id":       [10, 30],
        "household_weight":   [1.0, 1.0],
        "household_size":     [1, 1],
        "number_of_cars":     [0, 0],
        "number_of_bicycles": [0, 0],
        "income_class":       [5, 13],
        "urban_type":         ["suburb", "none"],  # Phase 4A: required by _HH_JOIN_COLS
        "departement_id":     ["03101", "03101"],
    })
    p = pd.DataFrame({
        "person_id":              [100, 300],
        "household_id":           [10, 30],
        "person_weight":          [1.0, 1.0],
        "age":                    [40, 70],
        "sex":                    ["male", "male"],
        "employed":               [True, False],
        "studies":                [False, False],
        "has_license":            [True, False],
        "has_pt_subscription":    [True, False],
        "number_of_trips":        [3, 1],
        "departement_id":         ["03101", "03101"],
        "trip_weight":            [1.0, 1.0],
        "socioprofessional_class":[3, 7],
    })

    src = EntdSource()
    # Unified mapper contract: (persons, pseudonym_map); ENTD's map is empty.
    out, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # class 5 (1350 EUR) -> high_income False
    assert not out[out["age"] == 40].iloc[0]["high_income"], (
        f"income_class=5 (1350 EUR) should not be high_income"
    )
    # class 13 (12000 EUR) -> high_income True
    assert out[out["age"] == 70].iloc[0]["high_income"], (
        f"income_class=13 (12000 EUR) should be high_income"
    )


# ---------------------------------------------------------------------------
# Test 8: HIGH_INCOME_THRESHOLD_EUR constant is 5000.0
# ---------------------------------------------------------------------------

def test_high_income_threshold_constant():
    """HIGH_INCOME_THRESHOLD_EUR must be 5000.0 EUR/month."""
    assert HIGH_INCOME_THRESHOLD_EUR == 5000.0, (
        f"Expected HIGH_INCOME_THRESHOLD_EUR=5000.0, got {HIGH_INCOME_THRESHOLD_EUR}"
    )
