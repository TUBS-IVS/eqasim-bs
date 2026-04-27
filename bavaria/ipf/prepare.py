"""Deprecation shim - moved to :mod:`braunschweig.ipf.prepare`."""

from __future__ import annotations

import warnings

from braunschweig.ipf.prepare import *  # noqa: F401,F403
from braunschweig.ipf.prepare import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.ipf.prepare has moved to braunschweig.ipf.prepare; update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
