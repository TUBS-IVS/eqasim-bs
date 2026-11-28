"""Tests of the student origin-destination table for the student layer (#140, Phase 2).

The CHE origin cells are tested on a small synthetic table whose expected cells are computed by
hand (see :func:`_che_toy`); one test reads the committed CHE table, one reads the institution
names pinned in the two seed scripts. The builder T, the movers step and the balance are tested on
toy inputs of one to three sites (see :func:`_toy_input`) whose expected values are computed by
hand in the comments; Kreis codes are single letters there because the builder treats them as
opaque keys.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.calibration.srv_distance_targets import ZGB_KREISE
from braunschweig.data.education import student_od_table as T
from braunschweig.data.education.student_geometry import PAIR_COLUMNS

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
    (lambda c: pd.concat([c, pd.DataFrame([{"view": "institution_origins", "institution": None,
                                            "row_kind": "total", "students_persons": "7"}])]), "missing"),
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


# --- the builder T, the movers step, the balance ---------------------------------------------------------

WEIGHTS = T.CommuterWeights(0.5, 0.3, 0.1)
SHORT, MIDDLE, LONG, NONE = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)


def _shown(ars5, institution, persons, source="both_views"):
    return (ars5, institution, persons, source, float(persons))


def _censored(ars5, institution, bound):
    return (ars5, institution, 0, "censored", float(bound))


def _site(destination_id, institution, kreis, scope, enrollment):
    return {"destination_id": destination_id, "che_institution": institution, "institution": f"LSN {institution}",
            "site_ags8": kreis + "001", "site_kreis_ars5": kreis, "scope": scope, "students_persons": enrollment}


def _pair(ars5, destination_id, distance_km, coefficients):
    return {"ars5": ars5, "destination_id": destination_id, "distance_km": distance_km,
            "coeff_short": coefficients[0], "coeff_middle": coefficients[1], "coeff_long": coefficients[2]}


def _toy_input(cells, totals, sites, pairs, region):
    return {"cells": pd.DataFrame(cells, columns=T.CELL_COLUMNS), "che_totals": pd.Series(totals, dtype="int64"),
            "sites": pd.DataFrame(sites), "pairs": pd.DataFrame(pairs, columns=PAIR_COLUMNS),
            "region_kreise": list(region)}


# S1 of Uni S in Kreis A: enrollment 1000, CHE total 500 -> scale 2. Region A, B; C lies outside.
TOY_CELLS = [_shown("A", "Uni S", 100), _shown("B", "Uni S", 50), _shown("C", "Uni S", 40)]
TOY_SITES = [_site("S1", "Uni S", "A", "local", 1000.0)]
TOY_PAIRS = [_pair("A", "S1", 3.0, SHORT), _pair("B", "S1", 15.0, SHORT), _pair("C", "S1", 80.0, LONG)]


def _at(od, home, destination, column):
    return od.set_index(["home_ars5", "destination_id"]).loc[(home, destination), column]


def origin_scaled(od, home, destination):
    return _at(od, home, destination, "origin_persons_scaled")


@pytest.fixture
def toy():
    return _toy_input(TOY_CELLS, {"Uni S": 500}, TOY_SITES, TOY_PAIRS, ["A", "B"])


@pytest.fixture
def toy_with_surrounding():
    # Plus S9 of Uni M in Kreis X outside the region, without enrollment (outside LSN). The pair (C, S9) is
    # absent on purpose: an outside Kreis never gets a row for a single surrounding site.
    cells = TOY_CELLS + [_shown("A", "Uni M", 30), _shown("B", "Uni M", 20), _shown("C", "Uni M", 60)]
    sites = TOY_SITES + [_site("S9", "Uni M", "X", "surrounding", math.nan)]
    pairs = TOY_PAIRS + [_pair("A", "S9", 40.0, MIDDLE), _pair("B", "S9", 70.0, LONG)]
    return _toy_input(cells, {"Uni S": 500, "Uni M": 900}, sites, pairs, ["A", "B"])


@pytest.fixture
def toy_art():
    # Plus S6 of the art academy Art K in Kreis A (enrollment 200): no CHE total, Kreis-view cells only.
    cells = TOY_CELLS + [_shown("A", "Art K", 30, "kreis_view"), _shown("B", "Art K", 20, "kreis_view"),
                         _shown("C", "Art K", 10, "kreis_view")]
    sites = TOY_SITES + [_site("S6", "Art K", "A", "local", 200.0)]
    pairs = TOY_PAIRS + [_pair("A", "S6", 2.0, SHORT), _pair("B", "S6", 15.0, SHORT), _pair("C", "S6", 80.0, LONG)]
    return _toy_input(cells, {"Uni S": 500}, sites, pairs, ["A", "B"])


def _toy_multi(censored):
    # Uni I: S2 in Kreis B (E 300), S3 in Kreis D (E 100); CHE total 200 -> scale 400 / 200 = 2. Region B, D;
    # E lies outside, beyond reach of both sites (every coefficient 0). ``censored``: D's cell is censored at 6
    # and E's cell censored without any bound.
    d_cell = _censored("D", "Uni I", 6) if censored else _shown("D", "Uni I", 4)
    e_cell = _censored("E", "Uni I", math.nan) if censored else _shown("E", "Uni I", 10)
    cells = [_shown("B", "Uni I", 8), d_cell, e_cell]
    sites = [_site("S2", "Uni I", "B", "local", 300.0), _site("S3", "Uni I", "D", "local", 100.0)]
    pairs = [_pair("B", "S2", 2.0, SHORT), _pair("B", "S3", 30.0, MIDDLE), _pair("D", "S2", 30.0, MIDDLE),
             _pair("D", "S3", 2.0, SHORT), _pair("E", "S2", 150.0, NONE), _pair("E", "S3", 160.0, NONE)]
    return _toy_input(cells, {"Uni I": 200}, sites, pairs, ["B", "D"])


@pytest.fixture
def toy_multi():
    return _toy_multi(censored=False)


@pytest.fixture
def toy_censored():
    return _toy_multi(censored=True)


@pytest.fixture
def toy_multi_scope():
    return _toy_multi_scope()


def _toy_multi_scope():
    # Uni J: S4 in Kreis A (local, E 600), S5 in Kreis X outside the region (surrounding, E 200); CHE total 400
    # -> scale 2. Region A, B; C lies outside, near X.
    cells = [_shown("A", "Uni J", 50), _shown("B", "Uni J", 30), _shown("C", "Uni J", 20)]
    sites = [_site("S4", "Uni J", "A", "local", 600.0), _site("S5", "Uni J", "X", "surrounding", 200.0)]
    pairs = [_pair("A", "S4", 2.0, SHORT), _pair("A", "S5", 120.0, NONE), _pair("B", "S4", 15.0, SHORT),
             _pair("B", "S5", 40.0, MIDDLE), _pair("C", "S4", 80.0, LONG), _pair("C", "S5", 15.0, SHORT)]
    return _toy_input(cells, {"Uni J": 400}, sites, pairs, ["A", "B"])


def test_single_site_by_hand(toy):
    od, diagnostics = T.build_od_table(**toy, weights=T.CommuterWeights(0.5, 0.3, 0.1))
    assert list(od.columns) == T.T_COLUMNS and len(od) == 3
    t = od.set_index(["home_ars5", "destination_id"])
    assert t.loc[("B", "S1"), "students_persons"] == pytest.approx(50.0)     # 50 * 2 * 0.5
    assert t.loc[("C", "S1"), "students_persons"] == pytest.approx(8.0)      # 40 * 2 * 0.1
    assert t.loc[("A", "S1"), "students_persons"] == pytest.approx(942.0)    # 1000 - 50 - 8
    assert t.loc[("A", "S1"), "movers_persons"] == pytest.approx(742.0)      # 942 - 100 * 2
    assert t["cell_kind"].to_dict() == {("A", "S1"): "local", ("B", "S1"): "commuter", ("C", "S1"): "commuter"}
    assert t["weight"].to_dict() == {("A", "S1"): 1.0, ("B", "S1"): 0.5, ("C", "S1"): 0.1}
    assert t["home_in_region"].to_dict() == {("A", "S1"): True, ("B", "S1"): True, ("C", "S1"): False}
    assert t.loc[[("B", "S1"), ("C", "S1")], "movers_persons"].tolist() == [0.0, 0.0]
    assert diagnostics["scale_factors"] == {"Uni S": 2.0}
    site = T.balance_tables(od, ["A", "B"])["by_site"].set_index("destination_id").loc["S1"]
    assert (site["residents_region_persons"], site["in_commuters_persons"]) == (pytest.approx(992.0), pytest.approx(8.0))
    assert site["enrollment_persons"] == site["total_persons"] == pytest.approx(1000.0)


def test_multi_site_split_uses_weight_one_in_the_own_kreis(toy_multi):
    # institution I: S2 in Kreis B (E 300), S3 in Kreis D (E 100); origin B 16 (scaled),
    # pair (B, S3) coefficients (0, 1, 0) -> weight 0.3
    od, diagnostics = T.build_od_table(**toy_multi, weights=T.CommuterWeights(0.5, 0.3, 0.1))
    assert origin_scaled(od, "B", "S2") == pytest.approx(16 * 300 / 330)
    assert origin_scaled(od, "B", "S3") == pytest.approx(16 * 30 / 330)
    # Origin D: 4 * 2 = 8 over S2 (v 0.3) and S3 (own Kreis, v 1): 300 * 0.3 : 100 * 1.
    assert origin_scaled(od, "D", "S2") == pytest.approx(8 * 90 / 190)
    # Origin E lies beyond reach of both sites (every v 0): split by enrollment alone, 20 * 300 / 400, counted.
    assert (origin_scaled(od, "E", "S2"), origin_scaled(od, "E", "S3")) == (pytest.approx(15.0), pytest.approx(5.0))
    assert _at(od, "E", "S2", "students_persons") == 0.0
    assert diagnostics["multi_site_split_cells"] == {"by_weight": 2, "by_enrollment_only": 1}


def test_multi_site_split_spans_local_and_surrounding_sites(toy_multi_scope):
    od, _ = T.build_od_table(**toy_multi_scope, weights=WEIGHTS)
    t = od.set_index(["home_ars5", "destination_id"])
    # B: 60 over S4 (v 0.5) and S5 (v 0.3): 600 * 0.5 : 200 * 0.3 = 300 : 60 -> 50 and 10.
    assert (origin_scaled(od, "B", "S4"), origin_scaled(od, "B", "S5")) == (pytest.approx(50.0), pytest.approx(10.0))
    # C: 40 over S4 (v 0.1) and S5 (v 0.5): 60 : 100 -> 15 to the local site; its 25 for S5 get no row.
    assert origin_scaled(od, "C", "S4") == pytest.approx(15.0) and ("C", "S5") not in t.index
    # A: all 100 to S4 (own Kreis, v 1; S5 beyond reach, v 0).
    assert origin_scaled(od, "A", "S4") == pytest.approx(100.0)
    assert t.loc[("A", "S4"), "students_persons"] == pytest.approx(573.5)  # 600 - 50 * 0.5 - 15 * 0.1
    assert t.loc[("A", "S4"), "movers_persons"] == pytest.approx(473.5)    # 573.5 - 100
    assert t.loc[("B", "S5"), "students_persons"] == pytest.approx(3.0)    # 10 * 0.3
    assert t.loc[("A", "S5"), "students_persons"] == 0.0                   # zero row kept
    assert t.loc[[("A", "S5"), ("B", "S5")], "cell_kind"].tolist() == ["out_commuter", "out_commuter"]


def test_surrounding_columns_hold_region_rows_only(toy_with_surrounding):
    od, diagnostics = T.build_od_table(**toy_with_surrounding, weights=WEIGHTS)
    s9 = od[od["destination_id"] == "S9"].set_index("home_ars5")
    assert sorted(s9.index) == ["A", "B"] and set(s9["cell_kind"]) == {"out_commuter"}
    # Uni M has no enrollment (outside LSN): unscaled and counted. A: 30 * 0.3 = 9, B: 20 * 0.1 = 2.
    assert s9["students_persons"].to_dict() == {"A": pytest.approx(9.0), "B": pytest.approx(2.0)}
    assert diagnostics["institutions_without_enrollment"] == ["Uni M"]
    assert diagnostics["scale_factors"] == {"Uni S": 2.0, "Uni M": 1.0}
    balance = T.balance_tables(od, ["A", "B"])  # 11 persons against a NaN enrollment: no error
    assert list(balance["by_site"]["destination_id"]) == ["S1"]
    assert balance["out_commuters"].set_index("destination_id").loc["S9", "out_commuters_persons"] == pytest.approx(11.0)


def test_pair_weight_is_the_coefficients_times_the_triple_and_a_bad_triple_raises():
    pairs = pd.DataFrame([_pair("A", "S1", 20.0, (0.5, 0.5, 0.0)), _pair("B", "S1", 200.0, NONE)], columns=PAIR_COLUMNS)
    assert T.pair_weight(pairs, T.CommuterWeights(0.6, 0.2, 0.1)).tolist() == [pytest.approx(0.4), 0.0]
    for bad in [(0.2, 0.5, 0.1), (1.2, 0.5, 0.1), (0.5, 0.3, -0.1), (0.5, math.nan, 0.1)]:
        with pytest.raises(ValueError, match="weight"):
            T.validate_weights(T.CommuterWeights(*bad))
        with pytest.raises(ValueError, match="weight"):
            T.pair_weight(pairs, T.CommuterWeights(*bad))


def test_pair_missing_from_the_coefficients_raises(toy):
    pairs = toy["pairs"]
    toy["pairs"] = pairs[~((pairs["ars5"] == "C") & (pairs["destination_id"] == "S1"))]
    with pytest.raises(ValueError, match=r"'C', 'S1'"):
        T.build_od_table(**toy, weights=WEIGHTS)


def _multi_site_without_enrollment(_):
    # A multi-site institution needs every site's enrollment for the split, a surrounding one's too.
    toy = _toy_multi_scope()
    return {**toy, "sites": toy["sites"].assign(students_persons=[600.0, math.nan])}


@pytest.mark.parametrize("change, match", [
    (_multi_site_without_enrollment, "S5"),
    # A surrounding site inside the region contradicts its scope.
    (lambda toy: {**toy, "sites": toy["sites"].assign(scope=["surrounding", "local"])}, "S2"),
    # A region Kreis without origin cells would lose its rows.
    (lambda toy: {**toy, "region_kreise": ["B", "D", "Z"]}, "Z"),
])
def test_invalid_builder_input_raises(toy_multi, change, match):
    with pytest.raises(ValueError, match=match):
        T.build_od_table(**change(toy_multi), weights=WEIGHTS)


def _big_b_cell(toy):
    # Cell B 1500 -> 3000 scaled -> 1500 commuters at weight 0.5, more than the enrollment 1000.
    cells = toy["cells"].copy()
    cells.loc[cells["origin_ars5"] == "B", ["che_persons", "upper_bound_persons"]] = [1500, 1500.0]
    return {**toy, "cells": cells}


def _small_che_total(toy):
    # CHE total 50 -> scale 20: local 1000 - 1000 * 0.5 - 800 * 0.1 = 420, below the own-Kreis origin 2000.
    return {**toy, "che_totals": pd.Series({"Uni S": 50})}


@pytest.mark.parametrize("change, column", [(_big_b_cell, "students_persons"), (_small_che_total, "movers_persons")])
def test_negative_local_count_raises(toy, change, column):
    with pytest.raises(ValueError, match=column):
        T.build_od_table(**change(toy), weights=WEIGHTS)


def test_local_count_within_the_tolerance_below_zero_is_exactly_zero():
    def build(enrollment):
        # Art Z without a CHE total (factor 1); its own-Kreis cell A is censored (origin 0, so movers = local).
        cells = [_censored("A", "Art Z", 5), _shown("B", "Art Z", 100)]
        sites = [_site("S1", "Art Z", "A", "local", enrollment)]
        pairs = [_pair("A", "S1", 3.0, SHORT), _pair("B", "S1", 15.0, SHORT)]
        return T.build_od_table(**_toy_input(cells, {}, sites, pairs, ["A", "B"]), weights=WEIGHTS)[0]

    od = build(50.0 - 5e-7)  # commuters 100 * 0.5 = 50 exceed the enrollment by 5e-7: rounding noise
    assert _at(od, "A", "S1", "students_persons") == 0.0 and _at(od, "A", "S1", "movers_persons") == 0.0
    with pytest.raises(ValueError, match="students_persons"):
        build(50.0 - 2e-6)  # beyond the tolerance of 1e-6 persons


def test_duplicate_row_key_raises(toy):
    od, _ = T.build_od_table(**toy, weights=WEIGHTS)
    with pytest.raises(ValueError, match="home_ars5.*destination_id.*cell_kind"):
        T._require_unique_row_key(pd.concat([od, od.iloc[[1]]], ignore_index=True), "test")


def test_apply_movers_moves_a_share_of_the_movers_and_keeps_every_site_total(toy):
    od, _ = T.build_od_table(**toy, weights=T.CommuterWeights(0.5, 0.3, 0.1))
    moved = T.apply_movers(od, 0.5, ("A",), "B")
    t = moved.groupby(["home_ars5", "cell_kind"])["students_persons"].sum()
    assert t[("B", "mover_to_braunschweig")] == pytest.approx(371.0) and t[("A", "local")] == pytest.approx(571.0)
    assert T.apply_movers(od, 0.0, ("A",), "B").equals(od)
    assert list(moved.columns) == T.T_COLUMNS and moved["movers_persons"].sum() == pytest.approx(742.0)
    # The NaN columns of the mover rows must stay float: an object column escapes the writer's %.10g format.
    numeric = ["enrollment_persons", "distance_km", "coeff_short", "coeff_middle", "coeff_long", "origin_persons_scaled",
               "upper_bound_persons_scaled", "weight", "students_persons", "movers_persons"]
    assert all(pd.api.types.is_float_dtype(moved[column]) for column in numeric), moved.dtypes.to_dict()
    geometry = ["distance_km", "coeff_short", "coeff_middle", "coeff_long"]
    mover = moved[moved["cell_kind"] == "mover_to_braunschweig"].iloc[0]
    commuter = od[(od["home_ars5"] == "B") & (od["destination_id"] == "S1")].iloc[0]
    assert mover[geometry].tolist() == commuter[geometry].tolist() and math.isnan(mover["weight"])
    site = T.balance_tables(moved, ["A", "B"])["by_site"].iloc[0]  # B lies in the region: 992 residents still
    assert (site["residents_region_persons"], site["total_persons"]) == (pytest.approx(992.0), pytest.approx(1000.0))
    with pytest.raises(ValueError, match="B"):
        T.apply_movers(od, 0.5, ("A", "B"), "B")
    with pytest.raises(ValueError, match="share"):
        T.apply_movers(od, 1.5, ("A",), "B")


def test_balance_reads_a_table_written_to_csv(toy_with_surrounding, tmp_path):
    od, _ = T.build_od_table(**toy_with_surrounding, weights=WEIGHTS)
    moved = T.apply_movers(od, 0.5, ("A",), "B")
    moved.to_csv(tmp_path / "od.csv", index=False)
    codes = {column: str for column in ["home_ars5", "destination_id", "site_kreis_ars5"]}
    back = pd.read_csv(tmp_path / "od.csv", dtype=codes)
    expected, read = T.balance_tables(moved, ["A", "B"]), T.balance_tables(back, ["A", "B"])
    for key in ["by_site", "out_commuters", "in_commuters_by_origin", "by_home_kreis"]:
        pd.testing.assert_frame_equal(read[key], expected[key])
    assert read["diagnostics"] == expected["diagnostics"]
    # The enrollment is a column: a row whose site disagrees with the other rows is caught.
    back.loc[back["destination_id"] == "S1", "enrollment_persons"] = [1000.0, 1000.0, 999.0, 1000.0]
    with pytest.raises(ValueError, match="enrollment_persons"):
        T.balance_tables(back, ["A", "B"])


def test_art_academy_unscaled_and_rest_local_with_counts(toy_art):
    od, diagnostics = T.build_od_table(**toy_art, weights=WEIGHTS)
    t = od.set_index(["home_ars5", "destination_id"])
    # Art K keeps factor 1: B 20 * 0.5 = 10, C 10 * 0.1 = 1, the rest 200 - 11 = 189 local, movers 189 - 30.
    assert t.loc[("B", "S6"), "students_persons"] == pytest.approx(10.0)
    assert t.loc[("C", "S6"), "students_persons"] == pytest.approx(1.0)
    assert t.loc[("A", "S6"), ["students_persons", "movers_persons"]].tolist() == [pytest.approx(189.0),
                                                                                    pytest.approx(159.0)]
    assert t.loc[("A", "S1"), "students_persons"] == pytest.approx(942.0)  # Uni S keeps its scale 2
    assert diagnostics["scale_factors"] == {"Uni S": 2.0, "Art K": 1.0}
    assert diagnostics["institutions_without_che_total"] == ["Art K"]
    assert diagnostics["institutions_without_enrollment"] == []


def test_censored_bound_passes_through_scale_split_and_weight(toy_censored):
    od, diagnostics = T.build_od_table(**toy_censored, weights=WEIGHTS)
    # D's cell is censored at 6 -> 12 scaled, split like a count: S2 v 0.3 (300 * 0.3 = 90), S3 own Kreis (100).
    assert origin_scaled(od, "D", "S2") == 0.0
    assert _at(od, "D", "S2", "upper_bound_persons_scaled") == pytest.approx(12 * 90 / 190)
    by_site = T.balance_tables(od, ["B", "D"])["by_site"].set_index("destination_id")
    bounds = ["censored_upper_bound_persons", "censored_upper_bound_commuters_persons"]
    assert by_site.loc["S2", bounds].tolist() == [pytest.approx(12 * 90 / 190), pytest.approx(12 * 90 / 190 * 0.3)]
    # S3's own-Kreis cell is the censored one: it bounds the site's origins but adds no commuter; counted.
    assert by_site.loc["S3", bounds].tolist() == [pytest.approx(12 * 100 / 190), 0.0]
    # E's cell has no bound: it adds nothing to the bounds above and is counted at both sites it splits to.
    assert by_site["censored_cells_without_bound"].tolist() == [1, 1]
    assert diagnostics["local_sites_with_censored_own_kreis_cell"] == ["S3"]
    # A censored own-Kreis cell counts 0: movers = local = 100 - 16 * 30 / 330 * 0.3.
    assert _at(od, "D", "S3", "movers_persons") == pytest.approx(100 - 144 / 330)
    assert _at(od, "D", "S3", "students_persons") == pytest.approx(100 - 144 / 330)


def test_censored_commuter_bound_is_capped_at_enrollment_minus_the_own_kreis_origin():
    # Art Z (no CHE total, factor 1), S1 in A with enrollment 100; A's own cell shows 80, B's cell is censored at 60.
    # Raw commuter bound 60 * 0.5 = 30, but at most 100 - 80 = 20 students of S1 can live outside A: capped, counted.
    cells = [_shown("A", "Art Z", 80), _censored("B", "Art Z", 60)]
    sites = [_site("S1", "Art Z", "A", "local", 100.0)]
    pairs = [_pair("A", "S1", 3.0, SHORT), _pair("B", "S1", 15.0, SHORT)]
    od, _ = T.build_od_table(**_toy_input(cells, {}, sites, pairs, ["A", "B"]), weights=WEIGHTS)
    balance = T.balance_tables(od, ["A", "B"])
    site = balance["by_site"].set_index("destination_id").loc["S1"]
    assert site["censored_upper_bound_commuters_persons"] == pytest.approx(20.0)
    assert site["censored_upper_bound_persons"] == pytest.approx(60.0)  # the origin bound itself is not capped
    assert balance["diagnostics"]["censored_upper_bound_commuters_capped_sites"] == ["S1"]


def test_by_home_kreis_row_sum_is_local_plus_out_commuting(toy_with_surrounding):
    od, _ = T.build_od_table(**toy_with_surrounding, weights=WEIGHTS)
    balance = T.balance_tables(od, ["A", "B"])
    by_home = balance["by_home_kreis"].set_index("home_ars5")
    # A: 942 at S1 and 9 to S9; B: 50 to S1 and 2 to S9. C lies outside the region: no row.
    columns = ["students_local_sites_persons", "students_out_commuting_persons", "students_living_persons"]
    assert list(by_home.index) == ["A", "B"]
    assert by_home.loc["A", columns].tolist() == [pytest.approx(942.0), pytest.approx(9.0), pytest.approx(951.0)]
    assert by_home.loc["B", columns].tolist() == [pytest.approx(50.0), pytest.approx(2.0), pytest.approx(52.0)]
    in_commuters = balance["in_commuters_by_origin"].set_index("home_ars5")["in_commuters_persons"]
    assert in_commuters.to_dict() == {"C": pytest.approx(8.0)}
