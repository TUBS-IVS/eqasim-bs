"""Tests for the all-employed extra-location per-person assignment (#203, Approach A).

The extra employed/studying persons have NO reference-day trip and therefore NO
observed commute distance, so the distance-minimising assignment used for
trip-havers (synthesis/population/spatial/primary/locations.py) does not apply.
Instead the extra per-commute candidates (origin_zone -> destination_zone ->
building, from the gravity OD) are assigned to the extra persons by RANDOM
ordering within each origin zone. What matters for the commuter-OD validation
universe is the home-zone -> work-zone flow, which the candidates already carry;
the specific building is drawn randomly.

Pure-function tests: no synpp context, no matsim import, no real data.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

from braunschweig.synthesis.locations.all_employed_primary import assign_extras_random


def _persons():
    # 3 persons in zone A, 1 in zone B
    return pd.DataFrame({
        "person_id": [10, 11, 12, 20],
        "origin_zone": ["A", "A", "A", "B"],
    })


def _candidates():
    # per-commute candidate rows: 3 for zone A, 1 for zone B (equal counts per zone)
    return pd.DataFrame({
        "origin_id": ["A", "A", "A", "B"],
        "destination_id": ["A", "A", "C", "B"],
        "location_id": ["w1", "w2", "w3", "w4"],
    })


def test_every_extra_person_gets_exactly_one_location():
    out = assign_extras_random(_persons(), _candidates(), "origin_zone",
                               np.random.RandomState(0))
    assert set(out["person_id"]) == {10, 11, 12, 20}
    assert len(out) == 4
    # each location used exactly once (bijective within the pool)
    assert sorted(out["location_id"]) == ["w1", "w2", "w3", "w4"]


def test_assignment_respects_origin_zone():
    """A person in zone B can only receive zone-B candidates."""
    out = assign_extras_random(_persons(), _candidates(), "origin_zone",
                               np.random.RandomState(0))
    b_row = out[out["person_id"] == 20].iloc[0]
    assert b_row["location_id"] == "w4"
    assert b_row["destination_id"] == "B"


def test_deterministic_for_fixed_seed():
    a = assign_extras_random(_persons(), _candidates(), "origin_zone",
                             np.random.RandomState(42))
    b = assign_extras_random(_persons(), _candidates(), "origin_zone",
                             np.random.RandomState(42))
    pd.testing.assert_frame_equal(
        a.sort_values("person_id").reset_index(drop=True),
        b.sort_values("person_id").reset_index(drop=True))


def test_count_mismatch_raises():
    """Per-zone person/candidate counts must match (mirrors the trip-haver path)."""
    bad_candidates = _candidates().iloc[:3]  # drop zone-B candidate
    try:
        assign_extras_random(_persons(), bad_candidates, "origin_zone",
                             np.random.RandomState(0))
        raised = False
    except Exception:
        raised = True
    assert raised, "Expected a mismatch between persons and candidates to raise"
