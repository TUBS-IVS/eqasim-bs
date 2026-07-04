import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from braunschweig.popsim.status_kreis_control import (  # noqa: E402
    STATUS_CONTROL_COLUMNS, shrunk_status_shares, status_kreis_count_table,
)
from braunschweig.data.mid.status_by_kreis import STATUS_KEYS  # noqa: E402


def _h4():
    # ZGB aggregate + two Kreise (integer row-% like the committed CSV).
    return pd.DataFrame([
        {"ars5": "03ZGB", "n_unweighted": 5924, "very_low": 9, "low": 12, "medium": 31, "high": 36, "very_high": 12},
        {"ars5": "03102", "n_unweighted": 792,  "very_low": 5, "low": 10, "medium": 29, "high": 42, "very_high": 13},
        {"ars5": "03101", "n_unweighted": 1105, "very_low": 10, "low": 8, "medium": 30, "high": 36, "very_high": 16},
    ])


def test_control_column_names_match_status_keys_order():
    assert STATUS_CONTROL_COLUMNS == tuple(f"economic_status_{k}" for k in STATUS_KEYS)


def test_shares_sum_to_one_and_prior0_is_raw_h4():
    s = shrunk_status_shares(_h4(), prior_n=0.0)
    row = s[s.ars5 == "03102"].iloc[0]
    assert row[list(STATUS_KEYS)].sum() == pytest.approx(1.0)
    # prior_n=0 -> exactly the raw H4 row renormalised (5/99..13/99).
    assert row["high"] == pytest.approx(42 / 99)


def test_shrinkage_pulls_toward_zgb():
    raw = shrunk_status_shares(_h4(), prior_n=0.0)
    shr = shrunk_status_shares(_h4(), prior_n=1000.0)  # heavy prior
    zgb = raw[raw.ars5 == "03ZGB"][list(STATUS_KEYS)].to_numpy().ravel()
    r = raw[raw.ars5 == "03102"][list(STATUS_KEYS)].to_numpy().ravel()
    s = shr[shr.ars5 == "03102"][list(STATUS_KEYS)].to_numpy().ravel()
    # shrunk row is strictly closer to ZGB than the raw row on the "high" class.
    hi = list(STATUS_KEYS).index("high")
    assert abs(s[hi] - zgb[hi]) < abs(r[hi] - zgb[hi])


def test_count_table_rows_sum_to_hh_total_integer():
    tbl = status_kreis_count_table(_h4(), {"03102": 50000.4, "03101": 136611.0}, prior_n=0.0)
    assert list(tbl.columns) == ["ARS_kreis", *STATUS_CONTROL_COLUMNS]
    sz = tbl[tbl.ARS_kreis == "03102"][list(STATUS_CONTROL_COLUMNS)].to_numpy().ravel()
    assert sz.sum() == 50000  # round(50000.4), integer partition
    assert (sz == np.floor(sz)).all()


def test_count_table_missing_kreis_raises():
    with pytest.raises(ValueError):
        status_kreis_count_table(_h4(), {"09999": 100.0}, prior_n=0.0)


# --- Task 2: catalog controls ---
from braunschweig.popsim import control_spec as cs  # noqa: E402


def test_status_kreis_controls_are_five_kreis_household_controls():
    controls = cs.status_kreis_controls()
    assert len(controls) == 5
    assert [c.name for c in controls] == list(STATUS_CONTROL_COLUMNS)
    for c in controls:
        assert c.geography == cs.GEO_KREIS
        assert c.seed_table == cs.SEED_TABLE_HOUSEHOLDS
        assert c.census_source == (c.name,)
        assert c.seed_expressions["entd"] is None


def test_status_kreis_control_expressions_map_oek_status_1_to_5():
    controls = {c.name: c for c in cs.status_kreis_controls()}
    assert controls["economic_status_very_low"].seed_expressions["mid"] == "(households.oek_status == 1)"
    assert controls["economic_status_very_high"].seed_expressions["mid"] == "(households.oek_status == 5)"


def test_full_catalog_includes_status_only_when_requested():
    base = cs.full_catalog(include_tiers=("tier0",))
    with_status = cs.full_catalog(include_tiers=("tier0",), include_status_kreis=True)
    assert len(with_status) == len(base) + 5
    # ENTD drops all five (oek_status is a MiD-only seed column).
    assert not any(c.name in STATUS_CONTROL_COLUMNS for c in cs.controls_for_seed(with_status, "entd"))
    assert sum(c.name in STATUS_CONTROL_COLUMNS for c in cs.controls_for_seed(with_status, "mid")) == 5
