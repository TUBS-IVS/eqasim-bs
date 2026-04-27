"""Deprecation shim — moved to :mod:`eqasim_common.spatial.entd_codes`.

Phase 2.3 of the eqasim-bs refactor moved this module.  The shim is kept
for one minor release; it is dropped in Phase 4.3.
"""

from __future__ import annotations

import warnings

from eqasim_common.spatial.entd_codes import *  # noqa: F401,F403
from eqasim_common.spatial.entd_codes import (  # noqa: F401
    configure,
    execute,
)

warnings.warn(
    "bavaria.entd_codes has moved to eqasim_common.spatial.entd_codes; "
    "update your imports or alias.",
    DeprecationWarning,
    stacklevel=2,
)
