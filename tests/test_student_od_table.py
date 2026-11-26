"""Tests of the student origin-destination table for the student layer (#140, Phase 2).

The CHE origin cells are tested on a small synthetic table whose expected cells are computed by
hand (see :func:`_che_toy`); one test reads the committed CHE table, one reads the institution
names pinned in the two seed scripts.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.data.education import student_od_table as T

ROOT = Path(__file__).resolve().parents[1]
CHE_TABLE = ROOT / "eqasim-data" / "data" / "braunschweig" / "schools" / "che_student_origins_ws2022_23.csv"

CHOICE_SET = ["Uni A", "Uni B", "Art C"]
DISTANCE = ("Distance D",)


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)  # never registered in sys.modules
    spec.loader.exec_module(module)
    return module


def _list_rows(view, entries, total, *, institution=None, kreis=None):
    """Rows of one CHE top list: ``entries`` are (institution or Kreis, persons) in rank order."""
    rows = []
    for rank, (key, persons) in enumerate(entries, start=1):
        inst, ars5 = (institution, key) if view == "institution_origins" else (key, kreis)
        rows.append({"view": view, "institution": inst, "origin_ars5": ars5, "origin_name": "name",
                     "rank": str(rank), "row_kind": "listed", "students_persons": str(persons)})
    rows.append({"view": view, "institution": institution, "origin_ars5": kreis, "origin_name": None,
                 "rank": None, "row_kind": "total", "students_persons": str(total)})
    return rows


def _che_toy(conflict: bool = False) -> pd.DataFrame:
    """Synthetic CHE table, all values as strings (as read with ``dtype=str``).

    Institution view: Uni A has ten rows (tenth value 30), Uni B a short list of three rows.
    Art C (the art-academy case) and Distance D have no own list.
    Kreis view: 03101, 03158 and 01002 have ten rows (tenth values 20, 12 and 5), 03153 a short
    list of two rows; 05111 and 01001 have no Kreis list.
    """
    uni_a = [("03101", 100), ("01001", 90), ("01002", 80), ("01003", 70), ("01004", 60),
             ("01005", 50), ("01006", 45), ("01007", 40), ("01008", 35), ("01009", 30)]
    uni_b = [("03101", 20), ("01001", 15), ("01002", 10)]
    k03101 = [("Uni A", 99 if conflict else 100), ("Art C", 60), ("Distance D", 55), ("Other E", 50),
              ("F1", 49), ("F2", 48), ("F3", 47), ("F4", 46), ("F5", 45), ("Uni B", 20)]
    k03158 = [("Distance D", 40), ("Other E", 30), ("G1", 28), ("G2", 26), ("G3", 24), ("G4", 20),
              ("G5", 18), ("G6", 16), ("Art C", 14), ("G7", 12)]
    k03153 = [("Uni B", 8), ("Uni A", 5)]
    k01002 = [(f"H{i}", 15 - i) for i in range(1, 11)]  # H1 14 ... H10 5, neither Uni A nor Uni B
    rows = (_list_rows("institution_origins", uni_a, 1000, institution="Uni A")
            + _list_rows("institution_origins", uni_b, 200, institution="Uni B")
            + _list_rows("kreis_destinations", k03101, 2000, kreis="03101")
            + _list_rows("kreis_destinations", k03158, 900, kreis="03158")
            + _list_rows("kreis_destinations", k03153, 300, kreis="03153")
            + _list_rows("kreis_destinations", k01002, 400, kreis="01002"))
    return pd.DataFrame(rows, columns=["view", "institution", "origin_ars5", "origin_name", "rank",
                                       "row_kind", "students_persons"])


@pytest.fixture
def che_toy() -> pd.DataFrame:
    return _che_toy()


def _cells(che, origin_kreise, region_kreise=("03101",), institutions=CHOICE_SET, distance=DISTANCE):
    cells, diagnostics = T.origin_cells(che, list(institutions), list(origin_kreise), list(region_kreise),
                                        distance)
    return cells.set_index(["origin_ars5", "institution"]), diagnostics


def test_cell_in_both_views_is_taken_once_and_a_conflict_raises(che_toy):
    cells, diagnostics = T.origin_cells(che_toy, ["Uni A"], ["03101", "03158"], ["03101"], ())
    assert list(cells.columns) == T.CELL_COLUMNS
    assert len(cells) == 2  # one row per (origin Kreis, institution), never one per view
    row = cells.set_index(["origin_ars5", "institution"]).loc[("03101", "Uni A")]
    assert (row["che_persons"], row["source"], row["upper_bound_persons"]) == (100, "both_views", 100)
    assert diagnostics["cells_per_source"] == {"institution_view": 0, "kreis_view": 0, "both_views": 1,
                                               "censored": 1}
    with pytest.raises(ValueError, match="03101"):
        T.origin_cells(_che_toy(conflict=True), ["Uni A"], ["03101"], ["03101"], ())


def test_censored_cell_bound_is_the_smaller_tenth_value(che_toy):
    cells, diagnostics = _cells(che_toy, ["03158", "05111"])
    # 03158 x Uni A: in neither list; Uni A's tenth value 30, 03158's tenth value 12 -> 12.
    row = cells.loc[("03158", "Uni A")]
    assert (row["che_persons"], row["source"], row["upper_bound_persons"]) == (0, "censored", 12)
    # 05111 has no Kreis list: only Uni A's own list bounds the cell.
    assert cells.loc[("05111", "Uni A"), "upper_bound_persons"] == 30
    assert diagnostics["origin_kreise_without_kreis_list"] == ["05111"]


def test_list_with_fewer_than_ten_rows_bounds_its_missing_cells_at_zero(che_toy):
    cells, diagnostics = _cells(che_toy, ["03158", "05111", "03153"])
    # Uni B's own list has three rows: every Uni B cell it does not show is zero.
    assert cells.loc[("03158", "Uni B"), "upper_bound_persons"] == 0
    assert cells.loc[("05111", "Uni B"), "upper_bound_persons"] == 0
    # 03153's Kreis list has two rows: Art C (no own list) is bounded at zero by it.
    row = cells.loc[("03153", "Art C")]
    assert (row["source"], row["upper_bound_persons"]) == ("censored", 0)
    # 03153 x Uni B = 8 is shown only in the Kreis view although Uni B's short list shows every cell
    # (bound 0): a transcription signal. 03153 x Uni A = 5 stays below Uni A's tenth value 30.
    assert cells.loc[("03153", "Uni B"), "source"] == "kreis_view"
    flagged = diagnostics["one_view_cells_above_other_tenth"]
    assert [(c["origin_ars5"], c["institution"], c["che_persons"], c["other_list_bound_persons"])
            for c in flagged] == [("03153", "Uni B", 8, 0)]


def test_institution_view_only_cell_is_taken_and_flagged_above_a_full_kreis_list(che_toy):
    cells, diagnostics = _cells(che_toy, ["01001", "01002"])
    # 01001 has no Kreis list: the own lists of Uni A (90) and Uni B (15) give the cells.
    row = cells.loc[("01001", "Uni A")]
    assert (row["che_persons"], row["source"], row["upper_bound_persons"]) == (90, "institution_view", 90)
    assert cells.loc[("01001", "Uni B"), "source"] == "institution_view"
    # 01002's full Kreis list (tenth value 5) misses Uni A (80) and Uni B (10): both are transcription signals.
    flagged = {(c["origin_ars5"], c["institution"], c["che_persons"], c["source"], c["other_list_bound_persons"])
               for c in diagnostics["one_view_cells_above_other_tenth"]}
    assert flagged == {("01002", "Uni A", 80, "institution_view", 5), ("01002", "Uni B", 10, "institution_view", 5)}
    assert diagnostics["cells_per_source"] == {"institution_view": 4, "kreis_view": 0, "both_views": 0,
                                               "censored": 2}


def test_institution_without_own_list_uses_kreis_view_cells(che_toy):
    cells, diagnostics = _cells(che_toy, ["03101", "03158", "05111"])
    assert cells.loc[("03101", "Art C"), ["che_persons", "source"]].tolist() == [60, "kreis_view"]
    assert cells.loc[("03158", "Art C"), ["che_persons", "source"]].tolist() == [14, "kreis_view"]
    # No own list and no Kreis list: censored without any bound, and counted.
    row = cells.loc[("05111", "Art C")]
    assert (row["che_persons"], row["source"]) == (0, "censored")
    assert math.isnan(row["upper_bound_persons"])
    assert diagnostics["censored_cells_without_bound"] == 1
    # 9 cells: both views (03101: Uni A, Uni B), Kreis view (Art C in 03101, 03158), the rest censored.
    assert diagnostics["cells_per_source"] == {"institution_view": 0, "kreis_view": 2, "both_views": 2,
                                               "censored": 5}
    # Censored bounds: 03158 x Uni A 12, 03158 x Uni B 0, 05111 x Uni A 30, 05111 x Uni B 0, 05111 x Art C none.
    assert diagnostics["censored_upper_bound_persons"] == 42
    # The art academy has no institution-view total.
    assert T.che_totals(che_toy).to_dict() == {"Uni A": 1000, "Uni B": 200}


def test_distance_learning_cells_are_dropped_and_counted(che_toy):
    cells, diagnostics = _cells(che_toy, ["03101", "03158", "05111"])
    assert "Distance D" not in cells.index.get_level_values("institution")
    assert diagnostics["distance_learning_cells_dropped"] == (2, 95)  # 55 in 03101, 40 in 03158
    with pytest.raises(ValueError, match="Distance D"):
        _cells(che_toy, ["03101"], institutions=["Uni A", "Distance D"])
    with pytest.raises(ValueError, match="Distance Z"):  # a misspelt name must not drop nothing silently
        _cells(che_toy, ["03101"], distance=("Distance D", "Distance Z"))


def test_listed_institution_outside_the_choice_set_is_named_with_its_persons(che_toy):
    _, diagnostics = _cells(che_toy, ["03101"], region_kreise=["03101", "03158"])
    assert diagnostics["listed_outside_choice_set"] == {
        "Other E": 80, "F1": 49, "F2": 48, "F3": 47, "F4": 46, "F5": 45,
        "G1": 28, "G2": 26, "G3": 24, "G4": 20, "G5": 18, "G6": 16, "G7": 12}
    # Choice-set cells listed for Kreise other than 03101 are counted, not lost: Uni A 01001-01009 (500)
    # and 03153 (5), Uni B 01001, 01002, 03153 (15 + 10 + 8), Art C 03158 (14).
    assert diagnostics["shown_cells_outside_origin_kreise"] == (14, 552)


@pytest.mark.parametrize("corrupt, match", [
    (lambda c: c.drop(columns="row_kind"), "row_kind"),
    (lambda c: pd.concat([c, c.iloc[[0]]], ignore_index=True), "duplicate"),
    (lambda c: c.assign(origin_ars5=c["origin_ars5"].str.lstrip("0")), "origin_ars5"),
    (lambda c: c.assign(students_persons=c["students_persons"].replace("90", "-90")), "students_persons"),
])
def test_invalid_che_table_raises(che_toy, corrupt, match):
    with pytest.raises(ValueError, match=match):
        _cells(corrupt(che_toy), ["03101"])


@pytest.mark.parametrize("corrupt, match", [
    (lambda c: c[~((c["institution"] == "Uni B") & (c["row_kind"] == "total"))], "Uni B"),
    (lambda c: pd.concat([c, c[(c["institution"] == "Uni A") & (c["row_kind"] == "total")]]), "Uni A"),
    (lambda c: pd.concat([c, pd.DataFrame([{"view": "institution_origins", "institution": "Art C",
                                            "row_kind": "total", "students_persons": "7"}])]), "Art C"),
])
def test_che_totals_need_exactly_one_total_per_own_list(che_toy, corrupt, match):
    with pytest.raises(ValueError, match=match):
        T.che_totals(corrupt(che_toy))


def test_unknown_choice_set_institution_raises(che_toy):
    with pytest.raises(ValueError, match="Uni Z"):
        _cells(che_toy, ["03101"], institutions=["Uni A", "Uni Z"])


def test_real_che_table_reads_without_conflict_and_every_own_list_has_a_crosswalk_entry():
    che = pd.read_csv(CHE_TABLE, comment="#", dtype=str)
    crosswalk_keys = set(T.INSTITUTION_CROSSWALK) | set(T.OUTSIDE_LSN_CROSSWALK)
    assert not set(T.INSTITUTION_CROSSWALK) & set(T.OUTSIDE_LSN_CROSSWALK)
    own_lists = set(che.loc[che["view"] == "institution_origins", "institution"])
    assert own_lists <= crosswalk_keys
    assert crosswalk_keys <= set(che["institution"].dropna())  # no typo in a crosswalk key
    assert set(T.che_totals(che).index) == own_lists
    kreise = sorted(che.loc[che["view"] == "kreis_destinations", "origin_ars5"].dropna().unique())
    cells, diagnostics = T.origin_cells(che, sorted(crosswalk_keys), kreise, list(ZGB_KREISE),
                                        T.DISTANCE_LEARNING_DEFAULT)
    assert len(cells) == len(kreise) * len(crosswalk_keys)
    assert sum(diagnostics["cells_per_source"].values()) == len(cells)
    assert diagnostics["origin_kreise_without_kreis_list"] == []


def test_crosswalk_targets_exist_in_the_seed_scripts():
    lsn_sites = _load_script("seed_lsn_university_sites")
    nds = _load_script("seed_nds_hochschulen")
    assert set(T.INSTITUTION_CROSSWALK.values()) == {row[0] for row in lsn_sites.SITE_ROWS}
    assert set(T.OUTSIDE_LSN_CROSSWALK.values()) <= {row[0] for row in nds._SURROUNDING}
