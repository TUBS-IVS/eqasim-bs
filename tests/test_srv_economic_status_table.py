"""Pin tests for the committed SrV 2023 economic-status-by-Kreis table.
Reads ONLY the committed CSV (raw microdata not needed)."""
from pathlib import Path

import pandas as pd
import pytest

CSV = (Path(__file__).resolve().parents[1] / "eqasim-data" / "data"
       / "braunschweig" / "srv" / "srv2023_economic_status_by_kreis.csv")

STATUS_COLS = ["very_low", "low", "medium", "high", "very_high"]
SRV_KREISE = {"03101", "03102", "03151", "03153", "03154", "03157", "03158"}


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    return pd.read_csv(CSV, comment="#", dtype={"code": str})


def test_levels_and_kreis_set(table):
    kreis = table[table["level"] == "kreis"]
    assert set(kreis["code"]) == SRV_KREISE
    assert "03103" not in set(table["code"])  # Wolfsburg not covered
    assert (table[table["level"] == "total"]["code"] == "total").all()


def test_shares_sum_to_one(table):
    sums = table[STATUS_COLS].sum(axis=1)
    assert ((sums - 1.0).abs() < 0.005).all()


def test_pinned_values(table):
    k = table[table["level"] == "kreis"].set_index("code")
    # Salzgitter: the cell that contradicts MiD H4 (42% high) -- SrV rebuild,
    # first-pass verified 2026-07-08 (srv_status_vs_mid_h4.py).
    assert k.loc["03102", "high"] == pytest.approx(0.243, abs=0.005)
    assert k.loc["03102", "very_high"] == pytest.approx(0.075, abs=0.005)
    assert k.loc["03101", "very_high"] == pytest.approx(0.147, abs=0.005)
    assert k.loc["03153", "very_low"] == pytest.approx(0.184, abs=0.005)


def test_income_missing_share_reported(table):
    total = table[table["level"] == "total"].iloc[0]
    # ~13.1% of households have no income answer (V_EINK -9/-5); they are
    # EXCLUDED from the status distribution but reported here.
    assert total["share_income_missing"] == pytest.approx(0.131, abs=0.01)
