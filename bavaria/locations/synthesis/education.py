"""Deprecation shim — moved to :mod:`eqasim_common.locations.synthesis.education`."""

from __future__ import annotations

import warnings

from eqasim_common.locations.synthesis.education import *  # noqa: F401,F403
from eqasim_common.locations.synthesis.education import (  # noqa: F401
    configure,
    execute,
)

warnings.warn(
    "bavaria.locations.synthesis.education has moved to "
    "eqasim_common.locations.synthesis.education; update your imports/aliases.",
    DeprecationWarning,
    stacklevel=2,
)
