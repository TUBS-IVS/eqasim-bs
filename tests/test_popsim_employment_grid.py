# tests/test_popsim_employment_grid.py
import logging

import pandas as pd
import pytest

from braunschweig.popsim import employment_grid as eg


# NOTE: test_band_for_age_maps_genesis_bands_and_floors_at_16 was removed because
# GENESIS_EMPLOYMENT_BANDS and band_for_age were deleted (GENESIS approach replaced by
# Zensus age-shares).
# NOTE: test_employable_population_by_kreis_sums_single_years_into_bands,
# test_employment_rates_divide_svb_by_population, test_employment_rates_zero_population_is_zero_rate,
# test_per_cell_targets_apply_shape_and_rescale_to_census_level,
# test_per_cell_targets_zero_census_level_yields_zero_column,
# test_add_employment_grid_columns_attaches_targets_to_cells_copy, and
# test_per_cell_targets_rescale_is_per_kreis_no_bleed were removed because the GENESIS-based
# functions they tested (employment_rates, employable_population_by_kreis, old 2-col
# per_cell_employment_targets, add_employment_grid_columns with svb arg) were deleted in
# Task 3 and replaced by the Zensus age-share 6-column implementation.


_COMPUTED_COLS_10 = {
    "EMPLOYED_M_16_29_agg", "EMPLOYED_M_30_39_agg", "EMPLOYED_M_40_49_agg",
    "EMPLOYED_M_50_59_agg", "EMPLOYED_M_60plus_agg",
    "EMPLOYED_F_16_29_agg", "EMPLOYED_F_30_39_agg", "EMPLOYED_F_40_49_agg",
    "EMPLOYED_F_50_59_agg", "EMPLOYED_F_60plus_agg",
}


def test_select_load_columns_strips_computed_and_adds_single_year_inputs():
    load_cols = ["HH..", "EMPLOYED_M_16_29_agg", "EMPLOYED_F_30_39_agg"]
    available = ["M_AGE_15", "M_AGE_16", "M_AGE_40", "F_AGE_30", "M_AGE_0_9_agg"]
    result = eg.select_load_columns(
        load_cols, available,
        computed_cols=_COMPUTED_COLS_10,
    )
    # Computed targets removed; below-min-age single-year col excluded.
    assert "EMPLOYED_M_16_29_agg" not in result
    assert "EMPLOYED_F_30_39_agg" not in result
    assert "M_AGE_15" not in result            # below min_age=16
    assert "M_AGE_0_9_agg" not in result       # not a single-year input col
    # Existing keeper preserved, available single-year inputs added.
    assert "HH.." in result
    assert "M_AGE_16" in result
    assert "M_AGE_40" in result
    assert "F_AGE_30" in result
    # Order: existing load_cols (minus computed) first, then the added inputs.
    assert result[0] == "HH.."
    # De-duplicated.
    assert len(result) == len(set(result))


def test_select_load_columns_no_duplicate_when_input_already_present():
    load_cols = ["HH..", "M_AGE_40", "EMPLOYED_M_16_29_agg"]
    available = ["M_AGE_40", "M_AGE_50"]
    result = eg.select_load_columns(
        load_cols, available,
        computed_cols=_COMPUTED_COLS_10,
    )
    assert result.count("M_AGE_40") == 1
    assert "M_AGE_50" in result
    assert "EMPLOYED_M_16_29_agg" not in result
    assert result[:2] == ["HH..", "M_AGE_40"]   # existing order preserved


# --- Task 3: 6-column Zensus age-share targets ---

def test_group_cell_pop_logs_nan_suppression(caplog):
    """The employment-grid single-year row-sum is the third aggregation site of
    issue #150. ``_group_cell_pop`` sums ``{prefix}_AGE_<year>`` columns with the
    pandas default ``skipna=True``, so a Zensus privacy-suppressed (NaN) component
    silently becomes 0. It must route through ``cells.sum_columns_logging_nan`` so
    that suppression is observable (CLAUDE.md fallback-transparency rule)."""
    cells = pd.DataFrame({
        "M_AGE_40": [1.0, float("nan")],
        "M_AGE_41": [10.0, 20.0],
    })
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.cells"):
        out = eg._group_cell_pop(cells, "M", 40, 41, min_age=16, single_year_max=100)
    # skipna behaviour is preserved: the NaN is treated as 0 inside the sum.
    assert out.iloc[0] == pytest.approx(11.0)
    assert out.iloc[1] == pytest.approx(20.0)
    # ...but it is now counted and logged rather than passing through silently.
    nan_logs = [r for r in caplog.records if "NaN" in r.message]
    assert nan_logs, "NaN suppression in _group_cell_pop was not logged"


def test_group_cell_pop_no_matching_columns_yields_zero_series():
    """No present single-year column -> all-zero Series aligned to the index
    (behaviour preserved when routed through the helper)."""
    cells = pd.DataFrame({"OTHER": [1.0, 2.0]}, index=[7, 8])
    out = eg._group_cell_pop(cells, "M", 40, 49, min_age=16, single_year_max=100)
    assert list(out.index) == [7, 8]
    assert (out == 0.0).all()


def test_per_cell_targets_5groups_sum_to_kreis_level_times_ageshare():
    # 1 Kreis, 2 cells. Males: cell c1 has 100 in 40_49 band, c2 has 300 in 40_49 + 50 in 16_29.
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1","c2"], "KREIS": ["03102","03102"],
        "M_AGE_40": [100, 300], "M_AGE_20": [0, 50], "F_AGE_40": [0, 0],
    })
    census = pd.DataFrame({"ARS_kreis": ["03102"],
                           "ERWERBSTAT_KURZ_STP__11_M": [200.0], "ERWERBSTAT_KURZ_STP__11_W": [0.0]})
    shares = {"03102": {"16_29": 0.25, "30_39": 0.0, "40_49": 0.70, "50_59": 0.0, "60plus": 0.05}}
    out = eg.per_cell_employment_targets(cells, census, shares)
    # 40_49 male level = 200*0.70 = 140, split 100:300 -> 35 / 105
    m = out.set_index("ZENSUS100m")
    assert round(out["EMPLOYED_M_40_49_agg"].sum(), 6) == 140.0
    assert round(m.loc["c1","EMPLOYED_M_40_49_agg"], 6) == 35.0
    assert round(m.loc["c2","EMPLOYED_M_40_49_agg"], 6) == 105.0
    # 16_29 male level = 200*0.25 = 50, all in c2 -> 50
    assert round(m.loc["c2","EMPLOYED_M_16_29_agg"], 6) == 50.0
    # female level 0 -> all female columns 0
    assert out["EMPLOYED_F_40_49_agg"].sum() == 0.0


def test_per_cell_targets_ten_columns_present():
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1"], "KREIS": ["03102"],
        "M_AGE_40": [100], "F_AGE_40": [50],
    })
    census = pd.DataFrame({"ARS_kreis": ["03102"],
                           "ERWERBSTAT_KURZ_STP__11_M": [60.0], "ERWERBSTAT_KURZ_STP__11_W": [30.0]})
    shares = {"03102": {"16_29": 0.2, "30_39": 0.1, "40_49": 0.4, "50_59": 0.2, "60plus": 0.1}}
    out = eg.per_cell_employment_targets(cells, census, shares)
    expected_cols = {f"EMPLOYED_{s}_{g}_agg"
                     for s in "MF"
                     for g in ("16_29", "30_39", "40_49", "50_59", "60plus")}
    assert expected_cols.issubset(set(out.columns))


def test_add_employment_grid_columns_attaches_10_targets_to_cells_copy():
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2"],
        "KREIS": ["03102", "03102"],
        "M_AGE_40": [100, 300],
        "F_AGE_40": [100, 100],
        "OTHER": [7, 8],   # must survive untouched
    })
    census_levels = pd.DataFrame({
        "ARS_kreis": ["03102"],
        "ERWERBSTAT_KURZ_STP__11_M": [240.0],
        "ERWERBSTAT_KURZ_STP__11_W": [120.0],
    })
    shares = {"03102": {"16_29": 0.1, "30_39": 0.1, "40_49": 0.6, "50_59": 0.1, "60plus": 0.1}}
    out = eg.add_employment_grid_columns(cells, census_levels, shares)
    # All 10 new columns present; original columns preserved.
    for s in "MF":
        for g in ("16_29", "30_39", "40_49", "50_59", "60plus"):
            assert f"EMPLOYED_{s}_{g}_agg" in out.columns
    assert list(out["OTHER"]) == [7, 8]
    # Input cells frame is not mutated (copy semantics).
    assert "EMPLOYED_M_40_49_agg" not in cells.columns


def test_per_cell_targets_per_kreis_no_bleed():
    """Kreis 03102 and 03103 each produce their own correct totals with no cross-bleed."""
    # Two Kreise, two cells each.  All prime-age males for simplicity.
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2", "c3", "c4"],
        "KREIS":      ["03102", "03102", "03103", "03103"],
        "M_AGE_40":   [100,      300,     200,      400],
        "M_AGE_50":   [0,        0,       0,        0],
        "F_AGE_40":   [0,        0,       0,        0],
    })
    census_levels = pd.DataFrame({
        "ARS_kreis":                   ["03102", "03103"],
        "ERWERBSTAT_KURZ_STP__11_M":   [140.0,   360.0],
        "ERWERBSTAT_KURZ_STP__11_W":   [0.0,     0.0],
    })
    # Both Kreise: all employed are 40_49-age (shares sum to 1 each, different distributions).
    age_shares_by_kreis = {
        "03102": {"16_29": 0.0, "30_39": 0.0, "40_49": 1.0, "50_59": 0.0, "60plus": 0.0},
        "03103": {"16_29": 0.0, "30_39": 0.0, "40_49": 1.0, "50_59": 0.0, "60plus": 0.0},
    }
    out = eg.per_cell_employment_targets(cells, census_levels, age_shares_by_kreis)
    out = out.set_index("ZENSUS100m")

    # Kreis 03102: level_M=140, 40_49_share=1.0 → total 40_49_M = 140
    #   cells c1:c2 pop ratio 100:300 → c1=35, c2=105
    total_03102 = out.loc[["c1", "c2"], "EMPLOYED_M_40_49_agg"].sum()
    assert round(total_03102, 6) == 140.0, f"03102 total={total_03102}"
    assert round(out.loc["c1", "EMPLOYED_M_40_49_agg"], 6) == 35.0
    assert round(out.loc["c2", "EMPLOYED_M_40_49_agg"], 6) == 105.0

    # Kreis 03103: level_M=360, 40_49_share=1.0 → total 40_49_M = 360
    #   cells c3:c4 pop ratio 200:400 → c3=120, c4=240
    total_03103 = out.loc[["c3", "c4"], "EMPLOYED_M_40_49_agg"].sum()
    assert round(total_03103, 6) == 360.0, f"03103 total={total_03103}"
    assert round(out.loc["c3", "EMPLOYED_M_40_49_agg"], 6) == 120.0
    assert round(out.loc["c4", "EMPLOYED_M_40_49_agg"], 6) == 240.0

    # The two totals must differ — confirming they are not cross-contaminated.
    assert total_03102 != total_03103


def test_per_cell_targets_warns_on_partially_unmatched_kreis(caplog):
    """A cell whose Kreis is absent from census_levels must log an observable warning.

    Cell c3 carries a Kreis key ("99999") that has no row in census_levels; it
    silently receives 0.0 for every EMPLOYED_* column, which must be surfaced as
    a fallback per CLAUDE.md rather than passed through quietly.
    """
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2", "c3"],
        "KREIS": ["03102", "03102", "99999"],
        "M_AGE_40": [100, 300, 50],
        "F_AGE_40": [0, 0, 0],
    })
    census = pd.DataFrame({
        "ARS_kreis": ["03102"],
        "ERWERBSTAT_KURZ_STP__11_M": [200.0], "ERWERBSTAT_KURZ_STP__11_W": [0.0],
    })
    shares = {"03102": {"16_29": 0.0, "30_39": 0.0, "40_49": 1.0, "50_59": 0.0, "60plus": 0.0}}
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.employment_grid"):
        out = eg.per_cell_employment_targets(cells, census, shares)

    # Unmatched cell gets zero for every EMPLOYED_* column, not an exception.
    m = out.set_index("ZENSUS100m")
    assert m.loc["c3", "EMPLOYED_M_40_49_agg"] == 0.0
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("1/3" in w and "99999" in w for w in warnings), warnings


def test_per_cell_targets_raises_when_all_cells_unmatched():
    """If EVERY cell's Kreis is absent from census_levels, this is a broken join,
    not a legitimate all-zero result -- CLAUDE.md mandates raising, not silently
    returning zeros for the whole frame."""
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1"], "KREIS": ["99999"],
        "M_AGE_40": [100], "F_AGE_40": [0],
    })
    census = pd.DataFrame({
        "ARS_kreis": ["03102"],
        "ERWERBSTAT_KURZ_STP__11_M": [200.0], "ERWERBSTAT_KURZ_STP__11_W": [0.0],
    })
    shares = {"03102": {"16_29": 0.0, "30_39": 0.0, "40_49": 1.0, "50_59": 0.0, "60plus": 0.0}}
    with pytest.raises(ValueError, match="99999"):
        eg.per_cell_employment_targets(cells, census, shares)
