"""Deprecation shim - bavaria.gravity.model has been merged into

:mod:raunschweig.gravity.model. Phase 2.11 inlined the gravity solver
into the BS module so the calibration layer no longer delegates through
this stage.
"""

from __future__ import annotations

import warnings

from braunschweig.gravity.model import (  # noqa: F401
    DEFAULT_SLOPE,
    DEFAULT_CONSTANT,
    DEFAULT_DIAGONAL,
    evaluate_gravity,
)

warnings.warn(
    "bavaria.gravity.model has been merged into braunschweig.gravity.model.",
    DeprecationWarning,
    stacklevel=2,
)
