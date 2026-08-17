"""Feature-parity tests for the legacy-IPF (``simple_ipf_open``) run configs.

Counterpart to ``test_popsim_config_parity.py``: the project rule is that
flag-gated model features are ON in run configs so they are not forgotten. That
test pins the parity flags of the two popsim configs and deliberately excludes
the IPF-only flags, because the popsim path replaces the legacy enriched stage
and the keys would be dead there. Nothing pinned the IPF side -- which is how
issue #251 happened: the three household-realism refinements below were designed
(ADR-0004/0005/0006), implemented, unit-tested, and then never enabled in any
committed configuration, so no run ever executed them.

Pinned here for every committed ``simple_ipf_open`` run config:

- ``braunschweig.ipf.use_joint_age_size_margin``  -- joint age x household-size
  IPF margin from Zensus 2022 1000A-3082 (ADR-0004),
- ``braunschweig.ipf.age_aware_chunking``         -- age-aware household
  composition, children-driven capacity + mother-age anchor (ADR-0005),
- ``braunschweig.chunking.sex_aware_couples``     -- sex-aware couple pairing,
  ~1.1% same-sex (ADR-0006).

``configs/fixtures/config_dryrun_braunschweig.yml`` is intentionally excluded: it
enables none of the household-realism margins and exists to resolve the stage
graph, not to produce a population.

The flags carry hard interdependencies (see
``braunschweig.ipf.config_validation``), so the combination each config ships is
additionally run through the very validator the IPF stages use at runtime. A
config that passes these tests cannot fail household-realism validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from braunschweig.ipf.config_validation import validate_household_realism_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "configs" / "fixtures"

#: Every committed run config of the legacy IPF workflow. All of them route
#: ``data.census.filtered`` to ``braunschweig.ipf.attributed``; the 25% fixture is
#: the canonical one (``braunschweig.documentation.dag.PIPELINE_CONFIGS``).
IPF_RUN_CONFIGS = (
    "config_local_braunschweig.yml",
    "config_local_braunschweig_10pct.yml",
    "config_local_braunschweig_25pct.yml",
    "config_smoke_simple_ipf.yml",
)

#: The three flags issue #251 found declared-but-never-enabled.
HOUSEHOLD_REALISM_FLAGS = (
    "braunschweig.ipf.use_joint_age_size_margin",
    "braunschweig.ipf.age_aware_chunking",
    "braunschweig.chunking.sex_aware_couples",
)

#: Root prerequisite of the whole group (see config_validation).
SIZE_MARGIN_FLAG = "braunschweig.ipf.use_household_size_margin"


def _config(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as f:
        return yaml.safe_load(f)["config"]


@pytest.mark.parametrize("name", IPF_RUN_CONFIGS)
def test_ipf_run_config_routes_census_filtered_to_the_ipf(name):
    """Guard the premise: these fixtures really are the legacy-IPF workflow."""
    with open(FIXTURES / name, encoding="utf-8") as f:
        document = yaml.safe_load(f)
    aliases = document.get("aliases") or {}
    assert aliases.get("data.census.filtered") == "braunschweig.ipf.attributed"


@pytest.mark.parametrize("name", IPF_RUN_CONFIGS)
@pytest.mark.parametrize("flag", HOUSEHOLD_REALISM_FLAGS)
def test_household_realism_flag_is_enabled(name, flag):
    config = _config(name)
    assert config.get(flag) is True, (
        f"{name} does not enable {flag}; the feature would be dead in this run "
        "(issue #251)")


@pytest.mark.parametrize("name", IPF_RUN_CONFIGS)
def test_household_realism_prerequisites_hold(name):
    """The shipped combination must pass the runtime validator, not just be true.

    ``age_aware_chunking`` and the joint margin require the size margin, and
    sex-aware pairing requires age-aware chunking; enabling a flag without its
    prerequisite makes the IPF stages raise at the top of ``execute()``.
    """
    config = _config(name)
    assert config.get(SIZE_MARGIN_FLAG) is True
    validate_household_realism_config(lambda key: config.get(key, False))
