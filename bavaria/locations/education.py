"""Deprecation shim — moved to :mod:`eqasim_common.locations.education`."""

from __future__ import annotations

import warnings

from eqasim_common.locations.education import *  # noqa: F401,F403
from eqasim_common.locations.education import (  # noqa: F401
    configure,
    execute,
)

warnings.warn(
    "bavaria.locations.education has moved to eqasim_common.locations.education; "
    "update your imports/aliases.",
    DeprecationWarning,
    stacklevel=2,
)
