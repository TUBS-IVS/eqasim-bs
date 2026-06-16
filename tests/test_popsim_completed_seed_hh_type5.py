"""Regression: the complete_members (default) seed-build must carry hh_type5.

The Tier-1 household_type control references ``households.hh_type5``. The
complete_members=True seed path (stage.py) projected the seed WITHOUT deriving
hh_type5 (only load_mid_seed / complete_members=False did), so PopulationSim
crashed with `'DataFrame' object has no attribute 'hh_type5'` once the Tier-1
control was enabled. project_completed_seed mirrors load_mid_seed's projection.
"""
import pandas as pd

from braunschweig.popsim import sources, mid


def test_project_completed_seed_includes_hh_type5_and_raw_cols():
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2"],
        cols.household_weight: [1.0, 1.0],
        "H_GR": [1, 2], "H_MIETE": [1, 2], "haustyp": [1, 5], "RegioStaR7": [73, 74],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h2"],
        cols.person_id: ["p1", "p2", "p3"],
        cols.person_weight: [1.0, 1.0, 1.0],
        cols.age: [40, 38, 10],
        cols.sex: [1, 2, 1],
    })
    seed_hh, seed_p = mid.project_completed_seed(households, persons, cols)
    # the bug: hh_type5 was absent -> PopulationSim could not evaluate the control
    assert "hh_type5" in seed_hh.columns
    # the raw control columns are still retained (tier1 size + tier2 tenure/building)
    assert {"H_GR", "H_MIETE", "haustyp"}.issubset(seed_hh.columns)
    # one label per household, aligned by id
    assert len(seed_hh) == 2


def test_project_completed_seed_retains_tier3_raw_cols():
    # Tier-3 employment/education controls evaluate over raw seed-person cols
    # (P_TAET / bildung1 / bildung2); they must survive into the seed persons.
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2"],
        cols.household_weight: [1.0, 1.0],
        "H_GR": [1, 2], "H_MIETE": [1, 2], "haustyp": [1, 5], "RegioStaR7": [73, 74],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h2"],
        cols.person_id: ["p1", "p2", "p3"],
        cols.person_weight: [1.0, 1.0, 1.0],
        cols.age: [40, 38, 10],
        cols.sex: [1, 2, 1],
        "P_TAET": [1, 11, 9], "bildung1": [4, 3, 1], "bildung2": [3, 1, 5],
    })
    _, seed_p = mid.project_completed_seed(households, persons, cols)
    assert {"P_TAET", "bildung1", "bildung2"}.issubset(seed_p.columns)
