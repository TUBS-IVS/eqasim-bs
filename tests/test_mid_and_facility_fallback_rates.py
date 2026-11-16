"""Fallback-rate instrumentation, audit D sites 4-6 (CLAUDE.md "Fallback transparency").

Each test drives the real function on a small synthetic input where the PRIMARY path
covers everything, asserts the returned table and that the rate line / print shows no
fallback; the fallback counterparts assert the count and the unchanged table.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Site 4: MiD coupling tables
# ---------------------------------------------------------------------------

def _rate_lines(caplog, table):
    return [r for r in caplog.records if r.getMessage().startswith(table + ": primary")]


def _income_df(key, key_col, regions):
    from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES

    rows = [
        {"region": reg, key_col: key, "income_bracket": b, "share_pct": 10.0,
         "base_weighted": 100.0}
        for reg in regions for b in INCOME_BRACKET_CATEGORIES
    ]
    return pd.DataFrame(rows)


def test_income_by_size_primary_path_reports_no_fallback(caplog):
    from braunschweig.data.mid import income_by_size as mod

    mod.report_fallback_rates()  # drop counts of earlier tests
    df_bl = _income_df("1", "hh_size", ["niedersachsen"])
    df_rt = _income_df("1", "hh_size", ["r1", "r2"])
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        pmf = mod.income_bracket_probabilities(df_bl, df_rt, "1", "r1")
        mod.report_fallback_rates()
    assert pmf == pytest.approx(np.full(10, 0.1))
    (line,) = _rate_lines(caplog, "income_by_size")
    assert "primary 1/1 (100.0%)" in line.getMessage()
    assert "fallback 0 (0.0%)" in line.getMessage()
    assert line.levelno == logging.INFO


def test_income_by_size_untilted_base_is_counted_and_warned(caplog):
    from braunschweig.data.mid import income_by_size as mod

    mod.report_fallback_rates()
    df_bl = _income_df("1", "hh_size", ["niedersachsen"])
    df_rt = _income_df("1", "hh_size", ["r1"])
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        pmf = mod.income_bracket_probabilities(df_bl, df_rt, "1", "unknown_region")
        mod.report_fallback_rates()
    assert pmf == pytest.approx(np.full(10, 0.1))  # behaviour unchanged: untilted base
    (line,) = _rate_lines(caplog, "income_by_size")
    assert "fallback untilted_base 1 (100.0%)" in line.getMessage()
    assert line.levelno == logging.WARNING


def test_income_by_status_primary_path_reports_no_fallback(caplog):
    from braunschweig.data.mid import income_by_status as mod

    mod.report_fallback_rates()
    status = mod.STATUS_CATEGORIES[0]
    df_bl = _income_df(status, "status", ["niedersachsen"])
    df_rt = _income_df(status, "status", ["r1", "r2"])
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        pmf = mod.income_bracket_probabilities_by_status(df_bl, df_rt, status, "r1")
        mod.report_fallback_rates()
    assert pmf == pytest.approx(np.full(10, 0.1))
    (line,) = _rate_lines(caplog, "income_by_status")
    assert "primary 1/1 (100.0%)" in line.getMessage()
    assert "fallback 0 (0.0%)" in line.getMessage()


def test_cars_by_status_table_build_reports_no_fallback(caplog):
    from braunschweig.data.mid import cars_by_status as mod

    cars = mod.CAR_COUNT_CATEGORIES
    df_hh = pd.DataFrame([
        {"num_cars": c, "hhtype": "h1", "status": "s1", "share_pct": 25.0,
         "base_weighted": 100.0}
        for c in cars
    ])
    df_rt = pd.DataFrame([{"num_cars": c, "region": "r1", "base_weighted": 100.0} for c in cars])
    mod.report_fallback_rates()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        base_map, _by_region, national = mod.cars_probabilities_table(df_hh, df_rt)
    assert base_map[("h1", "s1")].tolist() == pytest.approx([0.25] * 4)
    assert national.tolist() == pytest.approx([0.25] * 4)
    (line,) = _rate_lines(caplog, "cars_by_status")
    assert "fallback 0 (0.0%)" in line.getMessage()
    assert line.levelno == logging.INFO


def test_status_by_hhtype_primary_path_reports_no_fallback(caplog):
    from braunschweig.data.mid import status_by_hhtype as mod

    df = pd.DataFrame([
        {"region": reg, "status": s, "hhtype": "h1", "share_pct": 50.0,
         "base_weighted": 100.0}
        for reg in ("niedersachsen", "r1", "r2") for s in mod.STATUS_CATEGORIES
    ])
    mod.report_fallback_rates()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        out = mod.region_status_probabilities(df, df, "niedersachsen", "r1")  # real region
        mod.report_fallback_rates()
    n = len(mod.STATUS_CATEGORIES)
    assert out["h1"].tolist() == pytest.approx([1.0 / n] * n)
    (line,) = _rate_lines(caplog, "status_by_hhtype")
    assert "fallback 0 (0.0%)" in line.getMessage()
    assert line.levelno == logging.INFO


def test_missing_raumtyp_is_a_counted_fallback_in_every_coupling(caplog):
    """A failed RegioStaR-7 lookup (``raumtyp_region is None``) is no primary result."""
    from braunschweig.data.mid import income_by_size, income_by_status
    from braunschweig.data.mid import status_by_hhtype, tenure_by_income
    from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES

    status = income_by_status.STATUS_CATEGORIES[0]
    tenure_df = pd.DataFrame([
        {"region": r, "tenure": t, "income_bracket": b, "share_pct": 10.0, "base_weighted": 100.0}
        for r in ("niedersachsen", "r1") for t in tenure_by_income.TENURE_CATEGORIES
        for b in INCOME_BRACKET_CATEGORIES
    ])
    hh_df = pd.DataFrame([
        {"region": r, "status": s, "hhtype": "h1", "share_pct": 50.0, "base_weighted": 100.0}
        for r in ("niedersachsen", "r1") for s in status_by_hhtype.STATUS_CATEGORIES
    ])
    n_status = len(status_by_hhtype.STATUS_CATEGORIES)
    calls = {
        "income_by_size": (income_by_size, lambda: income_by_size.income_bracket_probabilities(
            _income_df("1", "hh_size", ["niedersachsen"]), _income_df("1", "hh_size", ["r1"]),
            "1", None), np.full(10, 0.1)),
        "income_by_status": (income_by_status, lambda: income_by_status.income_bracket_probabilities_by_status(
            _income_df(status, "status", ["niedersachsen"]), _income_df(status, "status", ["r1"]),
            status, None), np.full(10, 0.1)),
        "status_by_hhtype": (status_by_hhtype, lambda: status_by_hhtype.region_status_probabilities(
            hh_df, hh_df, "niedersachsen", None)["h1"], np.full(n_status, 1.0 / n_status)),
        "tenure_by_income": (tenure_by_income, lambda: tenure_by_income.tenure_probabilities_given_income(
            tenure_df, tenure_df, None)[0], np.full(3, 1 / 3)),
    }
    for table, (mod, call, expected) in calls.items():
        mod.reset_fallback_rates()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=mod.logger.name):
            value = call()
            mod.report_fallback_rates()
        assert value == pytest.approx(expected)  # behaviour unchanged: untilted base
        (line,) = _rate_lines(caplog, table)
        assert "fallback raumtyp_missing" in line.getMessage()
        assert line.levelno == logging.WARNING  # every unit missed it


def test_status_by_hhtype_warn_fraction_equals_the_shared_constant():
    # status_by_hhtype keeps a separate tally copy (synpp helper-hash closure); the
    # warn level must not drift from the shared one.
    from braunschweig.data.mid import raumtyp_tilt, status_by_hhtype

    assert status_by_hhtype._FALLBACK_WARN_FRACTION == raumtyp_tilt.FALLBACK_WARN_FRACTION


def test_tenure_by_income_primary_path_reports_no_fallback(caplog):
    from braunschweig.data.mid import tenure_by_income as mod
    from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES

    def make(regions):
        return pd.DataFrame([
            {"region": r, "tenure": t, "income_bracket": b, "share_pct": 10.0,
             "base_weighted": 100.0}
            for r in regions for t in mod.TENURE_CATEGORIES for b in INCOME_BRACKET_CATEGORIES
        ])

    mod.report_fallback_rates()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mat = mod.tenure_probabilities_given_income(
            make(["niedersachsen"]), make(["r1", "r2"]), "r1")
        mod.report_fallback_rates()
    assert mat.shape == (10, 3)
    assert mat[0].tolist() == pytest.approx([1 / 3] * 3)
    (line,) = _rate_lines(caplog, "tenure_by_income")
    assert "fallback 0 (0.0%)" in line.getMessage()
    assert line.levelno == logging.INFO


# ---------------------------------------------------------------------------
# Site 5: KBA EV grid, unmapped suppressed flag
# ---------------------------------------------------------------------------

def _ev_grid_dp(tmp_path, suppressed_values):
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True)
    rows = [
        {"cell_id": "c%d" % i, "stichtag": "2026-04-01", "ev_share": 0.04, "minx": 0.0,
         "miny": 0.0, "maxx": 1.0, "maxy": 1.0, "suppressed": s}
        for i, s in enumerate(suppressed_values)
    ]
    pd.DataFrame(rows).to_csv(d / "kba_ev_grid.csv", index=False)
    return str(tmp_path)


def test_load_ev_grid_blank_suppressed_is_counted_and_warned(tmp_path, caplog):
    from braunschweig.data.kba import fleet_tables as ft

    dp = _ev_grid_dp(tmp_path, ["True", "False", None, "False"])
    with caplog.at_level(logging.WARNING, logger=ft.logger.name):
        df = ft.load_ev_grid(dp)
    assert df["suppressed"].isna().sum() == 1  # mapping unchanged: NaN stays NaN
    msgs = [r.getMessage() for r in caplog.records
            if "suppressed flag unmapped" in r.getMessage()]
    assert len(msgs) == 1
    assert "1/4 cells (25.0%)" in msgs[0]


def test_load_ev_grid_clean_suppressed_does_not_warn(tmp_path, caplog):
    from braunschweig.data.kba import fleet_tables as ft

    dp = _ev_grid_dp(tmp_path, ["True", "False"])
    with caplog.at_level(logging.WARNING, logger=ft.logger.name):
        df = ft.load_ev_grid(dp)
    assert df["suppressed"].tolist() == [True, False]
    assert not [r for r in caplog.records if "suppressed flag unmapped" in r.getMessage()]


# ---------------------------------------------------------------------------
# Site 6: kita / university zero-weight units
# ---------------------------------------------------------------------------

def test_kita_zero_weight_unit_is_counted_in_print(capsys):
    import geopandas as gpd
    from shapely.geometry import Point
    from braunschweig.data.schools.kita_facilities import build_kita_facilities

    kitas = pd.DataFrame({"lsn_code": ["101", "151009"], "plaetze": [100.0, 50.0]})
    osm = gpd.GeoDataFrame({
        "commune_id": ["031010000000", "031010000000", "031510009009"],
        "weight": [300.0, 100.0, 0.0],  # the Gifhorn unit has zero total weight
        "geometry": [Point(605000, 5790000), Point(606000, 5790000),
                     Point(610000, 5860000)],
    }, crs="EPSG:25832")
    gdf = build_kita_facilities(kitas, osm)
    assert len(gdf) == 2  # unchanged: the zero-weight unit is skipped
    assert gdf["capacity"].sum() == pytest.approx(100.0)
    out = capsys.readouterr().out
    assert "[kita_facilities] WARNING units with OSM POIs: placed 1/2 (50.0%)" in out
    assert "zero-weight units skipped (Plaetze not placed) 1 (50.0%)" in out


def test_university_primary_path_prints_no_zero_weight_units(capsys):
    import geopandas as gpd
    from shapely.geometry import Point
    from braunschweig.data.schools.university_facilities import build_university_facilities

    hoch = pd.DataFrame({
        "institution": ["TU BS"], "scope": ["local"], "ars5": ["03101"],
        "enrollment": [1000.0], "lon": [float("nan")], "lat": [float("nan")],
    })
    osm = gpd.GeoDataFrame({
        "commune_id": ["031010000000", "031010000000"], "weight": [300.0, 100.0],
        "geometry": [Point(605000, 5790000), Point(606000, 5790000)],
    }, crs="EPSG:25832")
    gdf = build_university_facilities(hoch, osm)
    assert sorted(gdf["capacity"]) == pytest.approx([250.0, 750.0])
    out = capsys.readouterr().out
    assert "[university_facilities] units with OSM buildings: placed 1/1 (100.0%)" in out
    assert "units skipped (enrollment not placed) 0 (0.0%)" in out
    assert "WARNING" not in out


# ---------------------------------------------------------------------------
# Counting never changes a value; a report covers exactly one build
# ---------------------------------------------------------------------------

def test_cars_nan_total_takes_the_uniform_fallback_like_origin_main():
    from braunschweig.data.mid import cars_by_status as mod

    cars = mod.CAR_COUNT_CATEGORIES
    # +inf and -inf make the region sum NaN: origin/main tested ``total > 0`` (False for NaN)
    # and returned the uniform vector, so the counted branch must do the same.
    base = [np.inf, -np.inf] + [1.0] * (len(cars) - 2)
    df_rt = pd.DataFrame([{"num_cars": c, "region": "r1", "base_weighted": b}
                          for c, b in zip(cars, base)])
    mod.reset_fallback_rates()
    by_region, national = mod._raumtyp_cars_distribution(df_rt)
    assert by_region["r1"].tolist() == pytest.approx([1.0 / len(cars)] * len(cars))
    assert national.tolist() == pytest.approx([1.0 / len(cars)] * len(cars))
    assert mod._TALLY.fallbacks == {"region_uniform_cars": 1, "national_uniform_cars": 1}


def test_reset_drops_counts_of_other_callers(caplog):
    from braunschweig.data.mid import income_by_size as mod

    df_bl = _income_df("1", "hh_size", ["niedersachsen"])
    df_rt = _income_df("1", "hh_size", ["r1"])
    mod.income_bracket_probabilities(df_bl, df_rt, "1", "unknown_region")  # foreign caller
    mod.reset_fallback_rates()  # what a reporting stage does at the start of its build
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.income_bracket_probabilities(df_bl, df_rt, "1", "r1")
        mod.report_fallback_rates()
    (line,) = _rate_lines(caplog, "income_by_size")
    assert "primary 1/1 (100.0%)" in line.getMessage()
    assert "fallback 0 (0.0%)" in line.getMessage()


def test_loop_branch_is_reported_against_its_own_unit(caplog):
    from braunschweig.data.mid import income_by_size as mod

    df_bl = _income_df("1", "hh_size", ["niedersachsen"])
    df_rt = _income_df("1", "hh_size", ["r1", "r2"])
    df_rt["base_weighted"] = 0.0  # zero base in every raumtyp cell: equal weight, not a skip
    mod.reset_fallback_rates()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mod.income_bracket_probabilities(df_bl, df_rt, "1", "r1")
        mod.report_fallback_rates()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("per raumtyp base cell (n=2): fallback zero_base_equal_weight 2 (100.0%)" in m
               for m in msgs)
    (line,) = _rate_lines(caplog, "income_by_size")
    assert "primary 1/1 (100.0%)" in line.getMessage()  # the call itself was primary


def test_university_commune_without_buildings_counts_as_skipped_and_warns(capsys):
    import geopandas as gpd
    from shapely.geometry import Point
    from braunschweig.data.schools.university_facilities import build_university_facilities

    hoch = pd.DataFrame({
        "institution": ["TU BS", "Elsewhere"], "scope": ["local", "local"],
        "ars5": ["03101", "03999"], "enrollment": [1000.0, 500.0],
        "lon": [float("nan")] * 2, "lat": [float("nan")] * 2,
    })
    osm = gpd.GeoDataFrame({
        "commune_id": ["031010000000"], "weight": [300.0],
        "geometry": [Point(605000, 5790000)],
    }, crs="EPSG:25832")
    gdf = build_university_facilities(hoch, osm)
    assert gdf["capacity"].sum() == pytest.approx(1000.0)  # values unchanged
    out = capsys.readouterr().out
    assert "[university_facilities] WARNING units with OSM buildings: placed 1/2 (50.0%)" in out
    assert "(enrollment not placed) 1 (50.0%)" in out


def test_tenure_unknown_region_counts_each_tenure_once_and_warns(caplog):
    from braunschweig.data.mid import tenure_by_income as mod
    from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES

    def make(regions):
        return pd.DataFrame([
            {"region": r, "tenure": t, "income_bracket": b, "share_pct": 10.0,
             "base_weighted": 100.0}
            for r in regions for t in mod.TENURE_CATEGORIES for b in INCOME_BRACKET_CATEGORIES
        ])

    mod.reset_fallback_rates()
    with caplog.at_level(logging.INFO, logger=mod.logger.name):
        mat = mod.tenure_probabilities_given_income(
            make(["niedersachsen"]), make(["r1"]), "unknown_region")
        mod.report_fallback_rates()
    assert mat.shape == (10, 3)  # untilted NDS base, values unchanged
    lines = [r for r in caplog.records
             if "per tenure bracket pmf (n=3): fallback untilted_base 3 (100.0%)" in r.getMessage()]
    assert len(lines) == 1  # 3 of 3 tenures, not 3 of 6
    assert lines[0].levelno == logging.WARNING


@pytest.mark.parametrize("nan_stays_nan", [False, True])
def test_normalise_or_uniform_nan_row_is_counted_separately(nan_stays_nan):
    from braunschweig.data.mid.reference_tables import _normalise_or_uniform

    counter = [0, 0, 0]
    out = _normalise_or_uniform(np.array([np.nan, 1.0]), counter, nan_stays_nan=nan_stays_nan)
    if nan_stays_nan:
        assert np.isnan(out).all()  # breakdown loaders: NaN is divided through
    else:
        assert out.tolist() == [0.5, 0.5]  # margin loaders: uniform
    assert counter == [0, 1, 1]  # not a zero-sum row, not a normalised row
