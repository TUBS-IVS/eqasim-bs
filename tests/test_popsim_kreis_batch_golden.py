"""Regression guard: the KREIS batch folders reproduce their golden control tables (#658).

Rebuilds the two synthetic batch folders of ``popsim_kreis_batch_fixture`` and compares every
``control_totals_*.csv`` with the tables frozen in
``tests/data/popsim_input_consistency/kreis_batch/`` (README there). Every later #658 key at
its legacy value must keep this test green; a deliberate change regenerates the CSVs with
``python -m tests.popsim_kreis_batch_fixture`` and says why in its commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_kreis_batch_fixture as fixture  # noqa: E402


def test_kreis_batch_folders_reproduce_the_golden_tables(tmp_path):
    data_dirs = fixture.build_batch_folders(tmp_path)

    assert tuple(data_dirs) == fixture.BATCH_NAMES
    for batch, data_dir in data_dirs.items():
        golden_dir = fixture.GOLDEN_DIR / batch
        names = fixture.table_names(golden_dir)
        assert fixture.table_names(data_dir) == names
        for name in names:
            pd.testing.assert_frame_equal(
                fixture.read_table(data_dir, name), fixture.read_table(golden_dir, name),
                check_exact=True, obj=f"{batch}/control_totals_{name}")

    # The golden must hold what Part A changes: an education control, a car class, employed.
    kreis = fixture.read_table(fixture.GOLDEN_DIR / "batch_000", "KREIS")
    assert any(c.startswith("education_") for c in kreis.columns)
    assert any(c.startswith("number_of_cars_") for c in kreis.columns)
    assert "employed_KREIS" in kreis.columns
