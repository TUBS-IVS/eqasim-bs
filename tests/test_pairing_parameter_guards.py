"""Range guards for the two donor-side passive escort pairing parameters (issue #409).

Both are documented "valid range > 0" in ``braunschweig/popsim/stage/config_keys.py`` and in
``configs/base_bs.yml``, and neither was enforced before this module existed. The floor is the
dangerous one: ``pair_passive_legs`` admits an escorting adult with
``HP_ALTER >= adult_min_age``, so a floor of 0 makes EVERY household member eligible -- a
toddler then "escorts" the child it is travelling with, the leg is reported ``paired``, and
the module's only alarm (``WARN_PAIRED_SHARE``) fires on a LOW pairing share and therefore
stays silent in exactly that case. The point-of-use guard is tested in
``tests/test_escort_pairing.py``; this module covers the shared validator and the four
``configure()``s that declare the keys, so a bad YAML value fails at DAG-build time instead of
hours into a run.
"""
from __future__ import annotations

import pytest

from braunschweig.popsim import distance_distributions as DISTANCES
from braunschweig.popsim import trips_stage as TRIPS_STAGE
from braunschweig.popsim import stage as POPSIM_STAGE
from braunschweig.popsim.stage.config_keys import (
    KEY_PASSIVE_PAIR_ADULT_MIN_AGE_YEARS, KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, require_positive,
)
from braunschweig.synthesis.commute_day import home_office_donors_stage as DONORS

GUARDED_KEYS = [KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, KEY_PASSIVE_PAIR_ADULT_MIN_AGE_YEARS]
#: Every stage whose ``configure()`` declares the two pairing parameters.
DECLARING_STAGES = [TRIPS_STAGE, POPSIM_STAGE, DISTANCES, DONORS]


# ------------------------------------------------------------------- the shared validator

@pytest.mark.parametrize("value", [1, 15.0, 18, 0.5])
def test_require_positive_returns_a_valid_value_unchanged(value):
    assert require_positive("some.key", value) is value


@pytest.mark.parametrize("value", [0, 0.0, -1, -15.0])
def test_require_positive_rejects_a_non_positive_value_naming_the_key(value):
    with pytest.raises(ValueError, match="some.key must be > 0"):
        require_positive("some.key", value)


def test_require_positive_reports_a_non_number_as_a_configuration_error():
    # A context that resolves the key to nothing must not surface as a bare TypeError from
    # the comparison; it is the same class of configuration defect.
    with pytest.raises(ValueError, match="some.key must be a number > 0"):
        require_positive("some.key", None)


# ------------------------------------------------- the four configure() declaration sites

class _GuardProbeContext:
    """Minimal ``configure()`` context that resolves one key to a bad value.

    Mirrors synpp's ``ConfigurationContext``: ``config(name, default)`` RETURNS the resolved
    value (the default, unless this probe overrides that key), and ``stage()`` records the
    dependency. A ``configure()`` that validates what it declares reads the value back, so a
    stub that returned nothing would exercise the validator's type branch instead of its
    range branch.
    """

    def __init__(self, bad_key, bad_value):
        self._bad_key = bad_key
        self._bad_value = bad_value
        self.declared = {}

    def config(self, name, default=None, volatile=False):
        # ``volatile`` mirrors synpp's ConfigurationContext (ADR-0126 declares the
        # operational keys with volatile=True); the probe records the value either way,
        # because volatility is a cache-hash property and never changes what configure()
        # reads back.
        value = self._bad_value if name == self._bad_key else default
        self.declared[name] = value
        return value

    def stage(self, name, **_kwargs):
        return None


@pytest.mark.parametrize("module", DECLARING_STAGES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
@pytest.mark.parametrize("key", GUARDED_KEYS)
@pytest.mark.parametrize("bad_value", [0, -1])
def test_configure_rejects_a_non_positive_pairing_parameter(module, key, bad_value):
    with pytest.raises(ValueError, match=f"{key} must be > 0"):
        module.configure(_GuardProbeContext(key, bad_value))


@pytest.mark.parametrize("module", DECLARING_STAGES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_configure_accepts_the_declared_defaults(module):
    # The guards must not disturb the valid range: with nothing overridden every stage
    # declares both keys and configure() completes.
    context = _GuardProbeContext(bad_key=None, bad_value=None)
    module.configure(context)
    for key in GUARDED_KEYS:
        assert context.declared[key] > 0, key
