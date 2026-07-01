"""Tests for the per-call zone-key helper and home-TAZ wiring in the
primary candidates / locations stages.

Pure-function tests only: no synpp context, no matsim import, no real data.
Full WORK-flow e2e coverage is deferred to Task 9 + server smoke.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pandas as pd
import pytest

from synthesis.population.spatial.primary.candidates import _filter_pool_by_zone


# ---------------------------------------------------------------------------
# _filter_pool_by_zone helper
# ---------------------------------------------------------------------------

def test_filter_pool_by_zone_taz_vs_commune():
    """Helper returns the right rows under both taz_id and commune_id keys."""
    pool = pd.DataFrame({
        "location_id": [1, 2, 3],
        "commune_id": ["031010000000"] * 3,
        "taz_id": ["T1", "T2", "T1"],
    })
    result_taz = _filter_pool_by_zone(pool, "T1", zone_key="taz_id")
    assert set(result_taz["location_id"]) == {1, 3}, (
        "Expected locations 1 and 3 (taz_id=='T1'), got %s" % list(result_taz["location_id"]))

    result_commune = _filter_pool_by_zone(pool, "031010000000", zone_key="commune_id")
    assert set(result_commune["location_id"]) == {1, 2, 3}, (
        "Expected all 3 locations (commune_id match), got %s" % list(result_commune["location_id"]))


def test_filter_pool_by_zone_no_match_returns_empty():
    """No matching zone value returns an empty DataFrame (not an error)."""
    pool = pd.DataFrame({
        "location_id": [1, 2],
        "commune_id": ["031010000000"] * 2,
        "taz_id": ["T1", "T2"],
    })
    result = _filter_pool_by_zone(pool, "T_MISSING", zone_key="taz_id")
    assert len(result) == 0


def test_filter_pool_by_zone_preserves_columns():
    """Returned DataFrame contains all original columns (not just zone_key)."""
    pool = pd.DataFrame({
        "location_id": [10, 20],
        "commune_id": ["031010000000", "031010000001"],
        "taz_id": ["T1", "T1"],
        "employees": [5.0, 8.0],
    })
    result = _filter_pool_by_zone(pool, "T1", zone_key="taz_id")
    for col in ["location_id", "commune_id", "taz_id", "employees"]:
        assert col in result.columns, "Column %r missing from result" % col


def test_filter_pool_by_zone_default_is_commune_id():
    """Calling without zone_key defaults to commune_id (backward-compatible)."""
    pool = pd.DataFrame({
        "location_id": [1, 2, 3],
        "commune_id": ["A", "A", "B"],
    })
    result = _filter_pool_by_zone(pool, "A")
    assert set(result["location_id"]) == {1, 2}


def test_filter_pool_by_zone_missing_column_raises():
    """Requesting a zone_key column that does not exist in the pool raises KeyError."""
    pool = pd.DataFrame({
        "location_id": [1],
        "commune_id": ["031010000000"],
    })
    with pytest.raises(KeyError):
        _filter_pool_by_zone(pool, "T1", zone_key="taz_id")
