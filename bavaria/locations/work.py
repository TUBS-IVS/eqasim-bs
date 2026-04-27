"""Deprecation shim - merged into :mod:`braunschweig.locations.work`."""

from __future__ import annotations

import warnings

from braunschweig.locations.work import *  # noqa: F401,F403
from braunschweig.locations.work import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.locations.work has been merged into braunschweig.locations.work; "
    "update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
