"""Regression guard: the PopulationSim control build reproduces its golden tables (#657).

The four control-total tables the ``data.census.filtered`` control build produces from the
synthetic cell frame of ``popsim_control_build_fixture`` are frozen in
``tests/data/popsim_input_consistency/``. Every later config key of #657 at its legacy value
must keep this test green; a deliberate change of the expected tables regenerates the CSVs
with ``python -m tests.popsim_control_build_fixture`` and says why in its commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_control_build_fixture as fixture  # noqa: E402


def test_control_build_reproduces_the_golden_tables():
    built = fixture.build_control_tables()

    assert set(built) == set(fixture.GOLDEN_NAMES)
    for name in fixture.GOLDEN_NAMES:
        pd.testing.assert_frame_equal(
            built[name], fixture.read_golden(name), check_exact=True,
            obj=f"control_totals_{name}")
