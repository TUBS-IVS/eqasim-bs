import pandas as pd

from braunschweig.popsim import handoff as H


def test_handoff_assignment_unchanged_with_progress(capsys):
    buildings = pd.DataFrame({
        "cell_id": ["A", "A", "B"],
        "has_home": [True, False, True],
    })
    hh_by_cell = {"A": [1, 2, 3], "B": [4], "C": [5]}  # C has no buildings -> orphan
    # Real signature: assign_households_to_buildings(hh_by_cell, buildings, ...)
    result, report = H.assign_households_to_buildings(hh_by_cell, buildings)
    assert report.n_cells_with_households == 3
    assert report.n_households_orphan == 1   # household 5 in empty cell C
    assert report.n_cells_orphan == 1
