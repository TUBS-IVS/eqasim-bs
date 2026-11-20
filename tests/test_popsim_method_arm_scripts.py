"""The control-level and rounding arm driver of issue #660 (``docs/runs/artifacts/popsim-ladder-inputs-c-2026-11-19/scripts``).

Tiny synthetic batch folders: 4 cells in 2 squares. The base folder's integer tables are built from a
hand-made float frame with the library's integerisation (``mid.build_control_totals``, with the
household-less-cell move for the age bands), so the float step's self-check must accept that frame
and refuse a changed one. The float step's parquet load (``build_float_cells``) is replaced by the
hand-made frame; the pure household rescale is tested on its own.
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.popsim import control_spec, mid

SCRIPTS = (Path(__file__).resolve().parents[1] / "docs" / "runs" / "artifacts"
           / "popsim-ladder-inputs-c-2026-11-19" / "scripts")
HH = control_spec.HH_TOTAL_CENSUS_COLUMN
PARENT_1 = "CRS3035RES1000mN2689000E4337000"
PARENT_2 = "CRS3035RES1000mN2690000E4338000"
A1, A2 = "CRS3035RES100mN2689100E4337000", "CRS3035RES100mN2689200E4337000"
B1, B2 = "CRS3035RES100mN2690100E4338000", "CRS3035RES100mN2690200E4338000"
CELLS = [A1, A2, B1, B2]
AGE_CONTROLS = [c for c in control_spec.tier0_backbone_catalog(fine_teen_age_bands=True)
                if c.geography == "ZENSUS100m" and c.seed_table == "persons"]
AGE = [c.name for c in AGE_CONTROLS]
SIZE_CONTROLS = [c for c in control_spec.tier1_controls() if c.name.endswith("Groesse_des_privaten_Haushalts_100m_Gitter")]
SIZE = [c.name for c in SIZE_CONTROLS]
TENURE_CONTROLS = [c for c in control_spec.tier2_controls()
                   if c.name in {n for n, _ in control_spec._TIER2_TENURE_ENTRIES}]
TENURE = [c.name for c in TENURE_CONTROLS]
EMPLOYED, OWN = "EMPLOYED_M_16_29_agg", "OWN_example"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def float_frame() -> pd.DataFrame:
    """Unrounded cell values. A2 rounds to 0 households and is a small household-less age donor."""
    households = [9.6, 0.3, 5.4, 4.7]  # integers within the squares: 10, 0, 5, 5
    frame = {"ZENSUS100m": CELLS, HH: households}
    for k, name in enumerate(SIZE):
        frame[name] = [h * (k + 1) / 21.0 for h in households]
    frame[TENURE[0]] = [h * 0.45 for h in households]
    frame[TENURE[1]] = [h * 0.55 for h in households]
    for k, name in enumerate(AGE):
        frame[name] = [0.6 + 0.01 * k, 0.1, 0.4 + 0.02 * k, 0.5]
    return pd.DataFrame(frame)


def write_batch_folder(base: Path, frame: pd.DataFrame) -> None:
    for sub in ("configs", "data", "output"):
        (base / sub).mkdir(parents=True)
    rows = [{"target": f"{HH}_ZENSUS100m_target", "geography": "ZENSUS100m", "seed_table": "households",
             "importance": 1000000000, "control_field": f"{HH}_ZENSUS100m", "expression": "(households.H_ID > 0)"}]
    controls = pd.concat([pd.DataFrame(rows), control_spec.render_catalog_csv(
        [*SIZE_CONTROLS, *AGE_CONTROLS, *TENURE_CONTROLS], "mid")], ignore_index=True)
    controls.to_csv(base / "configs" / "controls.csv", index=False)
    (base / "configs" / "settings.yaml").write_text("geographies: [ZENSUS100m]\n")
    xwalk = pd.DataFrame({"ZENSUS100m": CELLS, "ZENSUS1km": [PARENT_1, PARENT_1, PARENT_2, PARENT_2]})
    plain = mid.build_control_totals(frame, xwalk, [HH, *SIZE, *TENURE])
    moved = mid.build_control_totals(frame, xwalk, [HH, *AGE], household_total_col=HH, person_cols=AGE,
                                     threshold_cols=AGE, move_persons_max=20.0)
    for key, suffix in (("ZENSUS100m", "_ZENSUS100m"), ("ZENSUS1km", "_ZENSUS1km")):
        table = plain[key].merge(moved[key][[key, *[f"{a}{suffix}" for a in AGE]]], on=key)
        table[f"{EMPLOYED}{suffix}"] = 3
        table[f"{OWN}{suffix}"] = 2
        table.iloc[::-1].to_csv(base / "data" / f"control_totals_{key}.csv", index=False)
    (base / "data" / "control_totals_KREIS.csv").write_text("KREIS\n1\n")
    xwalk.assign(KREIS="03101").to_csv(base / "data" / "geo_cross_walk.csv", index=False)
    pd.DataFrame({"H_ID": [1, 2]}).to_csv(base / "data" / "seed_households.csv", index=False)


def run_step(tmp_path, step, *, frame=None, monkeypatch=None, prepare=None, mutate_base=None):
    prepare = prepare or load_script("prepare_method_arm")
    base, out = tmp_path / "base" / "batch_000", tmp_path / "new" / "batch_000"
    write_batch_folder(base, float_frame())
    if mutate_base is not None:
        mutate_base(base)
    extra = []
    if frame is not None:
        monkeypatch.setattr(prepare, "build_float_cells", lambda *args, **kwargs: (frame.copy(), {"stub": True}))
        extra = ["--kreise", "03101", "--cells-100m", str(tmp_path / "unused.parquet")]
    rc = prepare.main(["--base", str(base), "--out", str(out), "--step", step, "--commit", "abc123", *extra])
    log = json.loads((out / "prepare_log.json").read_text(encoding="utf-8"))
    return base, out, rc, log


def test_age_sex_step_moves_22_controls_and_nothing_else(tmp_path):
    base, out, rc, log = run_step(tmp_path, "age_sex_at_1km")
    assert rc == 0, log["problems"]
    before = pd.read_csv(base / "configs" / "controls.csv", dtype=str)
    after = pd.read_csv(out / "configs" / "controls.csv", dtype=str)
    assert len(AGE) == 22 and len(after) == len(before)
    is_age = before["control_field"].isin({f"{a}_ZENSUS100m" for a in AGE})
    assert is_age.sum() == 22
    pd.testing.assert_frame_equal(after[~is_age], before[~is_age])  # same rows, same positions
    moved = after[is_age]
    assert (moved["geography"] == "ZENSUS1km").all()
    assert moved["control_field"].tolist() == [f"{a}_ZENSUS1km" for a in before.loc[is_age, "control_field"].str[:-len("_ZENSUS100m")]]
    assert (moved["target"] == moved["control_field"] + "_target").all()
    for column in ("importance", "seed_table", "expression"):
        assert moved[column].tolist() == before.loc[is_age, column].tolist()
    changed = [name for name, entry in log["files"].items() if entry["changed"]]
    assert changed == ["configs/controls.csv"]
    assert (out / "output").is_dir() and log["ok"] and log["step"] == "age_sex_at_1km"


def test_age_sex_step_refuses_a_missing_1km_column(tmp_path):
    missing = f"{AGE[3]}_ZENSUS1km"

    def drop_column(base):
        path = base / "data" / "control_totals_ZENSUS1km.csv"
        pd.read_csv(path).drop(columns=[missing]).to_csv(path, index=False)

    _, _, rc, log = run_step(tmp_path, "age_sex_at_1km", mutate_base=drop_column)
    assert rc == 1
    assert any(missing in p for p in log["problems"]) and not log["ok"]


def test_float_step_keeps_group_totals():
    prepare = load_script("prepare_method_arm")
    values = pd.DataFrame({"s1": [1.2], "s2": [0.9], "s3": [0.6]})
    base_integers = pd.DataFrame({"s1": [1], "s2": [1], "s3": [1]})
    result, report = prepare.rescale_to_households(values, pd.Series([3]), base_integers, group="size")
    np.testing.assert_allclose(result.iloc[0].to_numpy(), [4 / 3, 1.0, 2 / 3], atol=1e-3)
    assert abs(result.iloc[0].sum() - 3.0) < 1e-12
    assert report["fallback_cells"] == 0 and report["cells_with_households"] == 1


def test_rescale_fallback_is_counted():
    prepare = load_script("prepare_method_arm")
    values = pd.DataFrame({"s1": [0.0, 0.5, 1.0], "s2": [np.nan, 0.2, 1.0]})
    base_integers = pd.DataFrame({"s1": [1, 0, 1], "s2": [1, 0, 0]})
    result, report = prepare.rescale_to_households(values, pd.Series([2, 0, 1]), base_integers, group="size")
    assert result.iloc[0].tolist() == [1.0, 1.0]  # households 2, float sum 0: the base integers
    assert result.iloc[1].tolist() == [0.0, 0.0]  # no households
    assert result.iloc[2].tolist() == [0.5, 0.5]
    assert report["fallback_cells"] == 1 and report["fallback_households"] == 2
    assert report["cells_with_households"] == 2 and report["cells_without_households_zeroed"] == 1


def test_float_step_leaves_household_total_and_grids(tmp_path, monkeypatch):
    base, out, rc, log = run_step(tmp_path, "float_census_controls", frame=float_frame(), monkeypatch=monkeypatch)
    assert rc == 0, log["problems"]
    changed = sorted(n for n, e in log["files"].items() if e["changed"])
    assert changed == ["data/control_totals_ZENSUS100m.csv", "data/control_totals_ZENSUS1km.csv"]
    record = log["step_report"]
    assert record["self_check"]["ok"] and "building_type" in record["groups_skipped"]
    groups = [*SIZE, *TENURE, *AGE]
    xwalk = pd.read_csv(base / "data" / "geo_cross_walk.csv").set_index("ZENSUS100m")["ZENSUS1km"]
    for key, suffix in (("ZENSUS100m", "_ZENSUS100m"), ("ZENSUS1km", "_ZENSUS1km")):
        name = f"data/control_totals_{key}.csv"
        old = pd.read_csv(base / name, dtype=str)
        new = pd.read_csv(out / name, dtype=str)
        assert set(log["files"][name]["columns_changed"]) <= {f"{c}{suffix}" for c in groups}
        for column in (key, f"{HH}{suffix}", f"{EMPLOYED}{suffix}", f"{OWN}{suffix}"):
            assert new[column].tolist() == old[column].tolist()
    t100 = pd.read_csv(out / "data" / "control_totals_ZENSUS100m.csv").set_index("ZENSUS100m")
    t1km = pd.read_csv(out / "data" / "control_totals_ZENSUS1km.csv").set_index("ZENSUS1km")
    for column in groups:
        sums = t100[f"{column}_ZENSUS100m"].groupby(t100.index.map(xwalk)).sum()
        np.testing.assert_allclose(t1km[f"{column}_ZENSUS1km"].reindex(sums.index), sums, rtol=0, atol=1e-9)
    # Household groups sum to the integer household total of the cell; A2 (0 households) is empty.
    households = t100[f"{HH}_ZENSUS100m"]
    for group in (SIZE, TENURE):
        np.testing.assert_allclose(t100[[f"{c}_ZENSUS100m" for c in group]].sum(axis=1), households, atol=1e-9)
    assert households[A2] == 0 and t100.loc[A2, [f"{c}_ZENSUS100m" for c in AGE]].sum() == 0  # moved
    assert t100.loc[A1, f"{SIZE[0]}_ZENSUS100m"] != round(t100.loc[A1, f"{SIZE[0]}_ZENSUS100m"])  # unrounded
    assert (out / "configs" / "controls.csv").read_bytes() == (base / "configs" / "controls.csv").read_bytes()


def test_float_step_self_check_fails_on_a_mismatch(tmp_path, monkeypatch):
    frame = float_frame()
    frame.loc[0, SIZE[2]] -= 1.0  # same square sum (1.41), the unit moves from A1 to A2
    frame.loc[1, SIZE[2]] += 1.0
    _, _, rc, log = run_step(tmp_path, "float_census_controls", frame=frame, monkeypatch=monkeypatch)
    assert rc == 1
    assert any("household_size" in p and f"{SIZE[2]}_ZENSUS100m" in p for p in log["problems"])
    assert not [n for n, e in log["files"].items() if e["changed"]]


def test_arm2_building_type_floats_round_to_the_arm2_integers():
    """The basis ``arm2_integer_dwellings`` must reproduce what prepare_arm2.rescale_targets wrote.

    Defect caught: other parents, order or dwelling basis in the rebuild, which would make arm 10 differ
    from arm 8 by more than the rounding.
    """
    import pytest
    from braunschweig.popsim import controls as ctrl

    prepare = load_script("prepare_method_arm")
    arm2_path = SCRIPTS.parents[1] / "popsim-ladder-arm2-2026-11-17" / "scripts" / "prepare_arm2.py"
    arm2 = importlib.util.module_from_spec(importlib.util.spec_from_file_location("prepare_arm2", arm2_path))
    arm2.__spec__.loader.exec_module(arm2)
    columns = [name for name, _, _ in control_spec._TIER2_BUILDING_TYPE_ENTRIES]
    dwellings = {columns[0]: [6.4, 0.2, 2.5, 1.0], columns[1]: [4.9, 0.0, 3.4, 4.6], columns[2]: [0.0, 0.3, 0.0, 0.0]}
    batch = pd.DataFrame({"ZENSUS100m": CELLS, **dwellings})
    crosswalk = pd.DataFrame({"ZENSUS100m": CELLS, "ZENSUS1km": [PARENT_1, PARENT_1, PARENT_2, PARENT_2],
                              "KREIS": "03101", "STAAT": "1", "WELT": "1"})
    hh_field = f"{HH}_ZENSUS100m"
    base = pd.DataFrame({"ZENSUS100m": [A2, B1, A1, B2], hh_field: [0, 5, 10, 5]})
    frame, report = prepare.building_type_arm2_floats(batch, crosswalk, base, columns)
    fields = [f"{c}_ZENSUS100m" for c in columns]
    # The integer dwelling targets the production run wrote, then arm 2's rescale and rounding.
    integer_dwellings = mid.build_control_totals(batch, crosswalk, columns)["ZENSUS100m"].set_index("ZENSUS100m")
    arm1_totals = base.copy()
    for field in fields:
        arm1_totals[field] = integer_dwellings[field].reindex(base["ZENSUS100m"]).to_numpy()
    expected = arm2.rescale_targets(arm1_totals, crosswalk)
    for field in fields:
        rounded = ctrl.integerize_within_parents(frame, value_col=field, parent_col="ZENSUS1km").to_numpy()
        assert list(rounded) == list(expected[field].astype(int)), field
    sums = frame.set_index("ZENSUS100m")[fields].sum(axis=1)
    assert sums.reindex(base["ZENSUS100m"]).tolist() == pytest.approx([0, 5, 10, 5], abs=1e-9)

