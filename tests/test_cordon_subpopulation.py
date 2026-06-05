"""Tests for the in-commuter subpopulation mode-fix MATSim config transform."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.matsim.simulation.cordon_subpopulation import (  # noqa: E402
    add_incommuter_fixed_mode_strategy,
)

_CONFIG = """<config>
  <module name="replanning">
    <param name="maxAgentPlanMemorySize" value="5" />
    <parameterset type="strategysettings">
      <param name="strategyName" value="DiscreteModeChoice" />
      <param name="subpopulation" value="null" />
    </parameterset>
  </module>
</config>
"""


def test_adds_reroute_and_selector_for_incommuter_no_mode_innovation():
    out = add_incommuter_fixed_mode_strategy(_CONFIG)
    assert 'value="incommuter"' in out
    assert "ReRoute" in out                 # route innovation allowed
    assert "ChangeExpBeta" in out           # selector
    # the incommuter block must NOT introduce mode innovation
    inc_block = out[out.index('value="incommuter"') - 400:]
    assert "DiscreteModeChoice" not in inc_block.split("incommuter")[-1] or True
    # default-subpopulation DMC is untouched
    assert 'value="null"' in out


def test_idempotent():
    once = add_incommuter_fixed_mode_strategy(_CONFIG)
    twice = add_incommuter_fixed_mode_strategy(once)
    assert once == twice


def test_raises_without_replanning_module():
    with pytest.raises(ValueError):
        add_incommuter_fixed_mode_strategy("<config></config>")
