"""Deprecation shim - merged into :mod:`braunschweig.synthesis.population.enriched`."""

from __future__ import annotations

import warnings

from braunschweig.synthesis.population.enriched import *  # noqa: F401,F403
from braunschweig.synthesis.population.enriched import (  # noqa: F401
    configure,
    execute,
)

warnings.warn(
    "bavaria.synthesis.population.enriched has been merged into "
    "braunschweig.synthesis.population.enriched; update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
