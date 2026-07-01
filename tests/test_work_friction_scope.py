"""Tests for work_gravity_friction_factors scoping (Task 4).

Verifies that:
  1. configure() declares work_gravity_friction_factors with default None.
  2. On the OFF path (taz_work_location_choice=False):
     - when work_gravity_friction_factors is None, compute_work_od receives
       gravity_friction_factors (fallback, logged; byte-identical behaviour).
     - when work_gravity_friction_factors is set, the single Gemeinde call still
       receives gravity_friction_factors (the TAZ pass is not run).
  3. On the ON path (taz_work_location_choice=True):  # deferred to server (Task 5) -- ON path needs geopandas/sjoin stages
     - the education Gemeinde compute_work_od call receives gravity_friction_factors,
     - the work TAZ compute_work_od call receives work_gravity_friction_factors
       (when set, not the fallback).

Design notes
------------
synpp is a server-only dependency (requires real stages, LAPACK, etc.).  These
tests bypass synpp entirely by using a ``MockContext`` that records config()/stage()
calls and returns minimal synthetic DataFrames for the four required stages.
``compute_work_od`` is monkeypatched with a spy that records the ``friction_factors``
kwarg per call and returns a minimal valid OD frame.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pandas as pd
import pytest

import braunschweig.gravity.model as model_module
from braunschweig.gravity.model import configure


# ---------------------------------------------------------------------------
# MockContext used across all tests (matches the pattern from
# tests/test_taz_off_byte_identical.py, extended to return DataFrames for
# the four stages _execute_gravity_base reads unconditionally).
# ---------------------------------------------------------------------------

_ZONES = ["A", "B"]


def _make_population():
    return pd.DataFrame({"commune_id": _ZONES, "weight": [10.0, 20.0]})


def _make_employees():
    return pd.DataFrame({"commune_id": _ZONES, "weight": [15.0, 15.0]})


def _make_distances():
    rows = []
    for o in _ZONES:
        for d in _ZONES:
            rows.append({"origin_id": o, "destination_id": d,
                         "distance_km": float(abs(ord(o) - ord(d)))})
    return pd.DataFrame(rows)


def _make_regiostar():
    return pd.DataFrame(columns=["commune_id", "regiostar7"])


def _spy_compute_work_od():
    """Return a spy replacing compute_work_od; captures friction_factors per call."""
    calls = []

    def _spy(**kwargs):
        calls.append(kwargs.get("friction_factors"))
        origins = kwargs["df_population"]["origin_id"].tolist() \
            if "origin_id" in kwargs["df_population"].columns \
            else kwargs["df_population"][kwargs["df_population"].columns[0]].tolist()
        return pd.DataFrame({
            "origin_id": origins[:1],
            "destination_id": origins[:1],
            "weight": [1.0],
        })

    return _spy, calls


class MockContext:
    """Minimal synpp context stub that returns synthetic DataFrames for stages."""

    def __init__(self, flag_values=None, config_values=None):
        self._flag_values = flag_values or {}
        self._config_values = config_values or {}
        self.configs_requested = []

    def config(self, key, *args):
        """Return override if set, else supplied default, else None."""
        default = args[0] if args else None
        self.configs_requested.append((key, default))
        if key in self._config_values:
            return self._config_values[key]
        if key in self._flag_values:
            return self._flag_values[key]
        return default

    def stage(self, name):
        _stage_map = {
            "eqasim_common.gravity.distance_matrix": _make_distances(),
            "data.census.filtered": _make_population(),
            "braunschweig.data.census.employees": _make_employees(),
            "braunschweig.data.bbsr.regiostar": _make_regiostar(),
        }
        return _stage_map.get(name)


# ---------------------------------------------------------------------------
# Test 1: configure() declares work_gravity_friction_factors with default None
# ---------------------------------------------------------------------------

class _ConfigOnlyContext:
    """Context stub for configure() only (never calls execute-time stage())."""

    def __init__(self):
        self.configs_requested = []

    def config(self, key, *args):
        default = args[0] if args else None
        self.configs_requested.append((key, default))
        return default

    def stage(self, name):
        return None


def test_configure_declares_work_gravity_friction_factors():
    """configure() must declare work_gravity_friction_factors with default None."""
    ctx = _ConfigOnlyContext()
    configure(ctx)
    config_map = {k: d for k, d in ctx.configs_requested}
    assert "work_gravity_friction_factors" in config_map, (
        "configure() did not declare work_gravity_friction_factors"
    )
    assert config_map["work_gravity_friction_factors"] is None, (
        "work_gravity_friction_factors must default to None (not {}), "
        "or synpp flatten() will drop it"
    )


# ---------------------------------------------------------------------------
# Test 2: OFF path (taz=False) + work_gravity_friction_factors=None ->
#         fallback: compute_work_od receives gravity_friction_factors
# ---------------------------------------------------------------------------

def test_off_path_fallback_work_uses_gravity_friction_factors(monkeypatch):
    """OFF path + work_gravity_friction_factors unset -> fallback to gravity_friction_factors."""
    spy, calls = _spy_compute_work_od()
    monkeypatch.setattr(model_module, "compute_work_od", spy)

    edu_friction = {0: 1.1, 1: 0.9}
    ctx = MockContext(
        flag_values={
            "taz_work_location_choice": False,
            "braunschweig.gravity.sector_aware_enabled": False,
        },
        config_values={
            "gravity_slope": -0.2,
            "gravity_constant": -2.4,
            "gravity_diagonal": 1.0,
            "gravity_slope_by_regiostar7": None,
            "gravity_friction_factors": edu_friction,
            "work_gravity_friction_factors": None,  # unset -> fallback
            "gravity_max_iterations": 100,
        },
    )

    model_module._execute_gravity_base(ctx)

    # OFF path: exactly one compute_work_od call (Gemeinde only).
    assert len(calls) == 1, \
        f"Expected 1 compute_work_od call on OFF path, got {len(calls)}"
    # The single call must receive gravity_friction_factors (the fallback value).
    assert calls[0] is edu_friction, (
        "OFF path fallback: compute_work_od did not receive gravity_friction_factors"
    )


# ---------------------------------------------------------------------------
# Test 3: OFF path (taz=False) -- work_gravity_friction_factors set is inert
#         (TAZ pass is not run; the single Gemeinde call still gets gravity_friction_factors)
# ---------------------------------------------------------------------------

def test_off_path_work_friction_set_is_inert(monkeypatch):
    """OFF path: setting work_gravity_friction_factors is harmless (TAZ not run)."""
    spy, calls = _spy_compute_work_od()
    monkeypatch.setattr(model_module, "compute_work_od", spy)

    edu_friction = {0: 1.0}
    work_friction = {72: {0: 2.0}}
    ctx = MockContext(
        flag_values={
            "taz_work_location_choice": False,
            "braunschweig.gravity.sector_aware_enabled": False,
        },
        config_values={
            "gravity_slope": -0.2,
            "gravity_constant": -2.4,
            "gravity_diagonal": 1.0,
            "gravity_slope_by_regiostar7": None,
            "gravity_friction_factors": edu_friction,
            "work_gravity_friction_factors": work_friction,
            "gravity_max_iterations": 100,
        },
    )

    model_module._execute_gravity_base(ctx)

    # OFF path: still only one call (no TAZ pass).
    assert len(calls) == 1, \
        f"Expected 1 compute_work_od call on OFF path, got {len(calls)}"
    # The call receives gravity_friction_factors (education / Gemeinde pass).
    assert calls[0] is edu_friction, (
        "OFF path: single Gemeinde call must receive gravity_friction_factors "
        "even when work_gravity_friction_factors is set"
    )


# ---------------------------------------------------------------------------
# Test 4: configure() + OFF path declares work_gravity_friction_factors
#         (regression: OFF path byte-identical contract -- the key IS declared)
# ---------------------------------------------------------------------------

def test_off_path_configure_declares_new_key():
    """configure() with flag=False must still declare work_gravity_friction_factors.

    The key must be in the declared set so an existing config that adds it does
    not see 'Config option ... is not requested' at execute time.
    """
    ctx = _ConfigOnlyContext()
    configure(ctx)
    declared_keys = {k for k, _ in ctx.configs_requested}
    assert "work_gravity_friction_factors" in declared_keys
    # Also verify gravity_friction_factors is still declared (regression guard).
    assert "gravity_friction_factors" in declared_keys
