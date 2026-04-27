"""Deprecation shim — moved to :mod:`eqasim_common.locations.synthesis.replacement`."""

from __future__ import annotations

import warnings

from eqasim_common.locations.synthesis.replacement import *  # noqa: F401,F403
from eqasim_common.locations.synthesis.replacement import (  # noqa: F401
    configure,
    execute,
)

warnings.warn(
    "bavaria.locations.synthesis.replacement has moved to "
    "eqasim_common.locations.synthesis.replacement; update your imports/aliases.",
    DeprecationWarning,
    stacklevel=2,
)
