"""Pin tests for the committed LSN A9170102 per-Kreis income-tax aggregate.

Reads ONLY the committed CSV (no raw LSN XML needed), mirroring the SrV/MiD
reference-table test pattern. The table is the register-grade ordering arbiter
for MiD-vs-SrV economic-status disagreements (2026-07-08 control-sourcing spec).
"""

from pathlib import Path

import pandas as pd
import pytest

CSV = (
    Path(__file__).resolve().parents[1]
    / "eqasim-data" / "data" / "braunschweig" / "lsn"
    / "lsn2022_income_tax_by_kreis.csv"
)

EXPECTED_ARS5 = {"03NDS", "03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"}


@pytest.fixture(scope="module")
def table() -> pd.DataFrame:
    return pd.read_csv(CSV, comment="#", dtype={"ars5": str})


def test_all_zgb_kreise_and_state_total_present(table):
    assert set(table["ars5"]) == EXPECTED_ARS5
    assert len(table) == len(EXPECTED_ARS5)


def test_columns_and_ranges(table):
    assert list(table.columns) == ["kreis", "ars5", "n_taxpayers", "mean_gde_eur", "share_ge_50k"]
    assert (table["n_taxpayers"] > 0).all()
    assert (table["mean_gde_eur"] > 0).all()
    assert table["share_ge_50k"].between(0.0, 1.0).all()


def test_pinned_values(table):
    t = table.set_index("ars5")
    # Niedersachsen total pins the parser to the FIRST 'Insgesamt' block
    # (the raw table repeats subgroup blocks; a wrong block gives ~0.83M).
    assert t.loc["03NDS", "n_taxpayers"] == 4186734
    assert t.loc["03NDS", "mean_gde_eur"] == pytest.approx(46040.7, abs=0.1)
    # Salzgitter is the poorest ZGB Kreis by register income; this pin carries
    # the arbitration verdict against the MiD H4 SZ status cell.
    assert t.loc["03102", "mean_gde_eur"] == pytest.approx(37263.5, abs=0.1)
    assert t.loc["03151", "mean_gde_eur"] == pytest.approx(50538.9, abs=0.1)


def test_kreis_income_ordering(table):
    """The register ordering used by the control-sourcing decision rule:
    Gifhorn richest, Salzgitter poorest, Goslar second-poorest."""
    kreise = table[table["ars5"] != "03NDS"].set_index("ars5")["mean_gde_eur"]
    assert kreise.idxmax() == "03151"
    assert kreise.idxmin() == "03102"
    assert kreise.sort_values().index[1] == "03153"
