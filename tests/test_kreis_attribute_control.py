import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    KreisAttributeControl, REGISTRY, control_columns, attribute_kreis_count_table,
)


def _econ_entry():
    return next(c for c in REGISTRY if c.name == "economic_status")


def test_registry_has_economic_status_only():
    assert [c.name for c in REGISTRY] == ["economic_status"]


def test_control_columns_follow_name_category():
    c = _econ_entry()
    assert control_columns(c) == tuple(
        f"economic_status_{k}" for k in ("very_low", "low", "medium", "high", "very_high"))


def test_count_table_rows_sum_to_hh_total_integer():
    c = _econ_entry()
    tgt = pd.DataFrame([
        {"ars5": "03ZGB", "very_low": 9, "low": 12, "medium": 31, "high": 36, "very_high": 12},
        {"ars5": "03102", "very_low": 5, "low": 10, "medium": 29, "high": 42, "very_high": 13},
    ])
    out = attribute_kreis_count_table(c, tgt, {"03102": 50000.4}, prior_n=0.0)
    cols = list(control_columns(c))
    assert list(out.columns) == ["ARS_kreis", *cols]
    row = out[out.ARS_kreis == "03102"][cols].to_numpy().ravel()
    assert row.sum() == 50000 and (row == np.floor(row)).all()


def test_count_table_missing_kreis_raises():
    c = _econ_entry()
    tgt = pd.DataFrame([{"ars5": "03ZGB", "very_low": 9, "low": 12, "medium": 31, "high": 36, "very_high": 12}])
    with pytest.raises(ValueError):
        attribute_kreis_count_table(c, tgt, {"09999": 100.0}, prior_n=0.0)


# --- Task 2: generic catalog factory (economic_status via generic == the L1 controls) ---
from braunschweig.popsim import control_spec as cs  # noqa: E402


def test_generic_factory_reproduces_L1_economic_status_controls():
    econ = [c for c in REGISTRY if c.name == "economic_status"]
    generic = cs.attribute_kreis_controls(econ)
    assert [c.name for c in generic] == list(control_columns(econ[0]))
    for c in generic:
        assert c.geography == cs.GEO_KREIS and c.seed_table == cs.SEED_TABLE_HOUSEHOLDS
        assert c.census_source == (c.name,) and c.seed_expressions["entd"] is None
    exprs = {c.name: c.seed_expressions["mid"] for c in generic}
    assert exprs["economic_status_very_low"] == "(households.oek_status == 1)"
    assert exprs["economic_status_very_high"] == "(households.oek_status == 5)"


def test_status_kreis_controls_still_returns_five_identical():
    # The L1 public factory now delegates to the generic one; output must be unchanged.
    s = cs.status_kreis_controls()
    assert [c.name for c in s] == list(control_columns(_econ_entry()))
    assert all(c.geography == cs.GEO_KREIS and c.seed_table == cs.SEED_TABLE_HOUSEHOLDS for c in s)


# --- Task 1: generic per-Kreis target loader ---
from braunschweig.popsim.kreis_attribute_control import load_kreis_target  # noqa: E402


def _write_target_csv(tmp_path: Path) -> Path:
    root = tmp_path / "braunschweig" / "targets"
    root.mkdir(parents=True)
    p = root / "target2026_toy_by_kreis.csv"
    p.write_text(
        "# comment line one\n"
        "# CONSUMER NOTE: FINAL target - use with prior_n = 0.\n"
        "ars5,source,n_effective,a,b\n"
        "Gesamt,mid,0,0.6,0.4\n"
        "03101,blend,1000,0.7,0.3\n",
        encoding="utf-8",
    )
    return p


_TOY = KreisAttributeControl(
    name="toy", seed_column="toy_col", level="household",
    categories=(("a", "== 0"), ("b", ">= 1")),
    target_csv_relpath="braunschweig/targets/target2026_toy_by_kreis.csv",
    target_columns=("a", "b"), tier="soft",
)


def test_load_kreis_target_drops_comments_and_meta_columns(tmp_path):
    _write_target_csv(tmp_path)
    df = load_kreis_target(tmp_path, _TOY)
    assert list(df.columns) == ["ars5", "a", "b"]
    assert set(df["ars5"]) == {"Gesamt", "03101"}
    row = df[df["ars5"] == "03101"].iloc[0]
    assert row["a"] == pytest.approx(0.7)


def test_load_kreis_target_requires_aggregate_row(tmp_path):
    p = _write_target_csv(tmp_path)
    # strip the Gesamt row
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith("Gesamt")]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aggregate row"):
        load_kreis_target(tmp_path, _TOY)


def test_load_kreis_target_fails_on_missing_expected_kreis(tmp_path):
    _write_target_csv(tmp_path)
    with pytest.raises(ValueError, match="missing Kreis"):
        load_kreis_target(tmp_path, _TOY, expected_ars5=("03101", "03102"))


def test_load_kreis_target_fails_when_shares_do_not_sum_to_one(tmp_path):
    p = _write_target_csv(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("0.7,0.3", "0.7,0.9"), encoding="utf-8")
    with pytest.raises(ValueError, match="sum to 1"):
        load_kreis_target(tmp_path, _TOY)


def test_load_kreis_target_fails_on_missing_file(tmp_path):
    # No CSV written under tmp_path: the relpath resolves to a nonexistent file.
    with pytest.raises(FileNotFoundError):
        load_kreis_target(tmp_path, _TOY)


def test_load_kreis_target_fails_on_missing_target_column(tmp_path):
    p = _write_target_csv(tmp_path)
    # drop the "b" target column from header and rows
    p.write_text(
        "# comment line one\n"
        "# CONSUMER NOTE: FINAL target - use with prior_n = 0.\n"
        "ars5,source,n_effective,a\n"
        "Gesamt,mid,0,0.6\n"
        "03101,blend,1000,0.7\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing columns"):
        load_kreis_target(tmp_path, _TOY)
