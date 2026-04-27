"""Deprecation shim - moved to :mod:`braunschweig.matsim.simulation.prepare`."""

from __future__ import annotations

import warnings

from braunschweig.matsim.simulation.prepare import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.matsim.simulation.prepare has moved to "
    "braunschweig.matsim.simulation.prepare; update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
