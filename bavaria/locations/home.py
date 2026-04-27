"""Deprecation shim - merged into :mod:`braunschweig.locations.home`."""

from __future__ import annotations

import warnings

from braunschweig.locations.home import *  # noqa: F401,F403
from braunschweig.locations.home import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.locations.home has been merged into braunschweig.locations.home; "
    "update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
