"""Tests that the PopulationSim MiD seed households carry H_MIETE (tenure).

H_MIETE is required by the Tier-2 tenure controls added in Task 9.  The MiD
seed (load_mid_seed / select_seed_columns) must expose H_MIETE on the
seed_households frame so PopulationSim can evaluate the expressions
``(households.H_MIETE == 2)`` (owner) and ``(households.H_MIETE == 1)`` (renter).
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim.seed import MID_SEED_COLUMNS, select_seed_columns


def test_mid_seed_households_carry_h_miete() -> None:
    """select_seed_columns with extra_household_cols including 'H_MIETE' retains it.

    This exercises the same code path as the H_GR test: select_seed_columns
    is called with a minimal synthetic households frame carrying H_MIETE
    (coded 1=rent, 2=own) and asserts the column survives column selection.
    No real 7 GB MiD files are needed.
    """
    # Minimal MiD households: household ids, weights, H_MIETE (tenure code).
    households = pd.DataFrame({
        "H_ID": [201, 202, 203],
        "H_GEW": [10.0, 20.0, 5.0],
        "H_MIETE": [1, 2, 1],        # 1=renter, 2=owner
        # extra column that should be dropped
        "some_other_col": [9, 9, 9],
    })

    # Minimal MiD persons: one or more persons per household.
    persons = pd.DataFrame({
        "H_ID": [201, 202, 202, 203, 203, 203],
        "P_ID": [1, 2, 3, 4, 5, 6],
        "P_GEW": [10.0, 20.0, 20.0, 5.0, 5.0, 5.0],
        "HP_ALTER": [35, 40, 38, 25, 30, 55],
        "HP_SEX": [1, 1, 2, 2, 1, 1],
    })

    seed_hh, seed_persons = select_seed_columns(
        households,
        persons,
        MID_SEED_COLUMNS,
        extra_household_cols=("H_MIETE",),
    )

    assert "H_MIETE" in seed_hh.columns, (
        f"H_MIETE missing from seed_households columns after select_seed_columns: "
        f"{list(seed_hh.columns)}"
    )
    # Values must be preserved exactly (1=renter, 2=owner).
    expected = {201: 1, 202: 2, 203: 1}
    actual = seed_hh.set_index("H_ID")["H_MIETE"].to_dict()
    assert actual == expected, (
        f"H_MIETE values incorrect: expected {expected}, got {actual}"
    )
    # Unrelated columns must be dropped.
    assert "some_other_col" not in seed_hh.columns, (
        "select_seed_columns should drop columns not in essentials or extras"
    )
    # STAAT sentinel must be present.
    assert "STAAT" in seed_hh.columns, "STAAT geography sentinel missing from seed_households"
