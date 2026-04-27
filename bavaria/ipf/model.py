"""Deprecation shim - moved to :mod:`braunschweig.ipf.model`."""

from __future__ import annotations

import warnings

from braunschweig.ipf.model import *  # noqa: F401,F403
from braunschweig.ipf.model import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.ipf.model has moved to braunschweig.ipf.model; update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
