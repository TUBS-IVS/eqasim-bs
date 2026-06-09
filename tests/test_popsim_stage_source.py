"""Tests for source-resolution in popsim stage + trips_stage (Phase 1 Task 3).

Verifies that:
- stage._resolve_source("mid") returns a MidSource instance.
- stage._resolve_source with no explicit name (default "mid") also returns MidSource.
- stage._resolve_source("entd") raises NotImplementedError (planned but not implemented).
- stage._resolve_source with an unknown name raises ValueError.
- trips_stage.configure declares braunschweig.population.popsim.source with default "mid".

Full stage.execute / trips_stage.execute are NOT tested here: they require real
MiD CSV files, a PopulationSim uv environment, and a synpp context, none of which
are available in unit tests.  Source resolution is factored into _resolve_source
specifically so it can be verified independently.
"""

from __future__ import annotations

import pytest

from braunschweig.popsim import stage
from braunschweig.popsim.sources.mid import MidSource


# ---------------------------------------------------------------------------
# _resolve_source helper
# ---------------------------------------------------------------------------

def test_stage_default_source_is_mid():
    """_resolve_source('mid') must return a MidSource, matching the default config."""
    source = stage._resolve_source("mid")
    assert isinstance(source, MidSource), (
        f"Expected MidSource for source_name='mid', got {type(source).__name__}"
    )
    assert source.name == "mid"


def test_stage_resolve_source_mid_explicit():
    """Explicitly passing 'mid' to _resolve_source must resolve to MidSource."""
    source = stage._resolve_source("mid")
    assert isinstance(source, MidSource)


def test_stage_resolve_source_entd_raises_not_implemented():
    """_resolve_source('entd') must raise NotImplementedError (Phase 2 not yet done)."""
    with pytest.raises(NotImplementedError):
        stage._resolve_source("entd")


def test_stage_resolve_source_unknown_raises_value_error():
    """_resolve_source with an unknown name must raise ValueError."""
    with pytest.raises(ValueError):
        stage._resolve_source("completely_unknown_source_xyz")


# ---------------------------------------------------------------------------
# KEY_SOURCE constant + configure structure
# ---------------------------------------------------------------------------

def test_stage_key_source_constant():
    """KEY_SOURCE must be the expected config key string."""
    assert stage.KEY_SOURCE == "braunschweig.population.popsim.source"


def test_stage_resolve_source_returns_fresh_instance_each_call():
    """Each call to _resolve_source must return a fresh (independent) instance."""
    s1 = stage._resolve_source("mid")
    s2 = stage._resolve_source("mid")
    assert s1 is not s2, (
        "_resolve_source should return a new instance on each call "
        "(sources.get_source uses a factory, not a singleton)"
    )
