"""Population-generation workflow selection for the Braunschweig pipeline.

This package introduces a single configuration switch, ``population.method``
(config key ``braunschweig.population.method``), that selects one of three
independent synthetic-population workflows:

- ``simple_ipf_open`` -- the existing in-house IPF chain
  (``braunschweig.ipf.*``); open data only, no PopulationSim, no MiD.
- ``popsim_open``     -- PopulationSim with an open seed (ENTD); no MiD.
- ``popsim_mid``      -- PopulationSim with restricted MiD 2023 raw microdata;
  required only when this method is explicitly selected.

All three produce a harmonised persons/households frame (see
``braunschweig.population.schema``) that feeds the SAME downstream enrichment and
MATSim writers. The default is ``simple_ipf_open``, which preserves the current
pipeline behaviour exactly (see ``tests/test_simple_ipf_open_baseline.py`` and the
opt-in byte-identity guard ``tests/test_smoke_1pct.py``).

The package holds the method constants, the fail-fast config validator, the
output-schema contract and the producer selector. Both ``popsim_*`` methods
resolve to the producer stage
``braunschweig.popsim.stage``; the selector never silently falls back to the IPF
workflow (no silent fallback; CLAUDE.md "Fallback transparency").
"""

from braunschweig.population.methods import (
    DEFAULT_POPULATION_METHOD,
    POPULATION_METHODS,
    PopulationMethod,
)

__all__ = [
    "DEFAULT_POPULATION_METHOD",
    "POPULATION_METHODS",
    "PopulationMethod",
]
