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
