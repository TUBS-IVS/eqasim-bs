"""Canonical constants for the three population-generation methods.

Single source of truth for the allowed values of ``braunschweig.population.method``
and the default. Keep this list in lock-step with the producer selector
(``braunschweig.population.selector``) and the documentation in
``docs/population/``.
"""

from __future__ import annotations

from typing import Final

# The existing in-house IPF workflow (open data, no PopulationSim, no MiD).
SIMPLE_IPF_OPEN: Final[str] = "simple_ipf_open"

# PopulationSim with an open seed (ENTD); reproducible without restricted MiD.
POPSIM_OPEN: Final[str] = "popsim_open"

# PopulationSim with restricted MiD 2023 raw microdata (local-only seed).
POPSIM_MID: Final[str] = "popsim_mid"

# Ordered tuple of all valid methods (display / validation order).
POPULATION_METHODS: Final[tuple[str, ...]] = (
    SIMPLE_IPF_OPEN,
    POPSIM_OPEN,
    POPSIM_MID,
)

# The default preserves the current pipeline behaviour exactly.
DEFAULT_POPULATION_METHOD: Final[str] = SIMPLE_IPF_OPEN

# Methods that require PopulationSim (and therefore the batch/run machinery).
POPSIM_METHODS: Final[frozenset[str]] = frozenset({POPSIM_OPEN, POPSIM_MID})

# Methods that read restricted MiD 2023 raw microdata.
MID_METHODS: Final[frozenset[str]] = frozenset({POPSIM_MID})

# Backwards/typing-friendly alias; methods are plain strings at runtime.
PopulationMethod = str


def is_valid_method(method: str) -> bool:
    """Return ``True`` iff ``method`` is one of the three known methods."""
    return method in POPULATION_METHODS


def requires_populationsim(method: str) -> bool:
    """Return ``True`` iff the method runs PopulationSim."""
    return method in POPSIM_METHODS


def requires_mid_raw(method: str) -> bool:
    """Return ``True`` iff the method reads restricted MiD 2023 raw microdata."""
    return method in MID_METHODS
