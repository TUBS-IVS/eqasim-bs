"""Tests for braunschweig.calibration.reported_time_precision (issue #123, Phase 0 Task 1).

MiD/SrV respondents report clock times on a coarse grid (quarter-hour or five-minute), not to
the second. These tests pin the shared precision rule -- which half-width applies to a reported
minute-of-hour, and that de-rounding a reported time draws uniformly inside that half-width and
hands back the exact offset it drew -- so both the SrV reference builder (Task 2) and the
departure-time model (Task 3) read the SAME rule from ONE place.
"""
from __future__ import annotations

import numpy as np

from braunschweig.calibration import reported_time_precision as RP


def test_half_width_follows_the_reporting_grid():
    minutes = np.array([0, 15, 30, 45, 5, 10, 20, 55, 7, 23])
    assert RP.half_width_minutes(minutes).tolist() == [7.5, 7.5, 7.5, 7.5, 2.5, 2.5, 2.5, 2.5, 0.0, 0.0]


def test_deround_stays_inside_the_cell_and_returns_the_offset():
    rng = np.random.RandomState(0)
    minutes = np.array([420.0] * 1000 + [425.0] * 1000 + [427.0] * 10)   # 7:00, 7:05, 7:07
    derounded, offset = RP.deround_minutes_of_day(minutes, rng)
    assert np.all(np.abs(offset[:1000]) <= 7.5) and np.abs(offset[:1000]).max() > 6.0
    assert np.all(np.abs(offset[1000:2000]) <= 2.5)
    assert np.all(offset[2000:] == 0.0)
    assert np.allclose(derounded, minutes + offset)
