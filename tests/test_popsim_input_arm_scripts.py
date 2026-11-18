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
import shutil
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
    """The fixture cells as a prepared-cell parquet: grid id first, then the age columns.

    The single-year columns feed the universe weights, the band columns the trip_class person
    total (``person_total_by_kreis``).
    """
    ages = [c for c in cells.columns if c.startswith(("M_AGE_", "F_AGE_"))]
    cells[["ZENSUS100m", *ages]].to_parquet(path, index=False)
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
    # The trip_class partition assertion bites: persons 5 above the parts fail the 1-person bound.
    trip = targets[[c for c in targets.columns if c.startswith("trip_class_")]].sum(axis=1)
    with pytest.raises(ValueError, match="trip_class"):
        prep.check_trip_class_partition(targets.reset_index(), (trip + 5.0).to_dict(),
                                        tolerance_persons=1.0)


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
    # trip_class is active in the fixture, so the partition assertion ran on the pipeline's
    # person total (band columns, fine teen bands read from controls.csv).
    partition = log["kreis_targets"]["trip_class_partition"]
    assert partition["active"] is True and partition["max_abs_deviation_persons"] <= 1.0
    assert log["kreis_targets"]["fine_teen_age_bands"] is True

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


def test_employment_level_step_uses_the_seed_bridge_and_refuses_a_stratified_run(
        prep, legacy_batches, tmp_path):
    work, parquet = legacy_batches
    base = tmp_path / "base" / "batch_000"
    for sub in ("configs", "data"):
        shutil.copytree(work / "batch_000" / sub, base / sub)
    # Minimal seed (synthetic). Grid universe 16+: employed class (employed_*) weights 2 + 1,
    # P_TAET employed (code 1) weights 2 + 2 -> bridge 3 / 4 = 0.75 (hand-computed).
    pd.DataFrame({"H_ID": [1, 2, 3], "H_GEW": [2.0, 2.0, 1.0], "STAAT": [1, 1, 1]}).to_csv(
        base / "data" / "seed_households.csv", index=False)
    pd.DataFrame({"H_ID": [1, 2, 3], "P_ID": [1, 2, 3], "STAAT": [1, 1, 1],
                  "HP_ALTER": [30, 40, 50], "P_TAET": [1, 1, 5],
                  "work_by_employment": ["employed_work", "nonemployed_nowork",
                                         "employed_nowork"]}).to_csv(
        base / "data" / "seed_persons.csv", index=False)
    args = ["--base", str(base), "--steps", "employment_level", "--prod-work-dir", str(work),
            "--expected-batches", "2", "--commit", "test"]
    out = tmp_path / "arm" / "batch_000"
    assert prep.main([*args, "--out", str(out)]) == 0
    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    assert log["bridge"]["bridge"] == pytest.approx(0.75)
    assert log["bridge"]["source"] == "work_by_employment"
    assert log["stats"]["employment_level"]["parts_reconciled"] > 0
    assert sorted(n for n, e in log["files"].items() if e["changed"]) == [KREIS_FILE]
    assert log["seed_check"]["data/seed_persons.csv"]["base_equals_production"] is False

    # A stratified production run carries a different seed per batch: the step must refuse.
    stratified = tmp_path / "stratified"
    shutil.copytree(work, stratified)
    with (stratified / "batch_001" / "data" / "seed_persons.csv").open("a", encoding="utf-8") as f:
        f.write("2,2,1\n")
    with pytest.raises(ValueError, match="stratified"):
        prep.main(["--base", str(base), "--steps", "employment_level", "--prod-work-dir",
                   str(stratified), "--expected-batches", "2", "--commit", "test",
                   "--out", str(tmp_path / "arm2" / "batch_000")])


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


def _write_diag(directory: Path, fit_rows, consistency_rows, conservation_rows, solver_rows) -> Path:
    directory.mkdir(parents=True)
    pd.DataFrame(fit_rows).to_csv(directory / "control_fit.csv", index=False)
    pd.DataFrame(consistency_rows, columns=["batch", "scope", "key", "check", "cells", "amount",
                                            "base", "share"]).to_csv(
        directory / "input_consistency.csv", index=False)
    pd.DataFrame(conservation_rows, columns=["batch", "level", "abs_deviation_share",
                                             "conserved_pair_share"]).to_csv(
        directory / "household_conservation.csv", index=False)
    pd.DataFrame(solver_rows, columns=["batch", "level", "n_parents", "non_simultaneous"]).to_csv(
        directory / "solver_paths.csv", index=False)
    return directory


def _diag_tables(tmp_path, new_extra_fit=()):
    """Hand-made diagnostics of two arms; every expected verdict below is hand-computed."""
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
        *new_extra_fit,
    ])
    consistency = [
        ["batch_000", "kreis", "03101", "cars_0", 0, 0.0, 10.0, 0.0],
        ["batch_000", "kreis", "03101", "employed", 1, 2.0, 40.0, 0.05],
        ["batch_000", "kreis", "03101", "persons_0_17", 0, 0.0, 3000.0, 0.0],
        # a 200-person part at 5 %: ungated under min_base 1000
        ["batch_000", "kreis", "03158", "persons_0_17", 1, 10.0, 200.0, 0.05],
    ]
    base_conservation = [["batch_000", "ZENSUS1km", 0.01, 0.90], ["batch_000", "ZENSUS100m", 0.02, 0.80]]
    new_conservation = [["batch_000", "ZENSUS1km", 0.01, 0.90], ["batch_000", "ZENSUS100m", 0.03, 0.80]]
    solver = [["batch_000", "ZENSUS1km", 10, 0], ["batch_000", "ZENSUS100m", 50, 2]]
    base = _write_diag(tmp_path / "base", base_fit, consistency, base_conservation, solver)
    new = _write_diag(tmp_path / "new", new_fit, consistency, new_conservation, solver)
    return base, new


def _run_compare(compare, tmp_path, base, new, spec):
    tmp_path.mkdir(parents=True, exist_ok=True)
    checks_path = tmp_path / "checks.json"
    checks_path.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "out"
    rc = compare.main(["--base-diag", str(base), "--new-diag", str(new), "--checks",
                       str(checks_path), "--out", str(out)])
    verdict = (json.loads((out / "verdict.json").read_text(encoding="utf-8"))
               if (out / "verdict.json").is_file() else None)
    return rc, verdict, out


def test_compare_input_arms_gives_the_expected_verdict_per_check(compare, tmp_path):
    base, new = _diag_tables(tmp_path)
    own = {"kind": "fit_gap", "geography": "ZENSUS1km", "controls": ["own_cars_0"],
           "households_control": "hh_total", "households_geography": "ZENSUS100m"}
    rc, verdict, out = _run_compare(compare, tmp_path, base, new, {"checks": [
        {"id": "I-cars", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["cars_0"], "bound": 1e-6},
        {"id": "I-employed", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["employed"], "bound": 1e-6},
        {"id": "I-age-large-parts", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["persons_0_17"], "bound": 0.01, "min_base": 1000},
        {"id": "I-age-all-parts", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["persons_0_17"], "bound": 0.01},
        {"id": "O-age-0-17", "kind": "fit_ratio", "geography": "ZENSUS100m",
         "controls": ["M_AGE_0_9"], "bound": 0.02},
        {"id": "O-age-18plus", "kind": "fit_ratio", "geography": "ZENSUS100m",
         "controls": ["M_AGE_20_29"], "bound": 0.02},
        {"id": "O-own", **own, "measure": "net", "bound": 0.05},
        {"id": "O-own-tight", **own, "measure": "abs_error", "bound": 0.04},
        {"id": "N", "kind": "wape_vs_base", "deltas": {"ZENSUS100m": 0.02},
         "exclude_controls": ["own_cars_0"]},
        {"id": "N-age-excluded", "kind": "wape_vs_base", "deltas": {"ZENSUS100m": 0.02},
         "exclude_regex": "^M_AGE_20"},
        {"id": "C-strict", "kind": "conservation_vs_base", "abs_deviation_share_delta": 0.0,
         "conserved_pair_share_delta": 0.0},
        {"id": "C-1km", "kind": "conservation_vs_base", "abs_deviation_share_delta": 0.0,
         "conserved_pair_share_delta": 0.0, "levels": ["ZENSUS1km"]},
        {"id": "S-all", "kind": "no_sequential_fallback"},
        {"id": "S-1km", "kind": "no_sequential_fallback", "levels": ["ZENSUS1km"]},
    ]})
    assert rc == 0
    met = {key: value["met"] for key, value in verdict["checks"].items()}
    assert met == {"I-cars": True, "I-employed": False, "I-age-large-parts": True,
                   "I-age-all-parts": False, "O-age-0-17": True, "O-age-18plus": False,
                   "O-own": True, "O-own-tight": False, "N": False, "N-age-excluded": True,
                   "C-strict": False, "C-1km": True, "S-all": False, "S-1km": True}
    assert verdict["all_met"] is False
    table = pd.read_csv(out / "comparison.csv")
    assert {"check", "batch", "arm", "metric", "value", "limit", "met"} <= set(table.columns)
    ungated = table[(table["check"] == "I-age-large-parts")
                    & table["metric"].str.startswith("max_share_ungated")]
    assert len(ungated) == 2 and ungated["met"].isna().all()
    assert ungated["value"].tolist() == [0.05, 0.05]


def test_compare_input_arms_fails_incomplete_or_incomparable_inputs(compare, tmp_path):
    base, new = _diag_tables(tmp_path, new_extra_fit=[("extra", "ZENSUS100m", 10.0, 10.0, 0.0)])
    rc, verdict, _out = _run_compare(compare, tmp_path, base, new, {"checks": [
        {"id": "O-missing", "kind": "fit_ratio", "geography": "ZENSUS100m",
         "controls": ["not_a_control"], "bound": 0.5},
        {"id": "O-own-households-wrong-geography", "kind": "fit_gap", "geography": "ZENSUS1km",
         "controls": ["own_cars_0"], "measure": "net", "households_control": "hh_total",
         "households_geography": "ZENSUS1km", "bound": 1.0},
        {"id": "N-control-sets-differ", "kind": "wape_vs_base", "deltas": {"ZENSUS100m": 0.02},
         "exclude_regex": "^M_AGE_20"},
    ]})
    assert rc == 0
    checks = verdict["checks"]
    assert checks["O-missing"]["met"] is False
    assert checks["O-missing"]["missing_controls"] == {"batch_000": ["not_a_control"]}
    assert checks["O-own-households-wrong-geography"]["met"] is False
    assert checks["O-own-households-wrong-geography"]["missing_controls"] == {
        "batch_000": ["hh_total@ZENSUS1km"]}
    sets = checks["N-control-sets-differ"]["values"]["ZENSUS100m"]
    assert checks["N-control-sets-differ"]["met"] is False
    assert sets["controls_only_in_new"] == ["extra"] and sets["controls_only_in_base"] == []

    # A requested batch absent from an arm is an error, not a silent intersection.
    rc, verdict, _out = _run_compare(compare, tmp_path / "b", base, new, {
        "batches": ["batch_000", "batch_001"],
        "checks": [{"id": "S", "kind": "no_sequential_fallback"}]})
    assert rc == 1 and verdict is None


def test_compare_input_arms_never_passes_a_nan_base_silently(compare, tmp_path):
    base, new = _diag_tables(tmp_path)
    table = pd.read_csv(new / "input_consistency.csv", dtype={"key": str})
    table.loc[(table["check"] == "cars_0"), "base"] = np.nan     # the only cars_0 row of the new arm
    table.to_csv(new / "input_consistency.csv", index=False)
    rc, verdict, out = _run_compare(compare, tmp_path, base, new, {"checks": [
        {"id": "I-cars", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["cars_0"], "bound": 1e-6}]})
    assert rc == 0 and verdict["checks"]["I-cars"]["met"] is False
    assert verdict["checks"]["I-cars"]["values"]["new"]["rows_with_nan_base"] == 1
    rows = pd.read_csv(out / "comparison.csv")
    nan_row = rows[(rows["metric"] == "rows_with_nan_base") & (rows["arm"] == "new")]
    assert nan_row["value"].tolist() == [1] and nan_row["met"].tolist() == [False]


def test_compare_input_arms_names_an_absent_base_file_instead_of_a_traceback(compare, tmp_path):
    base, new = _diag_tables(tmp_path)
    for name in ("household_conservation.csv", "solver_paths.csv", "input_consistency.csv"):
        (base / name).unlink()
    rc, verdict, _out = _run_compare(compare, tmp_path, base, new, {"checks": [
        {"id": "C", "kind": "conservation_vs_base", "abs_deviation_share_delta": 0.0,
         "conserved_pair_share_delta": 0.0},
        {"id": "S", "kind": "no_sequential_fallback"},
        {"id": "I", "kind": "input_pairs", "file": "input_consistency.csv",
         "pairs": ["cars_0"], "bound": 1e-6}]})
    assert rc == 0 and verdict["all_met"] is False
    for check, name in (("C", "household_conservation.csv"), ("S", "solver_paths.csv"),
                        ("I", "input_consistency.csv")):
        entry = verdict["checks"][check]
        assert entry["met"] is False and str(base / name) in entry["absent_files"]["base"]


def test_no_sequential_fallback_reports_the_base_arm_ungated(compare, tmp_path):
    base, new = _diag_tables(tmp_path)
    rc, _verdict, out = _run_compare(compare, tmp_path, base, new, {"checks": [
        {"id": "S", "kind": "no_sequential_fallback"}]})
    rows = pd.read_csv(out / "comparison.csv")
    assert rc == 0
    assert rows[rows["arm"] == "base"]["met"].isna().all()
    assert rows[rows["arm"] == "new"]["met"].notna().all()


@pytest.mark.parametrize("new_outside, drop_new_file, expected_met", [
    (1, False, True),    # equal counts (base has one control outside, too)
    (2, False, False),   # one more control outside than the base
    (1, True, False),    # rounding_shift.csv absent in the new arm
])
def test_rounding_shift_vs_base_compares_the_outside_counts(
        compare, tmp_path, new_outside, drop_new_file, expected_met):
    base, new = _diag_tables(tmp_path)

    def shift_rows(n_outside):
        # the first n_outside of three controls lie outside the bound
        return pd.DataFrame({"batch": "batch_000", "control": ["c1", "c2", "c3"],
                             "target_sum": 100.0, "shift": [0.3, 0.2, 0.01],
                             "within_bound": [i >= n_outside for i in range(3)]})

    shift_rows(1).to_csv(base / "rounding_shift.csv", index=False)
    if not drop_new_file:
        shift_rows(new_outside).to_csv(new / "rounding_shift.csv", index=False)
    rc, verdict, out = _run_compare(compare, tmp_path, base, new, {"checks": [
        {"id": "G4", "kind": "rounding_shift_vs_base", "max_extra_outside": 0}]})
    assert rc == 0 and verdict["checks"]["G4"]["met"] is expected_met
    if drop_new_file:
        assert str(new / "rounding_shift.csv") in verdict["checks"]["G4"]["absent_files"]["new"]
    else:
        rows = pd.read_csv(out / "comparison.csv")
        n_out = rows[rows["metric"] == "n_outside"].set_index("arm")["value"]
        assert (n_out["base"], n_out["new"]) == (1, new_outside)
        assert rows[rows["arm"] == "base"]["met"].isna().all()
