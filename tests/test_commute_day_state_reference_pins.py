"""Pin tests for the committed MiD workday-location reference tables (committed CSVs only)."""
from pathlib import Path

import numpy as np
import pytest

from braunschweig.calibration import commute_day_state_reference as R

MID_DIR = Path(__file__).resolve().parents[1] / "eqasim-data" / "data" / "braunschweig" / "mid"


@pytest.fixture(scope="module")
def table():
    return R.load_workday_location_table(MID_DIR)


@pytest.fixture(scope="module")
def pool():
    return R.load_home_office_donor_pool(MID_DIR)


def test_classes_present_and_shares_sum_to_one(table):
    assert set(table["distance_class"]) == {"lt10", "10_25", "25_50", "50_100", "100_200", "all"}
    assert np.allclose(table[list(R.SHARE_COLUMNS)].sum(axis=1), 1.0, atol=1e-9)


def test_at_workplace_share_falls_with_distance(table):
    t = table.set_index("distance_class")
    assert t.loc["lt10", "share_at_workplace"] > t.loc["50_100", "share_at_workplace"] > t.loc["100_200", "share_at_workplace"]
    assert t.loc["lt10", "share_at_home"] < t.loc["100_200", "share_at_home"]


def test_pinned_values(table):
    t = table.set_index("distance_class")
    # Pinned from the committed table (server extraction, 2026-09-04).
    assert t.loc["lt10", "share_at_workplace"] == pytest.approx(0.591, abs=0.005)
    assert t.loc["lt10", "share_at_home"] == pytest.approx(0.082, abs=0.005)
    assert t.loc["100_200", "share_at_home"] == pytest.approx(0.329, abs=0.005)
    assert t.loc["100_200", "share_at_workplace"] == pytest.approx(0.314, abs=0.005)
    assert t.loc["100_200", "n_unweighted"] == 783
    assert t.loc["all", "n_unweighted"] == 49527


def test_pool_totals(pool):
    total = pool[(pool["distance_class"] == "all") & (pool["has_children"] == "all")].iloc[0]
    assert total["n_donors"] == 8026
    far = pool[(pool["distance_class"] == "100_200") & (pool["has_children"] == "all")].iloc[0]
    assert far["n_donors"] == 276
    assert 0 < far["n_donors"] < total["n_donors"]
