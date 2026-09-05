"""Tests for braunschweig.popsim.closure_dwell (Task 5, issue #367).

The empirical dwell model draws the duration of the last (unclosed) activity
from what MiD donors actually report for the same purpose and arrival band
when their day continues past that activity, instead of a fixed constant.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.closure_dwell import ClosureDwellModel


def _donor_trips():
    # two persons; work activities of 8 h and 9 h (arrivals 8:00 / 9:00), one shop activity of 30 min
    return pd.DataFrame({
        "person_id": [1, 1, 2, 2, 2],
        "departure_time": [7.5 * 3600, 16 * 3600, 8.5 * 3600, 18 * 3600, 18.75 * 3600],
        "arrival_time":   [8 * 3600, 16.5 * 3600, 9 * 3600, 18.25 * 3600, 19 * 3600],
        "following_purpose": ["work", "home", "work", "shop", "home"],
    })


def test_fixed_model_returns_constant():
    m = ClosureDwellModel.fixed(3600.0)
    assert m.draw("work", 10 * 3600) == 3600.0 and m.draw("leisure", 0.0) == 3600.0


def test_empirical_draws_from_cell_or_purpose_marginal():
    m = ClosureDwellModel.from_trips(_donor_trips(), rng=np.random.RandomState(0), min_obs=1)
    d = m.draw("work", 8 * 3600)
    assert d in (8 * 3600, 9 * 3600)            # both work durations sit in the <14 arrival band
    assert m.draw("shop", 18.25 * 3600) == pytest.approx(0.5 * 3600)
    d_leisure = m.draw("leisure", 12 * 3600)     # unknown purpose -> global marginal, counted
    assert d_leisure in (8 * 3600, 9 * 3600, 0.5 * 3600)
    assert m.report["n_fallback_global"] == 1


def test_empirical_min_obs_falls_back_to_purpose_marginal():
    m = ClosureDwellModel.from_trips(_donor_trips(), rng=np.random.RandomState(0), min_obs=5)
    m.draw("work", 8 * 3600)
    assert m.report["n_fallback_purpose_marginal"] == 1


def test_empirical_ignores_nonpositive_and_capped_durations():
    t = _donor_trips()
    t.loc[0, "arrival_time"] = 17 * 3600     # arrival after the next departure -> negative duration, ignored
    m = ClosureDwellModel.from_trips(t, rng=np.random.RandomState(0), min_obs=1)
    assert m.report["n_obs_by_purpose"]["work"] == 1
