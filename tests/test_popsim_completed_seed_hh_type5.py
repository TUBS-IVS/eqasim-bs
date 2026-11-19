"""Regression: the complete_members (default) seed-build must carry hh_type5.

The Tier-1 household_type control references ``households.hh_type5``. The
complete_members=True seed path (stage.py) projected the seed WITHOUT deriving
hh_type5 (only load_mid_seed / complete_members=False did), so PopulationSim
crashed with `'DataFrame' object has no attribute 'hh_type5'` once the Tier-1
control was enabled. project_completed_seed mirrors load_mid_seed's projection.
"""
import pandas as pd
import pytest

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


# Members' ages per household -> the expected under-18 minor class (issue #659).
_MINOR_CLASS_HOUSEHOLDS = {
    "1": ([40], "none"),
    "2": ([38, 10], "one_adult"),
    "3": ([40, 38, 10], "multi_adult"),
}


def _write_minor_class_mid(tmp_path):
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID;H_GEW;RegioStaR7;H_GR;H_MIETE;haustyp\n"
        + "".join(f"{hid};1.0;71;{len(ages)};1;1\n"
                  for hid, (ages, _) in _MINOR_CLASS_HOUSEHOLDS.items()),
        encoding="utf-8")
    rows = [(hid, age) for hid, (ages, _) in _MINOR_CLASS_HOUSEHOLDS.items() for age in ages]
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "P_ID;H_ID;P_GEW;HP_ALTER;HP_SEX;kernwo\n"
        + "".join(f"{i};{hid};1.0;{age};1;1\n" for i, (hid, age) in enumerate(rows, start=10)),
        encoding="utf-8")


@pytest.mark.parametrize("seed_path", ["project_completed_seed", "load_mid_seed"])
def test_seed_households_carry_the_minor_class_of_their_members(seed_path, tmp_path):
    # The minor-child controls (#659) evaluate households.hh_minor_class, so every seed
    # path that attaches hh_type5 must attach it too, derived from the members' ages.
    cols = sources.get_source("mid").seed_columns()
    if seed_path == "load_mid_seed":
        _write_minor_class_mid(tmp_path)
        seed_hh, _, _ = mid.load_mid_seed(tmp_path, day_filter_values=())
    else:
        households = pd.DataFrame({
            cols.household_id: list(_MINOR_CLASS_HOUSEHOLDS),
            cols.household_weight: 1.0, "H_GR": 1, "H_MIETE": 1, "haustyp": 1,
            "RegioStaR7": 71,
        })
        rows = [(hid, age) for hid, (ages, _) in _MINOR_CLASS_HOUSEHOLDS.items() for age in ages]
        persons = pd.DataFrame({
            cols.person_household_id: [hid for hid, _ in rows],
            cols.person_id: [f"p{i}" for i in range(len(rows))],
            cols.person_weight: 1.0,
            cols.age: [age for _, age in rows],
            cols.sex: 1,
        })
        seed_hh, _ = mid.project_completed_seed(households, persons, cols)

    assert "hh_type5" in seed_hh.columns
    got = seed_hh.set_index(seed_hh[cols.household_id].astype(str))["hh_minor_class"]
    assert got.to_dict() == {hid: cls for hid, (_, cls) in _MINOR_CLASS_HOUSEHOLDS.items()}
