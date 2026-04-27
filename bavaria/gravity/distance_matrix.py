"""Deprecation shim — moved to :mod:`eqasim_common.gravity.distance_matrix`.

Phase 2.2 of the eqasim-bs refactor moved this module.  The shim is kept
for one minor release; it is dropped in Phase 4.3.
"""

from __future__ import annotations

import warnings

from eqasim_common.gravity.distance_matrix import *  # noqa: F401,F403
from eqasim_common.gravity.distance_matrix import (  # noqa: F401
    configure,
    execute,
)

warnings.warn(
    "bavaria.gravity.distance_matrix has moved to "
    "eqasim_common.gravity.distance_matrix; update your imports. "
    "The shim is removed in Phase 4 of the refactor.",
    DeprecationWarning,
    stacklevel=2,
)
