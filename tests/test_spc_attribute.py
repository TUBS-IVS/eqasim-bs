"""Tests for Task A3c -- map the ``socioprofessional_class`` (SPC) person
attribute from (employed, age, studies) instead of the hardcoded constant 0.

The SPC code space is the eqasim/INSEE CS1 partition used by the analysis
marginals (``eqasim_common.analysis.marginals.SOCIOPROFESIONAL_CLASS_LABELS``):

    0 ???  1 Agriculture  2 Independent  3 Science  4 Intermediate
    5 Employee  6 Worker  7 Retired  8 Other (incl. students/children)

No occupation data exists upstream of the HTS in this fork, so SPC is mapped from
the broad activity status that the IPF + age inflation DO carry: students -> 8,
retired (inactive, age >= retirement) -> 7, other inactive -> 8, and employed ->
an age-proxied active class (a documented coarse seniority proxy, NOT real
occupation data). With ``reactivate_person_attributes`` OFF the legacy constant 0
is preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.ipf.attributed import (  # noqa: E402
    derive_socioprofessional_class, SPC_STUDENT, SPC_RETIRED, SPC_OTHER_INACTIVE,
)


def test_students_map_to_other_category():
    spc = derive_socioprofessional_class(
        employed=pd.Series([False, True]),
        age=pd.Series([20, 22]),
        studies=pd.Series([True, True]),
    )
    assert (spc == SPC_STUDENT).all()


def test_retired_inactive_maps_to_retired():
    spc = derive_socioprofessional_class(
        employed=pd.Series([False, False]),
        age=pd.Series([70, 80]),
        studies=pd.Series([False, False]),
    )
    assert (spc == SPC_RETIRED).all()


def test_working_age_inactive_maps_to_other_inactive():
    spc = derive_socioprofessional_class(
        employed=pd.Series([False]),
        age=pd.Series([40]),
        studies=pd.Series([False]),
    )
    assert spc.iloc[0] == SPC_OTHER_INACTIVE


def test_employed_maps_to_active_occupational_classes():
    # Active occupational classes are 3 (Science), 4 (Intermediate), 5 (Employee),
    # 6 (Worker). Employed persons must never land in 0/7/8.
    ages = pd.Series(np.arange(18, 65))
    spc = derive_socioprofessional_class(
        employed=pd.Series([True] * len(ages)),
        age=ages,
        studies=pd.Series([False] * len(ages)),
    )
    assert spc.isin([2, 3, 4, 5, 6]).all()
    assert spc.nunique() >= 2  # non-degenerate among the employed


def test_distribution_is_non_degenerate_on_a_mixed_population():
    rng = np.random.RandomState(0)
    n = 5000
    age = pd.Series(rng.randint(18, 90, n))
    employed = pd.Series((age < 67) & (rng.random_sample(n) < 0.6))
    studies = pd.Series((age < 30) & ~employed & (rng.random_sample(n) < 0.5))
    spc = derive_socioprofessional_class(employed, age, studies)
    # several distinct classes present (students, retired, inactive, active)
    assert spc.nunique() >= 4
    # never the unknown sentinel 0 once reactivated
    assert (spc != 0).all()


def test_mapping_is_a_pure_function_of_inputs():
    args = dict(
        employed=pd.Series([True, False, False, True]),
        age=pd.Series([30, 70, 25, 50]),
        studies=pd.Series([False, False, True, False]),
    )
    a = derive_socioprofessional_class(**args)
    b = derive_socioprofessional_class(**args)
    assert a.tolist() == b.tolist()
