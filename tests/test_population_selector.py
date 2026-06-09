"""Tests for the population producer selector (braunschweig.population.selector).

Phase 3: popsim_open is now wired to braunschweig.popsim.stage (same as popsim_mid;
the ENTD donor adapter is selected by the braunschweig.population.popsim.source config
key).  The previous NotImplementedError for popsim_open is replaced by the real
producer name.

This module extends the Phase 1 selector tests in test_population_config.py
(which must remain unmodified: they still verify simple_ipf_open and popsim_mid).
"""

from __future__ import annotations

import pytest

from braunschweig.population import selector
from braunschweig.population.methods import (
    POPSIM_MID,
    POPSIM_OPEN,
    SIMPLE_IPF_OPEN,
)


# ---------------------------------------------------------------------------
# Phase 3: popsim_open is now implemented
# ---------------------------------------------------------------------------

def test_popsim_open_resolves_to_popsim_stage():
    """popsim_open must now resolve to braunschweig.popsim.stage (Phase 3 wired)."""
    producer = selector.resolve_population_producer(POPSIM_OPEN)
    assert producer == "braunschweig.popsim.stage", (
        f"Expected 'braunschweig.popsim.stage' for popsim_open, got {producer!r}"
    )


def test_popsim_open_does_not_raise():
    """resolve_population_producer(popsim_open) must not raise NotImplementedError."""
    # Phase 1 had: pytest.raises(PopulationMethodNotImplemented).
    # Phase 3: must succeed without error.
    try:
        selector.resolve_population_producer(POPSIM_OPEN)
    except selector.PopulationMethodNotImplemented as exc:
        pytest.fail(
            f"popsim_open raised PopulationMethodNotImplemented (Phase 3 should be wired): {exc}"
        )


def test_popsim_open_and_popsim_mid_share_producer():
    """Both popsim_open and popsim_mid must resolve to the same stage.

    The ENTD vs. MiD selection is made at runtime via the source config key,
    not by routing to a different stage.
    """
    open_producer = selector.resolve_population_producer(POPSIM_OPEN)
    mid_producer = selector.resolve_population_producer(POPSIM_MID)
    assert open_producer == mid_producer, (
        f"popsim_open -> {open_producer!r} but popsim_mid -> {mid_producer!r}; "
        "both must resolve to braunschweig.popsim.stage (source selected by config key)"
    )


# ---------------------------------------------------------------------------
# Phase 1 regression: simple_ipf_open and popsim_mid still work
# ---------------------------------------------------------------------------

def test_simple_ipf_open_still_resolves_to_ipf_attributed():
    """Regression: simple_ipf_open must still resolve to the in-house IPF producer."""
    assert selector.resolve_population_producer(SIMPLE_IPF_OPEN) == "braunschweig.ipf.attributed"


def test_popsim_mid_still_resolves_to_popsim_stage():
    """Regression: popsim_mid must still resolve to braunschweig.popsim.stage."""
    assert selector.resolve_population_producer(POPSIM_MID) == "braunschweig.popsim.stage"


def test_selector_constants_are_defined():
    """POPSIM_OPEN_PRODUCER constant must be defined and equal the stage name."""
    assert hasattr(selector, "POPSIM_OPEN_PRODUCER")
    assert selector.POPSIM_OPEN_PRODUCER == "braunschweig.popsim.stage"
