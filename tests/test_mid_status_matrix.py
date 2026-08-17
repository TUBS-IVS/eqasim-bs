"""Pin tests for the committed MiD 2023 economic-status matrix (handbook
Abbildung 2, p.16), extracted from the PDF vector fills. Reads ONLY the
committed CSV."""
from pathlib import Path

import pandas as pd
import pytest

CSV = (Path(__file__).resolve().parents[1] / "eqasim-data" / "data"
       / "braunschweig" / "mid" / "mid2023_economic_status_matrix.csv")

ROW_LABELS = [1.0, 1.3, 1.5, 1.6, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6,
              2.7, 2.8, 2.9, 3.0, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9,
              4.0, 4.1, 4.3, 4.5]
STATUSES = {"very_low", "low", "medium", "high", "very_high"}


@pytest.fixture(scope="module")
def matrix() -> pd.DataFrame:
    return pd.read_csv(CSV, comment="#")


def test_shape_and_domains(matrix):
    assert len(matrix) == 450
    assert sorted(matrix["wsize_row"].unique()) == ROW_LABELS
    assert sorted(matrix["income_col"].unique()) == list(range(15))
    assert set(matrix["status"]) == STATUSES


def test_income_bounds_are_the_15_mid_classes(matrix):
    bounds = (matrix[["income_col", "income_lo_eur", "income_hi_eur"]]
              .drop_duplicates().sort_values("income_col"))
    assert bounds["income_lo_eur"].tolist() == [
        0, 500, 900, 1500, 2000, 2600, 3000, 3600, 4000, 4600,
        5000, 5600, 6000, 6600, 7000]
    assert bounds["income_hi_eur"].tolist() == [
        500, 900, 1500, 2000, 2600, 3000, 3600, 4000, 4600, 5000,
        5600, 6000, 6600, 7000, -1]


def test_pinned_cells(matrix):
    m = matrix.set_index(["wsize_row", "income_col"])["status"]
    # corners
    assert m[(4.5, 0)] == "very_low"
    assert m[(1.0, 14)] == "very_high"
    # single person: 1500-2000 -> low, 3000-3600 -> high, >4600 -> very_high
    assert m[(1.0, 3)] == "low"
    assert m[(1.0, 6)] == "high"
    assert m[(1.0, 9)] == "very_high"
    # family 2.1 (couple + 2 kids <14): 2000-2600 -> low, 4600-5000 -> high
    assert m[(2.1, 4)] == "low"
    assert m[(2.1, 9)] == "high"
    # large household 4.5: 3600-4000 -> low, >7000 -> very_high
    assert m[(4.5, 7)] == "low"
    assert m[(4.5, 14)] == "very_high"


def test_status_monotone_in_income_within_each_row(matrix):
    order = {"very_low": 0, "low": 1, "medium": 2, "high": 3, "very_high": 4}
    for _, grp in matrix.groupby("wsize_row"):
        vals = grp.sort_values("income_col")["status"].map(order).tolist()
        assert vals == sorted(vals)
