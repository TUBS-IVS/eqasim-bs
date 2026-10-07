"""Which age shape the popsim employment grid rescales to (ADR-0137).

Until ADR-0137 the employment grid split each Kreis's employed persons across age groups with an
exact shape for the three kreisfreie Staedte only; the five Landkreise took a national one. The
Zensus 2022 Regionaltabelle publishes the shape for every Kreis and per sex, so the default source
now gives every Kreis its own shape per sex. The previous source stays selectable and must keep
its results exactly.
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import employment_grid as eg
from braunschweig.popsim import stage as popsim_stage
from braunschweig.popsim import zensus_employment_age as za

KREIS_DIR_KEY = popsim_stage.KEY_KREIS_CONTROLS
SOURCE_KEY = popsim_stage.KEY_EMPLOYMENT_GRID_AGE_SHAPE_SOURCE


class _Context:
    def __init__(self, values):
        self.values = values

    def config(self, key, default=None, volatile=False):
        return self.values[key]


def _write_inputs(tmp_path, kreis_rows=True):
    kreis_dir = tmp_path / "kreis_controls"
    kreis_dir.mkdir(parents=True)
    pd.DataFrame({"ARS_kreis": ["03101", "03151"],
                  "ERWERBSTAT_KURZ_STP__11_M": [100.0, 80.0],
                  "ERWERBSTAT_KURZ_STP__11_W": [90.0, 60.0]}).to_parquet(
        kreis_dir / "kreis_erwerbsstatus.parquet")
    popsim_dir = tmp_path / "data" / "braunschweig" / "popsim"
    popsim_dir.mkdir(parents=True)
    pd.DataFrame([
        ("03101", "20-29", 100, 30, 0.3), ("03101", "40-49", 100, 50, 0.5), ("03101", "60-69", 100, 20, 0.2),
        ("DE_large_gemeinden", "20-29", 100, 40, 0.4), ("DE_large_gemeinden", "40-49", 100, 40, 0.4),
        ("DE_large_gemeinden", "60-69", 100, 20, 0.2),
    ], columns=["region", "age_band", "total", "erwerbstaetige", "rate"]).to_csv(
        popsim_dir / "zensus2022_employment_by_age_ref.csv", index=False)
    rows = []
    regions = [("03101", "Braunschweig, Stadt"), ("00", "Deutschland")]
    if kreis_rows:
        regions.insert(1, ("03151", "Gifhorn"))
    for region, name in regions:
        for sex, counts in (("M", [1, 9, 10, 30, 30, 15, 5]), ("F", [2, 8, 20, 20, 30, 15, 5])):
            rows += [(region, name, age_class, sex, count)
                     for age_class, count in zip(za.KREIS_AGE_CLASSES, counts)]
    with open(popsim_dir / za.KREIS_EMPLOYED_BY_AGE_FILE, "w", encoding="utf-8", newline="") as handle:
        handle.write("# synthetic reference for the test\n")
        pd.DataFrame(rows, columns=["region", "region_name", "age_class", "sex",
                                    "employed_persons"]).to_csv(handle, index=False)
    return str(kreis_dir), str(tmp_path / "data")


def _cells():
    return pd.DataFrame({
        "ZENSUS100m": ["bs1", "bs2", "gf1"],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000", "031510009009"],
        # Every age group populated in every Kreis: a group without residents gets no target.
        "M_AGE_25": [10, 30, 20], "M_AGE_35": [20, 10, 30], "M_AGE_45": [50, 20, 40],
        "M_AGE_55": [30, 30, 30], "M_AGE_63": [5, 5, 10],
        "F_AGE_25": [10, 10, 20], "F_AGE_35": [15, 15, 25], "F_AGE_45": [30, 20, 30],
        "F_AGE_55": [25, 25, 35], "F_AGE_63": [5, 5, 10],
    })


def _inject(tmp_path, source, **inputs):
    kreis_dir, data_path = _write_inputs(tmp_path, **inputs)
    context = _Context({KREIS_DIR_KEY: kreis_dir, "data_path": data_path, SOURCE_KEY: source})
    return popsim_stage._inject_employment_grid_columns(context, _cells(), True)


def test_the_default_source_gives_every_kreis_its_own_shape_per_sex(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        cells = _inject(tmp_path, popsim_stage.EMPLOYMENT_GRID_AGE_SHAPE_KREIS_BY_SEX)

    gifhorn = cells[cells["KREIS"] == "03151"]
    # Gifhorn men: 80 employed, 50_59 share 30/100 -> 24; women: 60 employed, 30/100 -> 18.
    assert gifhorn["EMPLOYED_M_50_59_agg"].sum() == pytest.approx(24.0)
    assert gifhorn["EMPLOYED_F_50_59_agg"].sum() == pytest.approx(18.0)
    # 30_39 differs by sex (10/100 vs 20/100) -- one shared shape could not produce this.
    assert gifhorn["EMPLOYED_M_30_39_agg"].sum() == pytest.approx(8.0)
    assert gifhorn["EMPLOYED_F_30_39_agg"].sum() == pytest.approx(12.0)
    assert "2/2 Kreise exact, 0 fell back to the national shape" in caplog.text


def test_the_previous_source_keeps_its_results_exactly(tmp_path):
    cells = _inject(tmp_path, popsim_stage.EMPLOYMENT_GRID_AGE_SHAPE_ZENSUS_2000S_2001)

    kreis_dir, data_path = _write_inputs(tmp_path / "expected")
    reference = f"{data_path}/braunschweig/popsim/zensus2022_employment_by_age_ref.csv"
    expected_cells = _cells()
    expected_cells["KREIS"] = expected_cells["RegionalSchlussel_ARS"].str[:5]
    levels = pd.read_parquet(f"{kreis_dir}/kreis_erwerbsstatus.parquet")
    expected = eg.add_employment_grid_columns(
        expected_cells, levels, {k: za.load_age_shares(reference, k) for k in ("03101", "03151")},
        kreis_col="KREIS")

    pd.testing.assert_frame_equal(cells, expected)


def test_a_national_fallback_under_the_new_source_is_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        _inject(tmp_path, popsim_stage.EMPLOYMENT_GRID_AGE_SHAPE_KREIS_BY_SEX, kreis_rows=False)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any("03151" in record.getMessage() and "national" in record.getMessage()
               for record in warnings), caplog.text


def test_an_unknown_source_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=SOURCE_KEY):
        _inject(tmp_path, "svb")


def test_the_stage_declares_the_source_with_the_exact_per_sex_default():
    declared = {}

    class _Recording:
        def config(self, key, default=None, volatile=False):
            declared[key] = default
            return default

        def stage(self, *args, **kwargs):
            return None

    popsim_stage.configure(_Recording())
    assert declared[SOURCE_KEY] == popsim_stage.EMPLOYMENT_GRID_AGE_SHAPE_KREIS_BY_SEX
