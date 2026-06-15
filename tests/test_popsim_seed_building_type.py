"""Tests that the PopulationSim MiD seed households carry haustyp (building type).

haustyp is required by the Tier-2 building_type controls added in the multi-column
aggregation extension.  The MiD seed (load_mid_seed / select_seed_columns) must expose
haustyp on the seed_households frame so PopulationSim can evaluate the expressions:
  - ``(households.haustyp == 1)``        -> ein-/zweifamilienhaus
  - ``(households.haustyp.isin([2, 3]))`` -> mehrfamilienhaus
  - ``(households.haustyp == 4)``        -> sonstiges
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim.seed import MID_SEED_COLUMNS, select_seed_columns


def test_mid_seed_households_carry_haustyp() -> None:
    """select_seed_columns with extra_household_cols including 'haustyp' retains it.

    This exercises the same code path as the H_MIETE test: select_seed_columns
    is called with a minimal synthetic households frame carrying haustyp
    (1=EFH, 2=MFH, 3=Geschosswohnung, 4=sonstiges, 95=n.z.) and asserts
    the column survives column selection.  No real MiD files are needed.
    """
    # Minimal MiD households: household ids, weights, haustyp.
    households = pd.DataFrame({
        "H_ID": [301, 302, 303, 304],
        "H_GEW": [10.0, 20.0, 5.0, 8.0],
        "haustyp": [1, 2, 4, 3],   # EFH, MFH, sonstiges, Geschosswohnung
        "irrelevant_col": [0, 0, 0, 0],
    })

    # Minimal MiD persons: one person per household.
    persons = pd.DataFrame({
        "H_ID": [301, 302, 303, 304],
        "P_ID": [1, 2, 3, 4],
        "P_GEW": [10.0, 20.0, 5.0, 8.0],
        "HP_ALTER": [35, 40, 25, 30],
        "HP_SEX": [1, 2, 1, 2],
    })

    seed_hh, _ = select_seed_columns(
        households,
        persons,
        MID_SEED_COLUMNS,
        extra_household_cols=("haustyp",),
    )

    assert "haustyp" in seed_hh.columns, (
        f"haustyp missing from seed_households columns after select_seed_columns: "
        f"{list(seed_hh.columns)}"
    )
    # Values must be preserved exactly.
    expected = {301: 1, 302: 2, 303: 4, 304: 3}
    actual = seed_hh.set_index("H_ID")["haustyp"].to_dict()
    assert actual == expected, (
        f"haustyp values incorrect: expected {expected}, got {actual}"
    )
    # Unrelated columns must be dropped.
    assert "irrelevant_col" not in seed_hh.columns, (
        "select_seed_columns should drop columns not in essentials or extras"
    )
    # STAAT sentinel must be present.
    assert "STAAT" in seed_hh.columns, "STAAT geography sentinel missing from seed_households"
