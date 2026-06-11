"""Resolve ``population.method`` to the synpp stage that produces the population.

The *producer* is the stage that yields the discrete persons/households frame
consumed by the downstream enrichment + MATSim writers. Today that frame is
produced by the in-house IPF chain via ``braunschweig.ipf.attributed`` (aliased
to ``data.census.filtered`` in the run configs); ``simple_ipf_open`` therefore
resolves to that existing producer, preserving current behaviour exactly.

The PopulationSim producers (``popsim_open`` / ``popsim_mid``) are introduced in
later phases of the refactor. Until then, selecting them raises a clear
``NotImplementedError`` -- the pipeline must NOT silently fall back to the IPF
workflow (CLAUDE.md "Fallback transparency").

This module only *resolves* the producer name; wiring it into the synpp DAG
(swapping the ``data.census.filtered`` alias by method) is a later phase.
"""

from __future__ import annotations

from braunschweig.population.methods import (
    POPSIM_MID,
    POPSIM_OPEN,
    SIMPLE_IPF_OPEN,
    is_valid_method,
)

# The existing in-house IPF producer (alias target of ``data.census.filtered``).
SIMPLE_IPF_OPEN_PRODUCER = "braunschweig.ipf.attributed"

# PopulationSim producer stage (shared by popsim_mid and popsim_open).
# Both workflows run through braunschweig.popsim.stage; the active donor source
# (MiD vs. ENTD) is controlled by the braunschweig.population.popsim.source
# config key.  popsim_open sets source="entd"; popsim_mid sets source="mid".
POPSIM_MID_PRODUCER = "braunschweig.popsim.stage"
POPSIM_OPEN_PRODUCER = "braunschweig.popsim.stage"


class PopulationMethodNotImplemented(NotImplementedError):
    """Raised when a recognised-but-not-yet-wired method is selected."""


def resolve_population_producer(method: str) -> str:
    """Return the synpp producer-stage name for ``method``.

    Parameters
    ----------
    method:
        A validated population method (see ``braunschweig.population.methods``).

    Returns
    -------
    str
        The dotted synpp stage name that produces the population frame.

    Raises
    ------
    ValueError
        If ``method`` is not a known method (defensive; callers should validate
        via ``braunschweig.population.config`` first).
    PopulationMethodNotImplemented
        If ``method`` is a recognised PopulationSim method that is not wired yet.
        Never falls back to the IPF producer.
    """
    if not is_valid_method(method):
        raise ValueError(
            f"Unknown population method {method!r}; validate the config via "
            "braunschweig.population.config.validate_population_config first."
        )
    if method == SIMPLE_IPF_OPEN:
        return SIMPLE_IPF_OPEN_PRODUCER
    if method == POPSIM_MID:
        return POPSIM_MID_PRODUCER
    if method == POPSIM_OPEN:
        # Phase 3 (popsim_open): uses the same stage as popsim_mid; the
        # braunschweig.population.popsim.source config key selects the ENTD
        # donor adapter at runtime.
        return POPSIM_OPEN_PRODUCER
    # Unreachable while POPULATION_METHODS and this dispatch stay in sync.
    raise ValueError(f"No producer mapping for population method {method!r}.")
