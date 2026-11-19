"""The household-universe arm scripts of issue #659 (``docs/runs/artifacts/popsim-ladder-inputs-b-2026-11-19/scripts``).

Tiny synthetic batch folders (4 cells in 2 squares of Kreis 03101, one cell of another Kreis in the grid)
and tiny hand-written diagnostics. ``move_persons`` is tested on its pure core (``rebuild_person_tables``,
``household_cells_check``): its full run needs the backbone controls and the employed-grid census inputs.
"""
import importlib.util
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim import control_spec, mid
from braunschweig.popsim import minor_child_targets as mct

SCRIPTS = (Path(__file__).resolve().parents[1] / "docs" / "runs" / "artifacts"
           / "popsim-ladder-inputs-b-2026-11-19" / "scripts")
HH_TOTAL = control_spec.HH_TOTAL_CENSUS_COLUMN
PARENT_1 = "CRS3035RES1000mN2689000E4337000"
PARENT_2 = "CRS3035RES1000mN2690000E4338000"
PARENT_X = "CRS3035RES1000mN2700000E4300000"
A1, A2 = "CRS3035RES100mN2689100E4337000", "CRS3035RES100mN2689200E4337000"
B1, B2 = "CRS3035RES100mN2690100E4338000", "CRS3035RES100mN2690200E4338000"
X1 = "CRS3035RES100mN2700100E4300000"
FAMILY_FIELDS = [f"{base}_ZENSUS100m" for base, _ in control_spec._TIER1_HH_TYPE_ENTRIES]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_batch_folder(base: Path) -> None:
    for sub in ("configs", "data", "output"):
        (base / sub).mkdir(parents=True)
    rows = [{"target": f"{HH_TOTAL}_ZENSUS100m_target", "geography": "ZENSUS100m",
             "seed_table": "households", "importance": 1000000000, "control_field": f"{HH_TOTAL}_ZENSUS100m",
             "expression": "(households.H_ID > 0)"}]
    for (base_name, cls), field in zip(control_spec._TIER1_HH_TYPE_ENTRIES, FAMILY_FIELDS):
        rows.append({"target": f"{field}_target", "geography": "ZENSUS100m", "seed_table": "households",
                     "importance": 200, "control_field": field,
                     "expression": f"(households.hh_type5 == '{cls}')"})
    pd.DataFrame(rows).to_csv(base / "configs" / "controls.csv", index=False)
    (base / "configs" / "settings.yaml").write_text("geographies: [ZENSUS100m]\n")
    order = [A2, B1, A1, B2]  # differs from the grid's order: the rewrite must reindex
    hh = {A1: 10, A2: 30, B1: 20, B2: 15}
    pd.DataFrame({"ZENSUS100m": order, f"{HH_TOTAL}_ZENSUS100m": [hh[i] for i in order]}).to_csv(
        base / "data" / "control_totals_ZENSUS100m.csv", index=False)
    pd.DataFrame({"ZENSUS1km": [PARENT_2, PARENT_1], f"{HH_TOTAL}_ZENSUS1km": [35, 40]}).to_csv(
        base / "data" / "control_totals_ZENSUS1km.csv", index=False)
    for geo in ("KREIS", "STAAT", "WELT"):
        (base / "data" / f"control_totals_{geo}.csv").write_text(f"{geo}\n1\n")
    pd.DataFrame({"ZENSUS100m": [A1, A2, B1, B2], "ZENSUS1km": [PARENT_1, PARENT_1, PARENT_2, PARENT_2],
                  "KREIS": "03101", "STAAT": 1, "WELT": 1}).to_csv(base / "data" / "geo_cross_walk.csv",
                                                                   index=False)
    pd.DataFrame({"H_ID": [1, 2, 3, 4], "H_GR": [2, 3, 1, 1]}).to_csv(
        base / "data" / "seed_households.csv", index=False)
    # Household 1: one adult + one minor; 2: two adults + one minor; 3: one adult; 4: a minor alone.
    pd.DataFrame({"H_ID": [1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 4.0],
                  "HP_ALTER": [40, 8, 35, 36, 3, 70, 16]}).to_csv(base / "data" / "seed_persons.csv",
                                                                  index=False)


def write_grids(folder: Path) -> tuple:
    """The 100 m grid (the four cells and one cell of Kreis 03102) and the 1 km family table.

    Square shares: P1 lone (2+8)/100 = 0.1, couples (15+0+15)/100 = 0.3; P2 lone 5/50 = 0.1,
    couples 10/50 = 0.2. Targets one/multi: a1 1/3, a2 3/9, b1 2/4, b2 1.5/3.
    """
    cells_100m, cells_1km = folder / "grid_100m.parquet", folder / "grid_1km.parquet"
    pd.DataFrame({
        "GITTER_ID_100m": [A1, A2, X1, B1, B2],
        mid._ARS_COLUMN: ["031010001001", "031010001001", "031020001001", "031010002002", "031010002002"],
        HH_TOTAL: [10.0, 30.0, 50.0, 20.0, 15.0],
    }).to_parquet(cells_100m)
    squares = {mct.SQUARE_ID_COLUMN: [PARENT_1, PARENT_2, PARENT_X]}
    lone_father, lone_mother = mct.LONE_PARENT_SOURCES
    married, partnership, cohabiting = mct.COUPLE_SOURCES
    squares.update({lone_father: [2, 0, 1], lone_mother: [8, 5, 1], married: [15, 10, 1],
                    partnership: [0, 0, 0], cohabiting: [15, 0, 1],
                    mct.FAMILY_TABLE_HOUSEHOLDS: [100, 50, 20]})
    pd.DataFrame(squares).to_parquet(cells_1km)
    return cells_100m, cells_1km


def run_prepare(tmp_path, steps, extra=()):
    prepare = load_script("prepare_household_arm")
    base, out = tmp_path / "base" / "batch_000", tmp_path / "new" / "batch_000"
    write_batch_folder(base)
    cells_100m, cells_1km = write_grids(tmp_path)
    rc = prepare.main(["--base", str(base), "--out", str(out), "--steps", steps, "--kreise", "03101",
                       "--cells-100m", str(cells_100m), "--cells-1km", str(cells_1km),
                       "--commit", "abc123", *extra])
    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    return prepare, base, out, rc, log


def test_family_none_removes_exactly_the_four_family_rows(tmp_path):
    _, base, out, rc, log = run_prepare(tmp_path, "family_none")
    assert rc == 0, log["problems"]
    before = pd.read_csv(base / "configs" / "controls.csv")
    after = pd.read_csv(out / "configs" / "controls.csv")
    assert sorted(set(before["control_field"]) - set(after["control_field"])) == sorted(FAMILY_FIELDS)
    assert len(after) == len(before) - 4
    changed = [name for name, entry in log["files"].items() if entry["changed"]]
    assert changed == ["configs/controls.csv"]
    assert log["ok"] and not log["problems"]


def test_family_minor_child_adds_the_library_rows_columns_and_seed_class(tmp_path):
    _, base, out, rc, log = run_prepare(tmp_path, "family_minor_child")
    assert rc == 0, log["problems"]
    controls = pd.read_csv(out / "configs" / "controls.csv")
    expected = control_spec.render_catalog_csv(control_spec.minor_child_controls(), "mid")
    added = controls[controls["control_field"].isin(expected["control_field"])].reset_index(drop=True)
    assert added["expression"].tolist() == expected["expression"].tolist()
    assert added["geography"].tolist() == ["ZENSUS1km", "ZENSUS1km"]
    assert added["importance"].tolist() == [200, 200]  # the removed rows' hhtype importance
    assert not set(FAMILY_FIELDS) & set(controls["control_field"])

    t100 = pd.read_csv(out / "data" / "control_totals_ZENSUS100m.csv")
    t1km = pd.read_csv(out / "data" / "control_totals_ZENSUS1km.csv").set_index("ZENSUS1km")
    xwalk = pd.read_csv(out / "data" / "geo_cross_walk.csv").set_index("ZENSUS100m")["ZENSUS1km"]
    for column in (mct.ONE_ADULT_TARGET, mct.MULTI_ADULT_TARGET):
        sums = t100.groupby(t100["ZENSUS100m"].map(xwalk))[f"{column}_ZENSUS100m"].sum()
        assert sums.to_dict() == t1km[f"{column}_ZENSUS1km"].to_dict()
    # Float targets: one P1 1+3 = 4, P2 2+1.5 = 3.5; multi P1 3+9 = 12, P2 4+3 = 7.
    assert t1km.loc[PARENT_1, f"{mct.ONE_ADULT_TARGET}_ZENSUS1km"] == 4
    assert t1km.loc[PARENT_1, f"{mct.MULTI_ADULT_TARGET}_ZENSUS1km"] == 12
    assert t1km.loc[PARENT_2, f"{mct.MULTI_ADULT_TARGET}_ZENSUS1km"] == 7

    seed = pd.read_csv(out / "data" / "seed_households.csv", keep_default_na=False)
    assert seed["hh_minor_class"].tolist() == ["one_adult", "multi_adult", "none", ""]
    step = log["steps_done"][0]
    assert step["seed_class"]["households_without_class"] == 1
    assert step["zgb_cell_filter"]["cells_kept"] == 4
    assert step["targets_report"]["squares_on_kreis_fallback"] == 0
    changed = sorted(n for n, e in log["files"].items() if e["changed"])
    assert changed == ["configs/controls.csv", "data/control_totals_ZENSUS100m.csv",
                       "data/control_totals_ZENSUS1km.csv", "data/seed_households.csv"]


def test_an_unexpected_changed_file_exits_1(tmp_path, monkeypatch):
    prepare = load_script("prepare_household_arm")
    original = prepare.step_family

    def step_and_touch(state, **kwargs):
        original(state, **kwargs)
        (state["out"] / "data" / "geo_cross_walk.csv").write_text("ZENSUS100m,ZENSUS1km\n")

    monkeypatch.setattr(prepare, "step_family", step_and_touch)
    base, out = tmp_path / "base" / "batch_000", tmp_path / "new" / "batch_000"
    write_batch_folder(base)
    rc = prepare.main(["--base", str(base), "--out", str(out), "--steps", "family_none", "--commit", "x"])
    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    assert rc == 1
    assert any("geo_cross_walk.csv" in p for p in log["problems"])


def test_contradicting_family_steps_are_refused(tmp_path):
    prepare = load_script("prepare_household_arm")
    base = tmp_path / "base" / "batch_000"
    write_batch_folder(base)
    rc = prepare.main(["--base", str(base), "--out", str(tmp_path / "o"), "--steps",
                       "family_none,family_minor_child", "--commit", "x"])
    assert rc == 2 and not (tmp_path / "o").exists()


def _move_fixture():
    """P1: a1 10 households (5+5 persons), a2 none (3+2 persons: a donor). P2 has no household:
    b1 (4+4) and b2 (1+0) are donors that cannot move."""
    ages, employed = ["M_AGE_0_9_agg", "F_AGE_0_9_agg"], ["EMPLOYED_M_16_29_agg"]
    cells = pd.DataFrame({"ZENSUS100m": [A1, A2, B1, B2], HH_TOTAL: [10.0, 0.0, 0.0, 0.0],
                          ages[0]: [5.0, 3.0, 4.0, 1.0], ages[1]: [5.0, 2.0, 4.0, 0.0],
                          employed[0]: [2.0, 1.0, 1.0, 0.0]})
    xwalk = pd.DataFrame({"ZENSUS100m": [A1, A2, B1, B2], "ZENSUS1km": [PARENT_1, PARENT_1, PARENT_2, PARENT_2]})
    return cells, xwalk, ages, employed


def test_self_check_core_reproduces_a_base_built_by_the_same_functions():
    prepare = load_script("prepare_household_arm")
    cells, xwalk, ages, employed = _move_fixture()
    base = mid.build_control_totals(cells, xwalk, [*ages, *employed])
    tables, report = prepare.rebuild_person_tables(cells, xwalk, age_cols=ages, employed_cols=employed,
                                                   move_persons_max=None)
    assert report is None
    for key in ("ZENSUS100m", "ZENSUS1km"):
        pd.testing.assert_frame_equal(tables[key], base[key])


def test_move_core_empties_donors_and_keeps_parent_sums():
    prepare = load_script("prepare_household_arm")
    cells, xwalk, ages, employed = _move_fixture()
    base = mid.build_control_totals(cells, xwalk, [*ages, *employed])
    tables, report = prepare.rebuild_person_tables(cells, xwalk, age_cols=ages, employed_cols=employed,
                                                   move_persons_max=20.0)
    assert report["donor_cells"] == 1 and report["persons_moved"] == 5.0
    t100 = tables["ZENSUS100m"].set_index("ZENSUS100m")
    assert t100.loc[A2, "M_AGE_0_9_agg_ZENSUS100m"] == 0 and t100.loc[A1, "M_AGE_0_9_agg_ZENSUS100m"] == 8
    check = prepare.household_cells_check(cells, xwalk, tables, base["ZENSUS1km"].astype(str),
                                          age_cols=ages, employed_cols=employed, move_persons_max=20.0)
    assert check["cells_left"] == 0
    assert check["cells_left_in_squares_without_households"] == 2
    assert check["max_abs_parent_sum_change_all"] == 0.0


def _write_diag(folder: Path, fits, composition=None):
    folder.mkdir(parents=True)
    pd.DataFrame(fits, columns=["scope", "batch", "control", "geography", "target_sum", "synthetic_sum",
                                "abs_error"]).to_csv(folder / "control_fit.csv", index=False)
    if composition is not None:
        pd.DataFrame(composition).to_csv(folder / "household_composition.csv", index=False)


@pytest.mark.parametrize("new_share,met", [(0.21, True), (0.25, False)])
def test_composition_cells_and_wape_check_kinds(tmp_path, new_share, met):
    compare = load_script("compare_household_arms")
    fits_base = [("batch", "batch_000", "M_AGE_0_9_agg_ZENSUS100m_target", "ZENSUS100m", 100, 100, 10)]
    fits_new = [("batch", "batch_000", "M_AGE_0_9_agg_ZENSUS100m_target", "ZENSUS100m", 100, 100, 8)]
    # Census share 0.20 (bound 2 pp); one-adult ratio 0.18 / 0.20 = 0.9 within [0.8, 1.25].
    composition = {"batch": ["batch_000"], "share_households_with_minor": [new_share],
                   "census_share_families_with_minor": [0.20],
                   "one_adult_share_among_minor_households": [0.18],
                   "census_lone_parent_share_among_families_with_minor": [0.20]}
    _write_diag(tmp_path / "base", fits_base)
    _write_diag(tmp_path / "new", fits_new, composition)
    prep = tmp_path / "prep" / "batch_000"
    prep.mkdir(parents=True)
    (prep / "household_cells_check.json").write_text(json.dumps(
        {"cells_left": 0 if met else 3, "cells_left_in_squares_without_households": 2,
         "max_abs_parent_sum_change_all": 0.0}))
    checks = {"checks": [
        {"id": "comp", "kind": "composition", "bound_pp": 2.0, "ratio_lo": 0.8, "ratio_hi": 1.25},
        {"id": "cells", "kind": "cells", "prepare_dir": str(tmp_path / "prep")},
        {"id": "wape", "kind": "wape_controls_vs_base", "include_regex": "_AGE_", "delta": 0.0}]}
    (tmp_path / "checks.json").write_text(json.dumps(checks))
    rc = compare.main(["--base-diag", str(tmp_path / "base"), "--new-diag", str(tmp_path / "new"),
                       "--checks", str(tmp_path / "checks.json"), "--out", str(tmp_path / "cmp")])
    verdict = json.loads((tmp_path / "cmp" / "verdict.json").read_text(encoding="utf-8"))
    assert rc == 0
    assert verdict["checks"]["comp"]["met"] is met
    assert verdict["checks"]["cells"]["met"] is met
    assert verdict["checks"]["wape"]["met"] is True  # 0.08 <= 0.10
    assert (tmp_path / "cmp" / "comparison.csv").is_file()
