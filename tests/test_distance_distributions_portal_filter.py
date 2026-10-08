"""The distance CDFs the sampler draws from stop at the portal threshold (eqasim-bs#442)."""
import inspect
import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import distance_distributions as dd
from braunschweig.synthesis.portal_trips import config_keys as keys

LOGGER_NAME = dd.logger.name


def _wege(n_far, far_hvm=4, far_km=200.0):
    base = {"H_ID": 1, "P_ID": 1, "W_ZWECK": 7, "hvm_imp": 4, "W_SZS": 8, "W_SZM": 0, "W_AZS": 9, "W_AZM": 0,
            "W_GEW": 1.0, "W_RBW": 0, "W_SO1": 1, "HP_ALTER": 40, "kernwo": 1}
    rows = []
    for i in range(400):
        rows.append({**base, "W_ID": i + 1, "wegkm_imp": 5.0})
    for i in range(n_far):
        rows.append({**base, "W_ID": 500 + i, "wegkm_imp": far_km, "hvm_imp": far_hvm})
    return pd.DataFrame(rows)


def test_run_accepts_a_distance_bound_and_drops_trips_beyond_it():
    assert "max_distance_m" in inspect.signature(dd.run).parameters
    bounded = dd.run(_wege(50), max_distance_m=45000.0)
    unbounded = dd.run(_wege(50))
    far_m = 200.0 * 1000.0 / 1.3
    assert all(v <= 45000.0 for d in bounded["car"]["distributions"] for v in d["values"])
    assert any(np.isclose(v, far_m) for d in unbounded["car"]["distributions"] for v in d["values"])


def test_no_bound_is_identical_to_the_default_call():
    default = dd.run(_wege(50))
    explicit_none = dd.run(_wege(50), max_distance_m=None)
    assert default.keys() == explicit_none.keys()
    for mode in default:
        assert np.array_equal(default[mode]["bounds"], explicit_none[mode]["bounds"])
        for left, right in zip(default[mode]["distributions"], explicit_none[mode]["distributions"]):
            assert np.array_equal(left["values"], right["values"])
            assert np.array_equal(left["weights"], right["weights"])


def test_bound_below_every_distance_of_a_mode_raises_naming_mode_and_bound():
    # The far trips are the only "bicycle" (hvm 2) trips: the bound removes the whole mode, which
    # the sampler could not serve (it would fail late with a KeyError), so the stage must raise.
    wege = _wege(30, far_hvm=2)
    with pytest.raises(ValueError, match=r"bicycle.*45000|45000.*bicycle"):
        dd.run(wege, max_distance_m=45000.0)


def test_dropped_trips_are_logged_as_a_rate_per_mode(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        dd.run(_wege(50), max_distance_m=45000.0)
    messages = [r.getMessage() for r in caplog.records]
    assert any("trips beyond 45000 m dropped: 50/450 (11.11%) [car]" in m for m in messages)


def test_a_bound_that_removes_every_trip_fails_loudly():
    with pytest.raises(ValueError, match="no trip left"):
        dd.run(_wege(0), max_distance_m=1.0)


def test_configure_declares_the_portal_keys():
    calls = {}

    class _Context:
        def config(self, key, default=None):
            calls[key] = default
            return default

        def stage(self, name, **kwargs):
            pass

    dd.configure(_Context())
    assert calls[keys.KEY_ENABLED] == keys.DEFAULT_ENABLED
    assert calls[keys.KEY_MAX_ROUTABLE_DISTANCE_M] == keys.DEFAULT_MAX_ROUTABLE_DISTANCE_M


@pytest.mark.parametrize("enabled, expected", [(True, 45000.0), (False, None)])
def test_execute_passes_the_bound_only_when_the_feature_is_enabled(monkeypatch, enabled, expected):
    from braunschweig.popsim.mid import donor

    seen = {}
    monkeypatch.setattr(donor, "load_mid_wege", lambda mid_dir: pd.DataFrame())
    monkeypatch.setattr(dd, "run", lambda wege, **kwargs: seen.update(kwargs) or {})

    values = {keys.KEY_ENABLED: enabled, keys.KEY_MAX_ROUTABLE_DISTANCE_M: 45000.0}

    class _Context:
        def config(self, key, *default):
            assert not default, "execute() must read config with ONE argument"
            if key in values:
                return values[key]
            return False if key not in ("braunschweig.population.popsim.mid_dir",) else "unused"

    # Numeric keys read by float()/int() need real numbers, not the False placeholder.
    from braunschweig.popsim.stage import config_keys as stage_keys
    values[stage_keys.KEY_PASSIVE_PAIR_MAX_GAP_MINUTES] = 10.0
    values[stage_keys.KEY_PASSIVE_PAIR_ADULT_MIN_AGE_YEARS] = 18

    dd.execute(_Context())
    assert seen["max_distance_m"] == expected


def test_the_deferred_helper_names_cover_the_portal_keys():
    assert "braunschweig.synthesis.portal_trips.config_keys" in dd._DEFERRED_HELPER_MODULE_NAMES
