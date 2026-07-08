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
