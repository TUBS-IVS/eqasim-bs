"""Tests for Task A3a -- reactivate the ``couple`` person attribute.

``couple`` was hardcoded to ``False`` in ``braunschweig.ipf.attributed`` although
the household-formation pass already produces a realised ``hh_type`` from which
the partnership status follows: the adults of a ``couple`` or
``couple_with_children`` household are exactly the paired partners.

With ``reactivate_person_attributes`` ON, ``couple`` is derived per person from
``hh_type`` + the adult-age threshold; with the flag OFF the legacy constant
``False`` is preserved (byte-identical).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import braunschweig.ipf.attributed as attributed  # noqa: E402
from braunschweig.ipf.attributed import derive_couple  # noqa: E402
from braunschweig.data.education.student_share import load_default_table  # noqa: E402


def _frame():
    # Three households:
    #  - couple (two adults) -> both couple=True
    #  - couple_with_children (two adults + one child) -> the two adults True,
    #    the child False
    #  - single_parent (one adult + one child) -> nobody is a couple
    return pd.DataFrame({
        "age": [40, 38, 45, 42, 10, 35, 8],
        "hh_type": [
            "couple", "couple",
            "couple_with_children", "couple_with_children", "couple_with_children",
            "single_parent", "single_parent",
        ],
    })


def test_couple_true_exactly_for_adults_in_couple_households():
    df = _frame()
    couple = derive_couple(df["hh_type"], df["age"], min_adult_age=18)
    expected = [True, True, True, True, False, False, False]
    assert couple.tolist() == expected


def test_children_in_couple_household_are_not_couple():
    df = _frame()
    couple = derive_couple(df["hh_type"], df["age"], min_adult_age=18)
    # the 10-year-old in the couple_with_children household
    assert couple.iloc[4] == False  # noqa: E712


def test_other_multi_and_single_never_couple():
    df = pd.DataFrame({
        "age": [30, 31, 32, 70],
        "hh_type": ["other_multi", "other_multi", "other_multi", "single"],
    })
    couple = derive_couple(df["hh_type"], df["age"], min_adult_age=18)
    assert not couple.any()


def test_min_adult_age_threshold_is_respected():
    # A 16-year-old in a couple_with_children household is a child, not a partner.
    df = pd.DataFrame({
        "age": [44, 42, 16],
        "hh_type": ["couple_with_children"] * 3,
    })
    couple = derive_couple(df["hh_type"], df["age"], min_adult_age=18)
    assert couple.tolist() == [True, True, False]


# --- execute()-level OFF/ON behaviour (the stage flag) ---------------------

class _StubCtx:
    def __init__(self, cfg, stages):
        self._cfg = cfg
        self._stages = stages

    def config(self, key, default=None):
        return self._cfg.get(key, default)

    def stage(self, name):
        return self._stages[name]


def _model_frame():
    # Two communes' worth of size-2 households so couple/couple_with_children
    # households get formed by the age-aware pass.
    n = 8
    return pd.DataFrame({
        "commune_id": ["03101"] * n,
        "departement_id": ["03101"] * n,
        "sex": (["male", "female"] * (n // 2)),
        "age_class": [98] * n,
        "employed": [True, True, False, False, True, True, False, False],
        "license": [False] * n,
        "weight": [1.0] * n,
        "hh_size": ["2"] * n,
    })


def _cfg(reactivate):
    return {
        "braunschweig.ipf.use_household_size_margin": True,
        "braunschweig.ipf.use_household_type_margin": False,
        "braunschweig.ipf.age_aware_chunking": True,
        "random_seed": 1,
        "braunschweig.chunking.minimum_adult_age": 18,
        "braunschweig.chunking.couple_age_weight": 1.0,
        "braunschweig.chunking.couple_age_std": 0.0,
        "braunschweig.chunking.parent_child_weight": 1.0,
        "braunschweig.chunking.parent_child_gap_years": 31.0,
        "braunschweig.chunking.parent_child_gap_std": 0.0,
        "braunschweig.chunking.sex_aware_couples": False,
        "braunschweig.chunking.same_sex_couple_share": 0.011,
        "braunschweig.chunking.child_parent_age_target_weight": 0.0,
        "reactivate_person_attributes": reactivate,
        # Registered default in attributed.configure(); the stub context does
        # not honour registered defaults, so it must be provided explicitly.
        "matching_attributes": [],
    }


def _household_type_frame():
    # All size-2 households are couples -> the formed adults must get couple=True.
    return pd.DataFrame({
        "commune_id": ["03101"],
        "hh_size": ["2"],
        "hh_type": ["couple"],
        "weight": [1.0],
    })


def _stages():
    return {
        "braunschweig.ipf.model": _model_frame(),
        "braunschweig.data.census.households_type": _household_type_frame(),
        "braunschweig.data.education.student_share": load_default_table(),
    }


def test_execute_off_keeps_couple_false():
    ctx = _StubCtx(_cfg(reactivate=False), _stages())
    out = attributed.execute(ctx)
    assert out["couple"].any() == False  # noqa: E712 -- legacy dead constant
    assert (out["studies"] == False).all()  # noqa: E712
    assert (out["socioprofessional_class"] == 0).all()


def test_execute_on_derives_couple_for_couple_household_adults():
    ctx = _StubCtx(_cfg(reactivate=True), _stages())
    out = attributed.execute(ctx)
    # Every adult in a couple household must be flagged; children must not.
    couple_hh = out[out["hh_type"].isin(["couple", "couple_with_children"])]
    adults = couple_hh["age"] >= 18
    assert (couple_hh.loc[adults, "couple"]).all()
    assert not (couple_hh.loc[~adults, "couple"]).any()
    # SPC is reactivated (no longer the constant 0) and studies is a real bool.
    assert (out["socioprofessional_class"] != 0).any()
    assert out["studies"].dtype == bool
