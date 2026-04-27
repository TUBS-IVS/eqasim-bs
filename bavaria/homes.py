"""Deprecation shim - moved to :mod:`braunschweig.synthesis.spatial.home_zones`."""

from __future__ import annotations

import warnings

from braunschweig.synthesis.spatial.home_zones import *  # noqa: F401,F403
from braunschweig.synthesis.spatial.home_zones import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.homes has moved to braunschweig.synthesis.spatial.home_zones; "
    "update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
