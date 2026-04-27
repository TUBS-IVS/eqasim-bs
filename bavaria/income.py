"""Deprecation shim - moved to :mod:`braunschweig.synthesis.income`."""

from __future__ import annotations

import warnings

from braunschweig.synthesis.income import *  # noqa: F401,F403
from braunschweig.synthesis.income import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.income has moved to braunschweig.synthesis.income; update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
