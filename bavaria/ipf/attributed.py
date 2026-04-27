"""Deprecation shim - moved to :mod:`braunschweig.ipf.attributed`."""

from __future__ import annotations

import warnings

from braunschweig.ipf.attributed import *  # noqa: F401,F403
from braunschweig.ipf.attributed import configure, execute  # noqa: F401

warnings.warn(
    "bavaria.ipf.attributed has moved to braunschweig.ipf.attributed; "
    "update aliases.",
    DeprecationWarning,
    stacklevel=2,
)
