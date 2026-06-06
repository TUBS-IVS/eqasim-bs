"""Tests for the centralised household-realism config validator, including the
connection to the PICT covering array (every feasible PICT case must pass the
real production validator)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.ipf.config_validation import (  # noqa: E402
    ATTRIBUTED_REQUIREMENTS,
    HOUSEHOLD_REALISM_REQUIREMENTS,
    MODEL_REQUIREMENTS,
    validate_household_realism_config,
)
from braunschweig.testing import pict  # noqa: E402


def _get(values):
    """A config accessor returning False for any unset key (the canonical default)."""
    return lambda key: values.get(key, False)


def test_each_requirement_raises_when_prerequisite_missing():
    for flag, prerequisite, _reason in HOUSEHOLD_REALISM_REQUIREMENTS:
        with pytest.raises(RuntimeError) as exc:
            validate_household_realism_config(
                _get({flag: True, prerequisite: False}),
                HOUSEHOLD_REALISM_REQUIREMENTS,
            )
        message = str(exc.value)
        assert flag in message and prerequisite in message


def test_all_off_passes():
    validate_household_realism_config(_get({}), HOUSEHOLD_REALISM_REQUIREMENTS)


def test_full_valid_stack_passes():
    enabled = {flag: True for flag, _p, _r in HOUSEHOLD_REALISM_REQUIREMENTS}
    for _flag, prerequisite, _r in HOUSEHOLD_REALISM_REQUIREMENTS:
        enabled[prerequisite] = True
    validate_household_realism_config(_get(enabled), HOUSEHOLD_REALISM_REQUIREMENTS)


def test_sex_aware_without_age_aware_is_now_rejected():
    # Hardened constraint: this combination was a silent no-op before, now fails fast.
    with pytest.raises(RuntimeError):
        validate_household_realism_config(
            _get({
                "braunschweig.chunking.sex_aware_couples": True,
                "braunschweig.ipf.age_aware_chunking": False,
            }),
            ATTRIBUTED_REQUIREMENTS,
        )


def test_model_and_attributed_subsets_partition_the_full_set():
    # Each stage validates only the constraints over the flags it declares; the two
    # subsets are disjoint and together cover the full documented constraint set.
    model = set(MODEL_REQUIREMENTS)
    attributed = set(ATTRIBUTED_REQUIREMENTS)
    assert model.isdisjoint(attributed)
    assert model | attributed == set(HOUSEHOLD_REALISM_REQUIREMENTS)


# Map the PICT short factor names to the real config keys (household-realism subset).
_PICT_TO_CONFIG = {
    "use_household_size_margin": "braunschweig.ipf.use_household_size_margin",
    "use_joint_age_size_margin": "braunschweig.ipf.use_joint_age_size_margin",
    "age_aware_chunking": "braunschweig.ipf.age_aware_chunking",
    "use_employment_margin": "braunschweig.ipf.use_employment_margin",
    "sex_aware_couples": "braunschweig.chunking.sex_aware_couples",
}


def test_every_pict_case_passes_the_production_validator():
    # The PICT model and the production validator must agree: every constraint-
    # feasible covering-array configuration is accepted by the real validator.
    for row in pict.pipeline_covering_array():
        values = {cfg: row[name] for name, cfg in _PICT_TO_CONFIG.items()}
        validate_household_realism_config(_get(values), HOUSEHOLD_REALISM_REQUIREMENTS)
