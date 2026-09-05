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
    assert t.loc["lt10", "share_did_not_work"] == pytest.approx(0.261, abs=0.005)
    assert t.loc["100_200", "share_at_home"] == pytest.approx(0.329, abs=0.005)
    assert t.loc["100_200", "share_at_workplace"] == pytest.approx(0.314, abs=0.005)
    assert t.loc["100_200", "n_unweighted"] == 783
    assert t.loc["all", "n_unweighted"] == 49527
    assert t.loc["all", "n_missing_distance"] == 4635


def test_pool_totals(pool):
    total = pool[(pool["distance_class"] == "all") & (pool["has_children"] == "all")].iloc[0]
    assert total["n_donors"] == 8026
    far = pool[(pool["distance_class"] == "100_200") & (pool["has_children"] == "all")].iloc[0]
    assert far["n_donors"] == 276
    assert 0 < far["n_donors"] < total["n_donors"]
    # Thinnest cross-classified cells (children AND active escort), pinned from the committed
    # table -- see the Data Registry record's limitations for why these are too thin on their own.
    # has_children/has_active_escort are loaded as strings ("all"/"True"/"False") because the
    # column also carries the "all" total marker, so pandas cannot infer a bool dtype for it.
    thin_50_100 = pool[(pool["distance_class"] == "50_100") & (pool["has_children"] == "True")
                        & (pool["has_active_escort"] == "True")].iloc[0]
    assert thin_50_100["n_donors"] == 76
    thin_100_200 = pool[(pool["distance_class"] == "100_200") & (pool["has_children"] == "True")
                         & (pool["has_active_escort"] == "True")].iloc[0]
    assert thin_100_200["n_donors"] == 30


def test_committed_header_discloses_universe_and_bin_convention():
    """Ruling R6/R7: the committed CSV's own comment header must state the universe definition
    and the left-inclusive bin convention verbatim, since the header -- not this test file, not a
    session artifact -- is the durable, traceable home for how the committed rows were derived."""
    path = MID_DIR / R.WORKDAY_LOCATION_TABLE
    header_text = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                             if line.startswith("#"))
    assert "P_STARB1 in (1, 2, 9)" in header_text
    assert "left-inclusive" in header_text.lower()
    # Finding 2 (whole-branch review): the header must state a MEASURED bin-convention deviation,
    # not a hand-typed "0.005" carried over from an earlier, uncommitted scan.
    assert "bin-convention deviation" in header_text.lower()
    assert "within 0.005" not in header_text.lower()
    # Follow-up (controller ruling R13): the header must NOT claim that shares "stay robust" or
    # that "only" the unweighted counts are convention-sensitive -- both counts AND shares are
    # convention-sensitive (counts strongly, shares by the measured deviation); the header must
    # say so in those terms instead.
    assert "stay robust" not in header_text.lower()
    assert "only the unweighted class counts are convention-sensitive" not in header_text.lower()
    assert "counts and shares are both convention-sensitive" in header_text.lower()
