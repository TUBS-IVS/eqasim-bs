# tests/test_popsim_employment_grid.py
import pandas as pd
from braunschweig.popsim import employment_grid as eg


def test_band_for_age_maps_genesis_bands_and_floors_at_16():
    assert eg.band_for_age(15) is None       # below minimum employment age
    assert eg.band_for_age(16) == 0          # u20 band
    assert eg.band_for_age(19) == 0
    assert eg.band_for_age(20) == 20
    assert eg.band_for_age(29) == 25
    assert eg.band_for_age(30) == 30
    assert eg.band_for_age(49) == 30
    assert eg.band_for_age(64) == 60
    assert eg.band_for_age(65) == 65
    assert eg.band_for_age(99) == 65


def test_employable_population_by_kreis_sums_single_years_into_bands():
    # Two cells, both Kreis "03102". Male single-year pops at ages 15,16,25,40,70.
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2"],
        "KREIS": ["03102", "03102"],
        "M_AGE_15": [10, 0],   # below 16 -> excluded
        "M_AGE_16": [4, 1],    # band 0
        "M_AGE_25": [3, 2],    # band 25
        "M_AGE_40": [5, 5],    # band 30
        "M_AGE_70": [1, 1],    # band 65
    })
    out = eg.employable_population_by_kreis(cells, sex_prefix="M")
    got = {(r.KREIS, r.age_class): r.pop for r in out.itertuples()}
    assert got[("03102", 0)] == 5    # 4+1 (age 16); age 15 excluded
    assert got[("03102", 25)] == 5   # 3+2
    assert got[("03102", 30)] == 10  # 5+5
    assert got[("03102", 65)] == 2   # 1+1
    assert ("03102", 20) not in got or got[("03102", 20)] == 0


def test_employment_rates_divide_svb_by_population():
    svb = pd.DataFrame({
        "departement_id": ["03102", "03102"],
        "age_class": [25, 30],
        "sex": ["male", "male"],
        "weight": [400, 900],
    })
    pop = pd.DataFrame({
        "KREIS": ["03102", "03102"],
        "age_class": [25, 30],
        "pop": [500.0, 1000.0],
    })
    out = eg.employment_rates(svb, pop, sex="male")
    got = {(r.KREIS, r.age_class): round(r.rate, 3) for r in out.itertuples()}
    assert got[("03102", 25)] == 0.8   # 400/500
    assert got[("03102", 30)] == 0.9   # 900/1000


def test_employment_rates_zero_population_is_zero_rate():
    svb = pd.DataFrame({"departement_id": ["03102"], "age_class": [65],
                        "sex": ["female"], "weight": [10]})
    pop = pd.DataFrame({"KREIS": ["03102"], "age_class": [65], "pop": [0.0]})
    out = eg.employment_rates(svb, pop, sex="female")
    assert out.loc[0, "rate"] == 0.0


def test_per_cell_targets_apply_shape_and_rescale_to_census_level():
    # One Kreis, two cells. Males all age 40 (band 30); GENESIS gives a rate via SvB/pop.
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2"],
        "KREIS": ["03102", "03102"],
        "M_AGE_40": [100, 300],   # cell c2 has 3x the working-age men of c1
        "F_AGE_40": [100, 100],
    })
    svb = pd.DataFrame({
        "departement_id": ["03102", "03102"],
        "age_class": [30, 30],
        "sex": ["male", "female"],
        "weight": [200, 100],     # SvB shape (level is overridden by census below)
    })
    census_levels = pd.DataFrame({
        "ARS_kreis": ["03102"],
        "ERWERBSTAT_KURZ_STP__11_M": [240.0],   # census LEVEL (males)
        "ERWERBSTAT_KURZ_STP__11_W": [120.0],   # census LEVEL (females)
    })
    out = eg.per_cell_employment_targets(cells, svb, census_levels)
    # Male target sums to census level 240, split by cell male population (100:300 = 1:3).
    assert round(out["EMPLOYED_M_agg"].sum(), 6) == 240.0
    m = out.set_index("ZENSUS100m")["EMPLOYED_M_agg"]
    assert round(m["c1"], 6) == 60.0    # 240 * 100/400
    assert round(m["c2"], 6) == 180.0   # 240 * 300/400
    # Female target sums to census level 120 (100:100 split -> 60 each).
    assert round(out["EMPLOYED_F_agg"].sum(), 6) == 120.0
    f = out.set_index("ZENSUS100m")["EMPLOYED_F_agg"]
    assert round(f["c1"], 6) == 60.0


def test_per_cell_targets_zero_census_level_yields_zero_column():
    cells = pd.DataFrame({"ZENSUS100m": ["c1"], "KREIS": ["03102"], "M_AGE_40": [100], "F_AGE_40": [0]})
    svb = pd.DataFrame({"departement_id": ["03102"], "age_class": [30], "sex": ["male"], "weight": [50]})
    census = pd.DataFrame({"ARS_kreis": ["03102"], "ERWERBSTAT_KURZ_STP__11_M": [0.0],
                           "ERWERBSTAT_KURZ_STP__11_W": [0.0]})
    out = eg.per_cell_employment_targets(cells, svb, census)
    assert out["EMPLOYED_M_agg"].sum() == 0.0
    assert out["EMPLOYED_F_agg"].sum() == 0.0


def test_select_load_columns_strips_computed_and_adds_single_year_inputs():
    load_cols = ["HH..", "EMPLOYED_M_agg", "EMPLOYED_F_agg"]
    available = ["M_AGE_15", "M_AGE_16", "M_AGE_40", "F_AGE_30", "M_AGE_0_9_agg"]
    result = eg.select_load_columns(
        load_cols, available, computed_cols={"EMPLOYED_M_agg", "EMPLOYED_F_agg"}
    )
    # Computed targets removed; below-min-age single-year col excluded.
    assert "EMPLOYED_M_agg" not in result
    assert "EMPLOYED_F_agg" not in result
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
    load_cols = ["HH..", "M_AGE_40", "EMPLOYED_M_agg"]
    available = ["M_AGE_40", "M_AGE_50"]
    result = eg.select_load_columns(
        load_cols, available, computed_cols={"EMPLOYED_M_agg"}
    )
    assert result.count("M_AGE_40") == 1
    assert "M_AGE_50" in result
    assert "EMPLOYED_M_agg" not in result
    assert result[:2] == ["HH..", "M_AGE_40"]   # existing order preserved


def test_add_employment_grid_columns_attaches_targets_to_cells_copy():
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2"],
        "KREIS": ["03102", "03102"],
        "M_AGE_40": [100, 300],
        "F_AGE_40": [100, 100],
        "OTHER": [7, 8],   # must survive untouched
    })
    svb = pd.DataFrame({
        "departement_id": ["03102", "03102"],
        "age_class": [30, 30],
        "sex": ["male", "female"],
        "weight": [200, 100],
    })
    census_levels = pd.DataFrame({
        "ARS_kreis": ["03102"],
        "ERWERBSTAT_KURZ_STP__11_M": [240.0],
        "ERWERBSTAT_KURZ_STP__11_W": [120.0],
    })
    out = eg.add_employment_grid_columns(cells, svb, census_levels)
    # New columns present; original columns preserved.
    assert "EMPLOYED_M_agg" in out.columns
    assert "EMPLOYED_F_agg" in out.columns
    assert list(out["OTHER"]) == [7, 8]
    # Targets match per_cell_employment_targets (240 male split 100:300).
    m = out.set_index("ZENSUS100m")["EMPLOYED_M_agg"]
    assert round(m["c1"], 6) == 60.0
    assert round(m["c2"], 6) == 180.0
    # Input cells frame is not mutated (copy semantics).
    assert "EMPLOYED_M_agg" not in cells.columns


def test_per_cell_targets_rescale_is_per_kreis_no_bleed():
    """Per-Kreis×sex rescale must not bleed census levels between Kreise.

    Two Kreise ("03102", "03103"), each with two cells.  The census EMPLOYED_M
    levels differ (240 vs 480).  After per_cell_employment_targets the sum of
    EMPLOYED_M_agg inside each Kreis must equal that Kreis's census level exactly,
    and the two totals must be distinct (i.e. no blending has occurred).
    """
    cells = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2", "c3", "c4"],
        "KREIS":      ["03102", "03102", "03103", "03103"],
        # Kreis 03102: 100 + 300 = 400 working-age men; Kreis 03103: 200 + 200 = 400
        "M_AGE_40":   [100, 300, 200, 200],
        "F_AGE_40":   [50,  50,  100, 100],
    })
    svb = pd.DataFrame({
        "departement_id": ["03102", "03102", "03103", "03103"],
        "age_class":      [30,      30,      30,      30],
        "sex":            ["male",  "female","male",  "female"],
        "weight":         [200,     80,      300,     120],
    })
    census_levels = pd.DataFrame({
        "ARS_kreis":                  ["03102", "03103"],
        "ERWERBSTAT_KURZ_STP__11_M":  [240.0,   480.0],   # deliberately different
        "ERWERBSTAT_KURZ_STP__11_W":  [60.0,    120.0],
    })
    out = eg.per_cell_employment_targets(cells, svb, census_levels)

    # Attach Kreis for grouping assertions
    out = out.copy()
    out["KREIS"] = cells["KREIS"].values

    kreis_m = out.groupby("KREIS")["EMPLOYED_M_agg"].sum()
    kreis_f = out.groupby("KREIS")["EMPLOYED_F_agg"].sum()

    # Each Kreis must sum exactly to its own census level
    assert round(kreis_m["03102"], 6) == 240.0, f"Kreis 03102 male: {kreis_m['03102']}"
    assert round(kreis_m["03103"], 6) == 480.0, f"Kreis 03103 male: {kreis_m['03103']}"
    assert round(kreis_f["03102"], 6) == 60.0,  f"Kreis 03102 female: {kreis_f['03102']}"
    assert round(kreis_f["03103"], 6) == 120.0, f"Kreis 03103 female: {kreis_f['03103']}"

    # Totals must be distinct — confirms no cross-Kreis blending
    assert kreis_m["03102"] != kreis_m["03103"]
    assert kreis_f["03102"] != kreis_f["03103"]
