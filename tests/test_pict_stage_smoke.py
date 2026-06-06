"""Stage-level smoke tests driven by the PICT covering array.

For every configuration in the pairwise covering array
(``braunschweig.testing.pict``), run the flag-gated IPF stages' ``configure()``
through a stub context and assert that the flag-conditional dependency wiring is
correct. This exercises the real configure-phase interaction logic across the
whole feasible flag space WITHOUT a full pipeline run or any input data:

  * braunschweig.ipf.model     -- declares the joint age-group bounds config
    exactly when the joint age x size margin is enabled.
  * braunschweig.ipf.attributed -- requests the Zensus household-type stage
    exactly when the household-type margin OR age-aware chunking is enabled.

It also confirms configure() never raises for any feasible combination.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.ipf import attributed as ipf_attributed  # noqa: E402
from braunschweig.ipf import model as ipf_model  # noqa: E402
from braunschweig.synthesis.locations import secondary_chainsolvers as sc  # noqa: E402
from braunschweig.testing import pict  # noqa: E402

# PICT short factor name -> real config key (household-realism subset).
_PICT_TO_CONFIG = {
    "use_household_size_margin": "braunschweig.ipf.use_household_size_margin",
    "use_joint_age_size_margin": "braunschweig.ipf.use_joint_age_size_margin",
    "age_aware_chunking": "braunschweig.ipf.age_aware_chunking",
    "use_employment_margin": "braunschweig.ipf.use_employment_margin",
    "sex_aware_couples": "braunschweig.chunking.sex_aware_couples",
    "chainsolvers_parallel": "braunschweig.chainsolvers.parallel",
    "chainsolvers_fallback": "braunschweig.chainsolvers.fallback",
}

_HH_TYPE_STAGE = "braunschweig.data.census.households_type"
_JOINT_BOUNDS_KEY = "braunschweig.ipf.joint_age_group_bounds"


class _ConfigureStub:
    """Minimal synpp configure-phase context: records declared config keys and
    requested stage dependencies; returns the PICT override or the declared
    default for each config read."""

    def __init__(self, overrides):
        self._overrides = overrides
        self.requested_stages = []
        self.declared = {}

    def config(self, key, default=None):
        self.declared[key] = default
        return self._overrides.get(key, default)

    def stage(self, name, *args, **kwargs):
        self.requested_stages.append(name)


def _overrides(row):
    return {cfg: row[name] for name, cfg in _PICT_TO_CONFIG.items() if name in row}


def test_ipf_model_configure_runs_for_every_pict_case():
    for row in pict.pipeline_covering_array():
        stub = _ConfigureStub(_overrides(row))
        ipf_model.configure(stub)  # must not raise
        assert "braunschweig.ipf.prepare" in stub.requested_stages
        # The joint age-group bounds config is declared exactly when the joint
        # margin is enabled (a cache-invalidating dependency).
        declared_bounds = _JOINT_BOUNDS_KEY in stub.declared
        assert declared_bounds == bool(row["use_joint_age_size_margin"])


def test_ipf_attributed_configure_runs_for_every_pict_case():
    for row in pict.pipeline_covering_array():
        stub = _ConfigureStub(_overrides(row))
        ipf_attributed.configure(stub)  # must not raise
        assert "braunschweig.ipf.model" in stub.requested_stages
        # households_type is requested exactly when the hh_type margin OR
        # age-aware chunking is enabled. The hh_type margin is not a PICT factor
        # (defaults off), so here it reduces to age-aware chunking.
        needs_hh_type = bool(row["age_aware_chunking"])
        assert (_HH_TYPE_STAGE in stub.requested_stages) == needs_hh_type


def test_secondary_chainsolvers_configure_runs_for_every_pict_case():
    for row in pict.pipeline_covering_array():
        stub = _ConfigureStub(_overrides(row))
        sc.configure(stub)  # must not raise for parallel on/off, either fallback
        # The fallback strategy is always declared; both PICT values are valid.
        assert "braunschweig.chainsolvers.fallback" in stub.declared
