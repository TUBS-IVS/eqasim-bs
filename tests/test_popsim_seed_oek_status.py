"""The economic_status x Kreis control (issue #109) needs oek_status on the seed households.

The controls evaluate ``(households.oek_status == k)`` over the PopulationSim seed. Both
seed-build paths must retain oek_status when the control is active, and must NOT change the
seed schema when it is off (OFF byte-identical):
  - project_completed_seed: the default complete_members path (the 100% popsim_mid run); the
    completed-donor households already carry oek_status.
  - select_seed_columns: the generic retention mechanism load_mid_seed relies on.
"""
import pandas as pd

from braunschweig.popsim import sources, mid
from braunschweig.popsim.seed import MID_SEED_COLUMNS, select_seed_columns


def _completed_donor_frames():
    cols = sources.get_source("mid").seed_columns()
    households = pd.DataFrame({
        cols.household_id: ["h1", "h2"],
        cols.household_weight: [1.0, 1.0],
        "H_GR": [1, 2], "H_MIETE": [1, 2], "haustyp": [1, 5], "RegioStaR7": [73, 74],
        "oek_status": [2, 4],
    })
    persons = pd.DataFrame({
        cols.person_household_id: ["h1", "h2", "h2"],
        cols.person_id: ["p1", "p2", "p3"],
        cols.person_weight: [1.0, 1.0, 1.0],
        cols.age: [40, 38, 10],
        cols.sex: [1, 2, 1],
    })
    return cols, households, persons


def test_project_completed_seed_retains_oek_status_only_when_requested():
    cols, households, persons = _completed_donor_frames()
    off_hh, _ = mid.project_completed_seed(households.copy(), persons.copy(), cols)
    on_hh, _ = mid.project_completed_seed(
        households.copy(), persons.copy(), cols, include_status_seed_col=True)
    # OFF: schema unchanged (byte-identical to today).
    assert "oek_status" not in off_hh.columns
    # ON: PopulationSim can evaluate (households.oek_status == k).
    assert "oek_status" in on_hh.columns
    assert on_hh.set_index(cols.household_id)["oek_status"].to_dict() == {"h1": 2, "h2": 4}


def test_select_seed_columns_retains_oek_status_extra():
    households = pd.DataFrame({"H_ID": [1, 2], "H_GEW": [1.0, 1.0], "oek_status": [3, 5]})
    persons = pd.DataFrame({"H_ID": [1, 2], "P_ID": [1, 2], "P_GEW": [1.0, 1.0],
                            "HP_ALTER": [40, 30], "HP_SEX": [1, 2]})
    seed_hh, _ = select_seed_columns(households, persons, MID_SEED_COLUMNS,
                                     extra_household_cols=("oek_status",))
    assert "oek_status" in seed_hh.columns
    assert seed_hh.set_index("H_ID")["oek_status"].to_dict() == {1: 3, 2: 5}
