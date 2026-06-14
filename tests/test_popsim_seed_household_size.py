"""Tests that the PopulationSim seed households carry H_GR (household size).

H_GR is required by the Tier-1 household_size controls added in Task 7.  Both
the ENTD seed (build_seed) and the MiD seed (load_mid_seed / select_seed_columns)
must expose H_GR on their seed_households frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# ENTD seed: build_seed must derive and attach H_GR
# ---------------------------------------------------------------------------

def _make_entd_households(hh_ids) -> pd.DataFrame:
    """Minimal ENTD household frame for testing."""
    return pd.DataFrame({
        "household_id": hh_ids,
        "household_weight": [1.0] * len(hh_ids),
        "urban_type": ["C"] * len(hh_ids),
        "number_of_cars": [0] * len(hh_ids),
        "number_of_bicycles": [0] * len(hh_ids),
        "income_class": [5] * len(hh_ids),
    })


def _make_entd_persons(rows) -> pd.DataFrame:
    """Minimal ENTD person frame for testing.

    ``rows`` is a list of (household_id, person_id, sex) tuples.
    """
    hh_ids, p_ids, sexes = zip(*rows)
    return pd.DataFrame({
        "household_id": list(hh_ids),
        "person_id": list(p_ids),
        "person_weight": [1.0] * len(rows),
        "age": [30] * len(rows),
        "sex": list(sexes),
        # ENTD persons carry these for build_seed retention.
        "employed": [True] * len(rows),
        "studies": [False] * len(rows),
        "has_license": [True] * len(rows),
        "has_pt_subscription": [False] * len(rows),
        "socioprofessional_class": [1] * len(rows),
    })


def test_entd_seed_households_carry_h_gr() -> None:
    """build_seed must attach H_GR = persons-per-household to seed_households."""
    from braunschweig.popsim.sources.entd import EntdSource

    # Household 10 has 2 persons; household 20 has 1 person.
    households = _make_entd_households([10, 20])
    persons = _make_entd_persons([
        (10, 1, "male"),
        (10, 2, "female"),
        (20, 3, "male"),
    ])

    source = EntdSource()
    seed_hh, seed_persons, report = source.build_seed(households, persons)

    assert "H_GR" in seed_hh.columns, (
        f"H_GR missing from seed_households columns: {list(seed_hh.columns)}"
    )
    hgr = seed_hh.set_index("H_ID")["H_GR"]
    assert int(hgr[10]) == 2, f"Expected H_GR=2 for household 10, got {hgr[10]}"
    assert int(hgr[20]) == 1, f"Expected H_GR=1 for household 20, got {hgr[20]}"
