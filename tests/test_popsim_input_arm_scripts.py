"""Tests of the driver and the comparison of the input-consistency ladder arms (#658, Task A7).

The two scripts live in the run-manifest artifact folder
``docs/runs/artifacts/popsim-ladder-inputs-a-2026-11-18/scripts/`` and are imported from their
path. ``prepare_input_arm.py`` runs on the synthetic two-batch folders of
``tests/popsim_kreis_batch_fixture.py`` (two Kreise, one split over both batches);
``compare_input_arms.py`` runs on small hand-made diagnostics tables. All values are synthetic.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_kreis_batch_fixture as fixture  # noqa: E402

from braunschweig.popsim import input_consistency, stage  # noqa: E402

SCRIPTS = (Path(__file__).resolve().parents[1] / "docs" / "runs" / "artifacts"
           / "popsim-ladder-inputs-a-2026-11-18" / "scripts")
KREIS_FILE = "data/control_totals_KREIS.csv"
GRID_PAIRS = ("cars_0", "cars_1", "cars_2", "cars_3plus", "bicycles_0", "bicycles_1",
              "bicycles_2", "bicycles_3", "bicycles_4plus", "employed")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_a7_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prep():
    return _load("prepare_input_arm")


@pytest.fixture(scope="module")
def compare():
    return _load("compare_input_arms")


def _fixture_cells_and_kreis_table():
    """The fixture's cell frame and its Kreis table and controls map (as build_batch_folders)."""
    cells = fixture.make_cells()
    _controls, _base, active = fixture._controls_and_base_columns()
    base = fixture.base_fixture
    cells = stage._add_aggregated_control_columns(
        cells, base.CONTROL_SOURCE, base.SEED_NAME, fixture.CONTROL_TIERS, **base.AGGREGATE_KWARGS)
    kreis_table, controls_map, _households = fixture._kreis_tables(cells, active)
    return cells, kreis_table, controls_map


def _write_cells_parquet(path: Path, cells: pd.DataFrame) -> Path:
    """The fixture cells as a prepared-cell parquet: grid id first, then the single-year columns."""
    years = [c for c in cells.columns if c.startswith(("M_AGE_", "F_AGE_"))
             and c.split("_")[-1].isdigit()]
    cells[["ZENSUS100m", *years]].to_parquet(path, index=False)
    return path


def _prepare_args(work: Path, out: Path, parquet: Path, steps: str) -> list[str]:
    return ["--base", str(work / "batch_000"), "--out", str(out), "--steps", steps,
            "--prod-work-dir", str(work), "--cells-100m", str(parquet),
            "--expected-kreise", "2", "--expected-batches", "2", "--commit", "test"]


@pytest.fixture(scope="module")
def legacy_batches(tmp_path_factory):
    work = tmp_path_factory.mktemp("legacy") / "work"
    fixture.build_batch_folders(work)
    cells, _table, _map = _fixture_cells_and_kreis_table()
    parquet = _write_cells_parquet(work.parent / "cells.parquet", cells)
    return work, parquet


def test_kreis_targets_are_reconstructed_from_the_batches(prep, legacy_batches):
    work, _parquet = legacy_batches
    _cells, kreis_table, controls_map = _fixture_cells_and_kreis_table()
    targets = prep.reconstruct_kreis_targets(prep.production_batches(work, expected_batches=2),
                                             expected_kreise=2).set_index("ARS_kreis")
    source = kreis_table.assign(ARS_kreis=kreis_table["ARS_kreis"].astype(str)).set_index("ARS_kreis")
    assert sorted(targets.index) == sorted(source.index)
    assert set(targets.columns) == set(controls_map)
    for name, columns in controls_map.items():
        expected = source.loc[targets.index, list(columns)].fillna(0.0).sum(axis=1)
        np.testing.assert_allclose(targets[name].to_numpy(), expected.to_numpy(), rtol=1e-9,
                                   err_msg=name)


def test_kreis_count_other_than_expected_is_an_error(prep, legacy_batches):
    work, _parquet = legacy_batches
    with pytest.raises(ValueError, match="Kreis"):
        prep.reconstruct_kreis_targets(prep.production_batches(work, expected_batches=2),
                                       expected_kreise=8)


def test_prepare_changes_only_the_kreis_table_and_the_grid_pairs_agree(prep, legacy_batches, tmp_path):
    work, parquet = legacy_batches
    out = tmp_path / "arm" / "batch_000"
    assert prep.main(_prepare_args(work, out, parquet, "kreis_universe,kreis_follow_grids")) == 0
    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    changed = sorted(name for name, entry in log["files"].items() if entry["changed"])
    assert changed == [KREIS_FILE]
    assert log["unexpected_changed_files"] == []
    assert log["steps"] == ["kreis_universe", "kreis_follow_grids"]
    assert log["commit"] == "test"
    assert any(entry["changed"] for entry in log["kreis_columns"].values())
    assert log["stats"]["kreis_follow_grids"]["n_controls"] == len(GRID_PAIRS)
    assert log["stats"]["kreis_universe"]["n_parts"] > 0

    data = out / "data"
    pairs = input_consistency.kreis_vs_fine(
        fixture.read_table(data, "KREIS").set_index("KREIS"),
        fixture.read_table(data, "ZENSUS100m").set_index("ZENSUS100m"),
        pd.read_csv(data / "geo_cross_walk.csv", dtype=str),
        cells_1km=fixture.read_table(data, "ZENSUS1km").set_index("ZENSUS1km"))
    grid = pairs[pairs["pair"].isin(GRID_PAIRS)]
    assert set(grid["pair"]) == set(GRID_PAIRS)
    assert np.allclose(grid["rel_diff"].to_numpy(dtype=float), 0.0, atol=1e-12)

    # The driver reproduces what assemble_batch_folder writes with universe + follow-grids on.
    settings = stage.KreisConsistencySettings("universe", True, "survey", 0.5, 0)
    pipeline = fixture.build_batch_folders(tmp_path / "pipeline", settings=settings)
    expected = fixture.read_table(pipeline["batch_000"], "KREIS").set_index("KREIS").sort_index()
    got = fixture.read_table(data, "KREIS").set_index("KREIS").sort_index()
    assert list(got.columns) == list(expected.columns)
    np.testing.assert_allclose(got.to_numpy(dtype=float), expected.to_numpy(dtype=float),
                               rtol=1e-9, atol=1e-9)


def test_an_unexpected_changed_file_makes_prepare_exit_1(prep, legacy_batches, tmp_path, monkeypatch):
    work, parquet = legacy_batches
    out = tmp_path / "arm" / "batch_000"
    original = prep.write_kreis_table

    def write_and_touch(folder, table):
        original(folder, table)
        with (Path(folder) / "configs" / "controls.csv").open("a", encoding="utf-8") as handle:
            handle.write("\n")

    monkeypatch.setattr(prep, "write_kreis_table", write_and_touch)
    assert prep.main(_prepare_args(work, out, parquet, "kreis_follow_grids")) == 1
    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    assert log["unexpected_changed_files"] == ["configs/controls.csv"]


def test_prepare_refuses_an_existing_out_folder(prep, legacy_batches, tmp_path):
    work, parquet = legacy_batches
    out = tmp_path / "arm" / "batch_000"
    out.mkdir(parents=True)
    assert prep.main(_prepare_args(work, out, parquet, "kreis_follow_grids")) == 1


# ---------------------------------------------------------------------------------------------
# compare_input_arms
# ---------------------------------------------------------------------------------------------

def _fit_rows(batch, rows):
    return [{"scope": "batch", "batch": batch, "control": c, "geography": g, "importance": 1,
             "target_sum": t, "synthetic_sum": s, "abs_error": e} for c, g, t, s, e in rows]


def _write_diag(directory: Path, fit_rows, consistency_rows) -> Path:
    directory.mkdir(parents=True)
    pd.DataFrame(fit_rows).to_csv(directory / "control_fit.csv", index=False)
    pd.DataFrame(consistency_rows, columns=["batch", "scope", "key", "check", "cells", "amount",
                                            "base", "share"]).to_csv(
        directory / "input_consistency.csv", index=False)
    return directory


def _diag_tables(tmp_path):
    base_fit = _fit_rows("batch_000", [
        ("M_AGE_0_9", "ZENSUS100m", 100.0, 95.0, 10.0),
        ("M_AGE_20_29", "ZENSUS100m", 200.0, 190.0, 20.0),
        ("own_cars_0", "ZENSUS1km", 50.0, 52.0, 4.0),
        ("hh_total", "ZENSUS100m", 100.0, 100.0, 0.0),
        ("employed_KREIS", "KREIS", 80.0, 70.0, 10.0),
    ])
    new_fit = _fit_rows("batch_000", [
        ("M_AGE_0_9", "ZENSUS100m", 100.0, 99.0, 11.0),
        ("M_AGE_20_29", "ZENSUS100m", 200.0, 170.0, 40.0),
        ("own_cars_0", "ZENSUS1km", 50.0, 53.0, 5.0),
        ("hh_total", "ZENSUS100m", 100.0, 100.0, 0.0),
        ("employed_KREIS", "KREIS", 80.0, 79.0, 1.0),
    ])
    consistency = [
        ["batch_000", "kreis", "03101", "cars_0", 0, 0.0, 10.0, 0.0],
        ["batch_000", "kreis", "03101", "employed", 1, 2.0, 40.0, 0.05],
        ["batch_000", "kreis", "03101", "persons_0_17", 0, 0.0, 30.0, 0.0],
    ]
    base = _write_diag(tmp_path / "base", base_fit, consistency)
    new = _write_diag(tmp_path / "new", new_fit, consistency)
    return base, new


def test_compare_input_arms_gives_the_expected_verdict_per_check(compare, tmp_path):
    base, new = _diag_tables(tmp_path)
    checks = {"checks": [
        {"id": "I-cars", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["cars_0"], "bound": 1e-6},
        {"id": "I-employed", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["employed"], "bound": 1e-6},
        {"id": "O-age-0-17", "kind": "fit_ratio", "geography": "ZENSUS100m",
         "controls": ["M_AGE_0_9"], "bound": 0.02},
        {"id": "O-age-18plus", "kind": "fit_ratio", "geography": "ZENSUS100m",
         "controls": ["M_AGE_20_29"], "bound": 0.02},
        {"id": "O-own", "kind": "fit_gap", "geography": "ZENSUS1km", "controls": ["own_cars_0"],
         "measure": "net", "households_control": "hh_total", "bound": 0.05},
        {"id": "O-own-tight", "kind": "fit_gap", "geography": "ZENSUS1km",
         "controls": ["own_cars_0"], "measure": "abs_error", "households_control": "hh_total",
         "bound": 0.04},
        {"id": "N", "kind": "wape_vs_base", "deltas": {"ZENSUS100m": 0.02},
         "exclude_controls": ["own_cars_0"]},
        {"id": "N-age-excluded", "kind": "wape_vs_base", "deltas": {"ZENSUS100m": 0.02},
         "exclude_regex": "^M_AGE_20"},
    ]}
    checks_path = tmp_path / "checks.json"
    checks_path.write_text(json.dumps(checks), encoding="utf-8")
    out = tmp_path / "out"
    assert compare.main(["--base-diag", str(base), "--new-diag", str(new), "--checks",
                         str(checks_path), "--out", str(out)]) == 0
    verdict = json.loads((out / "verdict.json").read_text(encoding="utf-8"))
    met = {key: value["met"] for key, value in verdict["checks"].items()}
    assert met == {"I-cars": True, "I-employed": False, "O-age-0-17": True, "O-age-18plus": False,
                   "O-own": True, "O-own-tight": False, "N": False, "N-age-excluded": True}
    assert verdict["all_met"] is False
    table = pd.read_csv(out / "comparison.csv")
    assert {"check", "batch", "arm", "metric", "value", "limit", "met"} <= set(table.columns)


def test_compare_input_arms_fails_a_check_whose_controls_are_absent(compare, tmp_path):
    base, new = _diag_tables(tmp_path)
    checks_path = tmp_path / "checks.json"
    checks_path.write_text(json.dumps({"checks": [
        {"id": "O-missing", "kind": "fit_ratio", "geography": "ZENSUS100m",
         "controls": ["not_a_control"], "bound": 0.5}]}), encoding="utf-8")
    out = tmp_path / "out"
    assert compare.main(["--base-diag", str(base), "--new-diag", str(new), "--checks",
                         str(checks_path), "--out", str(out)]) == 0
    verdict = json.loads((out / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["O-missing"]["met"] is False
    assert verdict["checks"]["O-missing"]["missing_controls"] == {"batch_000": ["not_a_control"]}
