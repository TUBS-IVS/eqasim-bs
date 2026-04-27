"""Deprecation shim - moved to :mod:`braunschweig.locations.secondary`."""

from __future__ import annotations

import warnings

from braunschweig.locations.secondary import *  # noqa: F401,F403
from braunschweig.locations.secondary import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.locations.secondary has moved to braunschweig.locations.secondary; "
    "update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
