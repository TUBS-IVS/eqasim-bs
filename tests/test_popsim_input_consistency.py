"""Tests for braunschweig.popsim.input_consistency on a hand-built batch.

Two Kreis parts (3101: cells A, B, C in 1 km zone K1; 3153: cells D, E in 1 km zone K2), five
100 m cells.  Column names are the PopulationSim control fields (control name + geography
suffix), exactly as the control_totals tables carry them.
"""
import logging

import pandas as pd
import pytest

from braunschweig.popsim import input_consistency as ic
from braunschweig.popsim.control_spec import backbone_age_bands

HH_TOTAL = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS100m"
CELLS = ["A", "B", "C", "D", "E"]
CROSSWALK = pd.DataFrame({
    "ZENSUS100m": CELLS, "ZENSUS1km": ["K1", "K1", "K1", "K2", "K2"],
    "KREIS": [3101, 3101, 3101, 3153, 3153]})


def _band_columns():
    return [f"{sex}_AGE_{label}_agg_ZENSUS100m"
            for sex in ("M", "F") for label, _, _ in backbone_age_bands(True)]


def _cells(**values):
    """All band, employment and household columns zero; ``values`` maps column -> {cell: value}."""
    columns = (_band_columns() + [HH_TOTAL] + list(ic.SIZE_COLUMNS) + list(ic.TENURE_COLUMNS)
               + list(ic.BUILDING_TYPE_COLUMNS) + list(ic.EMPLOYED_COLUMNS))
    frame = pd.DataFrame(0.0, index=pd.Index(CELLS, name="ZENSUS100m"), columns=columns)
    for column, by_cell in values.items():
        for cell, value in by_cell.items():
            frame.loc[cell, column] = value
    return frame


def _kreis(**values):
    """KREIS table with the given columns: {column: {kreis: value}}."""
    return pd.DataFrame({c: pd.Series(v) for c, v in values.items()}).rename_axis("KREIS")


def _pairs(*labels):
    return tuple(p for p in ic.KREIS_FINE_PAIRS if p.label in labels)


def _row(table, kreis, pair):
    rows = table[(table["KREIS"] == kreis) & (table["pair"] == pair)]
    assert len(rows) == 1
    return rows.iloc[0]


def test_kreis_vs_fine_equal_pair():
    bands = ic.UNDER_18_COLUMNS
    cells = _cells(**{bands[0]: {"A": 4, "B": 2}, bands[1]: {"A": 4}})   # 3101: 10 persons
    kreis = _kreis(education_0_5_edu_KREIS={3101: 7, 3153: 0}, education_0_5_noedu_KREIS={3101: 1, 3153: 0},
                   education_6_17_edu_KREIS={3101: 3, 3153: 0}, education_6_17_noedu_KREIS={3101: 1, 3153: 0})
    table = ic.kreis_vs_fine(kreis, cells, CROSSWALK, pairs=_pairs("persons_0_17"))
    row = _row(table, "3101", "persons_0_17")
    assert (row["relation"], row["kreis_sum"], row["fine_sum"]) == ("equal", 12, 10)
    assert row["diff"] == 2
    assert row["rel_diff"] == pytest.approx(0.2)
    assert list(table.columns) == ["KREIS", "pair", "relation", "kreis_sum", "fine_sum", "diff",
                                   "rel_diff"]


def test_kreis_vs_fine_bounds():
    from_15 = [c for c in _band_columns() if "_AGE_0_9_" not in c and "_AGE_10_14_" not in c]
    cells = _cells(**{from_15[0]: {"A": 8}, "M_AGE_10_14_agg_ZENSUS100m": {"A": 1}})
    employment = {f"employment_status_{label}_KREIS": {3101: 0, 3153: 0} for label in ic.EMPLOYMENT_LABELS}
    employment["employment_status_vollzeit_KREIS"][3101] = 7           # 14+ persons: 7
    table = ic.kreis_vs_fine(_kreis(**employment), cells, CROSSWALK,
                             pairs=_pairs("persons_14plus_low", "persons_14plus_high"))
    low = _row(table, "3101", "persons_14plus_low")
    high = _row(table, "3101", "persons_14plus_high")
    assert (low["relation"], low["fine_sum"], low["diff"]) == ("at_least", 8, -1)    # violated by 1
    assert ic.violation(low["relation"], low["diff"]) == 1
    assert (high["relation"], high["fine_sum"], high["diff"]) == ("at_most", 9, -2)
    assert ic.violation(high["relation"], high["diff"]) == 0                          # holds


def test_ratio_pair():
    cells = _cells(**{ic.EMPLOYED_COLUMNS[0]: {"A": 6, "B": 4}})                      # grid: 10
    kreis = _kreis(work_by_employment_employed_work_KREIS={3101: 6.0, 3153: 0},
                   work_by_employment_employed_nowork_KREIS={3101: 3.5, 3153: 0})
    table = ic.kreis_vs_fine(kreis, cells, CROSSWALK, pairs=_pairs("employed_survey"))
    row = _row(table, "3101", "employed_survey")
    assert (row["relation"], row["kreis_sum"], row["fine_sum"]) == ("ratio", 9.5, 10)
    assert row["rel_diff"] == pytest.approx(0.95)


def test_absent_pair_is_named_not_failed(caplog):
    kreis = _kreis(employed_KREIS={3101: 5, 3153: 5})
    with caplog.at_level(logging.WARNING):
        table = ic.kreis_vs_fine(kreis, _cells(), CROSSWALK)
    assert set(table["pair"]) == {"employed"}
    skipped = table.attrs["skipped"]
    assert "persons_all" in skipped and "cars_0" in skipped and "employed" not in skipped
    assert "persons_all" in caplog.text


def test_one_km_pairs_use_the_one_km_to_kreis_map():
    cells = pd.DataFrame({"OWN_CARS_0_agg_ZENSUS1km": {"K1": 30.0, "K2": 5.0}})
    kreis = _kreis(number_of_cars_0_KREIS={3101: 28, 3153: 6})
    table = ic.kreis_vs_fine(kreis, cells, CROSSWALK, pairs=_pairs("cars_0"))
    assert _row(table, "3101", "cars_0")["fine_sum"] == 30
    assert _row(table, "3153", "cars_0")["diff"] == 1


def test_cell_contradictions_counts():
    one_person, two_persons = ic.SIZE_COLUMNS[0], ic.SIZE_COLUMNS[1]
    cells = _cells(**{HH_TOTAL: {"A": 2}})
    cells.loc["A", [one_person, two_persons]] = 1                   # sizes 1x1 + 1x2: at most 3
    cells.loc["A", ic.UNDER_18_COLUMNS[0]] = 5                      # but 5 persons
    cells.loc["B", ic.UNDER_18_COLUMNS[0]] = 4                      # persons, no household
    cells.loc["C", HH_TOTAL] = 2                                    # households, no person
    table = ic.cell_contradictions(cells)
    assert list(table.columns) == ["check", "cells", "amount", "base"]
    by = table.set_index("check")
    assert (by.loc["persons_above_size_maximum", "cells"], by.loc["persons_above_size_maximum", "amount"]) == (1, 2)
    assert (by.loc["persons_without_households", "cells"], by.loc["persons_without_households", "amount"]) == (1, 4)
    assert (by.loc["households_without_persons", "cells"], by.loc["households_without_persons", "amount"]) == (1, 2)
    assert by.loc["persons_without_households", "base"] == 9        # all persons
    assert by.loc["households_without_persons", "base"] == 4        # all households
    assert by.loc["persons_below_size_minimum", "cells"] == 0
    assert by.loc["size_partition", "cells"] == 1                   # cell C: classes 0, total 2


def test_cell_contradictions_partitions_and_minimum():
    cells = _cells(**{HH_TOTAL: {"A": 3, "B": 3}})
    cells.loc["A", list(ic.TENURE_COLUMNS)] = [1, 1]                # classes 2 vs total 3
    cells.loc["B", ic.SIZE_COLUMNS[5]] = 3                          # three 6+ households: min 18 persons
    cells.loc["B", ic.UNDER_18_COLUMNS[0]] = 10                     # 10 persons
    table = ic.cell_contradictions(cells).set_index("check")
    assert (table.loc["tenure_partition", "cells"], table.loc["tenure_partition", "amount"]) == (2, 4)
    assert table.loc["persons_below_size_minimum", "amount"] == 8
    assert table.loc["persons_above_size_maximum", "cells"] == 0    # 6+ households have no maximum


def test_cell_contradictions_names_a_check_without_columns():
    table = ic.cell_contradictions(pd.DataFrame({HH_TOTAL: [1.0]}, index=["A"]))
    assert "persons_without_households" in table.attrs["skipped"]


def test_summarize_long_frame():
    cells = _cells(**{ic.EMPLOYED_COLUMNS[0]: {"A": 6, "B": 4}, HH_TOTAL: {"A": 1}})
    kreis = _kreis(employed_KREIS={3101: 12, 3153: 0})
    long = ic.summarize(kreis, cells, CROSSWALK)
    assert list(long.columns) == ["scope", "key", "check", "cells", "amount", "base", "share"]
    employed = long[(long["scope"] == "kreis") & (long["check"] == "employed")]
    assert sorted(employed["key"]) == ["3101", "3153"]
    row = employed[employed["key"] == "3101"].iloc[0]
    assert (row["amount"], row["base"], row["share"], row["cells"]) == (2, 10, 0.2, 1)
    assert (long["scope"] == "cell").any()


def test_summarize_reports_a_ratio_pair_but_scores_no_contradiction():
    cells = _cells(**{ic.EMPLOYED_COLUMNS[0]: {"A": 6, "B": 4}})
    kreis = _kreis(work_by_employment_employed_work_KREIS={3101: 6.0, 3153: 0},
                   work_by_employment_employed_nowork_KREIS={3101: 3.5, 3153: 0})
    long = ic.summarize(kreis, cells, CROSSWALK)
    assert "employed_survey" not in set(long["check"])
    row = long[(long["check"] == "ratio_reported") & (long["key"] == "3101")].iloc[0]
    assert (row["amount"], row["base"], row["cells"]) == (-0.5, 10, 0)
    assert row["share"] == pytest.approx(0.95)
    assert ic.violation("ratio", -0.5) == 0


def test_summarize_writes_a_row_per_skipped_pair_and_check():
    cells = pd.DataFrame({HH_TOTAL: [1.0]}, index=["A"])                 # no band, size ... columns
    kreis = _kreis(employed_KREIS={3101: 1})
    long = ic.summarize(kreis, cells, CROSSWALK)
    skipped = long[long["check"] == "skipped"]
    assert {"persons_all", "employed"} <= set(skipped[skipped["scope"] == "kreis"]["key"])
    assert "persons_without_households" in set(skipped[skipped["scope"] == "cell"]["key"])
    assert skipped["amount"].isna().all()
    assert set(skipped["key"]) == set(long.attrs["skipped"])             # pairs and cell checks


def test_kreis_without_grid_and_grid_without_kreis_are_counted(caplog):
    cells = _cells(**{ic.EMPLOYED_COLUMNS[0]: {"A": 6, "D": 2}})
    kreis = _kreis(employed_KREIS={3101: 6, 9999: 4})                    # 9999: no zone; 3153 absent
    with caplog.at_level(logging.WARNING):
        table = ic.kreis_vs_fine(kreis, cells, CROSSWALK, pairs=_pairs("employed"))
    assert table.attrs["kreis_without_grid"] == ["9999"]
    assert table.attrs["grid_without_kreis"] == ["3153"]
    assert "9999" in caplog.text and "3153" in caplog.text
    assert _row(table, "9999", "employed")["fine_sum"] == 0


def test_missing_value_in_a_cell_is_not_read_as_zero_persons():
    cells = _cells(**{HH_TOTAL: {"A": 2}})
    cells.loc["A", ic.UNDER_18_COLUMNS[0]] = float("nan")               # household, persons unknown
    table = ic.cell_contradictions(cells)
    by = table.set_index("check")
    assert by.loc["households_without_persons", "cells"] == 0           # not judged, not "no person"
    assert table.attrs["nan_cells"]["households_without_persons"] == 1


def test_pair_mixing_geographies_is_refused():
    mixed = ic.KreisFinePair("mixed", ("employed_KREIS",),
                             (ic.EMPLOYED_COLUMNS[0], "OWN_CARS_0_agg_ZENSUS1km"), "equal")
    with pytest.raises(ValueError, match="mixed"):
        ic.kreis_vs_fine(_kreis(employed_KREIS={3101: 1}), _cells(), CROSSWALK, pairs=(mixed,))


def test_one_km_and_100m_zone_ids_may_collide_when_kept_apart():
    crosswalk = pd.DataFrame({"ZENSUS100m": ["X1", "Y"], "ZENSUS1km": ["X1", "X2"],
                              "KREIS": [3101, 3153]})
    cells = pd.DataFrame(0.0, index=["X1", "Y"], columns=list(ic.EMPLOYED_COLUMNS))
    cells[ic.EMPLOYED_COLUMNS[0]] = [5.0, 1.0]
    cells_1km = pd.DataFrame({"OWN_CARS_0_agg_ZENSUS1km": {"X1": 7.0, "X2": 2.0}})
    kreis = _kreis(employed_KREIS={3101: 5, 3153: 1}, number_of_cars_0_KREIS={3101: 7, 3153: 2})
    table = ic.kreis_vs_fine(kreis, cells, crosswalk, pairs=_pairs("employed", "cars_0"),
                             cells_1km=cells_1km)
    assert _row(table, "3101", "employed")["fine_sum"] == 5
    assert _row(table, "3101", "cars_0")["fine_sum"] == 7
    with pytest.raises(ValueError, match="duplicate"):
        ic.kreis_vs_fine(kreis, pd.concat([cells, cells_1km]), crosswalk, pairs=_pairs("cars_0"))
