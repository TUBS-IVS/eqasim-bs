"""The arm-4 scripts of issue #657 (``docs/runs/artifacts/popsim-ladder-tenure-2026-11-18/scripts``).

One behavioural test per script on a tiny synthetic batch folder (4 cells, 2 parents) and tiny hand-written
diagnostics. Expected values are computed by hand in the comments.
"""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim import control_spec
from braunschweig.popsim.stage import solver_path_gate

SCRIPTS = (Path(__file__).resolve().parents[1] / "docs" / "runs" / "artifacts"
           / "popsim-ladder-tenure-2026-11-18" / "scripts")
HH_TOTAL = control_spec.HH_TOTAL_CENSUS_COLUMN
DWELLINGS = control_spec.OWNER_QUOTE_DWELLING_COLUMN
OWNER = "EigentuemerHH_Tenure_100m_Gitter"
RENTER = "MieterHH_Tenure_100m_Gitter"
PARENT_1 = "CRS3035RES1000mN2689000E4337000"
PARENT_2 = "CRS3035RES1000mN2690000E4338000"
A1, A2 = "CRS3035RES100mN2689100E4337000", "CRS3035RES100mN2689200E4337000"
B1, B2 = "CRS3035RES100mN2690100E4338000", "CRS3035RES100mN2690200E4338000"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_batch_folder(base: Path) -> None:
    """An arm-3-like batch folder whose tenure columns are the rule-off integerisation of the grid below."""
    for sub in ("configs", "data", "output"):
        (base / sub).mkdir(parents=True)
    (base / "configs" / "controls.csv").write_text("target,geography\nx,ZENSUS100m\n")
    (base / "data" / "seed_households.csv").write_text("hh_id\n1\n2\n")
    # Rows in an order that differs from the grid's, to test the reindexing.
    order = [A2, B1, A1, B2]
    # Rule off: owners P1 [8, 24] (sum 32), P2 [10.4, 6] (16.4 -> 16, floors 10 + 6); renters P1 [2, 6],
    # P2 [9.6, 9] (18.6 -> 19, floors 9 + 9 = 18, the 0.6 remainder gets the unit -> 10, 9).
    owner = {A1: 8, A2: 24, B1: 10, B2: 6}
    renter = {A1: 2, A2: 6, B1: 10, B2: 9}
    pd.DataFrame({
        "ZENSUS100m": order,
        f"{HH_TOTAL}_ZENSUS100m": [30, 20, 10, 15],
        f"{OWNER}_ZENSUS100m": [owner[i] for i in order],
        f"{RENTER}_ZENSUS100m": [renter[i] for i in order],
    }).to_csv(base / "data" / "control_totals_ZENSUS100m.csv", index=False)
    pd.DataFrame({
        "ZENSUS1km": [PARENT_2, PARENT_1],
        f"{HH_TOTAL}_ZENSUS1km": [35, 40],
        f"{OWNER}_ZENSUS1km": [16, 32],
        f"{RENTER}_ZENSUS1km": [19, 8],
    }).to_csv(base / "data" / "control_totals_ZENSUS1km.csv", index=False)
    pd.DataFrame({"ZENSUS100m": [A1, A2, B1, B2], "ZENSUS1km": [PARENT_1, PARENT_1, PARENT_2, PARENT_2],
                  "STAAT": 1, "WELT": 1, "KREIS": 3101}).to_csv(base / "data" / "geo_cross_walk.csv",
                                                                index=False)


def write_grid(path: Path) -> None:
    """a1: 10 households, 4 dwellings (6 outside < 10: unchanged); a2: 30 households, owners 24, 10 dwellings
    (20 outside: owners 24/30 * 10 = 8, renters 22); b1: dwellings 20 (unchanged, fractional 10.4 / 9.6);
    b2: 15 households, owners 6, 5 dwellings (10 outside: owners 6/15 * 5 = 2, renters 13)."""
    pd.DataFrame({
        "GITTER_ID_100m": [A1, A2, B1, B2],
        HH_TOTAL: [10.0, 30.0, 20.0, 15.0],
        OWNER: [8.0, 24.0, 10.4, 6.0],
        RENTER: [2.0, 6.0, 9.6, 9.0],
        DWELLINGS: [4.0, 10.0, 20.0, 5.0],
    }).to_parquet(path)


def tenure_by_id(folder: Path, table: str, key: str, suffix: str) -> dict:
    frame = pd.read_csv(folder / "data" / table).set_index(key)
    return {"owner": frame[f"{OWNER}{suffix}"].to_dict(), "renter": frame[f"{RENTER}{suffix}"].to_dict()}


@pytest.mark.parametrize("rule, corrupt, exit_code, owner_100m, renter_100m, owner_1km, renter_1km", [
    # Rule on: owners P1 [8, 8] (a1 unchanged 8, a2 limited to 8), P2 [10.4, 2] (12.4 -> 12, floors
    # 10 + 2); renters P1 [2, 22], P2 [9.6, 13] (22.6 -> 23, floors 9 + 13 = 22, remainder .6 -> b1 10).
    ("on", False, 0, {A1: 8, A2: 8, B1: 10, B2: 2}, {A1: 2, A2: 22, B1: 10, B2: 13},
     {PARENT_1: 16, PARENT_2: 12}, {PARENT_1: 24, PARENT_2: 23}),
    # Rule off reproduces the base folder exactly.
    ("off", False, 0, {A1: 8, A2: 24, B1: 10, B2: 6}, {A1: 2, A2: 6, B1: 10, B2: 9},
     {PARENT_1: 32, PARENT_2: 16}, {PARENT_1: 8, PARENT_2: 19}),
    # A change outside the two tenure columns (here a config file) makes the driver exit 1 and say so.
    ("on", True, 1, None, None, None, None),
])
def test_prepare_tenure_arm_changes_only_the_tenure_columns(
        tmp_path, monkeypatch, rule, corrupt, exit_code, owner_100m, renter_100m, owner_1km, renter_1km):
    prepare = load_script("prepare_tenure_arm")
    base, out, grid = tmp_path / "arm3" / "batch_000", tmp_path / "arm4" / "batch_000", tmp_path / "grid.parquet"
    write_batch_folder(base)
    write_grid(grid)
    if corrupt:
        real = prepare.rewrite_control_tables

        def rewrite_and_tamper(folder, new_100m, new_1km):
            real(folder, new_100m, new_1km)
            (folder / "configs" / "controls.csv").write_text("tampered\n")

        monkeypatch.setattr(prepare, "rewrite_control_tables", rewrite_and_tamper)

    code = prepare.main(["--base", str(base), "--out", str(out), "--cells-100m", str(grid), "--rule", rule,
                         "--library-commit", "abc123"])

    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    assert code == exit_code
    assert (out / "output").is_dir() and not any((out / "output").iterdir())
    if corrupt:
        assert log["ok"] is False and any("configs/controls.csv" in p for p in log["problems"])
        return
    assert log["ok"] is True and log["library_commit"] == "abc123"
    changed = {name: entry["columns_changed"] for name, entry in log["files"].items() if entry["changed"]}
    if rule == "off":
        assert changed == {} and log["rule_report"] is None
    else:
        assert changed == {
            "data/control_totals_ZENSUS100m.csv": [f"{OWNER}_ZENSUS100m", f"{RENTER}_ZENSUS100m"],
            "data/control_totals_ZENSUS1km.csv": [f"{OWNER}_ZENSUS1km", f"{RENTER}_ZENSUS1km"]}
        assert log["rule_report"]["cells_changed"] == 2  # a2 and b2
    hundred = tenure_by_id(out, "control_totals_ZENSUS100m.csv", "ZENSUS100m", "_ZENSUS100m")
    one_km = tenure_by_id(out, "control_totals_ZENSUS1km.csv", "ZENSUS1km", "_ZENSUS1km")
    assert hundred == {"owner": owner_100m, "renter": renter_100m}
    assert one_km == {"owner": owner_1km, "renter": renter_1km}
    # The row order of the folder's table is kept.
    assert list(pd.read_csv(out / "data" / "control_totals_ZENSUS100m.csv")["ZENSUS100m"]) == [A2, B1, A1, B2]


# ---------------------------------------------------------------------------------------------------------
# compare_tenure_arm
# ---------------------------------------------------------------------------------------------------------
HH_CONTROL = f"{HH_TOTAL}_ZENSUS100m_target"
OWNER_CONTROL, RENTER_CONTROL = f"{OWNER}_ZENSUS100m_target", f"{RENTER}_ZENSUS100m_target"


def fit_rows(batch: str, arm: int) -> list:
    """Batch rows of control_fit.csv. Arm 3 -> arm 4 by hand:

    ZENSUS100m, other controls: (household total abs 10 / 5, control X abs 20 / 28) over targets 100 + 100:
    WAPE 0.15 -> 0.165 (the building-type control, abs 0 / 50, and the zero-target control are ignored).
    ZENSUS1km control Y: abs 10 / 13 over 100: 0.10 -> 0.13. KREIS control Z: abs 5 / 5 over 100.
    Tenure controls (pooled): (10 + 10) / (60 + 40) = 0.20 -> (5 + 9) / (60 + 40) = 0.14.
    """
    new = arm == 4

    def row(control, geography, target, synthetic, abs_error):
        return {"scope": "batch", "batch": batch, "control": control, "geography": geography,
                "target_sum": target, "synthetic_sum": synthetic, "abs_error": abs_error}

    return [
        row(HH_CONTROL, "ZENSUS100m", 100.0, 105.0 if new else 110.0, 5.0 if new else 10.0),
        row("X_ZENSUS100m_target", "ZENSUS100m", 100.0, 100.0, 28.0 if new else 20.0),
        row("building_type_x_ZENSUS100m_target", "ZENSUS100m", 50.0, 50.0, 50.0 if new else 0.0),
        row("W_ZENSUS100m_target", "ZENSUS100m", 0.0, 3.0, 3.0),
        row(OWNER_CONTROL, "ZENSUS100m", 60.0, 60.0, 5.0 if new else 10.0),
        row(RENTER_CONTROL, "ZENSUS100m", 40.0, 40.0, 9.0 if new else 10.0),
        row("Y_ZENSUS1km_target", "ZENSUS1km", 100.0, 100.0, 13.0 if new else 10.0),
        row("Z_KREIS_target", "KREIS", 100.0, 100.0, 5.0),
    ]


def write_diag(diag: Path, work: Path, arm: int) -> None:
    diag.mkdir(parents=True)
    batches = ("batch_000", "batch_032")
    pd.DataFrame([r for b in batches for r in fit_rows(b, arm)]).to_csv(diag / "control_fit.csv", index=False)
    # G4: batch_000 misses in both arms (control B); batch_032 meets it in arm 3 and misses it in arm 4.
    shift = [("batch_000", "A", True), ("batch_000", "B", False), ("batch_032", "A", True),
             ("batch_032", "B", arm == 3)]
    pd.DataFrame([{"batch": b, "control": c, "target_sum": 100.0, "shift": 0.0, "within_bound": ok}
                  for b, c, ok in shift]).to_csv(diag / "rounding_shift.csv", index=False)
    # Solver paths: batch_032 of arm 4 has one non-simultaneous parent at ZENSUS100m.
    pd.DataFrame([{"batch": b, "level": level, "n_parents": 1,
                   "non_simultaneous": 1 if (arm == 4 and b == "batch_032" and level == "ZENSUS100m") else 0}
                  for b in batches for level in ("KREIS", "ZENSUS1km", "ZENSUS100m")]).to_csv(
        diag / "solver_paths.csv", index=False)
    pd.DataFrame([{"batch": b, "level": level, "abs_deviation_sum": 300.0 if (arm == 4 and level == "ZENSUS100m")
                   else (331.0 if level == "ZENSUS100m" else 0.0)}
                  for b in batches for level in ("KREIS", "ZENSUS1km", "ZENSUS100m")]).to_csv(
        diag / "household_conservation.csv", index=False)
    columns = [*solver_path_gate._COLUMNS, *solver_path_gate.CONTROLLED_COLUMNS]
    base_row = {c: "" for c in columns}
    call = {**base_row, "row_type": "call", "kind": "simul", "geography": "ZENSUS100m", "trace_label": "t",
            "status": "OPTIMAL", "rounding": "controlled", "method": "search", "x_count": "5", "swaps": "1",
            "sweeps": "1", "converged": "1", "distance_start": "2", "distance_final": "1", "flow_wanted": "3",
            "flow_achieved": "3", "zone_shortfall": "0", "row_shortfall": "0", "hh_unreachable": "0",
            "hh_outside_lp": "0", "hh_excess": "0"}
    outcome = {**base_row, "row_type": "outcome", "geography": "ZENSUS100m", "outcome": "simultaneous"}
    for b in batches:
        (work / b / "output").mkdir(parents=True)
        pd.DataFrame([call, outcome], columns=columns).to_csv(work / b / "output" / "integerizer_paths.csv",
                                                              index=False)


def test_compare_tenure_arm_gives_the_hand_computed_verdict(tmp_path):
    compare = load_script("compare_tenure_arm")
    write_diag(tmp_path / "diag3", tmp_path / "work3", 3)
    write_diag(tmp_path / "diag4", tmp_path / "work4", 4)
    logs = tmp_path / "logs"
    logs.mkdir()
    for b in ("batch_000", "batch_032"):
        (logs / f"{b}.rule_on.json").write_text(json.dumps(
            {"files": {"data/control_totals_ZENSUS100m.csv": {"changed": True}}, "problems": [],
             "rule_report": {"cells_changed": 2}}))
        (logs / f"{b}.rule_off.json").write_text(json.dumps(
            {"files": {"data/control_totals_ZENSUS100m.csv": {"changed": False}}, "problems": [],
             "rule_report": None}))

    code = compare.main(["--base-diag", str(tmp_path / "diag3"), "--new-diag", str(tmp_path / "diag4"),
                         "--base-work", str(tmp_path / "work3"), "--new-work", str(tmp_path / "work4"),
                         "--prepare-logs", str(logs), "--batches", "batch_000", "batch_032",
                         "--out", str(tmp_path / "out")])

    table = pd.read_csv(tmp_path / "out" / "comparison.csv").set_index(["batch", "check"])
    verdict = json.loads((tmp_path / "out" / "verdict.json").read_text(encoding="utf-8"))
    assert code == 1 and verdict["overall_met"] is False
    # batch_000: only the 1 km WAPE rises beyond its bound (0.10 -> 0.13 > 0.12).
    met = table.loc["batch_000"]["met"].to_dict()
    assert {c for c, ok in met.items() if not ok} == {"N-a_ZENSUS1km"}
    assert table.loc[("batch_000", "O-ten-b"), ["arm3_value", "arm4_value"]].tolist() == [10.0, 5.0]
    assert table.loc[("batch_000", "O-ten-c"), "arm3_value"] == pytest.approx(0.20)
    assert table.loc[("batch_000", "O-ten-c"), "arm4_value"] == pytest.approx(0.14)
    assert table.loc[("batch_000", "N-a_ZENSUS100m"), "arm3_value"] == pytest.approx(0.15)
    assert table.loc[("batch_000", "N-a_ZENSUS100m"), "arm4_value"] == pytest.approx(0.165)
    assert table.loc[("batch_000", "N-a_ZENSUS100m"), "bound"] == pytest.approx(0.17)
    # G4: batch_000 missed in arm 3 (not gated); batch_032 met in arm 3 and misses in arm 4 (gated, fails).
    assert bool(table.loc[("batch_000", "N-b"), "gated"]) is False and bool(table.loc[("batch_000", "N-b"), "met"])
    assert bool(table.loc[("batch_032", "N-b"), "gated"]) and not bool(table.loc[("batch_032", "N-b"), "met"])
    # Solver path: one non-simultaneous parent in batch_032 of arm 4.
    assert table.loc[("batch_032", "N-c"), "arm4_value"] == 1 and not bool(table.loc[("batch_032", "N-c"), "met"])
    assert ("batch_032", "O-ten-b") not in table.index
    assert verdict["checks"]["batch_032/N-b"] == {"gated": True, "met": False}
    assert verdict["reported"]["batch_000"]["g4_per_control_arm4"] == {"A": True, "B": False}
