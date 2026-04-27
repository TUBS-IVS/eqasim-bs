"""Deprecation shim — moved to :mod:`eqasim_common.data.osm.chunked`.

Phase 2.1 of the eqasim-bs refactor moved this module to
``eqasim_common.data.osm.chunked``.  The shim is kept for one minor release
so any external code that still imports the old path continues to work; it
is dropped in Phase 4.3.
"""

from __future__ import annotations

import warnings

from eqasim_common.data.osm.chunked import *  # noqa: F401,F403
from eqasim_common.data.osm.chunked import (  # noqa: F401
    configure,
    execute,
    process_municipality,
)

warnings.warn(
    "bavaria.data.osm.chunked has moved to eqasim_common.data.osm.chunked; "
    "update your imports. The shim is removed in Phase 4 of the refactor.",
    DeprecationWarning,
    stacklevel=2,
)
